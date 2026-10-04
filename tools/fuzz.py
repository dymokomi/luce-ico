#!/usr/bin/env python3
"""Mutate icons and cursors and decode every entry: the decoder must never trap, hang or
grow without bound, whatever the bytes.

Each case is one of the corpus files (see conformance.py) changed by a few random
mutations: bytes flipped, inserted, deleted or repeated, the file cut short, chunks
spliced in from another file, and the format's own values (types, counts, sizes of 0 and
256, offsets and lengths set to 0 or 0xFFFFFFFF, a PNG signature, DIB header sizes and
depths) dropped in at random places, the directory most often. Cases run in batches
through build/icocheck (tools/icocheck.lucb, with `qs`: every entry read, chosen and
decoded, nothing written, a 4-megapixel limit), each batch under a time limit; a batch
that dies or times out is narrowed to the case responsible, which is saved under
build/fuzz-failures. The peak memory of the decoding processes is reported.

First, a stress phase reads generated files that are large or adversarial: a directory
of 65535 entries sharing one image, entries whose offsets and lengths point past the
file, PNG entries whose IHDR claims 4 billion pixels a side, and a 2048 x 2048 32-bit
entry with its mask. Each must finish in under two seconds.

  python3 tools/fuzz.py [--cases 20000] [--seed 1] [--guard-malloc]
"""
import argparse, os, random, resource, shutil, struct, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from conformance import CORPORA  # noqa: E402

TOOL = ROOT / "build/icocheck"
FAILURES = ROOT / "build/fuzz-failures"
# With --guard-malloc, the tool runs under macOS's Guard Malloc (every allocation on its
# own page, freed pages unmapped), which turns an overrun or a use after free into a crash.
ENVIRONMENT = dict(os.environ)
TOKENS = [b"\x00\x00\x01\x00", b"\x00\x00\x02\x00", b"\x01\x00", b"\xff\xff", b"\x00", b"\x10", b"\x20", b"\x00\x00",
          b"\x00\x00\x00\x00", b"\xff\xff\xff\xff", b"\x16\x00\x00\x00", b"\x28\x00\x00\x00", b"\x0c\x00\x00\x00",
          b"\x89PNG\r\n\x1a\n", b"IHDR", b"\x01\x00\x20\x00", b"\x01\x00\x08\x00", b"\x01\x00\x01\x00",
          b"\x00\x00\x00\x80", b"\x00" * 64, b"\xff" * 64]


def icon(entries, images, kind=1):
    """An icon of `entries` [(width, height, planes, bits)] and their `images` bytes."""
    offset = 6 + 16 * len(entries)
    directory, body = b"", b""
    for (width, height, planes, bits), data in zip(entries, images):
        directory += struct.pack("<BBBBHHII", width % 256, height % 256, 0, 0, planes, bits, len(data), offset + len(body))
        body += data
    return struct.pack("<HHH", 0, kind, len(entries)) + directory + body


