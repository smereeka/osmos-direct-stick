#include "WookLibrary.hpp"

#include <libconfig.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <filesystem>
#include <iomanip>
#include <map>
#include <locale>
#include <set>
#include <sstream>
#include <system_error>
#include <utility>

namespace wook {
namespace {
struct Settings {
    bool saver = true;
    bool zoom = true;
    bool position = true;
    bool groups = true;
};
std::string root = "/switch/WookReader";
bool loaded = false, dirty = false, blocked = false, preserve_bad_primary = false;
std::string error;
Settings options;
std::map<std::string, BookState> books;
std::map<std::string, Collection> groups;
std::map<std::string, std::string> legacy_claims;
std::map<std::string, std::pair<int, int>> legacy_pages;
std::set<std::string> hidden_auto;
std::vector<std::string> sequence;
std::string sequence_name;
auto last_flush = std::chrono::steady_clock::now();

std::string canonical(std::string path) {
    if (path.compare(0, 6, "sdmc:/") == 0) path.erase(0, 5);
    if (path.empty()) return path;
    return std::filesystem::path(path).lexically_normal().generic_string();
}
std::string trim(std::string value) {
    const auto first = value.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) return "";
    const auto last = value.find_last_not_of(" \t\r\n");
    return value.substr(first, last - first + 1);
}
std::string quoted(const std::string& value) {
    std::string out = "\"";
    static const char hex[] = "0123456789abcdef";
    for (unsigned char c : value) {
        switch (c) {
            case '\\': out += "\\\\"; break;
            case '"': out += "\\\""; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 32) { out += "\\x"; out += hex[c >> 4]; out += hex[c & 15]; }
                else out += static_cast<char>(c);
        }
    }
    return out + "\"";
}
std::string hash_text(const std::string& value) {
    std::uint64_t hash = 14695981039346656037ULL;
    for (unsigned char c : value) { hash ^= c; hash *= 1099511628211ULL; }
    char buffer[17];
    std::snprintf(buffer, sizeof(buffer), "%016llx", static_cast<unsigned long long>(hash));
    return buffer;
}
const char* get_string(config_setting_t* setting, const char* key, const char* fallback = "") {
    const char* value = nullptr;
    return setting && config_setting_lookup_string(setting, key, &value) && value ? value : fallback;
}
int get_int(config_setting_t* setting, const char* key, int fallback = 0) {
    int value;
    return setting && config_setting_lookup_int(setting, key, &value) ? value : fallback;
}
bool get_bool(config_setting_t* setting, const char* key, bool fallback = false) {
    int value;
    return setting && config_setting_lookup_bool(setting, key, &value) ? value != 0 : fallback;
}
float get_float(config_setting_t* setting, const char* key, float fallback = 0) {
    double value;
    if (!setting || !config_setting_lookup_float(setting, key, &value) || !std::isfinite(value))
        return fallback;
    return static_cast<float>(value);
}
void normalize(Collection& value) {
    value.name = trim(value.name);
    std::set<std::string> used;
    std::vector<std::string> members;
    for (const auto& path : value.books) {
        auto normalized = canonical(path);
        if (!normalized.empty() && used.insert(normalized).second) members.push_back(normalized);
    }
    value.books.swap(members);
    value.cover = canonical(value.cover);
    if (used.count(value.cover) == 0) value.cover = value.books.empty() ? "" : value.books.front();
}
void load_legacy() {
    config_t cfg;
    config_init(&cfg);
    if (config_read_file(&cfg, (root + "/saved_pages.cfg").c_str())) {
        config_setting_t* all = config_root_setting(&cfg);
        for (int i = 0; i < config_setting_length(all); ++i) {
            config_setting_t* field = config_setting_get_elem(all, i);
            const char* key = config_setting_name(field);
            if (!key || config_setting_type(field) != CONFIG_TYPE_INT) continue;
            std::string name(key);
            if (name.size() >= 2 && name.compare(name.size() - 2, 2, "_T") == 0) continue;
            int total = get_int(all, (name + "_T").c_str(), 0);
            legacy_pages[name] = {std::max(0, config_setting_get_int(field)), std::max(0, total)};
        }
    }
    config_destroy(&cfg);
}
// 1 = loaded, 0 = unreadable/malformed, -1 = a newer unsupported schema.
int read_state(const std::string& filename) {
    config_t cfg;
    config_init(&cfg);
    if (!config_read_file(&cfg, filename.c_str())) { config_destroy(&cfg); return 0; }
    auto all = config_root_setting(&cfg);
    if (get_int(all, "schema", 0) != 1) { config_destroy(&cfg); return -1; }
    Settings parsed;
    auto settings = config_setting_get_member(all, "settings");
    parsed.saver = get_bool(settings, "battery_saver", true);
    parsed.zoom = get_bool(settings, "keep_zoom", true);
    parsed.position = get_bool(settings, "keep_position", true);
    parsed.groups = get_bool(settings, "group_series", true);
    std::map<std::string, BookState> parsed_books;
    auto list = config_setting_get_member(all, "books");
    if (list && config_setting_type(list) == CONFIG_TYPE_LIST) {
        for (int i = 0; i < config_setting_length(list); ++i) {
            auto record = config_setting_get_elem(list, i);
            BookState state;
            state.path = canonical(get_string(record, "path"));
            if (state.path.empty()) continue;
            state.total = std::max(0, get_int(record, "total"));
            state.page = std::max(0, get_int(record, "page"));
            if (state.total > 0) state.page = std::min(state.page, state.total - 1);
            state.finished = get_bool(record, "finished");
            state.favorite = get_bool(record, "favorite");
            long long time = 0;
            if (config_setting_lookup_int64(record, "last_read", &time)) state.last_read = time;
            else state.last_read = get_int(record, "last_read");
            state.has_view = get_bool(record, "has_view");
            state.view_mode = std::clamp(get_int(record, "view_mode"), 0, 2);
            state.zoom = std::clamp(get_float(record, "zoom", 1), 0.01f, 100.0f);
            state.pan_x = std::clamp(get_float(record, "pan_x"), -100.0f, 100.0f);
            state.pan_y = std::clamp(get_float(record, "pan_y"), -100.0f, 100.0f);
            parsed_books[state.path] = state;
        }
    }
    std::map<std::string, Collection> parsed_groups;
    list = config_setting_get_member(all, "collections");
    if (list && config_setting_type(list) == CONFIG_TYPE_LIST) {
        for (int i = 0; i < config_setting_length(list); ++i) {
            auto record = config_setting_get_elem(list, i);
            Collection value;
            value.id = get_string(record, "id");
            value.name = get_string(record, "name");
            value.cover = get_string(record, "cover");
            auto members = config_setting_get_member(record, "members");
            if (members && config_setting_type(members) == CONFIG_TYPE_LIST) {
                for (int n = 0; n < config_setting_length(members); ++n) {
                    const char* path = config_setting_get_string(config_setting_get_elem(members, n));
                    if (path) value.books.emplace_back(path);
                }
            }
            normalize(value);
            if (!value.id.empty() && !value.name.empty()) parsed_groups[value.id] = value;
        }
    }
    std::map<std::string, std::string> claims;
    list = config_setting_get_member(all, "legacy_claims");
    if (list && config_setting_type(list) == CONFIG_TYPE_LIST) {
        for (int i = 0; i < config_setting_length(list); ++i) {
            auto record = config_setting_get_elem(list, i);
            std::string key = get_string(record, "key");
            auto path = canonical(get_string(record, "path"));
            if (!key.empty() && !path.empty()) claims[key] = path;
        }
    }
    std::set<std::string> hidden;
    list = config_setting_get_member(all, "hidden_auto");
    if (list && config_setting_type(list) == CONFIG_TYPE_LIST) {
        for (int i = 0; i < config_setting_length(list); ++i) {
            const char* value = config_setting_get_string(config_setting_get_elem(list, i));
            if (value) hidden.insert(value);
        }
    }
    books.swap(parsed_books);
    groups.swap(parsed_groups);
    legacy_claims.swap(claims);
    hidden_auto.swap(hidden);
    options = parsed;
    config_destroy(&cfg);
    return 1;
}
BookState& edit_book(const std::string& path) {
    auto normalized = canonical(path);
    if (books.count(normalized) == 0) books[normalized] = book_state(normalized);
    return books[normalized];
}
bool file_exists(const std::string& path) {
    std::error_code ec;
    return std::filesystem::is_regular_file(path, ec);
}
std::string series_prefix(const std::string& path) {
    std::string value = title(path);
    // Remove trailing scan/translation tags, but keep a numeric volume/year tag:
    // Batman 001 [RUS] -> Batman; Batman (2016) 001 -> Batman (2016).
    for (;;) {
        value = trim(value);
        if (value.empty()) return "";
        char close = value.back(), open = close == ']' ? '[' : (close == ')' ? '(' : 0);
        if (!open) break;
        auto at = value.find_last_of(open);
        if (at == std::string::npos) break;
        auto inside = value.substr(at + 1, value.size() - at - 2);
        if (inside.find_first_of("0123456789") != std::string::npos) break;
        value.erase(at);
    }
    value = trim(value);
    auto end = value.size(), begin = end;
    while (begin > 0 && value[begin - 1] >= '0' && value[begin - 1] <= '9') --begin;
    if (begin == end || begin == 0) return "";
    const char separator = value[begin - 1];
    if (separator != ' ' && separator != '_' && separator != '-' && separator != '#' && separator != '.')
        return "";
    const auto digits = value.substr(begin);
    if (digits.size() == 4 && digits >= "1900" && digits <= "2099") return "";
    value.erase(begin);
    while (!value.empty() && (value.back() == ' ' || value.back() == '_' || value.back() == '-' ||
                             value.back() == '#' || value.back() == '.')) value.pop_back();
    return trim(value);
}
void append_utf8(std::string& out, std::uint32_t point) {
    if (point < 0x80) out += static_cast<char>(point);
    else if (point < 0x800) {
        out += static_cast<char>(0xc0 | (point >> 6));
        out += static_cast<char>(0x80 | (point & 0x3f));
    } else if (point < 0x10000) {
        out += static_cast<char>(0xe0 | (point >> 12));
        out += static_cast<char>(0x80 | ((point >> 6) & 0x3f));
        out += static_cast<char>(0x80 | (point & 0x3f));
    } else {
        out += static_cast<char>(0xf0 | (point >> 18));
        out += static_cast<char>(0x80 | ((point >> 12) & 0x3f));
        out += static_cast<char>(0x80 | ((point >> 6) & 0x3f));
        out += static_cast<char>(0x80 | (point & 0x3f));
    }
}
} // namespace

