// Host regression driver for the exact backend linked into the Switch NRO.
// The wrappers below only gate real UnRAR callbacks for deterministic cancellation
// tests; normal callback delivery and all decompression remain unchanged.
#include "WookRAR.hpp"
#ifndef _UNIX
#define _UNIX
#endif
#include "wookrar/dll.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <clocale>
#include <condition_variable>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <future>
#include <iostream>
#include <map>
#include <mutex>
#include <random>
#include <string>
#include <thread>
#include <vector>

using namespace std::chrono_literals;

[[noreturn]] static void fail(const std::string &message) {
    std::cerr << "FAIL: " << message << '\n';
    std::exit(2);
}
static void require(bool condition, const std::string &message) {
    if (!condition) fail(message);
}
static std::string quoted(const std::string &value) {
    std::string out = "\"";
    const char hex[] = "0123456789abcdef";
    for (unsigned char c : value) {
        if (c == '\\' || c == '"') { out += '\\'; out += static_cast<char>(c); }
        else if (c < 32) { out += "\\u00"; out += hex[c >> 4]; out += hex[c & 15]; }
        else out += static_cast<char>(c);
    }
    return out + '"';
}
static const char *error_name(wookrar::Error error) {
    static const char *names[] = {"none", "cancelled", "not_found", "corrupt", "unsupported", "size_limit", "no_memory", "io"};
    return names[static_cast<unsigned>(error)];
}
static std::string result_json(const wookrar::Result &r) {
    return "\"ok\":" + std::string(r.ok() ? "true" : "false") +
        ",\"error\":" + quoted(error_name(r.error)) + ",\"rar_code\":" +
        std::to_string(r.rar_code) + ",\"detail\":" + quoted(r.detail);
}

static thread_local unsigned alloc_count = 0, free_count = 0;
static void *allocate(std::size_t size) { ++alloc_count; return std::malloc(size); }
static void deallocate(void *p) { if (p) ++free_count; std::free(p); }
static void *allocate_nothing(std::size_t) { ++alloc_count; return nullptr; }

struct Payload {
    wookrar::Result result;
    std::vector<unsigned char> bytes;
    unsigned allocations = 0, frees = 0;
};
static Payload extract(const std::string &path, const std::string &name,
                       volatile int *cancel = nullptr, bool fail_alloc = false) {
    void *data = reinterpret_cast<void *>(1);
    std::size_t size = 123;
    alloc_count = free_count = 0;
    Payload payload;
    payload.result = wookrar::extract(path.c_str(), name, &data, &size, cancel,
                                     fail_alloc ? allocate_nothing : allocate, deallocate);
    if (payload.result.ok()) {
        require(data != nullptr && size != 0, "successful extraction did not publish a complete buffer");
        auto *bytes = static_cast<unsigned char *>(data);
        payload.bytes.assign(bytes, bytes + size);
        deallocate(data);
    } else {
        require(data == nullptr && size == 0, "failed extraction published partial data");
    }
    payload.allocations = alloc_count;
    payload.frees = free_count;
    if (!fail_alloc) require(alloc_count == free_count, "extraction leaked an allocated buffer");
    return payload;
}
static void write_bytes(const std::string &path, const std::vector<unsigned char> &data) {
    std::ofstream file(path, std::ios::binary);
    file.write(reinterpret_cast<const char *>(data.data()), data.size());
    require(file.good(), "cannot write safe test output " + path);
}
static std::vector<std::string> images(const std::string &path, bool *solid = nullptr) {
    std::vector<std::string> names;
    volatile int count = -1;
    wookrar::Info info;
    auto result = wookrar::list_images(path.c_str(), names, &count, &info);
    require(result.ok(), "list failed: " + wookrar::describe(result));
    require(count == static_cast<int>(names.size()), "progress count does not match complete list");
    require(!names.empty(), "archive has no test images");
    if (solid) *solid = info.solid;
    return names;
}

struct Gate {
    std::mutex mutex;
    std::condition_variable condition;
    unsigned member = 1;
    bool entered = false, released = false;
    void hit() {
        std::unique_lock<std::mutex> lock(mutex);
        if (entered) return;
        entered = true;
        condition.notify_all();
        condition.wait(lock, [&] { return released; });
    }
    bool wait() {
        std::unique_lock<std::mutex> lock(mutex);
        return condition.wait_for(lock, 5s, [&] { return entered; });
    }
    void release() {
        std::lock_guard<std::mutex> lock(mutex);
        released = true;
        condition.notify_all();
    }
};
static thread_local UNRARCALLBACK original_callback = nullptr;
static thread_local LPARAM original_userdata = 0;
static thread_local Gate *callback_gate = nullptr;
static thread_local unsigned process_member = 0;
static int CALLBACK gated_callback(UINT message, LPARAM, LPARAM p1, LPARAM p2) {
    if (message == UCM_PROCESSDATA && callback_gate && process_member == callback_gate->member)
        callback_gate->hit();
    return original_callback(message, original_userdata, p1, p2);
}
extern "C" HANDLE __real_RAROpenArchiveEx(struct RAROpenArchiveDataEx *);
extern "C" int __real_RARProcessFileW(HANDLE, int, wchar_t *, wchar_t *);
extern "C" HANDLE __wrap_RAROpenArchiveEx(struct RAROpenArchiveDataEx *options) {
    original_callback = options->Callback;
    original_userdata = options->UserData;
    process_member = 0;
    options->Callback = gated_callback;
    return __real_RAROpenArchiveEx(options);
}
extern "C" int __wrap_RARProcessFileW(HANDLE handle, int operation, wchar_t *dest, wchar_t *name) {
    ++process_member;
    return __real_RARProcessFileW(handle, operation, dest, name);
}

