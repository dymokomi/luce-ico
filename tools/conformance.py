#!/usr/bin/env python3
"""Read reference ICO and CUR files with luce-ico and compare every entry.

Oracles:

- Python Pillow, run here: for an icon, every directory entry (`ico.frame(i)`, the AND
  mask applied) against what luce-ico decodes for that entry, a PNG entry by decoding the
  bytes luce-ico hands back; for a cursor, the image Pillow opens against the largest
  entry. The entry Pillow opens an icon at (its largest) is checked against `choose`.
- Ladybird's LibGfx tests: the pixels its TestImageDecoder.cpp expects of its icons and
  cursor, and that its fuzzed icons fail to decode.

Pixels compare exactly, except that two fully transparent pixels are equal whatever
their colour. The corpora live in ../.donors (never in this repository):

  ../.donors/ladybird-pin/Tests/LibGfx/test-inputs/{ico,cur}
  ../.donors/pillow-tests/Tests/images (*.ico, *.cur)
  ../.donors/ico-corpus (favicons of twenty web sites, and made/: icons Pillow wrote in
  every format it writes, BMP and PNG entries, 1-bit to 32-bit, 16 to 256 pixels)

  python3 tools/conformance.py [--list-failing] [--python PYTHON-WITH-PILLOW]

LUCE_BASE names the compiler (default: luce-base on PATH).
"""
import argparse, json, os, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DONORS = ROOT.parent / ".donors"
LADYBIRD = DONORS / "ladybird-pin/Tests/LibGfx/test-inputs"
CORPORA = {
    "ladybird": [LADYBIRD / "ico", LADYBIRD / "cur"],
    "pillow": [DONORS / "pillow-tests/Tests/images"],
    "web": [DONORS / "ico-corpus"],
    "made": [DONORS / "ico-corpus/made"],
}
TOOL = ROOT / "build/icocheck"

# What Ladybird's TestImageDecoder.cpp expects: file -> [(x, y, RGBA)] of the entry
# shown (the largest), or None for a file whose image must fail to decode.
TRANSPARENT = (0, 0, 0, 0)
LADYBIRD_EXPECTS = {
    "cur/cursor.cur": [(0, 0, TRANSPARENT), (2, 2, (0, 0, 0, 255)), (8, 8, (255, 255, 255, 255))],
    "ico/serenity.ico": [(0, 0, TRANSPARENT), (7, 4, (161, 0, 0, 255))],
    "ico/malformed_maskless.ico": [(0, 0, TRANSPARENT), (7, 4, (161, 0, 0, 255))],
    "ico/yt-favicon.ico": [(14, 14, (234, 0, 0, 255)), (13, 15, (255, 10, 15, 255))],
    "ico/oss-fuzz-testcase-62541.ico": None, "ico/oss-fuzz-testcase-63177.ico": None,
    "ico/oss-fuzz-testcase-63357.ico": None,
}

# Where we differ from Pillow on purpose: name -> why.
SIZE = "an entry whose image is not the size the directory lists fails, as in Firefox and Ladybird; Pillow resizes it"
DELIBERATE = {
    "ladybird/oss-fuzz-testcase-62541.ico": SIZE,
    "pillow/hopper_unexpected.ico": SIZE,
    "made/paletted-bmp-entries.ico": "Pillow writes (and reads) AND mask rows unpadded; the format pads them to four "
                                     "bytes, as Windows and browsers read them",
}

