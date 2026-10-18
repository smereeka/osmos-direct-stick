#!/usr/bin/env python3
"""WookReader RU: safe library paging, page cache validation and low-RAM covers.
Applied to a fresh WookReader checkout AFTER the existing Russian UI patch.
Never alters comics, notes, saved_pages.cfg or options.cfg.
"""
from pathlib import Path
import sys

root=Path(sys.argv[1] if len(sys.argv)>1 else "wookreader-src")
chooser=root/"source/menus/book-chooser/MenuChooser.cpp"
pages=root/"source/menus/book/CBZPageLayout.cpp"
assert chooser.is_file() and pages.is_file(), "Invalid upstream WookReader checkout"

def once(text, old, new, label):
    n=text.count(old)
    if n != 1:
        raise RuntimeError(f"{label}: upstream mismatch (found {n} matches)")
    return text.replace(old,new,1)

c=chooser.read_text(encoding="utf-8")
p=pages.read_text(encoding="utf-8")

# RAR3 header-only parser must see a real end-of-archive. Otherwise fallback to libarchive.
p=once(p,"    long pos = 7;\n    for (;;)",
       "    long pos = 7;\n    bool reached_end = false;\n    for (;;)",
       "RAR3 parser")
p=once(p,"        if (btype == 0x7B) break;  // end-of-archive",
       "        if (btype == 0x7B) { reached_end = true; break; }  // actual archive end",
       "RAR3 archive end")
p=once(p,"    return true;  // was RAR3 format (even if no images found)",
       "    return reached_end && !out_names.empty(); // reject truncated page lists",
       "RAR3 return")
p=once(p,"    // Slow path: scan the archive via libarchive (needed for CBZ/CBT/CB7/RAR5).",
       "    // Drop any partial fast-parser results before scanning with libarchive.\n"
       "    _page_names.clear();\n"
       "    __atomic_store_n(&_enum_count, 0, __ATOMIC_RELEASE);\n"
       "    // Slow path: scan the archive via libarchive (needed for CBZ/CBT/CB7/RAR5).",
       "RAR3 fallback")

# New page cache format contains a page count and is only accepted when complete.
# The old .lst cache is automatically ignored; no user cache deletion needed.
start=p.index("static bool load_page_cache(")
end=p.index("// ---------------------------------------------------------------------------\n// LRU raw image byte cache",start)
p=p[:start]+r'''
static bool load_page_cache(const char *path, std::vector<std::string> &names)
{
    uint64_t key = compute_cache_key(path);
    char cache_path[512];
    snprintf(cache_path, sizeof(cache_path), "%s/%016llx.lst",
             CACHE_DIR, (unsigned long long)key);
    FILE *f = fopen(cache_path, "r");
    if (!f) return false;
    char line[1024];
    unsigned int expected = 0;
    if (!fgets(line, sizeof(line), f) ||
        sscanf(line, "#WR2 %u", &expected) != 1 ||
        expected == 0 || expected > 100000)
    {
        fclose(f);
        return false;  // old cache / damaged header: rebuild from real archive
    }
    std::vector<std::string> candidate;
    candidate.reserve(expected);
    while (fgets(line, sizeof(line), f)) {
        size_t len = strlen(line);
        if (len > 0 && line[len-1] != '\n' && !feof(f)) {
            // A path longer than the line buffer: ignore this incomplete cache.
            candidate.clear();
            break;
        }
        while (len > 0 && (line[len-1] == '\n' || line[len-1] == '\r')) len--;
        if (len) candidate.emplace_back(line,len);
        if (candidate.size() > expected) break;
    }
    fclose(f);
    if (candidate.size() != expected) {
        Log_Write(std::string("page_cache: invalid/incomplete, rebuilding ") + path);
        return false;
    }
    names.swap(candidate);
    Log_Write(std::string("page_cache: verified ") + path);
    return true;
}

static void save_page_cache(const char *path, const std::vector<std::string> &names)
{
    if (names.empty()) return;
    mkdir(CACHE_DIR, 0755);
    uint64_t key = compute_cache_key(path);
    char cache_path[512], tmp_path[512];
    snprintf(cache_path, sizeof(cache_path), "%s/%016llx.lst",
             CACHE_DIR, (unsigned long long)key);
    snprintf(tmp_path, sizeof(tmp_path), "%s.tmp", cache_path);
    FILE *f = fopen(tmp_path, "w");
    if (!f) { Log_Error(std::string("save_page_cache: cannot write ") + tmp_path); return; }
    bool ok = fprintf(f, "#WR2 %u\n", (unsigned)names.size()) >= 0;
    for (const auto &name : names) {
        if (fprintf(f, "%s\n", name.c_str()) < 0) { ok = false; break; }
    }
    if (ferror(f)) ok = false;
    if (fclose(f) != 0) ok = false;
    if (ok) {
        if (rename(tmp_path, cache_path) != 0) {
            // Some FAT filesystems need unlink before rename.
            remove(cache_path);
            if (rename(tmp_path, cache_path) != 0) remove(tmp_path);
        }
    } else {
        remove(tmp_path);
    }
}

'''+p[end:]

