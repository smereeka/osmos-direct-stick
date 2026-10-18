#!/usr/bin/env python3
"""Keep MuPDF's bundled JPEG9 ABI separate from portlibs JPEG/TurboJPEG.

Run after Makefile.mupdf and before linking WookReader. Derive the namespace
map from the actual bundled JPEG objects, then rewrite definitions AND uses in
every member of libmupdf.a. In particular, MuPDF's custom JPEG allocator must
stay with JPEG9. Reordering libraries would merely reverse the ABI mismatch.

After the app build, --verify-elf verifies the two actual AArch64 implementations
and the ABI argument/callee used by Turbo initialization in the final ELF.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile


PREFIX = "wookmupdf_"
JPEG_FAMILIES = ("jpeg_", "jinit_", "jcopy_", "jdiv_", "jround_", "jzero_")
REQUIRED = {
    "jpeg_CreateCompress", "jpeg_CreateDecompress", "jpeg_std_error",
    "jpeg_read_header", "jpeg_start_decompress", "jpeg_read_scanlines",
    "jpeg_finish_decompress", "jpeg_destroy_decompress", "jpeg_mem_init",
    "jpeg_mem_term",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args: str) -> str:
    result = subprocess.run(args, check=True, text=True, capture_output=True,
                            env={**os.environ, "LC_ALL": "C"})
    return result.stdout


def symbols(nm: str, path: Path) -> list[tuple[str, str]]:
    records = []
    for line in run(nm, "--format=posix", "--extern-only", str(path)).splitlines():
        if not line.strip() or line.endswith(":"):
            continue  # GNU nm's archive member heading
        fields = line.split()
        require(len(fields) >= 2 and len(fields[1]) == 1,
                "Unexpected nm output: " + line)
        records.append((fields[0], fields[1]))
    return records


def defined(records: list[tuple[str, str]]) -> set[str]:
    return {name for name, kind in records if kind not in ("U", "w", "v")}


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def verify_archive(nm: str, archive: Path, names: list[str]) -> list[tuple[str, str]]:
    records = symbols(nm, archive)
    all_names = {name for name, _ in records}
    require(not (all_names & set(names)), "Unprefixed bundled JPEG uses remain")
    require({PREFIX + name for name in names} <= defined(records),
            "A namespaced JPEG definition is missing")
    leftovers = sorted(name for name in all_names if name.startswith(JPEG_FAMILIES))
    require(not leftovers, "Unmapped JPEG symbols remain: " + ", ".join(leftovers))
    return records


def isolate(target: Path, tool_prefix: str) -> dict:
    nm, ar, objcopy = (tool_prefix + tool for tool in ("nm", "ar", "objcopy"))
    archive = target / "lib/libmupdf.a"
    require(archive.is_file(), "Build lib/libmupdf.a before JPEG isolation")
    objects = sorted((target / "mupdf/thirdparty/libjpeg").glob("*.o"))
    require(bool(objects), "No built bundled JPEG objects found")
    custom_allocator = target / "mupdf/source/fitz/jmemcust.o"
    if custom_allocator.is_file():
        objects.append(custom_allocator)
    origins = []
    names_set: set[str] = set()
    for path in objects:
        exports = sorted(defined(symbols(nm, path)))
        names_set.update(exports)
        origins.append({"path": path.relative_to(target).as_posix(),
                        "sha256": sha256(path), "exports": exports})
    require(REQUIRED <= names_set, "Bundled JPEG object set is incomplete")
    require(all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in names_set),
            "Unexpected non-C symbol in bundled JPEG objects")
    require(all(name.startswith(JPEG_FAMILIES) for name in names_set),
            "Unexpected non-JPEG export in the selected JPEG objects")
    names = sorted(names_set)
    mapping = {name: PREFIX + name for name in names}
    report_path = target / "jpeg-isolation.json"
    map_path = target / "jpeg-symbols.map"
    before = symbols(nm, archive)
    before_names = {name for name, _ in before}
    if before_names & set(mapping.values()):
        require(not (before_names & names_set), "Partially isolated JPEG archive")
        verify_archive(nm, archive, names)
        require(report_path.is_file(), "Isolated archive has no provenance report")
        previous = json.loads(report_path.read_text(encoding="utf-8"))
        require(previous["output_archive_sha256"] == sha256(archive),
                "Isolated archive differs from its provenance report")
        require(previous["renamed_symbols"] == names, "JPEG symbol map changed")
        return previous
    require(names_set <= defined(before), "JPEG objects are missing from libmupdf.a")
    unmapped = sorted(name for name in before_names
                      if name.startswith(JPEG_FAMILIES) and name not in mapping)
    require(not unmapped, "JPEG definitions/uses lack an object provider: " + ", ".join(unmapped))
    input_sha = sha256(archive)
    member_order = run(ar, "t", str(archive)).splitlines()
    expected_records = Counter((mapping.get(name, name), kind) for name, kind in before)
    map_text = "".join(name + " " + mapping[name] + "\n" for name in names)
    with tempfile.TemporaryDirectory(prefix="jpeg-isolation-", dir=archive.parent) as work:
        work = Path(work)
        temporary_map = work / "symbols.map"
        temporary_map.write_text(map_text, encoding="ascii")
        isolated = work / "libmupdf.a"
        run(objcopy, "--redefine-syms=" + str(temporary_map), str(archive), str(isolated))
        run(ar, "sD", str(isolated))
        require(run(ar, "t", str(isolated)).splitlines() == member_order,
                "JPEG isolation changed archive member order")
        after = verify_archive(nm, isolated, names)
        require(Counter(after) == expected_records,
                "JPEG isolation changed symbols beyond the derived namespace map")
        output_sha = sha256(isolated)
        os.replace(isolated, archive)
    map_path.write_text(map_text, encoding="ascii")
    report = {
        "ok": True,
        "schema": 1,
        "method": "objcopy namespace of every bundled JPEG export and every archive reference",
        "symbol_prefix": PREFIX,
        "bundled_jpeg_abi": 90,
        "portlibs_jpeg_abi": 62,
        "input_archive_sha256": input_sha,
        "output_archive_sha256": output_sha,
        "archive_members": len(member_order),
        "renamed_symbol_count": len(names),
        "renamed_symbols": names,
        "symbol_map_sha256": sha256(map_path),
        "unprefixed_jpeg_symbols_remaining": 0,
        "custom_mupdf_allocator_included": custom_allocator.is_file(),
        "objects": origins,
        "tools": {"nm": run(nm, "--version").splitlines()[0],
                  "ar": run(ar, "--version").splitlines()[0],
                  "objcopy": run(objcopy, "--version").splitlines()[0]},
    }
    write_json(report_path, report)
    return report


def elf_functions(data: bytes) -> dict[str, dict]:
    """Read normal ELF64 symbol tables without a Python runtime dependency."""
    require(data[:6] == b"\x7fELF\x02\x01", "Expected a little-endian ELF64 executable")
    require(struct.unpack_from("<H", data, 18)[0] == 183, "Final JPEG gate expects AArch64")
    shoff = struct.unpack_from("<Q", data, 40)[0]
    shentsize, shnum = struct.unpack_from("<HH", data, 58)
    require(shentsize == 64 and shnum > 0, "ELF section table is missing")
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, shoff + i * shentsize)
                for i in range(shnum)]
    found = {}
    for section in sections:
        if section[1] != 2:  # SHT_SYMTAB, including local Turbo helper functions
            continue
        strings_section = sections[section[6]]
        strings = data[strings_section[4]:strings_section[4] + strings_section[5]]
        require(section[9] == 24, "Unexpected ELF64 symbol size")
        for offset in range(section[4], section[4] + section[5], 24):
            name_offset, info, _, index, address, size = struct.unpack_from("<IBBHQQ", data, offset)
            if info & 15 != 2 or not 0 < index < len(sections) or not size:
                continue
            end = strings.find(b"\0", name_offset)
            require(end >= name_offset, "Invalid ELF symbol name")
            name = strings[name_offset:end].decode("utf-8", errors="strict")
            owner = sections[index]
            file_offset = owner[4] + address - owner[3]
            require(owner[4] <= file_offset and file_offset + size <= owner[4] + owner[5],
                    "Function lies outside its ELF section: " + name)
            found[name] = {"address": address, "size": size,
                           "code": data[file_offset:file_offset + size]}
    require(bool(found), "Final ELF has no retained function symbols")
    return found


def branch_target(address: int, word: int) -> int | None:
    if word & 0xFC000000 != 0x94000000:  # AArch64 BL immediate
        return None
    immediate = word & 0x03FFFFFF
    if immediate & 0x02000000:
        immediate -= 0x04000000
    return address + 4 * immediate


def verify_link(target: Path, tool_prefix: str, elf: Path) -> dict:
    report_path = target / "jpeg-isolation.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    archive = target / "lib/libmupdf.a"
    require(report["output_archive_sha256"] == sha256(archive),
            "MuPDF archive was replaced after JPEG isolation")
    verify_archive(tool_prefix + "nm", archive, report["renamed_symbols"])
    data = elf.read_bytes()
    functions = elf_functions(data)
    evidence = {}
    for name, abi in [("jpeg_CreateDecompress", 62), (PREFIX + "jpeg_CreateDecompress", 90)]:
        require(name in functions, "Linked JPEG implementation missing: " + name)
        function = functions[name]
        # The ABI version is the second argument (w1). Both pinned implementations
        # compare it before touching any image data or decompressor internals.
        instruction = struct.pack("<I", 0x7100003F | (abi << 10))  # cmp w1, #ABI
        offsets = [i for i in range(0, min(128, len(function["code"])) - 3, 4)
                   if function["code"][i:i + 4] == instruction]
        require(len(offsets) == 1, "Cannot verify linked JPEG ABI " + str(abi) + " in " + name)
        evidence[name] = {"address": hex(function["address"]), "abi": abi,
                          "abi_compare_address": hex(function["address"] + offsets[0]),
                          "abi_compare_bytes": instruction.hex()}
    original = functions["jpeg_CreateDecompress"]["address"]
    bundled = functions[PREFIX + "jpeg_CreateDecompress"]["address"]
    require(original != bundled, "JPEG ABI implementations were aliased")
    require("tjInitDecompress" in functions, "Turbo initialization symbol is missing")
    calls = []
    for name, function in functions.items():
        if "tjInitDecompress" not in name:
            continue
        code = function["code"]
        for offset in range(0, len(code) - 3, 4):
            word = struct.unpack_from("<I", code, offset)[0]
            destination = branch_target(function["address"] + offset, word)
            require(destination != bundled, "Turbo initialization calls the bundled JPEG9 ABI")
            if destination != original:
                continue
            # Check the actual argument setup immediately preceding this call.
            # MOVZ w1,#62 is the form used by the pinned portlibs compiler.
            mov62 = struct.pack("<I", 0x52800001 | (62 << 5))
            candidates = [i for i in range(max(0, offset - 24), offset, 4)
                          if code[i:i + 4] == mov62]
            require(len(candidates) == 1, "Turbo JPEG ABI argument 62 is unverified")
            setup = candidates[0]
            for middle in range(setup + 4, offset, 4):
                instruction = struct.unpack_from("<I", code, middle)[0]
                require(branch_target(function["address"] + middle, instruction) is None,
                        "Unexpected call between Turbo ABI argument and JPEG initialization")
                require(instruction & 31 != 1,
                        "Unrecognized write to the Turbo ABI argument register")
            calls.append({"caller": name, "call_address": hex(function["address"] + offset),
                          "argument_address": hex(function["address"] + setup),
                          "argument": 62, "callee": "jpeg_CreateDecompress",
                          "callee_address": hex(original)})
    require(len(calls) == 1, "Expected exactly one Turbo-to-JPEG initialization call")
    report["linked_elf"] = {"ok": True, "sha256": hashlib.sha256(data).hexdigest(),
                            "size": len(data), "architecture": "AArch64",
                            "jpeg_implementations": evidence, "turbo_calls": calls}
    write_json(report_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="Applied WookReader source/build tree")
    devkit = os.environ.get("DEVKITA64", "/opt/devkitpro/devkitA64")
    parser.add_argument("--tool-prefix", default=devkit + "/bin/aarch64-none-elf-",
                        help="Toolchain prefix; pass an empty string for host nm/ar/objcopy")
    parser.add_argument("--verify-elf", type=Path,
                        help="Verify a final AArch64 ELF against the existing isolation report")
    args = parser.parse_args()
    target = args.target.resolve()
    if args.verify_elf:
        report = verify_link(target, args.tool_prefix, args.verify_elf.resolve())
        print("JPEG link verified: Turbo ABI62 and isolated MuPDF ABI90; ELF SHA256 " +
              report["linked_elf"]["sha256"])
    else:
        report = isolate(target, args.tool_prefix)
        print("JPEG isolated: " + str(report["renamed_symbol_count"]) +
              " symbols; libmupdf.a SHA256 " + report["output_archive_sha256"])


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError, KeyError, struct.error) as error:
        print("JPEG isolation failed: " + str(error), file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr, file=sys.stderr)
        sys.exit(1)
