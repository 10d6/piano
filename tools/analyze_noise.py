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


def main():
    root, model_path, out_path = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    model = json.load(open(model_path))
    win = np.hanning(N)
    freqs = np.fft.rfftfreq(N, 1 / 48000)
    result = {"bands": BANDS.tolist(), "keys": []}
    for key in model["keys"]:
        levels = np.full((16, 2, len(BANDS)), -150.0)
        decays = []
        for layer in range(16):
            x, sr = A.load(root / "Samples" / f"{key['name']}v{layer + 1}.flac")
            for mic in range(2):
                s = x[: int(TMAX * sr) + N, mic]
                starts = np.arange(0, len(s) - N, HOP)
                t = (starts + N / 2) / sr
                spec = np.array([np.abs(np.fft.rfft(s[i:i + N] * win)) ** 2 for i in starts])
                pp = partial_power(key, layer, mic, t)
                for b, lo in enumerate(BANDS):
                    sel = (freqs >= lo) & (freqs < 2 * lo)
                    v = spec[:, sel].sum(1) / (0.1875 * N * N)       # variance in the band (Parseval)
                    resid = np.maximum(v - pp[b], 0) + 1e-16
                    db = 10 * np.log10(resid)
                    # fit from the first frame after the attack peak, while it's above -100 dBFS
                    ok = db > -100
                    if ok.sum() < 4:
                        continue
                    i0 = int(np.argmax(db[: max(3, int(0.03 * sr / HOP))]))
                    tt, dd = t[i0:][ok[i0:]], db[i0:][ok[i0:]]
                    if len(tt) < 4:
                        continue
                    slope, icpt = np.polyfit(tt - tt[0], dd, 1)
                    levels[layer, mic, b] = dd[0] if np.isfinite(dd[0]) else -150   # the peak, in dB of variance
                    if layer >= 8:
                        decays.append((b, min(slope, -5.0), dd[0]))
        # per band decay: weighted by level, pooled over the louder layers and both mics
        dec = []
        for b in range(len(BANDS)):
            ds = [(d, 10 ** (l / 20)) for bb, d, l in decays if bb == b]
            dec.append(float(np.average([d for d, _ in ds], weights=[w for _, w in ds])) if ds else -200.0)
        result["keys"].append({"key": key["key"], "name": key["name"], "levels": levels.tolist(), "decay": dec})
        print(key["name"], " ".join("%6.0f" % l for l in levels[15, 0]), " | decay", " ".join("%5.0f" % d for d in dec), flush=True)
    Path(out_path).write_text(json.dumps(result))


if __name__ == "__main__":
    main()
