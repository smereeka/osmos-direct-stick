#pragma once

#include <cstddef>
#include <string>
#include <vector>

// Shared by the page reader and the cover workers. No SDL/renderer dependency;
// the same implementation is exercised by the native regression executable.
namespace wookrar {

enum class Error {
    none, cancelled, not_found, corrupt, unsupported, size_limit, no_memory, io
};

struct Result {
    Error error = Error::none;
    int rar_code = 0;
    std::string detail;
    bool ok() const { return error == Error::none; }
};

struct Info { bool solid = false; };
using Allocator = void *(*)(std::size_t);
using Deallocator = void (*)(void *);

bool is_archive(const char *path);

// Header enumeration does not claim that compressed payloads have been tested.
// On an error, the partial list is discarded. Names are normalized UTF-8 paths.
Result list_images(const char *path, std::vector<std::string> &names,
                   volatile int *count = nullptr, Info *info = nullptr,
                   volatile int *cancel = nullptr);

// RAR_TEST produces bytes through a callback, with CRC/hash verification. Solid
// predecessors are decoded into a discard sink to reconstruct the dictionary.
// Nothing is written to archive-supplied filesystem paths. Output is published
// only on complete success and belongs to the caller's allocator/deallocator.
Result extract(const char *path, const std::string &name,
               void **data, std::size_t *size, volatile int *cancel,
               Allocator allocate, Deallocator deallocate);

std::string describe(const Result &result);

} // namespace wookrar
