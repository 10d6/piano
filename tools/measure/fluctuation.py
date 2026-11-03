#!/usr/bin/env python3
"""How much the envelopes fluctuate (beating), and whether left and right fluctuate
independently: recording against model.

  fluctuation   std of the detrended dB envelope, 0.3-2.5 s, partials 1-4, per mic.
                Too low sounds steady and synthetic; BEAT_SCALE in make_header.py is
                calibrated on this (recordings ~3.6 dB).
  L/R           correlation of the left and right envelope fluctuations, partials 1-3.
                Near 1 is a static, centred image (recordings ~0.3).

    python3 -I tools/measure/fluctuation.py [--render …] [--sal …]
"""
import numpy as np

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M  # noqa: E402

CASES = [(48, 12), (60, 12), (69, 12), (75, 12), (81, 12), (84, 12), (87, 12), (93, 12), (99, 12), (69, 6), (81, 6)]


def detrended(x, sr, p, k, ch):
    t, z = M.A.envelope(x[:, ch], sr, p["f"], max(k["window"], 0.02), tmax=2.6)
    e = 20 * np.log10(np.abs(z) + 1e-12)
    sel = (t > 0.3) & (t < 2.5)
    if e[sel].mean() < -85:
        return None
    return e[sel] - np.polyval(np.polyfit(t[sel], e[sel], 2), t[sel])


def main():
    a = M.args(__doc__)
    mdl = M.model()
    fl = {"rec": [], "mod": []}
    cor = {"rec": [], "mod": []}
    for key, layer in CASES:
        k = mdl[key]
        xs = {"rec": M.recording(a.sal, key, layer), "mod": M.note(a.render, key, M.layer_vel(layer), 2.8)}
        line = []
        for lab, x in xs.items():
            v, c = [], []
            for i, p in enumerate(k["partials"][:4]):
                d = [detrended(x, 48000, p, k, ch) for ch in (0, 1)]
                if d[0] is None or d[1] is None:
                    continue
                v += [np.std(d[0]), np.std(d[1])]
                if i < 3:
                    n = min(len(d[0]), len(d[1]))
                    c.append(np.corrcoef(d[0][:n], d[1][:n])[0, 1])
            fl[lab].append(np.median(v) if v else np.nan)
            cor[lab] += c
            line.append(f"{fl[lab][-1]:4.1f}")
        print(f"{M.C.name(key):4s} v{layer:<2d} fluctuation dB recording {line[0]} | model {line[1]}")
    print(f"median fluctuation: recording {np.nanmedian(fl['rec']):.1f} dB, model {np.nanmedian(fl['mod']):.1f} dB")
    print(f"median L/R correlation: recording {np.median(cor['rec']):.2f}, model {np.median(cor['mod']):.2f}")


if __name__ == "__main__":
    main()
