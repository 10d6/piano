#!/usr/bin/env python3
"""Measure the body's ring: what the recordings hold between the partials
from 0.1 s on.

After a strike the soundboard, case and room keep ringing: diffuse energy
between the strings' partials, a couple of dozen dB below the note, fading
over seconds. On high notes, whose partials are far apart, it is a large
part of the sound (the echo-like tail). For each sampled key, layer and mic,
this measures that energy in octave bands over time windows, removes the
recordings' own noise floor (hum, hiss), and fits each band with a level at
the strike and a decay.

    python3 -I tools/analyze_body.py <Salamander dir> tools/salamander.json tools/salamander_body.json
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import analyze as A  # noqa: E402

BANDS = 31.25 * 2.0 ** np.arange(10)        # the same octave bands as the strike noise
WINDOWS = [(0.10, 0.20), (0.20, 0.35), (0.35, 0.6), (0.6, 1.0), (1.0, 1.5), (1.5, 2.2), (2.2, 3.0)]
N = 1 << 17


def between(seg, sr, f0, B):
    """Variance per octave band of what lies between the partials (and away from mains hum),
    scaled up for the bins left out."""
    L = len(seg)
    # leave out each partial's main lobe and skirt; if that covers most of the gap between
    # partials (low notes, short windows), this window can't tell the body from the strings
    width = max(4.0 * sr / L, 0.04 * f0, 6.0)
    if 2 * width > 0.7 * f0:
        return np.full(len(BANDS), np.nan)
    sp = np.abs(np.fft.rfft(seg * np.hanning(L), N)) ** 2 / (0.1875 * L * L)
    f = np.fft.rfftfreq(N, 1 / sr)
    near = np.zeros_like(f, bool)
    k = 1
    while True:
        fk = k * f0 * np.sqrt(1 + B * k * k)
        if fk > sr / 2:
            break
        near |= np.abs(f - fk) < width
        k += 1
    near |= (np.abs(f - 50 * np.round(f / 50)) < 2.5) & (f < 1000)
    out = []
    for lo in BANDS:
        band = (f >= lo) & (f < 2 * lo)
        keep = band & ~near
        if keep.sum() < 3:
            out.append(np.nan)
            continue
        # the exclusions remove bins of a diffuse spectrum: scale back to the whole band
        out.append(sp[keep].sum() * band.sum() / keep.sum() * (N / L) ** -1)
    return np.array(out)


def main():
    root, model_path, out_path = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    model = json.load(open(model_path))
    sr = 48000
    # the recordings' own floor: the last 0.4 s of the quietest top notes
    fl = []
    for name in ("C8", "A7"):
        k = next(k for k in model["keys"] if k["name"] == name)
        for v in (1, 2, 3):
            x, _ = A.load(root / "Samples" / f"{name}v{v}.flac")
            for ch in range(2):
                fl.append(between(x[-int(0.4 * sr):, ch], sr, k["f0"], k["B"]))
    floor = np.nanmedian(np.array(fl), axis=0)
    print("floor (dB):", " ".join("%6.1f" % v for v in 10 * np.log10(floor)))

    tc = np.array([(a + b) / 2 for a, b in WINDOWS])
    result = {"bands": BANDS.tolist(), "floor": (10 * np.log10(floor)).tolist(), "keys": []}
    for key in model["keys"]:
        lv = np.full((16, 2, len(BANDS), len(WINDOWS)), np.nan)
        for layer in range(16):
            x, _ = A.load(root / "Samples" / f"{key['name']}v{layer + 1}.flac")
            for ch in range(2):
                for w, (a, b) in enumerate(WINDOWS):
                    if int(b * sr) > len(x):
                        continue
                    p = between(x[int(a * sr):int(b * sr), ch], sr, key["f0"], key["B"]) - floor
                    ok = np.isfinite(p) & (p > floor * 10 ** 0.6)   # at least 6 dB above the floor
                    lv[layer, ch, ok, w] = 10 * np.log10(p[ok])
        # decay per band from the louder layers, both mics: a common slope, an offset per series
        dec = []
        for b in range(len(BANDS)):
            series = [lv[l, c, b] for l in range(7, 16) for c in range(2)]
            xs, ys, gs = [], [], []
            for g, s in enumerate(series):
                ok = np.isfinite(s)
                if ok.sum() >= 3:
                    xs += list(tc[ok]); ys += list(s[ok]); gs += [g] * int(ok.sum())
            if len(set(gs)) < 2:
                dec.append(-60.0)
                continue
            G = sorted(set(gs))
            M = np.zeros((len(xs), len(G) + 1))
            for i, (t, g) in enumerate(zip(xs, gs)):
                M[i, 0] = t
                M[i, 1 + G.index(g)] = 1
            sol, *_ = np.linalg.lstsq(M, np.array(ys), rcond=None)
            dec.append(float(min(max(sol[0], -120.0), -3.0)))
        # level at the strike per layer, mic and band, along that decay
        lev = np.full((16, 2, len(BANDS)), -150.0)
        for layer in range(16):
            for c in range(2):
                for b in range(len(BANDS)):
                    s = lv[layer, c, b]
                    ok = np.isfinite(s)
                    if ok.sum() >= 2:
                        lev[layer, c, b] = float(np.mean(s[ok] - dec[b] * tc[ok]))
        result["keys"].append({"key": key["key"], "name": key["name"], "levels": lev.tolist(), "decay": dec})
        print(f"{key['name']:4s} v12 L: " + " ".join("%6.0f" % v for v in lev[11, 0]) +
              "  | decay " + " ".join("%5.0f" % v for v in dec), flush=True)
    Path(out_path).write_text(json.dumps(result))


if __name__ == "__main__":
    main()
