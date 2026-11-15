#!/usr/bin/env python3
"""A simulated hammer on a stiff string: what a hammer's surface does to the force on the string.

Offline only: the engine doesn't run this. It answers, before anything is built, which
properties of a hammer's surface change the sound enough to earn a control (docs/MODEL.md,
The hammer, simulated).

The string is its modes (hinged ends, the measured f0 and inharmonicity B of a key):

    q_n'' + 2 σ_n q_n' + ω_n² q_n = (2 / Ms) φ_n F(t)       φ_n = sin(nπx) · sinc(n w)

(x: the strike point; w: the contact's width, both as fractions of the string; Ms: the string's
mass). The hammer is a mass m that arrives at speed v and presses on the string through its
surface, compressed by ξ = y_hammer − y_string(x):

    m y'' = −F        F = K ξ^p · (1 + η ξ')    while ξ > 0 (never negative), 0 otherwise

p is the surface's character: 1 is a plain spring (wood, metal, hard plastic), felt is 2-4 (it
stiffens as it is pressed), η is its loss (Hunt and Crossley's form). A two-layer surface (thin
felt over a hard core) has a second, stiffer law past a depth d. Each partial ends up with
|F̂(ω_n)| · φ_n / ω_n, so what the surface does to the sound is the change in the force's
spectrum |F̂(f)|: that is what the engine's Hardness already moves, as one corner.

Time stepping is a split step: half a kick from the force, the modes' exact free motion for one
step (so their frequencies and decays are exact at any rate), the other half kick. `check`
verifies it: energy, the rigid hammer's analytic pulse, a plain spring's indifference to speed,
and convergence with the sample rate.

    python3 -I tools/hammer_sim.py check
    python3 -I tools/hammer_sim.py fit      [--keys 21,24,…] [--procs 2]   → tools/salamander_hammer_sim.json
    python3 -I tools/hammer_sim.py surfaces [--keys …]                      (each change against the felt; half a minute)
    python3 -I tools/hammer_sim.py report                                    (tables from both)

How audible each change would be: tools/measure/surfaces.py (needs a build).
"""
import argparse
import json
import sys
import tempfile
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

TOOLS = Path(__file__).resolve().parent
SURFACES = Path(tempfile.gettempdir()) / "piano-hammer" / "surfaces.json"     # 0.5 MB, rebuilt in half a minute
REF = 12                 # 0-based layer 13: what the on-screen keyboard's velocity 100 plays
V_REF = 2.5              # the hammer's speed at layer 13, m/s (a convention: only K·v^(p−1) matters)
NFFT = 1 << 16

# Nominal speaking lengths (m) by MIDI key, for the contact's width as a fraction of the string.
# A 2 m grand's, roughly: not measured here. Only the width uses them.
LENGTHS = [(21, 1.45), (33, 1.30), (45, 1.00), (57, 0.70), (60, 0.62), (72, 0.33), (84, 0.17), (96, 0.09), (108, 0.05)]
CONTACT_MM = 5.0         # nominal length of felt on the string


def string_length(key):
    ks, ls = zip(*LENGTHS)
    return float(np.exp(np.interp(key, ks, np.log(ls))))


class String:
    """A key's string as modes. ratio: hammer mass / string mass (per string of the unison)."""

    def __init__(self, f0, B, x, width=0.0, fmax=24000.0, nmax=600):
        n = np.arange(1, nmax + 1)
        f = n * f0 * np.sqrt(1 + B * n * n)
        keep = f < fmax
        self.n, self.f = n[keep], f[keep]
        self.w = 2 * np.pi * self.f
        self.phi = np.sin(np.pi * self.n * x) * np.sinc(self.n * width)
        self.sigma = 1.0 + 2e-9 * self.w ** 2          # s⁻¹: nothing over a few ms
        self.f0, self.B, self.x, self.width = f0, B, x, width


