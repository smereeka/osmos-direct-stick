#include "rar_backend.hpp"
#include "unarr_prefix.h"
#include "unarr.h"
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

namespace {
#ifdef __SWITCH__
Mutex guard{};
struct Lock { Lock(){ mutexLock(&guard); } ~Lock(){ mutexUnlock(&guard); } };
#else
std::mutex guard;
struct Lock { std::lock_guard<std::mutex> lock; Lock(): lock(guard){} };
#endif
constexpr size_t MAX_ENTRY = 128u * 1024u * 1024u;
constexpr size_t CHUNK = 65536;
bool cancelled(volatile int* p) { return p && __atomic_load_n(p, __ATOMIC_ACQUIRE); }
std::string normalize(const char* raw) {
    std::string s = raw ? raw : "";
    std::replace(s.begin(), s.end(), '\\', '/');
    while (s.compare(0,2,"./")==0) s.erase(0,2);
    return s;
}
struct Record { ar_off64_t offset; size_t size; };
struct Session {
    ar_stream* stream = nullptr;
    ar_archive* archive = nullptr;
    std::string path;
    int64_t length = -1, mtime = -1;
    std::map<std::string,Record> index;
    std::vector<std::string> order;
    ~Session(){ close(); }
    void close() {
        if (archive) ar_close_archive(archive);
        if (stream) ar_close(stream);
        archive=nullptr; stream=nullptr; path.clear(); index.clear(); order.clear();
        length=-1; mtime=-1;
    }
    bool open(const char* input, std::string& error) {
        struct stat st{};
        if (!input || stat(input,&st)!=0) { error="RAR: cannot stat input"; return false; }
        if (archive && path==input && length==st.st_size && mtime==st.st_mtime) return true;
        close();
        stream=ar_open_file(input);
        if (!stream) { error="RAR: cannot open input"; return false; }
        archive=ar_open_rar_archive(stream);
        if (!archive) { error="RAR: unsupported header"; close(); return false; }
        while (ar_parse_entry(archive)) {
            const char* raw=ar_entry_get_name(archive);
            std::string n=normalize(raw);
            if (n.empty()) { error="RAR: invalid entry name"; close(); return false; }
            if (index.find(n)!=index.end()) { error="RAR: duplicate normalized entry name"; close(); return false; }
            index.emplace(n,Record{ar_entry_get_offset(archive), ar_entry_get_size(archive)});
            order.push_back(n);
            if (order.size()>100000) { error="RAR: excessive entry count"; close(); return false; }
        }
        if (!ar_at_eof(archive) || order.empty()) {
            error="RAR: incomplete index or unsupported/encrypted archive";
            close(); return false;
        }
        path=input; length=st.st_size; mtime=st.st_mtime;
        return true;
    }
};
Session session;
}

bool wook_rar3(const char* path) {
    unsigned char h[7]{};
    FILE* f=path?fopen(path,"rb"):nullptr;
    if (!f) return false;
    size_t n=fread(h,1,7,f); fclose(f);
    return n==7 && memcmp(h,"Rar!\x1a\x07\x00",7)==0;
}

bool wook_rar_list(const char* path, std::vector<std::string>& names, std::string& error) {
    Lock lock; error.clear(); names.clear();
    if (!session.open(path,error)) return false;
    names=session.order;
    return true;
}

bool wook_rar_extract(const char* path, const std::string& name,
                      void** data, size_t* size, volatile int* cancel,
                      std::string& error) {
    if (!data || !size) return false;
    *data=nullptr; *size=0; error.clear();
    if (cancelled(cancel)) return false;
    Lock lock;
    if (cancelled(cancel)) return false;
    if (!session.open(path,error)) return false;
    auto found=session.index.find(normalize(name.c_str()));
    if (found==session.index.end()) { error="RAR: entry missing from validated index: "+name; return false; }
    const Record r=found->second;
    if (r.size==0 || r.size>MAX_ENTRY) { error="RAR: empty or oversized page"; return false; }
    if (!ar_parse_entry_at(session.archive,r.offset)) {
        error="RAR: cannot seek to indexed entry"; session.close(); return false;
    }
    void* buffer=malloc(r.size);
    if (!buffer) { error="RAR: insufficient memory for compressed image"; return false; }
    size_t done=0;
    while (done<r.size) {
        if (cancelled(cancel)) {
            free(buffer); session.close(); return false;
        }
        size_t take=std::min(CHUNK,r.size-done);
        if (!ar_entry_uncompress(session.archive,static_cast<unsigned char*>(buffer)+done,take)) {
            error="RAR: decode/checksum failure: "+name;
            free(buffer); session.close(); return false;
        }
        done+=take;
    }
    *data=buffer; *size=done;
    return true;
}

void wook_rar_close() { Lock lock; session.close(); }
