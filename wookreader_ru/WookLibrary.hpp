#pragma once

#include <cstdint>
#include <string>
#include <vector>

// Persistent user state. Call from the main/UI thread; getters return copies.
// UTF-8 paths are values, never libconfig identifiers. Existing progress is read
// from saved_pages.cfg without changing or deleting that legacy file.
namespace wook {

struct BookState {
    std::string path;
    int page = 0;                 // zero based
    int total = 0;                // zero until a book has been indexed
    bool finished = false;
    bool favorite = false;
    std::int64_t last_read = 0;
    bool has_view = false;
    int view_mode = 0;            // reader-specific three-state layout index
    float zoom = 1.0f;
    float pan_x = 0.0f;           // relative position, interpreted by the reader
    float pan_y = 0.0f;
};

struct Collection {
    std::string id;               // stable ASCII id; auto_ prefix is virtual
    std::string name;
    std::vector<std::string> books; // original UTF-8 full paths, ordered
    std::string cover;            // member book path, empty = first member
    bool automatic = false;
};

void initialize();
BookState book_state(const std::string& path);
void save_progress(const std::string& path, int page, int total);
void set_finished(const std::string& path, bool finished);
void set_favorite(const std::string& path, bool favorite);
void save_view(const std::string& path, int mode, float zoom, float pan_x, float pan_y);
void clear_view(const std::string& path);

std::vector<Collection> collections();
bool collection(const std::string& id, Collection& result);
std::string create_collection(const std::string& name, const std::vector<std::string>& books);
bool update_collection(const Collection& value); // auto id converts to persistent override
bool remove_collection(const std::string& id);   // never removes comic files
bool add_to_collection(const std::string& id, const std::string& path, int position = -1);
bool remove_from_collection(const std::string& id, const std::string& path);
bool move_in_collection(const std::string& id, const std::string& path, int offset);
std::vector<Collection> automatic_collections(const std::vector<std::string>& paths);
std::string continue_book(const Collection& collection);
int completed_books(const Collection& collection);

// An in-memory reading order, set by the library before opening a collection.
// Empty means the ordinary library has no automatic next-issue suggestion.
void set_reading_sequence(const std::vector<std::string>& books, const std::string& name = "");
void clear_reading_sequence();
std::string next_book(const std::string& current);
std::string previous_book(const std::string& current);
std::string reading_sequence_name();

bool battery_saver();             // default true
void set_battery_saver(bool value);
bool keep_zoom();                 // default true
void set_keep_zoom(bool value);
bool keep_position();             // default true
void set_keep_position(bool value);
bool group_series();              // default true
void set_group_series(bool value);

// flush() writes dirty state atomically. maybe_flush() limits SD writes during
// reading to one per 15 seconds; always flush explicitly when leaving a book.
bool flush();
void maybe_flush();
std::string last_error();         // Russian message, empty after successful write

std::string title(const std::string& path); // UTF-8 filename without extension
std::string casefold_utf8(const std::string& text); // Latin + RU/UA case folding
bool matches_query(const std::string& path_or_title, const std::string& query);
bool natural_less(const std::string& a, const std::string& b);
void pop_utf8(std::string& text);  // erase one complete final code point
std::string legacy_name(const std::string& path); // original notes/progress key

// Host tests only: select an isolated directory before initialize(). Also
// supported at runtime for tooling, never called by the Switch application.
void set_storage_root_for_tests(const std::string& root);

} // namespace wook
