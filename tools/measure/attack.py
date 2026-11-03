#!/usr/bin/env python3
"""The attack by octave band (62 Hz … 8 kHz) at 5, 30, 80, 200 and 500 ms: recording | model,
and the model's excess per band. The hissy, hi-hat-like attack showed here as 15-45 dB too
much at 4-8 kHz (noise bands leaking through gentle filters).

    python3 -I tools/measure/attack.py [--render …] [--sal …] [--verbose]
"""
import numpy as np

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M  # noqa: E402

BANDS = (62.5, 250, 1000, 4000, 8000)
TIMES = (0.005, 0.03, 0.08, 0.2, 0.5)
CASES = [(36, 12), (48, 12), (60, 12), (60, 4), (69, 16), (81, 12), (81, 4), (93, 12), (102, 12), (105, 14)]


def bands(x):
    m = x.mean(1)
    N = 1024
    f = np.fft.rfftfreq(N, 1 / 48000)
    rows = []
    for t in TIMES:
        i = int(t * 48000)
        sp = np.abs(np.fft.rfft(m[i:i + N] * np.hanning(N))) ** 2 / (0.1875 * N * N)
        rows.append([10 * np.log10(sp[(f >= lo) & (f < 2 * lo)].sum() + 1e-16) for lo in BANDS])
    return np.array(rows)


def extra(p):
    p.add_argument("--verbose", action="store_true")


def main():
    a = M.args(__doc__, extra)
    tot = []
    for k, layer in CASES:
        r = bands(M.recording(a.sal, k, layer))
        m = bands(M.note(a.render, k, M.layer_vel(layer), 1.0))
        tot.append(m - r)
        if a.verbose:
            print(f"{M.C.name(k)} v{layer}  bands " + "/".join(f"{b:g}" for b in BANDS) + " Hz; rows " +
                  ", ".join(f"{t * 1000:g}" for t in TIMES) + " ms: recording | model")
            for r1, r2 in zip(r, m):
                print("   " + " ".join("%5.0f" % v for v in r1) + "  |  " + " ".join("%5.0f" % v for v in r2))
    d = np.concatenate(tot)
    print("model − recording by band (" + "/".join(f"{b:g}" for b in BANDS) + " Hz): median " +
          " ".join("%+5.1f" % v for v in np.median(d, 0)) + "   worst " + " ".join("%+5.1f" % v for v in d.max(0)))


if __name__ == "__main__":
    main()
