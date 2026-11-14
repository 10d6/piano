#!/usr/bin/env python3
"""What Strike Point does to a note's partials, key by key.

Needs no recordings: there is no recording of this piano with its hammers elsewhere. It renders
notes at several settings and reports, against the default (12 %, the measured piano):

- each partial's change in level over the first 0.4 s (dB);
- how far the moved comb is from the one predicted for that key (sin(nπx) with the key's own x
  and notch floor, tools/analyze_point.py): the engine doing what the model says;
- **bumps**: how far any partial ends up above the median of its neighbours (n ± 1…3), beyond
  how far it stood above them at the default. A real hammer moved along a string only takes
  partials away near its nodes and gives back the ones it had taken; a partial standing well
  clear of its neighbours where none stood before is the control's reference comb missing the
  string's own (heard as a resonance, like a comb filter). Filling a dip isn't a bump.

    python3 -I tools/measure/strikepoint.py [--render build/piano_render] [--keys 24,36,48,60,72]
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M  # noqa: E402

SETTINGS = [6, 8, 10, 12, 15, 20]


def partial_levels(y, sr, freqs):
    """Each partial's level over the first 0.4 s: peak power within ±1.5 % of its frequency."""
    n = int(0.4 * sr)
    w = np.hanning(n)
    S = sum(np.abs(np.fft.rfft(y[:n, c] * w, 4 * n)) ** 2 for c in range(y.shape[1]))
    fb = sr / (4 * n)
    out = []
    for f in freqs:
        a, b = int(f * 0.985 / fb), int(f * 1.015 / fb) + 2
        out.append(10 * np.log10(S[a:b].max() + 1e-30))
    return np.array(out)


def bumps(L):
    """Each partial's level above the median of its neighbours 1-3 away on both sides."""
    out = np.zeros(len(L))
    for i in range(len(L)):
        nb = [L[j] for j in range(max(0, i - 3), min(len(L), i + 4)) if j != i]
        out[i] = L[i] - np.median(nb)
    return out


def main():
    def extra(p):
        p.add_argument("--keys", default="24,36,48,60,72,84")
        p.add_argument("--vel", type=int, default=100)
    a = M.args(__doc__, extra)
    model = M.model()
    point = {k["key"]: k for k in json.load(open(M.TOOLS / "salamander_point.json"))["keys"]}
    worst = 0.0
    for key in [int(k) for k in a.keys.split(",")]:
        k = model[key]
        ps = [p for p in k["partials"] if p["n"] <= 32 and p["f"] < 16000]
        f = np.array([p["f"] for p in ps])
        n = np.array([p["n"] for p in ps], float)
        ref = partial_levels(M.note(a.render, key, a.vel, 1.0), 48000, f)
        use = ref > ref.max() - 60
        x0, eps = point[key]["x"], point[key]["eps"]

        def comb(x):
            s2 = np.sin(np.pi * n * x) ** 2
            return 10 * np.log10(np.where(n * x <= 0.5, np.maximum(s2, 1e-4), (s2 + eps ** 2) / (1 + eps ** 2)))
        print(f"key {key} ({k['name']}), x {100 * x0:.2f} %, ε {eps:.2f} ({point[key]['source']}), {int(use.sum())} partials")
        b0 = bumps(ref[use])
        for s in SETTINGS:
            if s == 12:
                continue
            L = partial_levels(M.note(a.render, key, a.vel, 1.0, {"tone.strikepoint": s}), 48000, f)
            d = (L - ref)[use]
            pred = np.clip(comb(x0 * s / 12) - comb(x0), -24, 24)[use]
            b = np.maximum(bumps(L[use]), 0) - np.maximum(b0, 0)
            i = int(np.argmax(b))
            worst = max(worst, b[i])
            print(f"  {s:2d} %: partials 1-4 {' '.join(f'{v:+5.1f}' for v in d[:4])} dB; change {d.min():+.0f}…{d.max():+.0f} dB; "
                  f"off the model {np.sqrt(np.mean((d - pred) ** 2)):.1f} dB rms; largest new bump {b[i]:+.0f} dB (partial {int(n[use][i])})")
    print(f"strike point: largest new bump {worst:+.0f} dB")


if __name__ == "__main__":
    main()
