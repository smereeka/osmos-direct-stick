#!/usr/bin/env python3
"""Compile and exercise the exact SDL-independent RAR backend on the host.

CI uses only original synthetic fixtures committed beside this script. An
optional private archive/golden pair can be checked locally; its decoded pages
exist only in a temporary directory and are removed when the process exits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import zlib

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent


def check(condition: bool, detail: str) -> None:
    if not condition:
        raise AssertionError(detail)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_payloads(report: dict, output: Path, expected: dict[str, dict]) -> list[dict]:
    check(len(report["entries"]) == len(expected), "image count differs from golden")
    verified = []
    for entry in report["entries"]:
        name = entry["name"]
        check(name in expected, "unexpected image name: " + name)
        golden = expected[name]
        data = (output / entry["file"]).read_bytes()
        expected_size = golden.get("size", golden.get("actual_size"))
        expected_crc = golden.get("crc32", golden.get("actual_crc32"))
        digest, crc = sha256(data), f"{zlib.crc32(data):08x}"
        check(len(data) == expected_size == entry["size"], "image size mismatch: " + name)
        check(digest == golden["sha256"], "image SHA-256 mismatch: " + name)
        check(crc == expected_crc.lower(), "image CRC32 mismatch: " + name)
        verified.append({"name": name, "size": len(data), "sha256": digest, "crc32": crc})
    return verified


def mutate_member_crc(original: bytes, header: dict) -> bytes:
    # Keep compressed bytes unchanged and repair the header checksum. This
    # exercises payload verification, independently of decompression failures.
    changed = bytearray(original)
    offset = header["data_crc32_offset"]
    value = struct.unpack_from("<I", changed, offset)[0]
    struct.pack_into("<I", changed, offset, value ^ 1)
    start, size = header["header_offset"], header["header_size"]
    struct.pack_into("<H", changed, start, zlib.crc32(changed[start + 2:start + size]) & 0xffff)
    return bytes(changed)


def run_suite(prefix: Path, private_archive: Path | None, private_golden: Path | None) -> dict:
    manifest = json.loads((HERE / "fixtures/manifest.json").read_text(encoding="utf-8"))
    expected = {p["name"]: p for p in manifest["expected_files"] if p["is_image"]}
    physical_images = [n for n in manifest["physical_entry_order"] if n in expected]
    check(physical_images != manifest["page_names_sorted"], "fixture does not exercise physical/lexical order mismatch")
    report = {
        "schema_version": 1,
        "backend_source_sha256": sha256((SOURCE / "WookRAR.cpp").read_bytes()),
        "backend_header_sha256": sha256((SOURCE / "WookRAR.hpp").read_bytes()),
        "fixture_manifest_sha256": sha256((HERE / "fixtures/manifest.json").read_bytes()),
        "unrar": json.loads((prefix / "unrar-build.json").read_text(encoding="utf-8")),
        "synthetic_fixtures": [],
        "negative_cases": [],
        "cancellation": [],
        "process_locale": "C",
        "tested_on_nintendo_switch": False,
    }
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="wookrar_backend_") as scratch:
        temp = Path(scratch)
        executable = temp / "test_backend"
        compile_command = [
            os.environ.get("CXX", "g++"), "-O2", "-g", "-std=c++17",
            "-fno-exceptions", "-fno-rtti", "-pthread", "-Wall", "-Wextra", "-Werror",
            "-I", str(SOURCE), "-I", str(prefix / "include"),
            str(HERE / "test_backend.cpp"), str(SOURCE / "WookRAR.cpp"),
            str(prefix / "lib/libunrar.a"), "-Wl,--wrap=RAROpenArchiveEx",
            "-Wl,--wrap=RARProcessFileW", "-o", str(executable),
        ]
        subprocess.run(compile_command, check=True, timeout=180)

        def run(*args: object, timeout: int = 120) -> dict:
            result = subprocess.run([str(executable), *(str(a) for a in args)],
                                    capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                                    env={**os.environ, "LC_ALL": "C", "LANG": "C"})
            check(result.returncode == 0,
                  "backend harness failed: " + " ".join(str(a) for a in args[:2]) + "\n" + result.stderr)
            return json.loads(result.stdout)

        for fixture in manifest["fixtures"]:
            path = HERE / "fixtures" / fixture["name"]
            data = path.read_bytes()
            check(len(data) == fixture["size"] and sha256(data) == fixture["sha256"],
                  "fixture differs from recorded original: " + fixture["name"])
            listing = run("inspect", path)
            check(listing["ok"] and listing["is_archive"], "fixture not recognized as RAR")
            check(listing["solid"] == fixture["solid"], "solid archive flag mismatch")
            check(listing["names"] == physical_images, "enumeration changed UTF-8 names or physical order")
            check(listing["count"] == manifest["images_per_archive"], "nonimages counted as pages")
            output = temp / fixture["name"]
            output.mkdir()
            tested = run("exercise", path, output, 50)
            verified = verify_payloads(tested, output, expected)
            report["synthetic_fixtures"].append({
                "name": fixture["name"], "solid": fixture["solid"], "images": verified,
                "repeated_opens": tested["repeated_opens"],
                "random_backward_reads": tested["random_backward_reads"],
                "concurrent_workers": tested["concurrent_workers"],
                "concurrent_cycles": tested["concurrent_cycles"],
            })
            print("PASS", fixture["name"], "4 exact images; 50 repeated opens; 4 concurrent workers", flush=True)

        cases = manifest["runtime_negative_cases"]
        source_fixture = next(f for f in manifest["fixtures"] if f["name"] == cases["source_fixture"])
        original_path = HERE / "fixtures" / source_fixture["name"]
        original = original_path.read_bytes()
        headers = {h["name"]: h for h in source_fixture["rar4_file_headers"]}
        bad_paths = {}
        for case_name in ["bad_target_crc", "bad_nonimage_predecessor_crc"]:
            case = cases[case_name]
            bad = temp / (case_name + ".cbr")
            bad.write_bytes(mutate_member_crc(original, headers[case["mutate_entry"]]))
            bad_paths[case_name] = bad
            output = temp / (case_name + ".bin")
            result = run("extract", bad, case["extract_entry"], output)
            check(not result["ok"] and result["error"] == "corrupt", "CRC error was not reported distinctly")
            check(not output.exists() and result["size"] == 0, "corrupt payload escaped the backend")
            expected_allocs = 1 if case_name == "bad_target_crc" else 0
            check(result["allocations"] == result["frees"] == expected_allocs,
                  "corrupt extraction allocated/freed at the wrong stage")
            report["negative_cases"].append({"case": case_name, **result})

        truncated_case = cases["truncated_last_member"]
        truncated = temp / "truncated.cbr"
        truncated.write_bytes(original[:truncated_case["truncate_to_bytes"]])
        target_output = temp / "truncated.bin"
        result = run("extract", truncated, truncated_case["extract_entry"], target_output)
        check(not result["ok"] and result["error"] != "not_found" and not target_output.exists(),
              "truncated payload was accepted or mislabeled missing")
        report["negative_cases"].append({"case": "truncated_payload", **result})

        for mode, target, expected_error in [
            ("extract", cases["missing_member"]["extract_entry"], "not_found"),
            ("allocfail", cases["bad_target_crc"]["extract_entry"], "no_memory"),
        ]:
            output = temp / (mode + ".bin")
            result = run(mode, original_path, target, output)
            check(not result["ok"] and result["error"] == expected_error and not output.exists(),
                  "missing/allocation failure did not retain its distinct error")
            report["negative_cases"].append({"case": expected_error, **result})

        # UTF-8 archive path and old cache backslash separators must resolve to
        # exactly the same bytes as the normalized Unicode member name.
        unicode_dir = temp / "Папка з коміксами"
        unicode_dir.mkdir()
        unicode_archive = unicode_dir / "Набір 🦊.cbr"
        shutil.copyfile(original_path, unicode_archive)
        target = cases["bad_target_crc"]["extract_entry"]
        output = temp / "unicode.bin"
        result = run("extract", unicode_archive, target.replace("/", "\\"), output)
        check(result["ok"] and sha256(output.read_bytes()) == expected[target]["sha256"],
              "UTF-8 archive path/backslash member lookup failed")
        report["unicode_archive_path_and_backslash_member"] = True

        for filename in ["rar4_solid.cbr", "rar5_solid.cbr"]:
            result = run("cancel", HERE / "fixtures" / filename, target, 2, 3)
            check(result["ok"], "deterministic cancellation test failed")
            report["cancellation"].append({"fixture": filename, **result})
        mixed = run("mixed", original_path, bad_paths["bad_target_crc"], target)
        check(mixed["ok"], "mixed valid/corrupt concurrent sessions failed")
        report["mixed_concurrency"] = mixed
        print("PASS corrupt target/predecessor CRC, truncation, missing member, allocation failure, Unicode path", flush=True)
        print("PASS cancellation during predecessor/target decode and mutex wait; recovery; 64 mixed concurrent sessions", flush=True)

        if private_archive and private_golden:
            golden = json.loads(private_golden.read_text(encoding="utf-8"))
            check(sha256(private_archive.read_bytes()) == golden["archive"]["sha256"],
                  "private test archive differs from the independent golden")
            output = temp / "private-pages"
            output.mkdir()
            tested = run("exercise", private_archive, output, 0, timeout=300)
            private_expected = {p["name"]: p for p in golden["verified_files"]}
            verified = verify_payloads(tested, output, private_expected)
            report["private_archive"] = {
                "archive_sha256": golden["archive"]["sha256"], "solid": tested["solid"],
                "verified_files": verified, "random_backward_reads": tested["random_backward_reads"],
                "decoded_pages_retained": False,
            }
            print("PASS private archive:", len(verified), "images match independent size/SHA-256/CRC32 golden", flush=True)

    report["ok"] = True
    report["seconds"] = round(time.monotonic() - start, 3)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unrar-prefix", type=Path, required=True,
                        help="output directory previously built by build_unrar.py --host")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--private-archive", type=Path)
    parser.add_argument("--private-golden", type=Path)
    args = parser.parse_args()
    if bool(args.private_archive) != bool(args.private_golden):
        parser.error("provide both private arguments together")
    report = run_suite(args.unrar_prefix.resolve(), args.private_archive, args.private_golden)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("PASS exact WookRAR backend regression suite:", report["seconds"], "seconds")


if __name__ == "__main__":
    main()