class Surface:
    """F = K ξ^p (1 + η ξ'), with an optional harder core from depth d: + K2 (ξ − d)^p2."""

    def __init__(self, K, p, eta=0.0, d=None, K2=0.0, p2=1.0):
        self.K, self.p, self.eta, self.d, self.K2, self.p2 = K, p, eta, d, K2, p2

    def force(self, xi, dxi):
        c = np.maximum(xi, 0.0)
        F = self.K * c ** self.p
        if self.d is not None:
            F = F + self.K2 * np.maximum(xi - self.d, 0.0) ** self.p2
        if self.eta:
            F = F * np.maximum(1 + self.eta * dxi, 0.0)
        return F

    def scaled(self, **kw):
        s = Surface(self.K, self.p, self.eta, self.d, self.K2, self.p2)
        for k, v in kw.items():
            setattr(s, k, v)
        return s


def rigid(st, ratio, fc=40000.0):
    """A surface that doesn't give, as far as the time step allows: a spring whose resonance with
    the hammer and the string's modes together is at fc."""
    return Surface((2 * np.pi * fc) ** 2 / (1 + 2 * ratio * (st.phi ** 2).sum()), 1.0)


def simulate(st, surf, ratio, v, fs=192000.0, tmax=0.03, y0=None, u0=None, energy=False):
    """Strike string `st` with unit-mass hammers at the speeds `v` (array). Forces are per unit
    hammer mass (K is K / m), so the string's mass is 1 / ratio. Returns F(t) per speed (also per
    unit hammer mass), the time step, and the hammers' speeds on leaving."""
    v = np.atleast_1d(np.asarray(v, float))
    V, N = len(v), len(st.f)
    dt = 1.0 / fs
    g = 2.0 * ratio * st.phi                       # (2 / Ms) φ_n with m = 1
    c, s = np.cos(st.w * dt), np.sin(st.w * dt)
    dec = np.exp(-st.sigma * dt)
    q = np.zeros((V, N)) if y0 is None else np.array(y0, float)   # modal displacement
    u = np.zeros((V, N)) if u0 is None else np.array(u0, float)   # modal velocity / ω
    yh = np.zeros(V) + (q @ st.phi)
    vh = v.copy()
    steps = int(tmax * fs)
    F = np.zeros((V, steps))
    reach = np.zeros(V)
    f = surf.force(yh - q @ st.phi, vh - (u * st.w) @ st.phi)
    e0 = None
    if energy:
        e0 = 0.5 * vh ** 2 + (0.25 / ratio) * ((st.w * q) ** 2 + (st.w * u) ** 2).sum(1)
    last = steps
    for i in range(steps):
        F[:, i] = f
        u += (0.5 * dt) * f[:, None] * (g / st.w)
        vh -= (0.5 * dt) * f
        q, u = dec * (c * q + s * u), dec * (c * u - s * q)
        yh += vh * dt
        ys = q @ st.phi
        f = surf.force(yh - ys, vh - (u * st.w) @ st.phi)
        u += (0.5 * dt) * f[:, None] * (g / st.w)
        vh -= (0.5 * dt) * f
        reach = np.maximum(reach, np.abs(ys))
        # every hammer gone for good: moving away, and further off than the string has ever swung
        if i % 16 == 0 and np.all(vh < 0) and np.all(ys - yh > 2 * reach + 1e-6):
            last = i + 1
            break
    # an explicit step can't follow a contact stiffer than the rate allows: the hammer would leave faster than it came
    stable = bool(np.all(np.isfinite(vh)) and np.all(np.abs(vh) <= 1.001 * np.abs(v) + 1e-12))
    out = {"F": F[:, :last], "dt": dt, "v_out": vh.copy(), "stable": stable}
    if energy:
        e1 = 0.5 * vh ** 2 + (0.25 / ratio) * ((st.w * q) ** 2 + (st.w * u) ** 2).sum(1)
        out["energy"] = (e0, e1)
    return out


def spectrum(F, dt, f):
    """|F̂| (dB) of each pulse at the frequencies f."""
    S = np.abs(np.fft.rfft(F, NFFT, axis=1)) * dt
    fb = np.fft.rfftfreq(NFFT, dt)
    return np.array([20 * np.log10(np.maximum(np.interp(f, fb, s), 1e-30)) for s in S])