void initialize() {
    if (loaded) return;
    loaded = true;
    const auto filename = root + "/library.cfg";
    const bool exists = file_exists(filename);
    int result = read_state(filename);
    if (result == -1) {
        blocked = true;
        error = "Неподдерживаемая версия library.cfg. Файл сохранён без изменений.";
    } else if (result != 1) {
        const int backup_result = read_state(filename + ".bak");
        if (backup_result == -1) {
            blocked = true;
            error = "Неподдерживаемая версия library.cfg.bak. Файлы сохранены без изменений.";
        } else if (exists) {
            preserve_bad_primary = true;
            error = "Не удалось прочитать library.cfg. Исходный файл будет сохранён отдельно.";
        } else if (backup_result == 1) {
            dirty = true;
        }
    }
    load_legacy();
}
BookState book_state(const std::string& path) {
    initialize();
    const auto normalized = canonical(path);
    const auto found = books.find(normalized);
    if (found != books.end()) return found->second;
    BookState value;
    value.path = normalized;
    if (normalized.empty()) return value;
    const auto key = legacy_name(normalized);
    const auto old = legacy_pages.find(key);
    const auto claimed = legacy_claims.find(key);
    if (old != legacy_pages.end() && (claimed == legacy_claims.end() || claimed->second == normalized)) {
        value.page = old->second.first;
        value.total = old->second.second;
        if (value.total > 0) value.page = std::min(value.page, value.total - 1);
        value.finished = value.total > 0 && value.page == value.total - 1;
        books[normalized] = value;
        legacy_claims[key] = normalized;
        dirty = true;
    }
    return value;
}
void save_progress(const std::string& path, int page, int total) {
    initialize();
    if (path.empty()) return;
    auto& state = edit_book(path);
    state.total = total > 0 ? total : state.total;
    state.page = std::max(0, page);
    if (state.total > 0) state.page = std::min(state.page, state.total - 1);
    if (state.total > 0 && state.page == state.total - 1) state.finished = true;
    state.last_read = static_cast<std::int64_t>(std::time(nullptr));
    dirty = true;
}
void set_finished(const std::string& path, bool value) {
    initialize();
    auto& state = edit_book(path);
    state.finished = value;
    if (!value) state.page = 0;
    dirty = true;
}
void set_favorite(const std::string& path, bool value) {
    initialize();
    edit_book(path).favorite = value;
    dirty = true;
}
void save_view(const std::string& path, int mode, float zoom, float pan_x, float pan_y) {
    initialize();
    if (!std::isfinite(zoom) || !std::isfinite(pan_x) || !std::isfinite(pan_y)) return;
    auto& state = edit_book(path);
    state.has_view = true;
    state.view_mode = std::clamp(mode, 0, 2);
    state.zoom = std::clamp(zoom, 0.01f, 100.0f);
    state.pan_x = std::clamp(pan_x, -100.0f, 100.0f);
    state.pan_y = std::clamp(pan_y, -100.0f, 100.0f);
    dirty = true;
}
void clear_view(const std::string& path) { initialize(); edit_book(path).has_view = false; dirty = true; }

