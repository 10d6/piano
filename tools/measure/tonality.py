#!/usr/bin/env python3
"""Is what lies between the partials tone or noise, as in the recordings?

The perceptual distance and between.py compare levels, and a band at the right level can still
sound wrong: noise where the recording has tones. That was the body's ring, played as band noise,
heard as a noisy sound lingering for a quarter second on C5 (docs/TESTING.md). This measures the
spectral flatness of what lies between the partials (every partial, mode and cluster component
left out, ±max(25 Hz, 3 % of f0), and the mains hum): the geometric over the arithmetic mean of
the power spectrum, per third of an octave from 0.5 to 8 kHz (so the spectrum's tilt doesn't
count), the median of the thirds. White noise is about 0.56; tones among quiet bins give much less.

Per measured key from C3 up, at layers 6, 10 and 14, over 0.1-0.4 s and 0.4-1.0 s, recording
against model. Healthy: the model's flatness near the recording's.

    python3 -I tools/measure/tonality.py [--render …] [--sal …]
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M  # noqa: E402

KEYS = list(range(48, 109, 3))
LAYERS = (6, 10, 14)
WINDOWS = [(0.1, 0.4), (0.4, 1.0)]


def comps(k):
    fs = []
    for p in k["partials"]:
        fs.append(p["f"])
        fs += [p["f"] + m["df"] for m in p.get("modes") or []]
        fs += [p["f"] * 2 ** (c / 1200) for c in (p.get("cluster") or {}).get("cents", [])]
    return fs


def flatness(x, k, t0, t1, lo=500, hi=8000):
    seg = x[int(t0 * 48000):int(t1 * 48000)].mean(1)
    n = len(seg)
    P = np.abs(np.fft.rfft(seg * np.hanning(n))) ** 2
    f = np.fft.rfftfreq(n, 1 / 48000)
    m = (f > lo) & (f < hi)
    w = max(25.0, 0.03 * k["f0"])
    for fc in comps(k):
        m &= np.abs(f - fc) > w
    m &= ~((np.abs(f - 50 * np.round(f / 50)) < 2.5) & (f < 1000))
    # per third of an octave, so the spectrum's tilt doesn't count as tonality; the median of the
    # thirds that hold enough of the spectrum
    out = []
    edges = lo * 2 ** (np.arange(0, 3 * np.log2(hi / lo) + 1) / 3)
    for a, b in zip(edges[:-1], edges[1:]):
        p = P[m & (f >= a) & (f < b)]
        if len(p) >= 12:
            out.append(np.exp(np.mean(np.log(p + 1e-30))) / np.mean(p))
    return float(np.median(out)) if out else np.nan


def main():
    a = M.args(__doc__)
    model = M.model()
    rows = []
    print(f"{'key':5s} " + "  ".join(f"{t0:.1f}-{t1:.1f} s rec / model" for t0, t1 in WINDOWS))
    for key in KEYS:
        k = model[key]
        r = np.zeros((len(LAYERS), len(WINDOWS)))
        m = np.zeros_like(r)
        for i, layer in enumerate(LAYERS):
            rec = M.recording(a.sal, key, layer)
            mod = M.note(a.render, key, M.layer_vel(layer), 1.1)
            for j, (t0, t1) in enumerate(WINDOWS):
                r[i, j], m[i, j] = flatness(rec, k, t0, t1), flatness(mod, k, t0, t1)
        rows.append((np.nanmedian(r, 0), np.nanmedian(m, 0)))
        print(f"{k['name']:5s} " + "  ".join(f"     {rr:.3f} / {mm:.3f}    " for rr, mm in zip(*rows[-1])), flush=True)
    R = np.array([x[0] for x in rows])
    Mo = np.array([x[1] for x in rows])
    print("median flatness, recording / model: " +
          "  ".join(f"{t0:.1f}-{t1:.1f} s {np.nanmedian(R[:, j]):.3f} / {np.nanmedian(Mo[:, j]):.3f}"
                    for j, (t0, t1) in enumerate(WINDOWS)))


if __name__ == "__main__":
    main()
