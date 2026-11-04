#!/usr/bin/env python3
"""Does the perceptual distance (perceptual.py) hear what the owner heard?

Each problem the owner reported and that a later commit fixed is a labelled example: the build
before the fix should be further from the recordings than the build after, on the keys, velocities
and part of the note where the problem was. This checks every such pair from the history.

Build each commit (tools/measure/build_history.sh <dir> <sha …>: a worktree per commit), run
perceptual.py on each with --json <dir>/<sha>.json, then:

    python3 -I tools/measure/validate_perceptual.py <dir>
"""
import json
import sys
from pathlib import Path

import numpy as np

ALL = list(range(21, 109, 3))
# (before, after, what was heard, keys, layers (None = all), windows)
PAIRS = [
    ("accf3e9", "25f264f", "every key played A0's measurements", ALL, None, ["score"]),
    ("25f264f", "aac5fe3", "hi-hat-like hiss on every attack (4-8 kHz)", ALL, None, ["attack"]),
    ("aac5fe3", "a6211a2", "hard, buzzy attack; A4-D5 noisier than the recordings", ALL, None, ["attack", "early"]),
    ("a6211a2", "16898c6", "bell-like high notes (no body ring)", [k for k in ALL if k >= 84], None, ["sustain"]),
    ("16898c6", "938493e", "treble fundamental sank at the knee (a hole)", [k for k in ALL if k >= 81], None, ["early", "sustain"]),
    ("8b762c0", "aa388f8", "hiss at 8-16 kHz on the top keys", [k for k in ALL if k >= 93], None, ["attack", "early"]),
    ("aa388f8", "f2dabc8", "white noise on D#5-F#5 and D#6 at velocity 100", [75, 78, 87], [13], ["attack", "early"]),
    ("f2dabc8", "bc04e1f", "noisy strikes on A1-A2", [33, 36, 39, 42, 45], None, ["attack"]),
]


def pick(rows, keys, layers, windows):
    v = [np.mean([r[w] for w in windows]) for r in rows
         if r["key"] in keys and (layers is None or r["layer"] in layers)]
    return float(np.mean(v)) if v else float("nan")


def main():
    d = Path(sys.argv[1])
    ok = 0
    print(f"{'before':8s} {'after':8s}  {'before':>7s} {'after':>7s}  {'change':>7s}  heard")
    for b, a, what, keys, layers, windows in PAIRS:
        rb, ra = (json.load(open(d / f"{c}.json")) for c in (b, a))
        vb, va = pick(rb, keys, layers, windows), pick(ra, keys, layers, windows)
        good = va < vb
        ok += good
        print(f"{b:8s} {a:8s}  {vb:7.1f} {va:7.1f}  {100 * (va - vb) / vb:+6.0f}%  {'ok ' if good else 'NO '} {what} ({'+'.join(windows)})")
    print(f"{ok} of {len(PAIRS)} fixes rank better")


if __name__ == "__main__":
    main()
