# WookReader RU3

Build identity: `0.8.0-RU3`. The executable remains
`/switch/WookReader/WookReader.nro`, with the original icon asset and Russian UI.
The [RARFIX2 extraction repair](RAR_FIX.md) is retained unchanged.

## Cover failure: confirmed JPEG symbol collision

The delivered RARFIX2 executable linked two incompatible JPEG implementations:
MuPDF's bundled IJG 9 (ABI 90) and the portlibs TurboJPEG implementation (ABI 62).
Both export `jpeg_CreateDecompress`. With the existing link order and duplicate
symbol suppression, TurboJPEG called the IJG 9 function. The actual AArch64 NRO
passes 62 to a callee that checks for 90; initialization fails before reading
any image bytes.

The ordinary cover fallback rejects images above 12 million pixels. A supplied
progressive JPEG cover of 6287 by 3000 pixels therefore failed in the library,
while the reader could still show the page through its separate fallback. The
RAR backend extracted that first page with the correct CRC and bytes. The
archive's overall size alone did not explain this failure.

`isolate_jpeg.py` gives every external symbol defined by MuPDF's JPEG objects
and its custom JPEG allocator a `wookmupdf_` prefix throughout `libmupdf.a`.
Definitions and references, including MuPDF consumers, are renamed together.
The tool verifies archive membership, symbol preservation and absence of
unprefixed bundled JPEG references. It does not change JPEG link order, remove
the cover memory guard or replace MuPDF's allocator with an incompatible one.

After linking, the same tool reads the final ELF and checks the AArch64 call
from TurboJPEG to the actual ABI 62 initializer. The separate prefixed
initializer must still check for ABI 90. `jpeg-isolation.json` records this
check and the linked ELF hash. The build cannot package an unchecked link.

The host JPEG regression builds the pinned TurboJPEG 2.1.2 implementation and
the exact bundled IJG sources. It first reproduces the mixed-link failure, then
tests the production isolation helper with generated baseline and large
progressive images. Both JPEG APIs must decode after isolation. Supplied comic
files, extracted artwork and user logs are not published in this repository.

`fix_covers.py` adds errors for decoder initialization, oversized fallback
images, surface allocation, scaling and texture creation, including the
affected path. A selected cover can be refreshed from the library's action
menu. This removes only that cover's cached compressed image (`.cvr`) and the
paired archive-member name (`.cvrn`), plus its in-memory thumbnail.

## Persistent UTF-8 library

`WookLibrary.cpp` stores full UTF-8 paths as quoted values in anonymous libconfig
records. Paths are never configuration identifiers. Cyrillic, numeric-leading
names and two identically named books in different folders can have independent
progress, favorites and view state. Display/search use UTF-8-safe truncation,
Russian/Ukrainian case folding and natural numeric order.

New state is in `/switch/WookReader/library.cfg`. The old `saved_pages.cfg`
is read for compatible legacy progress and left unchanged. Historical progress
was keyed by a sanitized basename; an ambiguous old record is claimed by the
first matching full path instead of being copied to every namesake. Progress
that was never written by an older build cannot be reconstructed.

Writes use a completed temporary file and a retained previous `.bak`. A
malformed primary is preserved separately when recovery is saved. An unsupported
future schema blocks overwriting. Writes during reading are limited to one per
15 seconds, with an explicit flush on exit and after library edits. This is
application-level replacement and recovery; it is not a guarantee against SD
removal or power loss during a filesystem operation.

## Collections and library interface

Collections keep ordered references to original comic paths. They do not move,
rename, unpack or combine archives. Users can create/rename a collection, add
and remove members, change their order and choose a member's cover. Removing a
collection removes its metadata only.

Automatic series require at least two numbered issues with a common prefix
in the same folder. For example, `Рик и Морти 001.cbr`, `Рик и Морти 002.cbr`
and `Рик и Морти 010.cbr` are sorted by their issue numbers. Volume/year prefixes, annuals and different
folders remain distinct. Manual edits persist independently of automatic
discovery. Unnumbered or ambiguous names can be grouped manually.

The library provides collection, all-comic and recent views, title search,
favorites and unread filtering, per-book progress and a complete selected
title. Continue chooses an unfinished issue according to the collection's
reading history. The reader can offer the next or previous issue in that
collection, preserving the original ordered paths.

Search scans comic roots and folders known to the current library session.
The SD root is scanned shallowly; system/game directories are not recursively
traversed. Folder browsing remains available for other locations.

## Reader controls and errors

The three Y modes now agree with their navigation state. Comics use single
page, rotation and spread. Other supported documents use page fit, rotation
and width fit. ZL/ZR remain page turns in every reading mode. Contextual help
distinguishes reading controls from the menu's own controls.

Zoom is saved relative to a fitted page, and pan is saved as a relative visible
position, so reopening or turning to a differently sized page can restore a
bounded view. Keep-zoom and keep-position settings can be changed independently.
Load failures stay visible with Retry and Back. Opening shows actual archive
stages and discovered page counts, without a fabricated percentage.

Help, notes and issue prompts consume their own input before ordinary reading
commands. Notes scroll and use a path-derived file identity while retaining a
legacy-file fallback. The status clock uses 24-hour time, and button labels
are drawn with the same font used to measure them.

## Battery saver

Battery saver is enabled by default and can be toggled in library settings.
An unchanged reader/library frame is kept on screen instead of being redrawn
continuously. Controller input and completed background work are still polled
with an idle sleep of 16 milliseconds; changed input, asynchronous work,
status changes and timed overlays invalidate the frame.

Cover workers wait on libnx events instead of waking every two milliseconds.
Jobs, result acknowledgement and shutdown all signal the event, with predicate
checks to prevent missed wakeups. Failed thread creation/start remains guarded.
The saver prepares one forward comic page. The normal profile retains the
previous bounded forward/backward prefetch of two active workers.

Status timing uses elapsed time, independent of rendering. The clock is polled
once per second and battery state once per five seconds, with redraw only when
the displayed value changes. This build does not alter hardware clock rates,
brightness or system power policy. Battery-life percentage or runtime gains
require measurement on a physical Switch and are not asserted by host tests.

## Build and validation

The workflow pins the upstream source and devkitPro container. Patches apply in
this order: `apply_ru.py`, `optimize.py`, `fix_rar.py`, `fix_covers.py`,
`install_state.py`, `enhance_reader.py`, `enhance_library.py`, `enhance_power.py`.
It then builds UnRAR, MuPDF, isolates the JPEG symbols, builds the NRO and checks
the final ELF before packaging.

Meaningful host gates cover the actual RAR backend, thread startup and initial
reader state, logging concurrency, JPEG API isolation, persistent library
round trips, reader view math, event-worker handoff and power-profile timing.
The library UI is replayed through real native SDL/TTF with the bundled font
and synthetic cover/reader adapters. It exercises collections, search, filters,
notes, settings and grid sizes. This replay uses the chooser snapshot before
the independently tested power gate. Native SDL is installed only after target
compilation, so it cannot shadow the Switch SDL configuration during linking.
The NRO gate checks asset bounds, NACP version/title, Russian strings, build
markers and byte-for-byte original icon identity. Reports and build provenance
are included in the installation ZIP; the ELF/map are a separate debug artifact.

These tests do not execute Horizon or measure physical console power. The
installation guide includes a short on-console acceptance check and rollback
to the saved previous NRO.
