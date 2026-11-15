#!/usr/bin/env python3
"""What Touch Point does to a note, key by key (no recordings: it isn't part of the measured piano).

A finger on the string takes energy from each partial where that partial moves under it. This
renders each key with the finger at 1/2 and at 1/3 of the string (a light, narrow touch) and as a
mute (wide, heavy, near the end), against the same note untouched, and reports:

- the low partials' levels 0.4-0.9 s in, against the untouched note: at 1/2 the odd partials
  should be gone and the even ones kept; at 1/3 only every third kept;
- the perceptual distance from the untouched note (the owner could hardly hear a control at
  1-7 %; 15 % and more is plain);
- for the mute, how long the note takes to fall 40 dB from its peak.

    python3 -I tools/measure/touch.py [--render build/piano_render] [--keys 36,48,60,72,84]
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M      # noqa: E402
import perceptual as P  # noqa: E402

SR = 48000
HALF = {"touch.press": 600, "touch.point": 50, "touch.width": 1}
THIRD = {"touch.press": 600, "touch.point": 33.3, "touch.width": 1}
MUTE = {"touch.press": 1500, "touch.point": 8, "touch.width": 20, "touch.snap": 0}


def partials(y, f0, count=6, t0=0.4, t1=0.9):
    """Power (dB) within ±3 % of each of the first partials, both mics, over t0…t1."""
    s = y[int(t0 * SR):int(t1 * SR)]
    w = np.hanning(len(s))[:, None]
    S = (np.abs(np.fft.rfft(s * w, axis=0)) ** 2).sum(1)
    f = np.fft.rfftfreq(len(s), 1 / SR)
    return np.array([10 * np.log10(S[(f > n * f0 * 0.97) & (f < n * f0 * 1.03)].sum() + 1e-30) for n in range(1, count + 1)])


def fall_time(y, db=40):
    """Seconds from the peak until the level (10 ms frames) stays `db` under it."""
    hop = SR // 100
    e = np.array([np.mean(y[i:i + hop] ** 2) for i in range(0, len(y) - hop, hop)])
    lv = 10 * np.log10(e + 1e-30)
    pk = int(np.argmax(lv))
    above = np.nonzero(lv[pk:] > lv[pk] - db)[0]
    return (above[-1] + 1) * hop / SR if len(above) else 0.0


def dist(a, b):
    return P.distance(P.loudness(a, SR), P.loudness(b, SR))["score"]


def main():
    def extra(p):
        p.add_argument("--keys", default="36,48,60,72,84")
        p.add_argument("--vel", type=int, default=100)
    a = M.args(__doc__, extra)
    kept = {"half": [], "third": []}
    lost = {"half": [], "third": []}
    dists = {"half": [], "third": [], "mute": []}
    falls = []
    print("partials 1-6 against the untouched note, 0.4-0.9 s (dB); distance from untouched; mute: seconds to -40 dB")
    for key in [int(k) for k in a.keys.split(",")]:
        f0 = 440 * 2 ** ((key - 69) / 12)
        ref = M.note(a.render, key, a.vel, 3.2)
        pr = partials(ref, f0)
        row = f"{M.A.note_name(key):4s}"
        for name, prm, keep in (("half", HALF, 2), ("third", THIRD, 3)):
            y = M.note(a.render, key, a.vel, 3.2, prm)
            d = partials(y, f0) - pr
            # partials near the untouched note's floor say nothing either way
            ok = pr > pr.max() - 50
            kept[name] += [d[n - 1] for n in range(1, 7) if n % keep == 0 and ok[n - 1]]
            lost[name] += [d[n - 1] for n in range(1, 7) if n % keep != 0 and ok[n - 1]]
            dd = dist(ref, y)
            dists[name].append(dd)
            row += f"  1/{keep}: " + " ".join(f"{v:+4.0f}" for v in d) + f"  {dd:3.0f} %"
        y = M.note(a.render, key, a.vel, 3.2, MUTE)
        dd = dist(ref, y)
        dists["mute"].append(dd)
        falls.append((fall_time(y), fall_time(ref)))
        row += f"  mute: {falls[-1][0]:.2f} s (untouched {falls[-1][1]:.2f} s)  {dd:3.0f} %"
        print(row)
    for name, keep in (("half", "1/2"), ("third", "1/3")):
        print(f"touch at {keep}: partials with a node there change {min(kept[name]):+.1f}…{max(kept[name]):+.1f} dB, "
              f"the others {max(lost[name]):+.0f} dB or more; {min(dists[name]):.0f}-{max(dists[name]):.0f} % from untouched")
    print(f"mute: -40 dB in {min(f[0] for f in falls):.2f}-{max(f[0] for f in falls):.2f} s "
          f"(untouched {min(f[1] for f in falls):.1f}-{max(f[1] for f in falls):.1f} s); "
          f"{min(dists['mute']):.0f}-{max(dists['mute']):.0f} % from untouched")


if __name__ == "__main__":
    main()
