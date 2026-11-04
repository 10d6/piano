#!/usr/bin/env python3
"""A blind listening test against the recordings, as one HTML page (MUSHRA-style).

Its purpose is to check the perceptual distance (tools/measure/perceptual.py): if the ratings a
listener gives agree with the distance, the distance can stand in for listening while work is in
progress, and listening is left for pull requests.

Each trial is one recorded note (a key and a velocity layer). The listener hears the recording as
the reference, then rates several unlabelled versions of it from 0 (nothing like it) to 100 (can't
tell it apart), in a random order:

  - the recording itself, hidden (a listener who can't find it is guessing);
  - each build given with --build (name=path to piano_render), the note rendered dry (Room 0) as
    the recordings were made, with the As Recorded velocity map;
  - an anchor: the recording low-passed at 1 kHz and in mono, a known, clearly audible flaw. (The
    usual MUSHRA anchor, a 3.5 kHz low-pass, barely changes a piano note: rated 83 of 100 in the
    first test, and 1.7 % from the recording by the distance.)

The page keeps the answers in the browser while you go, and at the end shows them as JSON to copy
or download. tools/listen/analyze.py compares them with the distance, which this script computes
for every version and writes next to the page (<out>.json).

    python3 -I tools/listen/make_test.py --build main=build/piano_render --build old=/path/piano_render \\
        --trials 12 --out listen.html [--passages 3] [--low-share 0.3] [--sal ../salamander] [--seed 1]

Passages (a chord, an arpeggio, a bass note under a melody, a treble figure) take as their
reference the recorded notes added up at their times, and render with Resonance off (the
recordings were made note by note, so they hold no sympathetic ring).

The page holds short excerpts of the Salamander Grand Piano V3 recordings (CC BY 3.0, by
Alexander Holm), credited on it. Keep it out of the repository and the synth's page.
"""
import argparse
import base64
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import scipy.signal as sig
import soundfile as sf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "measure"))
import common as M      # noqa: E402
import perceptual as P  # noqa: E402

LENGTH = 3.0
FADE = 0.15


def clip(x, sr):
    x = np.asarray(x, float)[: int(LENGTH * sr)]
    if len(x) < int(LENGTH * sr):
        x = np.concatenate([x, np.zeros((int(LENGTH * sr) - len(x), x.shape[1]))])
    n = int(FADE * sr)
    x[-n:] *= np.linspace(1, 0, n)[:, None] ** 2
    return x


