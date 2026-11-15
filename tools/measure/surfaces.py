#!/usr/bin/env python3
"""Which properties of a hammer's surface could be heard? (No recordings needed.)

`tools/hammer_sim.py` gives, for a change to the hammer (harder, another character, another
mass, a rigid surface…), how the force on the string changes with frequency against the felt
fitted to this piano. The engine would apply that change to each partial and to the strike, as
Hardness does today, so its sound is predicted here without building it: a dry note is rendered,
its partials and strike (the render less its body ring and mechanism) go through a zero-phase
filter with that change (kept within ±36 dB, as far as Hardness reaches today), and the result is compared with the plain note by the perceptual
distance (levels matched over the first half second, so colour counts and loudness doesn't; the
level change is given beside it). The owner could hardly hear a control at 1-7 %; 15 % and more
is plain.

It is the change to the hammer's force alone: a surface's own sound (a click, a head that
rings, chatter) isn't in it.

Also the engine's own controls (ENGINE): Hardness for scale, and the hammer's surface (Edge,
Weight, Core, Material) beside the simulated change each is meant to follow, with the distance
between the two. The engine keeps a note's loudness; the simulation's changes don't, so the two
are compared with levels matched.

    python3 -I tools/measure/surfaces.py [--keys 36,48,60,72] [--layers 4,8,13,16] [--wav DIR]
    python3 -I tools/measure/surfaces.py --uneven      (Uneven 100 % on each key from C3 to C5)
"""
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M      # noqa: E402
import perceptual as P  # noqa: E402

sys.path.insert(0, str(M.TOOLS))
import hammer_sim as H  # noqa: E402

LENGTH = 3.3
# The engine's own controls: name, parameters, and the simulated change each is meant to follow
ENGINE = [("engine: Hardness +100 %", {"tone.hardness": 100}, None),
          ("engine: Hardness −100 %", {"tone.hardness": -100}, None),
          ("engine: Edge 25 %", {"hammer.edge": 25}, "a hard tip, 5 % of the mass (a tack)"),
          ("engine: Edge 50 %", {"hammer.edge": 50}, "a hard tip, 20 % of the mass"),
          ("engine: Edge 100 %", {"hammer.edge": 100}, "rigid (a tack)"),
          ("engine: Weight 50 %", {"hammer.weight": 50}, "hammer ×0.5 mass"),
          ("engine: Weight 200 %", {"hammer.weight": 200}, "hammer ×2 mass"),
          ("engine: Edge 100 %, Weight 30 %", {"hammer.edge": 100, "hammer.weight": 30}, "rigid, ×0.3 mass (a light hard hammer)"),
          ("engine: Core 48 %", {"hammer.core": 48}, "thin felt over a hard core"),
          ("engine: Wear 50 %", {"hammer.wear": 50}, None),
          ("engine: Wear 100 %", {"hammer.wear": 100}, None),
          ("engine: Material 25 % (worn felt)", {"hammer.morph": 1, "hammer.material": 25}, None),
          ("engine: Material 50 % (leather)", {"hammer.morph": 1, "hammer.material": 50}, None),
          ("engine: Material 75 % (wood)", {"hammer.morph": 1, "hammer.material": 75}, None),
          ("engine: Material 100 % (metal)", {"hammer.morph": 1, "hammer.material": 100}, None)]
LIMIT = 36.0     # dB: the most a partial may change, as far as Hardness reaches today (3 octaves at 12 dB)
QUIET = {"tone.body": -40, "tone.mech": -40}


def level(y, a=0.0, b=0.5):
    s = y[int(a * 48000):int(b * 48000)]
    return 10 * np.log10(np.mean(s ** 2) + 1e-30)


def note(tool, key, layer, params=None):
    return P.render_note(tool, key, layer, params)


def apply(y, f, db):
    """y through a zero-phase filter with the gain db (dB) at the frequencies f, held flat outside."""
    n = len(y)
    fb = np.fft.rfftfreq(n, 1 / 48000)
    g = 10 ** (np.clip(np.interp(np.log(np.maximum(fb, 1.0)), np.log(f), db), -LIMIT, LIMIT) / 20)
    return np.fft.irfft(np.fft.rfft(y, axis=0) * g[:, None], n, axis=0)


def changes(k, fit, layers):
    """name → [per layer: (f, dB)]: each variant's force spectrum against the felt's, widths included."""
    st = H.key_string(k)
    v = np.array(fit["v"])[[l - 1 for l in layers]]
    fb = np.fft.rfftfreq(H.NFFT, 1 / 192000.0)
    sel = (fb >= 20) & (fb <= 20000)
    f = fb[sel]
    out, base = {}, None
    for name, (s, surf, ratio) in H.variants(k, fit, st).items():
        r = H.run(s, surf, ratio, v, st)
        assert r["stable"], name
        S = H.spectrum(r["F"], r["dt"], f) + 20 * np.log10(ratio / fit["ratio"])
        S = S + 20 * np.log10(np.maximum(np.abs(np.sinc(f / k["f0"] * s.width)), 1e-3))
        if name == "felt":
            base = S
            continue
        out[name] = [(f, S[i] - base[i]) for i in range(len(layers))]
    return out