def contact_ms(F, dt):
    """How long each pulse lasts (first to last sample above 1 % of its peak), ms."""
    out = []
    for r in F:
        on = np.nonzero(r > 0.01 * r.max())[0]
        out.append((on[-1] - on[0] + 1) * dt * 1e3 if len(on) else 0.0)
    return np.array(out)


# ---------------------------------------------------------------------------------------------
# check: the scheme against what is known
# ---------------------------------------------------------------------------------------------

def check():
    ok = True

    def report(name, value, good):
        nonlocal ok
        ok = ok and good
        print(f"  {'ok ' if good else 'BAD'} {name}: {value}")

    print("energy (a lossless string, felt p = 2.5: the hammer's energy in = the hammer's + the string's out)")
    st = String(130.8, 1e-4, 0.12, fmax=40000)
    st.sigma[:] = 0
    r = simulate(st, Surface(2e11, 2.5), 0.4, [0.5, 2.0, 5.0], fs=192000, tmax=0.02, energy=True)
    e0, e1 = r["energy"]
    report("worst relative error", f"{np.abs(e1 / e0 - 1).max():.1e}", np.abs(e1 / e0 - 1).max() < 2e-3)

    print("a rigid hammer on an ideal string, before any reflection returns: F = 2Z v e^(−2Zt/m)")
    f0, ratio = 65.4, 0.3
    st = String(f0, 0.0, 0.5, fmax=180000, nmax=4000)       # struck in the middle: reflections at 1/(2 f0)
    st.sigma[:] = 0
    fs = 1536000.0
    r = simulate(st, rigid(st, ratio, 150000.0), ratio, [1.0], fs=fs, tmax=0.004)
    t = np.arange(r["F"].shape[1]) / fs
    tau = ratio / (4 * f0)                                  # m / 2Z, with Z = 2 f0 Ms and Ms = m / ratio
    want = (1.0 / tau) * np.exp(-t / tau)
    win = (t > 0.0003) & (t < 0.0035)
    k = int(0.0002 * fs)                                    # the stiff spring rings: smooth over 0.2 ms
    sm = np.convolve(r["F"][0], np.ones(k) / k, "same")
    err = np.abs(sm[win] / want[win] - 1).max()
    report(f"time constant m/2Z = {tau * 1e3:.2f} ms; worst error 0.3-3.5 ms", f"{100 * err:.1f} %", err < 0.05)

    print("a plain spring (p = 1) is a linear system: the pulse's shape can't depend on speed")
    st = String(261.6, 3e-4, 0.12, width=0.008)
    r = simulate(st, Surface(4e8, 1.0), 0.25, [0.3, 3.0])
    n = min(len(r["F"][0]), len(r["F"][1]))
    d = np.abs(r["F"][0][:n] * 10 - r["F"][1][:n]).max() / r["F"][1].max()
    report("soft × 10 against hard", f"{d:.1e}", d < 1e-6)

    print("felt brightens with speed, and the contact shortens as v^(−(p−1)/(p+1)) on a heavy string")
    st = String(261.6, 3e-4, 0.12, width=0.008)
    p = 3.0
    r = simulate(st, Surface(1e15, p), 0.002, [0.5, 4.0])   # a string 500 times the hammer: almost a wall
    T = contact_ms(r["F"], r["dt"])
    slope = np.log(T[1] / T[0]) / np.log(8)
    report(f"exponent (theory {-(p - 1) / (p + 1):.3f})", f"{slope:.3f}", abs(slope + (p - 1) / (p + 1)) < 0.03)

    print("convergence: C4 felt at 192 kHz against 768 kHz, partial levels to 10 kHz")
    st = String(261.6, 3e-4, 0.12, width=0.008)
    fq = st.f[st.f < 10000]
    a = simulate(st, Surface(3e12, 2.5, 1e-4), 0.25, [0.5, 2.5, 5.0], fs=192000)
    b = simulate(st, Surface(3e12, 2.5, 1e-4), 0.25, [0.5, 2.5, 5.0], fs=768000)
    d = np.abs(spectrum(a["F"], a["dt"], fq) - spectrum(b["F"], b["dt"], fq))
    lv = spectrum(b["F"], b["dt"], fq)
    d = d[lv > lv.max() - 60]
    report("worst difference within 60 dB of the top", f"{d.max():.2f} dB", d.max() < 0.5)
    print("all checks pass" if ok else "CHECKS FAILED")
    return ok


