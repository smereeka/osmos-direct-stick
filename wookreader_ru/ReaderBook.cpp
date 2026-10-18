// WOOK_READER_UX_COMPLETE
#include "BookReader.hpp"
#include "WookLibrary.hpp"
#include "LandscapePageLayout.hpp"
#include "VerticalPageLayout.hpp"
#include <libconfig.h>
#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstring>
#include <cstdlib>

extern void Log_Write(const std::string &message);
extern void Log_Error(const std::string &message);
extern "C" {
#include "SDL_helper.h"
#include "common.h"
#include "config.h"
#include "fs.h"
#include "status_bar.h"
#include "textures.h"
}

// Kept for the legacy chooser until its state migration has been applied.
int windowX = 1280, windowY = 720;
config_t *config = nullptr;
const char *configFile = "/switch/WookReader/saved_pages.cfg";
static const char *NOTES_DIR = "/switch/WookReader/.notes";

static std::string note_file(const std::string &path) {
    uint64_t hash = 14695981039346656037ULL;
    for (unsigned char c : path) { hash ^= c; hash *= 1099511628211ULL; }
    char key[32];
    snprintf(key, sizeof(key), "book_%016llx.txt", (unsigned long long)hash);
    return std::string(NOTES_DIR) + "/" + key;
}

static std::string load_notes(const std::string &path, const std::string &legacy) {
    FILE *file = fopen(note_file(path).c_str(), "rb");
    if (!file) file = fopen((std::string(NOTES_DIR) + "/" + legacy + ".txt").c_str(), "rb");
    if (!file) return {};
    std::string result;
    char buffer[1024];
    size_t count;
    while (result.size() < 65536 && (count = fread(buffer, 1, sizeof(buffer), file)))
        result.append(buffer, std::min(count, 65536 - result.size()));
    fclose(file);
    result.erase(std::remove(result.begin(), result.end(), '\r'), result.end());
    return result;
}

static bool comic_path(const std::string &path) {
    size_t dot = path.find_last_of('.');
    if (dot == std::string::npos) return false;
    std::string ext = path.substr(dot);
    for (char &c : ext) c = (char)std::tolower((unsigned char)c);
    return ext == ".cbz" || ext == ".cbr" || ext == ".cbt" || ext == ".cb7";
}

BookReader::BookReader(const char *path, int *result) {
    *result = 0; // Errors remain visible in the reader until Retry or Back.
    SDL_GetWindowSize(WINDOW, &windowX, &windowY);
    book_path = path ? path : "";
    book_name = wook::legacy_name(book_path);
    _notes = load_notes(book_path, book_name);
    _is_cbz = comic_path(book_path);
    const auto saved = wook::book_state(book_path);
    _currentPageLayout = (BookPageLayout)std::max(0, std::min(saved.view_mode, 2));
    _restore_pending = saved.has_view;
    _restore_view = {saved.zoom, saved.pan_x, saved.pan_y};
    if (_is_cbz) {
        switch_current_page_layout(_currentPageLayout, saved.page);
    } else {
        SDL_ClearScreen(RENDERER, configDarkMode ? BLACK : WHITE);
        SDL_DrawText(RENDERER, ROBOTO_30, 350, 335, configDarkMode ? WHITE : BLACK,
                     "Открытие документа...");
        SDL_RenderPresent(RENDERER);
        if (!ctx) {
            ctx = fz_new_context(nullptr, nullptr, 0);
            if (ctx) fz_register_document_handlers(ctx);
        }
        if (!ctx) {
            _open_error = "Недостаточно памяти для открытия документа.";
            return;
        }
        fz_try(ctx) {
            doc = fz_open_document(ctx, book_path.c_str());
            _total_pages = doc ? fz_count_pages(ctx, doc) : 0;
            if (_total_pages > 0) switch_current_page_layout(_currentPageLayout, saved.page);
            else _open_error = "В документе нет страниц.";
        }
        fz_catch(ctx) {
            _open_error = "Не удалось открыть документ. Проверьте файл и повторите попытку.";
            Log_Error("BookReader open: " + book_path);
        }
    }
    if (!layout && _open_error.empty()) _open_error = "Не удалось подготовить страницу.";
    show_status_bar();
    reset_nav_buttons();
}

BookReader::~BookReader() {
    save_session();
    FreeTextureIfNeeded(&_notes_texture);
    delete layout;
    layout = nullptr;
    if (doc) { fz_drop_document(ctx, doc); doc = nullptr; }
}

