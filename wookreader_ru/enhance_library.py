#!/usr/bin/env python3
"""Install the RU3 library UI on the pinned, cover-fixed chooser.

The archive, image decoder and reader implementations remain separate.  All
library records are provided by WookLibrary; a collection is a list of original
paths and no operation here moves or deletes a comic.
"""
from pathlib import Path
import sys


def once(text, old, new, name):
    if text.count(old) != 1:
        raise RuntimeError(f"{name}: expected one pinned source match")
    return text.replace(old, new, 1)


HELPERS = r'''
// RU3 library UI. Textures, state and navigation are owned by the UI thread.
// BEGIN WOOK_LIBRARY_HELPERS
enum class LibraryViewKind { Directory, All, Collections, Collection, Recent, Search };
enum class LibraryEntryKind { Folder, Link, Book, Series };
struct LibraryView {
  LibraryViewKind kind = LibraryViewKind::Directory;
  string path = "/switch/WookReader";
  string collection_id, query, selected_key;
  bool favorites = false, unread = false;
  int scroll = 0;
};
struct LibraryEntry {
  LibraryEntryKind kind = LibraryEntryKind::Book;
  string key, name, path, cover_path, collection_id;
  LibraryViewKind link = LibraryViewKind::All;
  wook::BookState state;
  int count = 0, completed = 0;
  bool note = false, missing = false;
};
struct LibraryAction {
  string label;
  std::function<void()> run;
};
enum class LibraryModal { None, Actions, Notes, Options, Text, Picker, Confirm };

static string library_extension(const string& path) {
  string ext = fs::path(path).extension().string();
  for (char& c : ext) if (c >= 'A' && c <= 'Z') c += 'a' - 'A';
  return ext;
}
static bool library_is_book(const string& path) {
  const string ext = library_extension(path);
  return ext == ".cbr" || ext == ".cbz" || ext == ".cbt" ||
         ext == ".cb7" || ext == ".pdf" || ext == ".epub" || ext == ".xps";
}
static bool library_exists(const string& path) {
  std::error_code ec;
  return !path.empty() && fs::is_regular_file(fs::path(path), ec);
}
static string library_parent(const string& path) {
  string parent = fs::path(path).parent_path().string();
  return parent.empty() ? "/" : parent;
}
static bool library_skip_directory(const fs::path& path) {
  string name = wook::casefold_utf8(path.filename().string());
  return name.empty() || name[0] == '.' || name == "nintendo" ||
         name == "atmosphere" || name == "bootloader" || name == "emuMMC" ||
         name == "emummc" || name == "config";
}
static void library_scan_root(const string& root, std::set<string>& paths) {
  std::error_code ec;
  if (!fs::is_directory(root, ec)) return;
  if (root == "/" || root == "sdmc:/") {
    // The SD root is useful for navigation, never an implicit recursive index.
    fs::directory_iterator it(root, fs::directory_options::skip_permission_denied, ec), end;
    for (; !ec && it != end; it.increment(ec)) {
      std::error_code entry_ec;
      if (it->is_regular_file(entry_ec) && library_is_book(it->path().string()))
        paths.insert(it->path().lexically_normal().string());
    }
    return;
  }
  fs::recursive_directory_iterator it(root, fs::directory_options::skip_permission_denied, ec), end;
  for (; !ec && it != end; it.increment(ec)) {
    std::error_code entry_ec;
    if (it->is_directory(entry_ec)) {
      if (library_skip_directory(it->path()) || it.depth() >= 32)
        it.disable_recursion_pending();
    } else if (it->is_regular_file(entry_ec) && library_is_book(it->path().string())) {
      paths.insert(it->path().lexically_normal().string());
    }
  }
}
static bool library_accepts(const string& path, const LibraryView& view) {
  if (!view.query.empty() && !wook::matches_query(wook::title(path), view.query)) return false;
  const auto state = wook::book_state(path);
  return (!view.favorites || state.favorite) && (!view.unread || !state.finished);
}
static string library_progress(const wook::BookState& state) {
  if (state.finished) return "Прочитано";
  if (state.last_read <= 0 && state.page <= 0) return "Не начато";
  if (state.total > 0)
    return std::to_string(std::min(state.page + 1, state.total)) + " / " + std::to_string(state.total);
  return "Читается";
}
static int library_text_width(TTF_Font* font, const string& text) {
  int width = 0;
  if (!font || TTF_SizeUTF8(font, text.c_str(), &width, nullptr) < 0) return 0;
  return width;
}
static string library_issue_suffix(const string& title) {
  // Keep an explicit issue number visible after shortening a long series name.
  // Also supports a trailing parenthesized scan/language tag after the number.
  size_t end = title.size();
  while (end > 0 && title[end - 1] == ' ') --end;
  size_t digits_end = end;
  if (end > 0 && (title[end - 1] == ')' || title[end - 1] == ']')) {
    const char open = title[end - 1] == ')' ? '(' : '[';
    const size_t tag = title.rfind(open, end - 1);
    if (tag != string::npos) {
      digits_end = tag;
      while (digits_end > 0 && title[digits_end - 1] == ' ') --digits_end;
    }
  }
  size_t begin = digits_end;
  while (begin > 0 && title[begin - 1] >= '0' && title[begin - 1] <= '9') --begin;
  const size_t digits = digits_end - begin;
  if (digits == 0 || digits > 3) return "";
  if (begin > 0 && title[begin - 1] == '#') --begin;
  if (begin > 0 && title[begin - 1] != ' ' && title[begin - 1] != '_' && title[begin - 1] != '-') return "";
  return " " + title.substr(begin, digits_end - begin);
}
static string library_fit_title(TTF_Font* font, const string& title, int width) {
  if (library_text_width(font, title) <= width) return title;
  string suffix = library_issue_suffix(title);
  string prefix = title;
  if (!suffix.empty()) {
    const size_t at = prefix.rfind(suffix.substr(1));
    if (at != string::npos) prefix.resize(at);
  }
  const string dots = "...";
  if (library_text_width(font, dots + suffix) > width) suffix.clear();
  while (!prefix.empty() && library_text_width(font, prefix + dots + suffix) > width)
    wook::pop_utf8(prefix);
  return prefix + dots + suffix;
}
static vector<string> library_wrap(TTF_Font* font, const string& text, int width) {
  vector<string> lines;
  string line;
  // Iterate complete UTF-8 code points; the next code point never loses a byte.
  for (size_t pos = 0; pos < text.size();) {
    if (text[pos] == '\r') { ++pos; continue; }
    if (text[pos] == '\n') { lines.push_back(line); line.clear(); ++pos; continue; }
    size_t next = pos + 1;
    while (next < text.size() && (static_cast<unsigned char>(text[next]) & 0xc0) == 0x80) ++next;
    const string cp = text.substr(pos, next - pos);
    if (!line.empty() && library_text_width(font, line + cp) > width) {
      const size_t word = line.rfind(' ');
      if (word != string::npos && word > line.size() / 3) {
        lines.push_back(line.substr(0, word));
        line = line.substr(word + 1);
      } else { lines.push_back(line); line.clear(); }
    }
    if (!(line.empty() && cp == " ")) line += cp;
    pos = next;
  }
  if (!line.empty() || lines.empty()) lines.push_back(line);
  return lines;
}

// SDL_helper's legacy cache truncates its key to 127 bytes. The library uses
// full strings, so distinct long Cyrillic titles cannot alias in that cache.
class LibraryTextCache {
  struct Value { SDL_Texture* texture; int width, height; size_t bytes; };
  std::map<string, Value> values;
  std::list<string> order;
  size_t bytes = 0;
 public:
  void clear() {
    for (const auto& item : values) SDL_DestroyTexture(item.second.texture);
    values.clear(); order.clear(); bytes = 0;
  }
  ~LibraryTextCache() { clear(); }
  void draw(TTF_Font* font, int x, int y, SDL_Color color, const string& text) {
    if (!font || text.empty()) return;
    const string key = std::to_string(reinterpret_cast<uintptr_t>(font)) + ":" +
      std::to_string(color.r) + ":" + std::to_string(color.g) + ":" +
      std::to_string(color.b) + ":" + std::to_string(color.a) + ":" + text;
    auto it = values.find(key);
    if (it == values.end()) {
      SDL_Surface* surface = TTF_RenderUTF8_Blended(font, text.c_str(), color);
      if (!surface) return;
      Value item = {SDL_CreateTextureFromSurface(RENDERER, surface), surface->w, surface->h,
                    static_cast<size_t>(surface->w) * surface->h * 4};
      SDL_FreeSurface(surface);
      if (!item.texture) return;
      while (!order.empty() && (values.size() >= 160 || bytes + item.bytes > 4 * 1024 * 1024)) {
        auto old = values.find(order.back());
        if (old != values.end()) { bytes -= old->second.bytes; SDL_DestroyTexture(old->second.texture); values.erase(old); }
        order.pop_back();
      }
      bytes += item.bytes;
      it = values.emplace(key, item).first;
    } else order.remove(key);
    order.push_front(key);
    SDL_Rect rect = {x, y, it->second.width, it->second.height};
    SDL_RenderCopy(RENDERER, it->second.texture, nullptr, &rect);
  }
};

static bool library_keyboard(const string& heading, const string& initial,
                             string& result, int max_chars = 160) {
  SwkbdConfig keyboard;
  Result rc = swkbdCreate(&keyboard, 0);
  if (R_FAILED(rc)) { Log_Error("library keyboard create failed rc=" + std::to_string(rc)); return false; }
  swkbdConfigSetType(&keyboard, SwkbdType_Normal);
  swkbdConfigSetHeaderText(&keyboard, heading.c_str());
  swkbdConfigSetStringLenMax(&keyboard, max_chars);
  swkbdConfigSetStringLenMin(&keyboard, 0);
  swkbdConfigSetBlurBackground(&keyboard, 1);
  swkbdConfigSetInitialText(&keyboard, initial.c_str());
  swkbdConfigSetInitialCursorPos(&keyboard, 1);
  vector<char> buffer(static_cast<size_t>(max_chars) * 4 + 1, 0);
  rc = swkbdShow(&keyboard, buffer.data(), buffer.size());
  swkbdClose(&keyboard);
  if (R_FAILED(rc)) return false;
  result = buffer.data();
  return true;
}
// END WOOK_LIBRARY_HELPERS
'''


