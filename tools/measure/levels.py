#!/usr/bin/env python3
"""Overall level over time, model against recording (dB, 100 ms RMS, mics summed).

Single notes, layer by layer. Watch the median line: a sustain that falls off too fast
or too slow shows as a drift across the columns. Single snapshots are moved by beating;
look at the median, and at tools/measure/partials.py for one key in detail.

    python3 -I tools/measure/levels.py [--render …] [--sal …]
"""
import numpy as np

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M  # noqa: E402

CASES = [(36, 12), (48, 12), (60, 12), (60, 4), (66, 12), (69, 12), (72, 12), (81, 12), (93, 12)]
TIMES = (0.1, 0.5, 1, 2, 4)


def main():
    a = M.args(__doc__)
    print("level (dB) at " + " / ".join(f"{t:g}" for t in TIMES) + " s: recording | model | difference")
    diffs = []
    for k, layer in CASES:
        rec = M.recording(a.sal, k, layer)
        mod = M.note(a.render, k, M.layer_vel(layer), 5.0)
        sr = 48000
        lv = lambda x, t: 20 * np.log10(np.sqrt(np.mean(x[int(t * sr):int(t * sr) + 4800].mean(1) ** 2)) + 1e-12)
        ts = [t for t in TIMES if t * sr < len(rec) - 4800]
        r = np.array([lv(rec, t) for t in ts])
        m = np.array([lv(mod, t) for t in ts])
        diffs.append(m - r)
        print(f"{M.C.name(k):4s} v{layer:<2d} " + " ".join("%6.1f" % v for v in r) + "  | " +
              " ".join("%6.1f" % v for v in m) + "  | " + " ".join("%+5.1f" % v for v in m - r))
    print("median difference by time: " + " ".join(
        "%+5.1f" % np.median([d[i] for d in diffs if len(d) > i]) for i in range(len(TIMES))))


if __name__ == "__main__":
    main()
