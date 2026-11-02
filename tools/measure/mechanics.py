#!/usr/bin/env python3
"""Pedal and key noises, and the strings' sympathetic ring, against the recordings.

  - pedal down: the render's octave bands over time against pedalD1/2 (the thump, then the
    free strings' ring, which the string bank makes), at the sfz's level for them;
  - pedal up: the first 0.3 s against pedalU1/2;
  - key release: the release noise alone (Key & Pedal at 0 dB minus at −40 dB, with the body
    ring, which shares its noise bands, turned down: the rest cancels)
    against rel<k> at the sfz's level;
  - sympathetic ring: a note played with the pedal down, Resonance at 0 dB minus at −40 dB
    (what the strings pick up from it), relative to the note, per band and over time.

Healthy: pedal and release bands within ~3 dB where the recording is above −100; the
sympathetic ring well under the note (−20 to −35 dB at 1 s) and strongest in the bass.

    python3 -I tools/measure/mechanics.py [--render …] [--sal …]
"""
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M  # noqa: E402

BANDS = 31.25 * 2.0 ** np.arange(10)
TRIM = -10.0


def bands(x, sr, t0, t1):
    """Octave-band variance (dB) per band, mics averaged, over t0…t1, leaving out mains hum."""
    seg = x[int(t0 * sr):int(t1 * sr)]
    if len(seg) < 64:
        return np.full(len(BANDS), -150.0)
    win = np.hanning(len(seg))
    fr = np.fft.rfftfreq(len(seg), 1 / sr)
    hum = np.zeros(len(fr), bool)
    for h in range(50, 1001, 50):
        hum |= np.abs(fr - h) < 1.0
    out = np.zeros(len(BANDS))
    for m in range(seg.shape[1]):
        s = np.abs(np.fft.rfft(seg[:, m] * win)) ** 2 / (0.375 * len(seg) ** 2 / 2)
        s[hum] = 0
        out += [s[(fr >= lo) & (fr < 2 * lo)].sum() for lo in BANDS]
    return 10 * np.log10(out / seg.shape[1] + 1e-16)


def row(label, v):
    return f"{label:<26}" + " ".join("%6.0f" % x for x in v)


def diff_row(label, r, m):
    d = np.where(r > -100, m - r, np.nan)
    return f"{label:<26}" + " ".join("     ." if np.isnan(x) else "%+6.1f" % x for x in d), d


def main():
    a = M.args(__doc__)
    smp = a.sal / "Samples"
    print(f"{'bands (Hz)':<26}" + " ".join("%6.0f" % b for b in BANDS))
    allbad = []

    # pedal down, held 6 s
    y, sr = M.render(a.render, [{"t": 0.0, "cc": 64, "value": 127}], 6.5, trim=False)
    recs = [sf.read(smp / f"pedalD{k}.flac", always_2d=True)[0] * 10 ** ((-20 + TRIM) / 20) for k in (1, 2)]
    print("\npedal down (render − recording, dB; recording levels below)")
    for t0, t1 in [(0.0, 0.3), (0.3, 1.0), (1.0, 3.0), (3.0, 6.0)]:
        r = 10 * np.log10(np.mean([10 ** (bands(x, 48000, t0, t1) / 10) for x in recs], 0))
        line, d = diff_row(f"  {t0:.1f}-{t1:.1f} s", r, bands(y, sr, t0, t1))
        print(line)
        print(row("    recording", r))
        allbad += list(d[np.isfinite(d)][:8])

    # pedal up after 3 s down
    y, sr = M.render(a.render, [{"t": 0.0, "cc": 64, "value": 127}, {"t": 3.0, "cc": 64, "value": 0}], 4.0, trim=False)
    recs = [sf.read(smp / f"pedalU{k}.flac", always_2d=True)[0] * 10 ** ((-19 + TRIM) / 20) for k in (1, 2)]
    r = 10 * np.log10(np.mean([10 ** (bands(x, 48000, 0, 0.3) / 10) for x in recs], 0))
    print("\npedal up, first 0.3 s")
    line, d = diff_row("  render − recording", r, bands(y, sr, 3.0, 3.3))
    print(line)
    print(row("    recording", r))

    # key release noise alone
    print("\nkey release (vel 127), 0-0.45 s")
    rel_d = []
    for key in (21, 33, 45, 57, 69, 81, 93, 105):
        ev = [{"t": 0.0, "on": key, "vel": 127}, {"t": 1.0, "off": key}]
        y0, sr = M.render(a.render, ev, 1.6, {"tone.mech": 0, "tone.body": -40}, trim=False)
        y1, _ = M.render(a.render, ev, 1.6, {"tone.mech": -40, "tone.body": -40}, trim=False)
        noise = (y0 - y1) / (1 - 10 ** (-2))
        rec = sf.read(smp / f"rel{key - 20}.flac", always_2d=True)[0] * 10 ** ((-37 + TRIM) / 20)
        r = bands(rec, 48000, 0, 0.45)
        line, d = diff_row(f"  key {key}", r, bands(noise, sr, 1.0, 1.45))
        print(line)
        rel_d += list(d[np.isfinite(d)])

    # sympathetic ring of a note with the pedal down
    print("\nsympathetic ring with the pedal down, relative to the note (dB), vel 80")
    print(f"{'':<26}" + " ".join("%6.0f" % b for b in BANDS))
    for key in (36, 48, 60, 72, 84):
        ev = [{"t": 0.0, "cc": 64, "value": 127}, {"t": 0.5, "on": key, "vel": 80}]
        y0, sr = M.render(a.render, ev, 4.0, {"string.resonance": 0, "tone.mech": -40}, trim=False)
        y1, _ = M.render(a.render, ev, 4.0, {"string.resonance": -40, "tone.mech": -40}, trim=False)
        res = (y0 - y1) / (1 - 10 ** (-2))
        for t0, t1 in [(0.6, 1.0), (1.5, 2.5), (3.0, 4.0)]:
            note = bands(y1, sr, t0, t1)
            rr = bands(res, sr, t0, t1)
            tot = 10 * np.log10((10 ** (rr / 10)).sum() / (10 ** (note / 10)).sum())
            d = np.where(note > -100, rr - note, np.nan)
            print(f"  key {key} {t0:.1f}-{t1:.1f} s total {tot:+5.1f}  " +
                  " ".join("     ." if np.isnan(x) else "%+6.0f" % x for x in d))

    print(f"\npedal-down bands: mean |diff| {np.mean(np.abs(allbad)):.1f} dB; release: mean |diff| {np.mean(np.abs(rel_d)):.1f} dB")


if __name__ == "__main__":
    main()
