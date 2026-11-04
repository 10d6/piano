#!/usr/bin/env python3
"""Measure the Salamander Grand Piano V3 recordings and write the model data.

For every sampled key (minor thirds from A0) this measures, from the
recordings themselves:

  - the fundamental and the string's stiffness (inharmonicity B), fitted to
    the partial frequencies f_n = n f0 sqrt(1 + B n^2);
  - for each partial: the prompt and aftersound decay rates (two-stage decay),
    the knee between them, and the beat rate and depth of the aftersound
    (from the 2-3 strings of a unison not being exactly in tune);
  - each partial's own frequency, where its peak in the first 0.3 s lies off
    the stiff-string line (the treble: refine_partials), and, where a partial
    is a cluster of the unison's strings, the cluster's components (clusters);
  - for each partial: its first burst above the decay line (x0, s0), and the
    decay shape separately for soft, medium and loud strikes;
  - for each of the 16 velocity layers and each microphone: every partial's
    starting level and phase.

docs/MODEL.md describes the methods.

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


def refine_partials(sig, sr, f0, partials):
    """Move each partial to its own peak near the stiff-string prediction, where there is a clear
    one. In the treble the prediction can be 10-30 cents off: early in A6's note, its third
    partial peaks 15 cents flat of it, and the predicted frequency sits in a valley 30 dB below
    the peak. Measured there, a partial's level and decay are read beside it, and its energy is
    counted as noise. The peak is taken from the first 0.3 s, where the string's own partial is
    strongest (some treble partials have a weaker, longer-lasting neighbour further off, which
    a long spectrum would pick instead). Returns the partials and, per partial number, whether
    its frequency was measured."""
    start = int(0.005 * sr)
    length = min(len(sig) - start, int(0.3 * sr))
    seg = sig[start:start + length] * np.hanning(length)
    nfft = 1 << 20
    spec = np.abs(np.fft.rfft(seg, nfft))
    freqs = np.fft.rfftfreq(nfft, 1 / sr)
    db = 20 * np.log10(spec + 1e-12)
    floor = np.percentile(db, 50)
    out, measured = [], {}
    for n, fn in partials:
        w = min(fn * (2 ** (60 / 1200) - 1), 0.3 * f0)    # ±60 cents, and well clear of the neighbours
        lo, hi = np.searchsorted(freqs, [fn - w, fn + w])
        measured[n] = False
        if hi > lo + 4:
            i = lo + int(np.argmax(spec[lo:hi]))
            if db[i] > floor + 25 and db[i] - np.median(db[lo:hi]) > 15 and lo < i < hi - 1:
                a, b, c = db[i - 1], db[i], db[i + 1]
                p = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0
                f = freqs[i] + p * (freqs[1] - freqs[0])
                if f <= FMAX:
                    fn, measured[n] = float(f), True
        out.append((n, fn))
    return out, measured


def clusters(sig, sr, f0, partials, T):
    """In the treble a partial is a cluster: the unison's two or three strings, tens of Hz apart
    (A6's third partial spreads over 50 Hz; D#7's second over 70). One narrow measurement reads
    only part of it, and what it misses would be counted as noise. Per partial, from the first
    0.5 s: up to four components within 20 dB of the strongest (cents from the partial's
    frequency, share of the power), where they spread over at least 4 Hz; and how much more power
    the cluster holds than the envelope measurement (a triangular window of T) takes in, in dB."""
    start = int(0.005 * sr)
    length = min(len(sig) - start, int(0.5 * sr))
    seg = sig[start:start + length] * np.hanning(length)
    nfft = 1 << 21
    X = np.abs(np.fft.rfft(seg, nfft)) ** 2
    freqs = np.fft.rfftfreq(nfft, 1 / sr)
    db = 10 * np.log10(X + 1e-30)
    L = max(2, int(T * sr / 2))
    out = {}
    for n, fn in partials:
        if fn < 1000.0:
            continue
        w = min(fn * (2 ** (70 / 1200) - 1), 0.3 * f0)
        lo, hi = np.searchsorted(freqs, [fn - w, fn + w])
        if hi <= lo + 8:
            continue
        s = db[lo:hi]
        if s.max() < np.percentile(db, 50) + 25:
            continue                                     # no clear partial here
        floor = 10 ** (np.median(s) / 10)
        pk = [i for i in range(1, len(s) - 1) if s[i] > s[i - 1] and s[i] >= s[i + 1] and s[i] > s.max() - 20]
        pk.sort(key=lambda i: -s[i])
        comps = []
        for i in pk:
            f = freqs[lo + i]
            if all(abs(f - g) >= 3.0 for g, _ in comps):
                lobe = (freqs >= f - 1.5) & (freqs <= f + 1.5)
                comps.append((f, float(np.maximum(X[lobe] - floor, 0).sum())))
            if len(comps) == 4:
                break
        if len(comps) < 2 or max(c[0] for c in comps) - min(c[0] for c in comps) < 4.0:
            continue
        tot = sum(c[1] for c in comps)
        # power the cluster holds, against what the envelope's window passes around fn
        band = np.maximum(X[lo:hi] - floor, 0)
        df = freqs[lo:hi] - fn
        x = np.pi * df * L / sr
        H = np.where(np.abs(x) < 1e-9, 1.0, (np.sin(x) / (L * np.sin(np.pi * df / sr) + 1e-30))) ** 4
        captured = float((band * np.minimum(H, 1.0)).sum())
        gain = float(np.clip(10 * np.log10(band.sum() / max(captured, 1e-30)), 0.0, 20.0))
        out[n] = {"cents": [1200 * np.log2(c[0] / fn) for c in comps], "share": [c[1] / tot for c in comps],
                  "gain_db": gain}
    return out


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


# velocity layers (1-based) pooled for the soft, medium and loud decay shapes, and where each
# group sits (0-based layer) for interpolating between them
DECAY_GROUPS = [(2, 4, 6), (8, 10, 12), (14, 15, 16)]
GROUP_CENTERS = [3.0, 9.0, 14.0]


def shape_at(p, layer):
    """The decay shape (s1, s2, drop, x0, s0) for a 0-based layer position, interpolated between the
    soft, medium and loud groups; the engine does the same."""
    shapes = p.get("shapes") or [p] * 3
    c = GROUP_CENTERS
    if layer <= c[0]:
        i, w = 0, 0.0
    elif layer >= c[2]:
        i, w = 1, 1.0
    else:
        i = 0 if layer < c[1] else 1
        w = (layer - c[i]) / (c[i + 1] - c[i])
    a, b = shapes[i], shapes[i + 1]
    return {k: (1 - w) * a[k] + w * b[k] for k in ("s1", "s2", "drop", "x0", "s0")}


def shape_db(sh, tt):
    """The decay line (1 at t = 0) plus the transient, in dB."""
    aft = 10 ** (sh["drop"] / 20) if sh["drop"] < -0.01 else 1.0
    return 20 * np.log10((1 - aft) * 10 ** (sh["s1"] * tt / 20) + aft * 10 ** (sh["s2"] * tt / 20)
                         + 10 ** (sh["x0"] / 20) * 10 ** (sh["s0"] * tt / 20))


def mode_layer(p, layer):
    """The layer whose modes stand for `layer` (the nearest fitted one), and the level shift to it
    per mic (from the partial's measured levels), as tools/make_header.py plays them."""
    fitted = p.get("fit_layers") or [v for v in range(LAYERS) if p["modes"][0]["levels"][v][0] > -190]
    src = min(fitted, key=lambda u: abs(u - layer))
    return src, [p["levels"][layer][c] - p["levels"][src][c] for c in (0, 1)]


def partial_env(p, layer, mic, t):
    """A partial's amplitude envelope at times t (recording time, s), as the model has it: the sum
    of its modes (tools/analyze_modes.py), beating included, where it has them; else its level and
    decay shape. Without the swell."""
    if p.get("modes"):
        src, shift = mode_layer(p, layer)
        z = np.zeros(len(t), complex)
        for m in p["modes"]:
            c = 10 ** ((m["levels"][src][mic] + shift[mic]) / 20) * np.exp(1j * m["phases"][src][mic])
            z += c * np.exp((m["decay"] / (20 / np.log(10)) + 2j * np.pi * m["df"]) * t)
        return np.abs(z)
    return 10 ** ((p["levels"][layer][mic] + shape_db(shape_at(p, layer), t)) / 20)


def smooth_beats(e, t):
    """Average an envelope's power over a window that grows with time (half the time since the
    start, up to 0.4 s): the strike keeps its detail, and later the unison beats (1-3 Hz in the
    treble), which pull a line fitted in dB down into their troughs, are averaged out."""
    p = 10 ** (e / 10)
    c = np.concatenate([[0.0], np.cumsum(p)])
    hop = t[1] - t[0] if len(t) > 1 else HOP
    half = np.clip((0.25 * np.maximum(t, 0) / hop).astype(int), 0, int(0.2 / hop))
    i = np.arange(len(p))
    lo = np.maximum(i - half, 0)
    hi = np.minimum(i + half + 1, len(p))
    return 10 * np.log10((c[hi] - c[lo]) / (hi - lo) + 1e-30)


def fit_curve(tt, ee):
    """A two-stage decay line (hinge_fit) plus a fast transient above it in the first 200 ms
    (the strike's first burst, before the strings settle): (l0, s1, s2, tk, x0, s0), x0 being the
    transient's level at t = 0 relative to the line (-100 = none) and s0 its decay."""
    l0, s1, s2, tk = hinge_fit(tt, ee)
    fitline = l0 + s1 * tt + (s2 - s1) * np.maximum(0, tt - tk)
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
    return l0, s1, s2, tk, x0, s0


def beat_of(t, resid):
    """The unison's beat: the strongest periodicity (0.3 … 8 Hz) of the envelope residual, and its
    peak-to-peak size in dB. The residual's slow curvature (what the two-stage line misses) is
    removed first, or it shows up as a 'beat' at the bottom of the range."""
    if len(t) < 64 or t[-1] - t[0] < 1.0:
        return 0.0, 0.0
    tc = t - t.mean()
    r = resid - np.polyval(np.polyfit(tc, resid, 3), tc)
    nfft = 1 << 15
    sp = np.abs(np.fft.rfft(r * np.hanning(len(r)), nfft))
    fr = np.fft.rfftfreq(nfft, t[1] - t[0])
    band = (fr > max(0.3, 1.5 / (t[-1] - t[0]))) & (fr < 8.0)   # at least 1.5 cycles in view
    if not band.any():
        return 0.0, 0.0
    i = np.argmax(np.where(band, sp, 0))
    if sp[i] < 3 * np.median(sp[band]):
        return 0.0, 0.0
    # its size: a sinusoid at that rate fitted to the residual (peak to trough)
    f = float(fr[i])
    M = np.stack([np.cos(2 * np.pi * f * tc), np.sin(2 * np.pi * f * tc), np.ones_like(tc), tc, tc ** 2, tc ** 3], 1)
    sol, *_ = np.linalg.lstsq(M, resid, rcond=None)
    ptp = float(min(2 * np.hypot(sol[0], sol[1]), 12.0))
    return f, ptp


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
    partials, measured = refine_partials(mono_loud, sr, f0, partials)
    cl = clusters(mono_loud, sr, f0, partials, T)

    result = {"key": k, "name": name, "f0": f0, "cents": cents, "B": B, "window": T, "partials": []}

    # decays: pooled from the loud layers (mono), where the partials sit highest above the noise
    decay_layers = [v for g in DECAY_GROUPS for v in g]
    for n, fn in partials:
        fits, curves = [], []
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
            l0, s1, s2, tk, x0, s0 = fit_curve(tt, ee)
            fitline = l0 + s1 * tt + (s2 - s1) * np.maximum(0, tt - tk)
            after = tt > tk if s1 != s2 else np.ones_like(tt, bool)
            bf, bdb = beat_of(tt[after], (ee - fitline)[after])
            # refine frequency from the phase slope over the first second
            ph = np.unwrap(np.angle(z[start:min(end, start + int(1.0 / HOP))]))
            df = np.polyfit(t[start:start + len(ph)], ph, 1)[0] / (2 * np.pi) if len(ph) > 10 else 0.0
            fits.append({"v": v, "beat": bf, "beat_db": bdb, "df": df, "w": e[peak_i] - floor})
            curves.append((start, end, smooth_beats(e, t), v, t))
        if not fits:
            continue
        # The decay shape, for soft, medium and loud strikes (soft notes sustain relatively longer):
        # per group, one fit to its layers' envelopes averaged (in dB, each shifted onto the group's
        # loudest over the time they share), which smooths the beats.
        shapes = []
        for g in DECAY_GROUPS:
            cs = [c for c in curves if c[3] in g]
            if not cs:
                shapes.append(None)
                continue
            rs, re_, rcurve, _, tref = max(cs, key=lambda c: c[1] - c[0])
            acc = np.zeros(len(rcurve))
            cnt = np.zeros(len(rcurve))
            for st, en, e, _, _ in cs:
                en = min(en, len(acc))      # recordings differ in length
                lo, hi = max(st, rs), min(en, re_)
                if hi - lo < 10:
                    continue
                off = np.mean(rcurve[lo:hi] - e[lo:hi])
                acc[st:en] += e[st:en] + off
                cnt[st:en] += 1
            idx = np.nonzero(cnt > 0)[0]
            if len(idx) < 20:
                shapes.append(None)
                continue
            i0, i1 = idx[0], idx[-1] + 1
            good = cnt[i0:i1] > 0
            l0, s1, s2, tk, x0, s0 = fit_curve(tref[i0:i1][good], (acc[i0:i1] / np.maximum(cnt[i0:i1], 1))[good])
            drop = (s1 - s2) * tk if s1 != s2 else 0.0
            shapes.append({"s1": s1, "s2": s2, "tk": tk if s1 != s2 else 0.0, "drop": drop, "x0": x0, "s0": s0})
        if all(sh is None for sh in shapes):
            continue
        # a group without enough signal (soft strikes of quiet partials) takes its neighbour's shape
        for i in (1, 0, 2, 1):
            if shapes[i] is None:
                shapes[i] = next(shapes[j] for j in sorted(range(3), key=lambda j: abs(j - i)) if shapes[j] is not None)
        s1, s2, tk, drop, x0, s0 = (shapes[1][k] for k in ("s1", "s2", "tk", "drop", "x0", "s0"))
        beats = [f for f in fits if f["beat"] > 0]
        beat = float(np.median([f["beat"] for f in beats])) if beats else 0.0
        beat_db = float(np.median([f["beat_db"] for f in beats])) if beats else 0.0
        df = float(np.median([f["df"] for f in fits]))
        if abs(df) > 0.002 * fn + 0.3:   # the phase slope locked onto something else: keep the fitted frequency
            df = 0.0
        result["partials"].append({"n": n, "f": fn + df, "f_measured": measured[n], "s1": s1, "s2": s2, "tk": tk, "drop": drop, "x0": x0, "s0": s0,
                                   "shapes": shapes, "beat": beat, "beat_db": beat_db})

    # starting level and phase per layer and mic: the complex amplitude over an early window,
    # brought back to t = 0 along the partial's own two-stage decay
    for p in result["partials"]:
        lv, ph = [], []
        for v in range(1, LAYERS + 1):
            sh = shape_at(p, v - 1)
            x, _ = layers[v - 1]
            lvv, phv = [], []
            for ch in range(2):
                t, z = envelope(x[:, ch], sr, p["f"], T, tmax=0.5)
                t0 = max(0.75 * T, 0.015)
                sel = (t >= t0) & (t <= t0 + 0.04)
                if not sel.any():
                    sel = slice(0, 3)
                zz, tt = z[sel], t[sel]
                lvv.append(float(np.mean(20 * np.log10(np.abs(zz) + 1e-12) - shape_db(sh, tt))))
                phv.append(float(np.angle(np.mean(zz))))
            lv.append(lvv)
            ph.append(phv)
        if p["n"] in cl:
            p["cluster"] = cl[p["n"]]
            lv = [[v + cl[p["n"]]["gain_db"] for v in mics] for mics in lv]   # the whole cluster's power
        p["levels"] = lv            # [layer][mic] dB of the decay line at the strike (the transient adds x0 dB on top)
        p["phases"] = ph            # [layer][mic] phase at the strike (rad), cosine reference
        p["phase"] = float(np.angle(np.mean(np.exp(1j * (np.array(ph)[8:, 1] - np.array(ph)[8:, 0])))))
    return result


def main():
    root = Path(sys.argv[1])
    out = Path(sys.argv[2])
    if len(sys.argv) > 3 and sys.argv[3] == "--merge":
        # combine runs over parts of the key list into one file:
        #   analyze.py <dir> a.json 21,27,… & analyze.py <dir> b.json 24,30,… ; analyze.py <dir> out.json --merge a.json b.json
        parts = [json.load(open(f)) for f in sys.argv[4:]]
        merged = parts[0]
        merged["keys"] = sorted((k for p in parts for k in p["keys"]), key=lambda k: k["key"])
        out.write_text(json.dumps(merged))
        return
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
