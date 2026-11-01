#!/usr/bin/env python3
"""Turn tools/salamander.json (from analyze.py) into src/PianoData.h.

    python3 -I tools/make_header.py tools/salamander.json src/PianoData.h
"""
import json
import math
import sys


def repair(keys):
    """The top keys have only 4-5 partials below 20 kHz, and their stiffness or
    pitch fit can fail. Going up the keyboard, compare each key with a straight
    line through the two keys below it (log B, cents) and replace clear failures:
    B off by more than 2.5x, or a pitch fit that fell back to nominal."""
    def cents_of(k):
        return 1200 * math.log2(k["f0"] / (440 * 2 ** ((k["key"] - 69) / 12)))
    for i in range(2, len(keys)):
        k, p1, p2 = keys[i], keys[i - 1], keys[i - 2]
        t = (k["key"] - p1["key"]) / (p1["key"] - p2["key"])
        lb = math.log(p1["B"]) + t * (math.log(p1["B"]) - math.log(p2["B"]))
        if abs(math.log(k["B"]) - lb) > math.log(2.5):
            print(f"repair {k['name']}: B {k['B']:.2e} -> {math.exp(lb):.2e}")
            k["B"] = math.exp(lb)
        c = cents_of(p1) + t * (cents_of(p1) - cents_of(p2))
        if abs(cents_of(k)) < 0.01 and k["key"] != 69:   # the fit fell back to nominal pitch
            print(f"repair {k['name']}: {cents_of(k):+.1f} c -> {c:+.1f} c")
            k["f0"] = 440 * 2 ** ((k["key"] - 69) / 12) * 2 ** (c / 1200)


NOISE_Q = 1.2


def band_matrix(bands, sr):
    """M[i][j]: share of band filter j's output power (unit total) that falls in octave band i,
    for the engine's filters: two identical RBJ band-pass stages at sqrt(2)·low edge."""
    import numpy as np
    f = np.linspace(1, sr / 2, 200000)
    w = 2 * np.pi * f / sr
    z = np.exp(-1j * w)
    M = np.zeros((len(bands), len(bands)))
    for j, lo in enumerate(bands):
        fc = lo * math.sqrt(2)
        if fc * math.sqrt(2) > 0.49 * sr:
            continue
        w0 = 2 * math.pi * fc / sr
        alpha = math.sin(w0) / (2 * NOISE_Q)
        a0 = 1 + alpha
        b0, a1, a2 = alpha / a0, -2 * math.cos(w0) / a0, (1 - alpha) / a0
        H = (b0 - b0 * z * z) / (1 + a1 * z + a2 * z * z)
        P = np.abs(H) ** 4
        tot = P.sum()
        for i, lo2 in enumerate(bands):
            sel = (f >= lo2) & (f < 2 * lo2)
            M[i, j] = P[sel].sum() / tot
    return M


def solve_gains(M, target_db):
    """Band gains (dB) whose filtered noise, overlap included, matches the target band levels."""
    import numpy as np
    from scipy.optimize import nnls
    t = 10 ** (np.array(target_db) / 10)
    active = M.sum(0) > 0
    # relative error, so quiet bands count as much as loud ones, but only within 40 dB of the
    # loudest band (deeper than that, a band can't be heard next to the others anyway)
    W = 1 / (t + t.max() * 1e-4)
    # a band with no measurable residual is one the partials cover: don't let it constrain the rest
    rows = np.array(target_db) > -120
    g, _ = nnls((M[:, active] * W[:, None])[rows], (t * W)[rows])
    out = np.full(len(target_db), -150.0)
    out[np.nonzero(active)[0]] = 10 * np.log10(np.maximum(g, 1e-15))
    return np.maximum(out, -150.0)


