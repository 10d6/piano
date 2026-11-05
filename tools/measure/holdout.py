#!/usr/bin/env python3
"""Hold-out test: how well do keys the model never heard come out?

Rebuilds the engine in a scratch copy with some measured keys left out of the data (so they
are interpolated from their neighbours, as the 58 unrecorded keys always are), then compares
those keys with their recordings (tools/compare.py), next to the normal build's result.

    python3 -I tools/measure/holdout.py --keys 45,60 [--perceptual] [--sal …]

Needs the native toolchain (cmake, ninja). Takes a few minutes.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M  # noqa: E402


def extra(p):
    p.add_argument("--keys", default="45,60", help="measured keys to leave out")
    p.add_argument("--define", action="append", default=[], help="NAME=VALUE for the engine (experiments)")
    p.add_argument("--perceptual", action="store_true",
                   help="also the perceptual distance (perceptual.py) at layers 4, 8, 13, 16, instead of only octave bands")


def main():
    a = M.args(__doc__, extra)
    keys = [int(k) for k in a.keys.split(",")]
    work = M.TMP / ("holdout" + "".join("-" + d.replace("=", "") for d in a.define))
    if work.exists():
        shutil.rmtree(work)
    (work / "data").mkdir(parents=True)
    for f in ("salamander.json", "salamander_noise.json", "salamander_onset.json", "salamander_body.json"):
        d = json.load(open(M.TOOLS / f))
        d["keys"] = [k for k in d["keys"] if k["key"] not in keys]
        json.dump(d, open(work / "data" / f, "w"))
    shutil.copy(M.TOOLS / "salamander_mech.json", work / "data")   # per release key, not per sampled key
    sp = M.TOOLS / "salamander_strike.npz"           # the recorded strikes (analyze_strike.py)
    if sp.exists():
        import numpy as np
        z = dict(np.load(sp))
        keep = np.array([int(k) not in keys for k in z["keys"]])
        np.savez_compressed(work / "data" / "salamander_strike.npz",
                            **{n: (v[keep] if v.ndim and len(v) == len(keep) else v) for n, v in z.items()})
    repo = work / "repo"
    repo.mkdir()
    for item in ("CMakeLists.txt", "synth.json", "src", "presets", "tests", "ui"):
        src = M.REPO / item
        (shutil.copytree if src.is_dir() else shutil.copy)(src, repo / item)
    (repo / "synthkit").symlink_to(M.REPO / "synthkit")
    subprocess.run([sys.executable, "-I", str(M.TOOLS / "make_header.py"), str(work / "data" / "salamander.json"),
                    str(repo / "src" / "PianoData.h")], check=True, capture_output=True)
    flags = " ".join(f"-D{d}" for d in a.define)
    subprocess.run(["cmake", "-S", str(repo), "-B", str(repo / "build"), "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
                    f"-DCMAKE_CXX_FLAGS={flags}"],
                   check=True, capture_output=True)
    subprocess.run(["cmake", "--build", str(repo / "build"), "--target", "piano_render"], check=True, capture_output=True)
    if a.perceptual:
        import perceptual as P
        for label, tool in (("left out (interpolated)", repo / "build" / "piano_render"), ("measured (normal build)", Path(a.render))):
            rows = P.run(str(tool), a.sal, keys, P.DEFAULT_LAYERS, yard=False)
            print(f"--- {label}: perceptual distance")
            for k in keys:
                print(f"{M.A.note_name(k):5s} " + "  ".join(f"{r['score']:5.1f}" for r in rows if r["key"] == k))
            print(P.summary(rows))
        return
    cases = ",".join(f"{k}:{l}" for k in keys for l in (16, 6))
    for label, tool in (("left out (interpolated)", repo / "build" / "piano_render"), ("measured (normal build)", Path(a.render))):
        print(f"--- {label}")
        out = subprocess.run([sys.executable, "-I", str(M.TOOLS / "compare.py"), str(a.sal), str(tool), str(work / "cmp"), cases],
                             check=True, capture_output=True, text=True).stdout
        print("\n".join(out.strip().splitlines()[1:]))


if __name__ == "__main__":
    main()
