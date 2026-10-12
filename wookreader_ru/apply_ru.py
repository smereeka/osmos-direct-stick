#!/usr/bin/env python3
# WookReader Russian localization for the Nintendo Switch.
import json
import pathlib
import sys

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv)>1 else "wookreader-src").resolve()
TRANSLATIONS = json.loads(r'''{"source/menus/book-chooser/MenuChooser.cpp":{"Edit note":"Изменить заметку","Exit":"Выход","Switch Theme":"Сменить тему","Options Menu":"Настройки","Recently Opened":"Недавние","This folder is empty":"Папка пуста","This file is not yet fully supported, and may":"Формат поддерживается не полностью.","cause a system, or app crash.":"Приложение может завершиться с ошибкой.","\"A\" - Read":"A — Читать","\"B\" - Cancel.":"B — Отмена","Option Menu":"Настройки","Folder Color: ":"Цвет папки: ","Book Color: ":"Цвет книги: ","Scroll Speed: ":"Прокрутка: ","Zoom Amount: ":"Шаг масштаба: ","Screen Buttons: ":"Экранные кнопки: ","Status Bar: ":"Строка состояния: ","On":"Вкл.","Off":"Выкл.","Notes:":"Заметки:","A = Edit    B / Y = Close":"A — Изменить    B/Y — Закрыть","No notes yet. Press A to add one.":"Заметок нет. Нажмите A для добавления."},"source/menus/book/BookReader.cpp":{"Error: Failed to load page.":"Ошибка загрузки страницы.","Opening archive...":"Открытие архива...","Error: No images found in archive.":"В архиве нет изображений.","Help Menu:":"Управление:","Stop reading / Close help menu.":"Выход / Закрыть подсказки","Switch to dark/light theme.":"Светлая / тёмная тема","Zoom in/out.":"Увеличить / уменьшить","Page up/down.":"Прокрутить страницу","Change page layout.":"Режим отображения","Keep status bar on.":"Строка состояния","Previous page.":"Предыдущая страница","Next page.":"Следующая страница","Jump to page.":"Перейти к странице","Notes:":"Заметки:","A = Edit    B = Close":"A — Изменить    B — Закрыть","No notes yet. Press A to add one.":"Заметок нет. Нажмите A для добавления."},"source/menus/book/menu_book_reader.cpp":{"Edit note":"Изменить заметку","Go to page":"Номер страницы"},"source/status_bar.c":{"\"+\" - Help":"+ — Помощь"}}''')
COLOR_NAMES = json.loads(r'''["Адвейта","Чёрный","Серо-синий","Синий","Бриз","Коричневый","Кармин","Голубой","Тёмно-голубой","Оранжевый","Зелёный","Серый","Индиго","Пурпурный","Нордик","Оранжевый","Светло-коричн.","Светло-оранж.","Розовый","Красный","Бирюзовый","Фиолетовый","Белый","Яру","Жёлтый"]''')

def main():
    if not (ROOT/"source/main.cpp").is_file():
        raise RuntimeError("WookReader source directory is missing.")
    translated=0
    for rel,strings in TRANSLATIONS.items():
        f=ROOT/rel
        src=f.read_text(encoding="utf-8")
        for en,ru in strings.items():
            old=json.dumps(en, ensure_ascii=False)
            new=json.dumps(ru, ensure_ascii=False)
            if old not in src:
                raise RuntimeError("Missing source literal: "+rel+": "+repr(en))
            src=src.replace(old,new)
            translated+=1
        f.write_text(src,encoding="utf-8")
    f=ROOT/"source/menus/book-chooser/MenuChooser.cpp"
    src=f.read_text(encoding="utf-8")
    marker="  load_config(&chosenFolderColor, &chosenBookColor, &scroll_option,"
    if src.count(marker)!=1: raise RuntimeError("Menu color insertion position changed.")
    src=src.replace(marker,"  const char* colorNamesRU[] = {"+", ".join(json.dumps(x,ensure_ascii=False) for x in COLOR_NAMES)+"};\n\n"+marker,1)
    for idx in ("chosenFolderColor","chosenBookColor"):
        old="colors["+idx+"].c_str()"
        if src.count(old)!=1: raise RuntimeError("Cannot replace color "+idx)
        src=src.replace(old,"colorNamesRU["+idx+"]")
    f.write_text(src,encoding="utf-8")
    utf8_renderer=utf8_size=0
    for p in (ROOT/"source").rglob("*"):
        if p.suffix not in (".c",".cpp",".h",".hpp"): continue
        src=p.read_text(encoding="utf-8")
        k=src.count("TTF_RenderText_")
        n=src.count("TTF_SizeText(")
        if k or n:
            p.write_text(src.replace("TTF_RenderText_","TTF_RenderUTF8_").replace("TTF_SizeText(","TTF_SizeUTF8("),encoding="utf-8")
            utf8_renderer+=k
            utf8_size+=n
    if not utf8_renderer or not utf8_size:
        raise RuntimeError("SDL2_ttf UTF-8 conversion did not apply.")
    mf=ROOT/"Makefile"
    src=mf.read_text(encoding="utf-8")
    if "Makefile.switch" in src:
        mf.write_text(src.replace("Makefile.switch","Makefile.mupdf"),encoding="utf-8")
    print(f"Russian strings: {translated}; theme colors: {len(COLOR_NAMES)}; SDL UTF-8: {utf8_renderer}/{utf8_size}.")

def fix_upstream_compile_errors():
    # WookReader upstream has the implementation but misses the declaration.
    helper=ROOT/"include/helpers/SDL_helper.h"
    h=helper.read_text(encoding="utf-8")
    declaration=("void SDL_DrawTextWrapped(SDL_Renderer *renderer, TTF_Font *font, "
                 "int x, int y, int wrapWidth, int maxH, SDL_Color colour, const char *text);")
    marker="void SDL_DrawTextf("
    if declaration not in h:
        if marker not in h: raise RuntimeError("Cannot insert SDL_DrawTextWrapped declaration.")
        h=h.replace(marker, declaration+"\n"+marker,1)
        helper.write_text(h,encoding="utf-8")
    # The settings are defined in main.cpp, but were not declared to other files.
    conf=ROOT/"include/config.h"
    c=conf.read_text(encoding="utf-8")
    marker="extern bool configDarkMode;"
    if c.count(marker)!=1: raise RuntimeError("Missing configDarkMode declaration.")
    c=c.replace(marker, marker+"\nextern bool configScreenButtons;\nextern bool configStatusBar;",1)
    conf.write_text(c,encoding="utf-8")
    print("Applied upstream build fixes: SDL_DrawTextWrapped + config flags.")

if __name__=="__main__":
    main()
    fix_upstream_compile_errors()
