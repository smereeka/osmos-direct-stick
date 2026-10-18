#!/usr/bin/env python3
"""Apply after apply_ru.py + optimize.py. No user data or icon is published."""
from pathlib import Path
import re, shutil, sys
root=Path(sys.argv[1] if len(sys.argv)>1 else 'wookreader-src')
assets=Path(__file__).resolve().parent
pfile=root/'source/menus/book/CBZPageLayout.cpp'
cfile=root/'source/menus/book-chooser/MenuChooser.cpp'
hfile=root/'source/menus/book/CBZPageLayout.hpp'
p=pfile.read_text(encoding='utf-8')
c=cfile.read_text(encoding='utf-8')
h=hfile.read_text(encoding='utf-8')

def one(s,old,new,label):
    n=s.count(old)
    if n!=1: raise RuntimeError(f'{label}: expected one source pattern, found {n}')
    return s.replace(old,new,1)

def block(s,start,end,new):
    a=s.index(start); b=s.index(end,a)
    return s[:a]+new+'\n\n'+s[b:]

shutil.copy2(assets/'archive_backend.cpp',root/'source/menus/book/archive_backend.cpp')
shutil.copy2(assets/'archive_backend.hpp',root/'source/menus/book/archive_backend.hpp')
p='#include "archive_backend.hpp"\n'+p

# Replace BOTH listing and extraction. Never leave the old permissive scanner active.
p=block(p,'static bool enumerate_images(', '// Open archive and extract image data',r'''static bool enumerate_images(const char *path, std::vector<std::string> &out_names,
                              std::string *first_name = nullptr,
                              void **first_data = nullptr, size_t *first_size = nullptr,
                              volatile int *out_count = nullptr)
{
    std::vector<std::string> entries;
    std::string error;
    out_names.clear();
    if (!wookarc::list(path, entries, error)) {
        Log_Error(std::string("archive_list: ") + error + " path=" + path);
        return false;
    }
    for (const auto& name : entries)
        if (is_image_ext(name.c_str())) out_names.push_back(name);
    std::sort(out_names.begin(), out_names.end());
    if (out_count) __atomic_store_n(out_count, (int)out_names.size(), __ATOMIC_RELEASE);
    if (first_name && first_data && first_size && !out_names.empty()) {
        *first_data = nullptr; *first_size = 0;
        if (wookarc::extract(path, out_names[0], first_data, first_size, error))
            *first_name = out_names[0];
        else Log_Error(std::string("archive_cover: ") + error + " path=" + path);
    }
    return true;
}''')
p=block(p,'static bool extract_image(', '// Wrapper that checks/populates',r'''static bool extract_image(const char *path, const std::string &target_name,
                          void **out_data, size_t *out_size,
                          volatile int *cancel_flag = nullptr)
{
    std::string error;
    bool ok = wookarc::extract(path, target_name, out_data, out_size, error, cancel_flag);
    if (!ok && !(cancel_flag && __atomic_load_n(cancel_flag, __ATOMIC_ACQUIRE)))
        Log_Error(std::string("archive_extract: ") + error + " page=" + target_name + " path=" + path);
    return ok;
}''')
p=block(p,'void CBZPageLayout::do_enumerate()', 'void CBZPageLayout::finish_enumeration()',r'''void CBZPageLayout::do_enumerate()
{
    _archive_error.clear();
    _page_names.clear();
    const bool cached = load_page_cache(_archive_path.c_str(), _page_names);
    if (!cached) {
        std::vector<std::string> all;
        if (!wookarc::list(_archive_path.c_str(), all, _archive_error)) {
            Log_Error("archive_list: " + _archive_error + " path=" + _archive_path);
            _page_names.clear();
            __atomic_store_n(&_enum_done, 1, __ATOMIC_RELEASE);
            return;
        }
        for (const auto& name : all)
            if (is_image_ext(name.c_str())) _page_names.push_back(name);
        std::sort(_page_names.begin(), _page_names.end());
        // Only a complete, successful scan may be persisted.
        if (!_page_names.empty()) save_page_cache(_archive_path.c_str(), _page_names);
    }
    __atomic_store_n(&_enum_count, (int)_page_names.size(), __ATOMIC_RELEASE);
    if (!_page_names.empty()) {
        void *data = nullptr; size_t size = 0;
        std::string error;
        if (wookarc::extract(_archive_path.c_str(), _page_names[0], &data, &size, error)) {
            _first_image_raw = data; _first_image_size = size;
            _first_image_name = _page_names[0];
            __atomic_store_n(&_first_image_ready, 1, __ATOMIC_RELEASE);
        } else {
            _archive_error = error;
            Log_Error("archive_first_page: " + error + " path=" + _archive_path);
        }
    } else {
        _archive_error = "No supported page images in archive";
    }
    Log_Write("Archive backend=" + std::string(wookarc::is_rar4(_archive_path.c_str()) ? "unarr/RAR4" : "libarchive")
              + " pages=" + std::to_string(_page_names.size()) + " path=" + _archive_path);
    __atomic_store_n(&_enum_done, 1, __ATOMIC_RELEASE);
}''')