# ---------------------------------------------------------------------------------------------
# fit: the felt the recordings were made with
# ---------------------------------------------------------------------------------------------

def load_keys():
    model = json.load(open(TOOLS / "salamander.json"))
    point = {k["key"]: k["x"] for k in json.load(open(TOOLS / "salamander_point.json"))["keys"]}
    corner = {k["key"]: k for k in json.load(open(TOOLS / "salamander_hammer.json"))["keys"]}
    keys = []
    for k in model["keys"]:
        ps = [p for p in k["partials"] if max(np.array(p["levels"]).mean(1)) > -96]
        if len(ps) < 4:
            continue
        f = np.array([p["f"] for p in ps])
        L = np.array([np.array(p["levels"]).mean(1) for p in ps])
        use = L[:, REF] > L[:, REF].max() - 50
        f, L = f[use], L[use]
        D = L - L[:, [REF]]
        keys.append({"key": k["key"], "name": k["name"], "f0": k["f0"], "B": k["B"], "x": point.get(k["key"], 0.12),
                     "f": f, "D": D, "vrel": 10 ** (D[:2].mean(0) / 20), "corner": corner.get(k["key"])})
    # the top keys' B fits fail here and there (make_header repairs them the same way): keep B rising
    ks = np.array([k["key"] for k in keys])
    lb = np.log([k["B"] for k in keys])
    mid = (ks >= 57) & (ks <= 93)
    line = np.polyfit(ks[mid], lb[mid], 1)
    for k in keys:
        if k["key"] > 93:
            k["B"] = float(np.exp(np.polyval(line, k["key"])))
    return keys


def key_string(k, **kw):
    return String(k["f0"], k["B"], k["x"], width=CONTACT_MM * 1e-3 / string_length(k["key"]), **kw)


def layer_speeds(k, st, surf, ratio, passes=1):
    """Each layer's hammer speed: from the two lowest partials (well under the corner, they follow
    the hammer's momentum), corrected so the simulation's own two lowest match the recordings'."""
    v = V_REF * k["vrel"]
    low = k["f"][:2]
    for _ in range(passes):
        r = simulate(st, surf, ratio, v)
        S = spectrum(r["F"], r["dt"], low).mean(1)
        v = v * 10 ** ((k["D"][:2].mean(0) - (S - S[REF])) / 20)
        v = np.clip(v / v[REF] * V_REF, 0.02, 12.0)
    return v


def layer_changes(k, st, surf, ratio):
    """The simulation's partial levels per layer against layer 13's, as the recordings' D."""
    v = layer_speeds(k, st, surf, ratio)
    r = simulate(st, surf, ratio, v)
    S = spectrum(r["F"], r["dt"], k["f"]).T
    return S - S[:, [REF]], v, r


def residual(k, st, surf, ratio):
    S, v, _ = layer_changes(k, st, surf, ratio)
    E = k["D"] - S
    E = np.delete(E, REF, axis=1)
    return (E - E.mean(0)).T.ravel()


def start_values(key):
    """Felt from the literature's order of magnitude (K / m rises steeply up the keyboard, p from
    about 2.3 to 3, the hammer lighter than the bass strings and heavier than the treble's)."""
    lk = np.interp(key, [36, 60, 96], [np.log(8e10), np.log(1.5e12), np.log(4.5e14)])
    p = float(np.interp(key, [36, 60, 96], [2.3, 2.5, 3.0]))
    lr = np.interp(key, [21, 36, 60, 96, 108], [np.log(0.04), np.log(0.14), np.log(0.25), np.log(1.6), np.log(3.0)])
    return lk, p, lr