std::vector<Collection> collections() {
    initialize();
    std::vector<Collection> result;
    for (const auto& item : groups) result.push_back(item.second);
    std::sort(result.begin(), result.end(), [](const Collection& a, const Collection& b) {
        return natural_less(a.name, b.name);
    });
    return result;
}
bool collection(const std::string& id, Collection& result) {
    initialize();
    auto found = groups.find(id);
    if (found == groups.end()) return false;
    result = found->second;
    return true;
}
std::string create_collection(const std::string& name, const std::vector<std::string>& members) {
    initialize();
    if (trim(name).empty()) { error = "Укажите название сборника."; return ""; }
    Collection value;
    value.name = name; value.books = members;
    std::string seed = name + "|" + std::to_string(std::time(nullptr));
    unsigned nonce = 0;
    do { value.id = "c_" + hash_text(seed + "|" + std::to_string(nonce++)); } while (groups.count(value.id));
    normalize(value);
    groups[value.id] = value;
    dirty = true;
    return value.id;
}
bool update_collection(const Collection& original) {
    initialize();
    Collection value = original;
    normalize(value);
    if (value.id.empty() || value.name.empty()) { error = "Укажите название сборника."; return false; }
    value.automatic = false;
    groups[value.id] = value;
    hidden_auto.erase(value.id);
    dirty = true;
    return true;
}
bool remove_collection(const std::string& id) {
    initialize();
    bool existed = groups.erase(id) != 0;
    if (id.compare(0, 5, "auto_") == 0) { hidden_auto.insert(id); existed = true; }
    dirty = dirty || existed;
    return existed;
}
bool add_to_collection(const std::string& id, const std::string& path, int position) {
    initialize();
    auto found = groups.find(id);
    if (found == groups.end() || path.empty()) return false;
    auto& value = found->second;
    auto normalized = canonical(path);
    value.books.erase(std::remove(value.books.begin(), value.books.end(), normalized), value.books.end());
    int index = position < 0 ? static_cast<int>(value.books.size()) :
                std::clamp(position, 0, static_cast<int>(value.books.size()));
    value.books.insert(value.books.begin() + index, normalized);
    normalize(value); dirty = true; return true;
}
bool remove_from_collection(const std::string& id, const std::string& path) {
    initialize();
    auto found = groups.find(id);
    if (found == groups.end()) return false;
    auto& value = found->second;
    const auto before = value.books.size();
    auto normalized = canonical(path);
    value.books.erase(std::remove(value.books.begin(), value.books.end(), normalized), value.books.end());
    normalize(value);
    dirty = dirty || before != value.books.size();
    return before != value.books.size();
}
bool move_in_collection(const std::string& id, const std::string& path, int offset) {
    initialize();
    auto found = groups.find(id);
    if (found == groups.end()) return false;
    auto& value = found->second;
    auto at = std::find(value.books.begin(), value.books.end(), canonical(path));
    if (at == value.books.end()) return false;
    int index = static_cast<int>(at - value.books.begin());
    int target = std::clamp(index + offset, 0, static_cast<int>(value.books.size()) - 1);
    if (target == index) return true;
    auto item = *at;
    value.books.erase(at);
    value.books.insert(value.books.begin() + target, item);
    dirty = true;
    return true;
}
std::vector<Collection> automatic_collections(const std::vector<std::string>& paths) {
    initialize();
    std::vector<Collection> result;
    if (!options.groups) return result;
    std::set<std::string> assigned;
    for (const auto& group : groups) for (const auto& path : group.second.books) assigned.insert(path);
    std::map<std::string, Collection> candidates;
    for (const auto& raw : paths) {
        const auto path = canonical(raw);
        if (assigned.count(path)) continue;
        auto prefix = series_prefix(path);
        if (prefix.empty()) continue;
        const auto parent = std::filesystem::path(path).parent_path().generic_string();
        auto id = "auto_" + hash_text(parent + "|" + casefold_utf8(prefix));
        if (hidden_auto.count(id) || groups.count(id)) continue;
        auto& group = candidates[id];
        group.id = id; group.name = prefix; group.automatic = true; group.books.push_back(path);
    }
    for (auto& item : candidates) {
        auto& group = item.second;
        normalize(group);
        if (group.books.size() < 2) continue;
        std::sort(group.books.begin(), group.books.end(), natural_less);
        group.cover = group.books.front();
        result.push_back(group);
    }
    std::sort(result.begin(), result.end(), [](const Collection& a, const Collection& b) {
        return natural_less(a.name, b.name);
    });
    return result;
}
std::string continue_book(const Collection& value) {
    initialize();
    if (value.books.empty()) return "";
    int recent = -1; std::int64_t latest = 0;
    for (std::size_t i = 0; i < value.books.size(); ++i) {
        auto state = book_state(value.books[i]);
        if (state.last_read > latest) { latest = state.last_read; recent = static_cast<int>(i); }
    }
    if (recent >= 0) {
        if (!book_state(value.books[recent]).finished) return value.books[recent];
        for (std::size_t i = static_cast<std::size_t>(recent + 1); i < value.books.size(); ++i)
            if (!book_state(value.books[i]).finished) return value.books[i];
    }
    for (const auto& path : value.books) if (!book_state(path).finished) return path;
    return value.books.front();
}
int completed_books(const Collection& value) {
    int total = 0;
    for (const auto& path : value.books) if (book_state(path).finished) ++total;
    return total;
}
void set_reading_sequence(const std::vector<std::string>& values, const std::string& name) {
    sequence.clear();
    for (const auto& value : values) sequence.push_back(canonical(value));
    sequence_name = name;
}
void clear_reading_sequence() { sequence.clear(); sequence_name.clear(); }
std::string next_book(const std::string& current) {
    auto at = std::find(sequence.begin(), sequence.end(), canonical(current));
    if (at == sequence.end() || ++at == sequence.end()) return "";
    return *at;
}
std::string previous_book(const std::string& current) {
    auto at = std::find(sequence.begin(), sequence.end(), canonical(current));
    if (at == sequence.end() || at == sequence.begin()) return "";
    return *--at;
}
std::string reading_sequence_name() { return sequence_name; }

