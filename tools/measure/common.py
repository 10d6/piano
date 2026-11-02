"""Shared helpers for the measurement scripts in tools/measure.

Every script takes the same options:

    --render PATH   the render tool (default build/piano_render)
    --sal DIR       the Salamander Grand Piano V3 checkout (default $PIANO_SAL or ../salamander)

and is run from the repo root: python3 -I tools/measure/<script>.py [options]
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

TOOLS = Path(__file__).resolve().parent.parent
REPO = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import analyze as A  # noqa: E402,F401  (re-exported for the scripts)
import compare as C  # noqa: E402,F401

TMP = Path(tempfile.gettempdir()) / "piano-measure"
TMP.mkdir(exist_ok=True)


def args(description, extra=None):
    """Parse the common options (and any the script adds through `extra(parser)`)."""
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--render", default=str(REPO / "build" / "piano_render"))
    p.add_argument("--sal", default=os.environ.get("PIANO_SAL", str(REPO.parent / "salamander")))
    if extra:
        extra(p)
    a = p.parse_args()
    a.sal = Path(a.sal)
    return a


def model():
    """The measurements, by key."""
    return {k["key"]: k for k in json.load(open(TOOLS / "salamander.json"))["keys"]}


def render(tool, events, length, params=None, onset=False, trim=True):
    """Render a scenario; returns stereo samples (and the sample rate). With onset, the start
    is aligned to the attack like the recordings (compare.load_rec); with trim, the engine's
    −10 dB headroom is undone so levels compare with the recordings'."""
    p = {"space.room": 0}
    p.update(params or {})
    sc = {"patch": "init", "params": p, "length": length, "events": events}
    js, wav = TMP / "s.json", TMP / "s.wav"
    js.write_text(json.dumps(sc))
    subprocess.run([tool, str(js), "--out", str(wav)], check=True, capture_output=True)
    y, sr = sf.read(wav, always_2d=True)
    if onset:
        m = y.mean(1)
        on = max(0, int(np.argmax(np.abs(m) > np.abs(m).max() * 0.05)) - int(0.002 * sr))
        y = y[on:]
    return (y * 10 ** 0.5 if trim else y), sr


def note(tool, key, vel, length, params=None):
    """One note, layer by layer as recorded (As Recorded velocity map), aligned and untrimmed."""
    p = {"play.velmap": 1}
    p.update(params or {})
    return render(tool, [{"t": 0, "on": key, "vel": vel}], length, p, onset=True)[0]


def layer_vel(layer):
    """The MIDI velocity in the middle of a recorded layer (1-based)."""
    lo, hi = C.VEL_RANGES[layer - 1]
    return (lo + hi) // 2


def recording(sal, key, layer):
    return C.load_rec(sal, key, layer)[0]


def env_power(x, sr, f, window, tmax):
    """A partial's power envelope per mic (heterodyne), mics averaged; times and power."""
    pw = []
    for ch in range(x.shape[1]):
        t, z = A.envelope(x[:, ch], sr, f, window, tmax=tmax)
        pw.append(np.abs(z) ** 2)
    n = min(len(p) for p in pw)
    return t[:n], np.mean([p[:n] for p in pw], 0)


def at(t, pw, centre, half=0.1):
    """Mean power in dB over centre ± half."""
    i0, i1 = int(max(0, centre - half) / (t[1] - t[0])), int((centre + half) / (t[1] - t[0]))
    return 10 * np.log10(np.mean(pw[i0:i1]) + 1e-20)