static void test_cancel(const std::string &path, const std::string &target,
                        unsigned predecessor_member, unsigned target_member) {
    const auto baseline = extract(path, target);
    require(baseline.result.ok(), "cancellation baseline failed");
    volatile int cancelled = 1;
    auto initial = extract(path, target, &cancelled);
    require(initial.result.error == wookrar::Error::cancelled, "pre-cancelled extract was not cancelled");
    std::vector<std::string> names{"stale"};
    volatile int count = 42;
    auto list = wookrar::list_images(path.c_str(), names, &count, nullptr, &cancelled);
    require(list.error == wookrar::Error::cancelled && names.empty() && count == 0,
            "cancelled listing retained names or count");

    for (unsigned member : {predecessor_member, target_member}) {
        Gate gate;
        gate.member = member;
        volatile int flag = 0;
        Payload interrupted;
        std::thread worker([&] {
            callback_gate = &gate;
            interrupted = extract(path, target, &flag);
            callback_gate = nullptr;
        });
        const bool observed = gate.wait();
        __atomic_store_n(&flag, 1, __ATOMIC_RELEASE);
        gate.release();
        worker.join();
        require(observed, "real predecessor/target PROCESSDATA callback was not reached");
        require(interrupted.result.error == wookrar::Error::cancelled, "in-flight extraction not cancelled");
        require(interrupted.allocations == (member == target_member ? 1u : 0u),
                "cancellation did not occur in the selected member");
        auto reopen = extract(path, target);
        require(reopen.result.ok() && reopen.bytes == baseline.bytes, "reopen after cancellation failed");
    }

    // Hold a real decode callback while a second worker is waiting for the
    // backend's session lock. It must observe cancellation without that lock.
    Gate holder_gate;
    holder_gate.member = predecessor_member;
    Payload holder_result, waiter_result;
    std::thread holder([&] {
        callback_gate = &holder_gate;
        holder_result = extract(path, target);
        callback_gate = nullptr;
    });
    bool held = holder_gate.wait();
    volatile int waiter_flag = 0;
    std::atomic<bool> started{false};
    std::promise<void> done;
    auto finished = done.get_future();
    std::thread waiter([&] {
        started.store(true, std::memory_order_release);
        waiter_result = extract(path, target, &waiter_flag);
        done.set_value();
    });
    while (!started.load(std::memory_order_acquire)) std::this_thread::yield();
    std::this_thread::sleep_for(20ms);
    __atomic_store_n(&waiter_flag, 1, __ATOMIC_RELEASE);
    bool prompt = finished.wait_for(2s) == std::future_status::ready;
    holder_gate.release();
    holder.join();
    waiter.join();
    require(held && prompt, "mutex waiter could not cancel while another session was held");
    require(waiter_result.result.error == wookrar::Error::cancelled && waiter_result.allocations == 0,
            "mutex waiter entered the decoder after cancellation");
    require(holder_result.result.ok() && holder_result.bytes == baseline.bytes, "holder corrupted by waiter cancellation");
    require(extract(path, target).bytes == baseline.bytes, "post-lock cancellation reopen failed");
    std::cout << "{\"ok\":true,\"checks\":[\"pre-cancelled list/extract\",\"mid-predecessor callback\",\"mid-target callback\",\"mutex wait cancellation\",\"reopen after each cancellation\"]}\n";
}

