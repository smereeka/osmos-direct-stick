#ifndef WOOK_READER_BOOK_READER_HPP
#define WOOK_READER_BOOK_READER_HPP

#include <mupdf/pdf.h>
#include <string>
#include "PageLayout.hpp"
#include "CBZPageLayout.hpp"
#include "ReaderView.hpp"
#include <switch.h>

enum BookPageLayout { BookPageLayoutPortrait, BookPageLayoutLandscape, BookPageLayoutVertical };

class BookReader {
public:
    BookReader(const char *path, int *result);
    ~BookReader();
    bool permStatusBar = false;
    void previous_page(int n);
    void next_page(int n);
    void goto_page(int page_1indexed);
    void zoom_in(float amount);
    void zoom_out(float amount);
    void zoom_at_point(float delta, float px, float py);
    void move_page_up(int speed);
    void move_page_down(int speed);
    void move_page_left(int speed);
    void move_page_right(int speed);
    void reset_page();
    void zoom_max();
    void switch_page_layout();
    void draw(bool help, bool notes = false);
    void reset_nav_buttons();
    const std::string &notes() const { return _notes; }
    void set_notes(const std::string &text);
    void scroll_notes(int pixels);
    BookPageLayout currentPageLayout() const { return _currentPageLayout; }
    bool navLandscape() const;
    bool is_loading() const;
    bool has_error() const;
    bool retry();
    bool is_zoomed() const;
    bool needs_redraw() const;
    bool status_visible() const;
    void mark_redraw() { _dirty = true; }
    void mark_finished();
    void save_session();
    bool take_next_issue_request();
    void set_issue_prompt(const std::string &title) { _issue_prompt = title; mark_redraw(); }
    void notify(const std::string &message);

private:
    void show_status_bar();
    void save_progress();
    void save_view();
    void switch_current_page_layout(BookPageLayout mode, int page);
    void turn_to(int page, bool relative, int step);
    void restore_pending_view();
    void draw_panel(const char *title, const std::string &message, const char *buttons);
    fz_document *doc = nullptr;
    PageLayout *layout = nullptr;
    BookPageLayout _currentPageLayout = BookPageLayoutPortrait;
    std::string book_name, book_path, _notes, _open_error, _issue_prompt, _notice;
    std::string _shown_state_error;
    bool _is_cbz = false;
    int _total_pages = 0, _saved_page = -1;
    bool _restore_pending = false, _mode_pending = false, _request_next_issue = false;
    ReaderView _restore_view;
    ReaderView _last_saved_view;
    int _last_saved_mode = -1;
    uint32_t _status_hide_at = 0, _btn_hide_at = 0, _notice_hide_at = 0;
    bool _dirty = true, _last_status_visible = false, _last_nav_visible = false;
    bool _overlay_active = false;
    SDL_Texture *_notes_texture = nullptr;
    int _notes_height = 0, _notes_scroll = 0;
    bool _notes_dark = false;
};

#endif