# The chooser must skip the cache header to select the real first image.
c=once(c,
'''    bool got = fgets(line, sizeof(line), f) != nullptr;
    fclose(f);
    if (!got) return "";
    size_t len = strlen(line);''',
'''    bool got = fgets(line, sizeof(line), f) != nullptr;
    if (got && strncmp(line, "#WR2 ", 5) == 0) {
        got = fgets(line, sizeof(line), f) != nullptr;  // skip version/count
    }
    fclose(f);
    if (!got) return "";
    size_t len = strlen(line);''',
"cover filename cached")

# LRU cache must replace an existing key without leaking SDL textures after zoom.
c=once(c,"  static const int MAX_ENTRIES = 300;",
       "  static const int MAX_ENTRIES = 28; // cap GPU memory for large libraries",
       "bounded LRU")
c=once(c,
'''    if (!tex) return nullptr;
    // Evict LRU entries until under the limit''',
'''    if (!tex) return nullptr;
    auto old = entries.find(key);
    if (old != entries.end()) {
      SDL_DestroyTexture(old->second.first);
      order.erase(old->second.second);
      entries.erase(old);
    }
    // Evict LRU entries until under the limit''',
"LRU key replacement")

# Borrow textures only during a single draw call; never retain pointers beyond eviction.
c=once(c,
'''  vector<SDL_Texture*> cover_textures;
  vector<pair<int,int>> entry_progress;''',
'''  vector<SDL_Texture*> cover_textures; // count/size only, cache owns all textures
  vector<uint8_t> cover_failed; // avoid endless retries on unreadable covers
  vector<pair<int,int>> entry_progress;''',
"cover failure state")
c=once(c,
'''    cover_textures.resize(numBooks, nullptr);
    cover_load_index = 0;''',
'''    cover_textures.resize(numBooks, nullptr);
    cover_failed.assign(numBooks, 0);
    cover_load_index = 0;''',
"folder cover reset")
c=once(c,
'''    cover_textures.resize(amountOfFiles, nullptr);
    cover_load_index = 0;''',
'''    cover_textures.resize(amountOfFiles, nullptr);
    cover_failed.assign(amountOfFiles, 0);
    cover_load_index = 0;''',
"recent cover reset")
c=once(c,
'''        cover_textures.assign(numBooks, nullptr);
        for (int i = 0; i < numBooks; i++)
          cover_textures[i] = g_cover_cache.get(sorted_entries[numFolders + i].string());
        cover_load_index = 0;''',
'''        cover_textures.assign(numBooks, nullptr);
        cover_failed.assign(numBooks, 0);
        for (int w = 0; w < N_WORKERS; w++)
          if (cl_in_flight[w] >= 0)
            __atomic_store_n(&cl[w].stale, 1, __ATOMIC_RELEASE);
        cover_load_index = 0;''',
"zoom cover reset")
c=once(c,
'''      // Result is from a previous directory — discard it
        free(cl[w].result_pixels);''',
'''      // Result is from a previous directory / zoom — discard it
        free(cl[w].result_pixels);''',
"stale result comment")
c=once(c,
'''        if (tex) g_cover_cache.put(fpath, tex);
        cover_textures[idx] = tex;''',
'''        if (tex) {
          g_cover_cache.put(fpath, tex);
          cover_failed[idx] = 0;
        } else {
          cover_failed[idx] = 1; // fall back to a book icon
        }''',
"result ownership")
c=once(c,
'''      SDL_Texture* cover =
          (bi < (int)cover_textures.size()) ? cover_textures[bi] : nullptr;''',
'''      SDL_Texture* cover = g_cover_cache.get(
          sorted_entries[numFolders + bi].string()); // cache-owned, valid this frame''',
"render safe cover lookup")

