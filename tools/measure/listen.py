#!/usr/bin/env python3
"""What Listening Point does to a note, key by key (no recordings: it isn't part of the measured piano).

Listening Point takes the sound from a point along the string, as a pickup would: partial n by
sin(nπy), and the note's loudness kept. This renders each key at Depth 100 % with the point at
50, 25, 12 and 4 % of the string, against the same note at Depth 0, and reports:

- the low partials' change against each other, 0.05-0.55 s, beside what sin(nπy) says (the
  common level is the loudness correction, so only differences between partials are compared);
- the note's level over its first half second against Depth 0 (kept: within a dB or two);
- the perceptual distance from Depth 0 (the owner could hardly hear a control at 1-7 %; 15 %
  and more is plain);
- moving: the *Wandering Ear* preset (an LFO on the point) against the plain note, window by
  window (0.25 s, levels matched): the mean distance, and how far the low partials swing.

    python3 -I tools/measure/listen.py [--render build/piano_render] [--keys 36,48,60,72,84]
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M      # noqa: E402
import perceptual as P  # noqa: E402

SR = 48000
POINTS = (50, 25, 12, 4)


def partials(y, f0, count=6, t0=0.05, t1=0.55):
    s = y[int(t0 * SR):int(t1 * SR)]
    w = np.hanning(len(s))[:, None]
    S = (np.abs(np.fft.rfft(s * w, axis=0)) ** 2).sum(1)
    f = np.fft.rfftfreq(len(s), 1 / SR)
    return np.array([10 * np.log10(S[(f > n * f0 * 0.97) & (f < n * f0 * 1.03)].sum() + 1e-30) for n in range(1, count + 1)])


def level(y, t0=0.0, t1=0.5):
    return 10 * np.log10(np.mean(y[int(t0 * SR):int(t1 * SR)] ** 2) + 1e-30)


def dist(a, b):
    return P.distance(P.loudness(a, SR), P.loudness(b, SR))["score"]


def patched(render, key, patch, seconds=4.0):
    """A held note through a patch given whole (for routes; Reverb off)."""
    patch = json.loads(json.dumps(patch))
    patch["params"]["space.room"] = 0
    sc = {"patch": patch, "length": seconds, "events": [{"t": 0, "on": key, "vel": 100}]}
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "s.json").write_text(json.dumps(sc))
        subprocess.run([render, str(Path(d) / "s.json"), "--out", str(Path(d) / "s.wav")], check=True, capture_output=True)
        return sf.read(str(Path(d) / "s.wav"), always_2d=True)[0]


def moving(y, ref, f0, t0=0.25, t1=3.25, win=0.25):
    """Against the plain note, window by window (levels matched): the mean distance, and how far
    each of the first six partials swings (the spread of its level against the plain note's, dB)."""
    ds, rows = [], []
    for i in range(int((t1 - t0) / win)):
        a0, a1 = t0 + i * win, t0 + (i + 1) * win
        a, b = ref[int(a0 * SR):int(a1 * SR)], y[int(a0 * SR):int(a1 * SR)]
        b = b * np.sqrt(np.mean(a ** 2) / (np.mean(b ** 2) + 1e-30))
        ds.append(dist(a, b))
        pa = partials(ref, f0, 6, a0, a1)
        rows.append(np.where(pa > pa.max() - 40, np.clip(partials(y, f0, 6, a0, a1) - pa, -30, 30), np.nan))
    rows = np.array(rows)
    with np.errstate(all="ignore"):
        swing = np.nanmax(rows, 0) - np.nanmin(rows, 0)
    return float(np.mean(ds)), float(np.nanmedian(swing))


def main():
    def extra(p):
        p.add_argument("--keys", default="36,48,60,72,84")
        p.add_argument("--vel", type=int, default=100)
    a = M.args(__doc__, extra)
    errs, levels, dists, moves = [], [], {p: [] for p in POINTS}, []
    print("per key and point: partials 2-6 against the 1st, change from Depth 0 (dB) | what sin(nπy) says; level; distance")
    for key in [int(k) for k in a.keys.split(",")]:
        f0 = 440 * 2 ** ((key - 69) / 12)
        ref = M.note(a.render, key, a.vel, 3.2)
        pr = partials(ref, f0)
        ok = pr > pr.max() - 45
        for pt in POINTS:
            y = M.note(a.render, key, a.vel, 3.2, {"listen.depth": 100, "listen.point": pt})
            d = partials(y, f0) - pr
            want = np.array([20 * np.log10(abs(np.sin(np.pi * n * pt / 100)) + 1e-9) for n in range(1, 7)])
            rel, wrel = d - d[0], want - want[0]
            # a partial at a node is silent in theory and at the render's floor in practice: left out
            use = ok & (want > -30) & ok[0]
            errs += list(np.abs(rel - wrel)[use][1:])
            lv = level(y) - level(ref)
            levels.append(lv)
            dd = dist(ref, y)
            dists[pt].append(dd)
            print(f"{M.A.note_name(key):4s} {pt:2d} %: " + " ".join(f"{v:+4.0f}" for v in rel[1:]) + "  |  " +
                  " ".join(f"{v:+4.0f}" if v > -30 else " node" for v in wrel[1:]) + f"   level {lv:+.1f} dB   {dd:3.0f} %")
        if key <= 72:   # higher, the note is gone before the LFO has turned
            wander = json.loads((Path(__file__).resolve().parents[2] / "presets" / "wandering-ear.json").read_text())
            md, sw = moving(patched(a.render, key, wander), ref, f0)
            moves.append((md, sw))
            print(f"{M.A.note_name(key):4s} Wandering Ear (the point between ~3 and ~31 % at 0.25 Hz): {md:.0f} % from the plain note, "
                  f"window by window; partials 1-6 swing {sw:.0f} dB (median)")
    print(f"partials against the 1st, away from nodes: within {max(errs):.1f} dB of sin(nπy) (mean {np.mean(errs):.1f})")
    print(f"level over the first half second: {min(levels):+.1f}…{max(levels):+.1f} dB from Depth 0")
    for pt in POINTS:
        print(f"point {pt:2d} %: {min(dists[pt]):.0f}-{max(dists[pt]):.0f} % from Depth 0")
    print(f"moving (Wandering Ear): {min(m[0] for m in moves):.0f}-{max(m[0] for m in moves):.0f} % from the plain note window by window; "
          f"partials swing {min(m[1] for m in moves):.0f}-{max(m[1] for m in moves):.0f} dB")

if __name__ == "__main__":
    main()
