#!/usr/bin/env python3
"""Fit each partial as the engine plays it: a few decaying modes, fitted to the recordings at once.

analyze.py describes a partial with separate measurements: a two-stage decay line (hinge fit) and
a first burst in dB, a beat found in what the line leaves over (and scaled by BEAT_SCALE), a level
and phase per layer read from one early window. Each is measured its own way, and they disagree
with each other in places (a beat that is really the decay's curvature, a level read where the
beat is in a trough).

Physically, a partial of a struck unison is a handful of modes: the two or three strings, each in
two polarisations, coupled through the bridge. Each mode is a decaying sinusoid with its own
frequency and decay, the same at every velocity (the system is linear once the hammer has gone),
and what the hammer sets is only how much of each mode there is, and in what phase, per mic.
The prompt sound and the aftersound are such modes (Weinreich): a fast one and a slow one. Two
close in frequency beat. So this fits, per partial:

    z(layer, mic, t) = sum_m c[layer, mic, m] · e^{s_m t} · (1 − e^{−(t−d)/τ})

  - s_m = −α_m + j 2π Δf_m: each mode's decay and its offset from the partial's frequency, shared
    by all 16 layers and both mics;
  - c: complex amplitudes per layer, mic and mode;
  - the swell (τ, d per key) from the onset fit (salamander_onset.json), as the engine plays it.

It is fitted to the heterodyned envelopes (analyze.envelope) with the envelope's own window applied
to the model exactly, so the model is compared with the recording in the same terms. The modes'
frequencies and decays are found by a subspace estimate (ESPRIT) over the loudest layers and then
refined by variable projection (for given modes the amplitudes are a linear least-squares problem),
weighting the error the way loudness grows (residual × level^−0.54), so the quiet tail counts in
proportion to how loud it is heard. One mode is added at a time while it lowers the error by more
than 15 %, up to four.

The modes are written into salamander.json's partials ("modes"), beside the older fields, which
the engine no longer uses where modes exist.

    python3 -I tools/analyze_modes.py <Salamander dir> tools/salamander.json [keys] [--procs 2]

Each key's result is kept in <tmp>/piano-modes/<key>.json, so an interrupted run picks up where it
stopped; delete that folder after changing this script.
"""
import itertools
import json
import math
import sys
import tempfile
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze as A  # noqa: E402

SR = 48000
DEBUG = False
DB = 20 / math.log(10)          # nepers → dB
MAX_MODES = 4                   # per partial; 6 from C5 up, where a partial is two or three strings
MAX_MODES_TREBLE = 6            # tens of cents apart, each with its own decay, and a fast burst besides
IMPROVE = 0.85                  # a mode is kept if it brings the error under this fraction
POINTS = 200                    # fit points per signal after the first 0.15 s
FIT_LAYERS = (2, 4, 6, 8, 10, 12, 13, 14, 16)   # layers the modes are fitted on (amplitudes: all)
GAMMA = 0.9                     # weighting: the error × level^−GAMMA; near 1 each moment counts for itself, as heard then


def window_gain(s, L):
    """What the envelope's triangular window (two boxcars of L samples) does to e^{st}, centred."""
    s = np.asarray(s, complex) / SR
    with np.errstate(divide="ignore", invalid="ignore"):
        box = np.exp(-s * (L - 1) / 2) * np.where(np.abs(s) < 1e-12, L, (1 - np.exp(s * L)) / (1 - np.exp(s)))
    return (box / L) ** 2


def basis(s, t, L, tau, d):
    """Envelope of e^{st}·(1 − e^{−(t−d)/τ}) as analyze.envelope sees it, at times t (after the window
    has cleared the swell's start)."""
    s = np.asarray(s, complex)[None, :]
    t = t[:, None]
    q = s - 1.0 / tau
    return window_gain(s, L) * np.exp(s * t) - math.exp(d / tau) * window_gain(q, L) * np.exp(q * t)


