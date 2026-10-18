#include "WookRAR.hpp"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cwchar>

#ifndef _UNIX
#define _UNIX
#endif
#include "wookrar/dll.hpp"

#ifdef __SWITCH__
#include <switch/kernel/mutex.h>
#include <switch/kernel/svc.h>
#else
#include <chrono>
#include <mutex>
#include <thread>
#endif

namespace wookrar {
namespace {

constexpr std::uint64_t MAX_IMAGE_BYTES = 64ULL * 1024 * 1024;
constexpr unsigned MAX_DICTIONARY_KIB = 64 * 1024;
constexpr std::size_t MAX_NAMES = 100000;
constexpr std::size_t NAME_CAPACITY = 4096;

bool cancelled(volatile int *flag) {
    return flag && __atomic_load_n(flag, __ATOMIC_ACQUIRE) != 0;
}

// UnRAR's ErrHandler is global even when each operation owns a different
// archive handle. Serialize the whole session, including open and close.
#ifdef __SWITCH__
Mutex rar_mutex = 0;
#else
std::mutex rar_mutex;
#endif

class Lock {
    bool held = false;
public:
    bool acquire(volatile int *cancel) {
        while (!cancelled(cancel)) {
#ifdef __SWITCH__
            held = mutexTryLock(&rar_mutex);
#else
            held = rar_mutex.try_lock();
#endif
            if (held) return true;
#ifdef __SWITCH__
            svcSleepThread(1000000LL);
#else
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
#endif
        }
        return false;
    }
    ~Lock() {
        if (!held) return;
#ifdef __SWITCH__
        mutexUnlock(&rar_mutex);
#else
        rar_mutex.unlock();
#endif
    }
};

Result failure(Error error, int code, const std::string &detail) {
    return {error, code, detail};
}

Result rar_failure(int code, const std::string &where) {
    Error error = Error::corrupt;
    if (code == ERAR_NO_MEMORY) error = Error::no_memory;
    else if (code == ERAR_EOPEN || code == ERAR_EREAD || code == ERAR_ECLOSE)
        error = Error::io;
    else if (code == ERAR_UNKNOWN_FORMAT || code == ERAR_MISSING_PASSWORD ||
             code == ERAR_BAD_PASSWORD || code == ERAR_EREFERENCE)
        error = Error::unsupported;
    else if (code == ERAR_LARGE_DICT) error = Error::size_limit;
    return failure(error, code, where);
}

bool add_utf8(std::string &out, std::uint32_t cp) {
    if (cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff)) return false;
    if (cp < 0x80) out.push_back(static_cast<char>(cp));
    else if (cp < 0x800) {
        out.push_back(static_cast<char>(0xc0 | (cp >> 6)));
        out.push_back(static_cast<char>(0x80 | (cp & 63)));
    } else if (cp < 0x10000) {
        out.push_back(static_cast<char>(0xe0 | (cp >> 12)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 63)));
        out.push_back(static_cast<char>(0x80 | (cp & 63)));
    } else {
        out.push_back(static_cast<char>(0xf0 | (cp >> 18)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 63)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 63)));
        out.push_back(static_cast<char>(0x80 | (cp & 63)));
    }
    return true;
}

bool from_wide(const wchar_t *name, std::string &out) {
    out.clear();
    std::size_t i = 0;
    for (; i < NAME_CAPACITY - 1 && name[i]; ++i) {
        std::uint32_t cp = static_cast<std::uint32_t>(name[i]);
        if (sizeof(wchar_t) == 2 && cp >= 0xd800 && cp <= 0xdbff) {
            if (i + 1 >= NAME_CAPACITY - 1) return false;
            std::uint32_t low = static_cast<std::uint32_t>(name[++i]);
            if (low < 0xdc00 || low > 0xdfff) return false;
            cp = 0x10000 + ((cp - 0xd800) << 10) + (low - 0xdc00);
        }
        if (cp == '\\') cp = '/';
        if (!add_utf8(out, cp)) return false;
    }
    // FileNameEx is truncated silently by UnRAR. Reject a filled buffer instead
    // of aliasing it to a different member with the same truncated prefix.
    return i < NAME_CAPACITY - 1;
}

bool to_wide(const char *text, std::wstring &out) {
    out.clear();
    const unsigned char *p = reinterpret_cast<const unsigned char *>(text);
    while (*p) {
        std::uint32_t cp = *p++;
        unsigned extra = 0;
        std::uint32_t minimum = 0;
        if (cp < 0x80) {}
        else if ((cp & 0xe0) == 0xc0) { cp &= 0x1f; extra = 1; minimum = 0x80; }
        else if ((cp & 0xf0) == 0xe0) { cp &= 0x0f; extra = 2; minimum = 0x800; }
        else if ((cp & 0xf8) == 0xf0) { cp &= 7; extra = 3; minimum = 0x10000; }
        else return false;
        for (unsigned i = 0; i < extra; ++i) {
            if ((*p & 0xc0) != 0x80) return false;
            cp = (cp << 6) | (*p++ & 63);
        }
        if (cp < minimum || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff))
            return false;
        if (sizeof(wchar_t) == 2 && cp >= 0x10000) {
            cp -= 0x10000;
            out.push_back(static_cast<wchar_t>(0xd800 + (cp >> 10)));
            out.push_back(static_cast<wchar_t>(0xdc00 + (cp & 1023)));
        } else out.push_back(static_cast<wchar_t>(cp));
    }
    return true;
}

