// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "miniz.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
namespace wookzip {
inline bool matches(const char* path) {
    FILE* f=fopen(path,"rb"); if(!f) return false;
    unsigned char m[4]; size_t n=fread(m,1,4,f); fclose(f);
    return n==4 && m[0]=='P' && m[1]=='K' &&
        ((m[2]==3 && m[3]==4) || (m[2]==5 && m[3]==6) || (m[2]==7 && m[3]==8));
}
struct Reader {
    mz_zip_archive zip{};
    bool ready=false;
    explicit Reader(const char* path) { ready=mz_zip_reader_init_file(&zip,path,0)!=0; }
    ~Reader() { if(ready) mz_zip_reader_end(&zip); }
    std::string error() { const char* s=mz_zip_get_error_string(mz_zip_get_last_error(&zip)); return s?s:"ZIP error"; }
    std::string name(mz_uint i) {
        mz_uint n=mz_zip_reader_get_filename(&zip,i,nullptr,0);
        if(!n || n>65536) return {};
        std::vector<char> buf(n);
        if(!mz_zip_reader_get_filename(&zip,i,buf.data(),n)) return {};
        return wookarc::normalize(buf.data());
    }
};
inline bool list(const char* path,std::vector<std::string>& names,std::string& error,volatile int* cancel) {
    Reader ar(path);
    if(!ar.ready) { error=ar.error(); return false; }
    mz_uint count=mz_zip_reader_get_num_files(&ar.zip);
    if(count>100000) { error="ZIP entry safety limit exceeded"; return false; }
    for(mz_uint i=0;i<count;++i) {
        if(cancel && __atomic_load_n(cancel,__ATOMIC_ACQUIRE)) { names.clear(); error="Cancelled"; return false; }
        if(mz_zip_reader_is_file_a_directory(&ar.zip,i)) continue;
        std::string name=ar.name(i);
        if(name.empty()) { names.clear(); error="ZIP filename unavailable"; return false; }
        names.push_back(name);
    }
    return true;
}
inline bool extract(const char* path,const std::string& target,void** data,size_t* size,std::string& error,volatile int* cancel) {
    Reader ar(path);
    if(!ar.ready) { error=ar.error(); return false; }
    const mz_uint count=mz_zip_reader_get_num_files(&ar.zip);
    for(mz_uint i=0;i<count;++i) {
        if(cancel && __atomic_load_n(cancel,__ATOMIC_ACQUIRE)) { error="Cancelled"; return false; }
        if(mz_zip_reader_is_file_a_directory(&ar.zip,i) || ar.name(i)!=target) continue;
        mz_zip_archive_file_stat st{};
        if(!mz_zip_reader_file_stat(&ar.zip,i,&st)) { error=ar.error(); return false; }
        if(st.m_uncomp_size>128ULL*1024*1024) { error="ZIP page exceeds 128 MiB safety limit"; return false; }
        if(st.m_bit_flag&1) { error="Password-protected ZIP is unsupported"; return false; }
        size_t n=(size_t)st.m_uncomp_size;
        void* buf=malloc(n?n:1);
        if(!buf) { error="Not enough memory for ZIP page"; return false; }
        if(!mz_zip_reader_extract_to_mem(&ar.zip,i,buf,n,0)) {
            free(buf); error=ar.error(); return false;
        }
        if(cancel && __atomic_load_n(cancel,__ATOMIC_ACQUIRE)) {
            free(buf); error="Cancelled"; return false;
        }
        *data=buf; *size=n; return true;
    }
    error="ZIP entry absent: "+target; return false;
}
}