bool BookReader::navLandscape() const {
    return _is_cbz && layout ? static_cast<CBZPageLayout *>(layout)->rotation_degrees() == 90
                            : _currentPageLayout == BookPageLayoutLandscape;
}
bool BookReader::is_loading() const { return layout && layout->is_loading(); }
bool BookReader::has_error() const {
    return !_open_error.empty() || (!is_loading() && layout && !layout->page_error().empty());
}
bool BookReader::is_zoomed() const { return layout && layout->capture_view().zoom > 1.01f; }
bool BookReader::status_visible() const {
    return !_overlay_active && configStatusBar &&
           (permStatusBar || (int32_t)(_status_hide_at - SDL_GetTicks()) > 0);
}
bool BookReader::needs_redraw() const {
    uint32_t now = SDL_GetTicks();
    bool nav = configScreenButtons && (int32_t)(_btn_hide_at - now) > 0;
    bool fade = nav && _btn_hide_at - now < 600;
    return _dirty || is_loading() || (layout && layout->pending_work()) || fade ||
        wook::last_error() != _shown_state_error ||
        _last_status_visible != status_visible() || _last_nav_visible != nav ||
        (!_notice.empty() && (int32_t)(_notice_hide_at - now) <= 0);
}
void BookReader::show_status_bar() { _status_hide_at = SDL_GetTicks() + 3300; mark_redraw(); }
void BookReader::reset_nav_buttons() { _btn_hide_at = SDL_GetTicks() + 3000; mark_redraw(); }
void BookReader::notify(const std::string &message) {
    _notice = message; _notice_hide_at = SDL_GetTicks() + 3500; mark_redraw();
}

void BookReader::save_progress() {
    if (!layout || is_loading() || has_error() || !layout->has_page() || _total_pages <= 0) return;
    if (_saved_page != layout->current_page()) {
        wook::save_progress(book_path, layout->current_page(), _total_pages);
        _saved_page = layout->current_page();
    }
}
void BookReader::save_view() {
    if (!layout || is_loading() || has_error() || !layout->has_page() || _restore_pending) return;
    auto view = layout->capture_view();
    if (_last_saved_mode == (int)_currentPageLayout &&
        std::abs(_last_saved_view.zoom - view.zoom) < 0.0001f &&
        std::abs(_last_saved_view.pan_x - view.pan_x) < 0.0001f &&
        std::abs(_last_saved_view.pan_y - view.pan_y) < 0.0001f) return;
    wook::save_view(book_path, (int)_currentPageLayout, view.zoom, view.pan_x, view.pan_y);
    _last_saved_view = view;
    _last_saved_mode = (int)_currentPageLayout;
}
void BookReader::save_session() { save_progress(); save_view(); wook::flush(); }
void BookReader::mark_finished() {
    if (is_loading() || has_error()) return;
    wook::set_finished(book_path, true);
    notify("Выпуск отмечен как прочитанный.");
}
bool BookReader::take_next_issue_request() {
    bool result = _request_next_issue; _request_next_issue = false; return result;
}

void BookReader::restore_pending_view() {
    if (!layout || is_loading() || has_error() || !layout->has_page()) return;
    if (_mode_pending && _is_cbz) {
        static_cast<CBZPageLayout *>(layout)->set_view_mode((int)_currentPageLayout);
        _mode_pending = false;
    }
    if (_restore_pending && !has_error()) {
        layout->restore_view(_restore_view);
        _restore_pending = false;
    }
}

