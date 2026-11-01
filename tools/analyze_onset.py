#!/usr/bin/env python3
"""Measure how each note swells in: the hammer's contact, per key and layer.

For each recorded key and velocity layer, the first 40 ms of the recording
(left mic) are fitted with the measured partials (tools/salamander.json:
levels, phases, frequencies, decays) multiplied by a swell 1 − e^{−t/τ}
starting d seconds in. The best τ and d (least squares, with a free overall
gain) are written out.

    python3 -I tools/analyze_onset.py <Salamander dir> tools/salamander.json tools/salamander_onset.json
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import analyze as A  # noqa: E402

TAUS = np.geomspace(0.15e-3, 25e-3, 36)
DELAYS = np.arange(-0.5e-3, 6.01e-3, 0.25e-3)
LEN = 0.04


def main():
    root, model_path, out_path = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    model = json.load(open(model_path))
    sr = 48000
    t = np.arange(int(LEN * sr)) / sr
    out = {"keys": []}
    for key in model["keys"]:
        taus, delays, fits = [], [], []
        for layer in range(16):
            x, _ = A.load(root / "Samples" / f"{key['name']}v{layer + 1}.flac")
            y = x[: len(t), 0]
            # the partials' sum (left mic) and its parts, without the swell
            parts = np.zeros_like(t)
            comps = []
            for p in key["partials"]:
                L = p["levels"][layer][0]
                if L < -100:
                    continue
                aft = 10 ** (p["drop"] / 20) if p["drop"] < -0.01 else 1.0
                env = 10 ** (L / 20) * ((1 - aft) * 10 ** (p["s1"] * t / 20) + aft * 10 ** (p["s2"] * t / 20)
                                        + 10 ** (p.get("x0", -100) / 20) * 10 ** (p.get("s0", -100) * t / 20))
                comps.append((p["f"], p["phases"][layer][0], env))
            best = None
            for d in DELAYS:
                td = t - d
                s0 = np.zeros_like(t)
                for f, ph, env in comps:
                    s0 += env * np.cos(2 * np.pi * f * t + ph)   # phases are referenced to the file's t = 0
                for tau in TAUS:
                    s = s0 * np.where(td > 0, 1 - np.exp(-np.maximum(td, 0) / tau), 0)
                    g = float(np.dot(s, y) / (np.dot(s, s) + 1e-20))
                    err = float(np.sum((y - g * s) ** 2) / (np.sum(y ** 2) + 1e-20))
                    if best is None or err < best[0]:
                        best = (err, tau, d, g)
            err, tau, d, g = best
            taus.append(tau)
            delays.append(d)
            fits.append(err)
        out["keys"].append({"key": key["key"], "name": key["name"], "tau": taus, "delay": delays, "err": fits})
        print(f"{key['name']:4s} tau ms " + " ".join("%4.1f" % (v * 1000) for v in taus[::3]) +
              "  delay ms " + " ".join("%4.1f" % (v * 1000) for v in delays[::3]) +
              "  resid " + " ".join("%4.2f" % v for v in fits[::3]), flush=True)
    Path(out_path).write_text(json.dumps(out))


if __name__ == "__main__":
    main()
