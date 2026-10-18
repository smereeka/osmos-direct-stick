#!/usr/bin/env python3
from pathlib import Path
import shutil, sys
root=Path(sys.argv[1]).resolve()
scripts=Path(__file__).resolve().parent
p=root/'source/menus/book/CBZPageLayout.cpp'
c=p.read_text(encoding='utf-8')

def once(s,a,b,label):
    assert s.count(a)==1, (label,s.count(a))
    return s.replace(a,b,1)

shutil.copyfile(scripts/'rar_backend.cpp',root/'source/menus/book/rar_backend.cpp')
shutil.copyfile(scripts/'rar_backend.hpp',root/'include/rar_backend.hpp')
c=once(c,'#include "CBZPageLayout.hpp"','#include "CBZPageLayout.hpp"\n#include "rar_backend.hpp"','backend include')
# Do not read an old page list produced by an incomplete decoder traversal.
c=c.replace('#WR2 ', '#WR3 ')
chooser=root/'source/menus/book-chooser/MenuChooser.cpp'
chooser.write_text(chooser.read_text(encoding='utf-8').replace('#WR2 ', '#WR3 '),encoding='utf-8')

start=c.index('static bool enumerate_images(')
end=c.index('// Wrapper that checks/populates g_raw_cache',start)
archive_functions=r'''
static std::string archive_path_normalize(const char* p) {
    std::string s=p?p:"";
    std::replace(s.begin(),s.end(),'\\','/');
    while (s.compare(0,2,"./")==0) s.erase(0,2);
    return s;
}
static void archive_diagnostic(struct archive* a,const char* phase,const char* path) {
    const char* e=archive_error_string(a);
    Log_Error(std::string("archive ")+phase+": "+(e?e:"unknown error")+" | "+path);
}
static bool archive_header(struct archive* a,struct archive_entry** e,bool& failed,const char* path) {
    for(int retries=0;retries<8;retries++) {
        *e=nullptr;
        int rc=archive_read_next_header(a,e);
        if(rc==ARCHIVE_EOF) return false;
        if(rc==ARCHIVE_OK || (rc==ARCHIVE_WARN && *e)) {
            if(rc==ARCHIVE_WARN) archive_diagnostic(a,"header warning",path);
            return true;
        }
        if(rc==ARCHIVE_RETRY) continue;
        archive_diagnostic(a,"header failure",path); failed=true; return false;
    }
    Log_Error(std::string("archive: header retry limit | ")+path); failed=true; return false;
}
static bool archive_image_bytes(struct archive* a,struct archive_entry* e,
                                void** output,size_t* length,volatile int* cancel,const char* path) {
    *output=nullptr; *length=0;
    const size_t cap=128u*1024u*1024u;
    la_int64_t declared=archive_entry_size(e);
    if(declared>(la_int64_t)cap) { Log_Error("archive: image exceeds memory limit"); return false; }
    std::vector<uint8_t> bytes;
    if(declared>0) bytes.reserve((size_t)declared);
    uint8_t chunk[65536];
    for(;;) {
        if(cancel && __atomic_load_n(cancel,__ATOMIC_ACQUIRE)) return false;
        la_ssize_t n=archive_read_data(a,chunk,sizeof(chunk));
        if(n<0) { archive_diagnostic(a,"image decode failure",path); return false; }
        if(n==0) break;
        if(bytes.size()+(size_t)n>cap) { Log_Error("archive: image exceeds memory limit"); return false; }
        bytes.insert(bytes.end(),chunk,chunk+n);
    }
    if(bytes.empty() || (declared>0 && bytes.size()!=(size_t)declared)) {
        Log_Error(std::string("archive: incomplete image | ")+path); return false;
    }
    *output=SDL_malloc(bytes.size());
    if(!*output) { Log_Error("archive: image allocation failed"); return false; }
    memcpy(*output,bytes.data(),bytes.size()); *length=bytes.size();
    return true;
}
static bool enumerate_images(const char *path,std::vector<std::string>& out_names,
                             std::string* first_name=nullptr,void** first_data=nullptr,
                             size_t* first_size=nullptr,volatile int* out_count=nullptr) {
    out_names.clear();
    if(first_data) *first_data=nullptr;
    if(first_size) *first_size=0;
    if(wook_rar3(path)) {
        std::vector<std::string> all; std::string error;
        if(!wook_rar_list(path,all,error)) { Log_Error(error+" | "+path); return false; }
        for(const auto& n:all) if(is_image_ext(n.c_str())) out_names.push_back(n);
        if(out_count) __atomic_store_n(out_count,(int)out_names.size(),__ATOMIC_RELEASE);
        if(first_name && first_data && first_size && !out_names.empty()) {
            if(wook_rar_extract(path,out_names[0],first_data,first_size,nullptr,error)) *first_name=out_names[0];
            else Log_Error(error+" | "+path);
        }
        return !out_names.empty();
    }
    struct archive* a=archive_read_new();
    if(!a) return false;
    archive_read_support_filter_all(a); archive_read_support_format_all(a);
    if(archive_read_open_filename(a,path,1048576)<ARCHIVE_WARN) {
        archive_diagnostic(a,"open",path); archive_read_free(a); return false;
    }
    struct archive_entry* e=nullptr; bool failed=false, first=false;
    while(archive_header(a,&e,failed,path)) {
        if(archive_entry_filetype(e)!=AE_IFDIR) {
            std::string n=archive_path_normalize(archive_entry_pathname(e));
            if(!n.empty() && is_image_ext(n.c_str())) {
                out_names.push_back(n);
                if(first_name && first_data && first_size && !first) {
                    if(archive_image_bytes(a,e,first_data,first_size,nullptr,path)) { *first_name=n; first=true; }
                    else { failed=true; break; }
                }
            }
        }
        int skip=archive_read_data_skip(a);
        if(skip<ARCHIVE_WARN) { archive_diagnostic(a,"skip",path); failed=true; break; }
    }
    archive_read_free(a);
    if(failed) {
        out_names.clear();
        if(first_data && *first_data) { SDL_free(*first_data); *first_data=nullptr; }
        if(first_size) *first_size=0;
        if(out_count) __atomic_store_n(out_count,0,__ATOMIC_RELEASE);
        return false;
    }
    if(out_count) __atomic_store_n(out_count,(int)out_names.size(),__ATOMIC_RELEASE);
    return !out_names.empty();
}
static bool extract_image(const char* path,const std::string& target_name,
                          void** out_data,size_t* out_size,volatile int* cancel_flag=nullptr) {
    *out_data=nullptr; *out_size=0;
    if(wook_rar3(path)) {
        std::string error;
        bool ok=wook_rar_extract(path,target_name,out_data,out_size,cancel_flag,error);
        if(!ok && !error.empty()) Log_Error(error+" | "+path);
        return ok;
    }
    struct archive* a=archive_read_new();
    if(!a) return false;
    archive_read_support_filter_all(a); archive_read_support_format_all(a);
    if(archive_read_open_filename(a,path,1048576)<ARCHIVE_WARN) {
        archive_diagnostic(a,"open",path); archive_read_free(a); return false;
    }
    bool failed=false,found=false,ok=false;
    struct archive_entry* e=nullptr;
    while(archive_header(a,&e,failed,path)) {
        if(cancel_flag && __atomic_load_n(cancel_flag,__ATOMIC_ACQUIRE)) { archive_read_free(a); return false; }
        if(archive_entry_filetype(e)!=AE_IFDIR &&
           archive_path_normalize(archive_entry_pathname(e))==archive_path_normalize(target_name.c_str())) {
            found=true; ok=archive_image_bytes(a,e,out_data,out_size,cancel_flag,path); break;
        }
        if(archive_read_data_skip(a)<ARCHIVE_WARN) { archive_diagnostic(a,"skip",path); failed=true; break; }
    }
    if(!found && !failed) Log_Error("archive: entry genuinely absent: "+target_name+" | "+path);
    archive_read_free(a);
    return ok;
}

'''
c=c[:start]+archive_functions+c[end:]
# Complete a RAR index using the same decoder that later reads its pages.
marker='void CBZPageLayout::do_enumerate()\n{'
insert=r'''
    if(wook_rar3(_archive_path.c_str())) {
        Log_Write("RAR3/4: using unarr solid-capable decoder | "+_archive_path);
        void* data=nullptr; size_t size=0;
        bool ok=enumerate_images(_archive_path.c_str(),_page_names,
                                 &_first_image_name,&data,&size,nullptr);
        if(ok) {
            std::sort(_page_names.begin(),_page_names.end());
            __atomic_store_n(&_enum_count,(int)_page_names.size(),__ATOMIC_RELEASE);
            if(data) {
                _first_image_raw=data; _first_image_size=size;
                __atomic_store_n(&_first_image_ready,1,__ATOMIC_RELEASE);
            }
            save_page_cache(_archive_path.c_str(),_page_names);
            Log_Write("RAR3/4: complete index pages="+std::to_string(_page_names.size()));
        } else {
            _page_names.clear();
            if(data) SDL_free(data);
            Log_Error("RAR3/4: index/decode failed | "+_archive_path);
        }
        __atomic_store_n(&_enum_done,1,__ATOMIC_RELEASE);
        return;
    }
'''
c=once(c,marker,marker+insert,'RAR route')
# One forward prefetch for RAR3 avoids concurrently replaying the solid stream.
c=once(c,'    int pages[N_PF] = {fwd_page, bwd_page, fwd2_page};',
       '    if (wook_rar3(_archive_path.c_str())) { bwd_page=-1; fwd2_page=-1; }\n    int pages[N_PF] = {fwd_page, bwd_page, fwd2_page};','prefetch')
