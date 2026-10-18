# Synthetic RAR regression fixtures

These four small CBR files contain original generated PNGs and plain text. They
contain no user comic pages, logs, downloaded artwork, or compressor binaries.
The synthetic fixture content and archives are dedicated under CC0-1.0.

| Fixture | Format | Solid | Bytes |
| --- | --- | --- | ---: |
| `rar4_nonsolid.cbr` | RAR4 | No | 7,495 |
| `rar4_solid.cbr` | RAR4 | Yes | 2,497 |
| `rar5_nonsolid.cbr` | RAR5 | No | 6,967 |
| `rar5_solid.cbr` | RAR5 | Yes | 956 |

Each archive holds four 32 x 24 PNGs and two non-image files. Image paths contain
Cyrillic UTF-8 text, nested directories, and an uppercase `.PNG` extension.
Physical image order differs from lexical filename order. Non-image members
precede later images, exercising decompressor state across skipped members.
The first physical member is an image, preserving the old cover-only failure
pattern in the RAR4 solid fixture.

`manifest.json` records expected uncompressed byte counts, SHA-256 and CRC32 for
every member, archive hashes, physical and lexical order, generation commands,
and the provenance/hash of the separately obtained RAR 6.24 creation tool.

## Regeneration

Run from any directory with the official RAR 6.24 Linux x64 executable supplied
separately:

```sh
python generate_fixtures.py --rar /absolute/path/to/rar
```

The generator uses Python's standard library to create the PNGs. Fixed input
timestamps, single-threaded compression, disabled archive sorting, and explicit
file arguments preserve the documented output. It checks the RAR executable
hash and verifies archive integrity and physical order after generation. RAR
6.24 is used because it supports creation of both RAR4 and RAR5 archives.
The executable is used locally under its trial terms and is not redistributed.

The documented archives were regenerated twice with identical hashes. Their
contents were then independently extracted with official UnRAR 7.23 (source
package 7.2.7): every byte count, CRC32 and SHA-256 matched the manifest, and all
PNG images decoded fully.

## Negative cases

`runtime_negative_cases` describes variants the test runner creates in a
temporary directory. No duplicate damaged binaries need to be committed.

- A target member with an incorrect data CRC32.
- A non-image predecessor with an incorrect data CRC32 before a requested image.
- An archive truncated inside the last image's compressed stream.
- A request for a member absent from the archive.

For the two CRC variants, XOR the member's little-endian data CRC32 with 1, then
recompute its header CRC16 as `crc32(header[2:]) & 0xffff`. The manifest includes
the RAR4 offsets needed for this mutation. This creates a data integrity failure
while keeping the header valid. Official UnRAR 7.23 reports a checksum error
for both variants; the truncated variant reports a checksum error and an
unexpected archive end.

In an independent libarchive 3.7.2 probe, RAR4 solid fails on the second member
with `RAR solid archive support unavailable.`. The RAR4 non-solid and both RAR5
fixtures reach a clean archive EOF. This distinction guards against attributing
the old limitation to all solid archive formats.
