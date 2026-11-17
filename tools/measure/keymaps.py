#!/usr/bin/env python3
"""Can the key maps be heard? (No recordings needed.)

Each map at a few values on one key against the same key with no map, as the perceptual
distance between the two renders (the owner could hardly hear a control at 1-7 %; 15 % and more
is plain), with the change in level. Velocity 100, Depth 100 %, one note per register. Then the
presets that use the maps against the same playing on Init (a scale and a chord).

The maps are custom state, not parameters, so this renders patches of its own rather than
common.render's.

    python3 -I tools/measure/keymaps.py [--render build/piano_render]
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M      # noqa: E402
import perceptual as P  # noqa: E402

KEYS = [48, 62, 72, 84]   # C3, D4, C5, C6
MAPS = [("Tuning +10 cents", "tune", 10.0), ("Tuning +25 cents", "tune", 25.0),
        ("Unison 0 %", "unison", 0.0), ("Unison 200 %", "unison", 2.0), ("Unison 400 %", "unison", 4.0),
        ("Hammer softer 50 %", "hammer", -0.5), ("Hammer softer 100 %", "hammer", -1.0),
        ("Hammer harder 50 %", "hammer", 0.5), ("Hammer harder 100 %", "hammer", 1.0),
        ("Level −6 dB", "level", -6.0),
        ("Sustain 50 %", "sustain", 0.5), ("Sustain 200 %", "sustain", 2.0),
        ("Stiffness 0 %", "stiff", 0.0), ("Stiffness 300 %", "stiff", 3.0)]
# the dampers act only on release: the key is let go at 1 s
DAMPERS = [("Dampers ×0.25", 0.25), ("Dampers ×4", 4.0), ("Dampers ×32", 32.0)]
NEUTRAL = {"tune": 0.0, "unison": 1.0, "hammer": 0.0, "level": 0.0, "sustain": 1.0, "damper": 1.0, "stiff": 1.0}
ROOT = M.TOOLS.parent
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def render(tool, patch, events, length):
    js, wav = M.TMP / "km.json", M.TMP / "km.wav"
    js.write_text(json.dumps({"patch": patch, "length": length, "events": events}))
    subprocess.run([tool, str(js), "--out", str(wav)], check=True, capture_output=True)
    y, _ = sf.read(wav, always_2d=True)
    return y * 10 ** 0.5


def patch(keys=None, params=None, base=None):
    p = dict(base or {"synthkit": 1, "synth": "piano", "patchVersion": 2, "name": "measure", "params": {}})
    p["params"] = {**p.get("params", {}), "space.room": 0, **(params or {})}
    p["custom"] = {"keys": keys or {}}
    return p


def one_map(map_id, key, value):
    a = [NEUTRAL[map_id]] * 88
    a[key - 21] = value
    return {map_id: a}


def dist(a, b):
    return P.distance(P.loudness(a, 48000), P.loudness(b, 48000))["score"]


def level(y):
    return 10 * np.log10(np.mean(y[:48000] ** 2) + 1e-30)


def after(y):
    return 10 * np.log10(np.mean(y[int(1.1 * 48000):int(1.5 * 48000)] ** 2) + 1e-30)


def main():
    tool = sys.argv[sys.argv.index("--render") + 1] if "--render" in sys.argv else str(ROOT / "build" / "piano_render")
    note = lambda k: [{"t": 0, "on": k, "vel": 100}]
    print("One key at velocity 100 against the same key with no map: distance % (level dB)\n")
    print(f"{'':22s}" + "".join(f"{NAMES[k % 12] + str(k // 12 - 1):>16s}" for k in KEYS))
    base = {k: render(tool, patch(), note(k), 3.0) for k in KEYS}
    for label, mid, v in MAPS:
        row = []
        for k in KEYS:
            y = render(tool, patch(one_map(mid, k, v)), note(k), 3.0)
            row.append(f"{dist(base[k], y):6.1f} ({level(y) - level(base[k]):+5.1f})")
        print(f"{label:22s}" + "".join(f"{c:>16s}" for c in row))
    released = lambda k: [{"t": 0, "on": k, "vel": 100}, {"t": 1.0, "off": k}]
    base = {k: render(tool, patch(), released(k), 3.0) for k in KEYS}
    for label, v in DAMPERS:
        row = []
        for k in KEYS:
            y = render(tool, patch(one_map("damper", k, v)), released(k), 3.0)
            row.append(f"{dist(base[k], y):6.1f} ({after(y) - after(base[k]):+5.1f})")
        print(f"{label:22s}" + "".join(f"{c:>16s}" for c in row))
    print("  (Dampers: let go at 1 s; in brackets, the level 0.1-0.5 s after release against no map)")

    print("\nPresets against Init, a scale C3-C5 and a chord (distance %):")
    ev = []
    for i, k in enumerate(range(48, 73, 2)):
        ev += [{"t": 0.25 * i, "on": k, "vel": 90}, {"t": 0.25 * i + 0.24, "off": k}]
    for k in (48, 55, 60, 64, 67):
        ev += [{"t": 3.5, "on": k, "vel": 95}, {"t": 5.5, "off": k}]
    for name in ("neglected-upright", "needs-a-tuner", "saloon-upright"):
        pre = json.load(open(ROOT / "presets" / f"{name}.json"))
        keys = pre.get("custom", {}).get("keys", {})
        y_maps = render(tool, patch(keys, base=pre), ev, 6.0)
        y_none = render(tool, patch({}, base=pre), ev, 6.0)
        y_init = render(tool, patch(), ev, 6.0)
        print(f"  {pre['name']:20s} {dist(y_init, y_maps):5.1f} against Init; the maps alone (preset with and without them): "
              f"{dist(y_none, y_maps):5.1f}")


if __name__ == "__main__":
    main()
