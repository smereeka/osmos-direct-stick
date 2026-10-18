#!/usr/bin/env python3
"""Install the Russian reader UX and view/error hooks after fix_rar + install_state."""
import argparse
from pathlib import Path
import shutil


def once(text, old, new):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one reader anchor, found {count}: {old[:100]!r}")
    return text.replace(old, new, 1)


def function(text, signature, replacement):
    start = text.index(signature)
    brace = text.index("{", start)
    depth, index = 1, brace + 1
    while depth:
        if text.startswith("//", index):
            index = text.index("\n", index) + 1
            continue
        if text.startswith("/*", index):
            index = text.index("*/", index) + 2
            continue
        if text[index] in "\"'":
            quote = text[index]
            index += 1
            while text[index] != quote:
                index += 2 if text[index] == "\\" else 1
            index += 1
            continue
        depth += (text[index] == "{") - (text[index] == "}")
        index += 1
    return text[:start] + replacement.strip() + text[index:]


def patch_layout(directory):
    header_path = directory / "PageLayout.hpp"
    header = header_path.read_text()
    header = once(header, '#include <string>', '#include <string>\n#include "ReaderView.hpp"\n// WOOK_READER_ENHANCED')
    header = once(header, '    virtual void previous_page(int n);', '''    virtual bool has_page() const { return page_texture && _page_error.empty(); }
    virtual bool is_loading() const { return _bg.active; }
    virtual bool pending_work() const {
        return _bg.active || (_pf_fwd.active && __atomic_load_n(&_pf_fwd.done, __ATOMIC_ACQUIRE)) ||
               (_pf_bwd.active && __atomic_load_n(&_pf_bwd.done, __ATOMIC_ACQUIRE));
    }
    virtual const std::string &page_error() const { return _page_error; }
    virtual int rotation_degrees() const { return 0; }
    virtual ReaderView capture_view() const;
    virtual void restore_view(const ReaderView &view);
    virtual void retry_page();
    void prepare_view(const ReaderView &view) { _next_view = view; _has_next_view = true; }
    virtual void previous_page(int n);''')
    header = once(header, '    virtual bool pageFitsWidth() const  { return page_bounds.x1 * zoom <= viewport.w + 0.5f; }',
                  '    virtual bool pageFitsWidth() const { return visual_width() <= viewport.w + 0.5f; }')
    header = once(header, '    virtual bool pageFitsHeight() const { return page_bounds.y1 * zoom <= viewport.h + 0.5f; }',
                  '    virtual bool pageFitsHeight() const { return visual_height() <= viewport.h + 0.5f; }')
    header = once(header, 'protected:\n', '''protected:
    std::string _page_error;
    virtual bool fit_to_width() const { return false; }
    float visual_width() const { return (rotation_degrees() == 90 ? page_bounds.y1-page_bounds.y0 : page_bounds.x1-page_bounds.x0) * zoom; }
    float visual_height() const { return (rotation_degrees() == 90 ? page_bounds.x1-page_bounds.x0 : page_bounds.y1-page_bounds.y0) * zoom; }
''')
    header = once(header, '    SDL_Rect viewport;', '    SDL_Rect viewport = {0, 0, 1280, 720};')
    header = once(header, '    std::string _bg_path;', '    ReaderView _next_view;\n    bool _has_next_view = false;\n    std::string _bg_path;')
    header_path.write_text(header)
    path = directory / "PageLayout.cpp"
    code = path.read_text().replace('pdf(ctx ? pdf_specifics(ctx, doc) : nullptr)', 'pdf(ctx && doc ? pdf_specifics(ctx, doc) : nullptr)')
    code = function(code, 'void PageLayout::draw_page()', '''void PageLayout::draw_page() {
    if (!page_texture) return;
    float w = (page_bounds.x1-page_bounds.x0)*zoom, h = (page_bounds.y1-page_bounds.y0)*zoom;
    SDL_Rect rect = {(int)(page_center.x-w/2), (int)(page_center.y-h/2), (int)w, (int)h};
    SDL_RenderCopyEx(RENDERER, page_texture, nullptr, &rect, rotation_degrees(), nullptr, SDL_FLIP_NONE);
}''')
    code = function(code, 'void PageLayout::move_page(float x, float y)', '''void PageLayout::move_page(float x, float y) {
    page_center.x = reader_center(page_center.x+x, visual_width(), viewport.w);
    page_center.y = reader_center(page_center.y+y, visual_height(), viewport.h);
}''')
    code = function(code, 'void PageLayout::top_of_page()', '''void PageLayout::top_of_page() {
    page_center.x = reader_restore_pan(0, visual_width(), viewport.w);
    page_center.y = reader_restore_pan(0, visual_height(), viewport.h);
}''')
    code = function(code, 'void PageLayout::reset()', 'void PageLayout::reset() { set_zoom(min_zoom); top_of_page(); }')
    code = function(code, 'void PageLayout::zoom_max()', 'void PageLayout::zoom_max() { set_zoom(max_zoom); }')
    code = once(code, '''void PageLayout::render_page_to_texture(int num, bool reset_zoom)
{
    _current_page''', '''void PageLayout::render_page_to_texture(int num, bool reset_zoom)
{
    _page_error.clear();
    if (pages_count <= 0) { _page_error = "В документе нет страниц."; return; }
    _current_page''')
    code = once(code, '''        std::cout << "Failed to load page bounds " << _current_page << std::endl;
        return;''', '''        _page_error = "Не удалось прочитать страницу " + std::to_string(_current_page + 1) + ".";
        return;''')
    code = once(code, '''        min_zoom = fmin(viewport.w / bounds.x1, viewport.h / bounds.y1);
        max_zoom = fmax(viewport.w / bounds.x1, viewport.h / bounds.y1);
        zoom = min_zoom;''', '''        float width = std::max(1.0f, bounds.x1-bounds.x0), height = std::max(1.0f, bounds.y1-bounds.y0);
        if (rotation_degrees() == 90) std::swap(width, height);
        min_zoom = fit_to_width() ? viewport.w/width : fmin(viewport.w/width, viewport.h/height);
        max_zoom = std::max(min_zoom, (float)fmax(viewport.w/width, viewport.h/height));
        zoom = min_zoom;''')
    code = once(code, '    // Check prefetch cache — instant texture swap with no rendering', '''    if (_has_next_view) {
        zoom = std::max(min_zoom, std::min(min_zoom * std::max(1.0f, _next_view.zoom), max_zoom));
        page_center.x = reader_restore_pan(_next_view.pan_x, visual_width(), viewport.w);
        page_center.y = reader_restore_pan(_next_view.pan_y, visual_height(), viewport.h);
        _has_next_view = false;
    }

    // Check prefetch cache — instant texture swap with no rendering''')
    code = once(code, '''    if (threadCreate(&_bg.thread, bg_thread_entry, this, nullptr, 0x80000, 0x30, 2) != 0)
        return;''', '''    if (threadCreate(&_bg.thread, bg_thread_entry, this, nullptr, 0x80000, 0x30, 2) != 0) {
        _page_error = "Не удалось запустить подготовку страницы. Повторите попытку.";
        return;
    }''')
    code = once(code, '''        threadClose(&_bg.thread);
        return;''', '''        threadClose(&_bg.thread);
        _page_error = "Не удалось запустить подготовку страницы. Повторите попытку.";
        return;''')
    code = once(code, '    _bg.target_page = page;', '    _page_error.clear();\n    _bg.target_page = page;')
    code = once(code, '''            if (new_tex)
            {
                FreeTextureIfNeeded(&page_texture);
                page_texture = new_tex;
            }
        }

        // Current page''', '''            if (new_tex) {
                FreeTextureIfNeeded(&page_texture);
                page_texture = new_tex;
                _page_error.clear();
            } else _page_error = "Не удалось подготовить изображение страницы.";
        } else _page_error = "Не удалось отрисовать страницу " + std::to_string(_current_page + 1) + ".";

        // Current page''')
    code = code.replace('page_texture = SDL_CreateTextureFromSurface(RENDERER, image);', 'page_texture = image ? SDL_CreateTextureFromSurface(RENDERER, image) : nullptr;')
    code = code.replace('page_texture = SDL_CreateTextureFromSurface(RENDERER, img);', 'page_texture = img ? SDL_CreateTextureFromSurface(RENDERER, img) : nullptr;')
    code = once(code, '''void PageLayout::set_zoom(float value)
{
    value''', '''void PageLayout::set_zoom(float value)
{
    _page_error.clear();
    value''')
    # Both synchronous raster paths expose allocation/decode errors persistently.
    code = once(code, '''}

void PageLayout::set_zoom(float value)''', '''    if (!page_texture) _page_error = "Не удалось подготовить страницу " + std::to_string(_current_page + 1) + ".";
}

void PageLayout::set_zoom(float value)''')
    code = once(code, '    move_page(0, 0);', '    if (!_bg.active && !page_texture) _page_error = "Не удалось подготовить изображение страницы.";\n    move_page(0, 0);')
    code += '''
ReaderView PageLayout::capture_view() const {
    return {min_zoom > 0 ? zoom/min_zoom : 1.0f,
            reader_save_pan(page_center.x, visual_width(), viewport.w),
            reader_save_pan(page_center.y, visual_height(), viewport.h)};
}
void PageLayout::restore_view(const ReaderView &view) {
    if (!(min_zoom > 0)) return;
    float ratio = std::isfinite(view.zoom) ? std::max(1.0f, view.zoom) : 1.0f;
    set_zoom(min_zoom * ratio);
    page_center.x = reader_restore_pan(view.pan_x, visual_width(), viewport.w);
    page_center.y = reader_restore_pan(view.pan_y, visual_height(), viewport.h);
}
void PageLayout::retry_page() {
    ReaderView view = capture_view();
    cancel_bg_render();
    cancel_pf_slot(_pf_fwd, &_pf_fwd_tex, &_pf_fwd_page, &_pf_fwd_zoom);
    cancel_pf_slot(_pf_bwd, &_pf_bwd_tex, &_pf_bwd_page, &_pf_bwd_zoom);
    prepare_view(view);
    render_page_to_texture(_current_page, true);
}
'''
    path.write_text(code)
    (directory / 'LandscapePageLayout.hpp').write_text('''#pragma once
#include "PageLayout.hpp"
class LandscapePageLayout : public PageLayout {
public:
    LandscapePageLayout(fz_document *doc, int page);
    int rotation_degrees() const override { return 90; }
};
''')
    (directory / 'LandscapePageLayout.cpp').write_text('''#include "LandscapePageLayout.hpp"
LandscapePageLayout::LandscapePageLayout(fz_document *doc, int page) : PageLayout(doc, page) {
    render_page_to_texture(current_page(), true);
    reset();
}
''')
    (directory / 'VerticalPageLayout.hpp').write_text('''#pragma once
#include "PageLayout.hpp"
class VerticalPageLayout : public PageLayout {
public:
    VerticalPageLayout(fz_document *doc, int page);
protected:
    bool fit_to_width() const override { return true; }
};
''')
    (directory / 'VerticalPageLayout.cpp').write_text('''#include "VerticalPageLayout.hpp"
VerticalPageLayout::VerticalPageLayout(fz_document *doc, int page) : PageLayout(doc, page) {
    render_page_to_texture(current_page(), true);
    reset();
}
''')


