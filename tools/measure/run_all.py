#!/usr/bin/env python3
"""Run every measurement and print its summary lines (the full output of each is in
<tmp>/piano-measure/<name>.txt). Takes about 10 minutes.

    python3 -I tools/measure/run_all.py [--render …] [--sal …]
"""
import subprocess
import sys
import time

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M  # noqa: E402

SCRIPTS = [
    # name, summary lines to show (from the end), needs recordings
    ("levels", 1, True),
    ("groups", 1, True),
    ("ring", 3, True),
    ("balance", 1, True),
    ("fluctuation", 2, True),
    ("decay_by_velocity", 6, True),
    ("attack", 1, True),
    ("velocity", 17, True),
    ("mechanics", 1, True),
    ("notes", 40, False),
]


def main():
    a = M.args(__doc__)
    common = ["--render", a.render, "--sal", str(a.sal)]
    print("--- compare.py (octave bands vs recordings)")
    out = subprocess.run([sys.executable, "-I", str(M.TOOLS / "compare.py"), str(a.sal), a.render, str(M.TMP / "compare")],
                         capture_output=True, text=True)
    print(out.stdout.strip() or out.stderr.strip())
    for name, tail, _ in SCRIPTS:
        t0 = time.time()
        r = subprocess.run([sys.executable, "-I", str(M.TOOLS / "measure" / f"{name}.py")] + common,
                           capture_output=True, text=True)
        (M.TMP / f"{name}.txt").write_text(r.stdout + r.stderr)
        lines = (r.stdout.strip() or r.stderr.strip()).splitlines()
        print(f"--- {name} ({time.time() - t0:.0f} s)" + ("" if r.returncode == 0 else "  FAILED"))
        print("\n".join(lines[-tail:]))


if __name__ == "__main__":
    main()