PILLOW = r"""
import json, sys, warnings
from PIL import Image
warnings.simplefilter("ignore")
Image.MAX_IMAGE_PIXELS = None
out = {}
directory, paths = sys.argv[1], sys.argv[2:]
for n, path in enumerate(paths):
    try:
        with Image.open(path) as image:
            record = {"size": list(image.size), "frames": {}}
            if path.endswith(".png"):
                target = f"{directory}/{n}.pillow"
                open(target, "wb").write(image.convert("RGBA").tobytes())
                record["rgba"] = target
            elif image.format == "ICO":
                # Pillow sorts its entries; key its frames by where their images are.
                for index, header in enumerate(image.ico.entry):
                    try:
                        frame = image.ico.frame(index).convert("RGBA")
                        target = f"{directory}/{n}-{index}.pillow"
                        open(target, "wb").write(frame.tobytes())
                        record["frames"][header.offset] = {"size": list(frame.size), "rgba": target}
                    except Exception as failure:
                        record["frames"][header.offset] = {"error": f"{type(failure).__name__}: {failure}"}
            else:
                target = f"{directory}/{n}.pillow"
                open(target, "wb").write(image.convert("RGBA").tobytes())
                record["rgba"] = target
            out[path] = record
    except Exception as failure:
        out[path] = {"error": f"{type(failure).__name__}: {failure}"}
print(json.dumps(out))
"""


def build():
    base = os.environ.get("LUCE_BASE", "luce-base")
    TOOL.parent.mkdir(exist_ok=True)
    subprocess.run([base, "build", str(ROOT / "tools/icocheck.lucb"), "-o", str(TOOL), "--release"], cwd=ROOT, check=True)


