#!/usr/bin/env python3
"""The hammer's surface as the engine plays it: a few curves fitted to the simulated hammer.

`tools/hammer_sim.py` fitted this piano's felt per key. From A0 to D#5, where that fit is good,
the force's spectrum (dB against its level at low frequencies) is, within a few dB:

    felt(f)  = −12 · (f / f12)^γ             f12 and γ per layer; f12 rising a little up the keyboard, γ falling
    rigid(f) = −10 · log10(1 + (f / fr)²)    a surface that doesn't give: one corner, the mass's
    mass(f)  = 20 · log10(μ · √((1 + (f / fm)²) / (1 + (μ f / fm)²)))    the hammer's mass × μ

The felt falls like a cliff (a smooth pulse), the rigid hammer at 6 dB per octave from a corner
its mass sets against the string, and a hammer μ times as heavy gives the partials under that
corner μ times as much and leaves those above it. The engine's Edge goes from felt to rigid, its
Weight is the mass, its Core is Edge from a velocity up (docs/MODEL.md, The hammer's surface).

One f12 and γ per layer for every key, each with one slope along the keyboard, and one fr and fm
line: per-key values would make neighbouring keys answer the controls unevenly, and above D#5
there is no good fit to take them from.

    python3 -I tools/analyze_surface.py        → tools/salamander_surface.json (a few seconds)
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hammer_sim as H  # noqa: E402

TOP_KEY = 75             # D#5: the simulated felt's fit is poor above
GRID = np.geomspace(30.0, 16000.0, 121)
GAMMA_MIN, GAMMA_MAX = 0.7, 2.0      # the engine keeps γ within these (Synth.cpp), where the keys' fits lie


def rms(x):
    return float(np.sqrt(np.mean(np.square(x))))


def mass_db(f, fm, mu):
    return 20 * np.log10(mu * np.sqrt((1 + (f / fm) ** 2) / (1 + (mu * f / fm) ** 2)))


def key_curves(k, fit):
    st, fine = H.key_string(k), H.key_string(k, fmax=60000)
    felt, ratio, v = H.Surface(fit["K"], fit["p"], fit["eta"]), fit["ratio"], np.array(fit["v"])
    g = GRID[(GRID > 0.7 * k["f0"]) & (GRID < min(16000.0, 0.9 * st.f[-1]))]
    r = H.simulate(st, felt, ratio, v)
    A = H.spectrum(r["F"], r["dt"], g)
    A0 = A - A[:, [0]]
    f12, gamma, err = [], [], []
    for l in range(16):
        use = A0[l] > -50
        s = least_squares(lambda x: (-12 * (g / np.exp(x[0])) ** x[1] - A0[l])[use], [np.log(1500), 1.3],
                          bounds=([np.log(100), 0.5], [np.log(20000), 6]))
        f12.append(float(np.exp(s.x[0])))
        gamma.append(float(s.x[1]))
        err.append(rms(s.fun))
    r = H.simulate(fine, H.rigid(fine, ratio), ratio, [H.V_REF], fs=768000.0)
    B = H.spectrum(r["F"], r["dt"], g)[0]
    B -= B[0]
    lo = g < 8000
    s = least_squares(lambda x: (-10 * np.log10(1 + (g / np.exp(x[0])) ** 2) - B)[lo], [np.log(600)])
    fr, fr_err = float(np.exp(s.x[0])), rms(s.fun)
    fms = []
    for mu in (0.5, 2.0):
        r = H.simulate(st, H.Surface(felt.K / mu, felt.p, felt.eta), ratio * mu, [H.V_REF])
        C = H.spectrum(r["F"], r["dt"], g)[0] + 20 * np.log10(mu) - A[H.REF]
        use = A0[H.REF] > -50
        s = least_squares(lambda x: (mass_db(g, np.exp(x[0]), mu) - C)[use], [np.log(500)])
        fms.append((float(np.exp(s.x[0])), rms(s.fun)))
    return {"key": k["key"], "name": k["name"], "f12": f12, "gamma": gamma, "felt_rms": err, "rigid_hz": fr,
            "rigid_rms": fr_err, "mass_hz": float(np.sqrt(fms[0][0] * fms[1][0])), "mass_rms": max(fms[0][1], fms[1][1])}


def main():
    fits = {f["key"]: f for f in json.load(open(H.TOOLS / "salamander_hammer_sim.json"))["keys"]}
    keys = [k for k in H.load_keys() if k["key"] in fits and k["key"] <= TOP_KEY]
    rows = []
    for k in keys:
        rows.append(key_curves(k, fits[k["key"]]))
        r = rows[-1]
        print(f"{r['key']:3d} {r['name']:4s} felt f12 {r['f12'][3]:5.0f} / {r['f12'][12]:5.0f} Hz, γ {r['gamma'][3]:.2f} / {r['gamma'][12]:.2f} "
              f"(layers 4 / 13; rms {np.mean(r['felt_rms']):.1f} dB)   rigid {r['rigid_hz']:5.0f} Hz ({r['rigid_rms']:.1f} dB)   "
              f"mass {r['mass_hz']:5.0f} Hz ({r['mass_rms']:.1f} dB)", flush=True)
    kk = np.array([r["key"] for r in rows], float)
    F = np.log(np.array([r["f12"] for r in rows]))
    G = np.array([r["gamma"] for r in rows])
    # one slope along the keyboard for every layer, then each layer's level at C4
    slope = float(np.mean([np.polyfit(kk, F[:, l], 1)[0] for l in range(16)]))
    f12 = np.exp(np.median(F - slope * (kk[:, None] - 60), axis=0))
    f12 = np.maximum.accumulate(f12)                       # a harder strike is never a longer contact
    gslope = float(np.mean([np.polyfit(kk, G[:, l], 1)[0] for l in range(16)]))     # the felt's cliff is less steep up the keyboard
    gamma = np.minimum.accumulate(np.median(G - gslope * (kk[:, None] - 60), axis=0))
    rigid = float(np.exp(np.median(np.log([r["rigid_hz"] for r in rows]))))
    ml = np.polyfit(kk, np.log([r["mass_hz"] for r in rows]), 1)
    out = {"f12": f12.round(1).tolist(), "gamma": gamma.round(3).tolist(), "f12_per_key": slope, "gamma_per_key": gslope, "rigid_hz": round(rigid, 1),
           "mass_hz_c4": round(float(np.exp(np.polyval(ml, 60))), 1), "mass_per_key": float(ml[0]), "keys": rows}
    # how far the shared curves are from each key's own simulation, where the partials are played
    res = [rms([-12 * (g / (f12[l] * np.exp(slope * (r["key"] - 60)))) ** np.clip(gamma[l] + gslope * (r["key"] - 60), GAMMA_MIN, GAMMA_MAX)
                + 12 * (g / r["f12"][l]) ** r["gamma"][l]
                for g in (500.0, 1000.0, 2000.0)]) for r in rows for l in range(16)]
    out["shared_rms"] = rms(res)
    print(f"\nfelt: f12 at C4 {f12[0]:.0f} … {f12[12]:.0f} … {f12[15]:.0f} Hz (layers 1, 13, 16), × {np.exp(12 * slope):.3f} per octave of keys; "
          f"γ at C4 {gamma[0]:.2f} … {gamma[12]:.2f} … {gamma[15]:.2f}, {12 * gslope:+.3f} per octave of keys")
    print(f"rigid: {rigid:.0f} Hz (keys: {min(r['rigid_hz'] for r in rows):.0f}-{max(r['rigid_hz'] for r in rows):.0f})")
    print(f"mass: {out['mass_hz_c4']:.0f} Hz at C4, × {np.exp(12 * ml[0]):.3f} per octave of keys")
    print(f"shared felt curves against each key's own at 0.5, 1 and 2 kHz: {out['shared_rms']:.1f} dB rms")
    json.dump(out, open(H.TOOLS / "salamander_surface.json", "w"), indent=1)


if __name__ == "__main__":
    main()
