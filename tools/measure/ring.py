#!/usr/bin/env python3
"""The body's ring: energy between the partials (away from mains hum), per octave band
125 Hz-4 kHz, at 0.3-0.6, 1-1.5 and 2-3 s: model − recording (dB).

This is what found the missing echo-like tail of high notes (the model was 60-80 dB
short), and later the 8-16 kHz hiss the band-gain solver put in untargeted bands.

    python3 -I tools/measure/ring.py [--render …] [--sal …]
"""
import numpy as np

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M  # noqa: E402

BANDS = [125, 250, 500, 1000, 2000, 4000]
WINDOWS = ((0.3, 0.6), (1.0, 1.5), (2, 3))
CASES = [(60, 12), (69, 12), (75, 12), (81, 12), (81, 4), (87, 12), (93, 12), (99, 12), (105, 12)]


def between(x, f0, B, a, b, sr=48000):
    out = []
    for ch in (0, 1):
        seg = x[int(a * sr):int(b * sr), ch]
        N = 1 << 17
        sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), N)) ** 2 / (0.1875 * len(seg) ** 2)
        f = np.fft.rfftfreq(N, 1 / sr)
        near = np.zeros_like(f, bool)
        k = 1
        while k * f0 * np.sqrt(1 + B * k * k) < sr / 2:
            near |= np.abs(f - k * f0 * np.sqrt(1 + B * k * k)) < max(12, 0.04 * f0)
            k += 1
        near |= (np.abs(f - 50 * np.round(f / 50)) < 2.5) & (f < 1000)
        out.append([10 * np.log10(sp[(f >= lo) & (f < 2 * lo) & ~near].sum() + 1e-16) for lo in BANDS])
    return np.mean(out, 0)


def main():
    a = M.args(__doc__)
    mdl = M.model()
    allv = []
    print("ring between partials, model − recording (dB), bands " + " ".join(str(b) for b in BANDS) + " Hz")
    for key, layer in CASES:
        k = mdl[key]
        rec = M.recording(a.sal, key, layer)
        mod = M.note(a.render, key, M.layer_vel(layer), 3.5)
        rows = []
        for w0, w1 in WINDOWS:
            rows.append(None if w1 * 48000 > len(rec) else
                        between(mod, k["f0"], k["B"], w0, w1) - between(rec, k["f0"], k["B"], w0, w1))
        allv.append(rows)
        print(f"{M.C.name(key):4s} v{layer:<2d} " + " | ".join(
            " ".join("%+5.0f" % v for v in r) if r is not None else "  (file ends)" for r in rows))
    for i, (w0, w1) in enumerate(WINDOWS):
        v = [r[i] for r in allv if r[i] is not None]
        print(f"median {w0:g}-{w1:g} s: " + " ".join("%+5.1f" % x for x in np.median(v, 0)))


if __name__ == "__main__":
    main()