# Ignore all caches written by older scanners, including valid-but-incomplete WR2 files.
p=p.replace('#WR2 ', '#WR3 ')
c=c.replace('#WR2 ', '#WR3 ')
h=one(h,'    std::string              _archive_path;',
      '    std::string              _archive_error;\n    std::string              _archive_path;', 'archive error member')
h=one(h,'    bool is_valid()        const { return _valid; }',
      '    const std::string& archive_error() const { return _archive_error; }\n    bool is_valid()        const { return _valid; }', 'archive error accessor')

# Do not navigate a vector while the enumeration worker owns it.
for name in ['previous_page(int n)','next_page(int n)','goto_page(int num)']:
    sig='void CBZPageLayout::'+name+'\n{'
    p=one(p,sig,sig+'\n    if (_enumerating) return;', 'safe navigation '+name)

# Only forward prefetch for RAR4: avoid repeatedly rebuilding a solid dictionary backwards.
p=one(p,'        if (i == 2) continue;',
      '        if (i == 2 || (i == 1 && wookarc::is_rar4(_archive_path.c_str()))) continue;',
      'RAR4 sequential prefetch')
p=one(p,'    cancel_prefetch();\n    free_ready_textures();\n    FreeTextureIfNeeded(&_tex);',
      '    cancel_prefetch();\n    wookarc::close_rar_session();\n    free_ready_textures();\n    FreeTextureIfNeeded(&_tex);',
      'release archive session')

# Decode BMP/TIF as well as the original list. Existing SDL fallback handles TIFF.
p=one(p,'           strcmp(ext, ".gif")  == 0 || strcmp(ext, ".tiff") == 0;',
      '           strcmp(ext, ".gif")  == 0 || strcmp(ext, ".tiff") == 0 ||\n'
      '           strcmp(ext, ".tif")  == 0 || strcmp(ext, ".bmp") == 0;', 'additional page images')

# On an actual failed page, show an error instead of silently leaving a black page/cover.
pos=p.index('void CBZPageLayout::draw_page()')
a=p.index('{',pos)+1
p=p[:a]+'''\n    if (!_tex) {
        SDL_DrawTextWrapped(RENDERER, ROBOTO_25, 100, 280, 1080, 200,
            configDarkMode ? WHITE : BLACK,
            "Не удалось прочитать страницу.\\nПодробности ошибки сохранены в log.txt.\\nB — вернуться к библиотеке.");
        return;
    }
'''+p[a:]

# Allow PDF thumbnails even before the first PDF has been opened; don't share MuPDF
# objects between independent threads without a locking context.
c=one(c,'  if (!ctx) return nullptr;\n  fz_context* bg_ctx = fz_clone_context(ctx);\n  if (!bg_ctx) return nullptr;',
      '  fz_context* bg_ctx = fz_new_context(nullptr, nullptr, 8 * 1024 * 1024);\n'
      '  if (!bg_ctx) return nullptr;\n  fz_register_document_handlers(bg_ctx);', 'independent PDF cover context')

# Release thumbnail GPU memory before opening any comic; RU2 never holds borrowed
# cache pointers across frames, so this is safe and covers reload after returning.
c=c.replace('Menu_OpenBook((char*)book.c_str(), scroll_speed, zoom_amount);',
            'g_cover_cache.clear();\n                Menu_OpenBook((char*)book.c_str(), scroll_speed, zoom_amount);')

