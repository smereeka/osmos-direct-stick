#!/usr/bin/env python3
"""Install persistent UTF-8 library state and common Russian UI typography."""
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
package = Path(__file__).resolve().parent
target = root / "source/menus/book"
for name in ("WookLibrary.hpp", "WookLibrary.cpp"):
    shutil.copyfile(package / name, target / name)
helper = root / "source/helpers/SDL_helper.c"
source = helper.read_text(encoding="utf-8")
old = """\tint textY = y - (imageH * 0.85) - yTextOffset; // 0.85 was retreived from trail and error when trying to center the text with the image.
\tSDL_DrawText(RENDERER, ROBOTO_20, textX, textY, textColor, text);"""
new = """\tint textY = imageY + (imageH - textHeight) / 2 - yTextOffset;
\tSDL_DrawText(renderer, font, textX, textY, textColor, text);"""
assert source.count(old) == 1, "Unexpected button prompt implementation"
helper.write_text(source.replace(old, new, 1), encoding="utf-8")
main = root / "source/main.cpp"
source = main.read_text(encoding="utf-8")
marker = '  Log_Write("WOOK RU2 COVERFIX1: isolated MuPDF JPEG and TurboJPEG backends");'
assert source.count(marker) == 1, "Missing cover-fix startup marker"
source = source.replace(marker, marker + '\n  Log_Write("WOOK RU3: UTF-8 library, collections, reader improvements and power saving");', 1)
main.write_text(source, encoding="utf-8")
makefile = root / "Makefile"
source = makefile.read_text(encoding="utf-8")
old = """#  - jpeg: НУЖЕН здесь. SDL2_image (IMG_jpg.c) требует системный libjpeg
#    (jpeg_CreateCompress и др.). Наша libmupdf.a содержит внутренний libjpeg,
#    но его объектные файлы уже зафиксированы в архиве — повторной коллизии
#    не будет, так как линкер берёт из libmupdf.a первым."""
new = """#  - jpeg / turbojpeg: системные JPEG API используют ABI 62. Встроенный
#    в MuPDF IJG 9 использует ABI 90 и изолируется скриптом isolate_jpeg.py
#    после сборки libmupdf.a: все его определения и ссылки получают префикс
#    wookmupdf_. Один порядок линковки не предотвращает эту ABI-коллизию."""
assert source.count(old) == 1, "Unexpected upstream JPEG link comment"
makefile.write_text(source.replace(old, new, 1), encoding="utf-8")
print("Installed full-path UTF-8 library state and consistent button text metrics.")

