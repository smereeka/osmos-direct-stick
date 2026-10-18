#!/usr/bin/env python3
"""Exercise actual applied reader methods with small host-only SDL/libnx doubles.

This is a focused integration test for early Y input, first-image ownership and
cover thread failure cleanup. It does not simulate Nintendo Switch rendering.
Pass a fresh upstream tree after the complete RU3 patch pipeline.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile


def between(text, start, end):
    a = text.index(start)
    return text[a:text.index(end, a)]


def method(text, signature):
    start = text.index(signature)
    position = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[position] == "{") - (text[position] == "}")
        position += 1
    return text[start:position] + "\n"


PAGE_SUPPORT = r'''
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>
#include "ReaderView.hpp"
static void require(bool ok, const char *why) {
    if (!ok) { std::fprintf(stderr, "%s\n", why); std::exit(1); }
}
struct Thread {};
struct SDL_Rect { int x=0, y=0, w=1280, h=720; };
using SDL_Texture = void;
static void *RENDERER = nullptr;
static int joined=0, closed=0, buffers_freed=0;
static void threadWaitForExit(Thread *) { ++joined; }
static void threadClose(Thread *) { ++closed; }
[[maybe_unused]] static void SDL_free(void *ptr) {
    require(joined > 0, "early image must be released only after enum join");
    ++buffers_freed; std::free(ptr);
}
static void FreeTextureIfNeeded(SDL_Texture **p) { *p = nullptr; }
static void SDL_RenderGetViewport(void *, SDL_Rect *) {}
[[maybe_unused]] static void Log_Error(const std::string &) {}
static void Log_Write(const std::string &) {}
class CBZPageLayout {
public:
    bool _valid=false, _enumerating=false, _spread_mode=false;
    int _rotation=0, _current_page=0, _start_page=0, _page_count=0;
    int _enum_count=0, _first_image_ready=0;
    Thread _enum_thread;
    std::vector<std::string> _page_names;
    std::string _archive_path="fixture.cbr", _enum_current_name, _first_image_name, _page_error, _enum_error;
    void *_first_image_raw=nullptr;
    size_t _first_image_size=0;
    SDL_Rect _viewport;
    SDL_Texture *_tex=nullptr, *_tex_r=nullptr;
    int loads=0, prefetches=0;
    bool fail_load=false;
    bool is_valid() const { return _valid; }
    void free_ready_textures() {}
    void cancel_prefetch() {}
    int current_page() const { return _current_page; }
    int rotation_degrees() const { return _rotation; }
    int view_mode() const { return _spread_mode ? 2 : (_rotation == 90 ? 1 : 0); }
    void set_view_mode(int);
    void load_page_texture(int page, bool) {
        ++loads;
        require(page >= 0 && static_cast<size_t>(page) < _page_names.size(),
                "Y reached page loading before the name vector was published");
        _current_page=page;
        _tex=fail_load ? nullptr : reinterpret_cast<void *>(1);
        if (fail_load) _page_error="decode failed";
    }
    void start_prefetch(int, int, int) { ++prefetches; }
    void toggle_spread();
    void finish_enumeration();
};
enum BookPageLayout { BookPageLayoutPortrait, BookPageLayoutLandscape,
                      BookPageLayoutVertical };
class BookReader {
public:
    bool _is_cbz=true;
    BookPageLayout _currentPageLayout=BookPageLayoutPortrait;
    CBZPageLayout *layout=nullptr;
    void switch_current_page_layout(BookPageLayout, int) {}
    bool is_loading() const { return layout && layout->_enumerating; }
    bool has_error() const { return layout && !layout->_page_error.empty(); }
    bool navLandscape() const;
    void show_status_bar() {}
    void reset_nav_buttons() {}
    void save_view() {}
    void switch_page_layout();
};
'''

PAGE_CASES = r'''
int main() {
    // UnRAR has reported an image in its private pending vector, but has not
    // published the completed names. Both public Y entry points must be safe.
    CBZPageLayout opening;
    opening._enumerating=true; opening._enum_count=1;
    opening.toggle_spread();
    require(opening.loads==0 && opening._rotation==0 && !opening._spread_mode,
            "opening archive accepted spread input");
    BookReader reader; reader.layout=&opening;
    reader.switch_page_layout();
    require(!reader.navLandscape() && opening.loads==0,
            "opening archive changed navigation direction");
    reader.layout=nullptr; reader.switch_page_layout();
    require(!reader.navLandscape(), "missing layout changed navigation direction");

    CBZPageLayout ready;
    ready._valid=true; ready._page_names={"001.png", "002.png"};
    reader.layout=&ready;
    reader.switch_page_layout();
    require(ready._rotation==90 && !ready._spread_mode && reader.navLandscape(),
            "ready-page rotation changed");
    reader.switch_page_layout();
    require(ready._rotation==0 && ready._spread_mode && !reader.navLandscape(),
            "ready-page spread changed");
    reader.switch_page_layout();
    require(ready._rotation==0 && !ready._spread_mode && ready.loads==3,
            "ready-page mode cycle changed");

    CBZPageLayout completed;
    completed._enumerating=true; completed._start_page=1;
    completed._page_names={"001.png", "002.png"};
    completed._first_image_raw=std::malloc(32); completed._first_image_size=32;
    completed._first_image_ready=1;
    completed.finish_enumeration();
    require(joined==1 && closed==1 && buffers_freed==1,
            "finished enumeration did not release the unused first image exactly once");
    require(!completed._enumerating && !completed._first_image_raw &&
            completed._first_image_size==0 && completed._first_image_ready==0,
            "finished enumeration retained the early-image buffer");
    require(completed._valid && completed._current_page==1 && completed.loads==1,
            "first-image release changed the saved starting page");
    completed.finish_enumeration();
    require(joined==1 && closed==1 && buffers_freed==1,
            "repeated finish attempted double cleanup");

    CBZPageLayout early;
    early._enumerating=true; early._valid=true;
    early._page_names={"001.png", "002.png"}; early._first_image_name="001.png";
    early._tex=reinterpret_cast<void *>(1); early._first_image_ready=1;
    early.finish_enumeration();
    require(early._valid && early._tex && early.loads==0 && early.prefetches==1,
            "already displayed first page was reloaded or discarded");
    require(buffers_freed==1, "already consumed first image was freed twice");

    CBZPageLayout empty;
    empty._enumerating=true;
    empty._first_image_raw=std::malloc(16); empty._first_image_size=16;
    empty.finish_enumeration();
    require(!empty._valid && empty.loads==0 && buffers_freed==2,
            "empty archive cleanup or validity changed");
    require(!empty._page_error.empty(), "empty archive did not retain an error for Retry/Back");
    CBZPageLayout broken;
    broken._enumerating=true; broken.fail_load=true;
    broken._page_names={"001.png"};
    broken.finish_enumeration();
    require(!broken._valid && !broken._tex && !broken._page_error.empty(),
            "failed decode was incorrectly promoted to a valid page");
    broken.finish_enumeration();
    require(!broken._valid && !broken._page_error.empty(),
            "read error disappeared on the next frame");
    std::puts("reader startup/input/first-image ownership: PASS");
}
'''

COVER_SUPPORT = r'''
#include <cstdio>
#include <cstdlib>
#include <string>
#include <cstddef>
using std::string;
static void require(bool ok, const char *why) {
    if (!ok) { std::fprintf(stderr, "%s\n", why); std::exit(1); }
}
using Result = int;
#define R_SUCCEEDED(r) ((r) == 0)
struct Thread { int id; };
struct LEvent {};
[[maybe_unused]] static void leventInit(LEvent *, bool, bool) {}
[[maybe_unused]] static void leventSignal(LEvent *) {}
static const int N_WORKERS=2;
static int failure[N_WORKERS], created[N_WORKERS], started[N_WORKERS];
static int closes[N_WORKERS], waits[N_WORKERS], create_calls;
static void cover_loader_worker(void *) {}
[[maybe_unused]] static void Log_Error(const std::string &) {}
static Result threadCreate(Thread *thread, void (*)(void *), void *, void *,
                           size_t, int, int) {
    const int i=create_calls++;
    require(i < N_WORKERS, "unexpected cover thread creation");
    if (failure[i]==1) return 101;
    thread->id=i+1; created[i]=1; return 0;
}
static int index_of(Thread *thread) {
    require(thread->id > 0 && thread->id <= N_WORKERS,
            "invalid cover thread handle used");
    const int i=thread->id-1;
    require(created[i] && !closes[i], "uncreated or closed cover thread used");
    return i;
}
static Result threadStart(Thread *thread) {
    const int i=index_of(thread);
    if (failure[i]==2) return 102;
    started[i]=1; return 0;
}
static void threadWaitForExit(Thread *thread) {
    const int i=index_of(thread);
    require(started[i], "attempted to join a cover thread that never started");
    ++waits[i];
}
static void threadClose(Thread *thread) { ++closes[index_of(thread)]; }
'''


def cover_program(source):
    loader = between(source, "struct CoverLoader {", "static void cover_loader_worker")
    startup = between(source, "  // Start background cover loaders on cores 1 and 2",
                      "  // Main loop: modal input is exclusive.")
    shutdown = between(source, "  // Stop all background cover loader threads",
                       "  library_text.clear(); g_cover_cache.clear();")
    demand = source.index("    const int first_card =")
    dispatch_start = source.index("    for (int w = 0; w < N_WORKERS; ++w) {", demand)
    inner_start = source.index("      for (int card = first_card; card < last_card; ++card) {", dispatch_start)
    # Use the real outer dispatch loop and gate; the expensive decoder job is
    # represented by a counter after that gate.
    dispatch = source[dispatch_start:inner_start] + "      ++dispatched[w];\n    }\n"
    return COVER_SUPPORT + loader + r'''
int main() {
  for (int first=0; first<3; ++first) for (int second=0; second<3; ++second) {
    failure[0]=first; failure[1]=second; create_calls=0;
    for (int i=0; i<N_WORKERS; ++i)
      created[i]=started[i]=closes[i]=waits[i]=0;
    CoverLoader cl[N_WORKERS];
    int cl_in_flight[N_WORKERS]={-1,-1}, dispatched[N_WORKERS]={};
''' + startup + dispatch + shutdown + r'''
    require(create_calls==N_WORKERS, "cover startup skipped an independent worker");
    for (int i=0; i<N_WORKERS; ++i) {
      require(dispatched[i]==(failure[i]==0), "job sent to inactive cover worker");
      require(waits[i]==(failure[i]==0), "cover join count incorrect");
      require(closes[i]==(failure[i]!=1), "cover handle leaked or closed twice");
    }
  }
  std::puts("cover startup/dispatch/shutdown (9 failure combinations): PASS");
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="fresh, fully patched upstream source tree")
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    page = (args.source / "source/menus/book/CBZPageLayout.cpp").read_text()
    reader = (args.source / "source/menus/book/BookReader.cpp").read_text()
    chooser = (args.source / "source/menus/book-chooser/MenuChooser.cpp").read_text()
    actual_methods = method(page, "void CBZPageLayout::toggle_spread()")
    actual_methods += method(page, "void CBZPageLayout::set_view_mode(int mode)")
    actual_methods += method(page, "void CBZPageLayout::finish_enumeration()")
    actual_methods += method(reader, "void BookReader::switch_page_layout()")
    actual_methods += method(reader, "bool BookReader::navLandscape() const")
    programs = {"reader_startup": PAGE_SUPPORT + actual_methods + PAGE_CASES,
                "cover_startup": cover_program(chooser)}
    with tempfile.TemporaryDirectory(prefix="wook-reader-startup-") as temp:
        for name, code in programs.items():
            cpp, binary = Path(temp) / (name + ".cpp"), Path(temp) / name
            cpp.write_text(code)
            subprocess.run([args.cxx, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra",
                            "-Werror", "-fsanitize=address,undefined", str(cpp),
                            "-I" + str(args.source.resolve() / "source/menus/book"),
                            "-o", str(binary)], check=True)
            env = os.environ.copy()
            # LeakSanitizer needs /proc access unavailable in some sandboxes.
            # The cases explicitly count ownership transfers and close/free calls.
            env["ASAN_OPTIONS"] = env.get("ASAN_OPTIONS", "") + ":detect_leaks=0"
            subprocess.run([str(binary)], check=True, timeout=10, env=env)


if __name__ == "__main__":
    main()