def mp3(x, sr):
    fd, wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    out = wav[:-4] + ".mp3"
    sf.write(wav, x, sr, subtype="FLOAT")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", wav, "-c:a", "libmp3lame", "-b:a", "160k", out], check=True)
    data = Path(out).read_bytes()
    os.unlink(wav)
    os.unlink(out)
    return base64.b64encode(data).decode()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--build", action="append", required=True, help="name=path to piano_render")
    p.add_argument("--sal", default=os.environ.get("PIANO_SAL", str(M.REPO.parent / "salamander")))
    p.add_argument("--trials", type=int, default=12)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--keys", help="keys to draw from (default: every measured key)")
    p.add_argument("--layers", default="4,8,13,16")
    p.add_argument("--passages", type=int, default=0,
                   help="also this many short passages (a chord, an arpeggio, a bass note under a melody), the "
                        "reference made by adding the recorded notes up")
    p.add_argument("--low-share", type=float, default=1.0,
                   help="share of single-note trials below C3 relative to an even draw (0.3: most from the middle up)")
    p.add_argument("--out", default="listen.html")
    a = p.parse_args()
    sal = Path(a.sal)
    builds = [b.split("=", 1) for b in a.build]
    rnd = random.Random(a.seed)
    keys = [int(k) for k in a.keys.split(",")] if a.keys else list(range(21, 109, 3))
    layers = [int(v) for v in a.layers.split(",")]
    pool = [(k, l) for k in keys for l in layers]
    wts = [a.low_share if k < 48 else 1.0 for k, _ in pool]
    cases = []
    while len(cases) < a.trials:
        c = rnd.choices(pool, wts)[0]
        if c not in cases:
            cases.append(c)
    # passages: (seconds, key, layer); keys are measured ones, so the recordings can be added up
    PASSAGES = [
        ("C major chord", [(0.0, 60, 11), (0.0, 63, 11), (0.0, 66, 11), (0.0, 72, 12)]),
        ("arpeggio", [(0.0, 57, 10), (0.25, 60, 10), (0.5, 63, 11), (0.75, 69, 12), (1.0, 72, 12)]),
        ("bass under a melody", [(0.0, 33, 12), (0.0, 72, 10), (0.6, 75, 11), (1.2, 78, 11), (1.8, 75, 10)]),
        ("treble figure", [(0.0, 84, 10), (0.2, 87, 10), (0.4, 90, 11), (0.6, 87, 10), (0.8, 84, 12)]),
    ]
    passages = PASSAGES[: a.passages]

    trials, manifest = [], []
    for i, (key, layer) in enumerate(cases):
        rec = clip(M.recording(sal, key, layer), 48000)
        Nr = P.loudness(rec)
        lp = sig.butter(8, 1000, fs=48000, output="sos")
        anchor = sig.sosfilt(lp, rec, axis=0).mean(1, keepdims=True).repeat(2, 1)
        versions = {"recording": rec, "anchor": anchor}
        for name, tool in builds:
            versions[name] = clip(P.render_note(tool, key, layer), 48000)
        order = list(versions)
        rnd.shuffle(order)
        stim = []
        for name in order:
            d = P.distance(Nr, P.loudness(versions[name]))
            manifest.append({"trial": i, "key": key, "layer": layer, "version": name, **d})
            stim.append({"version": name, "audio": mp3(versions[name], 48000)})
        trials.append({"key": key, "layer": layer, "name": M.A.note_name(key), "reference": mp3(rec, 48000),
                       "stimuli": stim})
        print(f"trial {i + 1}: {M.A.note_name(key)} layer {layer}", flush=True)

    for name, events in passages:
        i = len(trials)
        n = int(LENGTH * 48000)
        rec = np.zeros((n, 2))
        for t0, key, layer in events:
            x = M.recording(sal, key, layer)
            o = int(t0 * 48000)
            m = min(n - o, len(x))
            rec[o:o + m] += x[:m]
        rec = clip(rec, 48000)
        Nr = P.loudness(rec)
        anchor = sig.sosfilt(sig.butter(8, 1000, fs=48000, output="sos"), rec, axis=0).mean(1, keepdims=True).repeat(2, 1)
        versions = {"recording": rec, "anchor": anchor}
        ev = [{"t": t0, "on": key, "vel": M.layer_vel(layer)} for t0, key, layer in events]
        for bname, tool in builds:
            y, _ = M.render(tool, ev, LENGTH + 0.2, {"play.velmap": 1, "string.resonance": -40}, onset=True)
            versions[bname] = clip(y, 48000)
        order = list(versions)
        rnd.shuffle(order)
        stim = []
        for vname in order:
            d = P.distance(Nr, P.loudness(versions[vname]))
            manifest.append({"trial": i, "key": events[0][1], "layer": events[0][2], "version": vname, "passage": name, **d})
            stim.append({"version": vname, "audio": mp3(versions[vname], 48000)})
        trials.append({"key": events[0][1], "layer": events[0][2], "name": name, "reference": mp3(rec, 48000), "stimuli": stim})
        print(f"trial {i + 1}: {name}", flush=True)

    page = (HERE / "page.html").read_text().replace("/*TRIALS*/[]", json.dumps(trials))
    page = page.replace("/*TESTID*/\"\"", json.dumps(f"{Path(a.out).stem}-seed{a.seed}"))
    Path(a.out).write_text(page)
    Path(a.out).with_suffix(".json").write_text(json.dumps(manifest))
    print(f"{a.out}: {len(trials)} trials, {Path(a.out).stat().st_size / 1e6:.1f} MB; distances in "
          f"{Path(a.out).with_suffix('.json')}")


if __name__ == "__main__":
    main()
