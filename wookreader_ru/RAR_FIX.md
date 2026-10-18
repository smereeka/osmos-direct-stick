# WookReader RU2: RAR extraction repair

Build identity: `0.7.0-RU2-rar1`.

## Confirmed failure and scope

The old reader enumerated RAR4 member headers with its own seek-based parser,
but extracted images with libarchive. A valid solid RAR4 could therefore list
all its pages while libarchive rejected the second solid member with
`RAR solid archive support unavailable.`. The extraction loop treated any
non-OK header status like end-of-archive and logged `entry not found`.

This was reproduced locally with a supplied 25-page solid RAR4 and with the
original synthetic RAR4 solid fixture in this directory. Explicit skipping,
implicit skipping, and completely draining the first page all gave the same
libarchive failure. Official stable UnRAR independently verified every page of
the supplied archive. That archive and its log are not part of this repository.

RAR5 uses a different libarchive backend; its solid support is not the same
limitation. A matching user-visible symptom in another untested file does not
establish that file's precise cause. These checks do not establish a numerical
library-size limit or certify execution on physical Nintendo Switch hardware.

## Repair

- `WookRAR.cpp` is shared by page enumeration, page extraction and cover workers.
  The synchronous enumeration fallback uses it too.
- Actual RAR4/RAR5 signatures select UnRAR. Other archive formats keep their
  existing libarchive path, including a ZIP archive named `.cbr`.
- The stable official UnRAR 7.23 source package is pinned by URL and SHA-256.
  `build_unrar.py` records its version, compiler, flags and platform changes.
- Each extraction opens a fresh extraction-mode session. Solid predecessors
  are tested and discarded in order, reconstructing the decompression
  dictionary. The requested image is collected through `UCM_PROCESSDATA`.
- Images become visible to callers and caches only after successful UnRAR
  CRC/hash verification and an exact uncompressed-size match. No archive member
  is extracted to its named filesystem path.
- A single mutex covers open, listing, decoding and close because UnRAR's error
  handler is global. Cancellation is checked while waiting for that mutex and
  while processing both predecessors and the target.
- UTF-8 archive paths, wide member names and slash normalization use the same
  policy in the host test build and on Switch. No global locale is required.
- RAR header names are refreshed through UnRAR instead of trusting old RAR page
  lists. Libarchive traversal failures retain the actual decoder error.

The reader bounds one compressed image's output to 64 MiB and a RAR dictionary
to 64 MiB. Multipart archives, password-protected headers/members and redirected
entries receive explicit errors. It keeps one requested image output buffer;
it does not unpack an entire comic onto the SD card.

## Build and regression checks

The workflow pins upstream WookReader and the previously successful devkitPro
container, applies `apply_ru.py`, `optimize.py`, then `fix_rar.py`, builds UnRAR,
MuPDF and the NRO, and validates the packaged executable.

To run the host decoder tests:

```sh
python3 wookreader_ru/build_unrar.py /tmp/wookreader-unrar-host --host
python3 wookreader_ru/tests/test_rar_backend.py \
  --unrar-prefix /tmp/wookreader-unrar-host \
  --report /tmp/wookreader-rar-tests.json
```

The host harness compiles the exact `WookRAR.cpp` delivered in the NRO. The
synthetic matrix covers RAR4/RAR5, solid/non-solid, UTF-8 and nested names,
physical versus lexical order, non-image predecessors, repeated opens,
backward/random extraction and concurrent workers. Negative checks exercise
bad data CRCs, a truncated payload, a genuinely missing member, allocation
failure, cancellation during decoding or mutex wait, and successful recovery.

The workflow checks the backend source hash in the report against the copied
app source. It also verifies the NRO header/assets, Russian UI strings, NACP
identity and exact original icon bytes. Artifacts include the test report,
source/build provenance, checksums and UnRAR license.

## Existing WOOK icon

Replace `/switch/WookReader/WookReader.nro` with the new executable. Its filename
and location remain the same, so an existing WOOK forwarder targeting this path
continues to locate it. Russian UI/UTF-8 rendering and the original icon asset
are retained. Books, notes, saved pages and user settings stay in place.

The new log starts with `WOOK RU2 RARFIX1` and reports `RAR backend=UnRAR` for
RAR books. Verify the affected book's second, last and previous pages on the
console; a physical-console test remains separate from host and build checks.
