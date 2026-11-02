#!/usr/bin/env python3
"""Calibrate the strike noise by resynthesis: render each measured key, measure what lies between
its partials (the knock, then its slower part) against the recording, and correct the noise's
two stages per key and octave band. Run after analyze_noise.py and make_header.py, then rebuild
and repeat (two or three passes settle it):

    python3 -I tools/calibrate_noise.py <Salamander dir> build/piano_render tools/salamander_noise.json

The fits in analyze_noise.py read the noise from frames that are long next to a top key's knock,
and through partials whose skirts the model doesn't hold; measuring the result the way it is
heard, between the partials, corrects what those approximations miss. The first 40 ms set the
knock (keys from D#4 up, where 40 ms windows can tell partials from what's between them), 40-150
ms its slower part (from A2 up). Below the fundamental there are no partials, so there the whole
band is measured. Corrections accumulate in the json (adjust1, adjust2: dB per band) and
make_header.py applies them. The body's ring is left to its own fit
(analyze_body.py): it doesn't fade along one line, and a line fitted to its first second (tried)
takes away the long tail that high notes need.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent / "measure"))
sys.path.insert(0, str(Path(__file__).parent))
import analyze_body as AB  # noqa: E402
import common as M  # noqa: E402

LAYERS = [6, 10, 14]
BANDS = [0, 1, 2, 3, 4, 5, 6, 7, 8]   # 31 Hz … 8 kHz (below 500 Hz only where it's under the fundamental)
STAGES = [((0.0, 0.04), 63), ((0.04, 0.15), 45)]   # window, lowest key it's measured for


def between(x, key, t0, t1):
    """Per octave band (dB of variance, mics averaged): what lies between the partials, and below
    the fundamental (no partials there: all of it is the knock), the whole band."""
    seg = x[int(t0 * 48000):int(t1 * 48000)]
    p = np.mean([AB.between(seg[:, m], 48000, key["f0"], key["B"], AB.partial_freqs(key)) for m in range(2)], 0)
    L = len(seg)
    sp = np.mean([np.abs(np.fft.rfft(seg[:, m] * np.hanning(L))) ** 2 for m in range(2)], 0) / (0.375 * L * L / 2)
    f = np.fft.rfftfreq(L, 1 / 48000)
    hum = (np.abs(f - 50 * np.round(f / 50)) < 2.5) & (f < 1000)
    for b, lo in enumerate(AB.BANDS):
        if 2 * lo <= 0.9 * key["f0"]:
            p[b] = sp[(f >= lo) & (f < 2 * lo) & ~hum].sum()
    return 10 * np.log10(p + 1e-20)


def main():
    sal, render, path = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    model = {k["key"]: k for k in json.load(open(M.TOOLS / "salamander.json"))["keys"]}
    nz = json.load(open(path))
    for k in nz["keys"]:
        key = model[k["key"]]
        adj = [k.get("adjust1", [0.0] * 10), k.get("adjust2", [0.0] * 10)]
        diffs = [[[] for _ in BANDS] for _ in STAGES]
        for layer in LAYERS:
            if k["key"] < min(lo for _, lo in STAGES):
                break
            rec = M.recording(sal, k["key"], layer)
            mod = M.note(render, k["key"], M.layer_vel(layer), 0.3)
            for s, ((t0, t1), lo) in enumerate(STAGES):
                if k["key"] < lo:
                    continue
                r, m = between(rec, key, t0, t1), between(mod, key, t0, t1)
                for i, b in enumerate(BANDS):
                    # bands below 500 Hz only below the fundamental (higher up, partials crowd them)
                    if b < 4 and 2 * AB.BANDS[b] > 0.9 * key["f0"]:
                        continue
                    if np.isfinite(r[b]) and np.isfinite(m[b]) and r[b] > -100:
                        diffs[s][i].append(m[b] - r[b])
        line = []
        for s in range(len(STAGES)):
            for i, b in enumerate(BANDS):
                if diffs[s][i]:
                    # most of the measured error each pass (the stages overlap), within -30…+15 dB in all
                    adj[s][b] = float(np.clip(adj[s][b] - 0.6 * np.median(diffs[s][i]), -30, 15))
            line.append(" ".join("%+5.1f" % (np.median(d) if d else 0) for d in diffs[s]))
        k["adjust1"], k["adjust2"] = adj
        print(f"{k['name']:4s} error knock {line[0]} | slower {line[1]}", flush=True)
    path.write_text(json.dumps(nz))


if __name__ == "__main__":
    main()
