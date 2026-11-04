#!/usr/bin/env python3
"""Do the listening test's ratings agree with the perceptual distance?

    python3 -I tools/listen/analyze.py listen.json results1.json [results2.json …] [--keep-all]

listen.json is what make_test.py wrote next to the page (every version's distance); the results
are what the page gave each listener. It reports:

  - whether each listener found the hidden recording (rated it ≥ 90 in most trials) and rated the
    anchor low: a listener who didn't is left out, as MUSHRA does;
  - per version, the mean rating and the mean distance;
  - the rank correlation (Spearman) between rating and distance, within trials (does the distance
    order a trial's versions as the listener did?) and over everything. A useful distance has a
    clearly negative correlation: further from the recording, rated lower.
"""
import json
import sys
from collections import defaultdict

import numpy as np
from scipy.stats import spearmanr


def main():
    man = json.load(open(sys.argv[1]))
    dist = {(m["trial"], m["version"]): m for m in man}
    allr = []
    for f in [a for a in sys.argv[2:] if not a.startswith("--")]:
        res = json.load(open(f))
        rs = res["ratings"]
        by = defaultdict(dict)
        for r in rs:
            by[r["trial"]][r["version"]] = r["rating"]
        found = np.mean([t.get("recording", 0) >= 90 for t in by.values()])
        anchor = np.mean([t["anchor"] for t in by.values() if "anchor" in t])
        keep = (found >= 0.85 and anchor < 90) or "--keep-all" in sys.argv
        print(f"{res.get('who') or f}: {len(by)} trials, hidden recording found in {100 * found:.0f} %, anchor rated "
              f"{anchor:.0f}{'' if keep else '  — left out'}")
        if keep:
            allr += rs
    if not allr:
        return
    vers = defaultdict(list)
    for r in allr:
        vers[r["version"]].append((r["rating"], dist[(r["trial"], r["version"])]["score"]))
    print(f"\n{'version':12s} {'rating':>7s} {'distance':>9s}")
    for v, xs in sorted(vers.items(), key=lambda kv: -np.mean([x[0] for x in kv[1]])):
        print(f"{v:12s} {np.mean([x[0] for x in xs]):7.1f} {np.mean([x[1] for x in xs]):8.1f}%")
    # models only: the recording and the anchor are easy ends and would flatter the correlation
    models = [r for r in allr if r["version"] not in ("recording", "anchor")]
    for label, rows in (("all versions", allr), ("models only", models)):
        rho = spearmanr([r["rating"] for r in rows], [dist[(r["trial"], r["version"])]["score"] for r in rows])[0]
        print(f"{label:13s}: rank correlation rating vs distance {rho:+.2f} overall")
    per_trial = defaultdict(list)
    for r in models:
        per_trial[r["trial"]].append(r)
    agree = total = 0
    for rows_t in per_trial.values():
        for i in range(len(rows_t)):
            for j in range(i + 1, len(rows_t)):
                a, b = rows_t[i], rows_t[j]
                if a["version"] == b["version"] or a["rating"] == b["rating"]:
                    continue
                da, db = dist[(a["trial"], a["version"])]["score"], dist[(b["trial"], b["version"])]["score"]
                total += 1
                agree += (a["rating"] > b["rating"]) == (da < db)
    if total:
        print(f"pairs of models in one trial ordered as the listener did: {agree} of {total} ({100 * agree / total:.0f} %)")


if __name__ == "__main__":
    main()
