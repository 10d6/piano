#!/usr/bin/env python3
"""The tones between the partials: what the body's ring really is, where it isn't noise.

After the strike, the recordings hold energy between a note's partials for a second or more (the
body's ring, tools/analyze_body.py). The model played all of it as octave bands of noise. Measured
between the partials, the recordings' spectrum there is peaky, not flat: its spectral flatness is
0.06-0.12 from C4 to D#6 over 0.1-0.4 s, against 0.5-0.7 for the model's band noise (white noise
is ~0.56). It is tones: resonances of the soundboard and case, other strings ringing along, the
struck strings' own longitudinal and phantom components. Heard as noise instead, it was the "noisy
sound that lingers for a quarter second" on C5.

Per measured key this finds those tones and measures them, like a partial:

  1. One long spectrum (80 ms-1.2 s) of the four loudest layers, with every partial, mode and
     cluster component left out (±max(25 Hz, 3 % of f0)) and the mains hum below 1 kHz.
  2. Peaks standing 12 dB over the median of the spectrum around them (±1/6 octave), from 80 Hz
     to 10 kHz: the strongest TONES of them.
  3. Each tone's envelope per layer and mic (heterodyne, 0.1 s window, which passes nothing
     25 Hz or more away), from 80 ms while it stands 10 dB over the floor: its frequency from
     the phase's slope, one decay for all layers (the median, at most 40 dB/s: faster is the
     knock's), and per layer and mic the level and phase at t = 0 along that decay.

Written into salamander.json's keys as "tones". make_header.py takes the body's resonances from
them (every key's, merged); with VOICE_TONES=1 it also has each key's voice play its own (and
analyze_body.py leaves them out of the noise it measures), which is off.

    python3 -I tools/analyze_residual.py <Salamander dir> tools/salamander.json
"""
import json
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from scipy.signal import find_peaks

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze as A  # noqa: E402

SR = 48000
TONES = 24
T_ENV = 0.1
T0 = 0.08
MAX_DECAY = 40.0
DB = 20 / np.log(10)


def component_freqs(k):
    fs = []
    for p in k["partials"]:
        fs.append(p["f"])
        for m in p.get("modes") or []:
            fs.append(p["f"] + m["df"])
        for c in (p.get("cluster") or {}).get("cents", []):
            fs.append(p["f"] * 2 ** (c / 1200))
    n = 1
    while True:                           # and the stiff-string series, where partials weren't kept
        f = n * k["f0"] * np.sqrt(1 + k["B"] * n * n)
        if f > SR / 2:
            break
        fs.append(f)
        n += 1
    return np.array(fs)


def find_tones(layers, k):
    L = min(len(x) for x, _ in layers[-4:])
    mono = sum(x[:L].mean(1) for x, _ in layers[-4:])
    seg = mono[int(T0 * SR):int(1.2 * SR)]
    n = 1 << 20
    X = 20 * np.log10(np.abs(np.fft.rfft(seg * np.hanning(len(seg)), n)) + 1e-12)
    f = np.fft.rfftfreq(n, 1 / SR)
    width = max(25.0, 0.03 * k["f0"])
    near = np.zeros(len(f), bool)
    for fc in component_freqs(k):
        lo, hi = np.searchsorted(f, [fc - width, fc + width])
        near[lo:hi] = True
    near |= (np.abs(f - 50 * np.round(f / 50)) < 2.5) & (f < 1000)
    ok = ~near & (f > 80) & (f < 10000)
    Y = np.where(ok, X, -300.0)
    # local reference: the median of the kept spectrum within ±1/6 octave
    ref = np.full(len(f), -300.0)
    idx = np.nonzero(ok)[0]
    if not len(idx):
        return []
    step = 64
    for i in range(idx[0], idx[-1] + 1, step):
        lo, hi = np.searchsorted(f, [f[i] * 2 ** (-1 / 6), f[i] * 2 ** (1 / 6)])
        sel = ok[lo:hi]
        if sel.sum() > 10:
            ref[i:i + step] = np.median(X[lo:hi][sel])
    pk, _ = find_peaks(Y, distance=int(4.0 / (f[1] - f[0])))
    pk = [i for i in pk if Y[i] > ref[i] + 12]
    pk.sort(key=lambda i: -Y[i])
    out = []
    for i in pk:
        if all(abs(f[i] - g) > 8.0 for g in out):
            out.append(float(f[i]))
        if len(out) == TONES:
            break
    return out


