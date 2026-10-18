#!/usr/bin/env python3
"""Reproduce the shipped JPEG ABI clash and verify the production isolation helper.

Usage: python3 test_jpeg_isolation.py wookreader-src --report output/jpeg-tests.json

Requires a native C compiler, CMake, make, ar, nm and objcopy. The pinned official
libjpeg-turbo 2.1.2 source is downloaded with a checked SHA256; the IJG 9 sources
and custom allocator are taken from the supplied MuPDF tree. Host SIMD is disabled
because the regression concerns symbol binding and API compatibility, not speed.
No comic artwork is stored: baseline and large progressive fixtures are generated.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request


TURBO_URL = "https://codeload.github.com/libjpeg-turbo/libjpeg-turbo/tar.gz/refs/tags/2.1.2"
TURBO_SHA256 = "e7fdc8a255c45bc8fbd9aa11c1a49c23092fcd7379296aeaeb14d3343a3d1bed"
EXCLUDED_IJG = set("""
    wrjpgcom rdjpgcom djpeg cjpeg jpegtran example jmemmac jmemdos jmemname
    cdjpeg ckconfig rdcolmap rdswitch transupp wrgif rdgif wrbmp rdbmp
    wrtarga rdtarga wrppm rdppm wrrle rdrle jmemcust jmemnobs
