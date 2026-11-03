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


NOISE_Q = 1.2       # per stage
NOISE_STAGES = 2    # identical band-pass stages per band
NOISE_LP = 1.7      # then a 4th-order Butterworth low-pass at this × the band's lower edge
BEAT_SCALE = float(__import__("os").environ.get("BEAT_SCALE", "1.8"))   # calibrated: README


def band_matrix(bands, sr):
    """M[i][j]: share of band filter j's output power (unit total) that falls in octave band i,
    for the engine's filters: NOISE_STAGES identical RBJ band-pass stages at sqrt(2)·low edge,
    then a 4th-order Butterworth low-pass at NOISE_LP·low edge."""
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
        P = np.abs(H) ** (2 * NOISE_STAGES)
        fl = lo * NOISE_LP
        if fl < 0.45 * sr:
            for q in (0.5411961, 1.3065630):
                wl = 2 * math.pi * fl / sr
                al, c = math.sin(wl) / (2 * q), math.cos(wl)
                n0 = 1 + al
                L = ((1 - c) / 2 + (1 - c) * z + (1 - c) / 2 * z * z) / n0 / (1 + (-2 * c / n0) * z + ((1 - al) / n0) * z * z)
                P = P * np.abs(L) ** 2
        tot = P.sum()
        for i, lo2 in enumerate(bands):
            sel = (f >= lo2) & (f < 2 * lo2)
            M[i, j] = P[sel].sum() / tot
    return M


def solve_gains(M, target_db):
    """Band gains (dB) whose filtered noise, overlap included, matches the target band levels,
    as closely as possible in dB. (Matching in power, relative or not, lets a quiet band next to
    a loud one rule: rather than spill a little into it, the loud band was switched off, which
    left holes 20-30 dB deep in the noise spectra.) Untargeted bands get no noise; bands more
    than 60 dB under the loudest count less."""
    import numpy as np
    from scipy.optimize import least_squares
    t = np.array(target_db, float)
    active = (M.sum(0) > 0) & (t > -120)
    rows = t > -120
    out = np.full(len(t), -150.0)
    if not active.any():
        return out
    Ma = M[:, active][rows]
    tr = t[rows]
    w = np.where(tr > tr.max() - 60, 1.0, 0.3)

    def resid(u):
        return w * (10 * np.log10(Ma @ (10 ** (u / 10)) + 1e-16) - tr)

    u0 = t[active].copy()
    sol = least_squares(resid, u0, bounds=(u0 - 40, u0 + 20), method="trf")
    out[np.nonzero(active)[0]] = sol.x
    return np.maximum(out, -150.0)


