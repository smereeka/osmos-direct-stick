#!/usr/bin/env python3
"""WOOK RU3: event-driven idle frames/workers and a smaller prefetch profile.

Apply after enhance_library.py and enhance_reader.py. No CPU/GPU clock rates,
brightness, system power modes or book files are changed. Status timers use elapsed time,
so pausing redraw never pauses the clock, charge indicator or reader controls.
"""
from pathlib import Path
import re
import shutil
import sys


ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "wookreader-src")
PACKAGE = Path(__file__).resolve().parent


def once(text, old, new, label):
    if text.count(old) != 1:
        raise RuntimeError(f"{label}: expected one source anchor, got {text.count(old)}")
    return text.replace(old, new, 1)


def include_after(text, anchor, header):
    line = '#include "' + header + '"'
    if line in text:
        return text
    return once(text, anchor, anchor + "\n" + line, "include " + header)


chooser_path = ROOT / "source/menus/book-chooser/MenuChooser.cpp"
reader_loop_path = ROOT / "source/menus/book/menu_book_reader.cpp"
pages_path = ROOT / "source/menus/book/CBZPageLayout.cpp"
status_path = ROOT / "source/status_bar.c"
status_header_path = ROOT / "include/status_bar.h"
c = chooser_path.read_text(encoding="utf-8")
r = reader_loop_path.read_text(encoding="utf-8")
p = pages_path.read_text(encoding="utf-8")
s = status_path.read_text(encoding="utf-8")

# libnx declarations must first be included under C linkage.
c = once(c, '#include "MenuChooser.h"',
         '#include "MenuChooser.h"\n#include <switch/kernel/levent.h>\n#include "status_bar.h"',
         "cover event declarations")
c = include_after(c, '#include "../book/WookLibrary.hpp"', "../book/WookPower.hpp")
r = include_after(r, '#include "BookReader.hpp"', "WookPower.hpp")
r = include_after(r, '#include "SDL_helper.h"', "status_bar.h")
r = once(r, "if (event.type == SDL_WINDOWEVENT) reader->mark_redraw();",
         "if (wookpower::window_event(event)) reader->mark_redraw();",
         "reader window and foreground invalidation")
p = include_after(p, '#include "CBZPageLayout.hpp"', "WookPower.hpp")
p = include_after(p, '#include "CBZPageLayout.hpp"', "WookLibrary.hpp")

# Manual-clear event + predicate checks avoid both idle polling and a lost wake
# if the main thread publishes a job just before the worker starts waiting.
c = once(c, "  bool         active = false;\n};",
         "  bool         active = false;\n  LEvent       wake;\n};", "cover wake field")
c = once(c, '''  while (!__atomic_load_n(&cl->should_exit, __ATOMIC_ACQUIRE)) {
    if (__atomic_load_n(&cl->result_ready, __ATOMIC_ACQUIRE)) {
      svcSleepThread(2000000LL); continue; // wait for main thread to free pixels
    }
    if (!__atomic_load_n(&cl->job_ready, __ATOMIC_ACQUIRE)) {
      svcSleepThread(2000000LL); continue;  // 2 ms poll
    }''', '''  while (!__atomic_load_n(&cl->should_exit, __ATOMIC_ACQUIRE)) {
    leventClear(&cl->wake);
    if (__atomic_load_n(&cl->should_exit, __ATOMIC_ACQUIRE)) break;
    if (__atomic_load_n(&cl->result_ready, __ATOMIC_ACQUIRE) ||
        !__atomic_load_n(&cl->job_ready, __ATOMIC_ACQUIRE)) {
      leventWait(&cl->wake, UINT64_MAX);
      continue;
    }''', "cover worker blocking wait")
c = once(c, "    Result rc = threadCreate(&cl[w].thread, cover_loader_worker, &cl[w], nullptr, 512 * 1024, 0x2c, w + 1);",
         "    leventInit(&cl[w].wake, false, false);\n    Result rc = threadCreate(&cl[w].thread, cover_loader_worker, &cl[w], nullptr, 512 * 1024, 0x2c, w + 1);",
         "cover event initialization")
ack = "__atomic_store_n(&cl[w].result_ready, 0, __ATOMIC_RELEASE);"
if c.count(ack) != 2:
    raise RuntimeError("Expected two cover result acknowledgement paths")
