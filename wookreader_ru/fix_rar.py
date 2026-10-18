#!/usr/bin/env python3
"""Install the tested UnRAR backend after apply_ru.py and optimize.py.

The dependency build is separate (build_unrar.py); no comic, saved position,
configuration, forwarder or icon is modified by this patch.
"""
from pathlib import Path
import shutil
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else "wookreader-src")
here = Path(__file__).resolve().parent


def once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one upstream match, got {count}")
    return text.replace(old, new, 1)


pages = root / "source/menus/book/CBZPageLayout.cpp"
chooser = root / "source/menus/book-chooser/MenuChooser.cpp"
p = pages.read_text(encoding="utf-8")
c = chooser.read_text(encoding="utf-8")

p = once(p, '#include "CBZPageLayout.hpp"',
         '#include "CBZPageLayout.hpp"\n#include "WookRAR.hpp"', "page backend include")
c = once(c, '#include <turbojpeg.h>',
         '#include <turbojpeg.h>\n#include "../book/WookRAR.hpp"', "cover backend include")

# All RAR member names now come from the same parser as extraction. The old
# header-only parser could enumerate solid RAR4 that libarchive cannot decode.
start = p.index("// Scan a RAR3 archive's file headers WITHOUT decompression.")
end = p.index("static bool is_image_ext(const char *filename)\n{", start)
p = p[:start] + p[end:]

start = p.index("void CBZPageLayout::do_enumerate()\n{")
end = p.index("void CBZPageLayout::finish_enumeration()", start)
p = p[:start] + r'''void CBZPageLayout::do_enumerate()
{
    const bool rar = wookrar::is_archive(_archive_path.c_str());
    // Re-enumerate RAR headers with UnRAR even when an older .lst exists. This
    // also refreshes ANSI/truncated names from the old independent RAR parser.
    // Header listing is cheap and does not decompress the solid dictionary.
    bool have_names = !rar && load_page_cache(_archive_path.c_str(), _page_names);
    void *fd = nullptr; size_t fs = 0;
    if (rar) {
        wookrar::Info info;
        auto result = wookrar::list_images(_archive_path.c_str(), _page_names,
                                          &_enum_count, &info);
        if (!result.ok()) {
            Log_Error("RAR list: " + wookrar::describe(result) + " path=" + _archive_path);
            _page_names.clear();
        } else {
            std::sort(_page_names.begin(), _page_names.end());
            Log_Write("RAR backend=UnRAR solid=" + std::to_string(info.solid) +
                      " pages=" + std::to_string(_page_names.size()) + " path=" + _archive_path);
            save_page_cache(_archive_path.c_str(), _page_names);
            have_names = true;
        }
    } else if (!have_names) {
        _page_names.clear();
        __atomic_store_n(&_enum_count, 0, __ATOMIC_RELEASE);
        if (!enumerate_images(_archive_path.c_str(), _page_names,
                              &_first_image_name, &fd, &fs, &_enum_count)) {
            _page_names.clear();
        } else {
            std::sort(_page_names.begin(), _page_names.end());
            save_page_cache(_archive_path.c_str(), _page_names);
        }
    }
    __atomic_store_n(&_enum_count, (int)_page_names.size(), __ATOMIC_RELEASE);
    if (have_names && !_page_names.empty()) {
        _first_image_name = _page_names[0];
        extract_image_cached(_archive_path.c_str(), _page_names[0], &fd, &fs);
    }
    if (fd) {
        _first_image_raw = fd;
        _first_image_size = fs;
        __atomic_store_n(&_first_image_ready, 1, __ATOMIC_RELEASE);
    }
    __atomic_store_n(&_enum_done, 1, __ATOMIC_RELEASE);
}

''' + p[end:]

# Route by the actual magic bytes: a ZIP named .cbr still uses the ZIP backend.
marker = '''    *out_data = nullptr;
    *out_size = 0;

    struct archive *a = archive_read_new();'''
replacement = '''    *out_data = nullptr;
    *out_size = 0;

    if (wookrar::is_archive(path)) {
        auto result = wookrar::extract(path, target_name, out_data, out_size,
                                      cancel_flag, SDL_malloc, SDL_free);
        if (!result.ok() && result.error != wookrar::Error::cancelled)
            Log_Error("RAR extract: " + wookrar::describe(result) + " archive=" + path);
        else if (result.ok())
            Log_Write("RAR extracted: " + target_name + " bytes=" + std::to_string(*out_size));
        return result.ok();
    }

    struct archive *a = archive_read_new();'''
p = once(p, marker, replacement, "page extraction dispatch")

# Preserve archive traversal errors in the non-RAR fallback too. The old code
# turned every header failure (including ARCHIVE_FATAL) into 'entry not found'.
start = p.index("static bool extract_image(const char *path")
end = p.index("// Wrapper that checks/populates g_raw_cache", start)
extract = p[start:end]
extract = once(extract,
    "    while (archive_read_next_header(a, &entry) == ARCHIVE_OK)",
    "    int header_result = ARCHIVE_OK;\n"
    "    while ((header_result = archive_read_next_header(a, &entry)) == ARCHIVE_OK)",
    "capture extraction header status")