def fit_key(k):
    st = key_string(k)
    lk0, p0, lr0 = start_values(k["key"])

    def unpack(x):
        # K is carried as the stiffness at the reference compression 0.5 mm, so p and K don't trade
        p = x[1]
        K = np.exp(x[0]) / (5e-4 ** p)
        return Surface(K, p, eta=np.exp(x[3])), np.exp(x[2])

    def res(x):
        surf, ratio = unpack(x)
        return residual(k, st, surf, ratio)

    best = None
    base = lk0 + p0 * np.log(5e-4)
    for p, off in ((p0, 0.0), (3.5, 0.0)):
        x0 = [base + off, p, lr0, np.log(2e-4)]
        lo = [base - 7, 1.5, lr0 - 1.6, np.log(1e-6)]
        hi = [base + 7, 6.0, lr0 + 1.6, np.log(3e-3)]
        s = least_squares(res, x0, bounds=(lo, hi), diff_step=0.03, max_nfev=40, x_scale=[1, 0.5, 0.5, 1])
        if best is None or s.cost < best.cost:
            best = s
    surf, ratio = unpack(best.x)
    S, v, r = layer_changes(k, st, surf, ratio)
    flat = np.delete(k["D"], REF, axis=1)
    flat = (flat - flat.mean(0)).ravel()
    rms_flat, rms_sim = float(np.sqrt(np.mean(flat ** 2))), float(np.sqrt(np.mean(best.fun ** 2)))
    c = k["corner"]
    out = {"key": k["key"], "name": k["name"], "K": surf.K, "p": float(surf.p), "eta": float(surf.eta), "ratio": float(ratio),
           "v": v.tolist(), "contact_ms": contact_ms(r["F"], r["dt"]).tolist(), "restitution": (-r["v_out"] / v).tolist(),
           "rms_flat": rms_flat, "rms_sim": rms_sim, "rms_corner": c["rms_model"] if c else None}
    print(f"{k['key']:3d} {k['name']:4s} p {surf.p:4.2f}  hammer/string {ratio:5.2f}  η {surf.eta * 1e3:5.2f} ms/m  contact "
          f"{out['contact_ms'][0]:4.2f}/{out['contact_ms'][REF]:4.2f}/{out['contact_ms'][15]:4.2f} ms (layers 1/13/16)  "
          f"rms {rms_flat:4.1f} → {rms_sim:4.2f} dB (one corner: {c['rms_model'] if c else float('nan'):4.2f})", flush=True)
    return out


def cmd_fit(a):
    keys = load_keys()
    if a.keys:
        want = {int(x) for x in a.keys.split(",")}
        keys = [k for k in keys if k["key"] in want]
    with Pool(a.procs) as pool:
        fits = pool.map(fit_key, keys, chunksize=1)
    path = TOOLS / "salamander_hammer_sim.json"
    old = {}
    if a.keys and path.exists():
        old = {k["key"]: k for k in json.load(open(path))["keys"]}
    old.update({f["key"]: f for f in fits})
    json.dump({"v_ref": V_REF, "contact_mm": CONTACT_MM, "keys": [old[k] for k in sorted(old)]}, open(path, "w"), indent=1)


# ---------------------------------------------------------------------------------------------
# surfaces: one property changed at a time, from each key's fitted felt
# ---------------------------------------------------------------------------------------------

LAYERS = [3, 7, 12, 15]            # 0-based: layers 4, 8, 13, 16
GRID = np.geomspace(50.0, 16000.0, 97)


def corner_hz(st, surf, ratio, v, fs=192000.0):
    """A pulse's hardness as one number: where its spectrum first falls 12 dB under its level at
    low frequencies. (The contact's length won't do: a hammer thrown off and caught again by the
    string's reflections has no one length.)"""
    r = simulate(st, surf, ratio, [v], fs=fs)
    if not r["stable"]:
        return float("inf")
    S = np.abs(np.fft.rfft(r["F"][0], NFFT)) + 1e-30
    fb = np.fft.rfftfreq(NFFT, r["dt"])
    S = 20 * np.log10(S / S[1])
    under = np.nonzero(S < -12.0)[0]
    return float(fb[under[0]]) if len(under) else float(fb[-1])


