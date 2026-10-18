#!/usr/bin/env python3
import concurrent.futures, re, shutil, subprocess, sys
from pathlib import Path
source=Path(sys.argv[1]).resolve()
dest=Path(sys.argv[2]).resolve()
prefix=sys.argv[3] if len(sys.argv)>3 else ''
work=dest/'unarr-build'
work.mkdir(parents=True,exist_ok=True)
src=work/'src'
if src.exists(): shutil.rmtree(src)
shutil.copytree(source,src,ignore=shutil.ignore_patterns('.git','test','build*'))
# Avoid off64_t/time64_t conflicts with newlib/glibc headers.
for f in src.rglob('*'):
    if f.suffix in ('.c','.h','.in'):
        t=f.read_text(encoding='utf-8')
        t=re.sub(r'\boff64_t\b','ar_off64_t',t)
        t=re.sub(r'\btime64_t\b','ar_time64_t',t)
        f.write_text(t,encoding='utf-8')
t=(src/'unarr.h.in').read_text()
for key,value in {'unarr_VERSION':'1.2.0','unarr_VERSION_MAJOR':'1','unarr_VERSION_MINOR':'2','unarr_VERSION_PATCH':'0'}.items():
    t=t.replace('@'+key+'@',value)
(src/'unarr.h').write_text(t)
cm=(src/'CMakeLists.txt').read_text()
block=cm[cm.index('# Build target'):cm.index('# Set version in header')]
files=sorted(set(re.findall(r'([A-Za-z_0-9/.-]+\.c)\b',block)) - {'common/custalloc.c'})
files.append('lzmasdk/LzmaDec.c')
objects=[]
flags=['-std=gnu11','-O2','-fPIC','-fno-lto','-ffunction-sections','-fdata-sections','-DNDEBUG','-I'+str(src)]
if prefix: flags += ['-march=armv8-a+crc+crypto','-mtune=cortex-a57','-mtp=soft']
else: flags += ['-fsanitize=address','-fno-omit-frame-pointer']

def compile_one(name):
    obj=work/(name.replace('/','_')+'.o')
    subprocess.run([prefix+'gcc',*flags,'-c',str(src/name),'-o',str(obj)],check=True)
    return obj
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    objects=list(pool.map(compile_one,files))
# All unarr symbols are namespaced at object level, avoiding collisions with
# PPMd symbols in libarchive/MuPDF and their statically-linked dependencies.
symbols=set()
for obj in objects:
    s=subprocess.check_output([prefix+'nm','-g','--defined-only','--format=posix',str(obj)],text=True)
    for line in s.splitlines():
        cols=line.split()
        if len(cols)>=2 and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',cols[0]): symbols.add(cols[0])
mapfile=work/'symbols.txt'
mapfile.write_text(''.join(f'{s} wook_unarr_{s}\n' for s in sorted(symbols)))
for obj in objects: subprocess.run([prefix+'objcopy','--redefine-syms='+str(mapfile),str(obj)],check=True)
inc=dest/'include'; lib=dest/'lib'
inc.mkdir(exist_ok=True,parents=True); lib.mkdir(exist_ok=True,parents=True)
shutil.copy2(src/'unarr.h',inc/'unarr.h')
(inc/'unarr_prefix.h').write_text('#pragma once\n'+''.join(f'#define {s} wook_unarr_{s}\n' for s in sorted(symbols)))
subprocess.run([prefix+'ar','rcs',str(lib/'libunarr.a'),*map(str,objects)],check=True)
print('Unarr built:',len(objects),'objects;',len(symbols),'isolated symbols;',prefix or 'native ASAN')
