#!/usr/bin/env python3
"""Calibrate the strike by resynthesis, where the modal fit can't see it.

The modes (tools/analyze_modes.py) are fitted from where the envelope's window clears the swell:
in the bass that window is long (5/f0, 45 ms at A2), so the fit starts 50-100 ms in, and how
loud the strike is before that is extrapolated. Measured, the strike (10-40 ms) came out 2-3 dB
weak on A1, D#2 and A2 and 1.5 dB weak on C3-F#3, where the owner heard less punch than on main.

This renders each measured key below A3 at layers 4, 8, 12 and 16, measures its level in
octave bands over 10-40 ms against the recording's, and corrects each band by 60 % of the
difference, interpolated across layers. make_header.py plays the correction as one more mode per
partial in the band: on its strongest mode's frequency, in phase (or in opposition, to take
away), fading at 100 dB/s, so it changes the strike and is gone within a few tenths of a second. Corrections accumulate in salamander.json (`attack_adjust`, dB per
layer and band), which make_header.py applies. Run after choose_modes.py; then make_header.py,
build, and repeat (two or three passes).

    python3 -I tools/calibrate_attack.py <Salamander dir> build/piano_render tools/salamander.json
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "measure"))
import common as M  # noqa: E402

BANDS = [62.5 * 2 ** i for i in range(9)]
LAYERS = (4, 8, 12, 16)
LAST_KEY = 56                 # from A3 up the fit sees the strike (its window is short enough)
T0, T1 = 0.010, 0.040
STEP = 0.6


def bands(x, sr):
    seg = x[int(T0 * sr):int(T1 * sr)].mean(1)
    n = 1 << 14
    X = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), n)) ** 2
    f = np.fft.rfftfreq(n, 1 / sr)
    return np.array([10 * np.log10(X[(f >= lo) & (f < 2 * lo)].sum() + 1e-20) for lo in BANDS])


def main():
    sal, tool, path = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    data = json.load(open(path))
    for k in data["keys"]:
        if k["key"] > LAST_KEY:
            continue
        adj = np.array(k.get("attack_adjust") or [[0.0] * len(BANDS)] * 16)
        corr = {}
        for layer in LAYERS:
            rec = bands(M.recording(sal, k["key"], layer), 48000)
            mod = bands(M.note(tool, k["key"], M.layer_vel(layer), 0.1), 48000)
            d = rec - mod
            d[rec < rec.max() - 40] = 0.0          # bands too quiet to matter or to measure
            corr[layer - 1] = np.clip(d, -6, 6)
        xs = sorted(corr)
        for v in range(16):
            c = np.array([np.interp(v, xs, [corr[x][b] for x in xs]) for b in range(len(BANDS))])
            adj[v] = np.clip(adj[v] + STEP * c, -12, 12)
        k["attack_adjust"] = adj.round(2).tolist()
        print(f"{k['name']:4s} error " + " ".join(f"{v:+5.1f}" for v in np.mean([corr[x] for x in xs], 0)), flush=True)
    path.write_text(json.dumps(data))


if __name__ == "__main__":
    main()