def variants(k, fit, st):
    """name → (string, surface, ratio). Each changes one thing about the fitted felt."""
    felt = Surface(fit["K"], fit["p"], fit["eta"])
    ratio = fit["ratio"]
    fine = key_string(k, fmax=60000)
    out = {"felt": (st, felt, ratio)}
    # hardness: the same felt ten times stiffer or softer
    out["felt ×10 stiffer"] = (st, felt.scaled(K=felt.K * 10), ratio)
    out["felt ÷10 softer"] = (st, felt.scaled(K=felt.K / 10), ratio)
    # character: another exponent, giving the felt's force at the compression layer 13 reaches (so
    # it is as hard as the felt there: softer below and harder above, or the other way round)
    r = simulate(st, felt, ratio, [V_REF])
    f13 = r["F"].max()
    x13 = (f13 / felt.K) ** (1 / felt.p)

    def crossing(p, **kw):
        return felt.scaled(K=f13 / x13 ** p, p=p, **kw)

    out["spring p=1 (wood, metal)"] = (st, crossing(1.0, eta=0.0), ratio)
    out["p=1.5 (leather)"] = (st, crossing(1.5), ratio)
    out["progressive, p+1.5"] = (st, crossing(fit["p"] + 1.5), ratio)
    out["no loss"] = (st, felt.scaled(eta=0.0), ratio)
    out["loss ×4"] = (st, felt.scaled(eta=min(4 * fit["eta"] + 2e-4, 0.02)), ratio)
    # the hard limit: a surface that doesn't give (the string and the hammer's mass shape the pulse)
    out["rigid (a tack)"] = (fine, rigid(fine, ratio), ratio)
    # mass, with the felt as it is
    out["hammer ×0.5 mass"] = (st, Surface(felt.K * 2, felt.p, felt.eta), ratio * 0.5)
    out["hammer ×2 mass"] = (st, Surface(felt.K / 2, felt.p, felt.eta), ratio * 2)
    out["rigid, ×0.3 mass (a light hard hammer)"] = (fine, rigid(fine, ratio * 0.3), ratio * 0.3)
    # contact width
    pt = String(k["f0"], k["B"], k["x"], width=0.0)
    out["a point (width 0)"] = (pt, felt, ratio)
    w2 = String(k["f0"], k["B"], k["x"], width=min(3 * st.width, 0.3))
    out["width ×3 (a worn, flat hammer)"] = (w2, felt, ratio)
    # two layers: the felt to the depth it reaches at layer 6, then a core 30 times stiffer
    r = simulate(st, felt, ratio, [fit["v"][5]])
    depth = (r["F"].max() / felt.K) ** (1 / felt.p)
    kcore = 30 * felt.K * felt.p * depth ** (felt.p - 1)
    out["thin felt over a hard core"] = (st, Surface(felt.K, felt.p, felt.eta, d=depth, K2=kcore, p2=1.0), ratio)
    return out


def band_means(f, db):
    """Mean change in octave bands 125 Hz - 8 kHz (centre frequencies)."""
    out = []
    for fc in (125, 250, 500, 1000, 2000, 4000, 8000):
        m = (f >= fc / np.sqrt(2)) & (f < fc * np.sqrt(2))
        out.append(float(db[m].mean()) if m.any() else None)
    return out


def corner_model(f, fc, q):
    return -10 * q * np.log10(1 + (f / fc) ** 2)


