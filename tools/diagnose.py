#!/usr/bin/env python3
"""Side-by-side measurements of the model against the recordings.

For each key and velocity layer:
  noise   energy between the partials, relative to all energy (dB), over
          5-50 ms, 50-300 ms and 0.3-1.5 s: how noisy the note sounds
  rise    attack rise time, 10 % → 90 % of the first peak (ms)
  bright  spectral centroid of the first 8 ms (Hz): how clicky/bright the strike is
  sustain spectral centroid over 1-2 s (Hz): the brightness of the ringing note
  cents   frequency error of partials 1-30 (model − recording, cents), in groups

    python3 -I tools/diagnose.py <Salamander dir> <piano_render> <out dir> [key:layer,...] [--params k=v,...]
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import compare as C  # noqa: E402


def inter_partial(x, sr, f0, B, t0, t1):
    m = x.mean(1)
    seg = m[int(t0 * sr):int(t1 * sr)]
    n = len(seg)
    N = 1 << int(np.ceil(np.log2(max(n, 2048)))) * 1
    sp = np.abs(np.fft.rfft(seg * np.hanning(n), N)) ** 2
    f = np.fft.rfftfreq(N, 1 / sr)
    near = np.zeros_like(f, bool)
    width = max(3 * sr / n, 0.12 * f0)          # main lobe, or 12% of f0
    k = 1
    while True:
        fk = k * f0 * np.sqrt(1 + B * k * k)
        if fk > sr / 2:
            break
        near |= np.abs(f - fk) < width
        k += 1
    sel = f > 0.6 * f0
    return 10 * np.log10(sp[sel & ~near].sum() / (sp[sel].sum() + 1e-20) + 1e-12)


def rise(x, sr):
    m = np.abs(x.mean(1))
    k = int(0.0003 * sr)
    env = np.convolve(m, np.ones(k) / k, "same")[: int(0.05 * sr)]
    pk = env.max()
    i90 = int(np.argmax(env > 0.9 * pk))
    i10 = int(np.argmax(env > 0.1 * pk))
    return (i90 - i10) / sr * 1000


def centroid(x, sr, t0, t1):
    seg = x.mean(1)[int(t0 * sr):int(t1 * sr)]
    sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), 8192)) ** 2
    f = np.fft.rfftfreq(8192, 1 / sr)
    return float((sp * f).sum() / (sp.sum() + 1e-20))


def partial_freqs(x, sr, f0, B, nmax=30):
    seg = x.mean(1)[int(0.2 * sr):int(2.2 * sr)]
    N = 1 << 20
    sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), N))
    f = np.fft.rfftfreq(N, 1 / sr)
    out = []
    for n in range(1, nmax + 1):
        fn = n * f0 * np.sqrt(1 + B * n * n)
        if fn > 15000:
            break
        lo, hi = np.searchsorted(f, [fn * 0.985, fn * 1.015])
        i = lo + int(np.argmax(sp[lo:hi]))
        out.append((n, f[i], 20 * np.log10(sp[i] + 1e-12)))
    return out


def main():
    root, tool, out_dir = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = [(57, 4), (57, 14), (69, 2), (69, 6), (69, 10), (69, 16), (72, 4), (72, 10), (72, 16), (81, 10)]
    params = {"space.room": 0, "play.velmap": 1, "play.softattack": 0}   # layer by layer, as recorded
    for a in sys.argv[4:]:
        if a.startswith("--params"):
            for kv in a.split("=", 1)[1].split(","):
                k, v = kv.split(":")
                params[k] = float(v)
        else:
            cases = [tuple(int(v) for v in c.split(":")) for c in a.split(",")]
    model = {k["key"]: k for k in json.load(open(Path(__file__).parent / "salamander.json"))["keys"]} if not __import__("os").environ.get("DIAG_MODEL") else {k["key"]: k for k in json.load(open(__import__("os").environ["DIAG_MODEL"]))["keys"]}
    print(f"{'':10s} {'noise 5-50ms':>13s} {'50-300ms':>13s} {'0.3-1.5s':>13s} {'rise ms':>11s} {'bright Hz':>13s} {'sustain Hz':>13s}   (recording / model)")
    for k, layer in cases:
        lo, hi = C.VEL_RANGES[layer - 1]
        vel = (lo + hi) // 2
        rec, sr = C.load_rec(root, k, layer)
        sc = {"patch": "init", "params": params, "length": 2.5, "events": [{"t": 0, "on": k, "vel": vel}]}
        p = out_dir / f"d_{k}_{vel}.json"
        p.write_text(json.dumps(sc))
        w = out_dir / f"d_{k}_{vel}.wav"
        subprocess.run([tool, str(p), "--out", str(w)], check=True, capture_output=True)
        import soundfile as sf
        y, _ = sf.read(w, always_2d=True)
        m = y.mean(1)
        on = max(0, int(np.argmax(np.abs(m) > np.abs(m).max() * 0.05)) - int(0.002 * sr))
        mod = y[on:]
        km = model[k]
        f0, B = km["f0"], km["B"]
        row = []
        for t0, t1 in ((0.005, 0.05), (0.05, 0.3), (0.3, 1.5)):
            row.append(f"{inter_partial(rec, sr, f0, B, t0, t1):6.1f}/{inter_partial(mod, sr, f0, B, t0, t1):6.1f}")
        row.append(f"{rise(rec, sr):5.1f}/{rise(mod, sr):5.1f}")
        row.append(f"{centroid(rec, sr, 0, 0.008):6.0f}/{centroid(mod, sr, 0, 0.008):6.0f}")
        row.append(f"{centroid(rec, sr, 1, 2):6.0f}/{centroid(mod, sr, 1, 2):6.0f}")
        print(f"{C.name(k):4s} v{layer:<4d} " + " ".join(f"{r:>13s}" for r in row))
        pr = {n: (fr, db) for n, fr, db in partial_freqs(rec, sr, f0, B)}
        pm = {n: (fr, db) for n, fr, db in partial_freqs(mod, sr, f0, B)}
        errs = [(n, 1200 * np.log2(pm[n][0] / pr[n][0])) for n in pr if n in pm and pr[n][1] > max(v[1] for v in pr.values()) - 50]
        groups = [(1, 4), (5, 10), (11, 20), (21, 30)]
        txt = []
        for a, b in groups:
            e = [c for n, c in errs if a <= n <= b]
            if e:
                txt.append(f"n{a}-{b}: {np.median(e):+5.1f}")
        print(f"{'':10s} partial pitch, model − recording (cents): " + "  ".join(txt))


if __name__ == "__main__":
    main()
