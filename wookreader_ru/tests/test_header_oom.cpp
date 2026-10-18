// Exercise the public header API's memory failure boundary with the exact
// reader wrapper. Only one allocation made inside RARReadHeaderEx is rejected;
// archive parsing and all later allocations use the production implementation.
#include "WookRAR.hpp"
#ifndef _UNIX
#define _UNIX
#endif
#include "wookrar/dll.hpp"

#include <cstdio>
#include <cstdlib>
#include <new>
#include <string>
#include <vector>

static bool arm_header_failure = false;
static bool fail_next_allocation = false;
static unsigned injected_failures = 0;

void *operator new(std::size_t size) {
    if (fail_next_allocation) {
        fail_next_allocation = false;
        ++injected_failures;
        throw std::bad_alloc();
    }
    if (void *data = std::malloc(size ? size : 1)) return data;
    throw std::bad_alloc();
}

void operator delete(void *data) noexcept { std::free(data); }
void operator delete(void *data, std::size_t) noexcept { std::free(data); }

extern "C" int __real_RARReadHeaderEx(HANDLE, RARHeaderDataEx *);
extern "C" int __wrap_RARReadHeaderEx(HANDLE archive, RARHeaderDataEx *header) {
    if (arm_header_failure) {
        arm_header_failure = false;
        fail_next_allocation = true;
    }
    const int result = __real_RARReadHeaderEx(archive, header);
    fail_next_allocation = false;
    return result;
}

static void require(bool condition, const char *message) {
    if (condition) return;
    std::fprintf(stderr, "FAIL: %s\n", message);
    std::exit(2);
}

int main(int argc, char **argv) {
    require(argc == 2, "usage: test_header_oom <synthetic archive>");
    std::vector<std::string> expected;
    auto initial = wookrar::list_images(argv[1], expected);
    require(initial.ok() && !expected.empty(), "baseline listing failed");

    std::vector<std::string> names{"stale.png"};
    volatile int count = 123;
    arm_header_failure = true;
    auto result = wookrar::list_images(argv[1], names, &count);
    require(injected_failures == 1, "allocation failure was not injected exactly once");
    require(result.error == wookrar::Error::no_memory && result.rar_code == ERAR_NO_MEMORY,
            "header allocation failure did not return the memory error code");
    require(names.empty() && count == 0, "failed header listing published partial names");

    // A fresh session must work after the error. This also checks that closing
    // the failed session released UnRAR's global mutex and the archive handle.
    auto recovered = wookrar::list_images(argv[1], names, &count);
    require(recovered.ok() && names == expected && count == static_cast<int>(names.size()),
            "listing did not recover after the header allocation failure");
    std::puts("{\"ok\":true,\"checks\":[\"header allocation returns no_memory\","
              "\"no partial names\",\"fresh listing after failure\"]}");
}