# When changing directory, keep workers marked busy until they return their
# stale result. This avoids overwriting work that is still running.
old=r'''    for (int w = 0; w < N_WORKERS; w++) {
      __atomic_store_n(&cl[w].job_ready, 0, __ATOMIC_RELEASE);  // cancel queued job
      if (cl_in_flight[w] >= 0) {
        __atomic_store_n(&cl[w].stale, 1, __ATOMIC_RELEASE);    // discard when ready
        cl_in_flight[w] = -1;
      }
    }'''
new=r'''    for (int w = 0; w < N_WORKERS; w++) {
      if (cl_in_flight[w] >= 0)
        __atomic_store_n(&cl[w].stale, 1, __ATOMIC_RELEASE);
    }'''
c=once(c,old,new,"directory job invalidation")
old=r'''    for (int w = 0; w < N_WORKERS; w++) {
      __atomic_store_n(&cl[w].job_ready, 0, __ATOMIC_RELEASE);
      if (cl_in_flight[w] >= 0) {
        __atomic_store_n(&cl[w].stale, 1, __ATOMIC_RELEASE);
        cl_in_flight[w] = -1;
      }
    }'''
new=r'''    for (int w = 0; w < N_WORKERS; w++) {
      if (cl_in_flight[w] >= 0)
        __atomic_store_n(&cl[w].stale, 1, __ATOMIC_RELEASE);
    }'''
c=once(c,old,new,"recent job invalidation")

# Cover prefetch only covers visible rows + one row on each side; never
# spend several minutes loading books outside the screen.
start=c.index("    // Submit next uncached covers to any idle workers")
end=c.index("    // ── Rendering ──",start)
c=c[:start]+r'''    // Demand-driven covers: visible rows plus one margin row.
    // No O(library size) decode/memory spike with 70+ comics.
    int totalBooks = (int)cover_textures.size();
    int usableH = windowY - BOTTOM_H;
    int booksY = numFolders * FOLDER_H;
    int firstVisibleY = max(0, scroll_y - booksY);
    int lastVisibleY = max(0, scroll_y + usableH - booksY);
    int firstBook = max(0, firstVisibleY / cell_h - 1) * cols;
    int lastBook = min(totalBooks, (lastVisibleY / cell_h + 2) * cols);
    for (int w = 0; w < N_WORKERS; w++) {
      if (cl_in_flight[w] >= 0) continue;
      for (int i = firstBook; i < lastBook; i++) {
        if (cover_failed[i]) continue;
        bool alreadyBusy = false;
        for (int w2 = 0; w2 < N_WORKERS; w2++)
          if (cl_in_flight[w2] == i) alreadyBusy = true;
        if (alreadyBusy) continue;
        string fpath = sorted_entries[numFolders + i].string();
        SDL_Texture* cached = g_cover_cache.get(fpath);
        if (cached) {
          int cw = 0, ch = 0;
          SDL_QueryTexture(cached, nullptr, nullptr, &cw, &ch);
          if (cw > 0 && cw * 6 >= thumb_w * 5) continue;
        }
        string ext; size_t dot = fpath.find_last_of('.');
        if (dot != string::npos) ext = fpath.substr(dot);
        transform(ext.begin(), ext.end(), ext.begin(), ::tolower);
        bool is_pdf = (ext != ".cbz" && ext != ".cbr" && ext != ".cbt" && ext != ".cb7");
        strncpy(cl[w].job_path, fpath.c_str(), sizeof(cl[w].job_path) - 1);
        cl[w].job_path[sizeof(cl[w].job_path) - 1] = '\0';
        cl[w].job_tw = thumb_w; cl[w].job_th = thumb_h;
        cl[w].job_index = i; cl[w].job_is_pdf = is_pdf;
        cl_in_flight[w] = i;
        __atomic_store_n(&cl[w].stale, 0, __ATOMIC_RELEASE);
        __atomic_store_n(&cl[w].job_ready, 1, __ATOMIC_RELEASE);
        break;
      }
    }

'''+c[end:]

