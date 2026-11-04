#!/usr/bin/env python3
"""How different does the model sound from the recording? A perceptual distance, without listeners.

The octave-band measures (compare.py and the rest) say where the energy is wrong, in dB. This
says how much of the difference a listener could hear, from a simple model of the ear:

  1. Mains hum (50 Hz and harmonics) is notched out of both signals: the model doesn't play it.
  2. A gammatone filterbank, one band per ERB (the ear's frequency resolution), 40 Hz-16 kHz.
  3. Each band's power, smoothed over 8 ms and read every 2 ms (the ear's time resolution:
     beating and the attack stay, waveform phase and the fine structure of noise go).
  4. Specific loudness per band (Zwicker's form: N' ∝ (0.5 + 0.5 E/E_thr)^0.23 - 1), with the
     threshold of hearing, at a playback level where a full-scale sine is 100 dB SPL (--spl).
     Loudness is compressive, so a 1 dB error matters as much in a quiet band as in a loud one
     only in proportion to how loud the band is, and anything under the threshold doesn't count.
  5. The model gets the recordings' own background added (room noise and rumble, measured from
     the quiet ends of the top keys' softest takes), as a listener hears it under every recorded
     note; else a quiet note's tail sinking into it would count as the model's error.
  6. The difference per band and frame, less a tolerance of 5 % of the louder (about 1 dB, the
     ear's level discrimination), so differences too small to hear count as nothing.

The distance in a time window is the summed audible difference over the recording's summed
loudness there, in %. Three windows: the attack (0-80 ms), the early sound (80-500 ms) and the
sustain (0.5-3 s), and the score is their mean. Signed versions show which way it's wrong (too
loud or too quiet) in low (< 500 Hz), mid (0.5-4 kHz) and high (> 4 kHz) bands.

The yardstick: the same distance between the recording and the recording one velocity layer up
(or down, for the top layer), the smallest step the recordings themselves take. A model nearer
its recording than that is very close.

    python3 -I tools/measure/perceptual.py [--keys 21,33,…] [--layers 4,8,13,16] [--json out.json]
    python3 -I tools/measure/perceptual.py --compare a.json b.json      # two runs, key by key

Every script option of tools/measure (--render, --sal) applies. Renders run two at a time.
"""
import json
import os
import subprocess
import sys
import tempfile
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import scipy.signal as sig
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M  # noqa: E402

SR = 48000
HOP = 0.002            # frame step (s)
SMOOTH = 0.008         # power smoothing (s)
TOL = 0.05             # level tolerance: 5 % of specific loudness ≈ 1 dB
WINDOWS = [("attack", 0.0, 0.08), ("early", 0.08, 0.5), ("sustain", 0.5, 3.0)]
REGIONS = [("low", 0, 500), ("mid", 500, 4000), ("high", 4000, 1e9)]
DEFAULT_KEYS = list(range(21, 109, 3))           # every measured key
DEFAULT_LAYERS = [4, 8, 13, 16]                 # 13: velocity 100, the on-screen keyboard's


def erb_centres(lo=40.0, hi=16000.0, step=1.0):
    e = lambda f: 21.4 * np.log10(1 + 0.00437 * f)
    inv = lambda E: (10 ** (E / 21.4) - 1) / 0.00437
    return inv(np.arange(e(lo), e(hi) + 1e-9, step))


CENTRES = erb_centres()


def band_power(x, sr, fc):
    """A 4th-order gammatone band around fc (bandwidth 1.019 ERB): the signal shifted down by fc
    and through four one-pole low-passes, which is stable at any centre; returns |output|²."""
    erb = 24.7 * (4.37e-3 * fc + 1)
    a = np.exp(-2 * np.pi * 1.019 * erb / sr)
    y = x * np.exp(-2j * np.pi * fc * np.arange(len(x)) / sr)
    for _ in range(4):
        y = sig.lfilter([1 - a], [1, -a], y)
    return np.abs(y) ** 2 / 2          # a full-scale sine at fc → 0.5, as its mean square


def threshold_db(f):
    """Threshold of hearing (dB SPL, Terhardt)."""
    k = f / 1000.0
    return 3.64 * k ** -0.8 - 6.5 * np.exp(-0.6 * (k - 3.3) ** 2) + 1e-3 * k ** 4


