#pragma once
#include <cstddef>
#include <string>
#include <vector>

// RAR3/4 is handled by unarr, including solid archives.
// ZIP, RAR5 and other formats keep the existing libarchive path.
bool wook_rar3(const char* path);
bool wook_rar_list(const char* path, std::vector<std::string>& names, std::string& error);
bool wook_rar_extract(const char* path, const std::string& name,
                      void** data, std::size_t* size, volatile int* cancel,
                      std::string& error);
void wook_rar_close();