def patch_comic(directory):
    path = directory / 'CBZPageLayout.hpp'
    header = path.read_text()
    header = once(header, '    void toggle_spread();', '''    void toggle_spread();
    int view_mode() const { return _spread_mode ? 2 : (_rotation == 90 ? 1 : 0); }
    int rotation_degrees() const override { return _rotation; }
    void set_view_mode(int mode);
    ReaderView capture_view() const override;
    void restore_view(const ReaderView &view) override;
    void retry_page() override;
    bool has_page() const override { return _tex != nullptr; }
    bool is_loading() const override { return _enumerating; }
    bool pending_work() const override;
    std::string loading_message() const;''')
    header = once(header, '    volatile int   _enum_done', '    volatile int   _enum_cancel = 0;\n    volatile int   _enum_stage = 0;\n    std::string _enum_error;\n    volatile int   _enum_done')
    path.write_text(header)
    path = directory / 'CBZPageLayout.cpp'
    code = path.read_text()
    code = code.replace(
        '// stb_image: used for JPEG decoding because devkitPro libjpeg lacks progressive JPEG support',
        '// stb_image supplies fallback decoding when the primary image decoder rejects a stream.')
    code = code.replace(
        '// Fallback: stb_image handles progressive JPEG that turbo may reject',
        '// Fallback for JPEG streams rejected by the primary decoder.')
    code = once(code, '''void CBZPageLayout::do_enumerate()
{
''', '''void CBZPageLayout::do_enumerate()
{
    __atomic_store_n(&_enum_stage, 1, __ATOMIC_RELEASE);
''')
    code = once(code, '&_enum_count, &info);', '&_enum_count, &info, &_enum_cancel);')
    code = once(code, '''            Log_Error("RAR list: " + wookrar::describe(result) + " path=" + _archive_path);
            _page_names.clear();''', '''            Log_Error("RAR list: " + wookrar::describe(result) + " path=" + _archive_path);
            _enum_error = "Не удалось прочитать архив. Проверьте файл и повторите попытку.";
            _page_names.clear();''')
    code = once(code, '''                              &_first_image_name, &fd, &fs, &_enum_count)) {
            _page_names.clear();''', '''                              &_first_image_name, &fd, &fs, &_enum_count)) {
            _enum_error = "Не удалось прочитать архив. Проверьте файл и повторите попытку.";
            _page_names.clear();''')
    code = once(code, '''    if (have_names && !_page_names.empty()) {
        _first_image_name''', '''    if (have_names && !_page_names.empty()) {
        __atomic_store_n(&_enum_stage, 2, __ATOMIC_RELEASE);
        _first_image_name''')
    code = once(code, '        extract_image_cached(_archive_path.c_str(), _page_names[0], &fd, &fs);',
                  '        extract_image_cached(_archive_path.c_str(), _page_names[0], &fd, &fs, &_enum_cancel);')
    code = once(code, '    __atomic_store_n(&_enum_done, 1, __ATOMIC_RELEASE);',
                  '    __atomic_store_n(&_enum_stage, 3, __ATOMIC_RELEASE);\n    __atomic_store_n(&_enum_done, 1, __ATOMIC_RELEASE);')
    code = once(code, '''    if (_page_names.empty())
    {
        Log_Error(std::string("CBZPageLayout: no image files found in archive: ") + _archive_path);''', '''    if (_page_names.empty())
    {
        _valid = false;
        _page_error = _enum_error.empty() ? "В архиве нет поддерживаемых изображений." : _enum_error;
        Log_Error(std::string("CBZPageLayout: no image files found in archive: ") + _archive_path);''')
    code = once(code, '''CBZPageLayout::~CBZPageLayout()
{
''', '''CBZPageLayout::~CBZPageLayout()
{
    __atomic_store_n(&_enum_cancel, 1, __ATOMIC_RELEASE);
''')
    code = once(code, '''            Log_Error("RAR list (sync): " + wookrar::describe(result) + " path=" + path);
            return;''', '''            Log_Error("RAR list (sync): " + wookrar::describe(result) + " path=" + path);
            _page_error = "Не удалось прочитать архив.";
            return;''')
    code = once(code, '''    } else if (!enumerate_images(path, _page_names)) {
        return;''', '''    } else if (!enumerate_images(path, _page_names)) {
        _page_error = "Не удалось прочитать архив.";
        return;''')
    code = once(code, '''        Log_Error(std::string("CBZPageLayout: no image files found in archive: ") + path);
        return;''', '''        Log_Error(std::string("CBZPageLayout: no image files found in archive: ") + path);
        _page_error = "В архиве нет поддерживаемых изображений.";
        return;''')
    code = code.replace('    _valid = true;', '    _valid = (_tex != nullptr && _page_error.empty());')
    code = once(code, '''void CBZPageLayout::load_page_texture(int page_num, bool reset_zoom)
{
''', '''void CBZPageLayout::load_page_texture(int page_num, bool reset_zoom)
{
    _valid = false;
    _page_error.clear();
''')
    code = once(code, '''        Log_Error("CBZPageLayout: failed to load page " + std::to_string(_current_page));
        return;''', '''        Log_Error("CBZPageLayout: failed to load page " + std::to_string(_current_page));
        _page_error = "Не удалось загрузить страницу " + std::to_string(_current_page + 1) + ". Повторите попытку.";
        return;''')
    code = once(code, '''            Log_Error("CBZPageLayout: right page failed, showing single");''', '''            _page_error = "Не удалось загрузить правую страницу разворота. Повторите попытку.";
            Log_Error("CBZPageLayout: right page failed, showing single");''')
    code = once(code, '    // Start async prefetch: N+1 (slot 0/core 1), N-1 (slot 1/core 2), N+2 (slot 2/core 3)',
                  '    _valid = (_tex != nullptr && _page_error.empty());\n\n    // Start async prefetch: N+1 (slot 0/core 1), N-1 (slot 1/core 2), N+2 (slot 2/core 3)')
    code = function(code, 'void CBZPageLayout::toggle_spread()', '''void CBZPageLayout::toggle_spread() {
    set_view_mode(reader_next_mode(view_mode()));
}''')
    code = function(code, 'void CBZPageLayout::clamp_center()', '''void CBZPageLayout::clamp_center() {
    float width = (_rotation == 90 ? _tex_h : _tex_w) * _zoom;
    float height = (_rotation == 90 ? _tex_w : _tex_h) * _zoom;
    _cx = reader_center(_cx, width, _viewport.w);
    _cy = reader_center(_cy, height, _viewport.h);
}''')
    for direction, update in [('up', '_cy += scroll_speed;'), ('down', '_cy -= scroll_speed;'),
                              ('left', '_cx -= scroll_speed;'), ('right', '_cx += scroll_speed;')]:
        code = function(code, f'void CBZPageLayout::move_{direction}(int scroll_speed)',
                        f'void CBZPageLayout::move_{direction}(int scroll_speed) {{ {update} clamp_center(); }}')
    code = function(code, 'void CBZPageLayout::zoom_max()', 'void CBZPageLayout::zoom_max() { apply_zoom(_max_zoom); }')
    code += '''
void CBZPageLayout::set_view_mode(int mode) {
    if (!_valid || _enumerating || mode < 0 || mode > 2 || mode == view_mode()) return;
    _spread_mode = mode == 2;
    _rotation = mode == 1 ? 90 : 0;
    cancel_prefetch();
    load_page_texture(_current_page, true);
}
ReaderView CBZPageLayout::capture_view() const {
    float width = (_rotation == 90 ? _tex_h : _tex_w)*_zoom;
    float height = (_rotation == 90 ? _tex_w : _tex_h)*_zoom;
    return {_min_zoom > 0 ? _zoom/_min_zoom : 1.0f,
            reader_save_pan(_cx, width, _viewport.w), reader_save_pan(_cy, height, _viewport.h)};
}
void CBZPageLayout::restore_view(const ReaderView &view) {
    if (!_tex) return;
    float ratio = std::isfinite(view.zoom) ? std::max(1.0f, view.zoom) : 1.0f;
    apply_zoom(_min_zoom * ratio);
    float width = (_rotation == 90 ? _tex_h : _tex_w)*_zoom;
    float height = (_rotation == 90 ? _tex_w : _tex_h)*_zoom;
    _cx = reader_restore_pan(view.pan_x, width, _viewport.w);
    _cy = reader_restore_pan(view.pan_y, height, _viewport.h);
}
void CBZPageLayout::retry_page() {
    ReaderView view = capture_view();
    cancel_prefetch();
    load_page_texture(_current_page, true);
    if (_valid) restore_view(view);
}
bool CBZPageLayout::pending_work() const {
    if (_enumerating) return true;
    for (int i = 0; i < N_PF; ++i)
        if (_pf_active[i] && __atomic_load_n(&_pf_ok[i], __ATOMIC_ACQUIRE)) return true;
    return false;
}
std::string CBZPageLayout::loading_message() const {
    int stage = __atomic_load_n(&_enum_stage, __ATOMIC_ACQUIRE);
    int found = __atomic_load_n(&_enum_count, __ATOMIC_ACQUIRE);
    if (stage == 2) return "Распаковка первой страницы... Найдено страниц: " + std::to_string(found);
    if (stage == 3) return "Подготовка изображения...";
    return "Чтение списка страниц... Найдено: " + std::to_string(found);
}
'''
    path.write_text(code)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    directory = args.source / 'source/menus/book'
    here = Path(__file__).resolve().parent
    if not (directory / 'WookLibrary.hpp').exists():
        raise SystemExit('Run install_state.py before enhance_reader.py')
    if 'WOOK_READER_ENHANCED' in (directory / 'PageLayout.hpp').read_text():
        if 'WOOK_READER_UX_COMPLETE' not in (directory / 'BookReader.cpp').read_text():
            raise SystemExit('Incomplete reader patch: rebuild from a fresh source tree')
        print('Reader UX already installed')
        return
    patch_layout(directory)
    patch_comic(directory)
    for source, destination in [('ReaderBook.hpp', 'BookReader.hpp'), ('ReaderBook.cpp', 'BookReader.cpp'),
                                ('ReaderMenu.cpp', 'menu_book_reader.cpp'), ('ReaderView.hpp', 'ReaderView.hpp')]:
        shutil.copyfile(here / source, directory / destination)
    print('Installed Russian reader controls, view state, errors, notes and collection navigation')


if __name__ == '__main__':
    main()