def dehum(x, sr):
    """Notch out 50 Hz and its harmonics (to 2 kHz), ±1.2 Hz."""
    n = len(x)
    N = 2 * n                       # zero-padded: the notches' long ringing mustn't wrap round
    X = np.fft.rfft(x, N, axis=0)   # and carry the attack into the end of the clip
    f = np.fft.rfftfreq(N, 1 / sr)
    m = np.zeros(len(f), bool)
    for k in range(1, 41):
        m |= np.abs(f - 50.0 * k) < 1.2
    X[m] = 0
    return np.fft.irfft(X, N, axis=0)[:n]


def band_powers(x, sr=SR, length=3.0):
    """Each band's power per frame and mic, a full-scale sine = 0.5: (2, frames, bands)."""
    n = int(length * sr)
    x = np.asarray(x, float)[:n]
    if len(x) < n:
        x = np.concatenate([x, np.zeros((n - len(x), x.shape[1]))])
    x = dehum(x, sr)
    step, L = int(HOP * sr), int(SMOOTH * sr)
    out = []
    for ch in range(x.shape[1]):
        bands = []
        for fc in CENTRES:
            c = np.concatenate([[0.0], np.cumsum(band_power(x[:, ch], sr, fc))])
            idx = np.arange(0, n - L, step)
            bands.append((c[idx + L] - c[idx]) / L)
        out.append(np.array(bands).T)
    return np.array(out)


def specific_loudness(P, spl=100.0):
    thr = 10 ** (threshold_db(CENTRES) / 10)
    E = P * 2 * 10 ** (spl / 10)                      # intensity re 20 µPa, as a ratio
    return np.maximum(0.08 * thr ** 0.23 * ((0.5 + 0.5 * E / thr) ** 0.23 - 1), 0)


FLOOR = None


def recording_floor(sal):
    """The recordings' own background (room noise, rumble, what's left of the hum) per mic and
    band: the median band power over the last 0.4 s of the quietest takes of the top two keys,
    whose notes have died away by then. A listener hears it under every recorded note."""
    global FLOOR
    if FLOOR is None:
        cache = M.TMP / "perc_floor.npy"
        if cache.exists():
            FLOOR = np.load(cache)
        else:
            ps = []
            for key in (105, 108):
                for layer in (1, 2):
                    x, sr = M.C.load_rec(Path(sal), key, layer)
                    ps.append(np.median(band_powers(x[-int(0.4 * sr):], sr, 0.4), axis=1))
            FLOOR = np.median(np.array(ps), axis=0)          # (2, bands)
            np.save(cache, FLOOR)
    return FLOOR


def loudness(x, sr=SR, length=3.0, spl=100.0, floor=None):
    """Specific loudness [frame, band] per mic: (2, frames, bands). With floor (per mic and band
    power), that background is added first: the recordings have it, the model doesn't, and it
    mustn't count as the model's error where a quiet note's tail sinks into it."""
    P = band_powers(x, sr, length)
    if floor is not None:
        P = P + floor[:, None, :]
    return specific_loudness(P, spl)


def distance(Nr, Nm):
    """Audible difference between two loudness patterns (2, frames, bands)."""
    f = min(Nr.shape[1], Nm.shape[1])
    Nr, Nm = Nr[:, :f], Nm[:, :f]
    t = np.arange(f) * HOP + SMOOTH / 2
    diff = Nm - Nr
    aud = np.maximum(np.abs(diff) - TOL * np.maximum(Nr, Nm), 0)
    res = {}
    scores = []
    for name, t0, t1 in WINDOWS:
        sel = (t >= t0) & (t < t1)
        ref = Nr[:, sel].sum()
        if not sel.any() or ref <= 0:
            continue
        d = 100 * aud[:, sel].sum() / ref
        res[name] = round(float(d), 2)
        scores.append(d)
        for rn, f0, f1 in REGIONS:
            b = (CENTRES >= f0) & (CENTRES < f1)
            res[f"{name}_{rn}"] = round(float(100 * diff[:, sel][:, :, b].sum() / ref), 2)   # signed
    res["score"] = round(float(np.mean(scores)), 2)
    return res


# --- recordings and renders -------------------------------------------------------------------

def rec_loudness(sal, key, layer):
    cache = M.TMP / f"perc_rec_{key}_{layer}.npy"
    if cache.exists():
        return np.load(cache)
    x = M.recording(sal, key, layer)
    N = loudness(x)
    np.save(cache, N)
    return N


KNOWN = {}


def known_params(tool):
    """The parameter IDs a build has (older builds lack some: the history checks use them)."""
    if tool not in KNOWN:
        out = subprocess.run([tool, "--params"], capture_output=True, text=True).stdout
        KNOWN[tool] = {ln.split()[0] for ln in out.splitlines() if ln.strip()}
    return KNOWN[tool]


