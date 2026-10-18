// WOOK RU3 archive backend. SPDX-License-Identifier: GPL-3.0-or-later
#include "archive_backend.hpp"
#include "unarr.h"
#include <archive.h>
#include <archive_entry.h>
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <map>
#include <sys/stat.h>
#ifdef __SWITCH__
#include <switch.h>
#else
#include <mutex>
#endif

namespace wookarc {
namespace {
constexpr size_t MAX_PAGE = 128U * 1024U * 1024U;
constexpr size_t MAX_ENTRIES = 100000;
bool cancelled(volatile int* c) {
    return c && __atomic_load_n(c, __ATOMIC_ACQUIRE);
}
#ifdef __SWITCH__
Mutex rar_mutex{};
struct Guard { Guard() { mutexLock(&rar_mutex); } ~Guard() { mutexUnlock(&rar_mutex); } };
#else
std::mutex rar_mutex;
struct Guard { std::lock_guard<std::mutex> lock{rar_mutex}; };
#endif
struct UnarrHandle {
    ar_stream* stream = nullptr;
    ar_archive* reader = nullptr;
    ~UnarrHandle() { clear(); }
    void clear() {
        if (reader) ar_close_archive(reader);
        if (stream) ar_close(stream);
        reader = nullptr; stream = nullptr;
    }
    bool open(const char* path) {
        clear();
        stream = ar_open_file(path);
        if (stream) reader = ar_open_rar_archive(stream);
        return reader != nullptr;
    }
};
struct RarSession {
    UnarrHandle handle;
    std::string path;
    int64_t size = -1, modified = -1;
    std::map<std::string, off64_t> offsets;
    void clear() { handle.clear(); path.clear(); offsets.clear(); size = modified = -1; }
    bool open(const char* src, std::string& error, volatile int* cancel) {
        struct stat st{};
        if (stat(src, &st) != 0) { error = "Cannot stat archive"; return false; }
        if (path == src && handle.reader && size == (int64_t)st.st_size &&
            modified == (int64_t)st.st_mtime) return true;
        clear();
        if (!handle.open(src)) { error = "RAR4 open failed or archive is encrypted"; clear(); return false; }
        while (ar_parse_entry(handle.reader)) {
            if (cancelled(cancel)) { error = "Cancelled"; clear(); return false; }
            const char* n = ar_entry_get_name(handle.reader);
            if (!n) { error = "RAR4 entry name could not be decoded"; clear(); return false; }
            std::string key = normalize(n);
            if (!key.empty() && !offsets.emplace(key, ar_entry_get_offset(handle.reader)).second) {
                error = "Ambiguous duplicate archive entry: " + key;
                clear(); return false;
            }
            if (offsets.size() > MAX_ENTRIES) { error = "Archive entry safety limit exceeded"; clear(); return false; }
        }
        if (!ar_at_eof(handle.reader)) { error = "RAR4 header scan did not reach end of archive"; clear(); return false; }
        path = src; size = st.st_size; modified = st.st_mtime;
        return true;
    }
};
RarSession rar_session;

struct ArchiveHandle {
    archive* a = archive_read_new();
    ArchiveHandle() { if (a) { archive_read_support_filter_all(a); archive_read_support_format_all(a); } }
    ~ArchiveHandle() { if (a) archive_read_free(a); }
    bool open(const char* path, std::string& error) {
        if (!a) { error = "Cannot allocate archive reader"; return false; }
        if (archive_read_open_filename(a, path, 256 * 1024) < ARCHIVE_WARN) {
            error = reason("Archive open failed"); return false;
        }
        return true;
    }
    std::string reason(const char* where) {
        const char* e = archive_error_string(a);
        return std::string(where) + (e ? std::string(": ") + e : "");
    }
    int next(archive_entry** entry) {
        int rc;
        unsigned retries = 0;
        do { rc = archive_read_next_header(a, entry); } while (rc == ARCHIVE_RETRY && ++retries < 8);
        return rc;
    }
};

bool list_libarchive(const char* path, std::vector<std::string>& names,
                     std::string& error, volatile int* cancel) {
    ArchiveHandle ar;
    if (!ar.open(path, error)) return false;
    archive_entry* e = nullptr;
    for (;;) {
        if (cancelled(cancel)) { error = "Cancelled"; return false; }
        const int rc = ar.next(&e);
        if (rc == ARCHIVE_EOF) return true;
        if (rc < ARCHIVE_WARN || !e) { error = ar.reason("Header scan stopped"); return false; }
        const auto type = archive_entry_filetype(e);
        if (type == AE_IFREG || type == 0) {
            const char* name = archive_entry_pathname_utf8(e);
            if (!name) name = archive_entry_pathname(e);
            if (!name) { error = ar.reason("Entry name unavailable"); return false; }
            names.push_back(normalize(name));
            if (names.size() > MAX_ENTRIES) { error = "Archive entry safety limit exceeded"; return false; }
        }
        if (archive_read_data_skip(ar.a) < ARCHIVE_WARN) {
            error = ar.reason("Cannot advance to next archive entry"); return false;
        }
    }
}

bool extract_libarchive(const char* path, const std::string& target, void** data,
                        size_t* size, std::string& error, volatile int* cancel) {
    ArchiveHandle ar;
    if (!ar.open(path, error)) return false;
    archive_entry* e = nullptr;
    for (;;) {
        if (cancelled(cancel)) { error = "Cancelled"; return false; }
        const int rc = ar.next(&e);
        if (rc == ARCHIVE_EOF) { error = "Entry truly absent from complete archive: " + target; return false; }
        if (rc < ARCHIVE_WARN || !e) { error = ar.reason("Header scan stopped before target"); return false; }
        const char* name = archive_entry_pathname_utf8(e);
        if (!name) name = archive_entry_pathname(e);
        if (!name || normalize(name) != target || archive_entry_filetype(e) == AE_IFDIR) {
            if (archive_read_data_skip(ar.a) < ARCHIVE_WARN) {
                error = ar.reason("Cannot skip preceding entry"); return false;
            }
            continue;
        }
        if (archive_entry_is_encrypted(e) > 0) { error = "Password-protected comic is unsupported"; return false; }
        const la_int64_t expected = archive_entry_size_is_set(e) ? archive_entry_size(e) : -1;
        if (expected > (la_int64_t)MAX_PAGE) { error = "Page exceeds 128 MiB safety limit"; return false; }
        size_t capacity = expected > 0 ? (size_t)expected : 65536;
        auto* buf = (unsigned char*)malloc(capacity ? capacity : 1);
        if (!buf) { error = "Not enough memory for compressed page"; return false; }
        size_t done = 0;
        bool ok = false;
        for (;;) {
            if (cancelled(cancel)) { error = "Cancelled"; break; }
            unsigned char chunk[65536];
            const la_ssize_t n = archive_read_data(ar.a, chunk, sizeof(chunk));
            if (n < 0) { error = ar.reason("Page decompression failed"); break; }
            if (n == 0) {
                if (expected >= 0 && done != (size_t)expected) error = "Truncated page data";
                else ok = true;
                break;
            }
            if ((size_t)n > MAX_PAGE - done) { error = "Page exceeds 128 MiB safety limit"; break; }
            if (done + (size_t)n > capacity) {
                const size_t need = done + (size_t)n;
                const size_t next = std::min(MAX_PAGE, std::max(need, capacity * 2));
                void* bigger = realloc(buf, next);
                if (!bigger) { error = "Not enough memory while extracting page"; break; }
                buf = (unsigned char*)bigger; capacity = next;
            }
            memcpy(buf + done, chunk, (size_t)n); done += (size_t)n;
        }
        if (!ok) { free(buf); return false; }
        *data = buf; *size = done;
        return true;
    }
}

bool extract_rar4(const char* path, const std::string& target, void** data,
                  size_t* size, std::string& error, volatile int* cancel) {
    Guard guard;
    if (cancelled(cancel)) { error = "Cancelled"; return false; }
    if (!rar_session.open(path, error, cancel)) return false;
    auto it = rar_session.offsets.find(target);
    if (it == rar_session.offsets.end()) { error = "RAR4 entry absent: " + target; return false; }
    ar_archive* reader = rar_session.handle.reader;
    if (!ar_parse_entry_at(reader, it->second)) { error = "RAR4 seek to entry failed"; rar_session.clear(); return false; }
    size_t expected = ar_entry_get_size(reader);
    if (expected > MAX_PAGE) { error = "Page exceeds 128 MiB safety limit"; return false; }
    auto* buf = (unsigned char*)malloc(expected ? expected : 1);
    if (!buf) { error = "Not enough memory for RAR4 page"; return false; }
    size_t done = 0;
    while (done < expected) {
        if (cancelled(cancel)) { free(buf); error = "Cancelled"; return false; }
        size_t n = std::min((size_t)65536, expected - done);
        if (!ar_entry_uncompress(reader, buf + done, n)) {
            free(buf); error = "RAR4 decompression or checksum failed: " + target;
            rar_session.clear(); return false;
        }
        done += n;
    }
    // Empty entries still require the decoder's terminal checksum check.
    if (!expected && !ar_entry_uncompress(reader, buf, 0)) {
        free(buf); error = "RAR4 empty-entry checksum failed"; return false;
    }
    *data = buf; *size = done;
    return true;
}
}

std::string normalize(const std::string& name) {
    std::string n = name;
    std::replace(n.begin(), n.end(), '\\', '/');
    while (n.compare(0, 2, "./") == 0) n.erase(0, 2);
    return n;
}
bool is_rar4(const char* path) {
    FILE* f = fopen(path, "rb");
    if (!f) return false;
    unsigned char magic[7];
    const size_t n = fread(magic, 1, sizeof(magic), f); fclose(f);
    return n == 7 && memcmp(magic, "Rar!\x1a\x07\x00", 7) == 0;
}
bool list(const char* path, std::vector<std::string>& names, std::string& error, volatile int* cancel) {
    names.clear(); error.clear();
    if (is_rar4(path)) {
        UnarrHandle h;
        if (!h.open(path)) { error = "RAR4 open failed or archive is encrypted"; return false; }
        while (ar_parse_entry(h.reader)) {
            if (cancelled(cancel)) { names.clear(); error = "Cancelled"; return false; }
            const char* n = ar_entry_get_name(h.reader);
            if (!n) { names.clear(); error = "RAR4 filename conversion failed"; return false; }
            names.push_back(normalize(n));
            if (names.size() > MAX_ENTRIES) { names.clear(); error = "Archive entry safety limit exceeded"; return false; }
        }
        if (ar_at_eof(h.reader)) return true;
        // Normal/truncated legacy archives sometimes omit the final RAR marker.
        // Retry with libarchive, but never cache an incomplete scan as successful.
        names.clear();
    }
    bool ok = list_libarchive(path, names, error, cancel);
    if (!ok) names.clear();
    return ok;
}
bool extract(const char* path, const std::string& name, void** data,
             size_t* size, std::string& error, volatile int* cancel) {
    if (!data || !size) { error = "Invalid extraction output arguments"; return false; }
    *data = nullptr; *size = 0; error.clear();
    const std::string target = normalize(name);
    if (is_rar4(path)) {
        if (extract_rar4(path, target, data, size, error, cancel)) return true;
        if (cancelled(cancel)) return false;
        const std::string rar_error = error;
        if (extract_libarchive(path, target, data, size, error, cancel)) return true;
        error = rar_error + "; fallback: " + error;
        return false;
    }
    return extract_libarchive(path, target, data, size, error, cancel);
}
void close_rar_session() { Guard guard; rar_session.clear(); }
}