def surface_key(job):
    k, fit = job
    st = key_string(k)
    v = np.array(fit["v"])[LAYERS]
    res = {"key": k["key"], "name": k["name"], "f0": k["f0"], "layers": [l + 1 for l in LAYERS], "variants": {}}
    base = None                                             # the felt comes first
    top = min(16000.0, 0.9 * st.f[-1])
    grid = GRID[(GRID > 0.7 * k["f0"]) & (GRID < top)]
    part = st.f[(st.f < top)]
    for name, (s, surf, ratio) in variants(k, fit, st).items():
        fs = 768000.0 if s is not st and len(s.f) > len(st.f) else 192000.0
        # a lighter or heavier hammer keeps its speed (the same key press): the force per string mass is what counts
        r = simulate(s, surf, ratio, v, fs=fs)
        scale = 20 * np.log10(ratio / fit["ratio"])        # F is per unit hammer mass: back to a common scale
        S = spectrum(r["F"], r["dt"], grid) + scale
        P = spectrum(r["F"], r["dt"], part) + scale
        # the contact's width also low-passes what reaches each partial (φ_n): sinc(n w), n = f / f0
        S = S + 20 * np.log10(np.maximum(np.abs(np.sinc(grid / k["f0"] * s.width)), 1e-3))
        P = P + 20 * np.log10(np.maximum(np.abs(np.sinc(part / k["f0"] * s.width)), 1e-3))
        if name == "felt":
            base = (S, P)
        assert r["stable"], name
        entry = {"corner_hz": [corner_hz(s, surf, ratio, x, fs=fs) for x in v], "contact_ms": contact_ms(r["F"], r["dt"]).round(3).tolist(), "ratio_db": (S - base[0]).round(2).tolist(),
                 "partials_db": (P - base[1]).round(2).tolist()}
        # Can the engine's form follow it? The engine applies model(new) − model(felt) with
        # model = −10 q log10(1 + (f / fc)²), q = 2 today (12 dB per octave). Judged where the partials
        # that are played lie: both spectra within 50 dB of their low-frequency level.
        fits = []
        for li in range(len(LAYERS)):
            b0, n0 = base[0][li] - base[0][li][0], S[li] - S[li][0]
            use = (b0 > -50) & (n0 > -50)
            if use.sum() < 6:
                fits.append(None)
                continue
            g, tgt = grid[use], (n0 - b0)[use]

            def model(fc, q):
                return corner_model(g, fc, q) - corner_model(grid[0], fc, q)

            fb = least_squares(lambda x: model(np.exp(x[0]), x[1]) - b0[use], [np.log(2000), 2.0],
                               bounds=([np.log(50), 0.3], [np.log(60000), 12])).x
            rms = lambda r: float(np.sqrt(np.mean(r ** 2)))
            row = {"felt_fc": float(np.exp(fb[0])), "felt_slope": float(6 * fb[1]), "change_rms": rms(tgt)}
            # today's form: both corners free, 12 dB per octave
            t2 = least_squares(lambda x: model(np.exp(x[1]), 2.0) - model(np.exp(x[0]), 2.0) - tgt, [np.log(3000), np.log(3000)],
                               bounds=([np.log(50)] * 2, [np.log(200000)] * 2))
            row["today_rms"] = rms(t2.fun)
            # the felt's own corner and slope, the corner moved
            one = least_squares(lambda x: model(np.exp(x[0]), fb[1]) - model(np.exp(fb[0]), fb[1]) - tgt, [fb[0]],
                                bounds=([np.log(20)], [np.log(200000)]))
            row["corner_rms"] = rms(one.fun)
            two = least_squares(lambda x: model(np.exp(x[0]), x[1]) - model(np.exp(fb[0]), fb[1]) - tgt, [fb[0], fb[1]],
                                bounds=([np.log(20), 0.2], [np.log(200000), 12]))
            row["corner_slope_rms"] = rms(two.fun)
            row["fc"], row["slope"] = float(np.exp(two.x[0])), float(6 * two.x[1])
            fits.append(row)
        entry["form"] = fits
        entry["bands_db"] = [band_means(grid, d) for d in (S - base[0])]
        res["variants"][name] = entry
    res["grid"] = grid.round(1).tolist()
    res["partials"] = part.round(2).tolist()
    print(f"{k['key']:3d} {k['name']}", flush=True)
    return res


def cmd_surfaces(a):
    fits = {f["key"]: f for f in json.load(open(TOOLS / "salamander_hammer_sim.json"))["keys"]}
    keys = [k for k in load_keys() if k["key"] in fits]
    want = {int(x) for x in a.keys.split(",")} if a.keys else {24, 36, 48, 60, 72}
    jobs = [(k, fits[k["key"]]) for k in keys if k["key"] in want]
    with Pool(a.procs) as pool:
        out = pool.map(surface_key, jobs, chunksize=1)
    SURFACES.parent.mkdir(exist_ok=True)
    json.dump({"keys": out}, open(SURFACES, "w"))


