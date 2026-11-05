#!/usr/bin/env python3
"""The strike as recorded: what each key's recording holds between its partials in its first
0.3 s (the hammer, the action, the soundboard's response), kept as a waveform per microphone.

The strike was played as octave bands of random noise. But the recordings' strike is not
noise: it is the same response every time a key is struck. Between neighbouring velocity
layers its waveform correlates 0.9-0.99 from D#2 up (0.6-0.9 between layers 8 and 16), and
between the partials it is tone-like (spectral flatness ~0.3, where noise is ~0.55). Played as
noise, whatever level a band got wrong was heard as hiss.

So each measured key keeps one response: its velocity-100 recording (layer 13, what the page's
on-screen keyboard plays) with every partial notched out. Velocity changes it in two ways:

  - its brightness, as the felt does: a soft strike's knock has 15-30 dB less at 2-4 kHz than a
    velocity-100 one, a loud one 5-14 dB more. A first-order high shelf follows that within
    1-3 dB (in octave bands, 125 Hz-8 kHz, 0-40 ms; no shelf: 3-11 dB): one corner frequency per
    key, and a gain per layer, smoothed across the layers and kept within -24…+18 dB (the
    softest layers' top bands are the recordings' floor);
  - its level, measured per layer and mic in two windows (the knock, 0-40 ms, and its slower
    part, 40-150 ms), after the shelf, smoothed across the layers.

The finer changes of its spectrum with velocity are not kept (they would need a response per
layer).

  - Partials notched: every partial and cluster component the model plays, then the stiff-string
    series up to 22 kHz, ±max(16 Hz, 4 % of f0, the modes' spread), raised-cosine edges, and the
    mains hum below 1 kHz; zero-phase, over one long FFT, so nothing between them is touched.
  - Faded out from 0.15 s to 0.30 s: after that the body's ring (analyze_body.py) carries on.
  - Keys from A2 up. Lower, the partials are too dense to leave much between them (30-47 % of the
    spectrum is notched at A0-F#2) and the strike is masked by them: there, it made no audible
    difference (tools/measure/perceptual.py) or was a little worse, so those keys keep the band
    noise (analyze_noise.py).
  - Where the strike starts in the engine's time: the recordings and the renders are aligned to
    their attack alike (2 ms before the mono signal first crosses 5 % of its peak), so the
    response starts where that point falls in the engine's own render of the key at layer 13
    with the strike off. This needs a build (piano_render).

    python3 -I tools/analyze_strike.py ../salamander build/piano_render tools/salamander.json \\
        tools/salamander_strike.npz

The response is an excerpt of the Salamander recordings (CC BY 3.0, Alexander Holm): the
attribution in README covers it.
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "measure"))

SR = 48000
REF_LAYER = 13           # 1-based: velocity 89-104, the on-screen keyboard's 100
LENGTH = 0.30
FADE0 = 0.15
FIRST_KEY = 45           # A2
WINDOWS = ((0.0, 0.04), (0.04, 0.15))


def partial_freqs(key):
    """Every partial (and cluster component) the model plays, then the stiff-string series on up
    to 22 kHz; and the notch half-width (Hz)."""
    fs, spread = [], 0.0
    for p in key["partials"]:
        fs.append(p["f"])
        for c in (p.get("cluster") or {}).get("cents", []):
            fs.append(p["f"] * 2 ** (c / 1200))
        for m in p.get("modes") or []:
            spread = max(spread, abs(m.get("df", 0.0)))
    f0, B = key["f0"], key["B"]
    n = len(key["partials"]) + 1
    while True:
        f = n * f0 * np.sqrt(1 + B * n * n)
        if f > 22000:
            break
        fs.append(f)
        n += 1
    width = max(16.0, 0.04 * f0, 1.5 * spread + 8.0)
    return np.array(fs), width


def notch_mask(n, fs, width):
    """A zero-phase gain per FFT bin: 0 at the partials, 1 between, raised-cosine edges, and the
    50 Hz hum (below 1 kHz) out too."""
    f = np.fft.rfftfreq(n, 1 / SR)
    g = np.ones_like(f)
    edge = 0.5 * width
    for fc in fs:
        d = np.abs(f - fc)
        g *= np.where(d < width, 0.0, np.where(d < width + edge, 0.5 - 0.5 * np.cos(np.pi * (d - width) / edge), 1.0))
    g[(np.abs(f - 50 * np.round(f / 50)) < 2.5) & (f < 1000)] = 0
    return g


def between(x, fs, width, tmax=0.6):
    """x (frames, mics) with its partials notched out, its first tmax s."""
    L = int(tmax * SR)
    seg = np.zeros((L, x.shape[1]))
    m = min(L, len(x))
    seg[:m] = x[:m]
    pad = 1 << int(np.ceil(np.log2(4 * L)))
    g = notch_mask(pad, fs, width)
    out = np.empty_like(seg)
    o = pad // 4     # room ahead of t = 0 for the notches' symmetric ringing
    for ch in range(seg.shape[1]):
        buf = np.zeros(pad)
        buf[o:o + L] = seg[:, ch]
        out[:, ch] = np.fft.irfft(np.fft.rfft(buf) * g, pad)[o:o + L]
    return out


SHELF_BANDS = np.array([125.0 * 2 ** i for i in range(7)])     # to 8 kHz
SHELF_FC = np.geomspace(250, 8000, 31)
SHELF_G = np.arange(-36, 24.1, 0.5)
SHELF_RANGE = (-24.0, 18.0)


def shelf_coefs(fc, gain_db, sr=SR):
    """A first-order high shelf (gain 1 below fc, gain_db above): b0, b1, a1, as the engine has it."""
    g, K = 10 ** (gain_db / 20), np.tan(np.pi * fc / sr)
    return (g + K) / (1 + K), (K - g) / (1 + K), (K - 1) / (1 + K)


def shelf(x, fc, gain_db):
    import scipy.signal as sg
    b0, b1, a1 = shelf_coefs(fc, gain_db)
    return sg.lfilter([b0, b1], [1, a1], x, axis=0)


def shelf_db(fc, gain_db):
    """The shelf's response at the bands' centres (dB)."""
    f = SHELF_BANDS * 1.41
    x, g = (f / fc) ** 2, 10 ** (gain_db / 20)
    return 10 * np.log10((1 + g * g * x) / (1 + x))


def band_levels(r, t0=0.0, t1=0.04):
    seg = r[int(t0 * SR):int(t1 * SR)]
    n = 1 << 14
    sp = (np.abs(np.fft.rfft(seg * np.hanning(len(seg))[:, None], n, axis=0)) ** 2).mean(1)
    f = np.fft.rfftfreq(n, 1 / SR)
    return np.array([10 * np.log10(sp[(f >= lo) & (f < 2 * lo)].mean() + 1e-30) for lo in SHELF_BANDS])


def fit_shelf(D):
    """One corner frequency for the key and a gain per layer, for D (layers, bands): each layer's
    band levels against the reference layer's, up to a level."""
    best = None
    for fc in SHELF_FC:
        S = np.array([shelf_db(fc, g) for g in SHELF_G])            # (gains, bands)
        tot, gains = 0.0, []
        for d in D:
            e = d[None, :] - S
            e = np.sqrt(np.mean((e - e.mean(1, keepdims=True)) ** 2, 1))
            i = int(np.argmin(e))
            tot += e[i]
            gains.append(SHELF_G[i])
        if best is None or tot < best[0]:
            best = (tot, fc, np.array(gains))
    return best[1], best[2]