extract = once(extract,
    '''    archive_read_free(a);

    if (!found && !(cancel_flag && __atomic_load_n(cancel_flag, __ATOMIC_ACQUIRE)))
        Log_Error("extract_image: entry not found: " + target_name + " in " + path);''',
    '''    if (!found && !(cancel_flag && __atomic_load_n(cancel_flag, __ATOMIC_ACQUIRE))) {
        if (header_result != ARCHIVE_EOF) {
            const char *error = archive_error_string(a);
            Log_Error("extract_image: archive traversal failed rc=" + std::to_string(header_result) +
                      " error=" + (error ? error : "unknown") + " target=" + target_name + " in " + path);
        } else {
            Log_Error("extract_image: entry not found: " + target_name + " in " + path);
        }
    }
    archive_read_free(a);''', "preserve real extraction error")
p = p[:start] + extract + p[end:]

start = p.index("static bool enumerate_images(")
end = p.index("// Open archive and extract image data", start)
enumerate_code = p[start:end]
enumerate_code = once(enumerate_code,
    "    while (archive_read_next_header(a, &entry) == ARCHIVE_OK)",
    "    int header_result = ARCHIVE_OK;\n"
    "    while ((header_result = archive_read_next_header(a, &entry)) == ARCHIVE_OK)",
    "capture enumeration status")
enumerate_code = once(enumerate_code,
    "    archive_read_free(a);\n    return true;",
    '''    if (header_result != ARCHIVE_EOF) {
        const char *error = archive_error_string(a);
        Log_Error("enumerate_images: archive traversal failed rc=" + std::to_string(header_result) +
                  " error=" + (error ? error : "unknown") + " path=" + path);
        out_names.clear();
        if (out_count) __atomic_store_n(out_count, 0, __ATOMIC_RELEASE);
        if (first_data && *first_data) { SDL_free(*first_data); *first_data = nullptr; }
        if (first_size) *first_size = 0;
        archive_read_free(a);
        return false;
    }
    archive_read_free(a);
    return true;''', "reject partial enumeration on decoder error")
p = p[:start] + enumerate_code + p[end:]

# Thread creation/start can fail under memory pressure. The synchronous path
# must use the same RAR backend as background enumeration.
p = once(p,
    '''    // Fallback: synchronous enumeration if thread creation fails
    if (!enumerate_images(path, _page_names))
        return;''',
    '''    // Fallback: the same RAR decoder even when enumeration cannot start a thread.
    if (wookrar::is_archive(path)) {
        auto result = wookrar::list_images(path, _page_names);
        if (!result.ok()) {
            Log_Error("RAR list (sync): " + wookrar::describe(result) + " path=" + path);
            return;
        }
    } else if (!enumerate_images(path, _page_names)) {
        return;
    }''', "synchronous RAR enumeration")

# Cover extraction uses the same serialized decoder, including when the first
# alphabetical image is physically a later member of a solid archive.
start = c.index("static string find_cover_in_rar3(")
end = c.index("// Extract cover from a comic archive", start)
c = c[:start] + c[end:]
c = once(c,
    '''    if (target_name.empty()) {
      // Fast path for RAR3 (CBR): scan file headers directly using PACK_SIZE fseeks.
      // Zero decompression — correct for both solid and non-solid, completes in ms.
      target_name = find_cover_in_rar3(path);
    }

    // Single-pass libarchive: find target (if not known) AND extract in one traversal.
    {''',
    '''    if (wookrar::is_archive(path)) {
      vector<string> names;
      auto listed = wookrar::list_images(path, names);
      if (!listed.ok() || names.empty()) {
        if (!listed.ok()) Log_Error("RAR cover list: " + wookrar::describe(listed) + " path=" + path);
        return nullptr;
      }
      sort(names.begin(), names.end());
      target_name = names.front();
      void *raw = nullptr; size_t size = 0;
      auto result = wookrar::extract(path, target_name, &raw, &size, nullptr, malloc, free);
      if (!result.ok()) {
        Log_Error("RAR cover: " + wookrar::describe(result) + " path=" + path);
        return nullptr;
      }
      cover_data.assign((const uint8_t *)raw, (const uint8_t *)raw + size);
      free(raw);
    } else {
      // ZIP/TAR/7z retain their existing libarchive backend.''', "cover extraction dispatch")

main = root / "source/main.cpp"
m = main.read_text(encoding="utf-8")
m = once(m, '  Log_Write("Initialize Services");',
         '  Log_Write("Initialize Services");\n'
         '  Log_Write("WOOK RU2 RARFIX1: serialized UnRAR backend; CRC-checked solid extraction");',
         "build identification log")

makefile = root / "Makefile"
mf = makefile.read_text(encoding="utf-8")
mf = once(mf, "EXTRA_LIBS := $(TOPDIR)/lib/libmupdf.a",
          "EXTRA_LIBS := $(TOPDIR)/lib/libmupdf.a $(TOPDIR)/lib/libunrar.a",
          "static UnRAR linkage")

reader = root / "source/menus/book/BookReader.cpp"
r = reader.read_text(encoding="utf-8")
r = once(r, "BookReader: opening as comic ZIP: ",
         "BookReader: opening as comic archive: ", "accurate archive log label")

pages.write_text(p, encoding="utf-8")
chooser.write_text(c, encoding="utf-8")
main.write_text(m, encoding="utf-8")
makefile.write_text(mf, encoding="utf-8")
reader.write_text(r, encoding="utf-8")
for name in ("WookRAR.hpp", "WookRAR.cpp"):
    shutil.copyfile(here / name, root / "source/menus/book" / name)
print("RARFIX1: shared UnRAR decoder for page names, page data and covers; real decoder errors retained.")
