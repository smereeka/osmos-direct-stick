#include "WookLibrary.hpp"
#include <algorithm>
#include <cassert>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

static void check(bool value, const char* message) {
    if (!value) { std::cerr << "FAIL: " << message << "\n"; std::exit(2); }
}
static std::string read(const std::string& path) {
    std::ifstream input(path);
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}
int main(int argc, char** argv) {
    check(argc == 2, "test root");
    std::string root = argv[1];
    std::filesystem::create_directories(root);
    const std::string legacy = "RickandMorty052cbr = 11;\nRickandMorty052cbr_T = 25;\n";
    std::ofstream(root + "/saved_pages.cfg") << legacy;
    wook::set_storage_root_for_tests(root);
    check(wook::battery_saver(), "saver enabled by default");
    check(wook::keep_zoom() && wook::keep_position() && wook::group_series(), "default settings");
    auto old = wook::book_state("/switch/WookReader/books/Rick and Morty 052.cbr");
    check(old.page == 11 && old.total == 25, "legacy Latin progress imported");
    check(wook::book_state("/switch/WookReader/other/Rick and Morty 052.cbr").page == 0,
          "ambiguous basename not duplicated into another folder");
    const std::string russian = "/switch/WookReader/books/Хранители.cbr";
    const std::string same_name_elsewhere = "/switch/WookReader/other/Хранители.cbr";
    const std::string digit = "/switch/WookReader/books/123 Комикс.cbr";
    const std::string escaped = "/switch/WookReader/books/Історія \"Їжак\" \\\\ 002.cbz";
    wook::save_progress(russian, 11, 531);
    wook::save_progress(same_name_elsewhere, 42, 100);
    wook::save_progress(digit, 4, 25);
    wook::save_progress(escaped, 8, 30);
    wook::set_favorite(russian, true);
    wook::save_view(russian, 1, 2.125f, -0.25f, 0.625f);
    auto id = wook::create_collection("Хранители и \"другие\"", {russian, digit, escaped});
    check(!id.empty(), "create Unicode collection");
    check(wook::move_in_collection(id, digit, -1), "manual order");
    wook::Collection group;
    check(wook::collection(id, group) && group.books[0] == digit, "order applied");
    group.cover = escaped;
    check(wook::update_collection(group), "cover and title update");
    wook::set_battery_saver(false);
    wook::set_keep_position(false);
    check(wook::flush(), "first atomic write");
    check(read(root + "/saved_pages.cfg") == legacy, "legacy file untouched");

    wook::set_storage_root_for_tests(root);
    auto restored = wook::book_state(russian);
    check(restored.page == 11 && restored.total == 531 && restored.favorite, "Cyrillic progress round trip");
    check(restored.has_view && restored.view_mode == 1 && std::fabs(restored.zoom - 2.125f) < 1e-6 &&
          restored.pan_x == -0.25f && restored.pan_y == 0.625f, "view round trip");
    check(wook::book_state(same_name_elsewhere).page == 42, "full-path isolation");
    check(wook::book_state(digit).page == 4, "numeric leading name");
    check(wook::book_state(escaped).page == 8, "escaped UTF-8 path");
    check(wook::book_state("sdmc:" + russian).page == 11, "sdmc alias is same identity");
    check(!wook::battery_saver() && !wook::keep_position(), "settings persisted");
    check(wook::collection(id, group) && group.cover == escaped && group.books[0] == digit,
          "collection persisted");
    wook::set_reading_sequence(group.books, group.name);
    check(wook::next_book(digit) == russian && wook::previous_book(russian) == digit, "reading sequence");
    check(wook::previous_book(digit).empty() && wook::next_book(escaped).empty(), "sequence boundaries");
    wook::save_progress(digit, 24, 25);
    check(wook::book_state(digit).finished && wook::completed_books(group) == 1, "completed issue");
    check(wook::continue_book(group) == russian, "continue next unread issue");
    wook::set_finished(digit, false);
    check(!wook::book_state(digit).finished && wook::book_state(digit).page == 0, "mark unread resets progress");
    check(wook::matches_query("ХРАНИТЕЛИ.сbr", "хранители"), "Russian casefold search");
    check(wook::matches_query("ҐРУНТ ЄДНІСТЬ ЇЖАК ІСТОРІЯ", "ґрунт єдність їжак історія"),
          "Ukrainian casefold search");
    std::string cut = "Тест😄";
    wook::pop_utf8(cut);
    check(cut == "Тест", "remove complete 4-byte codepoint");
    wook::pop_utf8(cut);
    check(cut == "Тес", "remove complete Cyrillic codepoint");

    std::vector<std::string> names = {"/books/Рик и Морти 010.cbr", "/books/Рик и Морти 002.cbr",
        "/books/Рик и Морти 001.cbr", "/books/Batman Annual 001.cbr", "/books/Batman 001.cbr",
        "/books/Batman 002.cbr", "/books/Batman (2016) 001.cbr", "/books/Batman (2016) 002.cbr",
        "/books/Watchmen.cbr", "/books/Some title 2020.cbr", "/other/Рик и Морти 011.cbr"};
    auto automatic = wook::automatic_collections(names);
    check(automatic.size() == 3, "conservative series grouping keeps years/volumes/specials/folders distinct");
    auto rick = std::find_if(automatic.begin(), automatic.end(), [](const wook::Collection& c) {
        return c.name == "Рик и Морти";
    });
    check(rick != automatic.end() && rick->books.size() == 3 &&
          rick->books[0].find("001") != std::string::npos &&
          rick->books[2].find("010") != std::string::npos, "natural issue ordering");
    auto overridden = *rick;
    overridden.name = "Моя серия";
    std::reverse(overridden.books.begin(), overridden.books.end());
    check(wook::update_collection(overridden), "override auto series");
    check(wook::automatic_collections(names).size() == 2, "persistent override has no duplicate auto card");
    check(wook::remove_collection(overridden.id), "remove collection metadata only");
    check(wook::automatic_collections(names).size() == 2, "deleted auto group remains hidden");
    check(wook::flush(), "second write");
    check(std::filesystem::exists(root + "/library.cfg.bak"), "previous valid state retained");
    check(!std::filesystem::exists(root + "/library.cfg.tmp"), "no incomplete write left");
    wook::set_storage_root_for_tests(root);
    check(wook::automatic_collections(names).size() == 2, "hidden auto group persisted");

    const auto valid = read(root + "/library.cfg");
    std::ofstream(root + "/library.cfg") << "broken = {";
    wook::set_storage_root_for_tests(root);
    wook::initialize();
    check(!wook::last_error().empty(), "malformed config is reported");
    wook::set_favorite(russian, true);
    check(wook::flush(), "recover using backup while retaining malformed file");
    bool retained_bad = false;
    for (auto& file : std::filesystem::directory_iterator(root))
        if (file.path().filename().string().find("library.cfg.invalid.") == 0)
            retained_bad = read(file.path().string()) == "broken = {";
    check(retained_bad, "malformed original retained rather than deleted");

    std::ofstream(root + "/library.cfg") << "schema = 999;\n";
    wook::set_storage_root_for_tests(root);
    wook::set_favorite(russian, false);
    check(!wook::flush() && read(root + "/library.cfg") == "schema = 999;\n",
          "unsupported future schema cannot be overwritten");
    std::filesystem::remove(root + "/library.cfg");
    std::ofstream(root + "/library.cfg.bak") << "schema = 999;\n";
    wook::set_storage_root_for_tests(root);
    wook::set_favorite(russian, true);
    check(!wook::flush() && !wook::flush() &&
          !std::filesystem::exists(root + "/library.cfg") &&
          read(root + "/library.cfg.bak") == "schema = 999;\n",
          "future backup with missing primary cannot be rotated away");
    std::ofstream(root + "/library.cfg") << "broken = {";
    wook::set_storage_root_for_tests(root);
    wook::set_favorite(russian, false);
    check(!wook::flush() && read(root + "/library.cfg") == "broken = {" &&
          read(root + "/library.cfg.bak") == "schema = 999;\n",
          "future backup with malformed primary preserves both files");
    std::ofstream(root + "/library.cfg") << valid;
    std::cout << "{\"ok\":true,\"checks\":\"UTF-8 and numeric progress, legacy migration, path isolation, "
                 "view/settings persistence, manual/automatic collections, natural order, sequence, "
                 "search, unread/favorites, atomic backup and corrupt/future config preservation\"}\n";
}