def fade(r, t0=FADE0, t1=LENGTH):
    t = np.arange(len(r)) / SR
    w = np.where(t < t0, 1.0, 0.5 + 0.5 * np.cos(np.pi * np.clip((t - t0) / (t1 - t0), 0, 1)))
    return r * w[:, None]


def residual(rec, fs, width):
    """The recording's strike: partials notched out, faded out over FADE0-LENGTH."""
    return fade(between(rec, fs, width, LENGTH + 0.05)[: int(LENGTH * SR)])


def window_levels(r):
    """dB (mean power) per mic in each window."""
    return np.array([[10 * np.log10(np.mean(r[int(a * SR):int(b * SR), ch] ** 2) + 1e-30) for a, b in WINDOWS]
                     for ch in range(r.shape[1])])


def engine_start(tool, key):
    """Where the recordings' alignment point falls in the engine's render (samples at SR). It can be
    before the render's start (the engine's partials rise within the first 2 ms): then negative,
    and the response's first samples are skipped."""
    import common as M
    y, sr = M.render(tool, [{"t": 0, "on": key, "vel": M.layer_vel(REF_LAYER)}], 0.5,
                     {"play.velmap": 1, "tone.hammer": -40, "tone.body": -40, "string.resonance": -40}, onset=False)
    assert sr == SR
    m = y.mean(1)
    return int(np.argmax(np.abs(m) > np.abs(m).max() * 0.05)) - int(0.002 * SR)


def main():
    import analyze_noise as AN
    import compare as C
    sal, tool, model_path, out = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
    model = json.load(open(model_path))
    keys, data, scale, levels, gain, start, fcs, shelves = [], [], [], [], [], [], [], []
    for k in model["keys"]:
        if k["key"] < FIRST_KEY:
            continue
        fs, width = partial_freqs(k)
        res = []
        for layer in range(1, 17):
            rec, _ = C.load_rec(sal, k["key"], layer)
            res.append(residual(rec, fs, width))
        ref = res[REF_LAYER - 1]
        # brightness: the shelf per layer, smoothed across the layers
        bl = np.array([band_levels(r) for r in res])
        fc, g = fit_shelf(bl - bl[REF_LAYER - 1])
        g = np.clip(AN.smooth_layers(np.clip(g, *SHELF_RANGE)), *SHELF_RANGE)
        g = g - g[REF_LAYER - 1]
        # level: the recording's per layer, mic and window, smoothed; and the gain that takes the
        # shelved response there
        lv = np.array([window_levels(r) for r in res])                  # (16, mic, window)
        for mic in range(2):
            for w in range(2):
                lv[:, mic, w] = AN.smooth_layers(lv[:, mic, w])
        gn = np.array([lv[i] - window_levels(shelf(ref, fc, g[i])) for i in range(16)])
        pk = float(np.abs(ref).max()) or 1.0
        keys.append(k["key"])
        data.append(np.round(ref.T / pk * 32767).astype(np.int16))
        scale.append(pk / 32767)
        levels.append(lv)
        gain.append(gn)
        fcs.append(fc)
        shelves.append(g)
        start.append(engine_start(tool, k["key"]))
        print(f"{k['name']}: start {start[-1] / SR * 1000:+.1f} ms, shelf {fc:.0f} Hz, layer 1 {g[0]:+.0f} dB, "
              f"layer 16 {g[15]:+.0f} dB; level layer 1 {lv[0].mean() - lv[REF_LAYER - 1].mean():+.1f} dB, "
              f"layer 16 {lv[15].mean() - lv[REF_LAYER - 1].mean():+.1f} dB", flush=True)
    np.savez_compressed(out, keys=np.array(keys), data=np.array(data), scale=np.array(scale),
                        levels=np.array(levels), gain=np.array(gain), fc=np.array(fcs), shelf=np.array(shelves),
                        start=np.array(start), rate=SR)


if __name__ == "__main__":
    main()