def decode(paths, directory):
    """Run icocheck over `paths`: per path, its kind, chosen entries and entries."""
    listing = Path(directory) / "list.txt"
    listing.write_text("".join(f"- {p}\n" for p in paths))
    result = subprocess.run([str(TOOL), str(listing), directory], capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        sys.exit(f"icocheck failed:\n{result.stderr}")
    found = {}
    for line in result.stdout.splitlines():
        number, status, rest = line.split(" ", 2)
        path = paths[int(number)]
        if status == "error":
            found[path] = {"error": rest}
        elif status == "ok":
            kind, count, largest, closest = rest.split(" ")
            found[path] = {"kind": kind, "count": int(count), "largest": int(largest), "closest": int(closest), "entries": []}
        else:
            index, width, height, _, _, bits, form, hot_x, hot_y, result_code = rest.split(" ")
            suffix = "png" if form == "png" else "rgba"
            found[path]["entries"].append({"size": [int(width), int(height)], "bits": int(bits), "png": form == "png",
                                           "hotspot": (int(hot_x), int(hot_y)), "result": result_code,
                                           "file": str(Path(directory) / f"{number}-{index}.{suffix}")})
    return found


def differing(a, b):
    count = 0
    for at in range(0, min(len(a), len(b)), 4):
        if a[at + 3] == 0 and b[at + 3] == 0:
            continue
        if a[at:at + 4] != b[at:at + 4]:
            count += 1
    return count + abs(len(a) - len(b)) // 4


def entry_pixels(entry, pillow_out):
    """Our pixels of an entry: decoded BMP bytes, or the PNG bytes as Pillow decodes them."""
    if entry["result"] != "ok":
        return None
    if entry["png"]:
        decoded = pillow_out.get(entry["file"], {})
        return Path(decoded["rgba"]).read_bytes() if "rgba" in decoded else None
    return Path(entry["file"]).read_bytes()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-failing", action="store_true")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--no-build", action="store_true")
    options = parser.parse_args()
    if not options.no_build:
        build()
    named = {}
    for key, folders in CORPORA.items():
        for folder in folders:
            for path in sorted(list(folder.glob("*.ico")) + list(folder.glob("*.cur"))):
                named[str(path)] = f"{key}/{path.name}"
    paths = list(named)
    with tempfile.TemporaryDirectory() as directory:
        ours = decode(paths, directory)
        pngs = [e["file"] for v in ours.values() for e in v.get("entries", []) if e["png"] and e["result"] == "ok"]
        result = subprocess.run([options.python, "-c", PILLOW, directory, *paths, *pngs], capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            sys.exit(f"Pillow failed:\n{result.stderr}")
        theirs = json.loads(result.stdout)
        rows, entries_total, entries_matched = [], 0, 0
        for path in paths:
            name, got, other = named[path], ours[path], theirs[path]
            if "error" in got or "error" in other:
                ours_fail = "error" in got or all(e["result"] != "ok" for e in got["entries"])
                both = ours_fail and "error" in other
                rows.append((name, "" if both else (f"we fail ({got['error'][:50]})" if "error" in got else
                                                     f"Pillow fails ({other['error'][:50]})")))
                continue
            problems = []
            if got["kind"] == "cursor" or not other["frames"]:
                pairs = [(got["entries"][got["largest"]], {"size": other["size"], "rgba": other.get("rgba")})]
            else:
                data = Path(path).read_bytes()
                offsets = [int.from_bytes(data[6 + 16 * i + 12:6 + 16 * i + 16], "little") for i in range(len(got["entries"]))]
                pairs = [(entry, other["frames"].get(str(offsets[index]), {"error": "missing"})) for index, entry in enumerate(got["entries"])]
                largest = got["entries"][got["largest"]]["size"]
                if largest != other["size"] and largest[0] * largest[1] != other["size"][0] * other["size"][1]:
                    problems.append(f"largest {largest} vs Pillow {other['size']}")
            for index, (entry, frame) in enumerate(pairs):
                entries_total += 1
                mine = entry_pixels(entry, theirs)
                if "error" in frame or mine is None:
                    if ("error" in frame) != (mine is None):
                        problems.append(f"entry {index}: " + ("we fail" if mine is None else "Pillow fails"))
                    else:
                        entries_matched += 1
                    continue
                if entry["size"] != frame["size"]:
                    problems.append(f"entry {index}: size {entry['size']} vs {frame['size']}")
                    continue
                count = differing(mine, Path(frame["rgba"]).read_bytes())
                if count:
                    problems.append(f"entry {index}: {count} px differ")
                else:
                    entries_matched += 1
            rows.append((name, "; ".join(problems[:4])))
        ladybird = []
        for name, expected in LADYBIRD_EXPECTS.items():
            got = ours[str(LADYBIRD / name)]
            entry = got["entries"][got["largest"]] if "entries" in got else None
            if expected is None:
                ladybird.append((name, "" if entry is None or entry["result"] != "ok" else "decodes, Ladybird expects a failure"))
                continue
            if entry is None or entry["result"] != "ok":
                ladybird.append((name, "fails to decode"))
                continue
            pixels, width = Path(entry["file"]).read_bytes(), entry["size"][0]
            wrong = [f"({x},{y}) {tuple(pixels[(y * width + x) * 4:(y * width + x) * 4 + 4])}" for x, y, rgba in expected
                     if tuple(pixels[(y * width + x) * 4:(y * width + x) * 4 + 4]) != rgba and not (rgba[3] == 0 and pixels[(y * width + x) * 4 + 3] == 0)]
            ladybird.append((name, ", ".join(wrong)))
    failing = 0
    print(f"\nLadybird's expectations: {len(ladybird)} files, {sum(1 for _, p in ladybird if not p)} as expected")
    for name, problem in ladybird:
        if problem:
            failing += 1
            print(f"  DIFF {name}: {problem}")
    matched = [r for r in rows if not r[1]]
    deliberate = [r for r in rows if r[1] and r[0] in DELIBERATE]
    unexplained = [r for r in rows if r[1] and r[0] not in DELIBERATE]
    print(f"\nPillow 12, every corpus: {len(rows)} files ({entries_total} entries, {entries_matched} match), {len(matched)} files match, "
          f"{len(deliberate)} differ on purpose, {len(unexplained)} unexplained")
    for name, problem in unexplained:
        failing += 1
        print(f"  DIFF {name}: {problem}")
    if options.list_failing:
        for name, problem in deliberate:
            print(f"  on purpose {name}: {DELIBERATE[name]} [{problem}]")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
