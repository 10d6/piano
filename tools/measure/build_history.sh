#!/bin/bash
# Build old commits of the piano, one worktree each, for validate_perceptual.py:
#   tools/measure/build_history.sh <dir> <sha> [<sha> …]
# then, per commit: python3 -I tools/measure/perceptual.py --render <dir>/<sha>/build/piano_render \
#                     --no-yardstick --json <dir>/<sha>.json
# and: python3 -I tools/measure/validate_perceptual.py <dir>
set -u
dir=$1; shift
for c in "$@"; do
  d=$dir/$c
  [ -x "$d/build/piano_render" ] && continue
  git worktree add -f "$d" "$c" >/dev/null 2>&1
  (cd "$d" && git submodule update --init >/dev/null 2>&1 && cmake -S . -B build -G Ninja >/dev/null &&
   cmake --build build >/dev/null 2>&1) && echo "built $c" || echo "FAILED $c"
done
