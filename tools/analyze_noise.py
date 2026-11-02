#!/usr/bin/env python3
"""Measure the strike's noise: what the recordings contain beyond the partials.

For each sampled key, velocity layer and mic, over the first 250 ms:
octave-band power of the recording, minus the power the partial model
(tools/salamander.json) accounts for. What's left is the hammer's knock, the
key and action, and the soundboard's broadband ring. Each band is fitted with
a starting level and a decay rate.

    python3 -I tools/analyze_noise.py <Salamander dir> tools/salamander.json tools/salamander_noise.json
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import analyze as A  # noqa: E402

BANDS = 31.25 * 2.0 ** np.arange(10)   # lower edges: 31 Hz … 16 kHz
N = 1024
HOP = 96
TMAX = 0.25
ONSET_S = 0.002


def partial_power(key, layer, mic, t):
    """Power (variance) of the partial model per band at times t."""
    out = np.zeros((len(BANDS), len(t)))
    for p in key["partials"]:
        L = p["levels"][layer][mic]
        if L < -110:
            continue
        env = 10 ** ((L + A.shape_db(A.shape_at(p, layer), t)) / 20)
        b = np.searchsorted(BANDS, p["f"], side="right") - 1
        if 0 <= b < len(BANDS) and p["f"] < BANDS[b] * 2:
            out[b] += env ** 2 / 2
    return out


def between_ratio(x, sr, key, mic, t, pp):
    """How much of each band's residual (recording minus the partial model) really lies between
    the partials. In the treble, partials have skirts and neighbours the model's narrow partials
    don't hold; counted as noise, that energy would spread over the whole band as hiss (15-20 dB
    too much at 8-16 kHz on the top keys). Measured, like the body's ring, over the whole window
    from one long spectrum with each partial's surroundings left out; None where the partials
    are too close together for that."""
    L = len(t) and int(TMAX * sr)
    seg = x[:L, mic]
    f0 = key["f0"]
    width = max(4.0 * sr / L, 0.04 * f0, 6.0)
    if 2 * width > 0.7 * f0:
        return None
    nfft = 1 << 17
    sp = np.abs(np.fft.rfft(seg * np.hanning(L), nfft)) ** 2 / (0.375 * L * L / 2) / (nfft / L)
    f = np.fft.rfftfreq(nfft, 1 / sr)
    near = np.zeros_like(f, bool)
    for p in key["partials"]:
        near |= np.abs(f - p["f"]) < width
        for c in (p.get("cluster") or {}).get("cents", []):
            near |= np.abs(f - p["f"] * 2 ** (c / 1200)) < width
    near |= (np.abs(f - 50 * np.round(f / 50)) < 2.5) & (f < 1000)
    out = []
    for b, lo in enumerate(BANDS):
        band = (f >= lo) & (f < 2 * lo)
        keep = band & ~near
        if keep.sum() < 3 or not band.any():
            out.append(1.0)
            continue
        betw = sp[keep].sum() * band.sum() / keep.sum()            # variance between the partials, whole band
        resid = max(sp[band].sum() - pp[b].mean(), 1e-30)          # the band less the partial model
        out.append(float(min(1.0, betw / resid)))
    return out


def main():
    root, model_path, out_path = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    model = json.load(open(model_path))
    win = np.hanning(N)
    freqs = np.fft.rfftfreq(N, 1 / 48000)
    result = {"bands": BANDS.tolist(), "keys": []}
    for key in model["keys"]:
        levels = np.full((16, 2, len(BANDS)), -150.0)
        decays = []
        seen_between = False
        for layer in range(16):
            x, sr = A.load(root / "Samples" / f"{key['name']}v{layer + 1}.flac")
            for mic in range(2):
                s = x[: int(TMAX * sr) + N, mic]
                starts = np.arange(0, len(s) - N, HOP)
                t = (starts + N / 2) / sr
                spec = np.array([np.abs(np.fft.rfft(s[i:i + N] * win)) ** 2 for i in starts])
                pp = partial_power(key, layer, mic, t)
                ratio = between_ratio(x, sr, key, mic, t, pp)
                seen_between |= ratio is not None
                ratio = ratio or [1.0] * len(BANDS)
                for b, lo in enumerate(BANDS):
                    sel = (freqs >= lo) & (freqs < 2 * lo)
                    v = spec[:, sel].sum(1) / (0.1875 * N * N)       # variance in the band (Parseval)
                    resid = np.maximum(v - pp[b], 0) * ratio[b] + 1e-16
                    db = 10 * np.log10(resid)
                    # fit from the first frame after the attack peak, while it's above -100 dBFS
                    ok = db > -100
                    if ok.sum() < 4:
                        continue
                    i0 = int(np.argmax(db[: max(3, int(0.03 * sr / HOP))]))
                    tt, dd = t[i0:][ok[i0:]], db[i0:][ok[i0:]]
                    if len(tt) < 4:
                        continue
                    # two stages, as with the partials: the knock itself, gone within tens of ms, then a
                    # slower part (on the top keys the knock falls ~300 dB/s and the rest ~60). One line
                    # through both would fade the knock too slowly (hiss) and lose the slower part.
                    l0, s1, s2, tk = A.hinge_fit(tt - tt[0], dd)
                    # the level at the strike: the first frame is centred ~10 ms in (and averages a knock
                    # that falls hundreds of dB/s), so take the fitted line back to the attack's start
                    # (load() keeps 2 ms before it), at most 12 dB
                    back = min(12.0, max(0.0, -s1 * (tt[0] - ONSET_S)))
                    levels[layer, mic, b] = l0 + back if np.isfinite(l0) else -150   # dB of variance
                    if layer >= 8:
                        drop = (s1 - s2) * tk if s1 != s2 else 0.0
                        decays.append((b, min(s1, -5.0), min(s2, -5.0), min(0.0, max(-60.0, drop)), dd[0]))
        # per band decays: weighted by level, pooled over the louder layers and both mics
        dec, dec2, drops = [], [], []
        for b in range(len(BANDS)):
            ds = [d for d in decays if d[0] == b]
            if not ds:
                dec.append(-200.0); dec2.append(-200.0); drops.append(0.0)
                continue
            w = [10 ** (d[4] / 20) for d in ds]
            dec.append(float(np.average([d[1] for d in ds], weights=w)))
            dec2.append(float(np.average([d[2] for d in ds], weights=w)))
            drops.append(float(np.average([d[3] for d in ds], weights=w)))
        result["keys"].append({"key": key["key"], "name": key["name"], "levels": levels.tolist(), "decay": dec,
                               "decay2": dec2, "drop": drops, "between": seen_between})
        print(key["name"], " ".join("%6.0f" % l for l in levels[15, 0]), " | decay", " ".join("%5.0f" % d for d in dec),
              " | then", " ".join("%4.0f" % d for d in dec2), " | drop", " ".join("%4.0f" % d for d in drops), flush=True)
    Path(out_path).write_text(json.dumps(result))


if __name__ == "__main__":
    main()
