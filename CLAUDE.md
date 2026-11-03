# Piano — notes for Claude

Piano is a synth built on synthkit (the `synthkit/` submodule, pinned).
The owner plays it and gives feedback; Claude writes the code and opens PRs.
The owner tests via the PR's GitHub Pages preview, and installs the plugin zips
from CI at milestones.

## Ground rules

1. **`synthkit/API.md` is the contract.** Work in this repo needs only that
   page plus this repo. Don't read synthkit's source to build the synth; if
   the page is missing something, note it and fix the doc in a synthkit PR.
2. **Don't edit `synthkit/` here.** Kit changes go to the synthkit repo; this
   repo then moves the submodule to the new commit in its own PR.
3. **Real-time safety** (API.md §4a): no allocation, locks, I/O or exceptions
   in `start`, `release`, `render`, `processGlobal`, `processEvents`, `tune`,
   `allocateVoice`. Allocate in `prepare`. No `throw`/`catch` anywhere.
4. **Stable identifiers.** Parameter IDs, `synth.json` `id`, `pluginCode`,
   `vendorCode` and `bundleId` never change once released. Patches store real
   units.
5. **Verify by rendering** before every PR: `--check` passes, and listen to
   (or look at) the renders when the change is audible.
6. Escape hatches and custom state (API.md §4, §5a) are the way to do unusual
   things. A feature moves into the kit when a second synth needs it.
7. **Porting a browser sketch?** Use the Web Audio blocks (`sk::wa`, API.md
   §8a), which match the browser's nodes sample for sample, and compare the
   port's renders with the sketch's. Input from the page that isn't a
   parameter is a message (§9b); a look of the synth's own is `ui/page.js`
   (§9a), with `ui/style.css` for styling either page.

## This synth

- `docs/MODEL.md` (how each part is modelled and measured) and
  `docs/TESTING.md` (how it's tested, today's numbers, known gaps) are the
  reference. Keep them, README and `tools/measure/README.md` current with every
  change to the model or the tests: a PR that changes the sound updates the
  numbers it changes.
- `src/PianoData.h` is generated (`tools/make_header.py`); never edit it by
  hand. The measurements are `tools/salamander*.json`; README has the
  pipeline. The Salamander recordings are CC BY 3.0: keep the attribution in
  README and in the plugin/release notes.
- After any change to the sound, run `tools/measure/run_all.py` (every
  measurement against the recordings, about 1.5 minutes; `tools/measure/README.md`
  says what each answers and what healthy looks like) and
  `tools/bench-web.cjs` for browser speed, and report them. For one note in
  detail, `tools/measure/partials.py --key K --layer L` separates data
  problems from engine problems. Compare per mic and averaged over ~0.2 s:
  single mono snapshots are thrown off by beating and the mics' phase.
- The partial fits feed the noise, onset and body fits: after `analyze.py`,
  rerun `analyze_noise.py`, `analyze_onset.py` and `analyze_body.py` too
  (`analyze_mech.py` is independent of them), then `make_header.py`, build,
  and `calibrate_noise.py` (2-3 passes, `make_header.py` and a build between):
  it renders keys and corrects the strike noise's two stages per band
  against the recordings, between the partials. Re-analysing resets nothing:
  the corrections live in salamander_noise.json (`adjust1`, `adjust2`); clear
  them first if the noise analysis changes.
- Treble partials: `analyze.py` moves each partial to its peak in the first
  0.3 s (`refine_partials`; `dev` cents in the header) and records clusters
  (`clusters`: components within ±70 cents). Played as clusters where wide
  (≥ 15 % of the power off the main component, ≥ 6 Hz spread); otherwise the
  beat model. Only A5 and up were re-analysed with these so far (the rest:
  analysis from before, `f_measured` absent → `dev` 0).