c = c.replace(ack, ack + "\n        leventSignal(&cl[w].wake);")
c = once(c, "        __atomic_store_n(&cl[w].job_ready, 1, __ATOMIC_RELEASE);",
         "        __atomic_store_n(&cl[w].job_ready, 1, __ATOMIC_RELEASE);\n        leventSignal(&cl[w].wake);",
         "cover job signal")
c = once(c, '''    __atomic_store_n(&cl[w].should_exit, 1, __ATOMIC_RELEASE);
    __atomic_store_n(&cl[w].job_ready, 0, __ATOMIC_RELEASE);''',
         '''    __atomic_store_n(&cl[w].should_exit, 1, __ATOMIC_RELEASE);
    __atomic_store_n(&cl[w].job_ready, 0, __ATOMIC_RELEASE);
    leventSignal(&cl[w].wake);''', "cover exit signal")

# The library performs input and consumes cover results even when no frame is
# needed. Its UI patch places the marker before every render/setup operation.
c = once(c, "  bool library_visual_dirty = true;",
         "  bool library_visual_dirty = true;\n  wookpower::FrameGate power_frame;",
         "library frame gate")
c = once(c, "    // WOOK_POWER_LIBRARY_DRAW",
         '''    // WOOK_POWER_LIBRARY_DRAW: no GPU work for an unchanged idle library.
    const bool power_status_changed = StatusBar_PollChanged();
    power_frame.invalidate(library_visual_dirty || power_status_changed);
    wook::maybe_flush();
    if (!power_frame.take(wook::battery_saver())) {
      svcSleepThread(wookpower::idle_sleep_ns);
      continue;
    }
    library_visual_dirty = false;''',
         "library render gate")
c = once(c, '''    // WOOK_POWER_LIBRARY_STATUS
    const time_t now = time(nullptr); struct tm local_time = {}; localtime_r(&now, &local_time);
    char clock_text[8] = {}; strftime(clock_text, sizeof(clock_text), "%H:%M", &local_time);
    library_text.draw(ROBOTO_20, windowX - 82, 10, textColor, clock_text);''',
         '''    // WOOK_POWER_LIBRARY_STATUS: shared, elapsed-time status snapshot.
    library_text.draw(ROBOTO_20, windowX - 190, 10, textColor, StatusBar_BatteryText());
    library_text.draw(ROBOTO_20, windowX - 82, 10, textColor, StatusBar_ClockText());''',
         "library clock and battery header")

# The reader's UX patch supplies dirty/async/deadline state. Place the gate at
# its post-input draw marker, preserving the loop's input and state flush work.
function = r.index("void Menu_OpenBook(")
opening_brace = r.index("{", function)
r = r[:opening_brace + 1] + "\n    wookpower::FrameGate power_frame;" + r[opening_brace + 1:]
text_cache_calls = r.count("SDL_TextCache_NextFrame();")
if text_cache_calls != 1:
    raise RuntimeError("Expected one reader text-cache frame boundary")
r = re.sub(r"(?m)^\s*SDL_TextCache_NextFrame\(\);\n", "", r, count=1)
pattern = r"(?m)^([ \t]*)// WOOK_POWER_READER_DRAW\n\1reader->draw\(helpMenu, notesMenu\);"
replacement = '''// WOOK_POWER_READER_DRAW
{indent}const bool status_changed = StatusBar_PollChanged();
{indent}power_frame.invalidate(reader_input_active || reader->needs_redraw() ||
{indent}                       (reader->status_visible() && status_changed));
{indent}if (power_frame.take(wook::battery_saver())) {{
{indent}    SDL_TextCache_NextFrame();
{indent}    reader->draw(helpMenu, notesMenu);
{indent}}} else {{
{indent}    svcSleepThread(wookpower::idle_sleep_ns);
{indent}}}'''


def reader_gate(match):
    indent = match.group(1)
    return indent + replacement.format(indent=indent)


r, count = re.subn(pattern, reader_gate, r)
if count != 1:
    raise RuntimeError("enhance_reader.py must provide one reader power draw marker")

