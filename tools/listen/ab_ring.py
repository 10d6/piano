#!/usr/bin/env python3
"""An A/B of what rings on between the partials after the strike (the body's ring).

For each note (key, layer), aligned and at the recordings' level:

  current    the build as it is: the ring as band noise (Body), and the free strings (Resonance)
  off        both off (Body and Resonance −40 dB): the partials and the strike only
  strings    Body off, Resonance as it is: only what the model's free strings give
  recorded   'off' plus the recording's own ring: the recording with every partial notched out
             (as tools/analyze_strike.py does), faded in over 0.15-0.30 s as the recorded strike
             fades out, to the clip's end
  recording  the Salamander note

and measures each between the partials per octave band at 0.3-1, 1-2 and 2-3 s against the
recording, with the share of that energy in narrow peaks (tones) rather than spread (noise).

    python3 -I tools/listen/ab_ring.py --out ring-out [--keys 72,81,84,93,99] [--layers 8,13]

Clips are excerpts of the Salamander Grand Piano V3 (CC BY 3.0, Alexander Holm): keep them out of
the repository.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.ndimage import median_filter

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "measure"))
sys.path.insert(0, str(HERE.parent))
import common as M          # noqa: E402
import perceptual as P      # noqa: E402
import analyze_strike as AS  # noqa: E402

SR = 48000
LENGTH = 3.5
WINDOWS = ((0.3, 1.0), (1.0, 2.0), (2.0, 3.0))
BANDS = [250 * 2 ** i for i in range(6)]      # 250 Hz .. 8 kHz octaves


def fit(x, n):
    y = np.zeros((n, 2))
    m = min(n, len(x))
    y[:m] = x[:m]
    return y


def ring(rec, fs, width):
    r = AS.between(rec, fs, width, LENGTH + 0.1)[: int(LENGTH * SR)]
    t = np.arange(len(r)) / SR
    w = np.clip((t - AS.FADE0) / (AS.LENGTH - AS.FADE0), 0, 1)
    return r * (0.5 - 0.5 * np.cos(np.pi * w))[:, None]


def measure(x, fs, width):
    """Between the partials: dB per band and window, and the share in peaks (≥ 6 dB over the
    local median)."""
    out = {}
    for a, b in WINDOWS:
        seg = x[int(a * SR):int(b * SR)]
        n = 1 << 17
        sp = (np.abs(np.fft.rfft(seg * np.hanning(len(seg))[:, None], n, axis=0)) ** 2).mean(1)
        f = np.fft.rfftfreq(n, 1 / SR)
        keep = AS.notch_mask(n, fs, width, hum=True) > 0.99
        db = 10 * np.log10(sp + 1e-30)
        med = median_filter(db, size=201)
        lv, tone = [], []
        for lo in BANDS:
            m = keep & (f >= lo) & (f < 2 * lo)
            lv.append(float(10 * np.log10(sp[m].mean() + 1e-30)))
            tone.append(float(sp[m & (db > med + 6)].sum() / max(sp[m].sum(), 1e-30)))
        out[f"{a}-{b}s"] = {"level": lv, "tonal": tone}
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--render", default=str(M.REPO / "build" / "piano_render"))
    p.add_argument("--sal", default=os.environ.get("PIANO_SAL", str(M.REPO.parent / "salamander")))
    p.add_argument("--keys", default="72,81,84,93,99")
    p.add_argument("--layers", default="8,13")
    p.add_argument("--out", default="ring-out")
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    keys = M.model()
    clips, metrics = [], []
    n = int(LENGTH * SR)
    for k in [int(v) for v in a.keys.split(",")]:
        fs, width = AS.partial_freqs(keys[k])
        for layer in [int(v) for v in a.layers.split(",")]:
            name = M.A.note_name(k)
            rec = M.recording(a.sal and Path(a.sal), k, layer)
            off = P.render_note(a.render, k, layer, {"tone.body": -40, "string.resonance": -40})
            vers = {
                "current": fit(P.render_note(a.render, k, layer), n),
                "off": fit(off, n),
                "strings": fit(P.render_note(a.render, k, layer, {"tone.body": -40}), n),
                "recorded": fit(off, n) + fit(ring(rec, fs, width), n),
                "recording": fit(rec, n),
            }
            f = int(0.1 * SR)
            for v, x in vers.items():
                x[-f:] *= np.linspace(1, 0, f)[:, None] ** 2
                fn = f"{name.replace('#', 's')}-L{layer}-{v}.flac"
                sf.write(out / fn, np.clip(x, -1, 1), SR, subtype="PCM_24")
                clips.append({"key": k, "name": name, "layer": layer, "vel": M.layer_vel(layer), "version": v, "file": fn})
            ref = measure(vers["recording"], fs, width)
            for v, x in vers.items():
                m = measure(x, fs, width)
                for w in m:
                    m[w]["vs_rec_db"] = [round(x - r, 1) for x, r in zip(m[w]["level"], ref[w]["level"])]
                metrics.append({"name": name, "layer": layer, "version": v, **m})
            print(f"{name} L{layer} done", flush=True)
    (out / "clips.json").write_text(json.dumps(clips, indent=1))
    (out / "metrics.json").write_text(json.dumps(metrics, indent=1))


if __name__ == "__main__":
    main()
