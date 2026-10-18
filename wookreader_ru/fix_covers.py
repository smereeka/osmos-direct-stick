#!/usr/bin/env python3
"""Add bounded cover diagnostics after fix_rar.py.

JPEG symbol isolation is performed separately on the built MuPDF archive.
This patch preserves cover selection, cached image bytes, image scaling, and
the existing memory limits. Diagnostics run only during a cover job, never
while a placeholder is drawn each frame.
"""
from pathlib import Path
import sys


def once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one upstream match, got {count}")
    return text.replace(old, new, 1)


root = Path(sys.argv[1] if len(sys.argv) > 1 else "wookreader-src")
chooser = root / "source/menus/book-chooser/MenuChooser.cpp"
main = root / "source/main.cpp"
c = chooser.read_text(encoding="utf-8")
m = main.read_text(encoding="utf-8")

start = c.index("static unsigned char* extract_cbz_cover_pixels(")
end = c.index("// Extract cover from a PDF/EPUB", start)
cover = c[start:end]

cover = once(cover, r'''  if (is_jpeg) {
    tjhandle tj = tjInitDecompress();
    if (tj) {
      int subsamp = 0, cs = 0;
      if (tjDecompressHeader3(tj, d, (unsigned long)cover_data.size(),
                              &src_w, &src_h, &subsamp, &cs) == 0 &&
          src_w > 0 && src_h > 0) {
        int iw = src_w, ih = src_h, factorCount = 0;
        const tjscalingfactor* factors = tjGetScalingFactors(&factorCount);
        for (int k = 0; factors && k < factorCount; k++) {
          int fw = TJSCALED(src_w, factors[k]);
          int fh = TJSCALED(src_h, factors[k]);
          if (fw >= tw && fh >= th && (uint64_t)fw * fh < (uint64_t)iw * ih) {
            iw = fw; ih = fh;
          }
        }
        if ((uint64_t)iw * ih <= 12000000ULL) {
          src_px = (unsigned char*)malloc((size_t)iw * ih * 4);
          if (src_px && tjDecompress2(tj, d, (unsigned long)cover_data.size(),
                                      src_px, iw, 0, ih, TJPF_RGBA,
                                      TJFLAG_FASTDCT) == 0) {
            src_w = iw; src_h = ih;
          } else {
            free(src_px); src_px = nullptr;
          }
        }
      }
      tjDestroy(tj);
    }
  }
''', r'''  if (is_jpeg) {
    tjhandle tj = tjInitDecompress();
    if (tj) {
      int subsamp = 0, cs = 0;
      if (tjDecompressHeader3(tj, d, (unsigned long)cover_data.size(),
                              &src_w, &src_h, &subsamp, &cs) == 0 &&
          src_w > 0 && src_h > 0) {
        int iw = src_w, ih = src_h, factorCount = 0;
        const tjscalingfactor* factors = tjGetScalingFactors(&factorCount);
        for (int k = 0; factors && k < factorCount; k++) {
          int fw = TJSCALED(src_w, factors[k]);
          int fh = TJSCALED(src_h, factors[k]);
          if (fw >= tw && fh >= th && (uint64_t)fw * fh < (uint64_t)iw * ih) {
            iw = fw; ih = fh;
          }
        }
        if ((uint64_t)iw * ih <= 12000000ULL) {
          src_px = (unsigned char*)malloc((size_t)iw * ih * 4);
          if (src_px && tjDecompress2(tj, d, (unsigned long)cover_data.size(),
                                      src_px, iw, 0, ih, TJPF_RGBA,
                                      TJFLAG_FASTDCT) == 0) {
            src_w = iw; src_h = ih;
          } else {
            Log_Error(std::string("cover JPEG decode failed: ") +
                      (src_px ? tjGetErrorStr2(tj) : "pixel allocation failed") +
                      " source=" + std::to_string(src_w) + "x" + std::to_string(src_h) +
                      " scaled=" + std::to_string(iw) + "x" + std::to_string(ih) +
                      " path=" + path);
            free(src_px); src_px = nullptr;
          }
        } else {
          Log_Error("cover size limit: decoder=turbo source=" +
                    std::to_string(src_w) + "x" + std::to_string(src_h) +
                    " scaled=" + std::to_string(iw) + "x" + std::to_string(ih) +
                    " limit_pixels=12000000 path=" + path);
        }
      } else {
        Log_Error(std::string("cover JPEG header failed: ") + tjGetErrorStr2(tj) +
                  " dimensions=" + std::to_string(src_w) + "x" + std::to_string(src_h) +
                  " path=" + path);
      }
      tjDestroy(tj);
    } else {
      Log_Error(std::string("cover JPEG init failed: ") + tjGetErrorStr() + " path=" + path);
    }
  }
''', "JPEG initialization and decode diagnostics")

