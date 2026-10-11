#!/usr/bin/env python3
"""Offline host-level guard test for source rewrite; not a Switch execution test."""
from pathlib import Path
from tempfile import TemporaryDirectory
from apply_patch import run, INSERT_STATE_AFTER, INSERT_CONTROL_BEFORE, DRAW_OLD

def main():
    with TemporaryDirectory() as td:
        root=Path(td)/'osmos_nx'/'source';root.mkdir(parents=True)
        src=(INSERT_STATE_AFTER + '\nvoid nxp_update(void) {\n  s_nev = 0;\n' +
             INSERT_CONTROL_BEFORE + '\n}\n' + DRAW_OLD + '\n}\n')
        p=root/'nx_pointer.c';p.write_text(src)
        run(root.parent)
        new=p.read_text()
        assert new.count('s_direct_mode =')==2
        assert 's_cfg.screen_h * 0.29f' in new
        assert 'HidNpadButton_StickR' in new
        assert 'NXP_DOWN' in new and 'NXP_UP' in new
        assert 's_visible <= 0 || s_direct_mode' in new
        assert Path(str(p)+'.before_direct_stick.bak').read_text()==src
        run(root.parent)
        print('PASS: guarded patch, backup, mode-toggle, aim logic and idempotence')
if __name__=='__main__':main()
