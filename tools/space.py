#!/usr/bin/env python3
"""The Space reverb's levels and decay times, from renders (docs/MODEL.md, Space).

Renders a staccato chord across the keyboard (C2 to C7, released at 0.2 s) dry and in each
space at Reverb 100 %, and reports for each space the wet signal (render minus dry):

- its energy against Hall's (the spaces are trimmed to sound equally loud at the same Reverb
  setting: `trimDb` in src/Reverb.h; healthy is within ±0.5 dB),
- its RT60 from the energy decay (T20) at 500-1000 Hz and 4-8 kHz (somewhat under the design:
  the dry notes, the undamped top one especially, are still sounding into it).

Then Hall's RT60 with Decay, Damping and Size moved, to check the controls.

    python3 -I tools/space.py build/piano_render

To re-trim after changing a space: add each space's printed level to its `trimDb` (with the
sign flipped) and rebuild. `kWet` in src/Synth.cpp sets Hall's own level; it was matched to the
room the reverb replaced (a 1.6 s noise room at the same Reverb setting).
"""
import json, os, subprocess, sys, tempfile
import numpy as np, soundfile as sf
from scipy.signal import butter, sosfilt

tool = sys.argv[1]
SPACES = ['Room', 'Chamber', 'Hall', 'Cathedral', 'Plate']
tmp = tempfile.mkdtemp(prefix='piano-space-')


def render(params, name):
    ev = []
    for n in [36, 48, 60, 67, 76, 84, 96]:
        ev += [{"t": 0.0, "on": n, "vel": 100}, {"t": 0.2, "off": n}]
    sc = {"patch": "init", "params": params, "length": 16, "events": ev}
    js, wav = os.path.join(tmp, name + '.json'), os.path.join(tmp, name + '.wav')
    open(js, 'w').write(json.dumps(sc))
    subprocess.run([tool, js, '--out', wav], check=True, capture_output=True)
    x, sr = sf.read(wav)
    return x, sr


def rt60(w, sr, band):
    sos = butter(4, band, btype='bandpass', fs=sr, output='sos')
    e = (sosfilt(sos, w, axis=0) ** 2).sum(1)[int(0.3 * sr):]
    c = np.cumsum(e[::-1])[::-1]
    c = 10 * np.log10(c / c[0] + 1e-30)
    t = lambda db: np.argmax(c < db) / sr
    return 3 * (t(-25) - t(-5))


dry, sr = render({"space.room": 0}, 'dry')
wet = {}
for k, n in enumerate(SPACES):
    x, _ = render({"space.room": 100, "space.type": k}, n)
    wet[n] = x - dry
ref = 10 * np.log10((wet['Hall'] ** 2).sum())
print(f"{'space':10s} {'level vs Hall':>14s} {'RT60 500-1k':>12s} {'RT60 4-8k':>10s}")
for n in SPACES:
    w = wet[n]
    print(f"{n:10s} {10 * np.log10((w ** 2).sum()) - ref:+13.1f}  {rt60(w, sr, (500, 1000)):10.2f} s {rt60(w, sr, (4000, 8000)):8.2f} s")
print("\nHall with one control moved:")
for name, p in [('Decay 50 %', {"space.decay": 50}), ('Decay 200 %', {"space.decay": 200}),
                ('Damping 0 %', {"space.damping": 0}), ('Damping 100 %', {"space.damping": 100}),
                ('Size 50 %', {"space.size": 50}), ('Size 200 %', {"space.size": 200})]:
    x, _ = render(dict({"space.room": 100, "space.type": 2}, **p), name.replace(' ', '').replace('%', ''))
    w = x - dry
    print(f"{name:14s} RT60 500-1k {rt60(w, sr, (500, 1000)):.2f} s, 4-8k {rt60(w, sr, (4000, 8000)):.2f} s")
