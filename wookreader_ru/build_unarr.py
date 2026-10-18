#!/usr/bin/env python3
from pathlib import Path
import os, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

src=Path(sys.argv[1]).resolve()
out=Path(sys.argv[2]).resolve()
mode=sys.argv[3]
out.mkdir(parents=True,exist_ok=True)
header=(src/'unarr.h.in').read_text()
for k,v in {'unarr_VERSION_MAJOR':'1','unarr_VERSION_MINOR':'2','unarr_VERSION_PATCH':'0','unarr_VERSION':'1.2.0'}.items():
    header=header.replace('@'+k+'@',v)
assert not re.search(r'@[A-Za-z_]+@',header)
(src/'unarr.h').write_text(header)
files=[src/'common'/f for f in ['conv.c','crc32.c','stream.c','unarr.c']]
files+=sorted((src/'rar').glob('*.c'))
files+=[src/'lzmasdk'/f for f in ['CpuArch.c','Ppmd7.c','Ppmd7Dec.c','Ppmd7aDec.c','Ppmd8.c','Ppmd8Dec.c']]
flags=['-O2','-std=gnu11','-DNDEBUG','-D_POSIX_C_SOURCE=200809L','-fPIC','-I'+str(src)]
if mode=='switch':
    base=Path(os.environ.get('DEVKITPRO','/opt/devkitpro'))/'devkitA64/bin'
    cc=str(base/'aarch64-none-elf-gcc'); ar=str(base/'aarch64-none-elf-ar')
    flags+=['-march=armv8-a+crc+crypto','-mtune=cortex-a57','-mtp=soft','-D__SWITCH__']
else:
    cc='gcc'; ar='ar'

def compile_one(f):
    assert f.is_file(),f
    obj=out/('_'.join(f.relative_to(src).parts)+'.o')
    subprocess.run([cc,*flags,'-c',str(f),'-o',str(obj)],check=True)
    return obj
with ThreadPoolExecutor(max_workers=2) as pool:
    objs=list(pool.map(compile_one,files))
lib=out/'libwookunarr.a'
subprocess.run([ar,'rcs',str(lib),*[str(p) for p in objs]],check=True)
print('Built',mode,lib,len(objs),'objects',flush=True)