def esprit(sigs, order):
    """Poles shared by several signals (equally spaced samples): z_k per sample step."""
    N = min(len(x) for x in sigs)
    Lh = N // 2
    H = np.hstack([np.array([x[i:i + N - Lh + 1] for i in range(Lh)]) for x in sigs])
    U, _, _ = np.linalg.svd(H, full_matrices=False)
    Us = U[:, :order]
    Phi = np.linalg.lstsq(Us[:-1], Us[1:], rcond=None)[0]
    return np.linalg.eigvals(Phi)


class Problem:
    """The fit's data, padded into arrays (signals × points; padding has weight 0), so a set of modes
    is judged for every signal at once."""

    def __init__(self, ts, zs, ws, L, tau, d):
        self.ts, self.zs, self.ws, self.L, self.tau, self.d = ts, zs, ws, L, tau, d
        n = max(len(t) for t in ts)
        S = len(ts)
        self.T = np.zeros((S, n))
        self.Z = np.zeros((S, n), complex)
        self.W = np.zeros((S, n))
        for i, (t, z, w) in enumerate(zip(ts, zs, ws)):
            self.T[i, :len(t)], self.Z[i, :len(t)], self.W[i, :len(t)] = t, z, w
            self.T[i, len(t):] = t[-1]
        self.tau_ = np.asarray(tau, float)[:, None, None]
        self.dd = np.asarray(d, float)[:, None, None]
        self.WZ = self.W * self.Z

    def bases(self, s):
        """S × n × M: each mode's envelope at each signal's points, weighted."""
        s = np.asarray(s, complex)[None, None, :]
        q = s - 1.0 / self.tau_
        t = self.T[:, :, None]
        B = window_gain(s, self.L) * np.exp(s * t) - np.exp(self.dd / self.tau_) * window_gain(q, self.L) * np.exp(q * t)
        return B * self.W[:, :, None]

    def solve(self, s):
        """Amplitudes for modes s (per signal) and the weighted residual (flattened)."""
        Bw = self.bases(s)
        G = np.einsum("snm,snk->smk", Bw.conj(), Bw)
        h = np.einsum("snm,sn->sm", Bw.conj(), self.WZ)
        M = G.shape[1]
        G = G + 1e-12 * np.trace(G, axis1=1, axis2=2)[:, None, None] / M * np.eye(M)
        c = np.linalg.solve(G, h[:, :, None])[:, :, 0]
        r = np.einsum("snm,sm->sn", Bw, c) - self.WZ
        return list(c), r.ravel()

    def residual(self, theta):
        s = self.modes(theta)
        _, r = self.solve(s)
        return np.concatenate([r.real, r.imag])

    @staticmethod
    def modes(theta):
        th = np.asarray(theta).reshape(-1, 2)
        return -np.exp(th[:, 0]) + 2j * np.pi * th[:, 1]


