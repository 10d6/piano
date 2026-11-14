#!/usr/bin/env python3
"""How far is a real neighbouring key from a key's recording? The yardstick for the keys between.

The 58 keys without recordings are interpolated from their measured neighbours, and
`holdout.py --perceptual` says how far a key left out lands from its own recording (25 %). Is
that a fault of the interpolation, or just how much one string differs from the next? This
takes each key's measured neighbours (3 semitones below and above, the same ones a left-out key
is interpolated from), plays their *recordings* at the key's pitch (resampled) and measures the
perceptual distance to the key's own recording, layer for layer.

If a real string, a minor third away, is as far from the key as the interpolated model is, the
model has nothing left to learn from the neighbours: what remains is the string's own (where
its hammer sits against its partials, how its unison beats, how each partial decays), and no
recording of it exists for the 58 keys anyway.

Resampling moves everything with the pitch (the soundboard's response by 3 semitones, the decays
by 19 %), which the model's interpolation doesn't, so this is a yardstick, not a floor.

    python3 -I tools/measure/neighbour.py [--keys 30,42,54,66,78,90] [--sal …]
"""
import sys
from fractions import Fraction
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M      # noqa: E402
import perceptual as P  # noqa: E402


def transposed(sal, key, layer, semis):
    """The recording of `key`, played `semis` semitones higher (resampled)."""
    fr = Fraction(2 ** (-semis / 12)).limit_denominator(400)
    return resample_poly(M.recording(sal, key, layer), fr.numerator, fr.denominator, axis=0)


def main():
    def extra(p):
        p.add_argument("--keys", default="30,42,54,66,78,90", help="measured keys (holdout.py's default six)")
    a = M.args(__doc__, extra)
    floor = P.recording_floor(a.sal)
    measured = set(P.DEFAULT_KEYS)
    best, both = [], []
    print("key    layers 4, 8, 13, 16: the nearer neighbour (%)   |  from below, from above")
    for key in [int(k) for k in a.keys.split(",")]:
        row, sides = [], {-3: [], 3: []}
        for layer in P.DEFAULT_LAYERS:
            Nr = P.rec_loudness(a.sal, key, layer)
            ds = {}
            for nb in (-3, 3):
                if key + nb in measured:
                    ds[nb] = P.distance(Nr, P.loudness(transposed(a.sal, key + nb, layer, -nb), floor=floor))["score"]
                    sides[nb].append(ds[nb])
            row.append(min(ds.values()))
            both += list(ds.values())
        best += row
        print(f"{M.A.note_name(key):5s}  " + "  ".join(f"{v:5.1f}" for v in row) + "   |  " +
              ", ".join(f"{np.mean(v):5.1f}" for v in sides.values() if v))
    print(f"a real neighbouring key, transposed: mean {np.mean(best):.1f} % (the nearer one), {np.mean(both):.1f} % (either)")


if __name__ == "__main__":
    main()
