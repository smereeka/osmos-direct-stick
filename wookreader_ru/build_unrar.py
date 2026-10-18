#!/usr/bin/env python3
"""Build the pinned, stable UnRAR decoder for WookReader or host regressions.

The download's source-package version (7.2.7) differs from its public version
(UnRAR 7.23).  version.hpp is checked explicitly; this is not a beta build.
Only this helper's private work directory and its documented outputs are
written.  The rest of the WookReader source tree is left untouched.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request


SOURCE_PACKAGE = "7.2.7"
PUBLIC_VERSION = "7.23"
SOURCE_URL = "https://www.rarlab.com/rar/unrarsrc-7.2.7.tar.gz"
SOURCE_SHA256 = "01d903a7dcf413cb2925696d7796e48e38d471f79bfe7ef3ad2aebf6c12dbefd"
MAX_DOWNLOAD_BYTES = 4 * 1024 * 1024
MAX_UNPACKED_BYTES = 12 * 1024 * 1024

# The upstream makefile's OBJECTS + LIB_OBJ, in stable archive order.  We do
# not use its `lib` target: that target also builds a shared library and adds
# RAR_SMP/-pthread by default.  The library API excludes rar.cpp's CLI main.
SOURCE_UNITS = """
rar strlist strfn pathfn smallfn global file filefn filcreat archive arcread
unicode system crypt crc rawread encname resource match timefn rdwrfn consio
options errhnd rarvm secpassword rijndael getbits sha1 sha256 blake2s hash
extinfo extract volume list find unpack headers threadpool rs16 cmddata ui
largepage filestr scantree dll qopen
""".split()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def obtain_archive(work: Path, supplied: Path | None) -> Path:
    if supplied is not None:
        archive = supplied.resolve(strict=True)
    else:
        archive = work / f"unrarsrc-{SOURCE_PACKAGE}.tar.gz"
        if not archive.exists():
            request = urllib.request.Request(
                SOURCE_URL, headers={"User-Agent": "WookReader-build/1"}
            )
            with urllib.request.urlopen(request, timeout=45) as response:
                data = response.read(MAX_DOWNLOAD_BYTES + 1)
            if len(data) > MAX_DOWNLOAD_BYTES:
                raise RuntimeError("UnRAR source download exceeds its size limit")
            archive.write_bytes(data)
    actual = sha256(archive)
    if actual != SOURCE_SHA256:
        raise RuntimeError(
            f"UnRAR source SHA256 mismatch: expected {SOURCE_SHA256}, got {actual}"
        )
    return archive


def safe_extract(archive: Path, destination: Path) -> Path:
    """Extract only regular files/directories under the known `unrar/` root."""
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        if sum(member.size for member in members) > MAX_UNPACKED_BYTES:
            raise RuntimeError("UnRAR source archive exceeds unpacked size limit")
        for member in members:
            name = PurePosixPath(member.name)
            if (
                name.is_absolute()
                or ".." in name.parts
                or not name.parts
                or name.parts[0] != "unrar"
                or "\\" in member.name
                or not (member.isdir() or member.isfile())
            ):
                raise RuntimeError(f"Unexpected UnRAR archive member: {member.name}")
            target = destination.joinpath(*name.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                if source is None:
                    raise RuntimeError(f"Cannot read UnRAR member: {member.name}")
                with source, target.open("wb") as out:
                    shutil.copyfileobj(source, out)
    return destination / "unrar"


def replace_exact(path: Path, old: str, new: str, count: int = 1) -> None:
    content = path.read_text(encoding="utf-8")
    found = content.count(old)
    if found != count:
        raise RuntimeError(f"UnRAR patch drift in {path.name}: expected {count}, got {found}")
    path.write_text(content.replace(old, new), encoding="utf-8")


def patch_portability(source: Path) -> list[str]:
    """Keep decoding code unchanged; adapt only platform integration."""
    version = (source / "version.hpp").read_text(encoding="utf-8")
    definitions = {}
    for line in version.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[0] == "#define":
            definitions[fields[1]] = fields[2]
    if any(definitions.get(key) != value for key, value in {
        "RARVER_MAJOR": "7", "RARVER_MINOR": "23", "RARVER_BETA": "0"
    }.items()):
        raise RuntimeError("Unexpected UnRAR version.hpp; refusing an unverified version")

    # Horizon/newlib has no Linux ELF auxiliary vector.  Leave the portable
    # software CRC/AES paths active on Switch, even with +crc+crypto CFLAGS.
    replace_exact(
        source / "os.hpp",
        "#if defined(__aarch64__) && (defined(__ARM_FEATURE_CRYPTO) || defined(__ARM_FEATURE_CRC32))",
        "#if defined(__aarch64__) && !defined(__SWITCH__) && (defined(__ARM_FEATURE_CRYPTO) || defined(__ARM_FEATURE_CRC32))",
    )
    # libnx's newlib filesystem uses second-resolution stat/utime fields.
    # A large _POSIX_C_SOURCE alone does not establish Linux utimensat support.
    replace_exact(
        source / "os.hpp",
        "#if _POSIX_C_SOURCE >= 200809L || defined(__APPLE__) && defined(__arm64__)",
        "#if !defined(__SWITCH__) && (_POSIX_C_SOURCE >= 200809L || defined(__APPLE__) && defined(__arm64__))",
    )
    # devkitA64 publishes GCC's byte-order macros, not the Linux/BSD aliases
    # used by UnRAR's generic Unix detection. Select the actual compiler ABI;
    # never infer endianness merely from an ARM/AArch64 architecture name.
    replace_exact(
        source / "os.hpp",
        "#if !defined(LITTLE_ENDIAN) && !defined(BIG_ENDIAN)",
        """#if defined(__SWITCH__)
  #undef LITTLE_ENDIAN
  #undef BIG_ENDIAN
  #if defined(__BYTE_ORDER__) && defined(__ORDER_LITTLE_ENDIAN__) && __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
    #define LITTLE_ENDIAN
  #elif defined(__BYTE_ORDER__) && defined(__ORDER_BIG_ENDIAN__) && __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
    #define BIG_ENDIAN
  #else
    #error "UnRAR Switch build requires compiler byte-order macros"
  #endif
