#!/usr/bin/env python3
"""Sustain spectral balance: the whole spectrum in octave bands at 0.4-0.8 and 1.0-1.8 s,
each relative to the band holding the fundamental, model − recording (dB).

Positive numbers above the fundamental's band are the "higher-pitched resonance"; below it,
the body ring standing out. It showed the treble fundamental sinking (everything else
looking +10 dB) and led to the prompt/aftersound beating fix.

    python3 -I tools/measure/balance.py [--render …] [--sal …]
"""
import numpy as np

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M  # noqa: E402

BANDS = [125, 250, 500, 1000, 2000, 4000, 8000]
CASES = [(k, l) for k in (57, 63, 69, 72, 75, 78, 81, 84, 87, 93, 99) for l in (6, 12)]


def bands(x, a, b, sr=48000):
    out = []
    for ch in (0, 1):
        seg = x[int(a * sr):int(b * sr), ch]
        sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), 1 << 16)) ** 2
        f = np.fft.rfftfreq(1 << 16, 1 / sr)
        out.append([np.sum(sp[(f >= lo) & (f < 2 * lo)]) for lo in BANDS])
    return 10 * np.log10(np.mean(out, 0) + 1e-16)


def main():
    a = M.args(__doc__)
    print("model − recording (dB), bands " + " ".join(str(b) for b in BANDS) + " Hz, relative to the fundamental's band")
    errs = []
    for k, layer in CASES:
        rec = M.recording(a.sal, k, layer)
        mod = M.note(a.render, k, M.layer_vel(layer), 2.0)
        f0 = 440 * 2 ** ((k - 69) / 12)
        fb = max(i for i, b in enumerate(BANDS) if b <= f0 * 1.0001) if f0 >= 125 else 0
        row = []
        for w0, w1 in ((0.4, 0.8), (1.0, 1.8)):
            r, m = bands(rec, w0, w1), bands(mod, w0, w1)
            row.append((m - m[fb]) - (r - r[fb]))
        errs.append(np.mean(np.abs(np.concatenate(row))))
        print(f"{M.C.name(k):4s} v{layer:<2d} 0.4-0.8 s " + " ".join("%+4.0f" % v for v in row[0]) +
              "   1.0-1.8 s " + " ".join("%+4.0f" % v for v in row[1]))
    print(f"mean |difference| {np.mean(errs):.1f} dB")


if __name__ == "__main__":
    main()
