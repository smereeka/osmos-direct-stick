#!/usr/bin/env python3
"""Compile and run the real persistent-state implementation on host."""
import argparse
import hashlib
import json
import shlex
import subprocess
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--report", type=Path, default=Path("output/library-tests.json"))
parser.add_argument("--libconfig-source", type=Path)
parser.add_argument("--libconfig-library", type=Path)
args = parser.parse_args()
package = Path(__file__).resolve().parents[1]
sources = [package / "WookLibrary.cpp", package / "tests/test_library_state.cpp"]
with tempfile.TemporaryDirectory(prefix="wook-library-test-") as folder:
    work = Path(folder)
    flags = []
    if args.libconfig_source and args.libconfig_library:
        flags = ["-I", str(args.libconfig_source / "lib"), str(args.libconfig_library),
                 "-Wl,-rpath," + str(args.libconfig_library.parent)]
    else:
        flags = shlex.split(subprocess.check_output(
            ["pkg-config", "--cflags", "--libs", "libconfig"], text=True))
    binary = work / "test-library"
    subprocess.run(["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                    "-I", str(package), *(str(p) for p in sources), *flags, "-o", str(binary)], check=True)
    run = subprocess.run([str(binary), str(work / "state")], capture_output=True, text=True, check=True)
    result = json.loads(run.stdout)
    result["source_sha256"] = hashlib.sha256(sources[0].read_bytes()).hexdigest()
    result["test_sha256"] = hashlib.sha256(sources[1].read_bytes()).hexdigest()
    result["compiler"] = subprocess.check_output(["g++", "--version"], text=True).splitlines()[0]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))

