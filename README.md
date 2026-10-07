# luce-ico

An ICO and CUR decoder for Luce/Base: the directory, choosing an entry, BMP entries
decoded with their AND masks (through luce-bmp), PNG entries handed back as PNG bytes,
and cursor hotspots. It depends on luce-bmp and nothing else; a PNG entry is decoded by
the caller (with luce-png, say), so icons pull in no PNG decoder of their own. The
browser engine (favicons, `<img src=x.ico>`) and luce-image read icons through it.

```luce
from luce_ico import ico

let found = try ico.info(data)                    # icon or cursor, entry count
let best = try ico.choose(data)                   # the largest; ico.choose(data, 32) for 32 px
if best.png:
    try png.decode_rgba8(ico.image(data, &best), pixels)
else:
    try ico.decode_rgba8(data, &best, pixels)     # best.width * best.height * 4 bytes
let hotspot = (best.hotspot_x, best.hotspot_y)    # cursors
```

## API

- `info(data) -> Info!`: `kind` (`icon` or `cursor`) and `count`, the directory entries
  the file holds in full.
- `entry_at(data, index) -> Entry!`: `width` and `height` from the image's own header
  (the PNG's IHDR, the DIB's info header), `listed_width` and `listed_height` as the
  directory lists them (0 is 256), `bits` (a PNG's bit depth times its channels, the
  DIB's depth), `png`, `readable` (the image's header was read and gives the listed size)
  and a cursor's `hotspot_x` and `hotspot_y`.
- `choose(data, size = 0) -> Entry!`: with `size` 0 the largest image, else the smallest
  at least `size` pixels wide (the largest when none is); ties go to more bits, then the
  earlier entry; unreadable entries come last.
- `image(data, entry) -> const u8[]`: the entry's bytes, a PNG or a DIB with its mask.
- `decode_rgba8(data, entry, out, options)`: a BMP entry as straight RGBA, the AND mask
  applied where the image has no alpha of its own (fewer than 32 bits, or an alpha
  channel zero throughout). The DIB is read from its offset as far as its header says,
  even past the length the directory gives it, as some files need.
- `Options.max_pixels` (default 2^28).
- Errors: `corrupt` (not an ICO or CUR file, no images, an entry damaged, outside the
  file, or not the size the directory lists), `png` (the entry is a PNG: decode its
  `image` bytes), `unsupported` (a BMP form luce-bmp does not decode), `limit`, `invalid`
  (an index past the last entry, an output buffer too small).

As Firefox and Ladybird require, an entry whose image is not the size the directory
lists (other than larger than 256 where it lists 256) does not decode. A directory cut
short keeps its whole entries; an image cut short decodes the rows it holds.

## Tests

```
luc test                                    # unit tests, and tests/tools: tools/icocheck builds
tools/check.sh                              # lint: -W and fmt
python3 tools/conformance.py --python VENV/bin/python --list-failing
python3 tools/fuzz.py --cases 20000 [--guard-malloc]
```

The unit tests (`tests/ico/`) write their icons with a small builder. `tools/icocheck`
reads a list of files, choosing and decoding every entry; `tools/conformance.py` compares
them, on corpora kept outside the repository in `../.donors/`, with Ladybird's
expectations of its test icons and cursor (pixels, and failures for its fuzzed files),
and with Python Pillow, entry by entry, on Ladybird's and Pillow's test files, the
favicons of twenty web sites, and icons Pillow wrote in every format it writes (BMP and
PNG entries, 1 to 32 bits, 16 to 256 pixels):

| Oracle | Files | Match | Differ on purpose | Unexplained |
| --- | ---: | ---: | ---: | ---: |
| Ladybird's expectations | 7 | 7 | 0 | 0 |
| Pillow 12.3 (97 entries, 94 match) | 44 | 41 | 3 | 0 |

The deliberate differences: two entries whose images are not the size their directories
list fail, as in Firefox and Ladybird, where Pillow resizes them; and an icon Pillow wrote
has its AND mask rows unpadded, which Pillow reads back the same way while the format
(and Windows, and browsers) pads them to four bytes.

`tools/fuzz.py` mutates the corpora (20,000 cases: bit flips, insertions, truncations,
spliced chunks, the directory's own fields) and reads, chooses and decodes every entry
of each under a time limit, after a stress phase (65535 entries, offsets and lengths
past the end of the file, PNG entries claiming 4 billion pixels a side, a 2048 x 2048
entry, each well under two seconds): no traps, no hangs, the peak memory of a decoding
process 66 MB. Under Guard Malloc (`--guard-malloc`, and the unit tests run with
`DYLD_INSERT_LIBRARIES=/usr/lib/libgmalloc.dylib`) nothing is reported.
