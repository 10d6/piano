#!/usr/bin/env python3
"""Upper partials over time: model − recording (dB) for partial groups 1-2, 3-6, 7-12 and
13-24, averaged over 0.2 s windows at 0.05, 0.4 and 1.4 s, both mics.

Found the −90 dBFS cull floor removing soft notes' upper partials, and the buzzy upper
partials around C5 whose decay fits had been pulled flat by their tails.

    python3 -I tools/measure/groups.py [--render …] [--sal …]
"""
import numpy as np

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M  # noqa: E402

CASES = [(57, 4), (57, 14), (66, 10), (69, 2), (69, 10), (69, 16), (72, 4), (72, 16), (75, 10), (81, 10)]
GROUPS = ((1, 2), (3, 6), (7, 12), (13, 24))
STARTS = (0.05, 0.4, 1.4)


def main():
    a = M.args(__doc__)
    mdl = M.model()
    acc = {g: [] for g in GROUPS}
    for key, layer in CASES:
        k = mdl[key]
        rec = M.recording(a.sal, key, layer)
        mod = M.note(a.render, key, M.layer_vel(layer), 2.5)
        rows = []
        for p in k["partials"][:24]:
            out = []
            for x in (rec, mod):
                t, pw = M.env_power(x, 48000, p["f"], k["window"], 2.2)
                out.append([M.at(t, pw, s + 0.1) for s in STARTS])
            rows.append((p["n"], out))
        txt = []
        for g in GROUPS:
            d = [[o[1][i] - o[0][i] for n, o in rows if g[0] <= n <= g[1] and o[0][i] > -90] for i in range(3)]
            v = [np.median(x) if x else np.nan for x in d]
            acc[g].append(v)
            txt.append(f"n{g[0]}-{g[1]}: " + "/".join("%+5.1f" % x for x in v))
        print(f"{M.C.name(key):4s} v{layer:<2d} " + "  ".join(txt))
    print("median   " + "  ".join(f"n{g[0]}-{g[1]}: " + "/".join(
        "%+5.1f" % np.nanmedian([r[i] for r in v]) for i in range(3)) for g, v in acc.items()))


if __name__ == "__main__":
    main()