def case(job):
    tool, k, fit, layers, wav = job
    ch = changes(k, fit, layers)
    rows = []
    for i, layer in enumerate(layers):
        full = note(tool, k["key"], layer)
        tone = note(tool, k["key"], layer, QUIET)          # the partials and the strike
        n = min(len(full), len(tone))
        full, tone = full[:n], tone[:n]
        rest = full - tone
        Nf = P.loudness(full)

        def score(y):
            dl = level(y) - level(full)
            return P.distance(Nf, P.loudness(y * 10 ** (-dl / 20)))["score"], dl

        cells = {}
        made = {}
        for name, per in ch.items():
            f, db = per[i]
            y = apply(tone, f, db) + rest
            made[name] = y
            cells[name] = score(y)
        for name, prm, _ in ENGINE:
            if not set(prm) <= P.known_params(tool):
                continue
            y = note(tool, k["key"], layer, prm)[:n]
            made[name] = y
            cells[name] = score(y)
        for name, _, sim in ENGINE:
            if sim and name in made:
                a, b = made[name], made[sim]
                b = b * 10 ** ((level(a) - level(b)) / 20)
                cells[f"  … against the simulation's {sim.split(' (')[0]}"[:44] + ("" if name.count(",") == 0 else " ")] = \
                    (P.distance(P.loudness(a), P.loudness(b))["score"], 0.0)
        if wav:
            peak = max(np.abs(y).max() for y in made.values())
            g = 0.5 / max(peak, np.abs(full).max())
            sf.write(Path(wav) / f"{k['name'].replace('#', 's')}-layer{layer}-felt.wav", full[:int(2.5 * 48000)] * g, 48000)
            for name, y in made.items():
                safe = name.replace("+", "plus").replace("−", "minus").replace("×", "x").replace("÷", "div")
                safe = "-".join("".join(c if c.isalnum() else " " for c in safe).split())
                sf.write(Path(wav) / f"{k['name'].replace('#', 's')}-layer{layer}-{safe}.wav", y[:int(2.5 * 48000)] * g, 48000)
        rows.append((k["key"], k["name"], layer, cells))
    return rows


def uneven(tool, lo=48, hi=72, layer=13):
    """Uneven 100 %: each key from C3 to C5 against itself at 0 % (any key renders: no simulation)."""
    rows = []
    for key in range(lo, hi + 1):
        a = note(tool, key, layer)
        b = note(tool, key, layer, {"hammer.uneven": 100})
        n = min(len(a), len(b))
        dl = level(b) - level(a)
        rows.append((key, P.distance(P.loudness(a[:n]), P.loudness(b[:n] * 10 ** (-dl / 20)))["score"], dl))
    d = np.array([r[1] for r in rows])
    print(f"\nUneven 100 %, keys {lo}-{hi} at layer {layer}: distance from the same key at 0 % (levels matched) and level change")
    print("  " + "  ".join(f"{r[1]:.0f}" for r in rows) + "  %")
    print("  " + "  ".join(f"{r[2]:+.1f}" for r in rows) + "  dB")
    print(f"  median {np.median(d):.0f} %, {d.min():.0f}-{d.max():.0f} %; {np.sum(d < 7)} of {len(d)} keys under 7 %; "
          f"neighbours differ by {np.median(np.abs(np.diff(d))):.0f} % (median)")


def main():
    def extra(p):
        p.add_argument("--keys", default="36,48,60,72")
        p.add_argument("--layers", default="4,8,13,16")
        p.add_argument("--wav", default="")
        p.add_argument("--procs", type=int, default=2)
        p.add_argument("--uneven", action="store_true", help="only the Uneven sweep over neighbouring keys")
    a = M.args(__doc__, extra)
    if a.uneven:
        uneven(a.render)
        return
    import json
    fits = {f["key"]: f for f in json.load(open(M.TOOLS / "salamander_hammer_sim.json"))["keys"]}
    want = [int(x) for x in a.keys.split(",")]
    layers = [int(x) for x in a.layers.split(",")]
    if a.wav:
        Path(a.wav).mkdir(parents=True, exist_ok=True)
    keys = [k for k in H.load_keys() if k["key"] in want and k["key"] in fits]
    with Pool(a.procs) as pool:
        rows = [r for rs in pool.map(case, [(a.render, k, fits[k["key"]], layers, a.wav) for k in keys], chunksize=1) for r in rs]
    names = list(rows[0][3])
    print("distance from the felt (%, levels matched) and level change over the first 0.5 s (dB)")
    print(f"{'':44s}" + "".join(f"   layer {l:2d}      " for l in layers) + "  (range over " + ", ".join(k["name"] for k in keys) + ")")
    summary = {}
    for name in names:
        line = f"{name:44s}"
        for l in layers:
            d = np.array([r[3][name][0] for r in rows if r[2] == l])
            dl = np.array([r[3][name][1] for r in rows if r[2] == l])
            summary[(name, l)] = float(np.median(d))
            line += f" {d.min():3.0f}-{d.max():3.0f} % {np.median(dl):+5.1f} "
        print(line)
    print("\nper key at layer 13 (%):  " + "  ".join(k["name"] for k in keys))
    for name in names:
        print(f"{name:44s}" + " ".join(f"{r[3][name][0]:5.1f}" for r in rows if r[2] == 13))


if __name__ == "__main__":
    main()
