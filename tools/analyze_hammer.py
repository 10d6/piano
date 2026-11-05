#!/usr/bin/env python3
"""The hammer's spectrum, from how each key's partials brighten from soft to loud.

A felt hammer's force on the string is a pulse lasting a millisecond or a few; its spectrum is
flat up to a corner near the inverse of the contact time and falls above it. The felt stiffens
as it is compressed, so a faster hammer stays on the string for less time and the corner moves
up: that is why a piano brightens as it is played harder. Modelled as

    |F(f)| ∝ 1 / (1 + (f / fc)²)          (−12 dB per octave above fc)
    fc = fc13 · (v / v13)^α               (v: hammer speed, against layer 13's)

and fitted per measured key to the 16 layers' partial levels (salamander.json, mics averaged):
each layer's change against layer 13, partial by partial, up to a level per layer, for partials
within 50 dB of the loudest. The hammer's speed per layer is read from the two lowest partials,
which lie well below the corner. One corner and one exponent per key explain 70-86 % of the
partials' change with velocity from A0 to C6 (rms 4.6-9.3 dB → 1.9-4.9 dB); above C6, where
the top keys have few, noisy partials, 20-94 %. α comes out near 2 (1.4-2.8) up to C6, near 1
above; the corner at layer 13 is 3-12 kHz.

Both are smoothed across the keys (a running median of 5 for α, of 3 for log fc13), and the
corner per layer kept rising with the layer and within 300 Hz-24 kHz. The engine needs nothing
of this at default settings: Hardness moves the corner (a harder felt is a shorter contact),
and the partials and the strike change by the ratio of the moved spectrum to this one.

    python3 -I tools/analyze_hammer.py tools/salamander.json tools/salamander_hammer.json
"""
import json
import sys

import numpy as np
from scipy.optimize import least_squares

REF = 12          # 0-based layer 13
FC_RANGE = (300.0, 24000.0)


def levels(p):
    return np.array(p["levels"]).mean(1)


def fit_key(k):
    ps = [p for p in k["partials"] if max(levels(p)) > -96]
    if len(ps) < 4:
        return None
    f = np.array([p["f"] for p in ps])
    L = np.array([levels(p) for p in ps])
    use = L[:, REF] > L[:, REF].max() - 50
    f, L = f[use], L[use]
    D = L - L[:, [REF]]
    vrel = 10 ** (D[:2].mean(0) / 20)
    layers = [l for l in range(16) if l != REF]

    def resid(x):
        fc13, alpha = np.exp(x[0]), x[1]
        out = []
        for l in layers:
            pred = -20 * np.log10(1 + (f / (fc13 * vrel[l] ** alpha)) ** 2) + 20 * np.log10(1 + (f / fc13) ** 2)
            e = D[:, l] - pred
            out.append(e - e.mean())
        return np.concatenate(out)

    best = None
    for fc0 in (300, 1000, 3000):
        for a0 in (0.3, 1.0):
            s = least_squares(resid, [np.log(fc0), a0], bounds=([np.log(50), 0], [np.log(20000), 3]))
            if best is None or s.cost < best.cost:
                best = s
    flat = np.concatenate([D[:, l] - D[:, l].mean() for l in layers])
    return {"key": k["key"], "fc13": float(np.exp(best.x[0])), "alpha": float(best.x[1]), "vrel": vrel.tolist(),
            "rms_flat": float(np.sqrt(np.mean(flat ** 2))), "rms_model": float(np.sqrt(np.mean(best.fun ** 2)))}


def running_median(v, n):
    h = n // 2
    return np.array([np.median(v[max(0, i - h):i + h + 1]) for i in range(len(v))])


def main():
    model = json.load(open(sys.argv[1]))
    fits = [r for r in (fit_key(k) for k in model["keys"]) if r]
    alpha = running_median(np.array([r["alpha"] for r in fits]), 5)
    lfc = running_median(np.log([r["fc13"] for r in fits]), 3)
    out = []
    for r, a, lf in zip(fits, alpha, lfc):
        fc = np.exp(lf) * np.array(r["vrel"]) ** a
        fc = np.clip(np.maximum.accumulate(fc), *FC_RANGE)
        out.append({"key": r["key"], "fc": fc.round(1).tolist(), "alpha": float(a), "fc13": float(np.exp(lf)),
                    "fit_alpha": r["alpha"], "fit_fc13": r["fc13"], "rms_flat": r["rms_flat"], "rms_model": r["rms_model"]})
        print(f"{r['key']:3d}: corner at layer 13 {np.exp(lf):6.0f} Hz (fit {r['fc13']:6.0f}), α {a:.2f} (fit {r['alpha']:.2f}); "
              f"layers 1/16 {fc[0]:6.0f} / {fc[15]:6.0f} Hz; explains {100 * (1 - r['rms_model'] ** 2 / r['rms_flat'] ** 2):.0f} %")
    json.dump({"keys": out}, open(sys.argv[2], "w"), indent=1)


if __name__ == "__main__":
    main()
