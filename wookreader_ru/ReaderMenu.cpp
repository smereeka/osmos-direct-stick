extern "C" {
#include "menu_book_reader.h"
#include "common.h"
#include "config.h"
#include "SDL_helper.h"
}
#include "BookReader.hpp"
#include "WookLibrary.hpp"
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <string>

static bool reader_keyboard(const char *title, const std::string &initial,
                            bool number, std::string &text) {
    SwkbdConfig keyboard;
    Result result = swkbdCreate(&keyboard, 0);
    if (R_FAILED(result)) return false;
    swkbdConfigSetType(&keyboard, number ? SwkbdType_NumPad : SwkbdType_Normal);
    swkbdConfigSetHeaderText(&keyboard, title);
    swkbdConfigSetStringLenMax(&keyboard, number ? 6 : 2000);
    swkbdConfigSetStringLenMin(&keyboard, number ? 1 : 0);
    swkbdConfigSetBlurBackground(&keyboard, 1);
    swkbdConfigSetInitialText(&keyboard, initial.c_str());
    char buffer[8192] = {};
    result = swkbdShow(&keyboard, buffer, sizeof(buffer));
    swkbdClose(&keyboard);
    if (R_FAILED(result)) return false;
    text = buffer;
    return true;
}

void Menu_OpenBook(char *path, int scroll_speed, float zoom_amount) {
    std::string active_path = path ? path : "";
    int result = 0;
    BookReader *reader = new BookReader(active_path.c_str(), &result);
    bool helpMenu = false, notesMenu = false;
    std::string pending_issue;
    float previous_x = 0, previous_y = 0, start_x = 0, start_y = 0;
    float previous_distance = 0;
    int touch_prev_count = 0;
    bool dragging = false, pinching = false, touch_zoomed = false, left_chord = false;
    hidInitializeTouchScreen();
    padConfigureInput(1, HidNpadStyleSet_NpadStandard);
    PadState pad;
    padInitializeDefault(&pad);
    padUpdate(&pad);

    auto reopen = [&](const std::string &next_path) {
        // Copy first: next_path can refer to pending_issue, which is reset below.
        std::string next = next_path;
        delete reader;
        active_path = next;
        result = 0;
        reader = new BookReader(active_path.c_str(), &result);
        helpMenu = notesMenu = false;
        pending_issue.clear();
        touch_prev_count = 0;
        dragging = pinching = false;
        padUpdate(&pad);
    };
    auto ask_issue = [&](const std::string &next) {
        if (next.empty()) { reader->notify("В этой коллекции больше нет выпусков в этом направлении."); return; }
        pending_issue = next;
        reader->set_issue_prompt(wook::title(next));
        helpMenu = notesMenu = false;
    };

    while (appletMainLoop()) {
        SDL_TextCache_NextFrame();
        padUpdate(&pad);
        u64 kDown = padGetButtonsDown(&pad), kHeld = padGetButtons(&pad), kUp = padGetButtonsUp(&pad);
        int keyboard_issue = 0;
        float keyboard_zoom = 0;
        SDL_Event event;
        while (SDL_PollEvent(&event)) {
            if (event.type == SDL_WINDOWEVENT) reader->mark_redraw();
            if (event.type != SDL_KEYDOWN) continue;
            const SDL_Keycode key = event.key.keysym.sym;
            bool control = (event.key.keysym.mod & KMOD_CTRL) != 0;
            if (control && (key == SDLK_PAGEDOWN || key == SDLK_PAGEUP)) {
                keyboard_issue = key == SDLK_PAGEDOWN ? 1 : -1;
            } else if (key == SDLK_LEFT || key == SDLK_PAGEUP) kDown |= HidNpadButton_Left;
            else if (key == SDLK_RIGHT || key == SDLK_PAGEDOWN) kDown |= HidNpadButton_Right;
            else if (key == SDLK_UP) kDown |= HidNpadButton_Up;
            else if (key == SDLK_DOWN) kDown |= HidNpadButton_Down;
            else if (key == SDLK_ESCAPE) kUp |= HidNpadButton_B;
            else if (key == SDLK_RETURN || key == SDLK_KP_ENTER) kDown |= HidNpadButton_A;
            else if (key == SDLK_y) kDown |= HidNpadButton_Y;
            else if (key == SDLK_x) kDown |= HidNpadButton_X;
            else if (key == SDLK_n) kUp |= HidNpadButton_L;
            else if (key == SDLK_h || key == SDLK_F1) kDown |= HidNpadButton_Plus;
            else if (key == SDLK_EQUALS || key == SDLK_PLUS || key == SDLK_KP_PLUS) keyboard_zoom += zoom_amount;
            else if (key == SDLK_MINUS || key == SDLK_KP_MINUS) keyboard_zoom -= zoom_amount;
            reader->mark_redraw();
        }
        HidTouchScreenState state = {};
        hidGetTouchScreenStates(&state, 1);
        HidAnalogStickState stick_l = padGetStickPos(&pad, 0);
        HidAnalogStickState stick_r = padGetStickPos(&pad, 1);
        const int deadzone = 4000;
        bool analog_active = std::abs(stick_l.x) > deadzone || std::abs(stick_l.y) > deadzone ||
                             std::abs(stick_r.y) > deadzone;
        bool reader_input_active = kDown || kUp || kHeld || state.count || touch_prev_count || analog_active;
        if (reader_input_active) reader->mark_redraw();
        if (kDown || (state.count && !touch_prev_count)) reader->reset_nav_buttons();
        bool tap = false;
        if (state.count == 1) {
            float x = (float)state.touches[0].x, y = (float)state.touches[0].y;
            if (!touch_prev_count) {
                start_x = x; start_y = y; dragging = false; pinching = false;
                touch_zoomed = reader->is_zoomed();
            } else if (touch_prev_count == 1) {
                float dx = x - previous_x, dy = y - previous_y;
                if (std::hypot(x - start_x, y - start_y) > 18) dragging = true;
                if (dragging && notesMenu) reader->scroll_notes((int)-std::round(dy));
                else if (dragging && !helpMenu && pending_issue.empty() && !reader->has_error()) {
                    reader->move_page_right((int)std::round(dx));
                    reader->move_page_up((int)std::round(dy));
                }
            }
            previous_x = x; previous_y = y;
        } else if (state.count == 2) {
            float x0 = state.touches[0].x, y0 = state.touches[0].y;
            float x1 = state.touches[1].x, y1 = state.touches[1].y;
            float distance = std::hypot(x1 - x0, y1 - y0);
            if (touch_prev_count == 2 && !helpMenu && !notesMenu && pending_issue.empty() && !reader->has_error())
                reader->zoom_at_point((distance - previous_distance) * 0.002f, (x0 + x1) / 2, (y0 + y1) / 2);
            previous_distance = distance;
            pinching = dragging = true;
        } else if (!state.count && touch_prev_count == 1) {
            tap = !dragging && !pinching;
            if (dragging && !pinching && !touch_zoomed && !helpMenu && !notesMenu &&
                pending_issue.empty() && !reader->has_error()) {
                float dx = previous_x - start_x, dy = previous_y - start_y;
                if (!reader->navLandscape() && std::abs(dx) > 180 && std::abs(dx) > std::abs(dy) * 1.5f) {
                    if (dx < 0) reader->next_page(1); else reader->previous_page(1);
                } else if (reader->navLandscape() && std::abs(dy) > 180 && std::abs(dy) > std::abs(dx) * 1.5f) {
                    if (dy < 0) reader->previous_page(1); else reader->next_page(1);
                }
            }
        }
        touch_prev_count = state.count;
        if (kDown & HidNpadButton_L) left_chord = false;

        bool back = (kUp & HidNpadButton_B) != 0;
        if (tap && reader->is_loading() && start_x > 1000 && start_y > 630) back = true;
        if (reader->has_error()) {
            if (tap && start_y > 450) { if (start_x < 640) kDown |= HidNpadButton_A; else back = true; }
            if (back) break;
            if (kDown & HidNpadButton_A) { if (!reader->retry()) reopen(active_path); }
        } else if (!pending_issue.empty()) {
            if (tap && start_y > 450) { if (start_x < 640) kDown |= HidNpadButton_A; else back = true; }
            if (back) { pending_issue.clear(); reader->set_issue_prompt(""); }
            else if (kDown & HidNpadButton_A) reopen(pending_issue);
        } else if (notesMenu) {
            if (tap && start_y > 560) { if (start_x < 650) kDown |= HidNpadButton_A; else back = true; }
            if (back || (kDown & HidNpadButton_Plus)) notesMenu = false;
            else {
                if (kDown & HidNpadButton_Up) reader->scroll_notes(-120);
                if (kDown & HidNpadButton_Down) reader->scroll_notes(120);
                if (std::abs(stick_l.y) > deadzone) reader->scroll_notes((int)(-stick_l.y / 32767.0f * 12));
                if (kDown & HidNpadButton_A) {
                    std::string text;
                    if (reader_keyboard("Изменить заметку", reader->notes(), false, text)) reader->set_notes(text);
                    padUpdate(&pad); touch_prev_count = 0; reader->mark_redraw();
                }
            }
        } else if (helpMenu) {
            if (tap) {
                if (start_x > 1030 && start_y < 120) back = true;
                else if (start_y >= 415 && start_y < 460) kDown |= start_x < 640 ? HidNpadButton_ZL : HidNpadButton_ZR;
                else if (start_y >= 470 && start_y < 525) kDown |= start_x < 400 ? HidNpadButton_A :
                    (start_x < 720 ? HidNpadButton_L : HidNpadButton_X);
                else if (start_y >= 525 && start_y < 590) keyboard_issue = start_x < 640 ? -1 : 1;
                else if (start_y < 50 || start_y > 670) back = true;
            }
            if (back || (kDown & HidNpadButton_Plus)) helpMenu = false;
            else if ((kDown & HidNpadButton_L) || (kUp & HidNpadButton_L)) { notesMenu = true; helpMenu = false; }
            else if (kDown & HidNpadButton_X) reader->mark_finished();
            else if (kDown & HidNpadButton_A) {
                std::string text;
                if (reader_keyboard("Номер страницы", "", true, text)) reader->goto_page(std::atoi(text.c_str()));
                helpMenu = false; padUpdate(&pad); touch_prev_count = 0;
            }
            if (kDown & HidNpadButton_ZL) wook::set_keep_zoom(!wook::keep_zoom());
            if (kDown & HidNpadButton_ZR) wook::set_keep_position(!wook::keep_position());
            if (kDown & HidNpadButton_Left) keyboard_issue = -1;
            if (kDown & HidNpadButton_Right) keyboard_issue = 1;
            if (keyboard_issue) ask_issue(keyboard_issue > 0 ? wook::next_book(active_path) : wook::previous_book(active_path));
        } else {
            if (back) break;
            if (tap && !reader->is_loading()) {
                if (reader->navLandscape()) {
                    if (start_y < 150) reader->previous_page(1);
                    else if (start_y > 570) reader->next_page(1);
                    else helpMenu = true;
                } else {
                    if (start_x < 280) reader->previous_page(1);
                    else if (start_x > 1000) reader->next_page(1);
                    else helpMenu = true;
                }
            }
            if (kDown & HidNpadButton_ZL) {
                int amount = kHeld & HidNpadButton_L ? 10 : 1;
                left_chord |= amount == 10;
                reader->previous_page(amount);
            }
            if (kDown & HidNpadButton_ZR) reader->next_page(kHeld & HidNpadButton_R ? 10 : 1);
            if (kDown & (HidNpadButton_Left | HidNpadButton_LeftSL)) reader->previous_page(kDown & HidNpadButton_LeftSL ? 10 : 1);
            if (kDown & (HidNpadButton_Right | HidNpadButton_LeftSR)) reader->next_page(kDown & HidNpadButton_LeftSR ? 10 : 1);
            if (reader->navLandscape()) {
                if (kDown & HidNpadButton_Up) reader->previous_page(1);
                if (kDown & HidNpadButton_Down) reader->next_page(1);
            } else {
                if (kDown & HidNpadButton_Up) reader->zoom_in(zoom_amount);
                if (kDown & HidNpadButton_Down) reader->zoom_out(zoom_amount);
            }
            if (std::abs(stick_l.x) > deadzone) reader->move_page_left((int)(stick_l.x / 32767.0f * scroll_speed * 2));
            if (std::abs(stick_l.y) > deadzone) reader->move_page_up((int)(stick_l.y / 32767.0f * scroll_speed * 2));
            if (std::abs(stick_r.y) > deadzone) reader->zoom_in(stick_r.y / 32767.0f * zoom_amount * 0.07f);
            if (keyboard_zoom) reader->zoom_in(keyboard_zoom);
            if (kDown & (HidNpadButton_StickL | HidNpadButton_StickR)) reader->reset_page();
            if (kDown & HidNpadButton_Y) reader->switch_page_layout();
            if (kDown & HidNpadButton_X) reader->permStatusBar = !reader->permStatusBar;
            if (kUp & HidNpadButton_Minus) { configDarkMode = !configDarkMode; reader->retry(); }
            if ((kUp & HidNpadButton_L) && !left_chord && !reader->is_loading()) notesMenu = true;
            if (kDown & HidNpadButton_A) {
                std::string text;
                if (!reader->is_loading() && reader_keyboard("Номер страницы", "", true, text)) reader->goto_page(std::atoi(text.c_str()));
                padUpdate(&pad); touch_prev_count = 0;
            }
            if (kDown & HidNpadButton_Plus) helpMenu = true;
            if (keyboard_issue) ask_issue(keyboard_issue > 0 ? wook::next_book(active_path) : wook::previous_book(active_path));
        }
        if (reader->take_next_issue_request()) {
            wook::set_finished(active_path, true);
            ask_issue(wook::next_book(active_path));
        }
        if (reader_input_active) reader->mark_redraw();
        wook::maybe_flush();
        // WOOK_POWER_READER_DRAW
        reader->draw(helpMenu, notesMenu);
    }
    delete reader;
    wook::flush();
}
