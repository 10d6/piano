#!/usr/bin/env python3
"""Can the Playing settings, Stiffness and Resonance be heard? (No recordings needed.)

Each setting at its ends against its default, as the perceptual distance between the two
renders (the owner could hardly hear a control at 1-7 %; 15 % and more is plain), with the
change in level.

- Playing: one note at velocities 30, 64, 100 and 127 (Smooth map). The on-screen keyboard and
  the computer keys play 100, where these settings do little by design: they shape soft and
  medium notes. The page's velocity graph can be pressed to play any velocity.
- Stiffness: one note per register at velocity 100.
- Resonance: a short arpeggio with the sustain pedal held. The distance gives the ring after the
  notes little weight, so this reports the sympathetic strings' level against all the sound
  (during the notes and in the tail), from the render with Resonance off subtracted.

    python3 -I tools/measure/settings.py [--render build/piano_render]
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M      # noqa: E402
import perceptual as P  # noqa: E402

BASE = {"play.velmap": 0}
PLAYING = [("Touch −1", {"play.touch": -1}), ("Touch +1", {"play.touch": 1}),
           ("Dynamics 25 %", {"play.dynamics": 25}), ("Dynamics 150 %", {"play.dynamics": 150}),
           ("Tone Range 0 %", {"play.tone": 0}), ("Tone Range 150 %", {"play.tone": 150}),
           ("Map As Recorded", {"play.velmap": 1}),
           ("Soft Attack 0 %", {"play.softattack": 0}), ("Soft Attack 100 %", {"play.softattack": 100}),
           ("Soft Attack 300 %", {"play.softattack": 300}), ("Soft Attack 600 %", {"play.softattack": 600}), ("Soft Pedal 100 %", {"play.softpedal": 100})]


def level(y, a=0.0, b=1.0):
    s = y[int(a * 48000):int(b * 48000)]
    return 10 * np.log10(np.mean(s ** 2) + 1e-30)


def dist(a, b, **kw):
    return P.distance(P.loudness(a, 48000, **kw), P.loudness(b, 48000, **kw))["score"]


def arpeggio(tool, params):
    ev = [{"t": 0, "cc": 64, "value": 127}]
    for i, k in enumerate((48, 55, 60, 64, 67, 72)):
        ev += [{"t": 0.05 + 0.25 * i, "on": k, "vel": 100}, {"t": 0.25 + 0.25 * i, "off": k}]
    return M.render(tool, ev, 6.0, {**BASE, "tone.mech": -40, **params})[0]


def main():
    a = M.args(__doc__)
    vels = (30, 64, 100, 127)
    print("Playing, C4: distance from the default (%) and level change (dB) at velocity 30 | 64 | 100 | 127")
    refs = {v: M.note(a.render, 60, v, 3.2, BASE) for v in vels}
    for name, prm in PLAYING:
        cells = []
        for v in vels:
            y = M.note(a.render, 60, v, 3.2, {**BASE, **prm})
            cells.append(f"{dist(refs[v], y):5.1f} % {level(y) - level(refs[v]):+5.1f}")
        print(f"  {name:18s}" + " | ".join(cells))
    print("Stiffness, velocity 100: distance from 100 % (%)")
    for key in (36, 48, 60, 72, 84):
        r = M.note(a.render, key, 100, 3.2, BASE)
        print(f"  {M.A.note_name(key):4s}" + "  ".join(f"{s:3d} %: {dist(r, M.note(a.render, key, 100, 3.2, {**BASE, 'string.stiffness': s})):5.1f}" for s in (0, 300, 800)))
    print("Resonance, a staccato arpeggio with the pedal held: the sympathetic strings against all the sound (dB) at 0.5-1.5 s, 4-5 s; distance from off (%)")
    off = arpeggio(a.render, {"string.resonance": -40})
    for r in (-12, 0, 12, 24):
        y = arpeggio(a.render, {"string.resonance": r})
        b = y - off
        print(f"  {r:+3d} dB: {level(b, .5, 1.5) - level(y, .5, 1.5):6.1f}  {level(b, 4, 5) - level(y, 4, 5):6.1f}   tail {level(y, 4, 5) - level(off, 4, 5):+4.1f} dB louder   {dist(off, y, length=5.5):4.1f} %")


if __name__ == "__main__":
    main()
