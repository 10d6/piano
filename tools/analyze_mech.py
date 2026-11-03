#!/usr/bin/env python3
"""Measure the piano's mechanical noises: key release and the sustain pedal.

The Salamander set has a release noise for each of the 88 keys (rel1…rel88:
the key coming up, its damper landing) and two takes each of the pedal going
down (pedalD1/2) and up (pedalU1/2). For each, per mic, in the strike noise's
octave bands (tools/analyze_noise.py), every 10 ms:

  - an envelope: the total power per frame, relative to its loudest frame;
  - each band's level, as the power that a noise shaped by that envelope
    needs to carry the band's measured energy.

Pressing the pedal lifts every damper, and the lift sets the free strings
ringing: most of pedalD's six seconds is that ring (the bass strings' low
partials, mostly), not a noise. The engine's sympathetic string bank makes the
ring, so only the thump above it is measured as a noise, and the ring's band
levels (0.3-1.0 s) are kept as the target for the strings the lift excites.

    python3 -I tools/analyze_mech.py <Salamander dir> tools/salamander_mech.json
"""
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

BANDS = 31.25 * 2.0 ** np.arange(10)   # the strike noise's bands
N = 1024
HOP = 480                               # 10 ms frames
FLOOR_DB = -60                          # envelope frames kept down to this, relative to the peak


def band_power(x, sr):
    """Band variance per frame: [frames, mics, bands], and the frame times."""
    win = np.hanning(N)
    freqs = np.fft.rfftfreq(N, 1 / sr)
    pad = np.concatenate([np.zeros((N // 2, x.shape[1])), x, np.zeros((N, x.shape[1]))])
    starts = np.arange(0, len(pad) - N, HOP)
    out = np.zeros((len(starts), x.shape[1], len(BANDS)))
    sel = [(freqs >= lo) & (freqs < 2 * lo) for lo in BANDS]
    for i, s in enumerate(starts):
        for m in range(x.shape[1]):
            spec = np.abs(np.fft.rfft(pad[s:s + N, m] * win)) ** 2 / (0.1875 * N * N)
            out[i, m] = [spec[b].sum() for b in sel]
    return out, starts / sr


def load(path):
    """The file as the sampler plays it, from its first sample."""
    x, sr = sf.read(path, dtype="float64", always_2d=True)
    assert sr == 48000
    return x, sr


def band_var(x, sr, t0=0.0, t1=None):
    """Band variance per mic over t0…t1 from one long spectrum (fine enough for the lowest band,
    which a short frame can't resolve), leaving out the mains hum (50 Hz and its harmonics),
    which is the recording room's, not the piano's: [mics, bands]."""
    seg = x[int(t0 * sr):(len(x) if t1 is None else int(t1 * sr))]
    freqs = np.fft.rfftfreq(len(seg), 1 / sr)
    hum = np.zeros(len(freqs), bool)
    for h in range(50, 1001, 50):
        hum |= np.abs(freqs - h) < 1.0
    out = np.zeros((x.shape[1], len(BANDS)))
    for m in range(x.shape[1]):
        spec = 2 * np.abs(np.fft.rfft(seg[:, m])) ** 2 / len(seg) ** 2   # variance per bin
        spec[hum] = 0
        out[m] = [spec[(freqs >= lo) & (freqs < 2 * lo)].sum() for lo in BANDS]
    return out


def burst(v, var, n):
    """Envelope (dB per frame, peak 0) from the frames' total power v, and the band levels (dB)
    a noise with that envelope needs to carry the band variance var measured over n samples."""
    tot = v.sum(axis=(1, 2))
    env = tot / tot.max()
    db = 10 * np.log10(np.maximum(env, 1e-12))
    last = int(np.nonzero(db > FLOOR_DB)[0].max()) + 1
    env, db = env[:last], db[:last]
    levels = 10 * np.log10(np.maximum(var * n / (env.sum() * HOP), 1e-16))   # [mics, bands]
    return db, levels


def main():
    root, out_path = Path(sys.argv[1]), sys.argv[2]
    smp = root / "Samples"
    result = {"bands": BANDS.tolist(), "frame_s": HOP / 48000, "release": [], "pedal_down": [], "pedal_up": []}

    for k in range(1, 89):
        x, sr = load(smp / f"rel{k}.flac")
        env, lv = burst(band_power(x, sr)[0], band_var(x, sr), len(x))
        result["release"].append({"key": 20 + k, "env": np.round(env, 1).tolist(), "levels": np.round(lv, 2).tolist()})
        print(f"rel{k:<3d} {len(env):3d} frames, levels L", " ".join("%5.0f" % v for v in lv[0]), flush=True)

    rings = []
    for take in (1, 2):
        x, sr = load(smp / f"pedalD{take}.flac")
        v, t = band_power(x, sr)
        ring = band_var(x, sr, 0.3, 1.0)                           # [mics, bands]
        rings.append(ring)
        early = t < 0.3
        thump = np.maximum(v[early] - ring[None], 0)
        env, lv = burst(thump, np.maximum(band_var(x, sr, 0, 0.3) - ring, 0), int(0.3 * sr))
        result["pedal_down"].append({"env": np.round(env, 1).tolist(), "levels": np.round(lv, 2).tolist()})
        print(f"pedalD{take} thump {len(env)} frames", " ".join("%5.0f" % v for v in lv.mean(0)), flush=True)
        # the ring's decay per band, 0.3 s on: for checking the strings' own decay against it
        sel = t >= 0.3
        dec = []
        for b in range(len(BANDS)):
            d = 10 * np.log10(v[sel, :, b].mean(1) + 1e-16)
            ok = d > -110
            dec.append(float(np.polyfit(t[sel][ok], d[ok], 1)[0]) if ok.sum() > 10 else 0.0)
        result["pedal_down"][-1]["ring_decay"] = np.round(dec, 2).tolist()
        print("   ring", " ".join("%5.0f" % v for v in 10 * np.log10(ring.mean(0) + 1e-16)), " decay", " ".join("%5.1f" % d for d in dec))
    ring = np.mean(rings, axis=0)
    result["ring"] = np.round(10 * np.log10(np.maximum(ring, 1e-16)), 2).tolist()     # [mics, bands], dB of variance

    for take in (1, 2):
        x, sr = load(smp / f"pedalU{take}.flac")
        env, lv = burst(band_power(x, sr)[0], band_var(x, sr), len(x))
        result["pedal_up"].append({"env": np.round(env, 1).tolist(), "levels": np.round(lv, 2).tolist()})
        print(f"pedalU{take} {len(env)} frames", " ".join("%5.0f" % v for v in lv.mean(0)), flush=True)

    Path(out_path).write_text(json.dumps(result))


if __name__ == "__main__":
    main()