p = once(p, '''        // Skip slot 2: only 2 prefetch threads to avoid SD card I/O contention
        if (i == 2) continue;''',
         '''        // Saver prepares only the next page; normal keeps forward/backward.
        if (i >= wookpower::prefetch_slots(wook::battery_saver())) continue;''',
         "prefetch energy profile")

# Poll system information by elapsed milliseconds, not by the number of draws.
s = once(s, "#include <time.h>", "#include <time.h>\n#include <stdint.h>\n#include <string.h>",
         "status timer includes")
begin = s.index("// Throttle counters")
end = s.index("  u32 percent = s_percent;", begin)
s = s[:begin] + r'''// Main-thread status snapshot. Unsigned subtraction remains valid across the
// SDL tick counter's wrap; unchanged HH:MM text never forces an idle redraw.
static uint32_t g_clock_polled_at = 0;
static uint32_t g_battery_polled_at = 0;
static bool g_clock_polled = false;
static bool g_battery_polled = false;
static char g_clock_str[32] = "--:--";
static u32 g_battery_percent = 0;
static PsmChargerType g_battery_state = 0;
static bool g_battery_valid = false;

bool StatusBar_PollChanged(void) {
  const uint32_t now = SDL_GetTicks();
  bool changed = false;
  if (!g_clock_polled || (uint32_t)(now - g_clock_polled_at) >= 1000u) {
    g_clock_polled = true;
    g_clock_polled_at = now;
    time_t unix_time = time(NULL);
    struct tm* value = localtime(&unix_time);
    if (value) {
      char next[32];
      snprintf(next, sizeof(next), "%02d:%02d", value->tm_hour, value->tm_min);
      if (strcmp(next, g_clock_str) != 0) {
        strcpy(g_clock_str, next);
        changed = true;
      }
    }
  }
  if (!g_battery_polled || (uint32_t)(now - g_battery_polled_at) >= 5000u) {
    g_battery_polled = true;
    g_battery_polled_at = now;
    u32 percent = g_battery_percent;
    PsmChargerType state = g_battery_state;
    if (R_SUCCEEDED(psmGetChargerType(&state)) && state != g_battery_state) {
      g_battery_state = state;
      changed = true;
    }
    if (R_SUCCEEDED(psmGetBatteryChargePercentage(&percent))) {
      if (!g_battery_valid || percent != g_battery_percent) changed = true;
      g_battery_percent = percent;
      g_battery_valid = true;
    }
  }
  return changed;
}

static char* Clock_GetCurrentTime(void) { return g_clock_str; }
const char* StatusBar_ClockText(void) { return g_clock_str; }
const char* StatusBar_BatteryText(void) {
  static char text[16];
  if (g_battery_valid) snprintf(text, sizeof(text), "%u%%", g_battery_percent);
  else strcpy(text, "--%");
  return text;
}

static void StatusBar_GetBatteryStatus(int x, int y) {
  (void)StatusBar_PollChanged();
''' + s[end:]
s = once(s, "  u32 percent = s_percent;\n  PsmChargerType state = s_state;",
         "  const bool s_valid = g_battery_valid;\n  u32 percent = g_battery_percent;\n  PsmChargerType state = g_battery_state;",
         "status cached values")
s = once(s, "  // Advance throttle counters (once per frame)\n  g_clock_tick++;\n  g_bat_tick++;",
         "  (void)StatusBar_PollChanged();", "status drawing polls by elapsed time")
header = '''#ifndef WOOK_READER_STATUS_BAR_H
#define WOOK_READER_STATUS_BAR_H
#include <stdbool.h>
#ifdef __cplusplus
extern "C" {
#endif
// Main-thread polling; true only when the displayed clock/battery value changed.
bool StatusBar_PollChanged(void);
const char* StatusBar_ClockText(void);
const char* StatusBar_BatteryText(void);
void StatusBar_DisplayTime(bool portriat);
#ifdef __cplusplus
}
#endif
#endif
'''

for path, source in [(chooser_path, c), (reader_loop_path, r), (pages_path, p),
                     (status_path, s), (status_header_path, header)]:
    path.write_text(source, encoding="utf-8")
shutil.copyfile(PACKAGE / "WookPower.hpp", ROOT / "source/menus/book/WookPower.hpp")
print("WOOK RU3 power: idle frame gating, blocking cover workers, forward-only saver prefetch, monotonic status polling")