def dib(width, height, pixel=b"\x10\x20\x30\x00", mask=b"\x55"):
    header = struct.pack("<IiiHHIIiiII", 40, width, height * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    return header + pixel * (width * height) + mask * ((width + 31) // 32 * 4 * height)


def stress_files():
    """Large and adversarial icons: (name, bytes)."""
    image = dib(16, 16)
    directory = b"".join(struct.pack("<BBBBHHII", 16, 16, 0, 0, 1, 32, len(image), 6 + 16 * 65535) for _ in range(65535))
    yield "65535 entries", struct.pack("<HHH", 0, 1, 65535) + directory + image
    far = b"".join(struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, 0xFFFFFFFF, 0xFFFFE000 + n) for n in range(5000))
    yield "offsets past the end", struct.pack("<HHH", 0, 1, 5000) + far
    head = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR\xff\xff\xff\xff\xff\xff\xff\xff\x08\x06\x00\x00\x00"
    yield "huge PNG entries", icon([(0, 0, 1, 32)] * 1000, [head] * 1000)
    yield "2048 x 2048 entry", icon([(0, 0, 1, 32)], [dib(2048, 2048)])


def stress():
    """Decode the stress files; answer the failures."""
    failures = []
    with tempfile.TemporaryDirectory() as directory:
        for name, data, *flags in stress_files():
            path = Path(directory) / "stress.ico"
            path.write_bytes(data)
            started = time.monotonic()
            done, crashed, trailer = run([(flags[0] if flags else "qs", str(path))], timeout=30)
            spent = time.monotonic() - started
            verdict = "trap" if crashed else ("slow" if spent > 2 else "ok")
            print(f"stress {name:<20} {len(data) / 1e6:6.2f} MB {spent:6.2f} s  {verdict}", flush=True)
            if verdict != "ok":
                failures.append(f"stress {name}: {verdict} {trailer.strip()[-200:]}")
    return failures


def mutate(data, rng, donors):
    data = bytearray(data)
    for _ in range(rng.randint(1, 6)):
        choice = rng.random()
        at = rng.randint(0, len(data))
        if choice < 0.2 and data:
            data[min(at, len(data) - 1)] ^= 1 << rng.randint(0, 7)
        elif choice < 0.3:
            data[at:at] = rng.choice(TOKENS)
        elif choice < 0.5 and len(data) > 4:
            token = rng.choice(TOKENS)
            spot = rng.randint(0, min(len(data), 6 + 16 * 8) - 1)
            data[spot:spot + len(token)] = token
        elif choice < 0.58 and data:
            del data[at:at + rng.randint(1, 16)]
        elif choice < 0.65 and data:
            del data[at:]
        elif choice < 0.75 and data:
            end = min(len(data), at + rng.randint(1, 64))
            data[at:at] = data[at:end] * rng.randint(1, 8)
        elif choice < 0.88:
            donor = rng.choice(donors)
            start = rng.randint(0, max(0, len(donor) - 1))
            data[at:at] = donor[start:start + rng.randint(1, 200)]
        elif data:
            data[min(at, len(data) - 1)] = rng.choice([0, 1, 2, 4, 8, 16, 24, 32, 40, 0x80, 0xff])
    return bytes(data)


def run(jobs, timeout):
    """Run `jobs` [(flags, path)] in one tool process: (finished count, crashed?, stderr)."""
    with tempfile.TemporaryDirectory() as directory:
        listing = Path(directory) / "list.txt"
        listing.write_text("".join(f"{flags} {path}\n" for flags, path in jobs))
        try:
            result = subprocess.run([str(TOOL), str(listing), directory], capture_output=True, timeout=timeout, env=ENVIRONMENT)
        except subprocess.TimeoutExpired as expired:
            return len((expired.stdout or b"").splitlines()), True, "timeout"
        return len(result.stdout.splitlines()), result.returncode != 0, result.stderr.decode("utf-8", "replace")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--batch", type=int, default=500)
    parser.add_argument("--no-stress", action="store_true")
    parser.add_argument("--guard-malloc", action="store_true")
    options = parser.parse_args()
    if options.guard_malloc:
        ENVIRONMENT["DYLD_INSERT_LIBRARIES"] = "/usr/lib/libgmalloc.dylib"
    stressed = [] if options.no_stress else stress()
    rng = random.Random(options.seed)
    seeds = sorted({p for folders in CORPORA.values() for folder in folders for pattern in ("*.ico", "*.cur")
                    for p in folder.glob(pattern) if p.stat().st_size < 400000})
    donors = [p.read_bytes() for p in seeds]
    failures = []
    done, crashed, trailer = run([("qs", str(seed)) for seed in seeds], timeout=600)
    print(f"corpus: {len(seeds)} files, {'crashed: ' + trailer.strip()[-300:] if crashed else 'no traps'}", flush=True)
    if crashed:
        failures.append(f"corpus file {seeds[done]}: {trailer.strip()[-300:]}")
    with tempfile.TemporaryDirectory() as work:
        made = 0
        while made < options.cases:
            count = min(options.batch, options.cases - made)
            jobs = []
            for index in range(count):
                path = Path(work) / f"{made + index}.ico"
                path.write_bytes(mutate(rng.choice(donors), rng, donors))
                jobs.append(("qs", str(path)))
            start = 0
            while start < len(jobs):
                done, crashed, trailer = run(jobs[start:], timeout=120)
                if not crashed:
                    break
                bad = start + done
                FAILURES.mkdir(parents=True, exist_ok=True)
                saved = FAILURES / f"case-{made + bad}.ico"
                shutil.copy(jobs[bad][1], saved)
                failures.append(f"{saved}: {trailer.strip()[-300:]}")
                print(f"FAIL {failures[-1]}", flush=True)
                start = bad + 1
            made += count
            for path in Path(work).iterdir():
                path.unlink()
            print(f"{made} cases, {len(failures)} failing", flush=True)
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    peak_mb = peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024
    print(f"{options.cases} cases: {len(failures)} traps or hangs; peak memory of a decoding process {peak_mb:.1f} MB")
    for line in stressed:
        print(f"FAIL {line}")
    return 1 if failures or stressed else 0


if __name__ == "__main__":
    sys.exit(main())
