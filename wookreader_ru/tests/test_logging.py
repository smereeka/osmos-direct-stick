#!/usr/bin/env python3
"""Exercise the applied app's actual shared-log functions with concurrent writers.

Only the libnx mutex primitive is replaced by a host mutex. The stream, guards,
message writes, flushes and close are copied from the patched main.cpp.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-tree", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    source_path = args.source_tree / "source/main.cpp"
    source = source_path.read_text(encoding="utf-8")
    logging = source[source.index("#define LOG_FILE "):
                     source.index("// ===== КОНЕЦ ЛОГИРОВАНИЯ =====")]
    with tempfile.TemporaryDirectory(prefix="wook_log_test_") as temporary:
        work = Path(temporary)
        log = work / "concurrent.log"
        logging, replaced = re.subn(r"#define LOG_FILE .*", "#define LOG_FILE " +
                                   json.dumps(str(log)), logging, count=1)
        assert replaced == 1
        harness = r'''
#include <atomic>
#include <fstream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
using Mutex = unsigned int;
static std::mutex host_log_mutex;
static void mutexLock(Mutex*) { host_log_mutex.lock(); }
static void mutexUnlock(Mutex*) { host_log_mutex.unlock(); }
'''
        harness += logging
        harness += r'''
int main() {
  Log_Init();
  std::atomic<int> ready{0};
  std::atomic<bool> go{false};
  std::vector<std::thread> workers;
  for (int t = 0; t < 4; ++t) workers.emplace_back([&, t] {
    ready.fetch_add(1);
    while (!go.load()) std::this_thread::yield();
    for (int i = 0; i < 1500; ++i) {
      std::string text = "thread=" + std::to_string(t) +
                        " запис=" + std::to_string(i) + " " + std::string(64, 'X');
      if (i % 2) Log_Error(text); else Log_Write(text);
    }
  });
  while (ready.load() != 4) std::this_thread::yield();
  go.store(true);
  for (auto& thread : workers) thread.join();
  Log_Close();
}
'''
        program = work / "test_logging.cpp"
        program.write_text(harness, encoding="utf-8")
        executable = work / "test_logging"
        subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17", "-O2",
                        "-pthread", "-Wall", "-Wextra", "-Werror", str(program),
                        "-o", str(executable)], check=True, timeout=60)
        subprocess.run([str(executable)], check=True, timeout=30)
        lines = log.read_text(encoding="utf-8").splitlines()
        expected = {
            ("[ERROR] " if i % 2 else "[LOG] ") +
            f"thread={t} запис={i} " + "X" * 64
            for t in range(4) for i in range(1500)
        }
        assert lines[0] == "=== WookReader Log Started ==="
        assert lines[-1] == "=== WookReader Log Ended ==="
        records = lines[1:-1]
        assert len(records) == len(expected) == 6000, "lost or duplicated log records"
        assert set(records) == expected, "interleaved, missing or malformed log records"
    report = {
        "ok": True,
        "main_source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "concurrent_writers": 4,
        "verified_records": 6000,
        "utf8_records": True,
        "log_and_error_paths": True,
        "mutex_implementation": "host adapter; actual app log functions",
        "tested_on_nintendo_switch": False,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("PASS shared app logger: 6000 exact UTF-8 records from 4 concurrent writers")


if __name__ == "__main__":
    main()
