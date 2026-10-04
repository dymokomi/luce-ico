#!/usr/bin/env python3
"""Build and run luce-ico's tests, in native and C modes, with every compiler named:
LUCE_BASE (by default the luce-base checkout beside this package), then each of
LUCE_BASE_EXTRA (colon-separated; for checking a second toolchain, such as luce-base
main beside the pinned one). Each compiler also checks the sources with -W and their
formatting, and builds the tools."""
import os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIRST = Path(os.environ.get("LUCE_BASE") or ROOT.parent / "luce-base/build/luce-base").resolve()
EXTRA = [Path(p).resolve() for p in os.environ.get("LUCE_BASE_EXTRA", "").split(":") if p]
MODULES = ["ico"]
SOURCES = sorted(str(p.relative_to(ROOT)) for p in ROOT.glob("*/**/*.lucb") if p.parts[len(ROOT.parts)] in ("src", "tests", "tools"))


def run(*command):
    result = subprocess.run([str(c) for c in command], cwd=ROOT, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        sys.exit(f"FAIL: {' '.join(map(str, command))}\n{result.stdout}{result.stderr}")
    return result.stdout


for base in [FIRST, *EXTRA]:
    for module in MODULES:
        warnings = run(base, "check", f"src/{module}", "-W")
        if warnings.strip():
            sys.exit(f"FAIL: {base} check -W src/{module}:\n{warnings}")
    for source in SOURCES:
        run(base, "fmt", source, "--check")
    for flags in (["--native"], ["--backend=c"]):
        for module in MODULES:
            out = run(base, "test", f"src/{module}", *flags)
            print(f"{base}: {module} {' '.join(flags)}: {out.strip().splitlines()[-1]}")
    for tool in ("icocheck",):
        if (ROOT / f"tools/{tool}.lucb").exists():
            (ROOT / "build").mkdir(exist_ok=True)
            run(base, "build", f"tools/{tool}.lucb", "-o", f"build/{tool}-check")
print("PASS luce-ico: decoder and tools")