void BookReader::turn_to(int page, bool relative, int step) {
    if (!layout || is_loading() || has_error()) return;
    auto view = layout->capture_view();
    if (!wook::keep_zoom()) view.zoom = 1.0f;
    if (!wook::keep_position()) view.pan_x = view.pan_y = 0.0f;
    if (!_is_cbz) layout->prepare_view(view);
    if (relative) {
        if (step > 0) layout->next_page(step);
        else layout->previous_page(-step);
    } else layout->goto_page(page);
    if (!has_error()) layout->restore_view(view);
    show_status_bar();
    save_progress();
    save_view();
}
void BookReader::previous_page(int n) {
    if (n > 1 && layout) turn_to(layout->current_page() - n, false, 0);
    else turn_to(0, true, -n);
}
void BookReader::next_page(int n) {
    if (!layout || is_loading() || has_error()) return;
    bool spread = _is_cbz && static_cast<CBZPageLayout *>(layout)->view_mode() == 2;
    if (n > 0 && reader_last_visible(layout->current_page(), _total_pages, spread) >= _total_pages - 1) {
        _request_next_issue = true; mark_redraw(); return;
    }
    if (n > 1) turn_to(layout->current_page() + n, false, 0);
    else turn_to(0, true, n);
}
void BookReader::goto_page(int page) { turn_to(page - 1, false, 0); }
void BookReader::zoom_in(float amount) {
    if (!layout || is_loading() || has_error()) return;
    layout->zoom_in(amount); show_status_bar(); save_view();
}
void BookReader::zoom_out(float amount) { zoom_in(-amount); }
void BookReader::zoom_at_point(float delta, float x, float y) {
    if (!layout || is_loading() || has_error()) return;
    if (_is_cbz) static_cast<CBZPageLayout *>(layout)->zoom_at_point(delta, x, y);
    else layout->zoom_in(delta);
    show_status_bar(); save_view();
}
void BookReader::move_page_up(int speed) {
    if (!layout || is_loading() || has_error() || layout->pageFitsHeight()) return;
    layout->move_up(speed); mark_redraw(); save_view();
}
void BookReader::move_page_down(int speed) { move_page_up(-speed); }
void BookReader::move_page_left(int speed) {
    if (!layout || is_loading() || has_error() || layout->pageFitsWidth()) return;
    layout->move_left(speed); mark_redraw(); save_view();
}
void BookReader::move_page_right(int speed) { move_page_left(-speed); }
void BookReader::reset_page() {
    if (!layout || is_loading() || has_error()) return;
    layout->reset(); show_status_bar(); save_view();
}
void BookReader::zoom_max() {
    if (!layout || is_loading() || has_error()) return;
    layout->zoom_max(); show_status_bar(); save_view();
}
bool BookReader::retry() {
    if (!layout || !_open_error.empty() || ( _is_cbz && static_cast<CBZPageLayout *>(layout)->page_count() == 0))
        return false;
    layout->retry_page(); mark_redraw(); return true;
}
void BookReader::switch_page_layout() {
    if (!layout || is_loading() || has_error()) return;
    _currentPageLayout = (BookPageLayout)reader_next_mode((int)_currentPageLayout);
    if (_is_cbz) static_cast<CBZPageLayout *>(layout)->set_view_mode((int)_currentPageLayout);
    else switch_current_page_layout(_currentPageLayout, layout->current_page());
    show_status_bar(); reset_nav_buttons(); save_view();
}
void BookReader::switch_current_page_layout(BookPageLayout mode, int page) {
    delete layout; layout = nullptr;
    _currentPageLayout = mode;
    if (_is_cbz) {
        layout = new CBZPageLayout(book_path.c_str(), page);
        _mode_pending = true;
    } else {
        fz_try(ctx) {
            if (mode == BookPageLayoutLandscape) layout = new LandscapePageLayout(doc, page);
            else if (mode == BookPageLayoutVertical) layout = new VerticalPageLayout(doc, page);
            else layout = new PageLayout(doc, page);
            if (layout) layout->set_bg_path(book_path.c_str());
        }
        fz_catch(ctx) { _open_error = "Не удалось подготовить страницу документа."; }
    }
}

void BookReader::set_notes(const std::string &text) {
    FS_RecursiveMakeDir(NOTES_DIR);
    std::string path = note_file(book_path), temporary = path + ".tmp";
    FILE *file = fopen(temporary.c_str(), "wb");
    if (!file) { notify("Не удалось сохранить заметку."); return; }
    bool ok = fwrite(text.data(), 1, text.size(), file) == text.size();
    if (fclose(file) != 0) ok = false;
    if (!ok || rename(temporary.c_str(), path.c_str()) != 0) {
        remove(temporary.c_str()); notify("Не удалось сохранить заметку."); return;
    }
    _notes = text; _notes_scroll = 0; FreeTextureIfNeeded(&_notes_texture); mark_redraw();
}
void BookReader::scroll_notes(int pixels) {
    _notes_scroll = std::max(0, std::min(_notes_scroll + pixels, std::max(0, _notes_height - 390)));
    mark_redraw();
}

void BookReader::draw_panel(const char *title, const std::string &message, const char *buttons) {
    SDL_DrawRect(RENDERER, 160, 180, 960, 360, configDarkMode ? HINT_COLOUR_DARK : HINT_COLOUR_LIGHT);
    SDL_Color color = configDarkMode ? WHITE : BLACK;
    SDL_DrawText(RENDERER, ROBOTO_30, 190, 210, color, title);
    SDL_DrawTextWrapped(RENDERER, ROBOTO_25, 190, 270, 900, 170, color, message.c_str());
    SDL_DrawText(RENDERER, ROBOTO_25, 190, 490, color, buttons);
}