- Noise bands are 2 RBJ band-pass stages (Q 1.2) and a 4th-order low-pass
  at 1.7× the lower edge (`kNoiseLpRatio`): a top key's knock falls ~35-45 dB
  over an octave or two, and plain band-passes spill it upward as hiss that no
  gain solve or calibration can undo. Lower ratios cut the spill further but
  dig 12-17 dB notches at the octave edges. What's left: D#6 and A7 a few dB
  hissy at 8-16 kHz (as before PR #8). Gains are solved in dB (`solve_gains`):
  solving in power let a quiet band switch its loud neighbour off.
- Strike noise is measured between the partials only where they stand well
  apart (`between_ratio`: f0 from about G#3), and calibration uses each
  window only where it can see between them (knock from ~D5, slower part
  from ~G#3; below the fundamental, the whole band from A2). Lower keys keep
  the old cut rule: counting the bass's partial spill as knock made A1-A2's
  strikes noisy and fast repeats lose their rise.
- The noise analysis smooths each band's level across the 16 layers
  (`smooth_layers`): per-layer fits jump 20 dB and more, heard as white noise
  on some velocities only (D#5-F#5, D#6 at velocity 100 in PR #8's first
  version). Calibration measures layers 4, 8, 12, 16 for the same reason.
- Top Dampers (`string.topdampers`): E6 is the last damped key by default, as
  on a grand (measured: the top notes fade after release as when held,
  a little faster than the recordings); All keys damps every key.
- Judge the treble with `tools/measure/between.py` as well as the band
  levels: a band's total can be right with its energy in the wrong place.
- Tried and dropped: calibrating the body ring's level and decay over its
  first second (one line can't follow it; the long tail high notes need went
  17-27 dB too weak at 2-3 s).
- The page's on-screen keyboard always plays velocity 100 (mouse and
  computer keys); judge velocity response with MIDI or renders.
- Known gaps: the dampers' imperfect stop (the `harm*` release samples hold
  each note's residual ring after its damper lands), half-pedalling, the soft
  pedal. The recordings' late tails on top notes are partly mains hum (50 Hz
  and harmonics) and rumble: don't model those (the mechanics analysis leaves
  the hum's bins out).
- Mechanical noises (`tools/analyze_mech.py` → `releaseEnv`/`pedal*` tables)
  play through the body's band-noise bank. The release samples are
  normalised, so their level is the sfz's mix (−37 dB; pedal −20/−19 dB).
- The sympathetic string bank (`renderBank`, in `processGlobal`) drives a
  string only while its damper is up and it isn't sounding as a voice (voices
  set `sounding[note]` each block): a voice driving its own string would
  double it. Key and pedal changes reach it at their sample through a queue
  (`processEvents` sees the whole host block at once; `processGlobal` runs
  per control block): anything else that must follow events in
  `processGlobal` needs the same. The pedal's lift ring is calibrated
  against pedalD (`tools/measure/mechanics.py`); the note coupling
  (`kCoupling`) isn't measurable from these recordings.
- Velocity: `Piano::velocityMap` is the one place velocity becomes tone layer
  and level; the strike and the page's graph (`ui/custom.js`, messages
  `vel.query` / `vel.point` / `vel.note`) both use it.
- `analyze.py` takes about 25 minutes on one core: run halves of the key list
  in parallel and combine them with `--merge` (see `main()`).
- Voices: one per key. A restrike hands the old vibration over to the new
  (it fades three times as fast as the new strike swells), so fast repeats
  can't cancel. A voice ends when its total level is under −84 dBFS. The
  body ring lives in the synth, per key, and is damped from note and pedal
  events in `processEvents` (keys/pedal followed there), not by the voice.
- After any change to voices, notes or pedal handling, run
  `tools/stress.py build/piano_render 300` (and with `overlap`): random fast
  passages must all go silent after release.
- C5 (and nearby) is the weakest measured region: upper partials a few dB
  strong, body ring weak late. Known, not yet fixed. Soft low-mid strikes
  (C4 layer 4) have a 62 Hz thump 15-20 dB too strong (attack.py's worst case). The body ring is a few
  dB strong 0.4-1 s and weak at 2-3 s on many keys (one decay per band).
- Voices read parameters only in `render`, so the strike happens at the
  start of the first render after `start` (`pending`).
- Kit gaps found so far (candidates for synthkit PRs): continuous CC64 for
  half-pedalling (the kit treats it as on/off), release velocity, parameter
  access in `Voice::start`, sostenuto (CC66). API.md doesn't say that an
  event's `.sample` counts from the host block's start, nor that
  `processEvents` runs once per host block and `processGlobal` once per
  control block (found by rendering at different block sizes).

## Fresh workspace

A new session starts without the toolchains. Install them first (Ubuntu; the
same script CI uses):

```
git submodule update --init
synthkit/tools/ci/deps.sh native && synthkit/tools/ci/deps.sh web && synthkit/tools/ci/deps.sh plugin
```

Playwright/Chromium for the browser test: usually preinstalled (try
`NODE_PATH=$(npm root -g)`), else `npm install --no-save playwright@1.48.2 &&
npx playwright install --with-deps chromium`. synthkit's CLAUDE.md has more
(pluginval, a local JUCE checkout).

## Commands

```
cmake -S . -B build -G Ninja && cmake --build build
./build/piano_render --check . --out build/renders
./build/piano_render tests/chord.json --out x.wav
./build/piano_render --params

# browser page (API.md §11)
cmake -S . -B build-web -G Ninja -DCMAKE_TOOLCHAIN_FILE=synthkit/cmake/wasm32-wasi.cmake && cmake --build build-web
node synthkit/web/test/browser-test.cjs build-web/web/piano.html build-web/test tests/chord.json

# plugins (API.md §12)
cmake -S . -B build-plugin -G Ninja -DSYNTHKIT_PLUGIN=ON -DSYNTHKIT_WEB_UI_DIR=$PWD/build-web/web && cmake --build build-plugin
```

## CI

Every PR: native checks, the browser page (Chromium test + browser-vs-native
comparison), a Pages preview linked on the PR, and plugin builds for Windows,
macOS and Linux (plugin check, pluginval, auval) with a zip per OS. A `v*` tag
publishes a release. The steps live in `synthkit/tools/ci/`.
