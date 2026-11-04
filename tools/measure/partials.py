#!/usr/bin/env python3
"""Partial-by-partial envelopes for one key and layer: recording, model, and the stored
curve (what the data says the model should do), mics averaged over 0.2 s windows.

The quickest way to tell a data problem (stored curve ≠ recording) from an engine problem
(model ≠ stored curve). It found the A0-data-for-every-key bug, the prompt/aftersound
beating hole at the knee, and the cull floor eating upper partials.

    python3 -I tools/measure/partials.py --key 84 --layer 12 [--count 8] [--render …] [--sal …]
"""
import numpy as np

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M  # noqa: E402

TIMES = (0.05, 0.2, 0.5, 0.8, 1.2, 1.6, 2.5)


def extra(p):
    p.add_argument("--key", type=int, default=84)
    p.add_argument("--layer", type=int, default=12)
    p.add_argument("--count", type=int, default=6)


def main():
    a = M.args(__doc__, extra)
    k = M.model()[a.key]
    rec = M.recording(a.sal, a.key, a.layer)
    mod = M.note(a.render, a.key, M.layer_vel(a.layer), 3.0)
    print(f"{k['name']} layer {a.layer}: dB at " + " / ".join(f"{t:g}" for t in TIMES) + " s")
    ts = np.array(TIMES)
    for p in k["partials"][:a.count]:
        rows = []
        for x in (rec, mod):
            t, pw = M.env_power(x, 48000, p["f"], max(k["window"], 0.02), 3.0)
            rows.append([M.at(t, pw, c) if c + 0.1 < t[-1] else np.nan for c in TIMES])
        sh = M.A.shape_at(p, a.layer - 1)
        stored = 10 * np.log10(np.mean([M.A.partial_env(p, a.layer - 1, c, ts) ** 2 for c in (0, 1)], 0))
        print(f"n{p['n']:<2d} recording " + " ".join("%6.1f" % v for v in rows[0]))
        print(f"    model     " + " ".join("%6.1f" % v for v in rows[1]))
        print(f"    stored    " + " ".join("%6.1f" % v for v in stored) +
              f"   s1 {sh['s1']:.0f} s2 {sh['s2']:.1f} drop {sh['drop']:.1f} x0 {sh['x0']:.0f}  beat {p['beat']:.2f} Hz {p['beat_db']:.1f} dB")


if __name__ == "__main__":
    main()