static void exercise(const std::string &path, const std::string &directory, unsigned repeats) {
    bool solid = false;
    const auto physical = images(path, &solid);
    auto sorted = physical;
    std::sort(sorted.begin(), sorted.end());
    std::map<std::string, std::vector<unsigned char>> baseline;
    std::cout << "{\"ok\":true,\"solid\":" << (solid ? "true" : "false") << ",\"entries\":[";
    for (std::size_t i = 0; i < sorted.size(); ++i) {
        auto page = extract(path, sorted[i]);
        require(page.result.ok(), "baseline " + sorted[i] + ": " + wookrar::describe(page.result));
        baseline.emplace(sorted[i], page.bytes);
        const std::string file = "page-" + std::to_string(i) + ".bin";
        write_bytes(directory + "/" + file, page.bytes);
        if (i) std::cout << ',';
        std::cout << "{\"name\":" << quoted(sorted[i]) << ",\"size\":" << page.bytes.size() << ",\"file\":" << quoted(file) << '}';
    }
    auto order = sorted;
    std::reverse(order.begin(), order.end());
    std::mt19937 random(0x574f4f4b);
    unsigned sequential = 0;
    for (unsigned round = 0; round < 3; ++round) {
        if (round) std::shuffle(order.begin(), order.end(), random);
        for (const auto &name : order) {
            const auto page = extract(path, name);
            require(page.result.ok() && page.bytes == baseline.at(name), "backward/random read differs: " + name);
            ++sequential;
        }
    }
    for (unsigned i = 0; i < repeats; ++i) {
        require(images(path) == physical, "repeated listing changed after opening " + std::to_string(i));
        const auto &name = sorted[i % sorted.size()];
        const auto page = extract(path, name);
        require(page.result.ok() && page.bytes == baseline.at(name), "repeated opening corrupted data");
    }
    const unsigned workers = repeats ? 4 : 0, rounds = 12;
    std::vector<std::thread> threads;
    for (unsigned w = 0; w < workers; ++w) threads.emplace_back([&, w] {
        for (unsigned i = 0; i < rounds; ++i) {
            require(images(path) == physical, "concurrent list differs");
            const auto &name = sorted[(w + i) % sorted.size()];
            auto page = extract(path, name);
            require(page.result.ok() && page.bytes == baseline.at(name), "concurrent bytes differ");
        }
    });
    for (auto &thread : threads) thread.join();
    std::cout << "],\"random_backward_reads\":" << sequential << ",\"repeated_opens\":" << repeats
              << ",\"concurrent_workers\":" << workers << ",\"concurrent_cycles\":" << workers * rounds << "}\n";
}

static void mixed_stress(const std::string &good, const std::string &bad, const std::string &target) {
    const auto expected = extract(good, target);
    require(expected.result.ok(), "mixed-stress baseline failed");
    std::vector<std::thread> threads;
    for (unsigned worker = 0; worker < 4; ++worker) threads.emplace_back([&, worker] {
        for (unsigned i = 0; i < 16; ++i) {
            const bool corrupt = (i + worker) % 2;
            auto result = extract(corrupt ? bad : good, target);
            if (corrupt) require(!result.result.ok() && result.result.error != wookrar::Error::not_found,
                                 "bad archive lost its corruption result");
            else require(result.result.ok() && result.bytes == expected.bytes, "global error state leaked into valid session");
        }
    });
    for (auto &thread : threads) thread.join();
    std::cout << "{\"ok\":true,\"mixed_good_bad_operations\":64,\"workers\":4}\n";
}

int main(int argc, char **argv) {
    // The shared UnRAR build must interpret UTF-8 filenames even when the
    // process has never changed the default C locale, as on the Switch.
    require(std::strcmp(std::setlocale(LC_CTYPE, nullptr), "C") == 0,
            "host test must begin in the default C locale");
    require(argc >= 3, "usage: test_backend <inspect|extract|exercise|cancel|allocfail|mixed> <archive> ...");
    std::string mode = argv[1], archive = argv[2];
    if (mode == "inspect") {
        std::vector<std::string> names{"stale"};
        volatile int count = -1;
        wookrar::Info info;
        auto result = wookrar::list_images(archive.c_str(), names, &count, &info);
        if (!result.ok()) require(names.empty() && count == 0, "failed enumeration published a partial list");
        std::cout << '{' << result_json(result) << ",\"is_archive\":" << (wookrar::is_archive(archive.c_str()) ? "true" : "false")
                  << ",\"count\":" << count << ",\"solid\":" << (info.solid ? "true" : "false") << ",\"names\":[";
        for (std::size_t i = 0; i < names.size(); ++i) { if (i) std::cout << ','; std::cout << quoted(names[i]); }
        std::cout << "]}\n";
    } else if (mode == "extract" || mode == "allocfail") {
        require(argc == 5, "extract needs target and output filename");
        auto page = extract(archive, argv[3], nullptr, mode == "allocfail");
        if (page.result.ok()) write_bytes(argv[4], page.bytes);
        std::cout << '{' << result_json(page.result) << ",\"size\":" << page.bytes.size()
                  << ",\"allocations\":" << page.allocations << ",\"frees\":" << page.frees << "}\n";
    } else if (mode == "exercise") {
        require(argc == 5, "exercise needs dump directory and repeat count");
        exercise(archive, argv[3], static_cast<unsigned>(std::strtoul(argv[4], nullptr, 10)));
    } else if (mode == "cancel") {
        require(argc == 6, "cancel needs target, predecessor member number and target member number");
        test_cancel(archive, argv[3], std::strtoul(argv[4], nullptr, 10), std::strtoul(argv[5], nullptr, 10));
    } else if (mode == "mixed") {
        require(argc == 5, "mixed needs corrupt archive and target");
        mixed_stress(archive, argv[3], argv[4]);
    } else fail("unknown test mode");
    return 0;
}
