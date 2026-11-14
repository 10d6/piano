#!/usr/bin/env python3
"""Where the hammer strikes each key's string, from the comb it leaves in the partials.

A hammer striking a string at the fraction x of its length excites partial n as sin(nπx): the
partials with a node near the hammer are weak (every 1/x-th one), and the ones either side of a
node move in opposite senses. On these recordings the notches aren't infinitely deep (the hammer
has a width, the unison's strings and their polarisations share energy through the bridge, and
what the strings make between themselves lands near the missing partials), so past the first
antinode the comb has a floor:

    comb(n) = |sin(nπx)|                              up to the first antinode (n·x ≤ ½)
            = √( (sin²(nπx) + ε²) / (1 + ε²) )         beyond it
    level(n) = trend(log n) + 20·log10 comb(n)

fitted per measured key to the partials' levels at the strike (salamander.json; the modes' power
where a partial has modes, mics averaged in power): one x and one ε for the louder layers
together, a smooth trend (a quadratic in log n: the hammer's spectrum, the soundboard's) per
layer. Small errors in x matter: 0.3 points of the string's length turn the comb a quarter of its
period by the 25th partial, so one x for the whole keyboard is not enough.

A key's fit is kept where the comb is really there: it explains at least MIN_EXPLAINED of the
partial-to-partial variation over at least MIN_PARTIALS partials, and the layers fitted alone agree on x within MAX_SPREAD. That is
the low bass, A0-F#2: x 11.6-12.3 % of the string, explaining 16-59 %. The notches' depth is
seen by only a partial or two per key, so ε is pooled over those keys (their median). And the
notches differ: one node's partial is 20 dB under its neighbours, the next 8. So each partial of
those keys also gets its **lift**: how far it really sits below its neighbours. Moving the hammer
off a node gives a partial back no more than that (or 6 dB, the comb's broad shape), so no
partial is raised clear of its neighbours on the strength of a notch that wasn't there.

From A2 up the comb is weak in these recordings: with x held near the bass's, the fits want
ε ≥ 0.55 (notches of 2-5 dB; only the first dip, near partial 8, shows), and with x free they
find nothing steady. Those keys take the bass's median x and DESIGN_DEPTH: the deepest notch
the middle supports. That part is a judgment, not a measurement.

    python3 -I tools/analyze_point.py tools/salamander.json tools/salamander_point.json
"""
import json
import sys

import numpy as np

LAYERS = [9, 10, 11, 12, 13, 14, 15]     # 0-based: the louder layers, where most partials are fitted
N_MAX = 40          # further up, the bass's partials are weak and the fits less steady (x moves 0.1-0.2 points)
RANGE_DB = 60.0
X_RANGE = (0.07, 0.17)
EPS = (0.05, 0.07, 0.1, 0.14, 0.2, 0.28, 0.4, 0.55, 0.75, 1.0)
MIN_EXPLAINED = 0.15
MIN_PARTIALS = 25        # three periods of the comb: with a dozen partials (F#5, A5) a comb fits by chance
MAX_SPREAD = 0.004
DESIGN_DEPTH = 0.55      # ε where the comb couldn't be measured (notches about 6 dB deep)


def strike_levels(p, layers):
    """A partial's level at the strike per layer (dB, mics averaged in power): its modes' power
    where it has modes (as make_header plays them: unfitted layers take the nearest fitted one,
    moved by the difference in analyze.py's level), else analyze.py's level."""
    lv = np.array(p["levels"])
    base = 10 * np.log10(np.mean(10 ** (lv / 10), axis=1))
    if not p.get("modes"):
        return base[layers]
    fitted = p.get("fit_layers") or [v for v in range(len(lv)) if p["modes"][0]["levels"][v][0] > -190]
    out = []
    for v in layers:
        src = v if v in fitted else min(fitted, key=lambda u: abs(u - v))
        pw = sum(np.mean(10 ** (np.array(m["levels"][src]) / 10)) for m in p["modes"])
        out.append(10 * np.log10(pw + 1e-30) + base[v] - base[src])
    return np.array(out)


def comb_db(n, x, eps):
    s2 = np.sin(np.pi * n * x) ** 2
    return 10 * np.log10(np.where(n * x <= 0.5, s2, (s2 + eps ** 2) / (1 + eps ** 2)))