def measure_tone(layers, fc):
    """One tone's frequency, decay, and level and phase per layer and mic at t = 0."""
    fits = []
    for v, (x, _) in enumerate(layers):
        for ch in range(2):
            t, z = A.envelope(x[:, ch], SR, fc, T_ENV, hop=0.01, tmax=min(len(x) / SR, 3.0))
            e = 20 * np.log10(np.abs(z) + 1e-12)
            floor = np.median(e[-max(10, len(e) // 10):])
            sel = t >= T0
            above = np.nonzero(sel & (e > floor + 10))[0]
            if len(above) < 8:
                continue
            i0, i1 = above[0], above[-1] + 1
            tt, ee = t[i0:i1], e[i0:i1]
            slope, icpt = np.polyfit(tt, ee, 1)
            ph = np.unwrap(np.angle(z[i0:min(i1, i0 + 30)]))
            df = np.polyfit(t[i0:i0 + len(ph)], ph, 1)[0] / (2 * np.pi) if len(ph) > 4 else 0.0
            fits.append((v, ch, slope, tt, ee, z[i0:i1], df))
    if not fits:
        return None
    decay = float(np.clip(np.median([fi[2] for fi in fits]), -MAX_DECAY, -0.5))
    df = float(np.median([fi[6] for fi in fits]))
    if abs(df) > 3.0:
        df = 0.0
    f = fc + df
    lv = [[-200.0, -200.0] for _ in layers]
    ph = [[0.0, 0.0] for _ in layers]
    for v, ch, _, tt, ee, zz, _ in fits:
        lv[v][ch] = float(np.mean(ee - decay * tt))
        # phase at t = 0: the envelope was taken at fc, the tone is at f
        ph[v][ch] = float(np.angle(np.mean(zz * np.exp(-2j * np.pi * df * tt))))
    return {"f": f, "decay": decay, "levels": lv, "phases": ph}


def fill_layers(tone, key_levels):
    """Layers where the tone was too weak to measure take the nearest measured one, moved by the
    difference in the note's own loudness between the two layers."""
    have = [v for v in range(len(tone["levels"])) if max(tone["levels"][v]) > -190]
    for v in range(len(tone["levels"])):
        for c in (0, 1):
            if tone["levels"][v][c] > -190:
                continue
            src = min((u for u in have if tone["levels"][u][c] > -190), key=lambda u: abs(u - v), default=None)
            if src is None:
                src = min(have, key=lambda u: abs(u - v))
                tone["levels"][v][c] = tone["levels"][src][1 - c]
                tone["phases"][v][c] = tone["phases"][src][1 - c]
            else:
                tone["levels"][v][c] = tone["levels"][src][c] + key_levels[v] - key_levels[src]
                tone["phases"][v][c] = tone["phases"][src][c]


def analyze_key(job):
    root, k = job
    layers = [A.load(root / "Samples" / f"{k['name']}v{v}.flac") for v in range(1, A.LAYERS + 1)]
    loud = [10 * np.log10(sum(10 ** (p["levels"][v][0] / 10) for p in k["partials"]) + 1e-30) for v in range(A.LAYERS)]
    tones = []
    for fc in find_tones(layers, k):
        t = measure_tone(layers, fc)
        if t is None:
            continue
        fill_layers(t, loud)
        t["levels"] = [[round(a, 2) for a in v] for v in t["levels"]]
        t["phases"] = [[round(a, 4) for a in v] for v in t["phases"]]
        t["f"], t["decay"] = round(t["f"], 3), round(t["decay"], 3)
        tones.append(t)
    rel = [max(t["levels"][12]) - loud[12] for t in tones]
    print(f"{k['name']:4s} {len(tones):2d} tones, " +
          (f"{min(t['f'] for t in tones):6.0f}-{max(t['f'] for t in tones):6.0f} Hz, decay median "
           f"{np.median([t['decay'] for t in tones]):6.1f} dB/s, levels {np.median(rel):+5.1f} dB against the note (layer 13)"
           if tones else ""), flush=True)
    return k["key"], tones


def main():
    root, path = Path(sys.argv[1]), Path(sys.argv[2])
    data = json.load(open(path))
    with Pool(2) as pool:
        res = dict(pool.map(analyze_key, [(root, k) for k in data["keys"]], chunksize=1))
    for k in data["keys"]:
        k["tones"] = res.get(k["key"], [])
    path.write_text(json.dumps(data))


if __name__ == "__main__":
    main()