BODY = r'''
  float zoom_amount = 0.3f * zoom_option;
  int scroll_speed = 3 * scroll_option;
  wook::initialize();
  load_recent();
  LibraryTextCache library_text;
  bool library_visual_dirty = true;
  bool library_modal_open = false;
  bool suppress_input = false;
  bool should_leave = false;
  LibraryView view;
  vector<LibraryView> back_stack;
  vector<LibraryEntry> entries;
  vector<string> catalog;
  vector<wook::Collection> known_collections;
  std::set<string> scan_roots = {"/books", "/switch/WookReader/books"};
  bool catalog_dirty = true;
  int chosen_index = 0, numFolders = 0, scroll_y = 0;
  int windowX = 1280, windowY = 720;
  SDL_GetWindowSize(WINDOW, &windowX, &windowY);
  const int library_header_h = 88, library_footer_h = 116;
  int cols = 5, cell_w = windowX / cols, thumb_w = cell_w - 2;
  int thumb_h = static_cast<int>(thumb_w * (3000.0f / 1988.0f)), cell_h = thumb_h;
  vector<uint8_t> cover_failed;
  static const int N_WORKERS = 2;
  CoverLoader cl[N_WORKERS];
  int cl_in_flight[N_WORKERS] = {-1, -1};
  LibraryModal modal = LibraryModal::None;
  string modal_title, modal_text, note_path;
  vector<string> modal_lines;
  vector<LibraryAction> actions;
  int modal_index = 0, modal_scroll = 0;
  std::function<void()> confirm_action;
  string picker_collection;
  vector<string> picker_paths;
  std::set<string> picker_checked;
  bool picker_remove = false;
  string notice;
  string seen_state_error;
  int touch_prev_count = 0;
  float touch_start_x = 0, touch_start_y = 0, touch_prev_y = 0;
  bool touch_dragging = false;

  auto selected = [&]() -> LibraryEntry {
    return chosen_index >= 0 && chosen_index < static_cast<int>(entries.size()) ? entries[chosen_index] : LibraryEntry{};
  };
  auto mark_stale = [&]() {
    for (int w = 0; w < N_WORKERS; ++w)
      if (cl_in_flight[w] >= 0) __atomic_store_n(&cl[w].stale, 1, __ATOMIC_RELEASE);
  };
  auto usable_height = [&]() { return std::max(1, windowY - library_header_h - library_footer_h); };
  auto clamp_scroll = [&]() {
    const int cards = std::max(0, static_cast<int>(entries.size()) - numFolders);
    const int height = numFolders * FOLDER_H + ((cards + cols - 1) / cols) * cell_h;
    scroll_y = std::clamp(scroll_y, 0, std::max(0, height - usable_height()));
  };
  auto update_scroll = [&]() {
    if (entries.empty()) { scroll_y = 0; return; }
    const int top = chosen_index < numFolders ? chosen_index * FOLDER_H :
      numFolders * FOLDER_H + ((chosen_index - numFolders) / cols) * cell_h;
    const int height = chosen_index < numFolders ? FOLDER_H : cell_h;
    if (height > usable_height()) scroll_y = top;
    else if (top < scroll_y) scroll_y = top;
    else if (top + height > scroll_y + usable_height()) scroll_y = top + height - usable_height();
    clamp_scroll();
  };
  auto recalc_grid = [&]() {
    cell_w = windowX / cols; thumb_w = cell_w - 2;
    thumb_h = static_cast<int>(thumb_w * (3000.0f / 1988.0f)); cell_h = thumb_h;
    mark_stale(); cover_failed.assign(entries.size(), 0);
    library_text.clear(); library_visual_dirty = true; update_scroll();
  };
  auto close_modal = [&]() {
    if (modal == LibraryModal::Options)
      save_config(chosenFolderColor, chosenBookColor, scroll_option, zoom_option, configDarkMode);
    modal = LibraryModal::None; library_modal_open = false; library_visual_dirty = true;
    modal_scroll = modal_index = 0; touch_prev_count = 0;
  };
  auto show_text = [&](const string& title, const string& text) {
    modal = LibraryModal::Text; modal_title = title; modal_text = text;
    modal_lines = library_wrap(ROBOTO_20, text, windowX - 180);
    modal_index = modal_scroll = 0; library_modal_open = true; library_visual_dirty = true;
  };
  auto save_state = [&]() {
    library_visual_dirty = true;
    if (!wook::flush()) { show_text("Не удалось сохранить", wook::last_error()); return false; }
    return true;
  };
  auto get_collection = [&](const string& id, wook::Collection& out) {
    if (wook::collection(id, out)) return true;
    for (const auto& c : known_collections) if (c.id == id) { out = c; return true; }
    return false;
  };
  auto ensure_catalog = [&]() {
    if (!catalog_dirty) return;
    if (view.kind == LibraryViewKind::Directory) scan_roots.insert(view.path);
    for (const auto& path : g_recent) scan_roots.insert(library_parent(path));
    for (const auto& c : wook::collections()) for (const auto& path : c.books) scan_roots.insert(library_parent(path));
    std::set<string> found;
    // Index names only: no archive extraction, cover decoding or full SD scan.
    for (const auto& root : scan_roots) library_scan_root(root, found);
    for (const auto& c : wook::collections()) for (const auto& path : c.books)
      if (library_exists(path)) found.insert(path);
    catalog.assign(found.begin(), found.end());
    std::sort(catalog.begin(), catalog.end(), [](const string& a, const string& b) {
      const string ta = wook::title(a), tb = wook::title(b);
      return ta == tb ? wook::natural_less(a, b) : wook::natural_less(ta, tb);
    });
    catalog_dirty = false;
  };
  std::function<void(bool)> rebuild;
  rebuild = [&](bool preserve) {
    const string old_key = preserve ? selected().key : view.selected_key;
    const int old_scroll = preserve ? scroll_y : view.scroll;
    mark_stale();
    entries.clear(); numFolders = 0;
    if (view.kind == LibraryViewKind::Directory && scan_roots.insert(view.path).second) catalog_dirty = true;
    ensure_catalog();
    known_collections = wook::collections();
    if (wook::group_series()) {
      for (const auto& automatic : wook::automatic_collections(catalog)) {
        bool seen = false;
        for (const auto& c : known_collections) if (c.id == automatic.id) { seen = true; break; }
        if (!seen) known_collections.push_back(automatic);
      }
    }
    std::sort(known_collections.begin(), known_collections.end(), [](const auto& a, const auto& b) {
      return wook::natural_less(a.name, b.name);
    });
    auto add_book = [&](const string& path, const string& collection_id = string()) {
      if (!library_accepts(path, view)) return;
      LibraryEntry entry;
      entry.kind = LibraryEntryKind::Book; entry.path = path; entry.cover_path = path;
      entry.key = "book:" + path; entry.name = wook::title(path); entry.collection_id = collection_id;
      entry.state = wook::book_state(path); entry.note = chooser_note_exists(path);
      entry.missing = !library_exists(path); entries.push_back(entry);
    };
    auto add_series = [&](const wook::Collection& c) {
      if (!view.query.empty() && !wook::matches_query(c.name, view.query)) return;
      bool match = !view.favorites && !view.unread;
      for (const auto& path : c.books) if (library_accepts(path, view)) { match = true; break; }
      if (!match) return;
      LibraryEntry entry;
      entry.kind = LibraryEntryKind::Series; entry.key = "collection:" + c.id;
      entry.name = c.name; entry.collection_id = c.id; entry.count = c.books.size();
      entry.completed = wook::completed_books(c); entry.cover_path = c.cover;
      if (entry.cover_path.empty() && !c.books.empty()) entry.cover_path = c.books.front();
      entry.missing = !entry.cover_path.empty() && !library_exists(entry.cover_path);
      entries.push_back(entry);
    };
    if (view.kind == LibraryViewKind::Directory) {
      auto link = [&](const string& name, LibraryViewKind kind) {
        LibraryEntry e; e.kind = LibraryEntryKind::Link; e.name = name;
        e.key = "link:" + name; e.link = kind; entries.push_back(e); ++numFolders;
      };
      if (view.path == "/switch/WookReader") {
        link("Все комиксы", LibraryViewKind::All);
        link("Коллекции и серии", LibraryViewKind::Collections);
        link("Недавние", LibraryViewKind::Recent);
      }
      std::error_code ec;
      vector<fs::path> dirs, books;
      fs::directory_iterator it(view.path, fs::directory_options::skip_permission_denied, ec), end;
      for (; !ec && it != end; it.increment(ec)) {
        std::error_code entry_ec;
        if (it->is_directory(entry_ec) && !library_skip_directory(it->path())) dirs.push_back(it->path());
        else if (it->is_regular_file(entry_ec) && library_is_book(it->path().string())) books.push_back(it->path());
      }
      auto natural = [](const fs::path& a, const fs::path& b) { return wook::natural_less(a.filename().string(), b.filename().string()); };
      std::sort(dirs.begin(), dirs.end(), natural); std::sort(books.begin(), books.end(), natural);
      for (const auto& dir : dirs) {
        LibraryEntry e; e.kind = LibraryEntryKind::Folder; e.path = dir.string();
        e.name = dir.filename().string(); e.key = "dir:" + e.path; entries.push_back(e); ++numFolders;
      }
      vector<string> local_paths;
      for (const auto& book : books) local_paths.push_back(book.string());
      std::set<string> grouped;
      if (wook::group_series() && !view.favorites && !view.unread && view.query.empty()) {
        for (const auto& c : wook::automatic_collections(local_paths)) {
          wook::Collection actual = c; get_collection(c.id, actual);
          add_series(actual);
          for (const auto& path : actual.books) grouped.insert(path);
        }
      }
      for (const auto& book : books) if (!grouped.count(book.string())) add_book(book.string());
      if (ec) notice = "Не удалось полностью прочитать папку: " + view.path;
    } else if (view.kind == LibraryViewKind::Collections) {
      for (const auto& c : known_collections) add_series(c);
    } else if (view.kind == LibraryViewKind::Collection) {
      wook::Collection c;
      if (get_collection(view.collection_id, c)) for (const auto& path : c.books) add_book(path, c.id);
    } else if (view.kind == LibraryViewKind::Recent) {
      for (const auto& path : g_recent) if (library_exists(path)) add_book(path);
    } else {
      for (const auto& path : catalog) add_book(path);
    }
    chosen_index = 0;
    for (size_t i = 0; i < entries.size(); ++i) if (entries[i].key == old_key) { chosen_index = i; break; }
    scroll_y = old_scroll; clamp_scroll();
    cover_failed.assign(entries.size(), 0);
    library_visual_dirty = true;
  };
  auto remember_view = [&]() { view.selected_key = selected().key; view.scroll = scroll_y; };
  auto navigate = [&](LibraryView next) {
    remember_view(); back_stack.push_back(view); view = next;
    close_modal(); rebuild(false); update_scroll();
  };
  auto go_back = [&]() {
    if (!back_stack.empty()) { view = back_stack.back(); back_stack.pop_back(); rebuild(false); }
    else if (view.kind == LibraryViewKind::Directory && view.path != "/switch/WookReader") {
      view.path = library_parent(view.path); view.selected_key.clear(); view.scroll = 0; rebuild(false);
    } else if (save_state()) should_leave = true;
    library_visual_dirty = true;
  };
  auto keyboard = [&](const string& title, const string& initial, string& result, int max_chars = 160) {
    mark_stale(); bool accepted = library_keyboard(title, initial, result, max_chars);
    suppress_input = true; touch_prev_count = 0; library_visual_dirty = true;
    return accepted;
  };
  std::function<void(const string&, const string&, bool)> open_book;
  open_book = [&](const string& path, const string& collection_id, bool confirmed) {
    if (!library_exists(path)) { show_text("Файл недоступен", path + "\n\nПроверьте SD-карту и исходную папку комикса."); return; }
    const string ext = library_extension(path);
    if (!confirmed && (ext == ".epub" || ext == ".xps")) {
      modal = LibraryModal::Confirm; modal_title = "Открыть документ?";
      modal_text = "Этот формат может отображаться не полностью. A — открыть, B — отмена.";
      modal_lines = library_wrap(ROBOTO_20, modal_text, windowX - 180);
      confirm_action = [&, path, collection_id]() { open_book(path, collection_id, true); };
      modal_scroll = 0; library_modal_open = true; library_visual_dirty = true; return;
    }
    wook::Collection c;
    if (!collection_id.empty() && get_collection(collection_id, c)) wook::set_reading_sequence(c.books, c.name);
    else wook::clear_reading_sequence();
    mark_stale(); close_modal(); update_recent(path);
    Menu_OpenBook(const_cast<char*>(path.c_str()), scroll_speed, zoom_amount);
    suppress_input = true; touch_prev_count = 0; rebuild(true);
    if (!wook::last_error().empty()) show_text("Не удалось сохранить", wook::last_error());
  };
  auto open_selected = [&]() {
    if (entries.empty()) return;
    const LibraryEntry e = selected();
    if (e.kind == LibraryEntryKind::Folder) {
      LibraryView next; next.path = e.path; navigate(next);
    } else if (e.kind == LibraryEntryKind::Link) {
      LibraryView next; next.kind = e.link; navigate(next);
    } else if (e.kind == LibraryEntryKind::Series) {
      LibraryView next; next.kind = LibraryViewKind::Collection; next.collection_id = e.collection_id; navigate(next);
    } else open_book(e.path, e.collection_id, false);
  };
  auto continue_collection = [&](const string& id) {
    wook::Collection c;
    if (!get_collection(id, c)) return;
    const string path = wook::continue_book(c);
    if (path.empty()) { show_text("Коллекция пуста", "Добавьте комиксы через меню X."); return; }
    open_book(path, id, false);
  };
  auto search = [&]() {
    string query;
    if (keyboard("Поиск по названиям", view.query, query)) {
      LibraryView next; next.kind = LibraryViewKind::Search; next.query = query;
      next.favorites = view.favorites; next.unread = view.unread; navigate(next);
    }
  };
  auto show_notes = [&](const string& path) {
    if (path.empty()) return;
    note_path = path; modal_text = chooser_note_read(path);
    modal_title = "Заметка: " + wook::title(path);
    modal_lines = library_wrap(ROBOTO_20, modal_text.empty() ? "Заметок нет. Нажмите A для добавления." : modal_text, windowX - 180);
    modal = LibraryModal::Notes; modal_scroll = 0; library_modal_open = true; library_visual_dirty = true;
  };
  auto show_actions = [&](const string& title, vector<LibraryAction> items) {
    modal = LibraryModal::Actions; modal_title = title; actions = std::move(items);
    modal_index = modal_scroll = 0; library_modal_open = true; library_visual_dirty = true;
  };
  auto persist_collection = [&](wook::Collection c) {
    if (!wook::update_collection(c)) { show_text("Коллекция", "Не удалось изменить коллекцию."); return false; }
    if (!save_state()) return false;
    rebuild(true); return true;
  };
  auto open_picker = [&](const string& id, bool remove_items) {
    wook::Collection c;
    if (!get_collection(id, c)) return;
    ensure_catalog(); picker_collection = id; picker_remove = remove_items;
    picker_paths.clear(); picker_checked.clear();
    if (remove_items) picker_paths = c.books;
    else for (const auto& path : catalog)
      if (std::find(c.books.begin(), c.books.end(), path) == c.books.end()) picker_paths.push_back(path);
    modal = LibraryModal::Picker;
    modal_title = remove_items ? "Убрать из коллекции" : "Добавить в коллекцию";
    modal_index = modal_scroll = 0; library_modal_open = true; library_visual_dirty = true;
  };
  auto apply_picker = [&]() {
    wook::Collection c;
    if (!get_collection(picker_collection, c)) { close_modal(); return; }
    if (picker_remove) {
      c.books.erase(std::remove_if(c.books.begin(), c.books.end(), [&](const string& path) { return picker_checked.count(path) != 0; }), c.books.end());
      if (picker_checked.count(c.cover)) c.cover.clear();
    } else for (const auto& path : picker_paths) if (picker_checked.count(path)) c.books.push_back(path);
    close_modal(); persist_collection(c);
  };
  auto create_collection = [&](const vector<string>& initial) {
    string name;
    if (!keyboard("Название коллекции", "", name) || name.find_first_not_of(" \t\r\n") == string::npos) return;
    const string id = wook::create_collection(name, initial);
    if (id.empty()) { show_text("Коллекция", "Не удалось создать коллекцию."); return; }
    if (!save_state()) return;
    catalog_dirty = true; rebuild(true); open_picker(id, false);
  };
  auto retry_cover = [&](const string& path) {
    if (path.empty()) return;
    mark_stale();
    auto cached = g_cover_cache.entries.find(path);
    if (cached != g_cover_cache.entries.end()) {
      SDL_DestroyTexture(cached->second.first); g_cover_cache.order.erase(cached->second.second);
      g_cover_cache.entries.erase(cached);
    }
    char cache_name[32] = {};
    snprintf(cache_name, sizeof(cache_name), "%016llx", static_cast<unsigned long long>(comic_cache_key(path.c_str())));
    const string cache = string(PAGECACHE_DIR) + "/" + cache_name;
    std::remove((cache + ".cvr").c_str()); std::remove((cache + ".cvrn").c_str());
    for (size_t i = 0; i < entries.size(); ++i) if (entries[i].cover_path == path) cover_failed[i] = 0;
    close_modal(); library_visual_dirty = true;
  };
  auto show_help = [&]() {
    show_text("Управление библиотекой",
      "A — открыть комикс, папку или коллекцию. B — назад.\n"
      "Крестовина — выбор. ZL/ZR — от 8 до 2 колонок. Сенсор: прокрутка и касание для открытия.\n"
      "X — действия, поиск, коллекции, избранное и фильтры. R — продолжить выбранную коллекцию.\n"
      "Y — заметка выбранного комикса. В заметке A — изменить; вверх/вниз — прокрутка; L/R — страница текста. B/Y — закрыть.\n"
      "+ — настройки; − — светлая/тёмная тема. В меню A — выбрать, B — закрыть.\n"
      "В выборе выпусков A ставит отметку; X применяет изменения; B отменяет. Файлы остаются в исходных папках.\n"
      "Автосерии объединяют выпуски с одинаковым названием и номером в одной папке. Их название, порядок, состав и обложку можно менять через X.\n"
      "Все комиксы и поиск включают /books, /switch/WookReader/books, открытые папки и папки недавних/сохранённых коллекций. Для добавления другой папки откройте её и выберите X, пункт «Обновить библиотеку».\n"
      "Загрузка... означает ожидание обложки. Обложка недоступна — попытка завершилась без изображения; X, пункт «Обновить обложку» повторяет её.\n"
      "Экономия батареи включена по умолчанию. При неподвижном экране библиотека ждёт ввод или готовую обложку.");
  };
  std::function<void()> show_options;
  show_options = [&]() {
    const string on = "Вкл.", off = "Выкл.";
    vector<LibraryAction> items;
    auto toggle = [&](const string& name, bool value, std::function<void()> change) {
      items.push_back({name + ": " + (value ? on : off), [&, change]() { change(); if (save_state()) { rebuild(true); show_options(); } }});
    };
    toggle("Экономия батареи", wook::battery_saver(), [&]() { wook::set_battery_saver(!wook::battery_saver()); });
    toggle("Сохранять масштаб", wook::keep_zoom(), [&]() { wook::set_keep_zoom(!wook::keep_zoom()); });
    toggle("Сохранять положение", wook::keep_position(), [&]() { wook::set_keep_position(!wook::keep_position()); });
    toggle("Группировать серии", wook::group_series(), [&]() { wook::set_group_series(!wook::group_series()); });
    items.push_back({"Цвет папок: " + string(colorNamesRU[chosenFolderColor]), [&]() { chosenFolderColor = (chosenFolderColor + 1) % (COLORS_SIZE + 1); folder_image = folder_textures[chosenFolderColor]; show_options(); }});
    items.push_back({"Цвет книг: " + string(colorNamesRU[chosenBookColor]), [&]() { chosenBookColor = (chosenBookColor + 1) % (COLORS_SIZE + 1); book_image = book_textures[chosenBookColor]; show_options(); }});
    items.push_back({"Прокрутка: " + std::to_string(scroll_option), [&]() { scroll_option = scroll_option % 4 + 1; scroll_speed = SCROLL * scroll_option; show_options(); }});
    items.push_back({"Шаг масштаба: " + std::to_string(zoom_option), [&]() { zoom_option = zoom_option % 4 + 1; zoom_amount = ZOOM * zoom_option; show_options(); }});
    items.push_back({"Кнопки на экране: " + string(configScreenButtons ? on : off), [&]() { configScreenButtons = !configScreenButtons; show_options(); }});
    items.push_back({"Строка состояния: " + string(configStatusBar ? on : off), [&]() { configStatusBar = !configStatusBar; show_options(); }});
    items.push_back({"Тема: " + string(configDarkMode ? "Тёмная" : "Светлая"), [&]() { configDarkMode = !configDarkMode; show_options(); }});
    const int old = modal == LibraryModal::Options ? modal_index : 0;
    show_actions("Настройки", items); modal = LibraryModal::Options; modal_index = old;
  };
  std::function<void()> context_menu;
  context_menu = [&]() {
    const LibraryEntry e = selected();
    vector<LibraryAction> items;
    string collection_id = e.kind == LibraryEntryKind::Series ? e.collection_id :
      (view.kind == LibraryViewKind::Collection ? view.collection_id : string());
    if (!e.key.empty() && e.kind == LibraryEntryKind::Book) {
      items.push_back({"Открыть комикс", [&, e]() { open_book(e.path, e.collection_id, false); }});
      items.push_back({e.state.favorite ? "Убрать из избранного" : "В избранное", [&, e]() { wook::set_favorite(e.path, !e.state.favorite); if (save_state()) { close_modal(); rebuild(true); } }});
      items.push_back({e.state.finished ? "Отметить непрочитанным" : "Отметить прочитанным", [&, e]() { wook::set_finished(e.path, !e.state.finished); if (save_state()) { close_modal(); rebuild(true); } }});
      items.push_back({"Заметка", [&, e]() { show_notes(e.path); }});
      items.push_back({"Добавить в коллекцию...", [&, e]() {
        vector<LibraryAction> choices;
        choices.push_back({"Создать коллекцию", [&, e]() { create_collection({e.path}); }});
        for (auto c : known_collections) choices.push_back({c.name, [&, c, e]() mutable {
          if (std::find(c.books.begin(), c.books.end(), e.path) == c.books.end()) c.books.push_back(e.path);
          close_modal(); persist_collection(c);
        }});
        show_actions("Выберите коллекцию", choices);
      }});
    }
    wook::Collection c;
    if (!collection_id.empty() && get_collection(collection_id, c)) {
      items.push_back({"Продолжить коллекцию", [&, c]() { continue_collection(c.id); }});
      items.push_back({"Добавить выпуски...", [&, c]() { open_picker(c.id, false); }});
      items.push_back({"Убрать выпуски...", [&, c]() { open_picker(c.id, true); }});
      items.push_back({"Переименовать коллекцию", [&, c]() mutable { string name; if (keyboard("Название коллекции", c.name, name) && name.find_first_not_of(" \t\r\n") != string::npos) { c.name = name; close_modal(); persist_collection(c); } }});
      items.push_back({"Порядок по номерам", [&, c]() mutable { std::stable_sort(c.books.begin(), c.books.end(), wook::natural_less); close_modal(); persist_collection(c); }});
      if (e.kind == LibraryEntryKind::Book && e.collection_id == c.id) {
        items.push_back({"Выпуск раньше", [&, c, e]() mutable { auto i = std::find(c.books.begin(), c.books.end(), e.path); if (i != c.books.end() && i != c.books.begin()) std::iter_swap(i, i - 1); close_modal(); persist_collection(c); }});
        items.push_back({"Выпуск позже", [&, c, e]() mutable { auto i = std::find(c.books.begin(), c.books.end(), e.path); if (i != c.books.end() && i + 1 != c.books.end()) std::iter_swap(i, i + 1); close_modal(); persist_collection(c); }});
        items.push_back({"Сделать обложкой коллекции", [&, c, e]() mutable { c.cover = e.path; close_modal(); persist_collection(c); }});
      }
      items.push_back({c.id.rfind("auto_", 0) == 0 ? "Скрыть автосерию..." : "Удалить коллекцию...", [&, c]() {
        const bool automatic = c.id.rfind("auto_", 0) == 0;
        modal = LibraryModal::Confirm; modal_title = automatic ? "Скрыть автосерию?" : "Удалить коллекцию?";
        modal_lines = library_wrap(ROBOTO_20, "Коллекция «" + c.name + "» будет удалена из списка. " +
          (automatic ? string("Она больше не будет появляться автоматически. ") : string()) +
          "Исходные файлы комиксов останутся на SD-карте. A — подтвердить, B — отмена.", windowX - 180);
        confirm_action = [&, c]() { wook::remove_collection(c.id); if (save_state()) { close_modal(); if (view.kind == LibraryViewKind::Collection) go_back(); else rebuild(true); } };
        modal_scroll = 0; library_modal_open = true; library_visual_dirty = true;
      }});
    }
    if (!e.cover_path.empty()) items.push_back({"Обновить обложку", [&, e]() { retry_cover(e.cover_path); }});
    if (!e.key.empty()) items.push_back({"Полное название и сведения", [&, e]() {
      string info = e.name;
      if (e.kind == LibraryEntryKind::Book) info += "\n\n" + library_progress(e.state) + (e.state.favorite ? " · Избранное" : "") + "\n\n" + e.path;
      else if (e.kind == LibraryEntryKind::Series) info += "\n\nВыпусков: " + std::to_string(e.count) + "; прочитано: " + std::to_string(e.completed) + "\n\nX — состав, порядок и название; откройте выпуск и выберите X, чтобы назначить обложку.";
      else info += "\n\n" + e.path;
      show_text("Сведения", info);
    }});
    items.push_back({"Поиск по всем комиксам...", [&]() { search(); }});
    items.push_back({"Все комиксы", [&]() { LibraryView next; next.kind = LibraryViewKind::All; navigate(next); }});
    items.push_back({"Открыть SD-карту", [&]() { LibraryView next; next.path = "/"; navigate(next); }});
    items.push_back({"Коллекции и серии", [&]() { LibraryView next; next.kind = LibraryViewKind::Collections; navigate(next); }});
    items.push_back({"Создать коллекцию...", [&]() { create_collection({}); }});
    items.push_back({view.favorites ? "Показать не только избранное" : "Только избранное", [&]() { close_modal(); if (view.kind == LibraryViewKind::Directory || view.kind == LibraryViewKind::Collections) { LibraryView next; next.kind = LibraryViewKind::All; next.favorites = true; navigate(next); } else { view.favorites = !view.favorites; rebuild(true); } }});
    items.push_back({view.unread ? "Показать также прочитанное" : "Только непрочитанное", [&]() { close_modal(); if (view.kind == LibraryViewKind::Directory || view.kind == LibraryViewKind::Collections) { LibraryView next; next.kind = LibraryViewKind::All; next.unread = true; navigate(next); } else { view.unread = !view.unread; rebuild(true); } }});
    if (!view.query.empty() || view.favorites || view.unread) items.push_back({"Сбросить поиск и фильтры", [&]() { view.query.clear(); view.favorites = view.unread = false; close_modal(); rebuild(true); }});
    items.push_back({"Обновить библиотеку", [&]() { catalog_dirty = true; close_modal(); rebuild(true); }});
    items.push_back({"Настройки", [&]() { show_options(); }});
    items.push_back({"Управление и помощь", [&]() { show_help(); }});
    show_actions("Действия", items);
  };

  rebuild(false);
  if (!wook::last_error().empty()) show_text("Библиотека", wook::last_error());
  PadState pad;
  padConfigureInput(1, HidNpadStyleSet_NpadStandard);
  padInitializeDefault(&pad);
  hidInitializeTouchScreen();
  // Start background cover loaders on cores 1 and 2
  for (int w = 0; w < N_WORKERS; ++w) {
    Result rc = threadCreate(&cl[w].thread, cover_loader_worker, &cl[w], nullptr, 512 * 1024, 0x2c, w + 1);
    if (R_SUCCEEDED(rc)) {
      rc = threadStart(&cl[w].thread);
      if (R_SUCCEEDED(rc)) cl[w].active = true;
      else { threadClose(&cl[w].thread); Log_Error("cover worker: threadStart failed slot=" + std::to_string(w) + " rc=" + std::to_string(rc)); }
    } else Log_Error("cover worker: threadCreate failed slot=" + std::to_string(w) + " rc=" + std::to_string(rc));
  }

  // Main loop: modal input is exclusive. Closing a modal never opens a book.
  while (appletMainLoop() && !should_leave) {
    SDL_PumpEvents();
    padUpdate(&pad);
    u64 kDown = padGetButtonsDown(&pad);
    u64 kUp = padGetButtonsUp(&pad);
    SDL_Event library_event;
    bool keyboard_search_requested = false;
    string keyboard_text;
    while (SDL_PollEvent(&library_event)) {
      if (library_event.type == SDL_QUIT) should_leave = true;
      if (library_event.type == SDL_WINDOWEVENT || library_event.type == SDL_APP_DIDENTERFOREGROUND)
        library_visual_dirty = true;
      if (library_event.type == SDL_RENDER_TARGETS_RESET || library_event.type == SDL_RENDER_DEVICE_RESET) {
        library_text.clear(); g_cover_cache.clear(); cover_failed.assign(entries.size(), 0);
        library_visual_dirty = true;
      }
      if (library_event.type == SDL_TEXTINPUT && modal == LibraryModal::None)
        keyboard_text += library_event.text.text;
      if (library_event.type == SDL_KEYDOWN && !library_event.key.repeat) {
        library_visual_dirty = true;
        switch (library_event.key.keysym.sym) {
          case SDLK_UP: kDown |= HidNpadButton_Up; break;
          case SDLK_DOWN: kDown |= HidNpadButton_Down; break;
          case SDLK_LEFT: kDown |= HidNpadButton_Left; break;
          case SDLK_RIGHT: kDown |= HidNpadButton_Right; break;
          case SDLK_RETURN: kDown |= HidNpadButton_A; break;
          case SDLK_ESCAPE: kDown |= HidNpadButton_B; break;
          case SDLK_F1: if (modal == LibraryModal::None) show_help(); break;
          case SDLK_F2: kDown |= HidNpadButton_X; break;
          case SDLK_F3: keyboard_search_requested = modal == LibraryModal::None; break;
          case SDLK_BACKSPACE:
            if (modal == LibraryModal::None && view.kind == LibraryViewKind::Search) { wook::pop_utf8(view.query); rebuild(true); }
            break;
          default: break;
        }
      }
    }
    if (!suppress_input && modal == LibraryModal::None) {
      if (keyboard_search_requested) { search(); kDown = 0; }
      else if (!keyboard_text.empty()) {
        if (view.kind == LibraryViewKind::Search) { view.query += keyboard_text; rebuild(true); }
        else { LibraryView next; next.kind = LibraryViewKind::Search; next.query = keyboard_text; navigate(next); }
      }
    }
    HidTouchScreenState ts = {};
    hidGetTouchScreenStates(&ts, 1);
    if (suppress_input) {
      if (!padGetButtons(&pad) && ts.count == 0) suppress_input = false;
      kDown = kUp = 0; ts.count = 0; touch_prev_count = 0;
    }
    if (kDown || kUp || ts.count || touch_prev_count) library_visual_dirty = true;
    const bool modal_at_frame_start = modal != LibraryModal::None;
    const int modal_rows = std::max(1, (windowY - 240) / 38);
    const int text_clip_bottom = windowY - 130;
    const int text_rows = std::max(1, 1 + (text_clip_bottom - 140 - TTF_FontHeight(ROBOTO_20)) /
                                      (TTF_FontLineSkip(ROBOTO_20) + 3));
    const u64 up = HidNpadButton_Up | HidNpadButton_StickRUp;
    const u64 down = HidNpadButton_Down | HidNpadButton_StickRDown;
    if (modal_at_frame_start) {
      if (kDown & HidNpadButton_B || (modal == LibraryModal::Notes && (kDown & HidNpadButton_Y)) ||
          (modal == LibraryModal::Options && (kDown & HidNpadButton_Plus))) {
        close_modal();
      } else if (modal == LibraryModal::Actions || modal == LibraryModal::Options || modal == LibraryModal::Picker) {
        const int size = modal == LibraryModal::Picker ? picker_paths.size() : actions.size();
        if (size > 0) {
          if (kDown & up) modal_index = (modal_index + size - 1) % size;
          if (kDown & down) modal_index = (modal_index + 1) % size;
          if (kDown & HidNpadButton_L) modal_index = std::max(0, modal_index - modal_rows);
          if (kDown & HidNpadButton_R) modal_index = std::min(size - 1, modal_index + modal_rows);
          modal_scroll = std::clamp(modal_scroll, std::max(0, modal_index - modal_rows + 1), modal_index);
          if (kDown & HidNpadButton_A) {
            if (modal == LibraryModal::Picker) {
              const string p = picker_paths[modal_index];
              if (picker_checked.count(p)) picker_checked.erase(p); else picker_checked.insert(p);
            } else { const auto run = actions[modal_index].run; run(); }
          }
        }
        if (modal == LibraryModal::Picker && (kDown & HidNpadButton_X)) apply_picker();
      } else {
        if (kDown & up) --modal_scroll;
        if (kDown & down) ++modal_scroll;
        if (kDown & HidNpadButton_L) modal_scroll -= text_rows;
        if (kDown & HidNpadButton_R) modal_scroll += text_rows;
        modal_scroll = std::clamp(modal_scroll, 0, std::max(0, static_cast<int>(modal_lines.size()) - text_rows));
        if (modal == LibraryModal::Notes && (kDown & HidNpadButton_A)) {
          string edited;
          if (keyboard("Изменить заметку", modal_text, edited, 2000)) {
            chooser_note_save(note_path, edited);
            const string read_back = chooser_note_read(note_path);
            if (read_back != edited) show_text("Не удалось сохранить заметку", "Проверьте свободное место и доступность SD-карты.");
            else { rebuild(true); show_notes(note_path); }
          }
        } else if (modal == LibraryModal::Confirm && (kDown & HidNpadButton_A)) {
          const auto action = confirm_action; close_modal(); if (action) action();
        }
      }
    } else {
      if (kDown & HidNpadButton_B) go_back();
      else if (kDown & HidNpadButton_Plus) show_options();
      else if (kDown & HidNpadButton_X) context_menu();
      else if (kDown & HidNpadButton_Y) { const auto e = selected(); if (e.kind == LibraryEntryKind::Book) show_notes(e.path); }
      else if (kDown & HidNpadButton_A) open_selected();
      else if (kDown & HidNpadButton_R) {
        const auto e = selected();
        if (e.kind == LibraryEntryKind::Series) continue_collection(e.collection_id);
        else if (view.kind == LibraryViewKind::Collection) continue_collection(view.collection_id);
      } else if (kDown & HidNpadButton_Minus) {
        configDarkMode = !configDarkMode;
        save_config(chosenFolderColor, chosenBookColor, scroll_option, zoom_option, configDarkMode);
      } else if ((kDown & HidNpadButton_ZR && cols > 2) || (kDown & HidNpadButton_ZL && cols < 8)) {
        cols += kDown & HidNpadButton_ZR ? -1 : 1; recalc_grid();
      } else if (!entries.empty()) {
        const int size = entries.size();
        if (kDown & up) {
          if (chosen_index < numFolders) chosen_index = chosen_index > 0 ? chosen_index - 1 : size - 1;
          else chosen_index = chosen_index - numFolders >= cols ? chosen_index - cols : (numFolders ? numFolders - 1 : size - 1);
          update_scroll();
        }
        if (kDown & down) {
          if (chosen_index < numFolders) chosen_index = std::min(chosen_index + 1, size - 1);
          else chosen_index = chosen_index + cols < size ? chosen_index + cols : 0;
          update_scroll();
        }
        if (kDown & (HidNpadButton_Left | HidNpadButton_StickRLeft)) { chosen_index = (chosen_index + size - 1) % size; update_scroll(); }
        if (kDown & (HidNpadButton_Right | HidNpadButton_StickRRight)) { chosen_index = (chosen_index + 1) % size; update_scroll(); }
      }
    }

    // Touch gestures belong to the surface on which they started. A dismissing
    // touch/button is consumed and cannot activate the library behind a modal.
    if (modal_at_frame_start != (modal != LibraryModal::None) || suppress_input) {
      touch_prev_count = 0;
    } else if (ts.count == 1) {
      const float x = ts.touches[0].x, y = ts.touches[0].y;
      if (touch_prev_count == 0) { touch_start_x = x; touch_start_y = y; touch_dragging = false; }
      else {
        if (std::abs(x - touch_start_x) + std::abs(y - touch_start_y) > 18) touch_dragging = true;
        if (touch_dragging) {
          const int delta = static_cast<int>(std::round(touch_prev_y - y));
          if (modal == LibraryModal::None) { scroll_y += delta; clamp_scroll(); }
          else if (modal == LibraryModal::Notes || modal == LibraryModal::Text)
            modal_scroll = std::clamp(modal_scroll + (delta > 4 ? 1 : delta < -4 ? -1 : 0), 0, std::max(0, static_cast<int>(modal_lines.size()) - text_rows));
          else if (modal == LibraryModal::Actions || modal == LibraryModal::Options || modal == LibraryModal::Picker) {
            const int size = modal == LibraryModal::Picker ? picker_paths.size() : actions.size();
            modal_scroll = std::clamp(modal_scroll + (delta > 4 ? 1 : delta < -4 ? -1 : 0), 0, std::max(0, size - modal_rows));
          }
        }
      }
      touch_prev_y = y; touch_prev_count = 1;
    } else if (ts.count == 0 && touch_prev_count == 1) {
      touch_prev_count = 0;
      if (!touch_dragging) {
        if (modal != LibraryModal::None) {
          if (touch_start_y >= windowY - 130 && touch_start_y <= windowY - 75) {
            if (touch_start_x > windowX / 2) close_modal();
            else if (modal == LibraryModal::Picker) apply_picker();
            else if (modal == LibraryModal::Notes) {
              string edited;
              if (keyboard("Изменить заметку", modal_text, edited, 2000)) {
                chooser_note_save(note_path, edited);
                if (chooser_note_read(note_path) != edited) show_text("Не удалось сохранить заметку", "Проверьте SD-карту.");
                else { rebuild(true); show_notes(note_path); }
              }
            } else if (modal == LibraryModal::Confirm) { const auto action = confirm_action; close_modal(); if (action) action(); }
          } else if (touch_start_x >= 80 && touch_start_x < windowX - 80 && touch_start_y >= 140 &&
                     (modal == LibraryModal::Actions || modal == LibraryModal::Options || modal == LibraryModal::Picker)) {
            const int row = static_cast<int>((touch_start_y - 140) / 38) + modal_scroll;
            const int size = modal == LibraryModal::Picker ? picker_paths.size() : actions.size();
            if (row >= 0 && row < size && row < modal_scroll + modal_rows) {
              modal_index = row;
              if (modal == LibraryModal::Picker) { const string p = picker_paths[row]; if (picker_checked.count(p)) picker_checked.erase(p); else picker_checked.insert(p); }
              else { const auto action = actions[row].run; action(); }
            }
          }
        } else if (touch_start_y >= 44 && touch_start_y < library_header_h) {
          const int button = static_cast<int>(touch_start_x * 7 / windowX);
          if (button == 0) { LibraryView next; next.kind = LibraryViewKind::All; navigate(next); }
          else if (button == 1) { LibraryView next; next.kind = LibraryViewKind::Collections; navigate(next); }
          else if (button == 2) {
            if (view.kind == LibraryViewKind::Directory || view.kind == LibraryViewKind::Collections) { LibraryView next; next.kind = LibraryViewKind::All; next.favorites = true; navigate(next); }
            else { view.favorites = !view.favorites; rebuild(true); }
          }
          else if (button == 3) {
            if (view.kind == LibraryViewKind::Directory || view.kind == LibraryViewKind::Collections) { LibraryView next; next.kind = LibraryViewKind::All; next.unread = true; navigate(next); }
            else { view.unread = !view.unread; rebuild(true); }
          }
          else if (button == 4) search();
          else if (button == 5) context_menu();
          else show_options();
        } else if (touch_start_y >= library_header_h && touch_start_y < windowY - library_footer_h) {
          const int y = static_cast<int>(touch_start_y) - library_header_h + scroll_y;
          int index = y < numFolders * FOLDER_H ? y / FOLDER_H :
            numFolders + ((y - numFolders * FOLDER_H) / cell_h) * cols +
              std::clamp(static_cast<int>(touch_start_x) / cell_w, 0, cols - 1);
          if (index >= 0 && index < static_cast<int>(entries.size())) { chosen_index = index; open_selected(); }
        } else if (touch_start_y >= windowY - library_footer_h) {
          const auto e = selected(); if (!e.key.empty()) show_text("Полное название", e.name + (e.path.empty() ? "" : "\n\n" + e.path));
        }
      }
    } else if (ts.count != 1) touch_prev_count = ts.count;

    // ── Async cover loading ─────────────────────────────────────────────────
    for (int w = 0; w < N_WORKERS; ++w) {
      if (!__atomic_load_n(&cl[w].result_ready, __ATOMIC_ACQUIRE)) continue;
      const int idx = cl[w].result_index;
      if (__atomic_load_n(&cl[w].stale, __ATOMIC_ACQUIRE)) {
        free(cl[w].result_pixels); cl[w].result_pixels = nullptr;
        __atomic_store_n(&cl[w].result_ready, 0, __ATOMIC_RELEASE);
        __atomic_store_n(&cl[w].stale, 0, __ATOMIC_RELEASE); cl_in_flight[w] = -1;
        library_visual_dirty = true; continue;
      }
      SDL_Texture* tex = pixels_to_texture(cl[w].result_pixels, cl[w].result_iw, cl[w].result_ih, cl[w].job_path.c_str());
      cl[w].result_pixels = nullptr;
      if (idx >= 0 && idx < static_cast<int>(entries.size()) && entries[idx].cover_path == cl[w].job_path) {
        if (tex) g_cover_cache.put(entries[idx].cover_path, tex);
        for (size_t i = 0; i < entries.size(); ++i) if (entries[i].cover_path == cl[w].job_path) cover_failed[i] = tex ? 0 : 1;
      } else if (tex) SDL_DestroyTexture(tex);
      __atomic_store_n(&cl[w].result_ready, 0, __ATOMIC_RELEASE); cl_in_flight[w] = -1;
      library_visual_dirty = true;
    }
    const int books_y = numFolders * FOLDER_H;
    const int first_card = std::max(0, (std::max(0, scroll_y - books_y) / cell_h - 1) * cols);
    const int last_card = std::min(static_cast<int>(entries.size()) - numFolders,
      (std::max(0, scroll_y + usable_height() - books_y) / cell_h + 2) * cols);
    for (int w = 0; w < N_WORKERS; ++w) {
      if (!cl[w].active || cl_in_flight[w] >= 0) continue;
      for (int card = first_card; card < last_card; ++card) {
        const int i = numFolders + card;
        const string& path = entries[i].cover_path;
        if (path.empty() || cover_failed[i] || entries[i].missing) continue;
        bool busy = false;
        for (int w2 = 0; w2 < N_WORKERS; ++w2) if (cl_in_flight[w2] >= 0 && cl[w2].job_path == path) busy = true;
        if (busy) continue;
        SDL_Texture* cached = g_cover_cache.get(path);
        if (cached) { int cw = 0; SDL_QueryTexture(cached, nullptr, nullptr, &cw, nullptr); if (cw > 0 && cw * 6 >= thumb_w * 5) continue; }
        const string ext = library_extension(path);
        cl[w].job_path = path; cl[w].job_tw = thumb_w; cl[w].job_th = thumb_h;
        cl[w].job_index = i; cl[w].job_is_pdf = ext != ".cbz" && ext != ".cbr" && ext != ".cbt" && ext != ".cb7";
        cl_in_flight[w] = i;
        __atomic_store_n(&cl[w].stale, 0, __ATOMIC_RELEASE);
        __atomic_store_n(&cl[w].job_ready, 1, __ATOMIC_RELEASE);
        break;
      }
    }

    const string current_state_error = wook::last_error();
    if (current_state_error != seen_state_error) {
      seen_state_error = current_state_error;
      if (!current_state_error.empty()) show_text("Не удалось сохранить", current_state_error);
    }

    // ── Rendering ───────────────────────────────────────────────────────────
    // enhance_power.py inserts its idle gate here, before any SDL draw call.
    // WOOK_POWER_LIBRARY_DRAW
    SDL_TextCache_NextFrame();
    const SDL_Color textColor = configDarkMode ? WHITE : BLACK;
    const SDL_Color backColor = configDarkMode ? BACK_BLACK : BACK_WHITE;
    const SDL_Color selectorColor = configDarkMode ? SELECTOR_COLOUR_DARK : SELECTOR_COLOUR_LIGHT;
    const SDL_Color accent = {0, 175, 195, 255};
    SDL_RenderSetClipRect(RENDERER, nullptr);
    SDL_ClearScreen(RENDERER, backColor);
    SDL_RenderClear(RENDERER);
    string heading;
    if (view.kind == LibraryViewKind::Directory) heading = view.path;
    else if (view.kind == LibraryViewKind::All) heading = "Все комиксы";
    else if (view.kind == LibraryViewKind::Collections) heading = "Коллекции и серии";
    else if (view.kind == LibraryViewKind::Recent) heading = "Недавние";
    else if (view.kind == LibraryViewKind::Search) heading = "Поиск: " + view.query;
    else { wook::Collection c; heading = get_collection(view.collection_id, c) ? c.name : "Коллекция"; }
    if (view.favorites) heading += " · Избранное";
    if (view.unread) heading += " · Непрочитанное";
    library_text.draw(ROBOTO_25, 12, 7, textColor, library_fit_title(ROBOTO_25, heading, windowX - 200));
    // WOOK_POWER_LIBRARY_STATUS
    const time_t now = time(nullptr); struct tm local_time = {}; localtime_r(&now, &local_time);
    char clock_text[8] = {}; strftime(clock_text, sizeof(clock_text), "%H:%M", &local_time);
    library_text.draw(ROBOTO_20, windowX - 82, 10, textColor, clock_text);
    const char* header_labels[] = {"Все", "Коллекции", "Избранное", "Не прочитано", "Поиск", "X Действия", "+ Настройки"};
    for (int i = 0; i < 7; ++i) {
      const int x = i * windowX / 7, width = windowX / 7;
      if ((i == 2 && view.favorites) || (i == 3 && view.unread)) SDL_DrawRect(RENDERER, x + 2, 46, width - 4, 36, selectorColor);
      const string label = header_labels[i];
      library_text.draw(ROBOTO_20, x + (width - library_text_width(ROBOTO_20, label)) / 2, 51, i < 5 ? accent : textColor, label);
    }
    SDL_Rect content_clip = {0, library_header_h, windowX, usable_height()};
    SDL_RenderSetClipRect(RENDERER, &content_clip);
    for (int i = 0; i < numFolders; ++i) {
      const int y = library_header_h + i * FOLDER_H - scroll_y;
      if (y + FOLDER_H < library_header_h || y >= windowY - library_footer_h) continue;
      if (chosen_index == i) SDL_DrawRect(RENDERER, 0, y, windowX, FOLDER_H, selectorColor);
      SDL_DrawImageScale(RENDERER, folder_image, 8, y + 4, 32, 32);
      library_text.draw(ROBOTO_25, 48, y + 5, entries[i].kind == LibraryEntryKind::Link ? accent : textColor,
        library_fit_title(ROBOTO_25, entries[i].name, windowX - 60));
    }
    TTF_Font* label_font = cell_w >= 300 ? ROBOTO_25 : cell_w >= 190 ? ROBOTO_20 : ROBOTO_15;
    const int line_height = TTF_FontHeight(label_font);
    for (int card = first_card; card < last_card; ++card) {
      const int i = numFolders + card, x = (card % cols) * cell_w;
      const int y = library_header_h + books_y + (card / cols) * cell_h - scroll_y;
      if (y + cell_h < library_header_h || y >= windowY - library_footer_h) continue;
      const auto& entry = entries[i];
      SDL_Texture* cover = g_cover_cache.get(entry.cover_path);
      if (cover) SDL_DrawImageScale(RENDERER, cover, x + 1, y, thumb_w, thumb_h);
      else {
        const SDL_Color blank = configDarkMode ? SDL_Color{28, 32, 38, 255} : SDL_Color{226, 230, 234, 255};
        SDL_DrawRect(RENDERER, x + 1, y + 1, thumb_w, thumb_h - 2, blank);
        SDL_DrawImageScale(RENDERER, entry.kind == LibraryEntryKind::Series ? folder_image : book_image,
          x + (cell_w - 40) / 2, y + (cell_h - 40) / 2 - 25, 40, 40);
        const string status = entry.cover_path.empty() ? "Нет выпусков" : entry.missing ? "Файл недоступен" :
          cover_failed[i] || (!cl[0].active && !cl[1].active) ? "Обложка недоступна" : "Загрузка...";
        const auto lines = library_wrap(ROBOTO_15, status, cell_w - 12);
        int sy = y + cell_h / 2 + 4;
        for (const auto& line : lines) { library_text.draw(ROBOTO_15, x + (cell_w - library_text_width(ROBOTO_15, line)) / 2, sy, textColor, line); sy += TTF_FontLineSkip(ROBOTO_15); }
      }
      const int strip_h = line_height + TTF_FontHeight(ROBOTO_15) + 12;
      SDL_SetRenderDrawBlendMode(RENDERER, SDL_BLENDMODE_BLEND);
      SDL_SetRenderDrawColor(RENDERER, 0, 0, 0, 210);
      SDL_Rect strip = {x + 1, y + cell_h - strip_h, thumb_w, strip_h}; SDL_RenderFillRect(RENDERER, &strip);
      SDL_SetRenderDrawBlendMode(RENDERER, SDL_BLENDMODE_NONE);
      const string title = library_fit_title(label_font, entry.name, cell_w - 12);
      library_text.draw(label_font, x + 6, strip.y + 3, WHITE, title);
      const string progress = entry.kind == LibraryEntryKind::Series ?
        std::to_string(entry.count) + " вып. · " + std::to_string(entry.completed) + " прочитано" : library_progress(entry.state);
      library_text.draw(ROBOTO_15, x + 6, strip.y + line_height + 5, WHITE, library_fit_title(ROBOTO_15, progress, cell_w - 12));
      if (entry.kind == LibraryEntryKind::Book && entry.state.total > 0) {
        const int read = entry.state.finished ? entry.state.total : entry.state.last_read > 0 ? std::min(entry.state.page + 1, entry.state.total) : 0;
        SDL_DrawRect(RENDERER, x + 2, y + cell_h - 4, (cell_w - 4) * read / entry.state.total, 3, accent);
      }
      string badges;
      if (entry.state.favorite) badges = "Избр.";
      if (entry.note) badges += badges.empty() ? "Заметка" : " · Заметка";
      if (entry.kind == LibraryEntryKind::Series) badges = "R Продолжить";
      if (!badges.empty()) {
        const string label = library_fit_title(ROBOTO_15, badges, cell_w - 12);
        SDL_DrawRect(RENDERER, x + 4, y + 4, std::min(cell_w - 8, library_text_width(ROBOTO_15, label) + 10), TTF_FontHeight(ROBOTO_15) + 6, SDL_Color{0, 93, 104, 255});
        library_text.draw(ROBOTO_15, x + 9, y + 7, WHITE, label);
      }
      if (chosen_index == i) {
        SDL_SetRenderDrawColor(RENDERER, accent.r, accent.g, accent.b, 255);
        for (int t = 0; t < 3; ++t) { SDL_Rect border = {x + t, y + t, cell_w - 2 * t, cell_h - 2 * t}; SDL_RenderDrawRect(RENDERER, &border); }
      }
    }
    if (entries.empty()) {
      library_text.draw(ROBOTO_25, 24, library_header_h + 35, textColor,
        view.favorites || view.unread || !view.query.empty() ? "По этим условиям комиксы не найдены." : "Здесь пока нет комиксов.");
      library_text.draw(ROBOTO_20, 24, library_header_h + 78, textColor,
        view.kind == LibraryViewKind::Collections ? "X — создать коллекцию или обновить библиотеку." : "X — поиск, папки библиотеки и сброс фильтров.");
    }
    SDL_RenderSetClipRect(RENDERER, nullptr);
    SDL_DrawRect(RENDERER, 0, windowY - library_footer_h, windowX, library_footer_h, backColor);
    const auto current = selected();
    if (!current.key.empty()) {
      auto title_lines = library_wrap(ROBOTO_20, current.name, windowX - 24);
      TTF_Font* footer_font = ROBOTO_20;
      if (title_lines.size() > 2) { footer_font = ROBOTO_15; title_lines = library_wrap(footer_font, current.name, windowX - 24); }
      int y = windowY - library_footer_h + 6;
      for (size_t i = 0; i < title_lines.size() && i < 3; ++i) { library_text.draw(footer_font, 12, y, textColor, title_lines[i]); y += TTF_FontLineSkip(footer_font); }
      string summary = current.kind == LibraryEntryKind::Book ? library_progress(current.state) :
        current.kind == LibraryEntryKind::Series ? std::to_string(current.count) + " выпусков; прочитано: " + std::to_string(current.completed) + "; R — продолжить" : "Папка";
      if (current.state.favorite) summary += " · Избранное";
      if (title_lines.size() > 3) summary += " · Коснитесь для полного названия";
      library_text.draw(ROBOTO_15, 12, windowY - 51, accent, library_fit_title(ROBOTO_15, summary, windowX - 24));
    }
    library_text.draw(ROBOTO_15, 12, windowY - 29, textColor,
      "A Открыть   B Назад   X Действия   Y Заметка   ZL/ZR Сетка   R Продолжить   + Настройки");
    library_text.draw(ROBOTO_15, windowX - 130, windowY - 29, textColor,
      std::to_string(entries.empty() ? 0 : chosen_index + 1) + " / " + std::to_string(entries.size()));

    if (modal != LibraryModal::None) {
      SDL_SetRenderDrawBlendMode(RENDERER, SDL_BLENDMODE_BLEND);
      SDL_SetRenderDrawColor(RENDERER, 0, 0, 0, 165); SDL_Rect dim = {0, 0, windowX, windowY}; SDL_RenderFillRect(RENDERER, &dim);
      SDL_SetRenderDrawBlendMode(RENDERER, SDL_BLENDMODE_NONE);
      SDL_DrawRect(RENDERER, 60, 65, windowX - 120, windowY - 130, configDarkMode ? HINT_COLOUR_DARK : HINT_COLOUR_LIGHT);
      library_text.draw(ROBOTO_25, 80, 85, textColor, library_fit_title(ROBOTO_25, modal_title, windowX - 160));
      SDL_Rect modal_clip = {80, 135, windowX - 160, windowY - 265}; SDL_RenderSetClipRect(RENDERER, &modal_clip);
      if (modal == LibraryModal::Actions || modal == LibraryModal::Options || modal == LibraryModal::Picker) {
        const int size = modal == LibraryModal::Picker ? picker_paths.size() : actions.size();
        for (int row = 0; row < modal_rows && row + modal_scroll < size; ++row) {
          const int index = modal_scroll + row, y = 140 + row * 38;
          if (index == modal_index) SDL_DrawRect(RENDERER, 80, y - 1, windowX - 160, 36, selectorColor);
          string label = modal == LibraryModal::Picker ? (picker_checked.count(picker_paths[index]) ? "[x] " : "[ ] ") + wook::title(picker_paths[index]) : actions[index].label;
          library_text.draw(ROBOTO_20, 88, y + 3, textColor, library_fit_title(ROBOTO_20, label, windowX - 180));
        }
        if (size == 0) library_text.draw(ROBOTO_20, 88, 155, textColor, modal == LibraryModal::Picker ? "Подходящих выпусков нет." : "Действий нет.");
      } else {
        for (int row = 0; row < text_rows && row + modal_scroll < static_cast<int>(modal_lines.size()); ++row)
          library_text.draw(ROBOTO_20, 88, 140 + row * (TTF_FontLineSkip(ROBOTO_20) + 3), textColor, modal_lines[modal_scroll + row]);
      }
      SDL_RenderSetClipRect(RENDERER, nullptr);
      const string hint = modal == LibraryModal::Picker ? "A Отметить   X Применить (" + std::to_string(picker_checked.size()) + ")" :
        modal == LibraryModal::Notes ? "A Изменить   Вверх/вниз, L/R Прокрутка" :
        modal == LibraryModal::Confirm ? "A Подтвердить" :
        modal == LibraryModal::Actions || modal == LibraryModal::Options ? "A Выбрать   L/R Страница" : "Вверх/вниз, L/R Прокрутка";
      library_text.draw(ROBOTO_20, 85, windowY - 112, textColor, hint);
      library_text.draw(ROBOTO_20, windowX - 235, windowY - 112, accent, "B Закрыть");
      const int total = modal == LibraryModal::Picker ? picker_paths.size() :
        modal == LibraryModal::Actions || modal == LibraryModal::Options ? actions.size() : modal_lines.size();
      library_text.draw(ROBOTO_15, windowX - 180, 113, textColor, std::to_string(total ? modal_scroll + 1 : 0) + " / " + std::to_string(total));
      if (modal == LibraryModal::Picker && modal_index >= 0 && modal_index < static_cast<int>(picker_paths.size()))
        library_text.draw(ROBOTO_15, 88, 114, textColor, library_fit_title(ROBOTO_15, picker_paths[modal_index], windowX - 370));
    }
    SDL_RenderPresent(RENDERER);
  }

  save_config(chosenFolderColor, chosenBookColor, scroll_option, zoom_option, configDarkMode);
  if (!wook::flush()) Log_Error("library state save: " + wook::last_error());
  // Stop all background cover loader threads
  for (int w = 0; w < N_WORKERS; ++w) {
    __atomic_store_n(&cl[w].should_exit, 1, __ATOMIC_RELEASE);
    __atomic_store_n(&cl[w].job_ready, 0, __ATOMIC_RELEASE);
  }
  for (int w = 0; w < N_WORKERS; ++w) {
    if (!cl[w].active) continue;
    threadWaitForExit(&cl[w].thread); threadClose(&cl[w].thread); cl[w].active = false;
    free(cl[w].result_pixels); cl[w].result_pixels = nullptr;
  }
  library_text.clear(); g_cover_cache.clear();
  if (optionConfig) { config_destroy(optionConfig); free(optionConfig); optionConfig = nullptr; }
}
'''


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "wookreader-src")
    path = root / "source/menus/book-chooser/MenuChooser.cpp"
    text = path.read_text(encoding="utf-8")
    if "BEGIN WOOK_LIBRARY_HELPERS" in text:
        raise RuntimeError("Library UI is already installed")
    if not (root / "source/menus/book/WookLibrary.hpp").exists():
        raise RuntimeError("Run install_state.py before enhance_library.py")
    if "cover JPEG init failed:" not in text:
        raise RuntimeError("Run fix_covers.py before enhance_library.py")
    text = once(text, '#include "../book/WookRAR.hpp"', '#include "../book/WookRAR.hpp"\n#include "../book/WookLibrary.hpp"', "state include")
    text = once(text, "  char job_path[512];", "  string job_path; // published with job_ready; no UTF-8 path truncation", "full cover path")
    notes_start = text.index('static string chooser_note_path(')
    notes_end = text.index('// ── Reading history', notes_start)
    text = text[:notes_start] + r'''// Same identity and empty-file tombstone as ReaderBook.cpp.
static string chooser_note_path(const string& fpath) {
    uint64_t hash = 14695981039346656037ULL;
    for (unsigned char c : fpath) { hash ^= c; hash *= 1099511628211ULL; }
    char key[32];
    snprintf(key, sizeof(key), "book_%016llx.txt", (unsigned long long)hash);
    return string(CHOOSER_NOTES_DIR) + "/" + key;
}
static string chooser_note_read(const string& fpath) {
    FILE* file = fopen(chooser_note_path(fpath).c_str(), "rb");
    if (!file) file = fopen((string(CHOOSER_NOTES_DIR) + "/" + wook::legacy_name(fpath) + ".txt").c_str(), "rb");
    if (!file) return {};
    string result; char buffer[1024]; size_t count;
    while (result.size() < 65536 && (count = fread(buffer, 1, sizeof(buffer), file)))
        result.append(buffer, std::min(count, 65536 - result.size()));
    fclose(file);
    result.erase(std::remove(result.begin(), result.end(), '\r'), result.end());
    return result;
}
static bool chooser_note_exists(const string& fpath) { return !chooser_note_read(fpath).empty(); }
static void chooser_note_save(const string& fpath, const string& text) {
    FS_RecursiveMakeDir(CHOOSER_NOTES_DIR);
    const string path = chooser_note_path(fpath), temporary = path + ".tmp";
    FILE* file = fopen(temporary.c_str(), "wb");
    if (!file) { Log_Error("library note save failed path=" + fpath); return; }
    bool ok = fwrite(text.data(), 1, text.size(), file) == text.size();
    if (fclose(file) != 0) ok = false;
    if (!ok || rename(temporary.c_str(), path.c_str()) != 0) {
        remove(temporary.c_str()); Log_Error("library note replacement failed path=" + fpath);
    }
}

''' + text[notes_end:]
    text = once(text, '    *configDarkMode = config_setting_get_bool(dark);',
                '    if (dark) *configDarkMode = config_setting_get_bool(dark);', "optional legacy theme")
    text = once(text, r'''  if (folder && book && scroll && zoom) {
    *chosenFolderColor =
        std::clamp(config_setting_get_int(folder), 0, COLORS_SIZE);
    *chosenBookColor = std::clamp(config_setting_get_int(book), 0, COLORS_SIZE);
    *scroll_speed = std::clamp(config_setting_get_int(scroll), 1, 4);
    *zoom_amount = std::clamp(config_setting_get_int(zoom), 1, 4);
    if (dark) *configDarkMode = config_setting_get_bool(dark);
  }
''', r'''  if (folder) *chosenFolderColor = std::clamp(config_setting_get_int(folder), 0, COLORS_SIZE);
  if (book) *chosenBookColor = std::clamp(config_setting_get_int(book), 0, COLORS_SIZE);
  if (scroll) *scroll_speed = std::clamp(config_setting_get_int(scroll), 1, 4);
  if (zoom) *zoom_amount = std::clamp(config_setting_get_int(zoom), 1, 4);
  if (dark) *configDarkMode = config_setting_get_bool(dark);
''', "retain independent legacy options")
    save_start = text.index('  if (!folder || !book || !scroll || !zoom || !dark) {')
    save_end = text.index('\n  if (folder && book && scroll && zoom && dark)', save_start)
    text = text[:save_start] + r'''  if (!folder) folder = config_setting_add(config_root_setting(optionConfig), "FolderColor", CONFIG_TYPE_INT);
  if (!book) book = config_setting_add(config_root_setting(optionConfig), "BookColor", CONFIG_TYPE_INT);
  if (!scroll) scroll = config_setting_add(config_root_setting(optionConfig), "ScrollSpeed", CONFIG_TYPE_INT);
  if (!zoom) zoom = config_setting_add(config_root_setting(optionConfig), "ZoomAmount", CONFIG_TYPE_INT);
  if (!dark) dark = config_setting_add(config_root_setting(optionConfig), "DarkMode", CONFIG_TYPE_BOOL);
''' + text[save_end:]
    recent_start = text.index("static void load_recent() {")
    recent_end = text.index("\nstatic void save_recent()", recent_start)
    text = text[:recent_start] + r'''static void load_recent() {
    g_recent.clear();
    std::ifstream file(RECENT_FILE);
    string line;
    while (g_recent.size() < 10 && std::getline(file, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (!line.empty()) g_recent.push_back(line);
    }
}
''' + text[recent_end:]
    start = text.index("void Menu_StartChoosing() {")
    body = text.index("  float zoom_amount = 0.3f * zoom_option;", start)
    text = text[:start] + HELPERS + "\n" + text[start:body] + BODY
    path.write_text(text, encoding="utf-8")
    print("Installed RU3 library: collections, UTF-8 search, progress, filters, modal input, notes and cover retry.")


if __name__ == "__main__":
    main()
