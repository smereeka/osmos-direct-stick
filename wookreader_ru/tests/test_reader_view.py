#!/usr/bin/env python3
"""Run production view math and mode/end-of-issue invariants on the host."""
import argparse
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="c++")
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="wook-reader-view-") as temporary:
        executable = Path(temporary) / "reader_view"
        subprocess.run([args.cxx, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-I" + str(here.parent), str(here / "test_reader_view.cpp"),
                        "-o", str(executable)], check=True)
        subprocess.run([str(executable)], check=True, timeout=10)


if __name__ == "__main__":
    main()
