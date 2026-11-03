#!/usr/bin/env python3
"""Compare the model with the recordings it was measured from.

Renders single notes (dry: no room) at recorded keys and
velocity layers, and compares them with the Salamander files:

  - level over time in octave bands (dB error, after the first 30 ms);
  - spectrograms side by side (compare.png).

    python3 -I tools/compare.py <Salamander dir> <piano_render> <out dir>
"""
import json
import subprocess
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import soundfile as sf

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
VEL_RANGES = [(1, 26), (27, 34), (35, 36), (37, 43), (44, 46), (47, 50), (51, 56), (57, 64),
              (65, 72), (73, 80), (81, 88), (89, 96), (97, 104), (105, 112), (113, 120), (121, 127)]
TRIM_DB = -10.0
CASES = [(33, 16), (33, 6), (48, 12), (60, 16), (60, 4), (69, 10), (81, 16), (93, 12), (102, 14)]



def name(k):
    return f"{NAMES[k % 12]}{k // 12 - 1}"


def load_rec(root, k, layer):
    x, sr = sf.read(root / "Samples" / f"{name(k)}v{layer}.flac", always_2d=True)
    m = x.mean(1)
    on = max(0, int(np.argmax(np.abs(m) > np.abs(m).max() * 0.05)) - int(0.002 * sr))
    return x[on:], sr


def render(tool, out_dir, k, vel, length):
    # As Recorded velocity map: a layer's middle velocity plays that layer, as in the recordings
    sc = {"patch": "init", "params": {"space.room": 0, "play.velmap": 1}, "length": length,
          "events": [{"t": 0, "on": k, "vel": vel}]}
    p = out_dir / f"sc_{k}_{vel}.json"
    p.write_text(json.dumps(sc))
    w = out_dir / f"model_{k}_{vel}.wav"
    subprocess.run([tool, str(p), "--out", str(w)], check=True, capture_output=True)
    y, sr = sf.read(w, always_2d=True)
    m = y.mean(1)
    on = max(0, int(np.argmax(np.abs(m) > np.abs(m).max() * 0.05)) - int(0.002 * sr))
    return y[on:] * 10 ** (-TRIM_DB / 20), sr


def band_levels(x, sr, hop=0.02):
    m = x.mean(1)
    n = 4096
    win = np.hanning(n)
    frames = range(0, len(m) - n, int(hop * sr))
    spec = np.array([np.abs(np.fft.rfft(m[i:i + n] * win)) ** 2 for i in frames])
    f = np.fft.rfftfreq(n, 1 / sr)
    edges = 31.25 * 2 ** np.arange(0, 10)   # octave bands 31 Hz … 16 kHz
    out = []
    for lo in edges:
        sel = (f >= lo) & (f < 2 * lo)
        out.append(10 * np.log10(spec[:, sel].sum(1) + 1e-14))
    return np.array(out), edges


def main():
    global CASES
    root, tool, out_dir = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    if len(sys.argv) > 4:   # e.g. "45:16,45:6,60:16,60:4"
        CASES = [tuple(int(v) for v in c.split(":")) for c in sys.argv[4].split(",")]
    out_dir.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(len(CASES), 2, figsize=(12, 2.2 * len(CASES)))
    print(f"{'note':6s}{'layer':>6s}{'vel':>5s}  band-level error (dB, median |err| where the recording is within 50 dB of its peak and 10 dB above its noise)")
    for row, (k, layer) in enumerate(CASES):
        lo, hi = VEL_RANGES[layer - 1]
        vel = (lo + hi) // 2
        rec, sr = load_rec(root, k, layer)
        length = min(len(rec) / sr, 8.0)
        rec = rec[: int(length * sr)]
        mod, _ = render(tool, out_dir, k, vel, length)
        mod = mod[: len(rec)]
        br, edges = band_levels(rec, sr)
        bm, _ = band_levels(mod, sr)
        t0 = int(0.03 / 0.02)
        floor = np.median(br[:, -25:], axis=1, keepdims=True)      # the recording's own noise, per band
        mask = (br[:, t0:] > br.max() - 50) & (br[:, t0:] > floor + 10)
        err = np.abs(bm[:, t0:] - br[:, t0:])[mask]
        early = np.abs(bm[:, t0:t0 + 25] - br[:, t0:t0 + 25])[mask[:, :25]]
        print(f"{name(k):6s}{layer:6d}{vel:5d}  all {np.median(err):5.1f}   first 0.5 s {np.median(early):5.1f}")
        for col, (x, title) in enumerate(((rec, "recording"), (mod, "model"))):
            ax = axes[row, col]
            ax.specgram(x.mean(1) + 1e-7 * np.random.default_rng(0).standard_normal(len(x)), NFFT=2048, Fs=sr, noverlap=1536, cmap="magma", vmin=-140, vmax=-20, scale="dB")
            ax.set_ylim(0, min(12000, 40 * 440 * 2 ** ((k - 69) / 12)))
            ax.set_title(f"{name(k)} layer {layer} — {title}", fontsize=9)
            ax.tick_params(labelsize=7)
    fig.tight_layout()
    fig.savefig(out_dir / "compare.png", dpi=80)


if __name__ == "__main__":
    main()