bool battery_saver() { initialize(); return options.saver; }
void set_battery_saver(bool value) { initialize(); options.saver = value; dirty = true; }
bool keep_zoom() { initialize(); return options.zoom; }
void set_keep_zoom(bool value) { initialize(); options.zoom = value; dirty = true; }
bool keep_position() { initialize(); return options.position; }
void set_keep_position(bool value) { initialize(); options.position = value; dirty = true; }
bool group_series() { initialize(); return options.groups; }
void set_group_series(bool value) { initialize(); options.groups = value; dirty = true; }

bool flush() {
    initialize();
    if (!dirty) return !blocked;
    // Rate-limit failed automatic writes too: a full/unavailable SD card must
    // not turn the 16 ms input loop into repeated serialization and I/O.
    last_flush = std::chrono::steady_clock::now();
    if (blocked) return false;
    std::ostringstream out;
    out.imbue(std::locale::classic());
    out << std::setprecision(9);
    out << "schema = 1;\nsettings = { battery_saver = " << (options.saver ? "true" : "false")
        << "; keep_zoom = " << (options.zoom ? "true" : "false")
        << "; keep_position = " << (options.position ? "true" : "false")
        << "; group_series = " << (options.groups ? "true" : "false") << "; };\nbooks = (\n";
    bool comma = false;
    for (const auto& item : books) {
        const auto& value = item.second;
        if (comma) out << ",\n";
        comma = true;
        out << " { path = " << quoted(value.path) << "; page = " << value.page
            << "; total = " << value.total << "; finished = " << (value.finished ? "true" : "false")
            << "; favorite = " << (value.favorite ? "true" : "false")
            << "; last_read = " << value.last_read << "L; has_view = " << (value.has_view ? "true" : "false")
            << "; view_mode = " << value.view_mode << "; zoom = " << std::showpoint << value.zoom
            << "; pan_x = " << value.pan_x << "; pan_y = " << value.pan_y << std::noshowpoint << "; }";
    }
    out << "\n);\ncollections = (\n"; comma = false;
    for (const auto& item : groups) {
        const auto& value = item.second;
        if (comma) out << ",\n";
        comma = true;
        out << " { id = " << quoted(value.id) << "; name = " << quoted(value.name)
            << "; cover = " << quoted(value.cover) << "; members = (";
        for (std::size_t i = 0; i < value.books.size(); ++i) {
            if (i) out << ", ";
            out << quoted(value.books[i]);
        }
        out << "); }";
    }
    out << "\n);\nlegacy_claims = (\n"; comma = false;
    for (const auto& claim : legacy_claims) {
        if (comma) out << ",\n";
        comma = true;
        out << " { key = " << quoted(claim.first) << "; path = " << quoted(claim.second) << "; }";
    }
    out << "\n);\nhidden_auto = ("; comma = false;
    for (const auto& id : hidden_auto) {
        if (comma) out << ", ";
        comma = true;
        out << quoted(id);
    }
    out << ");\n";
    std::error_code ec;
    std::filesystem::create_directories(root, ec);
    if (ec) { error = "Не удалось создать папку настроек."; return false; }
    const auto filename = root + "/library.cfg";
    const auto temporary = filename + ".tmp";
    const auto backup = filename + ".bak";
    const auto text = out.str();
    FILE* file = std::fopen(temporary.c_str(), "wb");
    if (!file) { error = "Не удалось сохранить library.cfg. Проверьте свободное место на SD."; return false; }
    bool ok = std::fwrite(text.data(), 1, text.size(), file) == text.size();
    if (std::fflush(file) != 0) ok = false;
    if (std::fclose(file) != 0) ok = false;
    if (!ok) { std::remove(temporary.c_str()); error = "Ошибка записи настроек на SD."; return false; }
    if (preserve_bad_primary && file_exists(filename)) {
        std::string retained = filename + ".invalid." + std::to_string(std::time(nullptr));
        unsigned serial = 0;
        while (file_exists(retained)) retained += "." + std::to_string(++serial);
        if (std::rename(filename.c_str(), retained.c_str()) != 0) {
            std::remove(temporary.c_str()); error = "Не удалось сохранить исходный файл настроек."; return false;
        }
        preserve_bad_primary = false;
    }
    bool had_primary = file_exists(filename);
    if (had_primary) {
        std::remove(backup.c_str());
        if (std::rename(filename.c_str(), backup.c_str()) != 0) {
            std::remove(temporary.c_str()); error = "Не удалось обновить резервную копию настроек."; return false;
        }
    }
    if (std::rename(temporary.c_str(), filename.c_str()) != 0) {
        if (had_primary) std::rename(backup.c_str(), filename.c_str());
        std::remove(temporary.c_str()); error = "Не удалось установить новый файл настроек."; return false;
    }
    dirty = false; error.clear(); last_flush = std::chrono::steady_clock::now();
    return true;
}
void maybe_flush() {
    if (dirty && std::chrono::steady_clock::now() - last_flush >= std::chrono::seconds(15)) flush();
}
std::string last_error() { initialize(); return error; }

