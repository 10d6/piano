#!/usr/bin/env python3
"""Note handling: fast repeats, event orderings, release times, voice lifetime, and
strike-to-strike consistency. No recordings needed.

  repeats      10 strikes at 8 and 14 per second: each strike's level, and the smallest rise
               at a strike (≤ 0 dB: a strike that didn't sound; this caught the restrike
               cancellation and the full voices)
  orders       normal repeat, re-trigger (on, on, off) and overlapping repeats, with and
               without pedal
  release      level after note-off by key (the dampers)
  lifetime     when the sound ends (the voice is done), against the last moment above
               −80 and −70 dBFS; give --compare another build to see both
  consistency  the first 40 ms of 12 fresh strikes of one key: spread per channel (random
               beat/stereo choices once made the right channel vary by 6 dB)

    python3 -I tools/measure/notes.py [--render …] [--compare other/piano_render] [--only repeats,…]
"""
import numpy as np

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M  # noqa: E402


def extra(p):
    p.add_argument("--compare", default=None, help="another render tool, for lifetime")
    p.add_argument("--only", default="repeats,orders,release,lifetime,consistency")


def lvl(m, sr, a, b):
    return 20 * np.log10(np.sqrt(np.mean(m[int(a * sr):int(b * sr)] ** 2)) + 1e-12)


def repeats(tool):
    print("fast repeats (velocity 90): strike levels (dB), and the smallest rise at a strike")
    for k in (21, 33, 45, 60, 72):
        for rate in (8, 14):
            ev, times, t = [], [], 0.0
            for _ in range(10):
                ev += [{"t": round(t, 4), "on": k, "vel": 90}, {"t": round(t + 0.6 / rate, 4), "off": k}]
                times.append(t)
                t += 1.0 / rate
            y, sr = M.render(tool, ev, t + 0.5, trim=False)
            m = y.mean(1)
            rises = [lvl(m, sr, tt + 0.01, tt + 0.04) - lvl(m, sr, tt - 0.02, tt - 0.002) for tt in times[1:]]
            print(f"  key {k:3d} at {rate:2d}/s: " + " ".join("%5.1f" % lvl(m, sr, tt + 0.005, tt + 0.04) for tt in times) +
                  "   smallest rise %.1f dB" % min(rises))


def orders(tool):
    k = 60
    cases = [
        ("on, off, on (normal repeat)", [{"t": 0, "on": k, "vel": 90}, {"t": 0.15, "off": k}, {"t": 0.2, "on": k, "vel": 90}], 1.5),
        ("on, on, off (re-trigger)", [{"t": 0, "on": k, "vel": 90}, {"t": 0.2, "on": k, "vel": 90}, {"t": 0.22, "off": k}], 1.5),
        ("on, on, off, off (overlap)", [{"t": 0, "on": k, "vel": 90}, {"t": 0.2, "on": k, "vel": 90}, {"t": 0.22, "off": k}, {"t": 1.0, "off": k}], 1.5),
        ("overlap, pedal down", [{"t": 0, "cc": 64, "value": 127}, {"t": 0, "on": k, "vel": 90}, {"t": 0.2, "on": k, "vel": 90},
                                 {"t": 0.22, "off": k}, {"t": 1.0, "off": k}, {"t": 1.0, "cc": 64, "value": 0}], 1.8),
    ]
    print("event orderings, key 60: level (dB) at times")
    for name, ev, length in cases:
        y, sr = M.render(tool, ev, length, trim=False)
        m = y.mean(1)
        print(f"  {name:30s} " + "  ".join(f"{t:.1f}s {lvl(m, sr, t, t + 0.05):6.1f}" for t in (0.1, 0.3, 0.6, 1.0, 1.3) if t + 0.05 < length))


def release(tool):
    print("release: level after note-off at 1 s, relative to the level just before")
    for k in (21, 33, 45, 57, 69, 81, 88, 93):
        y, sr = M.render(tool, [{"t": 0, "on": k, "vel": 90}, {"t": 1.0, "off": k}], 4.0, trim=False)
        m = y.mean(1)
        ref = lvl(m, sr, 0.95, 1.0)
        print(f"  key {k:3d}: " + " ".join(f"+{t - 1:.1f}s {lvl(m, sr, t, t + 0.05) - ref:6.1f}" for t in (1.1, 1.3, 1.6, 2.0, 3.0)))


def lifetime(tool, other):
    def life(tl, k, pedal):
        ev = [{"t": 0, "on": k, "vel": 90}, {"t": 0.3, "off": k}]
        if pedal:
            ev = [{"t": 0, "cc": 64, "value": 127}, {"t": 0.01, "on": k, "vel": 90}, {"t": 0.3, "off": k}]
        y, sr = M.render(tl, ev, 45, trim=False)
        a = np.abs(y).max(1)
        nz = np.nonzero(a > 0)[0]
        env = np.array([a[i:i + 480].max() for i in range(0, len(a), 480)])
        below = lambda db: ((np.nonzero(20 * np.log10(env + 1e-15) > db)[0][-1] + 1) * 0.01) if (env > 10 ** (db / 20)).any() else 0
        return (nz[-1] / sr if len(nz) else 0), below(-80), below(-70)
    print("lifetime (s): sound ends | last above −80 dBFS | last above −70 dBFS")
    for k, pedal in ((24, True), (36, True), (48, True), (60, True), (72, True), (60, False), (96, False)):
        b = life(tool, k, pedal)
        s = f"  key {k:3d} {'pedal held' if pedal else 'released  '}: {b[0]:5.1f} | {b[1]:5.1f} | {b[2]:5.1f}"
        if other:
            o = life(other, k, pedal)
            s += f"     other build: {o[0]:5.1f} | {o[1]:5.1f} | {o[2]:5.1f}"
        print(s)


def consistency(tool):
    print("12 fresh strikes, velocity 90: spread of the first 40 ms level (dB)")
    for k in (60, 72, 84):
        ev = []
        for i in range(12):
            ev += [{"t": i * 1.0, "on": k, "vel": 90}, {"t": i * 1.0 + 0.5, "off": k}]
        y, sr = M.render(tool, ev, 12.5, trim=False)
        out = []
        for lab, sig in (("mono", y.mean(1)), ("L", y[:, 0]), ("R", y[:, 1])):
            v = [lvl(sig, sr, i + 0.005, i + 0.04) for i in range(12)]
            out.append(f"{lab} {max(v) - min(v):.1f}")
        print(f"  key {k}: " + ", ".join(out))


def main():
    a = M.args(__doc__, extra)
    only = a.only.split(",")
    if "repeats" in only: repeats(a.render)
    if "orders" in only: orders(a.render)
    if "release" in only: release(a.render)
    if "lifetime" in only: lifetime(a.render, a.compare)
    if "consistency" in only: consistency(a.render)


if __name__ == "__main__":
    main()
