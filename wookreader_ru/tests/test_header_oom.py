#!/usr/bin/env python3
"""Build and run the focused header-allocation regression against a host library.

Usage: python3 test_header_oom.py <build_unrar.py --host output directory>
"""

import argparse
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host_library", type=Path)
    parser.add_argument("--cxx", default="c++")
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    library = args.host_library.resolve()
    common = [args.cxx, "-O2", "-std=c++17", "-Wall", "-Wextra", "-Werror",
              "-fno-rtti", "-I" + str(here.parent), "-I" + str(library / "include")]
    with tempfile.TemporaryDirectory(prefix="wookrar-header-oom-") as temporary:
        output = Path(temporary)
        backend = output / "WookRAR.o"
        executable = output / "test_header_oom"
        subprocess.run([*common, "-fno-exceptions", "-c", str(here.parent / "WookRAR.cpp"),
                        "-o", str(backend)], check=True)
        subprocess.run([*common, "-fexceptions", str(here / "test_header_oom.cpp"),
                        str(backend), str(library / "lib/libunrar.a"), "-pthread",
                        "-Wl,--wrap=RARReadHeaderEx", "-o", str(executable)], check=True)
        subprocess.run([str(executable), str(here / "fixtures/rar4_solid.cbr")],
                       check=True, timeout=15)


if __name__ == "__main__":
    main()