# Release decompressor handle when closing a book (after workers stop).
c=once(c,'    cancel_prefetch();\n    free_ready_textures();\n    FreeTextureIfNeeded(&_tex);',
       '    cancel_prefetch();\n    wook_rar_close();\n    free_ready_textures();\n    FreeTextureIfNeeded(&_tex);','close rar')
c=once(c,'void CBZPageLayout::draw_page()\n{\n    if (!_tex)\n        return;',
       'void CBZPageLayout::draw_page()\n{\n    if (!_tex) {\n        SDL_DrawText(RENDERER,ROBOTO_30,90,310,configDarkMode?WHITE:BLACK,"Не удалось прочитать страницу.");\n        SDL_DrawText(RENDERER,ROBOTO_20,90,360,configDarkMode?WHITE:BLACK,"Подробности записаны в /switch/WookReader/log.txt");\n        return;\n    }','error screen')
p.write_text(c,encoding='utf-8')
(root/'archive_functions_for_test.inc').write_text(archive_functions,encoding='utf-8')
# Common image extensions absent in old code.
c=c.replace('strcmp(ext, ".gif")  == 0 || strcmp(ext, ".tiff") == 0;',
            'strcmp(ext, ".gif") == 0 || strcmp(ext, ".tiff") == 0 || strcmp(ext, ".tif") == 0 || strcmp(ext, ".bmp") == 0;')