void BookReader::draw(bool drawHelp, bool drawNotes) {
    _dirty = false;
    const std::string state_error = wook::last_error();
    if (state_error != _shown_state_error) {
        _shown_state_error = state_error;
        if (!state_error.empty()) notify(state_error);
    }
    uint32_t now = SDL_GetTicks();
    _last_nav_visible = configScreenButtons && (int32_t)(_btn_hide_at - now) > 0;
    SDL_ClearScreen(RENDERER, configDarkMode ? BLACK : WHITE);
    SDL_RenderClear(RENDERER);
    if (_is_cbz && layout) {
        auto *cbz = static_cast<CBZPageLayout *>(layout);
        if (cbz->is_enumerating_internal()) {
            if (cbz->is_enumerating()) {
                if (!cbz->is_valid() && cbz->is_first_image_ready()) cbz->apply_first_image();
            } else cbz->finish_enumeration();
        }
        if (!cbz->is_enumerating_internal()) _total_pages = cbz->page_count();
        cbz->poll_prefetch();
    } else if (layout) layout->poll_bg_render();
    restore_pending_view();
    save_progress();
    save_view();
    _overlay_active = drawHelp || drawNotes || !_issue_prompt.empty() || has_error() || is_loading();
    _last_status_visible = status_visible();
    if (layout && layout->has_page()) layout->draw_page();
    SDL_Color color = configDarkMode ? WHITE : BLACK;
    if (has_error()) {
        std::string message = !_open_error.empty() ? _open_error : layout->page_error();
        draw_panel("Ошибка чтения", message, "A — Повторить                         B — Назад");
    } else if (is_loading()) {
        std::string message = _is_cbz ? static_cast<CBZPageLayout *>(layout)->loading_message()
                                     : "Подготовка страницы " + std::to_string(layout->current_page() + 1) + "...";
        SDL_DrawRect(RENDERER, 0, 640, 1280, 80, SDL_MakeColour(0, 0, 0, 210));
        SDL_DrawText(RENDERER, ROBOTO_25, 30, 660, WHITE, message.c_str());
        SDL_DrawText(RENDERER, ROBOTO_20, 1090, 665, WHITE, "B — Назад");
    } else if (!_issue_prompt.empty()) {
        draw_panel("Следующий выпуск", _issue_prompt + "\nПродолжить чтение?",
                   "A — Открыть                           B — Остаться");
    } else if (drawNotes) {
        SDL_DrawRect(RENDERER, 190, 95, 900, 530, configDarkMode ? HINT_COLOUR_DARK : HINT_COLOUR_LIGHT);
        SDL_DrawText(RENDERER, ROBOTO_30, 220, 110, color, "Заметки");
        if (_notes_dark != (bool)configDarkMode) { FreeTextureIfNeeded(&_notes_texture); _notes_dark = configDarkMode; }
        if (!_notes.empty() && !_notes_texture && ROBOTO_20) {
            SDL_Surface *surface = TTF_RenderUTF8_Blended_Wrapped(ROBOTO_20, _notes.c_str(), color, 840);
            if (surface) {
                _notes_height = surface->h;
                _notes_texture = SDL_CreateTextureFromSurface(RENDERER, surface);
                SDL_FreeSurface(surface);
            }
        }
        if (_notes_texture) {
            _notes_scroll = std::max(0, std::min(_notes_scroll, std::max(0, _notes_height - 390)));
            int width = 0; SDL_QueryTexture(_notes_texture, nullptr, nullptr, &width, nullptr);
            int height = std::min(390, _notes_height - _notes_scroll);
            SDL_Rect source = {0, _notes_scroll, width, height}, dest = {220, 165, width, height};
            SDL_RenderCopy(RENDERER, _notes_texture, &source, &dest);
        } else SDL_DrawText(RENDERER, ROBOTO_25, 220, 180, color, _notes.empty()
            ? "Заметок нет. Нажмите A для добавления."
            : "Не удалось показать заметку. Нажмите A для редактора.");
        SDL_DrawText(RENDERER, ROBOTO_20, 220, 580, color, "A — Изменить     ↑/↓ или касание — Прокрутить     B — Закрыть");
    } else if (drawHelp) {
        SDL_DrawRect(RENDERER, 70, 50, 1140, 620, configDarkMode ? HINT_COLOUR_DARK : HINT_COLOUR_LIGHT);
        SDL_DrawText(RENDERER, ROBOTO_30, 100, 75, color, "Управление и настройки чтения");
        SDL_DrawText(RENDERER, ROBOTO_20, 1060, 80, color, "Закрыть");
        SDL_DrawText(RENDERER, ROBOTO_20, 100, 120, color, "Во время чтения:");
        const char *lines[] = {
            "ZL / ZR — Предыдущая / следующая страница", "Левый стик — Прокрутка; правый — Масштаб",
            "L + ZL / R + ZR — Назад / вперёд на 10 страниц", "A — Номер страницы; L — Заметки; X — Строка состояния",
            "Y — Режим: страница / поворот / разворот", "Нажать стик — Сбросить вид; − — Светлая / тёмная тема",
            "Касание краёв — Листать; щипок — Масштаб", "Центр экрана — Это меню; B или + — Закрыть"
        };
        for (int i = 0; i < 8; ++i) {
            const char *line = lines[i];
            if (i == 4 && !_is_cbz) line = "Y — Режим: страница / поворот / по ширине";
            SDL_DrawText(RENDERER, ROBOTO_20, 100, 153 + i * 29, color, line);
        }
        SDL_DrawText(RENDERER, ROBOTO_20, 100, 395, color, "В этом меню:");
        std::string options = std::string("ZL — Сохранять масштаб: ") + (wook::keep_zoom() ? "Да" : "Нет") +
            "     ZR — Позицию: " + (wook::keep_position() ? "Да" : "Нет");
        SDL_DrawText(RENDERER, ROBOTO_20, 100, 425, color, options.c_str());
        SDL_DrawText(RENDERER, ROBOTO_25, 100, 475, color, "A  Страница       L  Заметки       X  Прочитано");
        SDL_DrawText(RENDERER, ROBOTO_25, 100, 535, color, "←  Предыдущий выпуск       →  Следующий выпуск");
        SDL_DrawText(RENDERER, ROBOTO_20, 100, 610, color, "Клавиатура: ←/→, PgUp/PgDn; Ctrl+PgUp/PgDn — Выпуск; Esc — Назад");
    } else if (layout) {
        if (_last_status_visible) {
            char *info = layout->info();
            int width = 0, height = 0;
            if (info && ROBOTO_25) TTF_SizeUTF8(ROBOTO_25, info, &width, &height);
            if (navLandscape()) {
                SDL_DrawRect(RENDERER, 1235, 0, 45, 720, SDL_MakeColour(0, 0, 0, 180));
                if (info) SDL_DrawRotatedText(RENDERER, ROBOTO_25, 90, 1280 - width - (45 - height) / 2,
                                             (720 - height) / 2, WHITE, info);
                StatusBar_DisplayTime(true);
            } else {
                SDL_DrawRect(RENDERER, 0, 0, 1280, 45, SDL_MakeColour(0, 0, 0, 180));
                if (info) SDL_DrawText(RENDERER, ROBOTO_25, (1280 - width) / 2, (45 - height) / 2, WHITE, info);
                StatusBar_DisplayTime(false);
            }
        }
        if (_last_nav_visible) {
            int alpha = _btn_hide_at - now < 600 ? (int)((_btn_hide_at - now) * 200 / 600) : 200;
            SDL_Color background = SDL_MakeColour(0, 0, 0, alpha);
            if (navLandscape()) {
                SDL_DrawRect(RENDERER, 600, 60, 80, 60, background);
                SDL_DrawRect(RENDERER, 600, 600, 80, 60, background);
                SDL_DrawRotatedText(RENDERER, ROBOTO_35, 90, 620, 70, WHITE, "‹");
                SDL_DrawRotatedText(RENDERER, ROBOTO_35, 90, 620, 610, WHITE, "›");
            } else {
                SDL_DrawRect(RENDERER, 20, 320, 80, 80, background);
                SDL_DrawRect(RENDERER, 1180, 320, 80, 80, background);
                SDL_DrawText(RENDERER, ROBOTO_35, 50, 330, WHITE, "‹");
                SDL_DrawText(RENDERER, ROBOTO_35, 1210, 330, WHITE, "›");
            }
        }
    }
    if (!_notice.empty()) {
        if ((int32_t)(_notice_hide_at - now) <= 0) _notice.clear();
        else {
            SDL_DrawRect(RENDERER, 100, 650, 1080, 55, SDL_MakeColour(0, 0, 0, 220));
            SDL_DrawText(RENDERER, ROBOTO_20, 120, 665, WHITE, _notice.c_str());
        }
    }
    SDL_RenderPresent(RENDERER);
}
