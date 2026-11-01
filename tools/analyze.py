#!/usr/bin/env python3
"""Measure the Salamander Grand Piano V3 recordings and write the model data.

For every sampled key (minor thirds from A0) this measures, from the
recordings themselves:

  - the fundamental and the string's stiffness (inharmonicity B), fitted to
    the partial frequencies f_n = n f0 sqrt(1 + B n^2);
  - for each partial: the prompt and aftersound decay rates (two-stage decay),
    the knee between them, and the beat rate and depth of the aftersound
    (from the 2-3 strings of a unison not being exactly in tune);
  - for each of the 16 velocity layers and each microphone: every partial's
    starting level, and the phase between the two mics.

    python3 -I tools/analyze.py <Salamander dir> <out.json>

The JSON is turned into src/PianoData.h by tools/make_header.py.
"""
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
LAYERS = 16
VEL_RANGES = [(1, 26), (27, 34), (35, 36), (37, 43), (44, 46), (47, 50), (51, 56), (57, 64),
              (65, 72), (73, 80), (81, 88), (89, 96), (97, 104), (105, 112), (113, 120), (121, 127)]
MAX_PARTIALS = 96
FMAX = 20000.0
HOP = 0.005          # seconds between envelope frames


def note_name(k):
    return f"{NAMES[k % 12]}{k // 12 - 1}"


def sampled_keys():
    return list(range(21, 109, 3))   # A0, C1, D#1 ... C8


def load(path):
    x, sr = sf.read(path, dtype="float64", always_2d=True)
    assert sr == 48000
    m = x.mean(1)
    pk = np.abs(m).max()
    on = int(np.argmax(np.abs(m) > pk * 0.05))
    on = max(0, on - int(0.002 * sr))          # 2 ms before the attack crosses 5% of peak
    return x[on:], sr


def spectrum_peaks(sig, sr, f0n, keyidx):
    """Fit f0 and B from the peaks of one long spectrum."""
    start = int(0.05 * sr)
    length = min(len(sig) - start, int(3.0 * sr))
    seg = sig[start:start + length] * np.hanning(length)
    nfft = 1 << 21
    spec = np.abs(np.fft.rfft(seg, nfft))
    freqs = np.fft.rfftfreq(nfft, 1 / sr)
    db = 20 * np.log10(spec + 1e-12)
    # local noise floor: a running median would be ideal; a coarse percentile is enough
    floor = np.percentile(db, 50)

    f0, B = f0n, 1e-4 * 2 ** ((keyidx - 21) / 24) * 0.5   # rough starting stiffness
    found = []
    nmax = MAX_PARTIALS
    for it in range(4):
        found = []
        for n in range(1, nmax + 1):
            fp = n * f0 * np.sqrt(1 + B * n * n)
            if fp > FMAX:
                break
            w = max(0.06 * f0, 1.5)   # search ± 6% of f0
            lo, hi = np.searchsorted(freqs, [fp - w, fp + w])
            if hi <= lo + 2:
                continue
            i = lo + int(np.argmax(spec[lo:hi]))
            if db[i] < floor + 25:
                continue
            # parabolic interpolation on the dB peak
            if 0 < i < len(db) - 1:
                a, b, c = db[i - 1], db[i], db[i + 1]
                p = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0
            else:
                p = 0
            found.append((n, freqs[i] + p * (freqs[1] - freqs[0]), db[i]))
        if len(found) < 2:
            break
        n = np.array([f[0] for f in found], float)
        fr = np.array([f[1] for f in found])
        wts = 10 ** ((np.array([f[2] for f in found]) - max(f[2] for f in found)) / 40)
        y = (fr / n) ** 2
        A = np.stack([np.ones_like(n), n * n], 1) * wts[:, None]
        sol, *_ = np.linalg.lstsq(A, y * wts, rcond=None)
        f02 = sol[0]
        if f02 <= 0:
            break
        f0 = float(np.sqrt(f02))
        B = float(max(sol[1] / f02, 1e-7))
    return f0, B, [f[0] for f in found]


def envelope(sig, sr, f, T, hop=HOP, tmax=None):
    """Complex amplitude of the component at f: heterodyne, then a triangular
    moving average of length T, read every `hop` seconds."""
    n = len(sig) if tmax is None else min(len(sig), int(tmax * sr))
    t = np.arange(n) / sr
    y = sig[:n] * np.exp(-2j * np.pi * f * t)
    L = max(2, int(T * sr / 2))
    c = np.concatenate([[0], np.cumsum(y)])
    m1 = (c[L:] - c[:-L]) / L
    c2 = np.concatenate([[0], np.cumsum(m1)])
    m2 = (c2[L:] - c2[:-L]) / L                    # centred at index + L - 1
    step = int(hop * sr)
    idx = np.arange(0, len(m2), step)
    times = (idx + L - 1) / sr
    return times, 2 * m2[idx]                     # 2×: amplitude of a real cosine