def clean_onsets(by_key, key_list):
    """The swell fit (tools/analyze_onset.py) is only trusted where the partials explain the
    recording's first 40 ms well (residual < 25 %). Per key: the median swell of its good layers
    (the recordings show no consistent trend with velocity); keys with no good layer take the
    log-interpolation of their neighbours. Start offsets: the fit's where good, else 2 ms.
    Swells are kept within 1-15 ms."""
    if not by_key:
        return
    med = {}
    for k in key_list:
        o = by_key[k]
        good = [t for t, e in zip(o["tau"], o["err"]) if e < 0.25]
        if good:
            med[k] = float(sorted(good)[len(good) // 2])
    have = sorted(med)
    for k in key_list:
        o = by_key[k]
        if k not in med:
            lo = max([x for x in have if x < k], default=None)
            hi = min([x for x in have if x > k], default=None)
            if lo is None or hi is None:
                med[k] = med[lo if hi is None else hi]
            else:
                u = (k - lo) / (hi - lo)
                med[k] = math.exp(math.log(med[lo]) + u * (math.log(med[hi]) - math.log(med[lo])))
        tau = min(max(med[k], 0.001), 0.015)
        o["tau"] = [tau] * len(o["tau"])
        o["delay"] = [d if e < 0.25 else 0.002 for d, e in zip(o["delay"], o["err"])]
        print(f"onset {k}: {tau * 1000:.1f} ms")


def write_mech(out, path):
    """Key release and pedal noises (tools/analyze_mech.py): envelopes of 10 ms frames (dB under
    the peak; -128 ends one) and band gains solved like the strike noise's; and the ring of the
    strings the pedal's lift sets going, as band levels per mic (dB of variance) for the engine's
    string bank to match."""
    mech = json.load(open(path))
    M = band_matrix(mech["bands"], 48000.0)

    def gains(levels):
        return "{" + ", ".join("{" + ", ".join(str(round(max(v, -150) * 100)) for v in solve_gains(M, mic)) + "}"
                               for mic in levels) + "}"

    def envs(items, n):
        rows = []
        for it in items:
            e = [max(int(round(v)), -127) for v in it["env"]][:n]
            rows.append("  {" + ", ".join(str(v) for v in e + [-128] * (n - len(e))) + "}")
        return ",\n".join(rows)

    rel, dn, up = mech["release"], mech["pedal_down"], mech["pedal_up"]
    nr = max(len(r["env"]) for r in rel)
    nd = max(len(r["env"]) for r in dn)
    nu = max(len(r["env"]) for r in up)
    out.write("// Mechanical noises, from the release and pedal recordings: envelopes in 10 ms frames (dB under\n"
              "// the loudest; -128 ends one), band gains per mic (hundredths of a dB, as noiseLevels)\n")
    out.write(f"constexpr float kMechFrameS = {mech['frame_s']:g}f;\n")
    out.write(f"constexpr int kReleaseFrames = {nr}, kPedalDownFrames = {nd}, kPedalUpFrames = {nu};\n")
    out.write("constexpr signed char releaseEnv[88][kReleaseFrames] = {\n" + envs(rel, nr) + "\n};\n")
    out.write("constexpr short releaseLevels[88][2][kNoiseBands] = {\n" +
              ",\n".join("  " + gains(r["levels"]) for r in rel) + "\n};\n")
    out.write("// the pedal: two takes each, down (the thump, above the strings' ring) and up\n")
    out.write("constexpr signed char pedalDownEnv[2][kPedalDownFrames] = {\n" + envs(dn, nd) + "\n};\n")
    out.write("constexpr short pedalDownLevels[2][2][kNoiseBands] = {\n" +
              ",\n".join("  " + gains(r["levels"]) for r in dn) + "\n};\n")
    out.write("constexpr signed char pedalUpEnv[2][kPedalUpFrames] = {\n" + envs(up, nu) + "\n};\n")
    out.write("constexpr short pedalUpLevels[2][2][kNoiseBands] = {\n" +
              ",\n".join("  " + gains(r["levels"]) for r in up) + "\n};\n")
    out.write("// the free strings' ring after the pedal's lift, 0.3-1.0 s (hundredths of a dB of variance, per mic)\n")
    out.write("constexpr short pedalRing[2][kNoiseBands] = {" +
              ", ".join("{" + ", ".join(str(round(v * 100)) for v in mic) + "}" for mic in mech["ring"]) + "};\n\n")


def main():
    d = json.load(open(sys.argv[1]))
    n_layers = d["layers"]
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

    import os
    opath = os.path.join(os.path.dirname(sys.argv[1]), "salamander_onset.json")
    onset_by_key = {o["key"]: o for o in json.load(open(opath))["keys"]} if os.path.exists(opath) else {}
    clean_onsets(onset_by_key, [k["key"] for k in keys])
    rows_k, rows_p, rows_l, rows_ph = [], [], [], []
    for k in keys:
        first = len(rows_p)
        for p in sorted(k["partials"], key=lambda p: p["n"]):
            lv = p["levels"]
            if max(max(a) for a in lv) < -96:
                continue                       # never above the noise
            drop = min(0.0, max(-60.0, p["drop"]))
            # The beat's depth is measured as one sinusoid at the strongest beat rate; a real unison's
            # three strings fluctuate more than that one component shows. BEAT_SCALE is calibrated
            # so the model's envelopes fluctuate as much as the recordings' (see the README).
            beat_db = min(p["beat_db"] * BEAT_SCALE, 14.0)
            shapes = p.get("shapes") or [p, p, p]
            arr = lambda key, f, clamp=lambda x: x: "{" + ", ".join(f % clamp(sh.get(key, -100.0)) for sh in shapes) + "}"
            # where the partial was found off the stiff-string line (analyze.py's refine_partials), the
            # engine moves it there: cents from what the engine computes from this key's pitch and B
            n = p["n"]
            f_eng = (440 * 2 ** ((k["key"] - 69) / 12) * 2 ** (cents[k["key"]] / 1200) *
                     n * math.sqrt((1 + k["B"] * n * n) / (1 + k["B"])))
            dev = max(-100.0, min(100.0, 1200 * math.log2(p["f"] / f_eng))) if p.get("f_measured") else 0.0
            # a wide cluster (the treble's unisons: at least 15 % of the power off the main component,
            # spread over 6 Hz or more) is played as its components; narrower ones keep the beat model
            cl = p.get("cluster")
            cc, cw = [0.0] * 4, [0.0] * 4
            nc = 0
            if cl:
                hz = [p["f"] * (2 ** (c / 1200) - 1) for c in cl["cents"]]
                if 1 - max(cl["share"]) >= 0.15 and max(hz) - min(hz) >= 6.0:
                    nc = len(cl["cents"])
                    cc[:nc], cw[:nc] = cl["cents"], cl["share"]
            rows_p.append("  {%d, %s, %s, %s, %.3ff, %.2ff, %.4ff, %s, %s, %.2ff, %d, {%s}, {%s}}" % (
                p["n"], arr("s1", "%.3ff"), arr("s2", "%.3ff"), arr("drop", "%.2ff", lambda x: min(0.0, max(-60.0, x))),
                p["beat"], beat_db, p["phase"], arr("x0", "%.2ff"), arr("s0", "%.1ff"), dev, nc,
                ", ".join("%.2ff" % c for c in cc), ", ".join("%.3ff" % w for w in cw)))
            rows_l.append("  {" + ", ".join("{%d, %d}" % (round(max(a[0], -150) * 100), round(max(a[1], -150) * 100))
                                             for a in lv) + "}")
            phs = p.get("phases") or [[-math.pi / 2, -math.pi / 2 + p["phase"]]] * len(lv)
            # the engine starts the swell at its t = 0; the recordings' swell starts `delay` in,
            # so advance each phase to that moment
            dl = onset_by_key.get(k["key"], {}).get("delay", [0.0] * len(lv))
            phs = [[a[0] + 2 * math.pi * p["f"] * dl[i], a[1] + 2 * math.pi * p["f"] * dl[i]] for i, a in enumerate(phs)]
            q = lambda x: max(-127, min(127, round(math.remainder(x, 2 * math.pi) / math.pi * 127)))
            rows_ph.append("  {" + ", ".join("{%d, %d}" % (q(a[0]), q(a[1])) for a in phs) + "}")
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
              "// beatDb: the aftersound's beat, peak to trough; phase: right mic against left (rad);\n"
              "// x0, s0: a fast transient at the strike, its level over the decay line (dB, -100 = none) and decay (dB/s)\n")
    out.write("// s1, s2, drop, x0 and s0 come for soft, medium and loud strikes (layers 2-6, 8-12, 14-16;\n"
              "// centred on 0-based layers 3, 9 and 14): soft notes sustain relatively longer\n")
    out.write("// dev: cents off the stiff-string frequency, where the partial was measured elsewhere\n")
    out.write("// nc, cc, cw: a wide cluster's components (cents from the partial, share of its power)\n")
    out.write("struct Partial { unsigned char n; float s1[3], s2[3], drop[3], beat, beatDb, phase, x0[3], s0[3], dev;\n"
              "                 signed char nc; float cc[4], cw[4]; };\n")
    out.write("constexpr float kShapeLayer[3] = {3.f, 9.f, 14.f};\n\n")
    out.write(f"constexpr int kKeys = {len(keys)};\nconstexpr int kLayers = {d['layers']};\n")
    out.write("// MIDI velocity at the middle of each recorded layer\n")
    out.write("constexpr float kLayerVel[kLayers] = {" + ", ".join("%.1ff" % c for c in centers) + "};\n\n")
    out.write("constexpr Key keys[kKeys] = {\n" + ",\n".join(rows_k) + "\n};\n\n")
    out.write(f"constexpr int kPartials = {len(rows_p)};\n")
    out.write("constexpr Partial partials[kPartials] = {\n" + ",\n".join(rows_p) + "\n};\n\n")
    out.write("// starting level of each partial per layer and mic (left, right), in hundredths of a dB\n")
    out.write("constexpr short levels[kPartials][kLayers][2] = {\n" + ",\n".join(rows_l) + "\n};\n\n")
    out.write("// phase of each partial at the strike per layer and mic (cosine reference), in steps of pi/127\n")
    out.write("constexpr signed char phases[kPartials][kLayers][2] = {\n" + ",\n".join(rows_ph) + "\n};\n\n")
    # the strike's noise (tools/analyze_noise.py), if measured
    import os
    npath = os.path.join(os.path.dirname(sys.argv[1]), "salamander_noise.json")
    if os.path.exists(npath):
        nz = json.load(open(npath))
        bands = nz["bands"]
        byk = {k["key"]: k for k in nz["keys"]}
        M = band_matrix(bands, 48000.0)
        rows_nl, rows_nd, rows_nd2, rows_ndr = [], [], [], []
        for k in keys:
            n = byk[k["key"]]
            dec, cut = [], []
            f0 = k["f0"]
            for b, d in enumerate(n["decay"]):
                if bands[b] * 2 <= f0 * 0.9:
                    # below the fundamental: no partials here, so this is the knock itself
                    dec.append(min(max(d, -1500.0), -40.0))
                    cut.append(0.0 if n.get("between") else 18.0 * min(max((d + 40.0) / 35.0, 0.0), 1.0))
                else:
                    dec.append(min(max(d, -1500.0), -60.0))
                    # Where the analysis could measure between the partials (all but the lowest keys),
                    # the residual is already only what lies between them. Elsewhere, a residual that
                    # fades like a string is partial energy the analysis didn't account for, not
                    # knock: what decays slower than 60 dB/s is cut 25 dB.
                    cut.append(0.0 if n.get("between") else 25.0 * min(max((d + 150.0) / 90.0, 0.0), 1.0))
            # the knock's slower part fades at least 40 dB/s: anything slower is the body's or the strings',
            # and the strike noise, unlike them, isn't stopped by the damper
            dec2 = [min(max(d, -300.0), -40.0) for d in n.get("decay2", n["decay"])]
            drop = [min(max(d, -60.0), 0.0) for d in n.get("drop", [0.0] * len(bands))]
            # corrections from tools/calibrate_noise.py: the knock (a1) and its slower part (a2), dB
            a1s, a2s = n.get("adjust1", [0.0] * len(bands)), n.get("adjust2", [0.0] * len(bands))
            shift = []
            for b in range(len(bands)):
                d = 10 ** (drop[b] / 20)
                g1, g2 = (1 - d) * 10 ** (a1s[b] / 20), d * 10 ** (a2s[b] / 20)
                shift.append(20 * math.log10(max(g1 + g2, 1e-9)))
                drop[b] = min(0.0, max(-60.0, 20 * math.log10(max(g2 / max(g1 + g2, 1e-9), 1e-3))))
            lv = []
            for layer in n["levels"]:
                mics = []
                for mic in layer:
                    target = [max(min(x - cut[b] + shift[b], 0), -150) for b, x in enumerate(mic)]
                    g = solve_gains(M, target)
                    mics.append("{" + ", ".join(str(round(v * 100)) for v in g) + "}")
                lv.append("{" + ", ".join(mics) + "}")
            rows_nl.append("  {" + ", ".join(lv) + "}")
            rows_nd.append("  {" + ", ".join("%.1ff" % d for d in dec) + "}")
            rows_nd2.append("  {" + ", ".join("%.1ff" % d for d in dec2) + "}")
            rows_ndr.append("  {" + ", ".join("%.1ff" % d for d in drop) + "}")
        out.write(f"// The strike's noise (hammer, action, soundboard knock): octave bands from {bands[0]:g} Hz\n")
        out.write(f"constexpr int kNoiseBands = {len(bands)};\n")
        out.write(f"constexpr float kNoiseQ = {NOISE_Q}f;   // per stage\n")
        out.write(f"constexpr int kNoiseStages = {NOISE_STAGES};   // identical band-pass stages per band\n")
        out.write(f"constexpr float kNoiseLpRatio = {NOISE_LP}f;   // then a 4th-order low-pass at this × the lower edge\n")
        out.write("constexpr float kNoiseBandLow[kNoiseBands] = {" + ", ".join("%.2ff" % b for b in bands) + "};\n")
        out.write("// gain of each band's noise at the strike per key, layer and mic (hundredths of a dB), solved so\n"
                  "// that the bands together, with their filters' overlap, give the measured spectrum\n")
        out.write("constexpr short noiseLevels[kKeys][kLayers][2][kNoiseBands] = {\n" + ",\n".join(rows_nl) + "\n};\n")
        out.write("// decay per key and band (dB/s): the knock, then (noiseDrop dB down at the strike) a slower part\n")
        out.write("constexpr float noiseDecay[kKeys][kNoiseBands] = {\n" + ",\n".join(rows_nd) + "\n};\n")
        out.write("constexpr float noiseDecay2[kKeys][kNoiseBands] = {\n" + ",\n".join(rows_nd2) + "\n};\n")
        out.write("constexpr float noiseDrop[kKeys][kNoiseBands] = {\n" + ",\n".join(rows_ndr) + "\n};\n\n")
    # the body's ring (tools/analyze_body.py)
    bpath = os.path.join(os.path.dirname(sys.argv[1]), "salamander_body.json")
    bd = {b["key"]: b for b in json.load(open(bpath))["keys"]}
    nb = len(json.load(open(bpath))["bands"])
    Mb = band_matrix(json.load(open(bpath))["bands"], 48000.0)
    rows_bl, rows_bd = [], []
    nz_adj = {}
    if os.path.exists(npath):
        nzk = json.load(open(npath))["keys"]
        nz_adj = {n["key"]: n.get("adjust_body", [0.0] * nb) for n in nzk}
        nz_dec = {n["key"]: n.get("adjust_body_decay", [0.0] * nb) for n in nzk}
    for k in keys:
        # below C3 the partials are too dense to tell the ring from the strings: use C3's
        src = bd[k["key"]] if k["key"] >= 48 else bd[48]
        badj = nz_adj.get(k["key"], [0.0] * nb)      # tools/calibrate_noise.py's correction
        # a band that doesn't fade (> -5 dB/s) is the recordings' floor, not ring
        dead = [d > -5.0 for d in src["decay"]]
        lv = []
        for layer in src["levels"]:
            mics = []
            for mic in layer:
                target = [(-150.0 if dead[b] or x < -95 else x + badj[b]) for b, x in enumerate(mic)]
                g = solve_gains(Mb, target) if max(target) > -140 else [-150.0] * nb
                mics.append("{" + ", ".join(str(round(max(v, -150) * 100)) for v in g) + "}")
            lv.append("{" + ", ".join(mics) + "}")
        rows_bl.append("  {" + ", ".join(lv) + "}")
        ddec = nz_dec.get(k["key"], [0.0] * nb) if nz_adj else [0.0] * nb
        rows_bd.append("  {" + ", ".join("%.1ff" % min(max(d + ddec[b], -120.0), -5.0) for b, d in enumerate(src["decay"])) + "}")
    out.write("// the body's ring after the strike (soundboard, case, room): band gains per key, layer, mic\n")
    out.write("// (hundredths of a dB, solved like the strike noise's), and decay per key and band (dB/s)\n")
    out.write("constexpr short bodyLevels[kKeys][kLayers][2][kNoiseBands] = {\n" + ",\n".join(rows_bl) + "\n};\n")
    out.write("constexpr float bodyDecay[kKeys][kNoiseBands] = {\n" + ",\n".join(rows_bd) + "\n};\n\n")
    out.write("// how long each key takes to swell in at the strike, per layer (ms, τ of 1 − e^{−t/τ})\n")
    rows_o = []
    for k in keys:
        taus = onset_by_key.get(k["key"], {}).get("tau", [0.003] * n_layers)
        rows_o.append("  {" + ", ".join("%.2ff" % (t * 1000) for t in taus) + "}")
    out.write("constexpr float onsetMs[kKeys][kLayers] = {\n" + ",\n".join(rows_o) + "\n};\n\n")
    write_mech(out, os.path.join(os.path.dirname(sys.argv[1]), "salamander_mech.json"))
    out.write("}  // namespace piano_data\n")
    print(f"{len(keys)} keys, {len(rows_p)} partials")


if __name__ == "__main__":
    main()