def main():
    d = json.load(open(sys.argv[1]))
    out = open(sys.argv[2], "w")
    keys = d["keys"]
    repair(keys)

    # tuning: cents of each key's actual fundamental, f0·sqrt(1+B), against equal temperament,
    # shifted so that A4 sits exactly on the reference pitch
    def fund_cents(k):
        f = k["f0"] * math.sqrt(1 + k["B"])
        return 1200 * math.log2(f / (440 * 2 ** ((k["key"] - 69) / 12)))
    cents = {k["key"]: fund_cents(k) for k in keys}
    ks = sorted(cents)
    if 69 in cents:
        ref = cents[69]
    else:
        lo = max(x for x in ks if x < 69); hi = min(x for x in ks if x > 69)
        ref = cents[lo] + (cents[hi] - cents[lo]) * (69 - lo) / (hi - lo)

    centers = [(lo + hi) / 2 for lo, hi in d["velRanges"]]

    rows_k, rows_p, rows_l = [], [], []
    for k in keys:
        first = len(rows_p)
        for p in sorted(k["partials"], key=lambda p: p["n"]):
            lv = p["levels"]
            if max(max(a) for a in lv) < -96:
                continue                       # never above the noise
            drop = min(0.0, max(-60.0, p["drop"]))
            rows_p.append("  {%d, %.3ff, %.3ff, %.2ff, %.3ff, %.2ff, %.4ff}" % (
                p["n"], p["s1"], p["s2"], drop, p["beat"], p["beat_db"], p["phase"]))
            rows_l.append("  {" + ", ".join("{%d, %d}" % (round(max(a[0], -150) * 100), round(max(a[1], -150) * 100))
                                             for a in lv) + "}")
        # loudness of each layer: the power sum of every partial, both mics averaged
        loud = []
        for v in range(d["layers"]):
            pw = sum((10 ** (p["levels"][v][0] / 10) + 10 ** (p["levels"][v][1] / 10)) / 2 for p in k["partials"])
            loud.append(10 * math.log10(pw + 1e-15))
        rows_k.append("  {%d, %.3ff, %.4ef, %d, %d, {%s}}" % (k["key"], cents[k["key"]] - ref, k["B"], first,
                                                           len(rows_p) - first, ", ".join("%.2ff" % x for x in loud)))

    out.write("// Generated by tools/make_header.py from measurements of the\n")
    out.write(f"// {d['source']}.\n// Do not edit by hand.\n#pragma once\n\nnamespace piano_data {{\n\n")
    out.write("struct Key { unsigned char note; float cents; float B; unsigned short first, count; float loud[16]; };\n\n")
    out.write("// n: partial number; s1, s2: prompt and aftersound decay (dB/s); drop: the aftersound's\n"
              "// level at the strike relative to the whole partial (dB, 0 = one stage); beat: Hz;\n"
              "// beatDb: the aftersound's beat, peak to trough; phase: right mic against left (rad)\n")
    out.write("struct Partial { unsigned char n; float s1, s2, drop, beat, beatDb, phase; };\n\n")
    out.write(f"constexpr int kKeys = {len(keys)};\nconstexpr int kLayers = {d['layers']};\n")
    out.write("// MIDI velocity at the middle of each recorded layer\n")
    out.write("constexpr float kLayerVel[kLayers] = {" + ", ".join("%.1ff" % c for c in centers) + "};\n\n")
    out.write("constexpr Key keys[kKeys] = {\n" + ",\n".join(rows_k) + "\n};\n\n")
    out.write(f"constexpr int kPartials = {len(rows_p)};\n")
    out.write("constexpr Partial partials[kPartials] = {\n" + ",\n".join(rows_p) + "\n};\n\n")
    out.write("// starting level of each partial per layer and mic (left, right), in hundredths of a dB\n")
    out.write("constexpr short levels[kPartials][kLayers][2] = {\n" + ",\n".join(rows_l) + "\n};\n\n")
    # the strike's noise (tools/analyze_noise.py), if measured
    import os
    npath = os.path.join(os.path.dirname(sys.argv[1]), "salamander_noise.json")
    if os.path.exists(npath):
        nz = json.load(open(npath))
        bands = nz["bands"]
        byk = {k["key"]: k for k in nz["keys"]}
        M = band_matrix(bands, 48000.0)
        rows_nl, rows_nd = [], []
        for k in keys:
            n = byk[k["key"]]
            dec = []
            for b, d in enumerate(n["decay"]):
                dec.append(min(max(d, -1500.0), -60.0))
            # a residual that fades as slowly as the strings is partial energy the analysis
            # smeared, not knock: keep only what decays like a transient
            slow = [max(0.0, (d + 40.0) / 35.0) for d in n["decay"]]   # 0 at -40 dB/s, 1 at -5 dB/s
            lv = []
            for layer in n["levels"]:
                mics = []
                for mic in layer:
                    target = [max(min(x - 18 * min(slow[b], 1.0), 0), -150) for b, x in enumerate(mic)]
                    g = solve_gains(M, target)
                    mics.append("{" + ", ".join(str(round(v * 100)) for v in g) + "}")
                lv.append("{" + ", ".join(mics) + "}")
            rows_nl.append("  {" + ", ".join(lv) + "}")
            rows_nd.append("  {" + ", ".join("%.1ff" % d for d in dec) + "}")
        out.write(f"// The strike's noise (hammer, action, soundboard knock): octave bands from {bands[0]:g} Hz\n")
        out.write(f"constexpr int kNoiseBands = {len(bands)};\n")
        out.write(f"constexpr float kNoiseQ = {NOISE_Q}f;   // per stage; two stages per band\n")
        out.write("constexpr float kNoiseBandLow[kNoiseBands] = {" + ", ".join("%.2ff" % b for b in bands) + "};\n")
        out.write("// gain of each band's noise at the strike per key, layer and mic (hundredths of a dB), solved so\n"
                  "// that the bands together, with their filters' overlap, give the measured spectrum\n")
        out.write("constexpr short noiseLevels[kKeys][kLayers][2][kNoiseBands] = {\n" + ",\n".join(rows_nl) + "\n};\n")
        out.write("// decay per key and band (dB/s)\n")
        out.write("constexpr float noiseDecay[kKeys][kNoiseBands] = {\n" + ",\n".join(rows_nd) + "\n};\n\n")
    out.write("}  // namespace piano_data\n")
    print(f"{len(keys)} keys, {len(rows_p)} partials")


if __name__ == "__main__":
    main()