def hinge_fit(t, e):
    """Fit e(t) [dB] with a continuous two-slope line. Returns
    (level0, slope1, slope2, knee) with slopes in dB/s.

    The fit leans towards the start (weight 1/(t + 0.3 s)): the loud first part
    of a partial is what's heard most, and a long quiet tail mustn't flatten it."""
    w = 1.0 / (t + 0.3)
    sw = np.sqrt(w)
    A1 = np.stack([np.ones_like(t), t], 1)
    sol, *_ = np.linalg.lstsq(A1 * sw[:, None], e * sw, rcond=None)
    sse1 = float(np.sum(w * (A1 @ sol - e) ** 2))
    single = (sol[0], sol[1], sol[1], t[-1])
    if len(t) < 12:
        return single
    best = None
    span = t[-1] - t[0]
    for tk in np.concatenate([t[0] + np.linspace(0.02, 0.3, 15) * span ** 0 * np.minimum(1, span),
                              np.linspace(t[0] + 0.1 * span, t[0] + 0.8 * span, 40)]):
        if not (t[0] + 0.015 < tk < t[-1] - 0.05):
            continue
        A = np.stack([np.ones_like(t), t, np.maximum(0, t - tk)], 1)
        sol2, *_ = np.linalg.lstsq(A * sw[:, None], e * sw, rcond=None)
        sse = float(np.sum(w * (A @ sol2 - e) ** 2))
        if best is None or sse < best[0]:
            best = (sse, sol2, tk)
    if best is None:
        return single
    sse, s2v, tk = best
    s1, s2 = s2v[1], s2v[1] + s2v[2]
    # keep the knee if it is a real prompt → aftersound bend and it helps
    if s1 < s2 - 1.0 and sse < 0.75 * sse1 and s2 <= 0.5:
        l0 = s2v[0]
        # the aftersound line again, from the tail alone and unweighted, so the weighting that
        # gets the start right can't pull the long tail off; then the knee where the lines meet
        tail = t > tk
        if tail.sum() >= 12:
            s2b, c2 = np.polyfit(t[tail], e[tail], 1)
            drop = c2 - l0
            if s2b <= 0.5 and s1 < s2b - 1.0 and drop < 0:
                return (l0, s1, min(s2b, -0.05), drop / (s1 - s2b))
        return (l0, s1, min(s2, -0.05), tk)
    return single


def beat_of(t, resid):
    """Dominant slow periodicity (0.15 … 8 Hz) of the envelope residual, and its
    peak-to-peak size in dB."""
    if len(t) < 64:
        return 0.0, 0.0
    r = (resid - resid.mean()) * np.hanning(len(resid))
    nfft = 1 << 15
    sp = np.abs(np.fft.rfft(r, nfft))
    fr = np.fft.rfftfreq(nfft, t[1] - t[0])
    band = (fr > 0.25) & (fr < 8.0)
    if not band.any():
        return 0.0, 0.0
    i = np.argmax(np.where(band, sp, 0))
    # require the peak to stand out from the band's median
    if sp[i] < 4 * np.median(sp[band]):
        return 0.0, 0.0
    ptp = float(min(np.percentile(resid, 95) - np.percentile(resid, 5), 12.0))
    return float(fr[i]), ptp