# JPEG cover decoding: use TurboJPEG native scaling (e.g. 1/8 image size).
# Decode thumbnails directly rather than allocating 100MB for every full cover.
start=c.index("  if (is_jpeg) {\n    tjhandle tj = tjInitDecompress();")
end=c.index("  if (!src_px) return nullptr;",start)
c=c[:start]+r'''  if (is_jpeg) {
    tjhandle tj = tjInitDecompress();
    if (tj) {
      int subsamp = 0, cs = 0;
      if (tjDecompressHeader3(tj, d, (unsigned long)cover_data.size(),
                              &src_w, &src_h, &subsamp, &cs) == 0 &&
          src_w > 0 && src_h > 0) {
        int iw = src_w, ih = src_h, factorCount = 0;
        const tjscalingfactor* factors = tjGetScalingFactors(&factorCount);
        for (int k = 0; factors && k < factorCount; k++) {
          int fw = TJSCALED(src_w, factors[k]);
          int fh = TJSCALED(src_h, factors[k]);
          if (fw >= tw && fh >= th && (uint64_t)fw * fh < (uint64_t)iw * ih) {
            iw = fw; ih = fh;
          }
        }
        if ((uint64_t)iw * ih <= 12000000ULL) {
          src_px = (unsigned char*)malloc((size_t)iw * ih * 4);
          if (src_px && tjDecompress2(tj, d, (unsigned long)cover_data.size(),
                                      src_px, iw, 0, ih, TJPF_RGBA,
                                      TJFLAG_FASTDCT) == 0) {
            src_w = iw; src_h = ih;
          } else {
            free(src_px); src_px = nullptr;
          }
        }
      }
      tjDestroy(tj);
    }
  }
  if (!src_px) {
    // Never fallback to an unscaled 100MB bitmap when memory is tight.
    int iw = 0, ih = 0, ch = 0;
    if (stbi_info_from_memory(d, (int)cover_data.size(), &iw, &ih, &ch) &&
        ((uint64_t)iw * ih > 12000000ULL)) {
      return nullptr; // display book icon instead of running out of memory
    }
    src_px = stbi_load_from_memory(d, (int)cover_data.size(), &src_w, &src_h, &ch, 4);
    is_jpeg = false;
  }
'''+c[end:]

# Keep worker results exclusive until their previous buffer has been consumed.
c=once(c,
'''  while (!__atomic_load_n(&cl->should_exit, __ATOMIC_ACQUIRE)) {
    if (!__atomic_load_n(&cl->job_ready, __ATOMIC_ACQUIRE)) {''',
'''  while (!__atomic_load_n(&cl->should_exit, __ATOMIC_ACQUIRE)) {
    if (__atomic_load_n(&cl->result_ready, __ATOMIC_ACQUIRE)) {
      svcSleepThread(2000000LL); continue; // wait for main thread to free pixels
    }
    if (!__atomic_load_n(&cl->job_ready, __ATOMIC_ACQUIRE)) {''',
"worker result handshake")

chooser.write_text(c,encoding="utf-8")
pages.write_text(p,encoding="utf-8")
print("Optimized: full RAR3 validation, verified page cache, demand covers, bounded LRU,")
print("           reduced JPEG thumbnail memory, fixed stale workers and cache ownership.")
