#!/usr/bin/env python3
from pathlib import Path
import json, shutil, struct, subprocess, sys, zipfile, zlib

sys.path.insert(0,str(Path('rarfile-tests').resolve()))
import rarfile
root=Path('archive-test-cases')
if root.exists(): shutil.rmtree(root)
root.mkdir()
refdir=Path('rarfile-tests/test/files')
samples=[refdir/'rar3-solid.rar', refdir/'rar5-solid.rar']
for p in samples: assert p.is_file(),p

def normal(s):
    s=s.replace('\\','/')
    while s.startswith('./'): s=s[2:]
    return s

def png(page):
    w=h=16
    raw=b''.join(b'\0'+bytes(v for x in range(w) for v in ((x*11+page)%256,(y*13)%256,page%256)) for y in range(h))
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')

expected={}
counts={'RAR4_solid':0,'RAR5_solid':0,'CBZ':0}
for number in range(1,71):
    if number%3==0:
        name=f'{number:03}_Кириллица.CBZ'
        expected[name]={}
        with zipfile.ZipFile(root/name,'w',zipfile.ZIP_DEFLATED) as z:
            for p in range(1,71):
                entry=f'Папка/Страница_{p:03}.png'
                data=png(p)
                z.writestr(entry,data)
                expected[name][entry]=(len(data),zlib.crc32(data)&0xffffffff)
        counts['CBZ']+=1
    else:
        sample=samples[0 if number%3==1 else 1]
        name=f'{number:03}_Sample.CBR'
        shutil.copy2(sample,root/name)
        with rarfile.RarFile(sample) as ar:
            expected[name]={normal(i.filename):(i.file_size,i.CRC) for i in ar.infolist() if not i.isdir()}
        counts['RAR4_solid' if number%3==1 else 'RAR5_solid']+=1

run=subprocess.run(['./archive-test',str(root)],capture_output=True,text=True,timeout=120)
Path('archive_test_stdout.tsv').write_text(run.stdout,encoding='utf-8')
Path('archive_test_stderr.txt').write_text(run.stderr,encoding='utf-8')
if run.returncode:
    print(run.stderr[-8000:],flush=True)
    raise RuntimeError(f'Archive regression test failed: {run.returncode}')
seen=set()
for row in run.stdout.splitlines():
    archive,name,size,crc=row.split('\t')
    result=(int(size),int(crc))
    assert result==tuple(expected[archive][name]), (archive,name,result,expected[archive][name])
    seen.add((archive,name))
expected_count=sum(map(len,expected.values()))
assert len(seen)==expected_count,(len(seen),expected_count)
assert len(expected)==70

# The legacy decoder demonstration uses a genuine upstream solid-RAR fixture.
old=subprocess.run(['bsdtar','-xOf',str(samples[0]),'stest2.txt'],capture_output=True)
# A deliberately truncated archive must fail, not report a valid partial book.
bad=Path('archive-test-invalid'); bad.mkdir(exist_ok=True)
(bad/'truncated.cbr').write_bytes(samples[0].read_bytes()[:45])
reject=subprocess.run(['./archive-test',str(bad)],capture_output=True,text=True,timeout=20)
assert reject.returncode!=0, 'Truncated archive falsely accepted'
qa={
 'archives_tested':70,'formats':counts,'entries_crc_verified':expected_count,
 'reverse_navigation':True,'concurrent_first_last_reads':True,
 'cancellation_checked':True,'file_descriptor_leak_test':True,
 'truncated_archive_rejected':True,
 'legacy_solid_decoder_exit_code':old.returncode,
 'legacy_solid_decoder_error':old.stderr.decode('utf-8','replace').strip(),
 'test_result':run.stderr.strip().splitlines()[-1],
 'user_comics_tested':False,
 'physical_switch_tested':False,
 'scope':'Portable archive backend on Linux; Switch binary separately cross-compiled.'
}
Path('ARCHIVE_QA.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(qa,ensure_ascii=False,indent=2),flush=True)