def fit_partial(layers, f, T, tau, d, f0, hint=None, legacy=None):
    """Fit one partial: returns modes [(alpha, df)], amplitudes [layer][mic][mode] (complex) and the
    relative error, or None. hint: analyze.py's (s1, s2, beat) for the partial, as more starting
    points (the subspace estimate misses slow modes now and then); legacy(meta, problem): that
    decay shape's envelopes at the fit's points, to compare with."""
    Tf = float(np.clip(5.0 / f0, 0.004, 0.2))
    L = max(2, int(Tf * SR / 2))
    band = 0.45 / Tf                     # offsets the window still passes
    # sample the envelope at a quarter of its window (at most ~1500 points over the fit)
    sigs = []
    for v, (x, _) in enumerate(layers):
        for ch in range(2):
            t, z = A.envelope(x[:, ch], SR, f, Tf, hop=Tf / 4, tmax=min(len(x) / SR, 10.0))
            sigs.append((v, ch, t, z))
    # where to fit, per layer: from when the window has cleared the swell to where the partial
    # meets the recording's floor (+10 dB), both mics together
    ts, zs, ws, taus, ds, meta, full = [], [], [], [], [], [], []
    for v in range(len(layers)):
        (_, _, t, zl), (_, _, _, zr) = sigs[2 * v], sigs[2 * v + 1]
        n = min(len(zl), len(zr))
        t, zl, zr = t[:n], zl[:n], zr[:n]
        p = 0.5 * (np.abs(zl) ** 2 + np.abs(zr) ** 2)
        e = 10 * np.log10(p + 1e-30)
        floor = np.median(e[-max(20, n // 20):])
        t_s = Tf / 2 + max(0.01, d[v] + 3 * tau[v])
        i0 = int(np.searchsorted(t, t_s))
        pk = e[i0:i0 + max(3, int(0.3 / (Tf / 4)))].max() if i0 < n else -300
        if pk < floor + 20:
            continue
        # to where the level, beats smoothed out (0.2 s), last stands 10 dB over the floor: a beat's
        # trough on the way mustn't end it
        k = max(1, int(0.2 / (Tf / 4)))
        es = 10 * np.log10(np.convolve(p, np.ones(k) / k, mode="same") + 1e-30)
        above = np.nonzero(es[i0:] > floor + 10)[0]
        i1 = i0 + (above[-1] + 1 if len(above) else 0)
        if i1 - i0 < 24:
            continue
        # every sample for the first 0.15 s (beats, the prompt), then thinning out geometrically: about
        # POINTS per signal; each point's weight makes up for the spacing, so time counts evenly
        dense = min(i1, i0 + int(0.15 / (Tf / 4)))
        idx = np.unique(np.concatenate([np.arange(i0, dense, max(1, (dense - i0) // 120)),
                                        np.geomspace(max(dense, 1), i1 - 1, POINTS).astype(int)]))
        idx = idx[(idx >= i0) & (idx < i1)]
        gap = np.diff(np.concatenate([idx, [i1]])).astype(float)
        # smoothed level for the weights (beats averaged out over ~0.2 s)
        ps = np.convolve(p, np.ones(k) / k, mode="same")
        lv = np.sqrt(ps[idx]) + 3 * 10 ** (floor / 20)   # near the floor, the noise mustn't count
        peak = lv.max()
        # time counts on a log scale: the first 80 ms, the next 0.4 s and the seconds after weigh alike,
        # as each is heard on its own (the attack, the early sound, the sustain)
        w = lv ** -GAMMA * peak ** (GAMMA - 1) * np.sqrt(gap * (Tf / 4) / (t[idx] + 0.03))
        for z in (zl, zr):
            ts.append(t[idx])
            zs.append(z[idx])
            ws.append(w)
            taus.append(tau[v])
            ds.append(d[v])
        meta.append(v)
        full.append((t[i0:i1], zl[i0:i1], zr[i0:i1]))
    if not meta:
        return None
    prob_all = Problem(ts, zs, ws, L, taus, ds)
    sub = [i for i, v in enumerate(meta) if v + 1 in FIT_LAYERS] or list(range(len(meta)))
    pick = [2 * i + c for i in sub for c in (0, 1)]
    prob = Problem([ts[j] for j in pick], [zs[j] for j in pick], [ws[j] for j in pick], L,
                   [taus[j] for j in pick], [ds[j] for j in pick])
    # starting modes: ESPRIT over the loudest layers' envelopes, on a common grid
    loud = sorted(range(len(meta)), key=lambda i: -np.abs(full[i][1]).max())[:4]
    n_c = min(len(full[i][1]) for i in loud)
    dec = max(1, n_c // 400)          # ESPRIT on at most ~400 uniform samples
    hop = Tf / 4 * dec
    n_c //= dec
    cand = []
    if n_c >= 24:
        try:
            zk = esprit([full[i][1 + c][::dec][:n_c] for i in loud for c in (0, 1)], min(6, n_c // 4))
            for zz in zk:
                if abs(zz) < 1e-9:
                    continue
                sk = np.log(zz) / hop
                a, df = -sk.real, sk.imag / (2 * np.pi)
                if 0.01 < a * DB < 3000 and abs(df) < band:
                    cand.append((a, df))
        except np.linalg.LinAlgError:
            pass
    if not cand:
        cand = [(1.0, 0.0)]
    # order the candidates by how much of the signal each carries
    s_all = np.array([-a + 2j * np.pi * df for a, df in cand])
    cs, _ = prob.solve(s_all)
    energy = np.sum([np.abs(c) ** 2 for c in cs], 0) / np.maximum(-s_all.real, 0.05)   # energy over the note
    cand = [cand[i] for i in np.argsort(-energy)]
    if DEBUG:
        print("  candidates", [(round(a * DB, 1), round(f, 3)) for a, f in cand])
    # most a fast mode may decay: from 30 dB over its level where the fit starts, at t = 0, and no
    # more than 12 dB over it 20 ms in (after the swell). The bass's long window starts the fit
    # ~0.1 s in, and modes free to grow back towards the strike made A0's attack 30 % too loud
    t_first = min(t[0] for t in ts)
    a_max = min(3000 / DB, 30 / DB / max(t_first, 1e-3), 12 / DB / max(t_first - 0.02, 1e-3))
    lo = [math.log(0.8 / DB), -band]       # no string mode rings on slower than 0.8 dB/s
    hi = [math.log(a_max), band]
    def bounded(a, df):
        return [math.log(min(max(a, math.exp(lo[0]) * 1.01), a_max * 0.99)), float(np.clip(df, -band * 0.99, band * 0.99))]

    results = []
    prev = []
    max_modes = MAX_MODES_TREBLE if f0 > 500 else MAX_MODES
    for M in range(1, max_modes + 1):
        if M > 1 and not prev:
            break
        # starting points: the best few sets of M of ESPRIT's candidates (each judged by its
        # linear fit alone, which is cheap), and the modes so far with a slower or a faster one added
        inits = []
        if len(cand) >= M:
            combos = []
            for cmb in itertools.combinations(range(min(len(cand), 6)), M):
                ss = np.array([-cand[i][0] + 2j * np.pi * cand[i][1] for i in cmb])
                _, rr = prob.solve(ss)
                combos.append((float(np.sum(np.abs(rr) ** 2)), cmb))
            combos.sort()
            inits += [[cand[i] for i in cmb] for _, cmb in combos[:1]]
        if M > 1:
            a0, f0_ = prev[0]
            inits += [prev + [(a0 / 3, f0_ + 0.2)], prev + [(a0 * 5, f0_ - 0.2)]]
        if hint and len(inits) < 4:
            s1, s2, bt = (-hint[0] / DB, -hint[1] / DB, hint[2])
            hs = {1: [[(s2, 0.0)], [(s1, 0.0)]], 2: [[(s1, 0.0), (s2, 0.0)], [(s2, -bt / 2), (s2, bt / 2)]],
                  3: [[(s1, 0.0), (s2, -bt / 2), (s2, bt / 2)]], 4: [[(s1, 0.0), (s1, bt), (s2, -bt / 2), (s2, bt / 2)]]}
            if M in hs:
                inits += hs[M]
            elif M > 1:
                inits.append(prev + [(s2 * 0.7, prev[0][1] - bt)])   # a slower string beside the others
        best_r = None
        for init in inits:
            th0 = sum((bounded(a, df) for a, df in init), [])
            try:
                r = least_squares(prob.residual, th0, bounds=(lo * M, hi * M), x_scale="jac", max_nfev=25 * M)
            except (ValueError, np.linalg.LinAlgError):
                continue
            if best_r is None or r.cost < best_r.cost:
                best_r = r
        if best_r is None:
            break
        s = Problem.modes(best_r.x)
        cs, _ = prob.solve(s)
        # reject fits whose modes, played back from the strike, swell far above where the fit starts
        # (large modes nearly cancelling within the fit, apart before it)
        C = np.array(cs)
        tt = np.linspace(0, t_first, 12)
        E = np.abs(np.exp(np.outer(tt, s)) @ C.T)          # times × signals
        if M > 1 and np.median(E.max(0) / (E[-1] + 1e-15)) > 10 ** (30 / 20):
            break
        results.append((2 * best_r.cost, s, cs))
        prev = [(-x.real, x.imag / (2 * np.pi)) for x in s]
        if len(results) >= 2 and results[-1][0] > 0.99 * results[-2][0]:
            break                       # another mode didn't help: more won't either
    # the fewest modes within reach of the best: each mode must earn its place
    best = None
    if results:
        pen = [c * (1 / IMPROVE) ** i for i, (c, _, _) in enumerate(results)]
        best = int(np.argmin(pen))
    if best is None:
        return None
    cost, s, _ = results[best]
    cs, r = prob_all.solve(s)

    def db_err(model):
        # in dB against the level envelope, weighted as the fit
        num = den = 0.0
        for z, w, m in zip(prob_all.zs, prob_all.ws, model):
            e = np.abs(20 * np.log10(np.abs(m) + 1e-12) - 20 * np.log10(np.abs(z) + 1e-12))
            ww = (w * np.abs(z)) ** 2
            num += float(np.sum(ww * np.minimum(e, 30)))
            den += float(np.sum(ww))
        return num / max(den, 1e-30)
    mod = [basis(s, t, prob_all.L, tau_, d_) @ c for t, c, tau_, d_ in zip(prob_all.ts, cs, prob_all.tau, prob_all.d)]
    err_db = db_err(mod)
    legacy_db = db_err(legacy(meta, prob_all)) if legacy else None
    cost = float(np.sum(np.abs(r) ** 2))
    ref = float(np.sum(np.concatenate([np.abs(z * w) ** 2 for z, w in zip(zs, ws)])))
    amps = [[None, None] for _ in layers]
    for i, v in enumerate(meta):
        amps[v][0], amps[v][1] = cs[2 * i], cs[2 * i + 1]
    return {"s": s, "amps": amps, "err": cost / max(ref, 1e-30), "layers": meta, "err_db": err_db, "legacy_db": legacy_db}


def refine_long(sig, f0, partials):
    """Each partial's peak in a 3 s spectrum (from 50 ms), followed up the series: each is looked
    for where the partial below it was found, relative to analyze.py's stiff-string line (within
    ±0.3 f0 of that), and taken where it stands clear (15 dB over the median around it). In the
    bass the line drifts away from the partials (A0's are 38 cents sharp of it by the 15th, and
    more above): searched around the line itself, partials beyond ±0.4 f0 weren't found, or
    their neighbour was found in their place."""
    start = int(0.05 * SR)
    n = min(len(sig) - start, int(3.0 * SR))
    spec = np.abs(np.fft.rfft(sig[start:start + n] * np.hanning(n), 1 << 21))
    freqs = np.fft.rfftfreq(1 << 21, 1 / SR)
    db = 20 * np.log10(spec + 1e-12)
    fnew, measured = {}, {}
    ratio = 1.0                     # found / line, from the last partial found
    for p in sorted(partials, key=lambda p: p["n"]):
        fn = p["f"] * ratio
        w = 0.3 * f0
        lo, hi = np.searchsorted(freqs, [fn - w, fn + w])
        if hi <= lo + 4:
            continue
        i = lo + int(np.argmax(spec[lo:hi]))
        if lo + 1 < i < hi - 2 and db[i] - np.median(db[lo:hi]) > 15:
            a, b, c = db[i - 1], db[i], db[i + 1]
            q = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0
            f = float(freqs[i] + q * (freqs[1] - freqs[0]))
            fnew[p["n"]] = f
            measured[p["n"]] = True
            ratio = f / p["f"]
    return fnew, measured


def merge_twins(modes):
    """Two modes with the same pole are one mode (the fit split it in two, nearly cancelling): add
    their amplitudes. In the engine, such a pair at +29 dB, with phases kept to 1.4° and levels to
    0.01 dB, no longer cancelled, and A0's partials 21-40 came out 16 dB loud at the strike."""
    out = []
    for m in modes:
        twin = next((o for o in out if abs(o["df"] - m["df"]) < 0.02 and
                     abs(o["decay"] - m["decay"]) < 0.02 * max(abs(m["decay"]), 1.0)), None)
        if twin is None:
            out.append({k: (v if not isinstance(v, list) else [list(x) for x in v]) for k, v in m.items()})
            continue
        for v in range(len(m["levels"])):
            for c in (0, 1):
                z = (10 ** (twin["levels"][v][c] / 20) * np.exp(1j * twin["phases"][v][c]) +
                     10 ** (m["levels"][v][c] / 20) * np.exp(1j * m["phases"][v][c]))
                twin["levels"][v][c] = float(20 * np.log10(abs(z) + 1e-15)) if max(twin["levels"][v][c], m["levels"][v][c]) > -190 else -200.0
                twin["phases"][v][c] = float(np.angle(z))
    return out


def cancelling(modes, t=0.05):
    """How far the modes' largest amplitude stands over their sum, in dB (median over layers and
    mics, at time t): large means they nearly cancel, which the engine can't reproduce."""
    r = []
    for v in range(len(modes[0]["levels"])):
        for c in (0, 1):
            zs = [10 ** (m["levels"][v][c] / 20) * np.exp(1j * m["phases"][v][c] + (m["decay"] / DB + 2j * np.pi * m["df"]) * t)
                  for m in modes if m["levels"][v][c] > -190]
            if zs:
                r.append(20 * np.log10(max(abs(z) for z in zs) / (abs(sum(zs)) + 1e-15)))
    return float(np.median(r)) if r else 0.0


def onset_for(onsets, key):
    o = onsets.get(key)
    if not o:
        return [0.004] * 16, [0.002] * 16
    good = [t for t, e in zip(o["tau"], o["err"]) if e < 0.25]
    tau = float(np.clip(np.median(good) if good else np.median(o["tau"]), 0.001, 0.015))
    d = [dl if e < 0.25 else 0.002 for dl, e in zip(o["delay"], o["err"])]
    return [tau] * 16, d


def analyze_key(job):
    root, k, onsets = job
    cache = Path(tempfile.gettempdir()) / "piano-modes" / f"{k['key']}.json"
    if cache.exists():
        r = json.load(open(cache))
        return k["key"], {int(n): v for n, v in r.items()}
    key, out = analyze_key_(root, k, onsets)
    cache.parent.mkdir(exist_ok=True)
    cache.write_text(json.dumps(out))
    return key, out


def analyze_key_(root, k, onsets):
    name = k["name"]
    layers = [A.load(root / "Samples" / f"{name}v{v}.flac") for v in range(1, A.LAYERS + 1)]
    tau, d = onset_for(onsets, k["key"])
    # where each partial really is: a partial heterodyned a few Hz off its peak is seen only through
    # the window's skirt (C1's 15th partial is 7 Hz, 25 cents, above the stiff-string line).
    # analyze.py found the treble's from the first 0.3 s (A5 up); below, from a long spectrum here.
    L_ = min(len(x) for x, _ in layers[-4:])
    mono = sum(x[:L_].mean(1) for x, _ in layers[-4:])
    fnew, measured = ({}, {}) if k["key"] >= 81 else refine_long(mono, k["f0"], k["partials"])
    out = {}
    for p in k["partials"]:
        if measured.get(p["n"]):
            p["f"] = fnew[p["n"]]
        def legacy(meta, prob, p=p):
            # analyze.py's level and decay shape at the fit's points (magnitude only)
            out = []
            for i, v in enumerate(meta):
                for ch in (0, 1):
                    out.append(10 ** ((p["levels"][v][ch] + A.shape_db(A.shape_at(p, v), prob.ts[2 * i + ch])) / 20))
            return out
        r = fit_partial(layers, p["f"], k["window"], tau, d, k["f0"], (p["s1"], p["s2"], p.get("beat", 0.0)), legacy)
        # the modes must describe the partial better than the decay shape does, or it keeps that
        if r is None or (r["legacy_db"] is not None and r["err_db"] >= r["legacy_db"]):
            continue
        modes = []
        for m, sm in enumerate(r["s"]):
            lv, ph = [], []
            for v in range(A.LAYERS):
                lvv, phv = [], []
                for ch in range(2):
                    c = r["amps"][v][ch]
                    if c is None:
                        lvv.append(-200.0)
                        phv.append(0.0)
                    else:
                        lvv.append(float(20 * np.log10(abs(c[m]) + 1e-15)))
                        phv.append(float(np.angle(c[m])))
                lv.append(lvv)
                ph.append(phv)
            modes.append({"decay": float(sm.real * DB), "df": float(sm.imag / (2 * np.pi)), "levels": lv, "phases": ph})
        modes = merge_twins(modes)
        if cancelling(modes) > 12:
            continue                     # modes in near-opposition: the engine couldn't play them
        out[p["n"]] = {"f": p["f"], "f_measured": bool(p.get("f_measured") or measured.get(p["n"])),
                       "modes": modes, "fit_err": r["err"], "fit_layers": r["layers"],
                       "fit_db": round(r["err_db"], 2), "legacy_db": round(r["legacy_db"], 2)}
    nm = np.mean([len(v["modes"]) for v in out.values()]) if out else 0
    print(f"{name:4s} {len(out):3d} of {len(k['partials'])} partials, {nm:.1f} modes each, dB error "
          f"{np.median([v['fit_db'] for v in out.values()]) if out else 0:.2f} "
          f"(decay shapes: {np.median([v['legacy_db'] for v in out.values()]) if out else 0:.2f})", flush=True)
    return k["key"], out


def main():
    root, path = Path(sys.argv[1]), Path(sys.argv[2])
    args = sys.argv[3:]
    procs = 2
    if "--procs" in args:
        i = args.index("--procs")
        procs = int(args[i + 1])
        args = args[:i] + args[i + 2:]
    data = json.load(open(path))
    keys = [int(x) for x in args[0].split(",")] if args else [k["key"] for k in data["keys"]]
    opath = path.parent / "salamander_onset.json"
    onsets = {o["key"]: o for o in json.load(open(opath))["keys"]} if opath.exists() else {}
    jobs = [(root, k, onsets) for k in data["keys"] if k["key"] in keys]
    with Pool(procs) as pool:
        res = dict(pool.map(analyze_key, jobs, chunksize=1))
    for k in data["keys"]:
        if k["key"] not in res:
            continue
        # every partial where it really is, modes or not (one left at the stiff-string line can sit
        # on a neighbour's peak in the bass, and double it)
        if k["key"] < 81:
            layers = [A.load(root / "Samples" / f"{k['name']}v{v}.flac") for v in range(13, A.LAYERS + 1)]
            L_ = min(len(x) for x, _ in layers)
            fnew, _ = refine_long(sum(x[:L_].mean(1) for x, _ in layers), k["f0"], k["partials"])
        else:
            fnew = {}
        for p in k["partials"]:
            p.pop("modes", None)
            if p["n"] in fnew:
                p["f"], p["f_measured"] = fnew[p["n"]], True
            r = res[k["key"]].get(p["n"])
            if r:
                p.update({**r, "modes": rounded(r["modes"])})
    path.write_text(json.dumps(data))


def rounded(modes):
    """Modes with their numbers rounded as far as the header keeps them (0.01 dB, ~0.001 rad)."""
    return [{"decay": round(m["decay"], 4), "df": round(m["df"], 5),
             "levels": [[round(x, 2) for x in v] for v in m["levels"]],
             "phases": [[round(x, 4) for x in v] for v in m["phases"]]} for m in modes]


if __name__ == "__main__":
    main()