def analyze_key(root, k):
    name = note_name(k)
    f0n = 440 * 2 ** ((k - 69) / 12)
    layers = [load(root / "Samples" / f"{name}v{v}.flac") for v in range(1, LAYERS + 1)]
    sr = layers[0][1]

    # f0 and B from the loudest four layers, summed
    L = min(len(x) for x, _ in layers[-4:])
    mono_loud = sum(x[:L].mean(1) for x, _ in layers[-4:])
    f0, B, found = spectrum_peaks(mono_loud, sr, f0n, k)
    cents = 1200 * np.log2(f0 / f0n)

    T = float(np.clip(5.0 / f0, 0.012, 0.2))       # envelope window: partials stay apart
    partials = []
    for n in range(1, MAX_PARTIALS + 1):
        fn = n * f0 * np.sqrt(1 + B * n * n)
        if fn > FMAX:
            break
        partials.append((n, fn))

    result = {"key": k, "name": name, "f0": f0, "cents": cents, "B": B, "window": T, "partials": []}

    # decays: pooled from the loud layers (mono), where the partials sit highest above the noise
    decay_layers = [8, 12, 14, 16]
    for n, fn in partials:
        fits = []
        for v in decay_layers:
            x, _ = layers[v - 1]
            t, z = envelope(x.mean(1), sr, fn, T)
            e = 20 * np.log10(np.abs(z) + 1e-12)
            floor = np.median(e[-max(20, len(e) // 20):])
            peak_i = int(np.argmax(e[: max(3, int(0.3 / HOP))]))
            if e[peak_i] < floor + 20:
                continue
            start = max(peak_i, int((T + 0.01) / HOP))
            above = np.nonzero(e[start:] < floor + 10)[0]
            end = start + (above[0] if len(above) else len(e) - start)
            if end - start < 20:
                continue
            tt, ee = t[start:end], e[start:end]
            l0, s1, s2, tk = hinge_fit(tt, ee)
            fitline = l0 + s1 * tt + (s2 - s1) * np.maximum(0, tt - tk)
            # a fast transient above the decay line in the first 200 ms (the strike's first
            # burst, before the strings settle): its level at t = 0 relative to the line, and its decay
            x0, s0 = -100.0, min(s1, -60.0)
            early = tt < tt[0] + 0.2
            ex = 10 ** (ee / 20) - 10 ** (fitline / 20)
            sel = early & (ex > 0.25 * 10 ** (ee / 20))
            if sel.sum() >= 4 and sel[:3].any():
                xs = np.nonzero(sel)[0]
                xs = xs[: int(np.argmax(np.diff(np.concatenate([xs, [10 ** 9]])) > 2)) + 1]   # first run only
                if len(xs) >= 4:
                    b, a = np.polyfit(tt[xs], 20 * np.log10(ex[xs]), 1)
                    if b < min(s1, -40.0) - 20 and a - l0 > -20:
                        x0, s0 = float(min(a - l0, 30.0)), float(max(b, -2000.0))
            after = tt > tk if s1 != s2 else np.ones_like(tt, bool)
            bf, bdb = beat_of(tt[after], (ee - fitline)[after])
            # refine frequency from the phase slope over the first second of the loudest layer
            ph = np.unwrap(np.angle(z[start:min(end, start + int(1.0 / HOP))]))
            df = np.polyfit(t[start:start + len(ph)], ph, 1)[0] / (2 * np.pi) if len(ph) > 10 else 0.0
            fits.append({"v": v, "l0": l0, "s1": s1, "s2": s2, "tk": tk, "x0": x0, "s0": s0, "beat": bf, "beat_db": bdb,
                         "df": df, "span": tt[-1] - tt[0], "w": e[peak_i] - floor})
        if not fits:
            continue
        # the decay shape comes whole from one layer, the one with the most range above its
        # noise: medians of slopes and knees taken from different layers don't make one curve
        best = max(fits, key=lambda f: f["w"])
        s1, s2, tk = best["s1"], best["s2"], best["tk"]
        drop = (s1 - s2) * tk if s1 != s2 else 0.0
        if s1 == s2:
            tk = 0.0
        x0, s0 = best["x0"], best["s0"]
        beats = [f for f in fits if f["beat"] > 0]
        beat = float(np.median([f["beat"] for f in beats])) if beats else 0.0
        beat_db = float(np.median([f["beat_db"] for f in beats])) if beats else 0.0
        df = float(np.median([f["df"] for f in fits]))
        if abs(df) > 0.002 * fn + 0.3:   # the phase slope locked onto something else: keep the fitted frequency
            df = 0.0
        result["partials"].append({"n": n, "f": fn + df, "s1": s1, "s2": s2, "tk": tk, "drop": drop, "x0": x0, "s0": s0,
                                   "beat": beat, "beat_db": beat_db})

    # starting level and phase per layer and mic: the complex amplitude over an early window,
    # brought back to t = 0 along the partial's own two-stage decay
    for p in result["partials"]:
        aft = 10 ** (p["drop"] / 20) if p["drop"] < -0.01 else 1.0
        X = 10 ** (p["x0"] / 20)
        def shape_db(tt):   # the decay line (1 at t = 0) plus the transient
            return 20 * np.log10((1 - aft) * 10 ** (p["s1"] * tt / 20) + aft * 10 ** (p["s2"] * tt / 20)
                                 + X * 10 ** (p["s0"] * tt / 20))
        lv, ph = [], []
        for v in range(1, LAYERS + 1):
            x, _ = layers[v - 1]
            lvv, phv = [], []
            for ch in range(2):
                t, z = envelope(x[:, ch], sr, p["f"], T, tmax=0.5)
                t0 = max(0.75 * T, 0.015)
                sel = (t >= t0) & (t <= t0 + 0.04)
                if not sel.any():
                    sel = slice(0, 3)
                zz, tt = z[sel], t[sel]
                lvv.append(float(np.mean(20 * np.log10(np.abs(zz) + 1e-12) - shape_db(tt))))
                phv.append(float(np.angle(np.mean(zz))))
            lv.append(lvv)
            ph.append(phv)
        p["levels"] = lv            # [layer][mic] dB of the decay line at the strike (the transient adds x0 dB on top)
        p["phases"] = ph            # [layer][mic] phase at the strike (rad), cosine reference
        p["phase"] = float(np.angle(np.mean(np.exp(1j * (np.array(ph)[8:, 1] - np.array(ph)[8:, 0])))))
    return result


def main():
    root = Path(sys.argv[1])
    out = Path(sys.argv[2])
    keys = sampled_keys()
    if len(sys.argv) > 3:
        keys = [int(a) for a in sys.argv[3].split(",")]
    data = {"source": "Salamander Grand Piano V3 by Alexander Holm (CC BY 3.0)", "layers": LAYERS,
            "velRanges": VEL_RANGES, "keys": []}
    for k in keys:
        r = analyze_key(root, k)
        print(f"{r['name']:4s} f0 {r['f0']:8.2f} Hz  {r['cents']:+6.1f} c  B {r['B']:.2e}  partials {len(r['partials'])}",
              flush=True)
        data["keys"].append(r)
    out.write_text(json.dumps(data))


if __name__ == "__main__":
    main()