def render_note(tool, key, layer, params=None):
    vel = M.layer_vel(layer)
    p = {"space.room": 0, "play.velmap": 1}
    p.update(params or {})
    p = {k: v for k, v in p.items() if k in known_params(tool)}
    sc = {"patch": "init", "params": p, "length": 3.3, "events": [{"t": 0, "on": key, "vel": vel}]}
    fd, js = tempfile.mkstemp(suffix=".json", dir=M.TMP)
    os.close(fd)
    wav = js[:-5] + ".wav"
    Path(js).write_text(json.dumps(sc))
    try:
        subprocess.run([tool, js, "--out", wav], check=True, capture_output=True)
        y, sr = sf.read(wav, always_2d=True)
    finally:
        for f in (js, wav):
            try:
                os.unlink(f)
            except OSError:
                pass
    m = y.mean(1)
    on = max(0, int(np.argmax(np.abs(m) > np.abs(m).max() * 0.05)) - int(0.002 * sr))
    return y[on:] * 10 ** 0.5        # undo the engine's −10 dB headroom


def case(job):
    tool, sal, key, layer, yard, params = job
    Nr = rec_loudness(sal, key, layer)
    r = distance(Nr, loudness(render_note(tool, key, layer, params), floor=recording_floor(sal)))
    r.update(key=key, layer=layer)
    if yard:
        other = layer + 1 if layer < 16 else layer - 1
        r["yardstick"] = distance(Nr, rec_loudness(sal, key, other))["score"]
    return r


def run(tool, sal, keys, layers, yard=True, params=None, procs=2):
    jobs = [(tool, sal, k, l, yard, params) for k in keys for l in layers]
    with Pool(procs) as p:
        return p.map(case, jobs)


def summary(rows, label=""):
    sc = np.array([r["score"] for r in rows])
    line = f"{label}score median {np.median(sc):5.1f} %  mean {sc.mean():5.1f} %"
    for w, _, _ in WINDOWS:
        v = [r[w] for r in rows if w in r]
        line += f"   {w} {np.median(v):5.1f}"
    if all("yardstick" in r for r in rows):
        y = np.array([r["yardstick"] for r in rows])
        line += f"   yardstick (one layer apart) {np.median(y):5.1f} %  model/yardstick {np.median(sc / np.maximum(y, 1e-3)):4.2f}"
    return line


def main():
    def extra(p):
        p.add_argument("--keys", default=",".join(map(str, DEFAULT_KEYS)))
        p.add_argument("--layers", default=",".join(map(str, DEFAULT_LAYERS)))
        p.add_argument("--json")
        p.add_argument("--params", default="{}", help="extra parameters, JSON")
        p.add_argument("--no-yardstick", action="store_true")
        p.add_argument("--compare", nargs=2)
    a = M.args(__doc__, extra)
    if a.compare:
        A_, B_ = (json.load(open(f)) for f in a.compare)
        ia = {(r["key"], r["layer"]): r for r in A_}
        print(f"{'note':5s}{'layer':>6s}  {'A':>6s} {'B':>6s}  {'B-A':>6s}")
        for r in B_:
            o = ia.get((r["key"], r["layer"]))
            if o:
                print(f"{M.A.note_name(r['key']):5s}{r['layer']:6d}  {o['score']:6.1f} {r['score']:6.1f}  {r['score'] - o['score']:+6.1f}")
        print(summary(A_, "A: "))
        print(summary(B_, "B: "))
        return
    keys = [int(k) for k in a.keys.split(",")]
    layers = [int(v) for v in a.layers.split(",")]
    rows = run(a.render, a.sal, keys, layers, not a.no_yardstick, json.loads(a.params))
    print(f"{'note':5s}{'layer':>6s}  {'score':>6s} {'attack':>7s} {'early':>6s} {'sust':>6s}  {'yard':>5s}   "
          f"signed, early: low / mid / high")
    for r in rows:
        print(f"{M.A.note_name(r['key']):5s}{r['layer']:6d}  {r['score']:6.1f} {r.get('attack', 0):7.1f} {r.get('early', 0):6.1f} "
              f"{r.get('sustain', 0):6.1f}  {r.get('yardstick', 0):5.1f}   "
              f"{r.get('early_low', 0):+5.1f} {r.get('early_mid', 0):+5.1f} {r.get('early_high', 0):+5.1f}")
    print(summary(rows))
    if a.json:
        Path(a.json).write_text(json.dumps(rows))


if __name__ == "__main__":
    main()