std::string normalize(std::string name) {
    std::replace(name.begin(), name.end(), '\\', '/');
    return name;
}

bool image_name(const std::string &name) {
    const auto dot = name.find_last_of('.');
    if (dot == std::string::npos) return false;
    std::string ext = name.substr(dot);
    for (char &c : ext) if (c >= 'A' && c <= 'Z') c += 'a' - 'A';
    return ext == ".jpg" || ext == ".jpeg" || ext == ".png" ||
           ext == ".webp" || ext == ".gif" || ext == ".tiff";
}

struct CallbackState {
    volatile int *cancel = nullptr;
    unsigned char *data = nullptr;
    std::size_t expected = 0;
    std::size_t received = 0;
    bool collect = false;
    bool overflow = false;
    bool interaction = false;
};

int CALLBACK callback(UINT msg, LPARAM user, LPARAM p1, LPARAM p2) {
    auto &state = *reinterpret_cast<CallbackState *>(user);
    if (cancelled(state.cancel)) return -1;
    if (msg == UCM_PROCESSDATA) {
        if (p2 < 0) { state.overflow = true; return -1; }
        if (!state.collect) return 1;
        const auto bytes = static_cast<std::size_t>(p2);
        if (bytes > state.expected - state.received || (bytes && !p1)) {
            state.overflow = true;
            return -1;
        }
        if (bytes) std::memcpy(state.data + state.received,
                              reinterpret_cast<const void *>(p1), bytes);
        state.received += bytes;
        return 1;
    }
    // No password dialogs, volume lookup or large-dictionary confirmation from
    // a background worker. These archives receive an explicit unsupported error.
    state.interaction = true;
    return -1;
}

struct Session {
    HANDLE handle = nullptr;
    unsigned flags = 0;
    ~Session() { if (handle) RARCloseArchive(handle); }
    Result open(const char *path, unsigned mode, CallbackState &state) {
        std::wstring wide_path;
        if (!to_wide(path, wide_path))
            return failure(Error::unsupported, 0, "archive path is not UTF-8");
        RAROpenArchiveDataEx options{};
        options.ArcNameW = &wide_path[0];
        options.OpenMode = mode;
        options.Callback = callback;
        options.UserData = reinterpret_cast<LPARAM>(&state);
        handle = RAROpenArchiveEx(&options);
        flags = options.Flags;
        if (!handle || options.OpenResult != ERAR_SUCCESS)
            return rar_failure(options.OpenResult, "open archive");
        if (flags & (ROADF_VOLUME | ROADF_ENCHEADERS))
            return failure(Error::unsupported, 0, "multipart or encrypted-header RAR");
        return {};
    }
};

struct Header {
    RARHeaderDataEx data{};
    wchar_t wide_name[NAME_CAPACITY]{};
    int read(HANDLE archive) {
        data = {};
        wide_name[0] = 0;
        data.FileNameEx = wide_name;
        data.FileNameExSize = NAME_CAPACITY;
        return RARReadHeaderEx(archive, &data);
    }
};

Result process(Session &session, CallbackState &state, int operation,
               const std::string &name) {
    const int code = RARProcessFileW(session.handle, operation, nullptr, nullptr);
    if (cancelled(state.cancel)) return failure(Error::cancelled, code, "cancelled");
    if (state.overflow) return failure(Error::corrupt, code, "payload size exceeds header: " + name);
    if (state.interaction)
        return failure(Error::unsupported, code, "password, volume or dictionary request: " + name);
    if (code != ERAR_SUCCESS) return rar_failure(code, "process member: " + name);
    return {};
}

} // namespace

bool is_archive(const char *path) {
    unsigned char magic[8]{};
    FILE *file = std::fopen(path, "rb");
    if (!file) return false;
    const auto bytes = std::fread(magic, 1, sizeof(magic), file);
    std::fclose(file);
    return (bytes >= 7 && std::memcmp(magic, "Rar!\x1a\x07\x00", 7) == 0) ||
           (bytes == 8 && std::memcmp(magic, "Rar!\x1a\x07\x01\x00", 8) == 0);
}

