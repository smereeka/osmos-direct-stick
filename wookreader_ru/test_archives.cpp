#include "archive_backend.hpp"
#include <filesystem>
#include <algorithm>
#include <iostream>
#include <vector>
#include <thread>
#include <atomic>
#include <cstdlib>
#include <zlib.h>
namespace fs=std::filesystem;
static unsigned long crc(const void* data, size_t n) { return crc32(0,(const Bytef*)data,n); }
static int fd_count() {
    int n=0;
    for (const auto& x: fs::directory_iterator("/proc/self/fd")) { (void)x; ++n; }
    return n;
}
int main(int argc,char** argv) {
    if(argc!=2) return 90;
    const int fd_before=fd_count();
    std::vector<fs::path> cases;
    for(const auto& x:fs::directory_iterator(argv[1])) if(x.is_regular_file()) cases.push_back(x.path());
    std::sort(cases.begin(),cases.end());
    size_t reads=0, archives=0;
    for(const auto& path:cases) {
        std::vector<std::string> names;
        std::string error;
        if(!wookarc::list(path.c_str(),names,error) || names.empty()) {
            std::cerr<<"LIST FAILED "<<path<<" : "<<error<<"\n"; return 1;
        }
        std::vector<unsigned long> reference;
        std::vector<size_t> sizes;
        for(const auto& name:names) {
            void* data=nullptr; size_t size=0;
            if(!wookarc::extract(path.c_str(),name,&data,&size,error)) {
                std::cerr<<"READ FAILED "<<path<<" : "<<name<<" : "<<error<<"\n"; return 2;
            }
            reference.push_back(crc(data,size)); sizes.push_back(size); free(data); ++reads;
            std::cout<<path.filename().string()<<'\t'<<name<<'\t'<<size<<'\t'<<reference.back()<<'\n';
        }
        // Check backward seeks including the very last and second entries.
        for(size_t i=names.size();i-->0;) {
            void* data=nullptr; size_t size=0;
            if(!wookarc::extract(path.c_str(),names[i],&data,&size,error)) {
                std::cerr<<"REVERSE FAILED "<<error<<"\n"; return 3;
            }
            bool same=size==sizes[i] && crc(data,size)==reference[i]; free(data);
            if(!same) { std::cerr<<"CRC MISMATCH after reverse read\n"; return 4; }
            ++reads;
        }
        if(names.size()>1) {
            std::atomic<bool> ok{true};
            auto read_one=[&](size_t i){
                void* data=nullptr; size_t size=0; std::string err;
                bool success=wookarc::extract(path.c_str(),names[i],&data,&size,err);
                if(!success || size!=sizes[i] || crc(data,size)!=reference[i]) ok=false;
                free(data);
            };
            std::thread a(read_one,0),b(read_one,names.size()-1);
            a.join(); b.join();
            if(!ok) { std::cerr<<"CONCURRENT READ FAILED\n"; return 5; }
            reads+=2;
        }
        // Cancellation must not return partial bytes as a valid page.
        volatile int cancel=1;
        void* data=nullptr; size_t size=0;
        if(wookarc::extract(path.c_str(),names[0],&data,&size,error,&cancel) || data) {
            std::cerr<<"CANCELLATION FAILED\n"; free(data); return 6;
        }
        ++archives;
    }
    wookarc::close_rar_session();
    const int fd_after=fd_count();
    if(fd_after>fd_before) { std::cerr<<"FILE HANDLE LEAK "<<fd_before<<" -> "<<fd_after<<"\n"; return 7; }
    std::cerr<<"PASS archives="<<archives<<" reads="<<reads<<" fd_before="<<fd_before<<" fd_after="<<fd_after<<"\n";
    return 0;
}
