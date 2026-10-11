#!/usr/bin/env python3
"""Source-level, reversible direct-thrust input patch for ChanseyIsTheBest/osmos_nx.

Designed for current upstream nx_pointer.c from main, October 2026.
This file does NOT patch an .nro binary. A devkitPro build is required.
"""
from pathlib import Path
import shutil
import sys

INSERT_STATE_AFTER = 'static int   s_tap_prev  = 0;       /* A or mouse-left held last frame */\n'
STATE = '''\n/* Direct thrust: aim relative to the centre of the game camera, not a free cursor.\n * Manual toggle on R-stick click; the original cursor remains available for menus. */\nstatic int s_direct_mode = 0;\nstatic int s_direct_fire_prev = 0;\nstatic int s_direct_require_release = 0;\nstatic float s_direct_dx = 1.0f, s_direct_dy = 0.0f;\nstatic float s_direct_fire_x = 0.0f, s_direct_fire_y = 0.0f;\n'''

INSERT_CONTROL_BEFORE = '''  if (s_visible > 0) {\n    HidAnalogStickState ls = padGetStickPos(&s_pad, 0);\n'''
DIRECT_CONTROLS = '''  /* R3 toggles direct-thrust mode. Do not leave a synthetic touch held when\n   * switching control modes. Require the trigger to be released once so a\n   * mode toggle while holding ZR cannot accidentally launch the mote. */\n  if (pressed & HidNpadButton_StickR) {\n    if (s_direct_mode && s_direct_fire_prev)\n      push(s_cfg.cursor_id, s_direct_fire_x, s_direct_fire_y, NXP_UP);\n    if (!s_direct_mode && s_tap_prev)\n      push(s_cfg.cursor_id, s_cx, s_cy, NXP_UP);\n    s_direct_fire_prev = 0;\n    s_tap_prev = 0;\n    s_direct_mode = !s_direct_mode;\n    s_direct_require_release = 1;\n    logf_("nxp: direct thrust %s\\n", s_direct_mode ? "ON" : "OFF");\n  }\n  if (!(held & HidNpadButton_ZR)) s_direct_require_release = 0;\n\n  if (s_direct_mode) {\n    /* Left stick chooses the desired MOTION direction. A touch on the\n     * opposite side of the mote causes the reaction-mass impulse.\n     * Because the port lacks the game's world-space player position, the\n     * screen centre is used as an approximation of the followed mote. */\n    HidAnalogStickState ls = padGetStickPos(&s_pad, 0);\n    const float dx = (float)ls.x / 32767.0f;\n    const float dy = (float)ls.y / 32767.0f;  /* +y means up */\n    const float length = sqrtf(dx*dx + dy*dy);\n    if (length > 0.20f) {\n      s_direct_dx = dx / length;\n      s_direct_dy = dy / length;\n    }\n\n    const int fire = ((held & HidNpadButton_ZR) != 0) &&\n                     !s_direct_require_release;\n    if (fire && !s_direct_fire_prev) {\n      const float radius = (float)s_cfg.screen_h * 0.29f;\n      s_direct_fire_x = (float)s_cfg.screen_w * 0.5f - s_direct_dx * radius;\n      s_direct_fire_y = (float)s_cfg.screen_h * 0.5f + s_direct_dy * radius;\n      if (s_direct_fire_x < 1.0f) s_direct_fire_x = 1.0f;\n      if (s_direct_fire_y < 1.0f) s_direct_fire_y = 1.0f;\n      if (s_direct_fire_x > (float)s_cfg.screen_w - 2.0f)\n        s_direct_fire_x = (float)s_cfg.screen_w - 2.0f;\n      if (s_direct_fire_y > (float)s_cfg.screen_h - 2.0f)\n        s_direct_fire_y = (float)s_cfg.screen_h - 2.0f;\n      push(s_cfg.cursor_id, s_direct_fire_x, s_direct_fire_y, NXP_DOWN);\n    } else if (!fire && s_direct_fire_prev) {\n      push(s_cfg.cursor_id, s_direct_fire_x, s_direct_fire_y, NXP_UP);\n    }\n    s_direct_fire_prev = fire;\n  } else if (s_visible > 0) {\n    HidAnalogStickState ls = padGetStickPos(&s_pad, 0);\n'''

DRAW_OLD = 'void nxp_draw(void) {\n  if (!s_ready || s_visible <= 0) return;\n'
DRAW_NEW = 'void nxp_draw(void) {\n  if (!s_ready || s_visible <= 0 || s_direct_mode) return;\n'


def replace_once(text, old, new, name):
    n = text.count(old)
    if n != 1:
        raise ValueError(f'Unsafe patch: expected exactly one {name}, found {n}. File unchanged.')
    return text.replace(old, new, 1)


def run(target):
    p = Path(target).expanduser().resolve()
    if p.is_dir():
        p = p / 'source' / 'nx_pointer.c'
    if not p.is_file():
        raise FileNotFoundError(f'Not found: {p}')
    original = p.read_text(encoding='utf-8')
    if 's_direct_fire_prev' in original:
        print('Already patched. No changes made.')
        return
    result = original
    result = replace_once(result, INSERT_STATE_AFTER, INSERT_STATE_AFTER + STATE, 'state anchor')
    result = replace_once(result, INSERT_CONTROL_BEFORE, DIRECT_CONTROLS, 'nxp_update input anchor')
    result = replace_once(result, DRAW_OLD, DRAW_NEW, 'cursor draw anchor')
    # This marker must be the toggle inside nxp_update, after events have been reset.
    assert result.index('s_nev = 0;', result.index('void nxp_update(void)')) < result.index('R3 toggles direct-thrust mode')
    assert 'const int fire = ' in result
    backup = p.with_suffix(p.suffix + '.before_direct_stick.bak')
    if backup.exists():
        raise FileExistsError(f'Backup already exists; refusing to overwrite: {backup}')
    shutil.copy2(p, backup)
    p.write_text(result, encoding='utf-8')
    print(f'PATCHED: {p}')
    print(f'BACKUP: {backup}')
    print('Build using devkitPro. Original .nro binary is NOT modified by this script.')

if __name__=='__main__':
    if len(sys.argv) != 2:
        sys.exit('Usage: python apply_patch.py <path-to-osmos_nx-repository>')
    try:
        run(sys.argv[1])
    except (ValueError, OSError) as exc:
        sys.exit(f'ERROR: {exc}')
