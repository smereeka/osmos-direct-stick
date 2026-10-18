// WOOK RU3 archive backend. SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <cstddef>
#include <string>
#include <vector>
namespace wookarc {
// Signature-based routing, independent of filename extension.
bool is_rar4(const char* path);
std::string normalize(const std::string& name);
bool list(const char* path, std::vector<std::string>& names, std::string& error,
          volatile int* cancel = nullptr);
// Success returns malloc-owned bytes; caller must free the buffer.
bool extract(const char* path, const std::string& name, void** data,
             size_t* size, std::string& error, volatile int* cancel = nullptr);
void close_rar_session();
}