#endif

#if !defined(LITTLE_ENDIAN) && !defined(BIG_ENDIAN)""",
    )
    # SD paths and WookReader's internal strings are UTF-8. Use this same
    # conversion in host regressions and on Switch, independent of locale.
    replace_exact(
        source / "unicode.cpp", "#elif defined(_APPLE)",
        "#elif defined(_APPLE) || defined(WOOK_UNRAR_UTF8)", count=2,
    )

    license_text = (source / "license.txt").read_text(encoding="utf-8")
    paragraph = license_text.split("   2. ", 1)[1].split("   3. ", 1)[0].rstrip()
    license_comment = (
        "/* WookReader: platform integration adaptations only.\n"
        + paragraph + "\n*/\n"
    )
    for name in ("os.hpp", "unicode.cpp"):
        path = source / name
        path.write_text(license_comment + path.read_text(encoding="utf-8"), encoding="utf-8")
    return ["switch-software-cpu-detection", "switch-stat-time-portability",
            "switch-compiler-endianness", "wookreader-utf8-paths"]


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, **kwargs)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}\n{result.stdout}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="WookReader source tree (or isolated host output tree)")
    parser.add_argument("--host", action="store_true", help="Build for host regression tests")
    parser.add_argument("--archive", type=Path, help="Use a local archive, still verifying the pinned SHA256")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--cxx", help="Explicit C++ compiler executable")
    parser.add_argument("--ar", help="Explicit static archiver executable")
    args = parser.parse_args()
    if args.jobs < 1 or args.jobs > 32:
        parser.error("--jobs must be between 1 and 32")

    target = args.target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    mode = "host" if args.host else "switch"
    work = target / f".unrar-build-{mode}"
    work.mkdir(exist_ok=True)
    archive = obtain_archive(work, args.archive)
    flags = ["-O2", "-std=c++11", "-fexceptions", "-ffunction-sections", "-fdata-sections",
             "-D_FILE_OFFSET_BITS=64", "-D_LARGEFILE_SOURCE", "-DRARDLL", "-URAR_SMP",
             "-DWOOK_UNRAR_UTF8"]
    if args.host:
        cxx = args.cxx or "c++"
        ar = args.ar or "ar"
        flags += ["-fPIC"]
    else:
        devkitpro = Path(os.environ.get("DEVKITPRO", "/opt/devkitpro"))
        devkita64 = Path(os.environ.get("DEVKITA64", str(devkitpro / "devkitA64")))
        cxx = args.cxx or str(devkita64 / "bin/aarch64-none-elf-g++")
        ar = args.ar or str(devkita64 / "bin/aarch64-none-elf-ar")
        # Strict -std=c++11 hides POSIX declarations in newlib unless requested.
        # Expose the real ftruncate/fileno/symlink/lchown APIs before considering
        # any platform feature exclusions; do not provide replacement libc stubs.
        flags += ["-D__SWITCH__", "-D_POSIX_C_SOURCE=200809L", "-D_DEFAULT_SOURCE",
                  "-march=armv8-a+crc+crypto", "-mtune=cortex-a57",
                  "-mtp=soft", "-fPIE", "-I" + str(devkitpro / "libnx/include")]
    compiler_version = run([cxx, "--version"]).stdout.splitlines()[0]

    with tempfile.TemporaryDirectory(prefix="build-", dir=work) as temporary:
        build = Path(temporary)
        source = safe_extract(archive, build)
        patches = patch_portability(source)
        object_dir = build / "objects"
        object_dir.mkdir()

        def compile_one(name: str) -> Path:
            output = object_dir / f"{name}.o"
            run([cxx, *flags, "-c", str(source / f"{name}.cpp"), "-o", str(output)])
            return output

        print(f"Building UnRAR {PUBLIC_VERSION} ({mode}, {len(SOURCE_UNITS)} units, {args.jobs} jobs)", flush=True)
        objects = []
        errors = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            # Gather every translation unit's result. A failure in the first
            # header must not hide unrelated newlib portability diagnostics.
            futures = [(name, pool.submit(compile_one, name)) for name in SOURCE_UNITS]
            for name, future in futures:
                try:
                    objects.append(future.result())
                except (RuntimeError, OSError) as error:
                    errors.append(f"{name}.cpp:\n{error}")
        error_log = target / "unrar-compile-errors.txt"
        if errors:
            diagnostics = "\n\n".join(errors) + "\n"
            error_log.write_text(diagnostics, encoding="utf-8")
            print(diagnostics, flush=True)
            raise RuntimeError(f"{len(errors)}/{len(SOURCE_UNITS)} UnRAR units failed; full log: {error_log}")
        error_log.unlink(missing_ok=True)
        library = build / "libunrar.a"
        # GNU ar D removes timestamps/UID/GID from the static archive index.
        run([ar, "rcsD", str(library), *map(str, objects)])
        library_dir = target / "lib"
        include_dir = target / "include/wookrar"
        library_dir.mkdir(exist_ok=True)
        include_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(library, library_dir / "libunrar.a")
        shutil.copyfile(source / "dll.hpp", include_dir / "dll.hpp")
        shutil.copyfile(source / "license.txt", target / "UNRAR-LICENSE.txt")
        metadata = {
            "source_url": SOURCE_URL,
            "source_package_version": SOURCE_PACKAGE,
            "version": PUBLIC_VERSION,
            "beta": False,
            "source_sha256": SOURCE_SHA256,
            "mode": mode,
            "compiler": compiler_version,
            "flags": flags,
            "patches": patches,
            "source_units": SOURCE_UNITS,
            "library_sha256": sha256(library_dir / "libunrar.a"),
            "dll_header_sha256": sha256(include_dir / "dll.hpp"),
        }
        (target / "unrar-build.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Ready: {target / 'lib/libunrar.a'}", flush=True)


if __name__ == "__main__":
    main()