def fit_key(k, layers=LAYERS, x_range=X_RANGE):
    ps = [p for p in k["partials"] if p["n"] <= N_MAX and p["f"] < 16000]
    if len(ps) < 8:
        return None
    n = np.array([p["n"] for p in ps], float)
    L = np.array([strike_levels(p, layers) for p in ps])          # partial × layer
    use = L.max(1) > L.max() - RANGE_DB
    n, L = n[use], L[use]
    if len(n) < 8 or n.max() < 12:
        return None
    X = np.stack([np.ones_like(n), np.log2(n), np.log2(n) ** 2], 1)
    P = np.eye(len(n)) - X @ np.linalg.pinv(X)                    # removes the trend

    def cost(c, Lc):
        r = P @ (Lc - c[:, None])
        r = np.clip(r, -15, 15)                                   # a partial far off counts as 15 dB
        return np.mean(r ** 2, axis=0)                            # per layer

    v0 = cost(np.zeros_like(n), L)
    xs = np.arange(x_range[0], x_range[1], 0.00025)
    best, per = (v0.mean(), None, None), [(v, None) for v in v0]
    for x in xs:
        for e in EPS:
            v = cost(comb_db(n, x, e), L)
            if v.mean() < best[0]:
                best = (v.mean(), x, e)
            for i in range(len(layers)):
                if v[i] < per[i][0]:
                    per[i] = (v[i], x)
    if best[1] is None:
        return None
    xl = np.array([x for _, x in per if x is not None])
    return {"key": k["key"], "x": float(best[1]), "eps": float(best[2]),
            "explained": float(1 - best[0] / v0.mean()), "spread": float(np.percentile(xl, 75) - np.percentile(xl, 25)) if len(xl) else 1.0,
            "rms_flat": float(np.sqrt(v0.mean())), "rms_comb": float(np.sqrt(best[0])), "partials": int(len(n))}


def lifts(k, layers=LAYERS):
    """How far each partial sits below its neighbours (the median of those 1-3 away on either
    side), in dB, the median over the louder layers: what the string's own comb took from it,
    and so the most that moving the hammer away can give back."""
    ps = sorted(k["partials"], key=lambda p: p["n"])
    L = {p["n"]: strike_levels(p, layers) for p in ps}
    out = {}
    for n in L:
        nb = [L[m] for m in range(n - 3, n + 4) if m != n and m in L]
        if len(nb) >= 3:
            out[n] = float(np.clip(np.median(np.median(nb, axis=0) - L[n]), 0, 30))
    return out


def name(key):
    return "C C# D D# E F F# G G# A A# B".split()[key % 12] + str(key // 12 - 1)


def main():
    model = json.load(open(sys.argv[1]))
    fits = {k["key"]: fit_key(k) for k in model["keys"]}
    kept = {key: f for key, f in fits.items() if f and f["explained"] >= MIN_EXPLAINED and f["partials"] >= MIN_PARTIALS and f["spread"] <= MAX_SPREAD}
    keys = [k["key"] for k in model["keys"]]
    by_key = {k["key"]: k for k in model["keys"]}
    x_bass = float(np.median([f["x"] for f in kept.values()])) if kept else 0.12
    eps_bass = float(np.median([f["eps"] for f in kept.values()])) if kept else DESIGN_DEPTH
    out = []
    for key in keys:
        f = fits[key]
        if key in kept:
            x, eps, src = f["x"], eps_bass, "measured"
        else:
            x, eps, src = x_bass, DESIGN_DEPTH, "design"
        lift = {str(n): round(v, 1) for n, v in lifts(by_key[key]).items()} if key in kept else {}
        out.append({"key": key, "x": round(float(x), 5), "eps": eps, "source": src, "lift": lift, "fit": f})
        tail = (f"fit x {100 * f['x']:5.2f} %, ε {f['eps']:.2f}, explains {100 * f['explained']:3.0f} % "
                f"({f['rms_flat']:.1f} → {f['rms_comb']:.1f} dB over {f['partials']} partials), layers' spread {100 * f['spread']:.2f}") if f else "no fit"
        print(f"{name(key):4s} x {100 * x:5.2f} %  ε {eps:.2f}  {src:8s} | {tail}")
    json.dump({"keys": out}, open(sys.argv[2], "w"), indent=1)


if __name__ == "__main__":
    main()
