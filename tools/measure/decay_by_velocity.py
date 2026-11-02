#!/usr/bin/env python3
"""The stored decay curves against the recordings, by velocity layer (a data check: nothing
is rendered). Partials 1-3, power averaged over 0.3 s windows at 0.3 / 0.75 / 1.5 / 2.5 s,
median over A3-D#7, stored − recording (dB).

Found that softly struck notes sustain relatively longer than the single (loud-layer)
decay shape allowed, which led to the soft / medium / loud decay groups.

    python3 -I tools/measure/decay_by_velocity.py [--sal …]
"""
import warnings

import numpy as np

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M  # noqa: E402

KEYS = (57, 69, 75, 81, 87, 93, 99)
LAYERS = (3, 6, 10, 14, 16)
CENTRES = (0.3, 0.75, 1.5, 2.5)


def main():
    warnings.simplefilter("ignore")
    a = M.args(__doc__)
    mdl = M.model()
    tot = {v: [] for v in LAYERS}
    for key in KEYS:
        k = mdl[key]
        for v in LAYERS:
            x, sr = M.A.load(a.sal / "Samples" / f"{k['name']}v{v}.flac")
            errs = []
            for p in k["partials"][:3]:
                sh = M.A.shape_at(p, v - 1)
                for ch in (0, 1):
                    t, z = M.A.envelope(x[:, ch], sr, p["f"], max(k["window"], 0.02), tmax=3.0)
                    pw = np.abs(z) ** 2
                    for tc in CENTRES:
                        i0, i1 = int((tc - 0.15) / 0.005), int((tc + 0.15) / 0.005)
                        if i1 >= len(pw):
                            errs.append(np.nan)
                            continue
                        rec = 10 * np.log10(pw[i0:i1].mean() + 1e-20)
                        mdl_db = 10 * np.log10(np.mean(10 ** ((p["levels"][v - 1][ch] + M.A.shape_db(sh, t[i0:i1])) / 10)))
                        errs.append(mdl_db - rec if rec > -95 else np.nan)
            tot[v].append(np.nanmedian(np.array(errs).reshape(-1, len(CENTRES)), 0))
    print("stored − recording (dB) at " + " / ".join(f"{c:g}" for c in CENTRES) + " s")
    for v, r in tot.items():
        print(f"layer {v:2d}: " + " ".join("%+5.1f" % x for x in np.nanmedian(np.array(r), 0)))


if __name__ == "__main__":
    main()
