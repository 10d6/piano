#!/usr/bin/env python3
"""Per measured key: does it sound nearer its recordings with its partials' modes
(tools/analyze_modes.py) or with the older description (analyze.py's decay shapes and beats)?

Each partial's modes were already required to follow its recorded envelope better than the older
description does. This checks the whole note the way it's heard: it builds the engine a second
time without modes (in a scratch copy), renders every measured key at layers 4, 8, 13 and 16 with
both, and compares their perceptual distance (tools/measure/perceptual.py). A key where the older
description is nearer by more than 0.5 points keeps it (`modes_off` in salamander.json, which
make_header.py honours). Run after the noise calibration; then make_header.py and build again.

    python3 -I tools/choose_modes.py <Salamander dir> build/piano_render tools/salamander.json
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "measure"))
import common as M      # noqa: E402
import perceptual as P  # noqa: E402


def legacy_build(json_path):
    work = M.TMP / "choose-modes"
    if work.exists():
        shutil.rmtree(work)
    repo = work / "repo"
    repo.mkdir(parents=True)
    for item in ("CMakeLists.txt", "synth.json", "src", "presets", "tests", "ui"):
        src = M.REPO / item
        (shutil.copytree if src.is_dir() else shutil.copy)(src, repo / item)
    (repo / "synthkit").symlink_to(M.REPO / "synthkit")
    subprocess.run([sys.executable, "-I", str(HERE / "make_header.py"), str(json_path), str(repo / "src" / "PianoData.h")],
                   check=True, capture_output=True, env={**os.environ, "NO_MODES": "1"})
    subprocess.run(["cmake", "-S", str(repo), "-B", str(repo / "build"), "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release"],
                   check=True, capture_output=True)
    subprocess.run(["cmake", "--build", str(repo / "build"), "--target", "piano_render"], check=True, capture_output=True)
    return str(repo / "build" / "piano_render")


def main():
    sal, tool, path = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    data = json.load(open(path))
    keys = [k["key"] for k in data["keys"]]
    old = legacy_build(path)
    with_modes = P.run(tool, sal, keys, P.DEFAULT_LAYERS, yard=False)
    without = P.run(old, sal, keys, P.DEFAULT_LAYERS, yard=False)
    print(f"{'key':5s} {'modes':>6s} {'older':>6s}")
    for k in data["keys"]:
        a = np.mean([r["score"] for r in with_modes if r["key"] == k["key"]])
        b = np.mean([r["score"] for r in without if r["key"] == k["key"]])
        k["modes_off"] = bool(b < a - 0.5)
        print(f"{k['name']:5s} {a:6.1f} {b:6.1f}{'   keeps the older description' if k['modes_off'] else ''}")
    path.write_text(json.dumps(data))


if __name__ == "__main__":
    main()