def cmd_report(a):
    fits = json.load(open(TOOLS / "salamander_hammer_sim.json"))["keys"]
    print("The felt, fitted per measured key (rms of the partials' change from layer to layer, dB)")
    print("key        p   hammer/string  contact ms (layers 1 / 13 / 16)   no model → simulated felt   one corner")
    for f in fits:
        c = f["contact_ms"]
        rc = f"{f['rms_corner']:.2f}" if f["rms_corner"] else "  - "
        print(f"{f['key']:3d} {f['name']:4s} {f['p']:5.2f}   {f['ratio']:6.2f}        {c[0]:5.2f} / {c[REF]:5.2f} / {c[15]:5.2f}"
              f"             {f['rms_flat']:5.2f} → {f['rms_sim']:5.2f}            {rc}")
    lo = [f for f in fits if f["key"] <= 84 and f["rms_corner"]]
    ex = lambda f, key: 100 * (1 - f[key] ** 2 / f["rms_flat"] ** 2)
    print(f"A0-C6: the simulated felt explains {np.median([ex(f, 'rms_sim') for f in lo]):.0f} % (median; "
          f"{min(ex(f, 'rms_sim') for f in lo):.0f}-{max(ex(f, 'rms_sim') for f in lo):.0f}), one corner "
          f"{np.median([ex(f, 'rms_corner') for f in lo]):.0f} % ({min(ex(f, 'rms_corner') for f in lo):.0f}-{max(ex(f, 'rms_corner') for f in lo):.0f})")
    path = SURFACES
    if not path.exists():
        print("\n(run `surfaces` for the changes against the felt)")
        return
    S = json.load(open(path))["keys"]
    names = list(S[0]["variants"])
    print("\nHow much harder can a hammer be? The force's corner (12 dB down), felt at layers 4 / 8 / 13 / 16 and a rigid surface (Hz)")
    for k in S:
        c, r = k["variants"]["felt"]["corner_hz"], k["variants"]["rigid (a tack)"]["corner_hz"]
        print(f"  {k['name']:4s} felt " + " / ".join(f"{x:5.0f}" for x in c) + f"   rigid {np.mean(r):5.0f}   headroom at layer 13: {np.log2(np.mean(r) / c[2]):4.1f} octaves")
    felt = [k["variants"]["felt ×10 stiffer"]["form"] for k in S]
    print("\nThe felt's own force spectrum as a corner and a slope (median over the keys):")
    for li, layer in enumerate(S[0]["layers"]):
        rows = [f[li] for f in felt if f[li]]
        print(f"  layer {layer:2d}: corner {np.median([r['felt_fc'] for r in rows]):6.0f} Hz, {np.median([r['felt_slope'] for r in rows]):4.1f} dB per octave")
    print("\nEach change against the fitted felt: mean over the keys of the change in octave bands (dB), by layer")
    print("bands: 125  250  500   1k   2k   4k   8k Hz")
    print("form: rms (dB) of the change over the played partials' range, and what is left of it by: today's form (two corners,")
    print("12 dB/octave) / the felt's own slope with the corner moved / corner and slope both moved")
    for name in names[1:]:
        print(f"\n{name}")
        for li, layer in enumerate(S[0]["layers"]):
            rows = np.array([[np.nan if x is None else x for x in k["variants"][name]["bands_db"][li]] for k in S])
            with np.errstate(all="ignore"):
                m = np.nanmean(rows, 0)
            fr = [k["variants"][name]["form"][li] for k in S if k["variants"][name]["form"][li]]
            f = [np.mean([r[key] for r in fr]) for key in ("change_rms", "today_rms", "corner_rms", "corner_slope_rms")]
            print(f"  layer {layer:2d}: " + " ".join(f"{x:5.1f}" for x in m) + f"   form {f[0]:4.1f}: {f[1]:4.1f} / {f[2]:4.1f} / {f[3]:4.1f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["check", "fit", "surfaces", "report"])
    ap.add_argument("--keys", default="")
    ap.add_argument("--procs", type=int, default=2)
    a = ap.parse_args()
    if a.cmd == "check":
        sys.exit(0 if check() else 1)
    {"fit": cmd_fit, "surfaces": cmd_surfaces, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()
