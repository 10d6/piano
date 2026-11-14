#!/usr/bin/env python3
"""What the soft pedal does to a note, key by key (no recordings: there are none with it down).

Renders each key with the pedal at half and full against none, and reports the level over three
windows (the strike, the early sustain, the late sustain), the first 0.3 s's spectral centroid,
and the perceptual distance from the same note without the pedal: is it large enough to hear
(the owner couldn't hear a control at 1-7 %), and does it do what a soft pedal does (quieter and
rounder at the strike, sustain kept)?

    python3 -I tools/measure/softpedal.py [--render build/piano_render] [--keys 36,48,60,72,84,96]
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M      # noqa: E402
import perceptual as P  # noqa: E402


def level(y, a, b):
    s = y[int(a * 48000):int(b * 48000)]
    return 10 * np.log10(np.mean(s ** 2) + 1e-30)


def centroid(y):
    s = y[:int(0.3 * 48000)].mean(1)
    S = np.abs(np.fft.rfft(s * np.hanning(len(s)))) ** 2
    f = np.fft.rfftfreq(len(s), 1 / 48000)
    return (S * f).sum() / S.sum()


def main():
    def extra(p):
        p.add_argument("--keys", default="36,43,48,55,60,67,72,79,84,96")
        p.add_argument("--vel", type=int, default=90)
    a = M.args(__doc__, extra)
    print("key    pedal: level at 0-0.1 s, 0.5-1 s, 2-3 s (dB) | centroid | distance from no pedal")
    dist = {50: [], 100: []}
    for key in [int(k) for k in a.keys.split(",")]:
        ref = M.note(a.render, key, a.vel, 3.2)
        Nr = P.loudness(ref, 48000)
        cells = []
        for s in (50, 100):
            y = M.note(a.render, key, a.vel, 3.2, {"play.softpedal": s})
            d = P.distance(Nr, P.loudness(y, 48000))["score"]
            dist[s].append(d)
            cells.append(f"{s:3d} %: " + " ".join(f"{level(y, t0, t1) - level(ref, t0, t1):+5.1f}" for t0, t1 in ((0, 0.1), (0.5, 1), (2, 3))) +
                         f" | {100 * (centroid(y) / centroid(ref) - 1):+4.0f} % | {d:4.1f} %")
        print(f"{M.A.note_name(key):5s}  " + "   ".join(cells))
    print(f"soft pedal: distance from no pedal {min(dist[50]):.0f}-{max(dist[50]):.0f} % at half, {min(dist[100]):.0f}-{max(dist[100]):.0f} % at full")


if __name__ == "__main__":
    main()
