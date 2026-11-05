#!/usr/bin/env python3
"""An A/B of the strike's noise: is the hiss in the attack the band-noise model?

For each note (key, layer) it makes these versions, aligned and at the recordings' level:

  current    the build as it is (strike noise: random band noise)
  off        the strike noise off (Hammer −40 dB): partials, body ring, nothing else
  residual   'off' plus the recording's own residual: the recording with every partial notched
             out (zero-phase, ±max(16 Hz, 4 % f0, the modes' spread)), its first 0.3 s, faded
             out from 0.15 s. A deterministic, recorded strike in place of the random one
  recording  the Salamander note

and, to hear the strike on its own, 'current' minus 'off' (the model's strike noise) against the
residual (the recording's).

It also measures each version between the partials (the same notches, inverted), per octave
band, over 0-40 ms and 40-150 ms, in dB against the recording, and the spectral flatness there
(tone-like ~0.3, white noise ~0.55).

    python3 -I tools/listen/ab_strike.py --out ab-out [--keys 72,75,87,105] [--layers 8,13]

Writes FLAC clips, clips.json (what each is) and metrics.json into --out. The clips are excerpts
of the Salamander Grand Piano V3 (CC BY 3.0, Alexander Holm): keep them out of the repository.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "measure"))
import common as M      # noqa: E402
import perceptual as P  # noqa: E402
sys.path.insert(0, str(HERE.parent))
import analyze_strike as AS  # noqa: E402

SR = 48000
LENGTH = 2.0
RES_LEN, RES_FADE0 = 0.30, 0.15
BANDS = [125 * 2 ** i for i in range(8)]            # 125 Hz .. 16 kHz octaves


partial_freqs, notch_mask, between = AS.partial_freqs, AS.notch_mask, AS.between


def residual(rec, fs, width):
    return AS.fade(between(rec, fs, width, RES_LEN + 0.05)[: int(RES_LEN * SR)], RES_FADE0, RES_LEN)


def fit(x, n):
    y = np.zeros((n, 2))
    m = min(n, len(x))
    y[:m] = x[:m]
    return y


def clip(x):
    n = int(LENGTH * SR)
    y = fit(x, n)
    f = int(0.1 * SR)
    y[-f:] *= np.linspace(1, 0, f)[:, None] ** 2
    return y


def band_metrics(x, fs, width):
    """Between-the-partials power per band (dB) over 0-40 and 40-150 ms, mics averaged, and the
    spectral flatness there per band."""
    b = between(x, fs, width, 0.2)
    out = {}
    for name, (t0, t1) in {"0-40ms": (0.0, 0.04), "40-150ms": (0.04, 0.15)}.items():
        seg = b[int(t0 * SR):int(t1 * SR)]
        n = 1 << 14
        w = np.hanning(len(seg))[:, None]
        sp = (np.abs(np.fft.rfft(seg * w, n, axis=0)) ** 2).mean(1)
        f = np.fft.rfftfreq(n, 1 / SR)
        g = notch_mask(n, fs, width) > 0.99
        lv, fl = [], []
        for lo in BANDS:
            sel = (f >= lo) & (f < 2 * lo) & g
            if sel.sum() < 4:
                lv.append(None)
                fl.append(None)
                continue
            s = sp[sel] + 1e-30
            lv.append(10 * np.log10(s.mean()))
            fl.append(float(np.exp(np.log(s).mean()) / s.mean()))
        out[name] = {"level": lv, "flatness": fl}
    return out


def flac(path, x):
    sf.write(path, np.clip(x, -1, 1), SR, subtype="PCM_24")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--render", default=str(M.REPO / "build" / "piano_render"))
    p.add_argument("--sal", default=os.environ.get("PIANO_SAL", str(M.REPO.parent / "salamander")))
    p.add_argument("--keys", default="72,75,87,105")
    p.add_argument("--layers", default="8,13")
    p.add_argument("--out", default="ab-out")
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sal = Path(a.sal)
    keys = M.model()
    clips, metrics = [], []
    for k in [int(v) for v in a.keys.split(",")]:
        fs, width = partial_freqs(keys[k])
        for layer in [int(v) for v in a.layers.split(",")]:
            name = M.A.note_name(k)
            rec = M.recording(sal, k, layer)
            cur = P.render_note(a.render, k, layer)
            off = P.render_note(a.render, k, layer, {"tone.hammer": -40})
            res = residual(rec, fs, width)
            n = int(LENGTH * SR)
            vers = {
                "current": clip(cur),
                "off": clip(off),
                "residual": clip(fit(off, n) + fit(res, n)),
                "recording": clip(rec),
                "strike-model": clip(fit(cur, n) - fit(off, n)),
                "strike-recorded": clip(res),
            }
            Nr = P.loudness(vers["recording"])
            for v, x in vers.items():
                fn = f"{name.replace('#', 's')}-L{layer}-{v}.flac"
                flac(out / fn, x)
                d = P.distance(Nr, P.loudness(x)) if not v.startswith("strike") else None
                clips.append({"key": k, "name": name, "layer": layer, "vel": M.layer_vel(layer), "version": v,
                              "file": fn, "distance": d})
            ref = band_metrics(vers["recording"], fs, width)
            for v in ("current", "off", "residual", "recording"):
                bm = band_metrics(vers[v], fs, width)
                for win in bm:
                    bm[win]["vs_rec_db"] = [None if (x is None or r is None) else round(x - r, 1)
                                            for x, r in zip(bm[win]["level"], ref[win]["level"])]
                metrics.append({"name": name, "layer": layer, "version": v, "width_hz": round(width, 1), **bm})
            print(f"{name} L{layer} done", flush=True)
    (out / "clips.json").write_text(json.dumps(clips, indent=1))
    (out / "metrics.json").write_text(json.dumps(metrics, indent=1))


if __name__ == "__main__":
    main()