Result list_images(const char *path, std::vector<std::string> &names,
                   volatile int *count, Info *info, volatile int *cancel) {
    names.clear();
    if (count) __atomic_store_n(count, 0, __ATOMIC_RELEASE);
    if (info) info->solid = false;
    Lock lock;
    if (!lock.acquire(cancel)) return failure(Error::cancelled, 0, "cancelled");
    CallbackState state;
    state.cancel = cancel;
    Session session;
    Result result = session.open(path, RAR_OM_LIST, state);
    if (!result.ok()) return result;
    if (info) info->solid = (session.flags & ROADF_SOLID) != 0;

    std::vector<std::string> pending;
    Header header;
    while (true) {
        if (cancelled(cancel)) { result = failure(Error::cancelled, 0, "cancelled"); break; }
        const int code = header.read(session.handle);
        if (code == ERAR_END_ARCHIVE) { names.swap(pending); return {}; }
        if (code != ERAR_SUCCESS) { result = rar_failure(code, "read member header"); break; }
        std::string name;
        if (!from_wide(header.wide_name, name)) {
            result = failure(Error::unsupported, 0, "invalid or excessively long member name"); break;
        }
        if (!(header.data.Flags & RHDF_DIRECTORY) && !header.data.RedirType && image_name(name)) {
            if (pending.size() >= MAX_NAMES) {
                result = failure(Error::size_limit, 0, "too many image entries"); break;
            }
            pending.push_back(name);
            if (count) __atomic_store_n(count, static_cast<int>(pending.size()), __ATOMIC_RELEASE);
        }
        result = process(session, state, RAR_SKIP, name);
        if (!result.ok()) break;
    }
    if (count) __atomic_store_n(count, 0, __ATOMIC_RELEASE);
    return result;
}

Result extract(const char *path, const std::string &target_name,
               void **out_data, std::size_t *out_size, volatile int *cancel,
               Allocator allocate, Deallocator deallocate) {
    *out_data = nullptr;
    *out_size = 0;
    Lock lock;
    if (!lock.acquire(cancel)) return failure(Error::cancelled, 0, "cancelled");
    CallbackState state;
    state.cancel = cancel;
    Session session;
    Result result = session.open(path, RAR_OM_EXTRACT, state);
    if (!result.ok()) return result;
    const bool solid = (session.flags & ROADF_SOLID) != 0;
    const std::string target = normalize(target_name);
    Header header;
    while (true) {
        if (cancelled(cancel)) return failure(Error::cancelled, 0, "cancelled");
        const int code = header.read(session.handle);
        if (code == ERAR_END_ARCHIVE) return failure(Error::not_found, code, target);
        if (code != ERAR_SUCCESS) return rar_failure(code, "read member header before " + target);
        std::string name;
        if (!from_wide(header.wide_name, name))
            return failure(Error::unsupported, 0, "invalid or excessively long member name");
        const bool directory = (header.data.Flags & RHDF_DIRECTORY) != 0;
        const bool wanted = !directory && name == target;
        const bool decode = wanted || (!directory && (solid || (header.data.Flags & RHDF_SOLID)));
        if (decode) {
            if ((header.data.Flags & (RHDF_ENCRYPTED | RHDF_SPLITBEFORE | RHDF_SPLITAFTER)) ||
                header.data.RedirType)
                return failure(Error::unsupported, 0, "encrypted, split or redirected member: " + name);
            if (header.data.DictSize > MAX_DICTIONARY_KIB)
                return failure(Error::size_limit, 0, "RAR dictionary exceeds 64 MiB: " + name);
        }
        if (wanted) {
            const std::uint64_t size = header.data.UnpSize |
                (static_cast<std::uint64_t>(header.data.UnpSizeHigh) << 32);
            if (!size || size > MAX_IMAGE_BYTES)
                return failure(Error::size_limit, 0, "image is empty or exceeds 64 MiB: " + name);
            state.expected = static_cast<std::size_t>(size);
            state.data = static_cast<unsigned char *>(allocate(state.expected));
            if (!state.data) return failure(Error::no_memory, 0, "allocate image: " + name);
            state.collect = true;
        }
        // Unlike RAR_SKIP, RAR_TEST invokes PROCESSDATA for solid predecessors,
        // making cancellation responsive and verifying their CRC/hash too.
        result = process(session, state, decode ? RAR_TEST : RAR_SKIP, name);
        if (!result.ok()) {
            if (state.data) deallocate(state.data);
            return result;
        }
        if (wanted) {
            if (state.received != state.expected) {
                deallocate(state.data);
                return failure(Error::corrupt, 0, "incomplete image payload: " + name);
            }
            *out_data = state.data;
            *out_size = state.received;
            return {};
        }
    }
}

std::string describe(const Result &result) {
    static const char *names[] = {"ok", "cancelled", "entry not found", "corrupt archive/data",
        "unsupported archive", "size limit", "out of memory", "I/O error"};
    return std::string(names[static_cast<unsigned>(result.error)]) +
        " (UnRAR code=" + std::to_string(result.rar_code) + "): " + result.detail;
}

} // namespace wookrar