cover = once(cover, r'''      return nullptr; // display book icon instead of running out of memory
''', r'''      Log_Error("cover size limit: decoder=stb source=" +
                std::to_string(iw) + "x" + std::to_string(ih) +
                " limit_pixels=12000000 path=" + path);
      return nullptr; // display book icon instead of running out of memory
''', "fallback pixel limit diagnostic")

cover = once(cover, "  if (!src_px) return nullptr;", r'''  if (!src_px) {
    const char* reason = stbi_failure_reason();
    Log_Error(std::string("cover image decode failed: ") + (reason ? reason : "unknown") +
              " bytes=" + std::to_string(cover_data.size()) + " path=" + path);
    return nullptr;
  }''', "fallback decoder diagnostic")

cover = once(cover, "    SDL_BlitScaled(src_surf, NULL, dst_surf, NULL);", r'''    if (SDL_BlitScaled(src_surf, NULL, dst_surf, NULL) != 0) {
      Log_Error(std::string("cover scale failed: ") + SDL_GetError() + " path=" + path);
    }''', "surface scaling diagnostic")

cover = once(cover, r'''    if (result) { memcpy(result, dst_surf->pixels, (size_t)dw * dh * 4); }
    *out_iw = dw; *out_ih = dh;
  }
  SDL_FreeSurface(dst_surf);''', r'''    if (result) { memcpy(result, dst_surf->pixels, (size_t)dw * dh * 4); }
    else {
      Log_Error("cover pixel allocation failed: dimensions=" + std::to_string(dw) +
                "x" + std::to_string(dh) + " path=" + path);
    }
    *out_iw = dw; *out_ih = dh;
  } else {
    Log_Error(std::string("cover surface failed: ") + SDL_GetError() +
              " source=" + std::to_string(src_w) + "x" + std::to_string(src_h) +
              " target=" + std::to_string(dw) + "x" + std::to_string(dh) +
              " path=" + path);
  }
  SDL_FreeSurface(dst_surf);''', "surface and final pixel allocation diagnostics")
c = c[:start] + cover + c[end:]

c = once(c,
         "static SDL_Texture* pixels_to_texture(unsigned char* pixels, int iw, int ih) {",
         "static SDL_Texture* pixels_to_texture(unsigned char* pixels, int iw, int ih, const char* path) {",
         "texture diagnostic path")
c = once(c, r'''  SDL_Texture* tex = surf ? SDL_CreateTextureFromSurface(RENDERER, surf) : nullptr;
  SDL_FreeSurface(surf);''', r'''  SDL_Texture* tex = surf ? SDL_CreateTextureFromSurface(RENDERER, surf) : nullptr;
  if (!tex) {
    Log_Error(std::string("cover texture failed: ") + SDL_GetError() +
              " dimensions=" + std::to_string(iw) + "x" + std::to_string(ih) +
              " path=" + path);
  }
  SDL_FreeSurface(surf);''', "final texture creation diagnostic")
c = once(c,
         "pixels_to_texture(cl[w].result_pixels, cl[w].result_iw, cl[w].result_ih)",
         "pixels_to_texture(cl[w].result_pixels, cl[w].result_iw, cl[w].result_ih, cl[w].job_path)",
         "pass stable job path to texture diagnostic")

marker = '  Log_Write("WOOK RU2 RARFIX2: serialized UnRAR backend; CRC-checked solid extraction");'
m = once(m, marker, marker + '\n'
         '  Log_Write("WOOK RU2 COVERFIX1: isolated MuPDF JPEG and TurboJPEG backends");',
         "cover fix build identification")

# Match and validate every expected source block before writing either file.
chooser.write_text(c, encoding="utf-8")
main.write_text(m, encoding="utf-8")
print("COVERFIX1: cover decoder, size-limit and texture failures are logged once per job.")