# UTF-8-safe label trimming (bytewise pop_back could split Cyrillic characters).
c=one(c,'              fname.pop_back();',
      '''              size_t cut = fname.size() - 1;
              while (cut > 0 && ((unsigned char)fname[cut] & 0xC0) == 0x80) --cut;
              fname.erase(cut);''', 'Cyrillic title trimming')

# Bound thumbnail memory by bytes, not by an entry count smaller than a wide viewport.
a=c.index('struct LRUTextureCache {'); b=c.index('static LRUTextureCache g_cover_cache;',a)
c=c[:a]+'''struct LRUTextureCache {
  struct Entry { SDL_Texture* tex; size_t bytes; list<string>::iterator pos; };
  list<string> order;
  unordered_map<string,Entry> entries;
  size_t total = 0;
  static constexpr size_t MAX_BYTES = 32 * 1024 * 1024;
  SDL_Texture* get(const string& key) {
    auto it = entries.find(key);
    if (it == entries.end()) return nullptr;
    order.splice(order.begin(), order, it->second.pos);
    return it->second.tex;
  }
  SDL_Texture* put(const string& key, SDL_Texture* tex) {
    if (!tex) return nullptr;
    auto old = entries.find(key);
    if (old != entries.end()) {
      total -= old->second.bytes; SDL_DestroyTexture(old->second.tex);
      order.erase(old->second.pos); entries.erase(old);
    }
    int w = 0, h = 0;
    SDL_QueryTexture(tex, nullptr, nullptr, &w, &h);
    size_t bytes = (size_t)max(0,w) * max(0,h) * 4;
    while (!order.empty() && total + bytes > MAX_BYTES) {
      auto it = entries.find(order.back());
      total -= it->second.bytes; SDL_DestroyTexture(it->second.tex);
      entries.erase(it); order.pop_back();
    }
    order.push_front(key); entries.emplace(key, Entry{tex,bytes,order.begin()});
    total += bytes; return tex;
  }
  void clear() {
    for (auto& e : entries) SDL_DestroyTexture(e.second.tex);
    entries.clear(); order.clear(); total = 0;
  }
};
''' + c[b:]

pfile.write_text(p,encoding='utf-8'); cfile.write_text(c,encoding='utf-8'); hfile.write_text(h,encoding='utf-8')

# Correct errors shown after enumeration, while retaining real diagnostic details in log.
bfile=root/'source/menus/book/BookReader.cpp'
b=bfile.read_text(encoding='utf-8')
b=one(b,'                        "В архиве нет изображений.");',
      '                        "Не удалось открыть страницы. Подробности — в log.txt.");', 'archive failure UI')
bfile.write_text(b,encoding='utf-8')

# Guard multi-threaded stream writes; interleaved std::ofstream access is undefined.
mfile=root/'source/main.cpp'; m=mfile.read_text(encoding='utf-8')
m=one(m,'std::ofstream logFile;',
      'static Mutex wook_log_mutex{};\nstruct WookLogGuard { WookLogGuard() { mutexLock(&wook_log_mutex); } ~WookLogGuard() { mutexUnlock(&wook_log_mutex); } };\nstd::ofstream logFile;', 'log mutex')
for func in ['void Log_Write(const std::string& msg) {','void Log_Error(const std::string& msg) {','void Log_Close() {']:
    m=one(m,func,func+'\n  WookLogGuard guard;', 'log synchronization')
m=m.replace('=== WookReader Log Started ===','=== WOOK 0.7.0-RU3 | RAR4=unarr | other=libarchive ===')
mfile.write_text(m,encoding='utf-8')

mf=root/'Makefile'; s=mf.read_text(encoding='utf-8')
s=one(s,'EXTRA_LIBS := $(TOPDIR)/lib/libmupdf.a',
      'EXTRA_LIBS := $(TOPDIR)/lib/libmupdf.a $(TOPDIR)/lib/libwookunarr.a', 'link RAR backend')
s=s.replace('APP_TITLE   := WookReader','APP_TITLE   := WOOK')
s=re.sub(r'^APP_VERSION\s*:=.*$', 'APP_VERSION := 0.7.0-RU3', s, flags=re.M)
mf.write_text(s,encoding='utf-8')
print('RU3 installed: actual RAR4 decoder, strict reads, WR3 cache, no partial enumeration races, bounded library GPU memory.')
