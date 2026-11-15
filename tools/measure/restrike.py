#!/usr/bin/env python3
"""What Restrike does when a key is struck while it rings (no recordings: these hold no such strike).

At 0 % a restrike hands the old vibration over to the new strike (it fades as the new one swells
in). At 100 % the new strike is added to the vibration already there, less what the hammer's
felt takes. Three passages per key, under the pedal, at Restrike 0 and 100 %:

- loud, then soft: velocity 115, and 35 half a second later. With the handover the soft strike
  replaces the loud note; with Restrike the loud note rings on under it. The level of the second
  after the soft strike against Restrike 0, and the perceptual distance;
- fast, uneven repeats: twelve strikes at velocity 90, 70-130 ms apart (seeded). The weakest
  strike's first 30 ms against the first strike's (a strike that cancelled would show here), how
  much the strikes differ from one another (the first six partials' spread over strikes 4-12,
  and the distance between successive strikes, levels matched), and the build (last against first);
- even repeats, four a second, eight strikes: the same figures. Evenly spaced strikes meet the
  old vibration the same way every time, and by then it has faded far: little changes.

And the even repeats with the key let go between strikes, without the pedal (the dampers have
stopped most of the old vibration: nothing should change) and with Dampers at 400 % (slow).

With --felt0 BUILD (a build with -DRESTRIKE_FELT_DB=0.f) it also checks the sum: two strikes at
Restrike 100 % against the two rendered alone and added. It holds only where the partials are
modes (elsewhere each strike draws its own beat rates, and below A2 the strike is noise).

    python3 -I tools/measure/restrike.py [--render build/piano_render] [--keys 36,48,60,72,84] [--felt0 build-felt0/piano_render]
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as M      # noqa: E402
import perceptual as P  # noqa: E402

SR = 48000
GAP, COUNT, VEL = 0.25, 8, 90


def render(tool, events, params, length):
    sc = {"patch": "init", "params": {"space.room": 0, **params}, "length": length, "events": events}
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "s.json").write_text(json.dumps(sc))
        subprocess.run([tool, str(Path(d) / "s.json"), "--out", str(Path(d) / "s.wav")], check=True, capture_output=True)
        return sf.read(str(Path(d) / "s.wav"), always_2d=True)[0]


def events(key, times, vels, pedal=True, hold=0.6):
    ev = [{"t": 0, "cc": 64, "value": 127}] if pedal else []
    for i, (t, v) in enumerate(zip(times, vels)):
        ev.append({"t": round(t, 4), "on": key, "vel": v})
        if not pedal:
            nxt = times[i + 1] if i + 1 < len(times) else t + GAP
            ev.append({"t": round(t + hold * (nxt - t), 4), "off": key})
    return ev


def db(x):
    return 10 * np.log10(np.mean(x ** 2) + 1e-30)


def cut(y, t0, t1):
    return y[int(t0 * SR):int(t1 * SR)]


def repeats(y, times, f0, win):
    """Weakest attack (dB against the first strike's), partials' spread (dB), strike to strike (%), build (dB)."""
    n = len(times)
    att = min(db(cut(y, t, t + 0.03)) for t in times[1:]) - db(cut(y, times[0], times[0] + 0.03))
    pp = np.array([partials(cut(y, t + 0.01, t + win), f0) for t in times[3:]])
    steps = []
    for i in range(3, n - 1):
        a, b = cut(y, times[i], times[i] + win), cut(y, times[i + 1], times[i + 1] + win)
        steps.append(dist(a, b * np.sqrt(np.mean(a ** 2) / (np.mean(b ** 2) + 1e-30))))
    build = db(cut(y, times[-1], times[-1] + win)) - db(cut(y, times[0], times[0] + win))
    return att, float(np.mean(np.std(pp, 0))), float(np.mean(steps)), build


def partials(s, f0, count=6):
    w = np.hanning(len(s))[:, None]
    S = (np.abs(np.fft.rfft(s * w, axis=0)) ** 2).sum(1)
    f = np.fft.rfftfreq(len(s), 1 / SR)
    return np.array([10 * np.log10(S[(f > n * f0 * 0.97) & (f < n * f0 * 1.03)].sum() + 1e-30) for n in range(1, count + 1)])


def dist(a, b):
    return P.distance(P.loudness(a, SR), P.loudness(b, SR))["score"]


def main():
    import random

    def extra(p):
        p.add_argument("--keys", default="36,48,60,72,84")
        p.add_argument("--felt0", default="")
    a = M.args(__doc__, extra)
    keys = [int(k) for k in a.keys.split(",")]
    R = {k: [] for k in ("soft_db", "soft_d", "u0", "u1", "u_d", "e0", "e1", "e_d", "dry", "slow")}
    for key in keys:
        f0 = 440 * 2 ** ((key - 69) / 12)
        name = M.A.note_name(key)
        # loud, then soft
        ev = events(key, [0, 0.5], [115, 35])
        y0, y1 = (render(a.render, ev, {"string.restrike": r}, 2.5) for r in (0, 100))
        sdb, sd = db(cut(y1, 0.6, 1.6)) - db(cut(y0, 0.6, 1.6)), dist(cut(y0, 0.6, 1.6), cut(y1, 0.6, 1.6))
        R["soft_db"].append(sdb); R["soft_d"].append(sd)
        print(f"{name:4s} loud, then soft: the second after is {sdb:+.1f} dB against Restrike 0, {sd:.0f} % from it")
        # fast, uneven
        rnd, t, times = random.Random(key), 0.0, []
        for _ in range(12):
            times.append(t)
            t += rnd.uniform(0.07, 0.13)
        ev = events(key, times, [VEL] * 12)
        y0, y1 = (render(a.render, ev, {"string.restrike": r}, t + 0.5) for r in (0, 100))
        u0, u1, ud = repeats(y0, times, f0, 0.07), repeats(y1, times, f0, 0.07), dist(y0, y1)
        R["u0"].append(u0); R["u1"].append(u1); R["u_d"].append(ud)
        for r, u in ((0, u0), (100, u1)):
            print(f"{name:4s} fast uneven repeats, Restrike {r:3d} %: weakest attack {u[0]:+5.1f} dB, partials' spread {u[1]:.1f} dB, "
                  f"strike to strike {u[2]:3.0f} %, build {u[3]:+.1f} dB" + (f"; the passage {ud:.0f} % from Restrike 0" if r else ""))
        # even
        times = [i * GAP for i in range(COUNT)]
        ev = events(key, times, [VEL] * COUNT)
        y0, y1 = (render(a.render, ev, {"string.restrike": r}, COUNT * GAP + 0.5) for r in (0, 100))
        e0, e1, ed = repeats(y0, times, f0, GAP), repeats(y1, times, f0, GAP), dist(y0, y1)
        R["e0"].append(e0); R["e1"].append(e1); R["e_d"].append(ed)
        print(f"{name:4s} even repeats, Restrike 100 %: weakest attack {e1[0]:+5.1f} dB, partials' spread {e1[1]:.1f} dB, "
              f"strike to strike {e1[2]:3.0f} %, build {e1[3]:+.1f} dB; {ed:.0f} % from Restrike 0")
        # the key let go between strikes
        ev = events(key, times, [VEL] * COUNT, pedal=False)
        d0, d1 = (render(a.render, ev, {"string.restrike": r}, COUNT * GAP + 0.5) for r in (0, 100))
        s0, s1 = (render(a.render, ev, {"string.restrike": r, "string.damper": 400}, COUNT * GAP + 0.5) for r in (0, 100))
        R["dry"].append(dist(d0, d1)); R["slow"].append(dist(s0, s1))
        print(f"{name:4s} the key let go between strikes: {R['dry'][-1]:.0f} % from Restrike 0; with Dampers 400 %: {R['slow'][-1]:.0f} %")

    def rng(vals, f="{:.0f}"):
        return f.format(min(vals)) + "…" + f.format(max(vals))
    print(f"loud, then soft: {rng(R['soft_db'], '{:+.1f}')} dB, {rng(R['soft_d'])} % from Restrike 0")
    for tag, k0, k1, kd in (("fast uneven repeats", "u0", "u1", "u_d"), ("even repeats", "e0", "e1", "e_d")):
        for r, k in ((0, k0), (100, k1)):
            v = R[k]
            print(f"{tag}, Restrike {r:3d} %: weakest attack {rng([x[0] for x in v], '{:+.1f}')} dB, spread {rng([x[1] for x in v], '{:.1f}')} dB, "
                  f"strike to strike {rng([x[2] for x in v])} %, build {rng([x[3] for x in v], '{:+.1f}')} dB" + (f"; {rng(R[kd])} % from Restrike 0" if r else ""))
    print(f"the key let go between strikes: {rng(R['dry'])} % from Restrike 0; with Dampers 400 %: {rng(R['slow'])} %")
    if a.felt0:
        print("the sum (felt at 0, Restrike 100 %, Body and Key & Pedal at −40 dB): how far a restrike is from the two strikes added")
        for key in keys:
            for gap, v1, v2 in ((0.5, 100, 100), (0.137, 100, 60), (0.05, 70, 110)):
                prm = {"string.restrike": 100, "tone.body": -40, "tone.mech": -40}
                ped = [{"t": 0, "cc": 64, "value": 127}]
                A = render(a.felt0, ped + [{"t": 0, "on": key, "vel": v1}], prm, 3)
                B = render(a.felt0, ped + [{"t": gap, "on": key, "vel": v2}], prm, 3)
                AB = render(a.felt0, ped + [{"t": 0, "on": key, "vel": v1}, {"t": gap, "on": key, "vel": v2}], prm, 3)
                n0, n1 = int((gap + 0.02) * SR), int((gap + 1.5) * SR)
                print(f"{M.A.note_name(key):4s} {gap * 1000:3.0f} ms apart, velocities {v1}/{v2}: the difference is "
                      f"{db((A + B)[n0:n1]) - db((AB - A - B)[n0:n1]):.0f} dB under the sum")


if __name__ == "__main__":
    main()
