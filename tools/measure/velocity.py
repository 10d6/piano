#!/usr/bin/env python3
"""Velocity response.

  curve      the model's loudness (dB from the hardest strike) and attack brightness
             (spectral centroid, first 30 ms) at MIDI velocities 10…127, on the default
             Smooth map: steps should be even, with no flat stretches or cliffs.
  vs layers  from soft (layer 3) to loud (layer 15), on the As Recorded map, the change in
             loudness, attack-to-body balance and attack spectrum, recording | model: the model
             should change as much as the recordings do.

    python3 -I tools/measure/velocity.py [--render …] [--sal …]
"""
import numpy as np

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M  # noqa: E402

VELS = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 127]
BANDS = [125, 250, 500, 1000, 2000, 4000, 8000]


def feats(x, sr=48000):
    m = x.mean(1)

    def bands(a, b):
        seg = m[int(a * sr):int(b * sr)]
        sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), 8192)) ** 2
        f = np.fft.rfftfreq(8192, 1 / sr)
        return np.array([10 * np.log10(sp[(f >= lo) & (f < 2 * lo)].sum() + 1e-16) for lo in BANDS])

    a = bands(0, 0.03)
    lvl = 10 * np.log10(np.mean(m[:int(0.3 * sr)] ** 2) + 1e-16)
    early = 10 * np.log10(np.mean(m[:int(0.015 * sr)] ** 2) + 1e-16) - 10 * np.log10(np.mean(m[int(0.015 * sr):int(0.1 * sr)] ** 2) + 1e-16)
    return a - a.max(), lvl, early


def main():
    a = M.args(__doc__)
    print("curve (Smooth map): velocity " + " ".join("%5d" % v for v in VELS))
    for k in (48, 60, 72, 84, 96):
        L, Bc = [], []
        for v in VELS:
            y, sr = M.render(a.render, [{"t": 0, "on": k, "vel": v}], 0.6, trim=False)
            m = y.mean(1)
            L.append(10 * np.log10(np.mean(m[:int(0.3 * sr)] ** 2) + 1e-16))
            seg = m[:int(0.03 * sr)]
            sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), 4096)) ** 2
            f = np.fft.rfftfreq(4096, 1 / sr)
            Bc.append((sp * f).sum() / sp.sum())
        print(f"{M.C.name(k):4s} dB  " + " ".join("%5.1f" % (x - L[-1]) for x in L))
        print("     Hz  " + " ".join("%5.0f" % x for x in Bc))
    print("\nsoft (layer 3) → loud (layer 15), recording | model: loudness dB, attack-vs-body dB, attack spectrum change by band")
    for k in (48, 60, 69, 72, 81, 93):
        res = []
        for src in ("rec", "mod"):
            fs = [feats(M.recording(a.sal, k, l) if src == "rec" else M.note(a.render, k, M.layer_vel(l), 1.0))
                  for l in (3, 15)]
            res.append((fs[1][1] - fs[0][1], fs[1][2] - fs[0][2], fs[1][0] - fs[0][0]))
        (rl, re_, rs), (ml, me, ms) = res
        print(f"{M.C.name(k):4s} {rl:5.1f} | {ml:5.1f}   {re_:+5.1f} | {me:+5.1f}   rec " +
              " ".join("%+5.1f" % v for v in rs) + "   mod " + " ".join("%+5.1f" % v for v in ms))


if __name__ == "__main__":
    main()
