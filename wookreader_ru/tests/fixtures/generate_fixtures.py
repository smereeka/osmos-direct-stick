#!/usr/bin/env python3
"""Regenerate original synthetic CBR fixtures using a separately supplied RAR CLI.

No third-party images, user comic pages, or compressor binaries are distributed.
The PNG encoder below uses only Python's standard library. RAR 6.24 is used
because it can create both RAR4 and RAR5; newer RAR versions removed RAR4 output.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import zlib

RAR_SOURCE = {
    'version': 'RAR 6.24 (3 Oct 2023)',
    'download_url': 'https://www.rarlab.com/rar/rarlinux-x64-624.tar.gz',
    'archive_sha256': '88e22a8e84125c947637bbf28c746e338a0a63279d80f9f9d7373603875db1eb',
    'executable_sha256': '71ab9088d575e438d243672fcc6d36d2f9a39f5a8d4d26e998129cea12dbf682',
    'redistributed': False,
}


def png(page):
    def chunk(kind, payload):
        return (struct.pack('>I', len(payload)) + kind + payload +
                struct.pack('>I', zlib.crc32(kind + payload)))
    width, height = 32, 24
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            # Deliberately similar pages exercise solid dictionary reuse.
            marker = page * 31 if x < 6 and y < 6 else 0
            rows.extend(((x * 7 + marker) % 256, (y * 11) % 256,
                         ((x + y) * 3) % 256))
    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) +
            chunk(b'IDAT', zlib.compress(bytes(rows), level=0)) +
            chunk(b'IEND', b''))


def input_members():
    return [
        ('Комикс/010_обложка.png', png(1)),
        ('notes/readme.txt', b'Original WookReader synthetic fixture.\n' * 64),
        ('Комикс/002_середина.PNG', png(2)),
        ('notes/metadata.json', b'{"synthetic":true,"copyrighted_comic":false}\n'),
        ('Комикс/nested/003_middle.png', png(3)),
        ('Комикс/001_начало.png', png(4)),
    ]


def rar4_file_headers(data, names):
    """Record mutation offsets so corruption cases can be made at test runtime."""
    offset = 7
    files = []
    while offset + 7 <= len(data):
        _, kind, flags, head_size = struct.unpack_from('<HBHH', data, offset)
        packed_size = struct.unpack_from('<I', data, offset + 7)[0] if flags & 0x8000 else 0
        if kind == 0x74:
            if flags & 0x100:
                packed_size += struct.unpack_from('<I', data, offset + 32)[0] << 32
            files.append({
                'name': names[len(files)], 'header_offset': offset,
                'header_size': head_size, 'data_crc32_offset': offset + 16,
                'packed_offset': offset + head_size, 'packed_size': packed_size,
                'solid_file_flag': bool(flags & 0x10),
            })
        offset += head_size + packed_size
        if kind == 0x7b:
            break
    if len(files) != len(names):
        raise RuntimeError('Unexpected RAR4 file header count')
    return files


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--rar', type=Path, required=True,
                    help='Path to the separately obtained official RAR 6.24 CLI')
    ap.add_argument('--output', type=Path, default=Path(__file__).resolve().parent)
    args = ap.parse_args()
    args.rar = args.rar.resolve()
    args.output = args.output.resolve()
    if hashlib.sha256(args.rar.read_bytes()).hexdigest() != RAR_SOURCE['executable_sha256']:
        raise SystemExit('Expected the documented official RAR 6.24 Linux x64 executable')
    args.output.mkdir(parents=True, exist_ok=True)
    members = input_members()
    names = [name for name, _ in members]
    files = [{'name': name, 'size': len(data),
              'sha256': hashlib.sha256(data).hexdigest(),
              'crc32': f'{zlib.crc32(data):08x}',
              'is_image': name.lower().endswith('.png')}
             for name, data in members]
    manifest = {
        'schema_version': 1,
        'description': 'Original synthetic PNG/text fixtures; no user data or external artwork.',
        'license': 'CC0-1.0 for the synthetic fixture content and archives.',
        'creator': RAR_SOURCE,
        'physical_entry_order': names,
        'page_names_sorted': sorted(f['name'] for f in files if f['is_image']),
        'images_per_archive': 4,
        'expected_files': files,
        'fixtures': [],
    }
    env = dict(os.environ, LC_ALL='C.UTF-8', TZ='UTC')
    with tempfile.TemporaryDirectory(prefix='wook_fixture_inputs_') as tmp:
        root = Path(tmp)
        for name, data in members:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o644)
            # RAR4 has a mandatory timestamp, even with -ts-. Keep it fixed.
            os.utime(path, (946684800, 946684800))
        for version in (4, 5):
            for solid in (False, True):
                name = f'rar{version}_{"solid" if solid else "nonsolid"}.cbr'
                dest = args.output / name
                switches = ['a', '-cfg-', f'-ma{version}', '-m5', '-md1m',
                            '-s' if solid else '-s-', '-ds', '-mt1', '-ts-',
                            '-qo-', '-idq']
                # Create in the scratch directory first, then replace atomically.
                temporary_archive = root / name
                command = [str(args.rar), *switches, str(temporary_archive), *names]
                run = subprocess.run(command, cwd=root, env=env, capture_output=True)
                if run.returncode:
                    raise RuntimeError(run.stdout.decode(errors='replace') +
                                       run.stderr.decode(errors='replace'))
                data = temporary_archive.read_bytes()
                dest.write_bytes(data)
                actual = subprocess.run([str(args.rar), 'lb', str(dest)],
                                        env=env, capture_output=True, check=True)
                actual_names = actual.stdout.decode('utf-8').splitlines()
                if actual_names != names:
                    raise RuntimeError(f'Physical member order mismatch: {actual_names!r}')
                test = subprocess.run([str(args.rar), 't', '-idq', str(dest)],
                                      env=env, capture_output=True, check=True)
                fixture = {
                    'name': name, 'format': f'RAR{version}', 'solid': solid,
                    'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                    'generation_command': ['rar', *switches, name, *names],
                    'physical_order_verified': True, 'creator_integrity_test_ok': True,
                }
                if version == 4:
                    fixture['rar4_file_headers'] = rar4_file_headers(data, names)
                manifest['fixtures'].append(fixture)
    solid4 = next(f for f in manifest['fixtures'] if f['name'] == 'rar4_solid.cbr')
    headers = solid4['rar4_file_headers']
    manifest['runtime_negative_cases'] = {
        'source_fixture': 'rar4_solid.cbr',
        'crc_mutation': 'XOR the little-endian data CRC32 with 1, then recompute '
                        'the header CRC16 as CRC32(header[2:]) & 0xffff. '
                        'Compressed data remains unchanged; integrity verification must fail.',
        'bad_target_crc': {'mutate_entry': headers[2]['name'],
                           'extract_entry': headers[2]['name']},
        'bad_nonimage_predecessor_crc': {'mutate_entry': headers[1]['name'],
                                         'extract_entry': headers[2]['name']},
        'truncated_last_member': {'truncate_to_bytes': headers[-1]['packed_offset'] +
                                                       max(1, headers[-1]['packed_size'] // 2),
                                  'extract_entry': headers[-1]['name']},
        'missing_member': {'extract_entry': 'Комикс/missing-page.png'},
    }
    (args.output / 'manifest.json').write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'files': manifest['fixtures'], 'images_per_archive': 4},
                     indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