std::string title(const std::string& path) {
    auto at = path.find_last_of("/\\");
    auto name = at == std::string::npos ? path : path.substr(at + 1);
    at = name.find_last_of('.');
    if (at != std::string::npos && at != 0) name.erase(at);
    return name;
}
std::string legacy_name(const std::string& path) {
    auto at = path.find_last_of("/\\");
    auto name = at == std::string::npos ? path : path.substr(at + 1);
    const std::string invalid = " :/?#[]@!$&'()*+,;=.";
    name.erase(std::remove_if(name.begin(), name.end(), [&](char c) {
        return invalid.find(c) != std::string::npos;
    }), name.end());
    return name;
}
std::string casefold_utf8(const std::string& text) {
    std::string out;
    for (std::size_t i = 0; i < text.size();) {
        const auto start = i;
        unsigned char first = static_cast<unsigned char>(text[i++]);
        std::uint32_t point = first;
        unsigned extra = 0;
        if ((first & 0xe0) == 0xc0) { point = first & 0x1f; extra = 1; }
        else if ((first & 0xf0) == 0xe0) { point = first & 0x0f; extra = 2; }
        else if ((first & 0xf8) == 0xf0) { point = first & 7; extra = 3; }
        bool valid = i + extra <= text.size();
        for (unsigned n = 0; valid && n < extra; ++n) {
            unsigned char c = static_cast<unsigned char>(text[i + n]);
            if ((c & 0xc0) != 0x80) valid = false;
            else point = (point << 6) | (c & 0x3f);
        }
        if (!valid) { out.append(text, start, 1); continue; }
        i += extra;
        if (point >= 'A' && point <= 'Z') point += 32;
        else if (point >= 0x410 && point <= 0x42f) point += 32;
        else if (point >= 0x400 && point <= 0x40f) point += 80;
        else if (point == 0x490) point = 0x491;
        append_utf8(out, point);
    }
    return out;
}
bool matches_query(const std::string& value, const std::string& query) {
    auto haystack = casefold_utf8(value);
    std::istringstream words(casefold_utf8(query));
    std::string word;
    while (words >> word) if (haystack.find(word) == std::string::npos) return false;
    return true;
}
bool natural_less(const std::string& left, const std::string& right) {
    const auto a = casefold_utf8(left), b = casefold_utf8(right);
    std::size_t i = 0, j = 0;
    while (i < a.size() && j < b.size()) {
        bool da = a[i] >= '0' && a[i] <= '9', db = b[j] >= '0' && b[j] <= '9';
        if (da && db) {
            auto ae = i, be = j;
            while (ae < a.size() && a[ae] >= '0' && a[ae] <= '9') ++ae;
            while (be < b.size() && b[be] >= '0' && b[be] <= '9') ++be;
            auto az = i, bz = j;
            while (az < ae && a[az] == '0') ++az;
            while (bz < be && b[bz] == '0') ++bz;
            if (ae - az != be - bz) return ae - az < be - bz;
            auto compared = a.compare(az, ae - az, b, bz, be - bz);
            if (compared != 0) return compared < 0;
            i = ae; j = be;
        } else {
            auto ca = static_cast<unsigned char>(a[i]), cb = static_cast<unsigned char>(b[j]);
            if (ca != cb) return ca < cb;
            ++i; ++j;
        }
    }
    if (i != a.size() || j != b.size()) return i == a.size();
    return left < right;
}
void pop_utf8(std::string& text) {
    if (text.empty()) return;
    auto at = text.size() - 1;
    while (at > 0 && (static_cast<unsigned char>(text[at]) & 0xc0) == 0x80) --at;
    text.erase(at);
}
void set_storage_root_for_tests(const std::string& value) {
    root = value; loaded = false; dirty = false; blocked = false; preserve_bad_primary = false;
    error.clear(); options = Settings{}; books.clear(); groups.clear(); legacy_claims.clear();
    legacy_pages.clear(); hidden_auto.clear(); clear_reading_sequence();
    last_flush = std::chrono::steady_clock::now();
}
} // namespace wook

