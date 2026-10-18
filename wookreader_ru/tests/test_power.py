#!/usr/bin/env python3
"""Exercise actual patched idle worker, status timers and prefetch control.

LEvent uses a host condition-variable double with manual-clear semantics. This
tests WOOK's predicates/wake protocol, not the Switch kernel implementation.
The status function and prefetch method are extracted from the applied source.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def between(text, start, end):
    first = text.index(start)
    return text[first:text.index(end, first)]


COMMON = r'''
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
static void require(bool ok, const char *why) {
    if (!ok) { std::fprintf(stderr, "%s\n", why); std::exit(1); }
}
'''

WORKER_SUPPORT = COMMON + r'''
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <thread>
using std::string;
struct Thread {};
struct LEvent {
    std::mutex mutex;
    std::condition_variable cv;
    bool signaled = false;
    std::atomic<unsigned> waits{0};
    std::atomic<bool> parked{false};
};
static void leventInit(LEvent *event, bool signaled, bool autoclear) {
    require(!autoclear, "worker must use the checked manual-clear protocol");
    event->signaled = signaled;
}
static void leventClear(LEvent *event) {
    std::lock_guard<std::mutex> lock(event->mutex);
    event->signaled = false;
}
static void leventSignal(LEvent *event) {
    {
        std::lock_guard<std::mutex> lock(event->mutex);
        event->signaled = true;
    }
    event->cv.notify_one();
}
static bool leventWait(LEvent *event, std::uint64_t timeout) {
    require(timeout == UINT64_MAX, "idle worker must block without a polling timeout");
    std::unique_lock<std::mutex> lock(event->mutex);
    ++event->waits;
    event->parked = true;
    event->cv.wait(lock, [&] { return event->signaled; });
    event->parked = false;
    return true;
}
static std::atomic<int> decoded{0}, allocated{0};
static std::atomic<bool> block_decode{false}, entered_decode{false}, release_decode{false};
static unsigned char *decode(const char *path, int width, int height, int *iw, int *ih) {
    ++decoded;
    entered_decode = true;
    while (block_decode && !release_decode)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    *iw = width; *ih = height;
    if (path[0] == '!') return nullptr;
    auto *pixels = static_cast<unsigned char *>(std::malloc(4));
    require(pixels != nullptr, "host allocation failed");
    ++allocated;
    pixels[0] = static_cast<unsigned char>(path[0]);
    return pixels;
}
static unsigned char *extract_cbz_cover_pixels(const char *p, int w, int h, int *iw, int *ih) {
    return decode(p,w,h,iw,ih);
}
static unsigned char *extract_pdf_cover_pixels(const char *p, int w, int h, int *iw, int *ih) {
    return decode(p,w,h,iw,ih);
}
template<class Predicate> static void until(Predicate predicate, const char *why) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    while (!predicate()) {
        require(std::chrono::steady_clock::now() < deadline, why);
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
}
'''

WORKER_CASES = r'''
static void dispatch(CoverLoader &cl, int index, char name) {
    require(!__atomic_load_n(&cl.result_ready, __ATOMIC_ACQUIRE), "main overwrote an unconsumed result");
    cl.job_path[0] = name; cl.job_path[1] = 0;
    cl.job_tw = 140; cl.job_th = 210; cl.job_index = index; cl.job_is_pdf = (index % 2) != 0;
    __atomic_store_n(&cl.job_ready, 1, __ATOMIC_RELEASE);
    leventSignal(&cl.wake);
}
static void consume(CoverLoader &cl, int index, char name) {
    until([&] { return __atomic_load_n(&cl.result_ready, __ATOMIC_ACQUIRE) != 0; }, "worker lost job signal");
    require(cl.result_index == index && cl.result_iw == 140 && cl.result_ih == 210,
            "worker result lost its job identity/dimensions");
    if (name == '!') require(!cl.result_pixels, "failed decode unexpectedly returned pixels");
    else {
        require(cl.result_pixels && cl.result_pixels[0] == static_cast<unsigned char>(name),
                "worker read overwritten job data");
        std::free(cl.result_pixels); --allocated;
    }
    cl.result_pixels = nullptr;
    __atomic_store_n(&cl.result_ready, 0, __ATOMIC_RELEASE);
    leventSignal(&cl.wake);
}
static void stop(CoverLoader &cl) {
    __atomic_store_n(&cl.should_exit, 1, __ATOMIC_RELEASE);
    __atomic_store_n(&cl.job_ready, 0, __ATOMIC_RELEASE);
    leventSignal(&cl.wake);
}
int main() {
    CoverLoader cl;
    leventInit(&cl.wake, false, false);
    // Job/signal arrive before the worker clears its event: the predicate must
    // still let it run. This was the important lost-wake boundary.
    dispatch(cl, 0, 'A');
    std::thread worker(cover_loader_worker, &cl);
    until([&] { return __atomic_load_n(&cl.result_ready, __ATOMIC_ACQUIRE) != 0; }, "pre-start job was lost");
    until([&] { return cl.wake.parked.load(); }, "worker did not block while the main thread owns its result");
    const unsigned waits = cl.wake.waits.load();
    std::this_thread::sleep_for(std::chrono::milliseconds(25));
    require(cl.wake.waits == waits && decoded == 1, "worker is polling/re-decoding while idle");
    consume(cl, 0, 'A');
    // Acknowledgement and next dispatch coalesce; both orderings around wait
    // must remain safe. Alternate PDF/comic jobs through the real worker.
    for (int i = 1; i <= 64; ++i) {
        const char name = static_cast<char>('A' + i % 26);
        dispatch(cl, i, name);
        consume(cl, i, name);
    }
    dispatch(cl, 65, '!'); consume(cl, 65, '!');
    dispatch(cl, 66, 'Z'); consume(cl, 66, 'Z');
    until([&] { return cl.wake.parked.load(); }, "empty worker did not park");
    stop(cl); worker.join();
    require(allocated == 0 && decoded == 67, "idle shutdown leaked/repeated a result");

    CoverLoader busy;
    leventInit(&busy.wake, false, false);
    entered_decode = false; release_decode = false; block_decode = true;
    dispatch(busy, 100, 'B');
    std::thread busy_worker(cover_loader_worker, &busy);
    until([&] { return entered_decode.load(); }, "decoder did not begin");
    stop(busy); release_decode = true;
    busy_worker.join();
    require(busy.result_pixels && busy.result_index == 100, "shutdown lost a completed in-flight result");
    std::free(busy.result_pixels); --allocated;
    require(allocated == 0, "busy shutdown leaked the final result");
    std::puts("manual-event cover jobs, idle block, failure recovery and shutdown: PASS");
}
'''

STATUS_SUPPORT = COMMON + r'''
#include <ctime>
#include "WookPower.hpp"
using u32 = std::uint32_t;
using PsmChargerType = int;
#define R_SUCCEEDED(result) ((result) == 0)
static std::uint32_t ticks = 0;
static int time_queries = 0, battery_queries = 0, charger_queries = 0;
static int charge = 0, battery = 80;
static bool battery_available = true;
static std::time_t seconds = 23 * 3600 + 58 * 60;
static std::uint32_t SDL_GetTicks() { return ticks; }
static std::time_t fake_time(std::time_t *) { ++time_queries; return seconds; }
static std::tm *fake_localtime(const std::time_t *value) { return std::gmtime(value); }
static int psmGetChargerType(PsmChargerType *value) { ++charger_queries; *value=charge; return 0; }
static int psmGetBatteryChargePercentage(u32 *value) {
    ++battery_queries; *value=static_cast<u32>(battery); return battery_available ? 0 : -1;
}
#define time fake_time
#define localtime fake_localtime
'''

STATUS_CASES = r'''
#undef time
#undef localtime
int main() {
    wookpower::FrameGate frames;
    int presents=0;
    for (ticks=0; ticks<=61000; ticks+=16) {
        seconds = 23 * 3600 + 58 * 60 + ticks / 1000;
        frames.invalidate(StatusBar_PollChanged());
        if (frames.take(true)) ++presents;
    }
    // No input/charge change: first frame plus the visible minute boundary.
    require(presents==2, "unchanged status caused continuous idle rendering");
    require(std::strcmp(Clock_GetCurrentTime(), "23:59")==0, "24-hour clock is incorrect");
    require(time_queries<=61 && battery_queries<=13 && charger_queries<=13,
            "system status services were polled at the input/frame rate");

    ticks=66000; battery=79;
    require(StatusBar_PollChanged(), "charge percentage change did not invalidate display");
    ticks=66016;
    require(!StatusBar_PollChanged(), "unchanged charge repeated invalidation");
    ticks=71000; charge=1;
    require(StatusBar_PollChanged(), "charger connection did not invalidate display");
    ticks=76000; battery_available=false;
    require(!StatusBar_PollChanged(), "temporary service failure replaced a valid display");
    const int unavailable_queries=battery_queries;
    for (int n=1; n<=100; ++n) { ticks=76000+n*16; StatusBar_PollChanged(); }
    require(battery_queries==unavailable_queries, "failed battery service was polled continuously");

    ticks=0xfffffff0u; seconds=60; StatusBar_PollChanged();
    const int before_wrap=time_queries;
    ticks=0x20u; StatusBar_PollChanged();
    require(time_queries==before_wrap, "SDL tick wrap caused a premature clock poll");
    ticks=0x500u; seconds=120;
    require(StatusBar_PollChanged() && time_queries==before_wrap+1,
            "SDL tick wrap stalled status updates");

    // Each external visual event creates one frame; ordinary profile remains
    // unrestricted, and returning to saver does not redraw an unchanged view.
    require(!frames.take(true), "unchanged frame was retained as dirty");
    frames.invalidate(); require(frames.take(true), "cover/input/async dirty frame was skipped");
    require(!frames.take(true), "one invalidation caused multiple frames");
    for (int i=0;i<10;++i) require(frames.take(false), "normal rendering profile was gated");
    require(!frames.take(true), "profile switch retained a spurious dirty frame");
    std::puts("real status polling, minute/charge invalidation, tick wrap and idle frames: PASS");
}
'''

READER_SUPPORT = COMMON + r'''
#include "WookPower.hpp"
static bool configStatusBar = true, configScreenButtons = true;
static std::uint32_t ticks = 0;
static std::uint32_t SDL_GetTicks() { return ticks; }
namespace wook {
static std::string state_error;
static const std::string &last_error() { return state_error; }
}
struct TestLayout {
    bool loading = false, completed = false;
    bool is_loading() const { return loading; }
    bool pending_work() const { return completed; }
};
class BookReader {
public:
    TestLayout *layout = nullptr;
    bool permStatusBar = false, _dirty = true, _overlay_active = false;
    bool _last_status_visible = false, _last_nav_visible = false;
    std::uint32_t _status_hide_at = 0, _btn_hide_at = 0, _notice_hide_at = 0;
    std::string _notice, _shown_state_error;
    bool is_loading() const;
    bool status_visible() const;
    bool needs_redraw() const;
    // Only the draw bookkeeping is doubled; the next-frame decision under
    // test is extracted verbatim from the installed reader implementation.
    void presented() {
        _dirty = false;
        _shown_state_error = wook::last_error();
        _last_status_visible = status_visible();
        _last_nav_visible = configScreenButtons && (std::int32_t)(_btn_hide_at-ticks)>0;
        if (!_notice.empty() && (std::int32_t)(_notice_hide_at-ticks)<=0) _notice.clear();
        if (layout) layout->completed = false;
    }
};
'''

READER_CASES = r'''
int main() {
    BookReader reader;
    TestLayout layout;
    reader.layout = &layout;
    require(reader.needs_redraw(), "new reader did not draw its initial state");
    reader.presented();
    for (ticks=0; ticks<2000; ticks+=16)
        require(!reader.needs_redraw(), "static page continuously invalidated itself");

    layout.loading=true;
    require(reader.needs_redraw(), "in-flight current page stopped being polled");
    layout.loading=false; layout.completed=true;
    require(reader.needs_redraw(), "completed asynchronous result was not consumed");
    reader.presented();
    require(!reader.needs_redraw(), "consumed asynchronous result kept rendering");

    wook::state_error="SD is full";
    require(reader.needs_redraw(), "state write failure did not become visible");
    reader.presented();
    require(!reader.needs_redraw(), "unchanged persistent error kept the GPU active");
    wook::state_error.clear();
    require(reader.needs_redraw(), "state recovery left a stale error visible");
    reader.presented();

    reader._dirty=true; // menu open/close, retry, page move and input use this.
    require(reader.needs_redraw(), "modal/input invalidation was skipped");
    reader.presented();
    reader._status_hide_at=ticks+1000;
    require(reader.needs_redraw(), "status appearance was skipped");
    reader.presented();
    ticks+=999;
    require(!reader.needs_redraw(), "static visible status caused extra frames");
    ++ticks;
    require(reader.needs_redraw(), "status expiry left stale controls on screen");
    reader.presented();

    reader._btn_hide_at=ticks+1000;
    require(reader.needs_redraw(), "navigation appearance was skipped");
    reader.presented();
    ticks+=399;
    require(!reader.needs_redraw(), "navigation was redrawn before its fade");
    ticks+=2;
    require(reader.needs_redraw(), "navigation fade stopped animating");
    ticks+=600;
    require(reader.needs_redraw(), "navigation disappearance was skipped");
    reader.presented();
    require(!reader.needs_redraw(), "expired navigation kept the GPU active");

    reader._notice="saved"; reader._notice_hide_at=ticks+1000;
    reader._dirty=true; reader.presented();
    require(!reader.needs_redraw(), "static notification continuously invalidated itself");
    ticks+=1000;
    require(reader.needs_redraw(), "notification expiry left stale text on screen");
    reader.presented();
    require(!reader.needs_redraw(), "cleared notification kept the GPU active");

    ticks=0xfffffff0u;
    reader._status_hide_at=ticks+1000;
    reader.presented();
    ticks=0x20u;
    require(reader.status_visible() && !reader.needs_redraw(), "tick wrap prematurely hid status");
    ticks=0x500u;
    require(!reader.status_visible() && reader.needs_redraw(), "tick wrap stalled status expiry");
    std::puts("actual reader redraw decisions, async completion, dialogs and deadline expiry: PASS");
}
'''

PREFETCH_SUPPORT = COMMON + r'''
#include "WookPower.hpp"
using Result = int;
#define R_SUCCEEDED(result) ((result) == 0)
namespace wook {
static bool saver = true;
static bool battery_saver() { return saver; }
}
struct Thread { bool started=false; };
static int creates=0, starts=0, joins=0, closes=0;
static void Log_Error(const std::string &) {}
static Result threadCreate(Thread *, void (*)(void *), void *, void *, size_t, int, int) {
    ++creates; return 0;
}
static Result threadStart(Thread *thread) { thread->started=true; ++starts; return 0; }
static void threadWaitForExit(Thread *thread) {
    require(thread->started, "prefetch joined an inactive thread"); ++joins;
}
static void threadClose(Thread *thread) { thread->started=false; ++closes; }
class CBZPageLayout {
public:
    static const int N_PF=3;
    Thread _pf_thread[N_PF];
    bool _pf_active[N_PF]={}, _pf_ok[N_PF]={};
    int _pf_cancel[N_PF]={}, _pf_page[N_PF]={}, _pf_w[N_PF]={}, _pf_h[N_PF]={};
    unsigned char *_pf_pixels[N_PF]={};
    std::string _pf_page_name[N_PF];
    std::vector<std::string> _page_names={"001.png","002.png","003.png","004.png"};
    static void prefetch_entry_0(void *) {}
    static void prefetch_entry_1(void *) {}
    static void prefetch_entry_2(void *) {}
    void start_prefetch(int, int, int);
    void cancel_prefetch();
};
'''

PREFETCH_CASES = r'''
int main() {
    CBZPageLayout page;
    wook::saver=true;
    page.start_prefetch(2,0,3);
    require(creates==1 && starts==1 && page._pf_active[0] && !page._pf_active[1] && !page._pf_active[2],
            "saver launched more than the next-page worker");
    wook::saver=false;
    page.start_prefetch(3,1,-1);
    require(joins==1 && closes==1 && creates==3 && starts==3 &&
            page._pf_active[0] && page._pf_active[1] && !page._pf_active[2],
            "normal profile did not preserve bounded forward/backward prefetch");
    wook::saver=true;
    page.start_prefetch(-1,2,-1);
    require(joins==3 && closes==3 && creates==3 &&
            !page._pf_active[0] && !page._pf_active[1] && !page._pf_active[2],
            "profile switch did not clean all previous workers at final page");
    std::puts("actual prefetch method, energy/normal profile and transition cleanup: PASS");
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="fully applied source tree including enhance_power.py")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    chooser_path = args.source / "source/menus/book-chooser/MenuChooser.cpp"
    status_path = args.source / "source/status_bar.c"
    pages_path = args.source / "source/menus/book/CBZPageLayout.cpp"
    reader_path = args.source / "source/menus/book/BookReader.cpp"
    chooser = chooser_path.read_text(encoding="utf-8")
    status = status_path.read_text(encoding="utf-8")
    pages = pages_path.read_text(encoding="utf-8")
    reader = reader_path.read_text(encoding="utf-8")
    worker = between(chooser, "struct CoverLoader {", "// ── Main chooser")
    clock = between(status, "// Main-thread status snapshot.", "static void StatusBar_GetBatteryStatus(")
    prefetch = between(pages, "void CBZPageLayout::start_prefetch(", "void CBZPageLayout::cancel_prefetch(")
    reader_state = between(reader, "bool BookReader::is_loading()", "bool BookReader::has_error()")
    reader_state += between(reader, "bool BookReader::status_visible()", "void BookReader::show_status_bar()")
    worker_cases = WORKER_CASES
    if re.search(r"\b(?:std::)?string\s+job_path\b", worker):
        worker_cases = worker_cases.replace("cl.job_path[0] = name; cl.job_path[1] = 0;",
                                            "cl.job_path = std::string(1, name);")
    programs = {
        "cover_event": WORKER_SUPPORT + worker + worker_cases,
        "status_frames": STATUS_SUPPORT + clock + STATUS_CASES,
        "reader_redraw": READER_SUPPORT + reader_state + READER_CASES,
        "prefetch_profile": PREFETCH_SUPPORT + prefetch + PREFETCH_CASES,
    }
    results = {}
    with tempfile.TemporaryDirectory(prefix="wook-power-") as tmp:
        work = Path(tmp)
        for name, code in programs.items():
            source, binary = work / (name + ".cpp"), work / name
            source.write_text(code, encoding="utf-8")
            subprocess.run([args.cxx, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                            "-pthread", "-fsanitize=address,undefined",
                            "-I" + str(args.source / "source/menus/book"),
                            str(source), "-o", str(binary)], check=True)
            env = {**os.environ, "ASAN_OPTIONS": "detect_leaks=0", "TZ": "UTC"}
            result = subprocess.run([str(binary)], check=True, capture_output=True,
                                    text=True, timeout=12, env=env)
            results[name] = result.stdout.strip()
            print(result.stdout.strip())
    report = {"ok": True, "checks": results,
              "scope": "host source-derived control-flow tests; no console power measurements",
              "source_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in [chooser_path, status_path, pages_path, reader_path]}}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