""".split())


def run(command, *, timeout=180):
    result = subprocess.run([str(item) for item in command], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {command}\n{result.stdout}")
    return result.stdout


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get_turbo(work, cmake, jobs):
    archive = work / "libjpeg-turbo-2.1.2.tar.gz"
    if not archive.exists():
        with urllib.request.urlopen(TURBO_URL, timeout=60) as response:
            payload = response.read()
        if hashlib.sha256(payload).hexdigest() != TURBO_SHA256:
            raise RuntimeError("libjpeg-turbo source archive checksum mismatch")
        archive.write_bytes(payload)
    if sha256(archive) != TURBO_SHA256:
        raise RuntimeError("Cached libjpeg-turbo source archive checksum mismatch")
    source = work / "libjpeg-turbo-2.1.2"
    if not source.exists():
        with tarfile.open(archive, "r:gz") as package:
            for member in package.getmembers():
                name = Path(member.name)
                if (name.is_absolute() or ".." in name.parts or
                        not (member.isfile() or member.isdir()) or
                        not name.parts or name.parts[0] != source.name):
                    raise RuntimeError(f"Unsafe source archive entry: {member.name}")
            if hasattr(tarfile, "data_filter"):
                package.extractall(work, filter="data")
            else:
                package.extractall(work)
    build = work / "turbo-build"
    flags = ["-DCMAKE_BUILD_TYPE=Release", "-DENABLE_SHARED=OFF", "-DENABLE_STATIC=ON",
             "-DWITH_SIMD=OFF", "-DWITH_TURBOJPEG=ON", "-DWITH_JAVA=OFF",
             "-DWITH_JPEG7=OFF", "-DWITH_JPEG8=OFF"]
    run([cmake, "-S", source, "-B", build, *flags])
    run([cmake, "--build", build, "--target", "turbojpeg-static", "-j", str(jobs)])
    library = build / "libturbojpeg.a"
    config = (build / "jconfig.h").read_text()
    if not re.search(r"#define\s+JPEG_LIB_VERSION\s+62\b", config):
        raise RuntimeError("Native TurboJPEG build is not the shipped ABI 62")
    return source, library, flags


def execute(args, work):
    work.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).resolve().parent
    source = args.source.resolve()
    jpeg = source / "mupdf/thirdparty/libjpeg"
    fitz = source / "mupdf/source/fitz"
    config = source / "mupdf/scripts/libjpeg"
    if not re.search(r"#define\s+JPEG_LIB_VERSION\s+90\b", (jpeg / "jpeglib.h").read_text()):
        raise RuntimeError("Expected the bundled MuPDF IJG 9 header")
    jobs = max(1, min(args.jobs, 8))
    print("Building pinned libjpeg-turbo 2.1.2 (ABI 62)...", flush=True)
    turbo_source, turbo_library, turbo_flags = get_turbo(work, args.cmake, jobs)
    target = work / "mupdf-host"
    jpeg_objects = target / "mupdf/thirdparty/libjpeg"
    fitz_objects = target / "mupdf/source/fitz"
    (target / "lib").mkdir(parents=True, exist_ok=True)
    jpeg_objects.mkdir(parents=True, exist_ok=True)
    fitz_objects.mkdir(parents=True, exist_ok=True)
    native = [args.cc, "-O2", "-std=c11"]
    ijg_flags = [*native, "-DNDEBUG", "-I" + str(config), "-I" + str(jpeg), "-I" + str(fitz)]
    sources = sorted(path for path in jpeg.glob("*.c") if path.stem not in EXCLUDED_IJG)
    if len(sources) < 40:
        raise RuntimeError("The bundled JPEG source tree is incomplete")
    print("Building bundled IJG 9 and MuPDF custom allocator...", flush=True)

    def compile_ijg(path):
        output = jpeg_objects / (path.stem + ".o")
        run([*ijg_flags, "-c", path, "-o", output])
        return output

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        objects = list(pool.map(compile_ijg, sources))
    allocator = fitz_objects / "jmemcust.o"
    run([*ijg_flags, "-c", fitz / "jmemcust.c", "-o", allocator])
    test_source = here / "test_jpeg_isolation.c"
    consumer = target / "mupdf_consumer.o"
    run([*ijg_flags, "-Wall", "-Wextra", "-Werror", "-DWOOK_TEST_IJG",
         "-c", test_source, "-o", consumer])
    library = target / "lib/libmupdf.a"
    if library.exists():
        library.unlink()
    # MuPDF's own objects precede bundled JPEG objects in Makefile.mupdf.
    run([args.ar, "rcs", library, consumer, allocator, *objects])
    original = target / "lib/libmupdf-before.a"
    shutil.copyfile(library, original)
    turbo_test_flags = [*native, "-Wall", "-Wextra", "-Werror", "-I" + str(turbo_source)]
    generator = work / "generate-jpeg"
    run([*turbo_test_flags, "-DWOOK_TEST_GENERATE", test_source, turbo_library,
         "-lm", "-o", generator])
    application = work / "application.o"
    run([*turbo_test_flags, "-c", test_source, "-o", application])
    old_executable = work / "before-isolation"
    run([args.cc, application, original, turbo_library, "-lm",
         "-Wl,--allow-multiple-definition", "-o", old_executable])
    fixtures = [
        ("baseline", 128, 96, False),
        ("large_progressive", 6287, 3000, True),
    ]
    before, after, fixture_records = {}, {}, {}
    for name, width, height, progressive in fixtures:
        path = work / (name + ".jpg")
        run([generator, path, width, height, int(progressive)])
        fixture_records[name] = {"width": width, "height": height,
                                 "progressive": progressive, "sha256": sha256(path)}
        before[name] = json.loads(run([old_executable, "old", path, width, height,
                                      int(progressive)], timeout=30))
    print("Old link reproduced: TurboJPEG passes ABI 62 to IJG 90 and fails before input.", flush=True)
    helper = here.parent / "isolate_jpeg.py"
    run([sys.executable, helper, target, "--tool-prefix", ""])
    isolated_executable = work / "after-isolation"
    # Unlike the shipped build, this focused link does not hide duplicate symbols.
    run([args.cc, application, library, turbo_library, "-lm", "-o", isolated_executable])
    for name, width, height, progressive in fixtures:
        after[name] = json.loads(run([isolated_executable, "isolated", work / (name + ".jpg"),
                                     width, height, int(progressive)], timeout=30))
    print("Isolated link passed: IJG 90 full decode and TurboJPEG 62 scaled RGBA decode.", flush=True)
    inputs = [*sources, jpeg / "jpeglib.h", config / "jconfig.h",
              fitz / "jmemcust.c", fitz / "jmemcust.h", test_source]
    report = {
        "status": "passed",
        "scope": "Host ABI/symbol regression; not Nintendo Switch runtime execution or a complete PDF test",
        "turbojpeg": {"version": "2.1.2", "abi": 62, "source_url": TURBO_URL,
                      "archive_sha256": TURBO_SHA256, "cmake_flags": turbo_flags,
                      "static_library_sha256": sha256(turbo_library)},
        "bundled_jpeg_abi": 90,
        "custom_mupdf_allocator": True,
        "source_sha256": {str(path.relative_to(source)) if path.is_relative_to(source)
                          else path.name: sha256(path) for path in inputs},
        "compiler": run([args.cc, "--version"]).splitlines()[0],
        "fixtures": fixture_records, "before_isolation": before, "after_isolation": after,
        "isolation": json.loads((target / "jpeg-isolation.json").read_text()),
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="WookReader source tree containing mupdf/")
    parser.add_argument("--work-dir", type=Path, help="Retain built libraries and synthetic fixtures here")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--cc", default="cc")
    parser.add_argument("--ar", default="ar")
    parser.add_argument("--cmake", default="cmake")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    args = parser.parse_args()
    if args.work_dir:
        execute(args, args.work_dir.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="wook-jpeg-isolation-") as temporary:
            execute(args, Path(temporary))


if __name__ == "__main__":
    main()