p.write_text(c,encoding='utf-8')
m=root/'Makefile'; s=m.read_text(encoding='utf-8')
s=s.replace('LIBS_REAL = stdc++fs','LIBS_REAL = unarr stdc++fs')
s=s.replace('APP_TITLE   := WookReader','APP_TITLE   := WOOK')
s=s.replace('APP_VERSION := ${VERSION_MAJOR}.${VERSION_MINOR}.${VERSION_MICRO}-sigma','APP_VERSION := 0.7.0-RU3')
m.write_text(s,encoding='utf-8')
# Diagnostic version and serialized log writing across decode workers.
m=root/'source/main.cpp'; s=m.read_text(encoding='utf-8')
s=s.replace('std::ofstream logFile;','std::ofstream logFile;\nstatic Mutex wookLogMutex{};\nstruct WookLogLock { WookLogLock(){ mutexLock(&wookLogMutex); } ~WookLogLock(){ mutexUnlock(&wookLogMutex); } };')
for name in ['Log_Write(const std::string& msg)','Log_Error(const std::string& msg)']:
    s=s.replace('void '+name+' {','void '+name+' {\n  WookLogLock lock;')
s=s.replace('=== WookReader Log Started ===','=== WOOK 0.7.0-RU3 RAR Decoder Log Started ===')
m.write_text(s,encoding='utf-8')
print('RU3: RAR3/4 decoder replaced, complete index, real errors, stream serialization, fixed old caches')
