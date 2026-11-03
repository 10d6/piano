#!/usr/bin/env python3
"""What lies between the partials (the strike's knock, its slower part, the body's ring), against
the recordings: per time window and octave band, model − recording in dB, over the measured keys
from A3 up at three velocities.

This is where the top keys went wrong for a long time without the band levels showing it: a band
can carry the right total while the model puts it in the wrong place, flat noise between the
partials (hiss) where the piano has it at the partials. Healthy: mean near 0 and mean |diff|
within ~5 dB in each window up to 0.4 s (the 8-16 kHz band is the hardest; later, the body's ring
is a few dB strong).

    python3 -I tools/measure/between.py [--render …] [--sal …]
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M  # noqa: E402
import analyze_body as AB  # noqa: E402  (tools/, on the path through common)

KEYS = [57, 63, 69, 75, 81, 84, 87, 90, 93, 96, 99, 102, 105]
WINDOWS = [(0.0, 0.04), (0.04, 0.15), (0.15, 0.4), (0.4, 1.0)]
BANDS = [4, 5, 6, 7, 8]          # 500 Hz … 8 kHz


def between(x, key, t0, t1):
    seg = x[int(t0 * 48000):int(t1 * 48000)]
    p = [AB.between(seg[:, m], 48000, key["f0"], key["B"], AB.partial_freqs(key)) for m in range(2)]
    return 10 * np.log10(np.mean(p, 0) + 1e-20)


def main():
    a = M.args(__doc__)
    model = M.model()
    D = np.full((len(KEYS), 3, len(WINDOWS), len(BANDS)), np.nan)
    for i, k in enumerate(KEYS):
        key = model[k]
        for j, layer in enumerate((6, 10, 14)):
            rec = M.recording(a.sal, k, layer)
            mod = M.note(a.render, k, M.layer_vel(layer), 1.0)
            for w, (t0, t1) in enumerate(WINDOWS):
                r, m = between(rec, key, t0, t1), between(mod, key, t0, t1)
                for b, bb in enumerate(BANDS):
                    if r[bb] > -100:
                        D[i, j, w, b] = m[bb] - r[bb]
        print(f"key {k}: " + "  ".join(" ".join("%+4.0f" % np.nanmean(D[i, :, w, b]) for b in range(len(BANDS)))
                                        for w in range(len(WINDOWS))), flush=True)
    print(f"\n{'window':<12} {'mean':>6} {'|mean|':>7}   by band (500 Hz … 8 kHz)")
    for w, (t0, t1) in enumerate(WINDOWS):
        x = D[:, :, w, :]
        print(f"{t0:.2f}-{t1:.2f} s  {np.nanmean(x):+6.1f} {np.nanmean(np.abs(x)):7.1f}   " +
              " ".join("%+5.1f" % np.nanmean(x[:, :, b]) for b in range(len(BANDS))))


if __name__ == "__main__":
    main()
