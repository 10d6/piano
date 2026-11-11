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
- `src/PianoData.h` and `src/PianoStrike.h` are generated (`tools/make_header.py`); never edit
  them by hand. The measurements are `tools/salamander*.json`; README has the
  pipeline. The Salamander recordings are CC BY 3.0: keep the attribution in
  README and in the plugin/release notes.
- **Work against the perceptual distance** (`tools/measure/perceptual.py`, docs/TESTING.md):
  it agrees with the owner's ears on 7 of 8 fixes in the history and with two blind listening
  tests (rank correlation −0.59; −0.78 for the recorded strike). Use it on a branch instead of asking the owner to listen;
  listening tests (`tools/listen/make_test.py`) are for PRs, with most trials in the middle
  and upper range (the owner rarely hears the low bass alone, and rates it least reliably:
  there, trust the distance, and never let the bass get further from its recordings). It
  gives body-ring changes little weight: check those with `between.py` too.
- **Partials are modes** where `tools/analyze_modes.py` fitted them (docs/MODEL.md): 1-6
  decaying sinusoids per partial shared by all layers, with per-layer, per-mic amplitudes,
  fitted to the envelopes in engine terms. They replace the prompt/burst/aftersound/beat
  description, which partials without modes keep (mostly the top treble). The fit caches
  per key in `<tmp>/piano-modes/` (delete it after changing the script); about 40 minutes on
  two cores. After it: `analyze_noise.py`, `analyze_body.py`, `make_header.py`, build,
  `calibrate_noise.py` ×3, then `choose_modes.py` (per key, modes or not, by the distance), then `calibrate_attack.py` ×3 (the bass's strike, which the fit can't see).
  Lessons: the stiff-string line is far off in the bass (A0's partials reach 2 semitones
  sharp of it; follow them up the series, `refine_long`); modes that nearly cancel can't
  survive the header's quantisation (merge twins, refuse cancelling sets); a fit that starts
  late (the bass's long window) must not let fast modes grow back towards the strike; the
  noise analysis must keep subtracting the older description (with its burst), or a
  partial's first milliseconds become knock (heard as attack noise on C5/D#5, which the
  distance missed: it can't tell noise from tone within a band).
- After any change to the sound, run `tools/measure/run_all.py` (every
  measurement against the recordings, about 1.5 minutes; `tools/measure/README.md`
  says what each answers and what healthy looks like) and
  `tools/bench-web.cjs` for browser speed, and report them. For one note in
  detail, `tools/measure/partials.py --key K --layer L` separates data
  problems from engine problems. Compare per mic and averaged over ~0.2 s:
  single mono snapshots are thrown off by beating and the mics' phase.
- **The strike is recorded from A2 up** (`tools/analyze_strike.py` → `salamander_strike.npz` →
  `PianoStrike.h`): each measured key's velocity-100 recording with every partial notched out,
  0.3 s per mic, through a first-order high shelf per key and layer (the felt's brightening:
  15-30 dB less at 2-4 kHz on a soft strike) and two gains per layer and mic (knock 0-40 ms,
  slower part 40-150 ms). It replaced band noise there, which was heard as hiss: the strike
  isn't noise (waveform correlation 0.9-0.99 between neighbouring layers). Below A2 the band
  noise stays (the strike is masked by the partials; recorded, it was no better). Lessons: fit
  the velocity shelf on unweighted bands (weighting by level hid the 2-8 kHz change that
  `velocity.py` shows); a flexible shelf, timing gains or per-layer offsets didn't help, only
  more stored layers would. Rerun it after `analyze.py`/`analyze_modes.py` (its notches follow
  the partials) and after anything that moves the engine's attack (it measures where the strike
  starts in a render). `calibrate_noise.py` skips keys with a recorded strike.
  `tools/listen/ab_strike.py` makes the A/B (current, strike off, recorded strike, recording).
  Never notch the mains hum out of a signal that is kept (only out of measurements): a 5 Hz-wide
  notch smears the attack into faint 50 Hz-harmonic tones for hundreds of ms.
- **Hardness and Strike Point** (`tone.hardness`, `tone.strikepoint`): the hammer's force
  spectrum 1/(1+(f/fc)²), its corner per key and layer (`hammerFc`, `tools/analyze_hammer.py`,
  fitted to how the partials brighten across the 16 layers: 70-86 % of that change from A0 to C6).
  Hardness moves the corner ±3 octaves; partials, the recorded strike (two first-order sections)
  and the swell (τ × (fc/fc')^½) change by the ratio of spectra. Strike Point: |sin(nπx)| against
  x = 12 %. Defaults are bit-identical to the measured piano: keep them so (check with `cmp` on
  renders against the previous build). Harder saturates (+4-6 dB above 2 kHz): the measured
  hammer is already flat to its corner. Rerun `analyze_hammer.py` after `analyze.py`.
- **Stiffness and Overtone Decay** (`string.stiffness`, `string.overtonedecay`): `partialRatio`
  scales B and the `dev` offsets together (voices at the strike, the bank when it changes);
  `overtoneDecayDb` scales each component's decay by (f/f0)^γ within ¼…4, in `updatePoles` and
  `bankPoles`. Defaults bit-identical (`cmp`). A b1 + b3 f² loss law doesn't fit these decays
  (50-90 % error; b3's sign varies), hence the scaling. The body's ring is mostly strings (tones
  fading 5-20 dB/s; partly the free strings above E6), but the recorded ring sounded no different
  to the owner from today's, so it stays band noise (MODEL.md).
- The partial fits feed the noise, onset and body fits: after `analyze.py`,
  rerun `analyze_noise.py`, `analyze_onset.py` and `analyze_body.py` too
  (`analyze_mech.py` is independent of them), then `make_header.py`, build,
  and `calibrate_noise.py` (2-3 passes, `make_header.py` and a build between):
  it renders keys and corrects the band noise's two stages per band
  against the recordings, between the partials (it skips keys with a recorded
  strike, so today it changes nothing).  Then `analyze_strike.py` (needs a build)
  and `analyze_hammer.py`, `make_header.py`, build. Re-analysing resets nothing:
  the corrections live in salamander_noise.json (`adjust1`, `adjust2`); clear
  them first if the noise analysis changes.
- Treble partials: `analyze.py` moves each partial to its peak in the first
  0.3 s (`refine_partials`; `dev` cents in the header) and records clusters
  (`clusters`: components within ±70 cents). Played as clusters where wide
  (≥ 15 % of the power off the main component, ≥ 6 Hz spread); otherwise the
  beat model. Below A5, `analyze_modes.py` moves every partial to its peak in a 3 s
  spectrum, following the series up (`refine_long`), so `dev` is set there too.
- Noise bands (the strike below A2, the body's ring, the mechanism) are 2 RBJ band-pass stages (Q 1.2) and a 4th-order low-pass
  at 1.7× the lower edge (`kNoiseLpRatio`): a top key's knock falls ~35-45 dB
  over an octave or two, and plain band-passes spill it upward as hiss that no
  gain solve or calibration can undo. Lower ratios cut the spill further but
  dig 12-17 dB notches at the octave edges. (D#6 and A7's hiss at 8-16 kHz went with the recorded strike.) Gains are solved in dB (`solve_gains`):
  solving in power let a quiet band switch its loud neighbour off.
- The band-noise strike (below A2 today) is measured between the partials only where they stand well
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
  (`kCoupling`) is a judgment (no two notes sound together in these recordings), though the
  free strings above E6 ring along in every one and could measure it.
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
- The top octave and a half (C6 up) is the weakest measured region (22 %; C8 42 %, D#7 32 %);
  C5 and nearby, long the weakest, is at 5.5 % since the recorded strike. Keys between the
  measured ones are ~25 % (holdout). Soft low-mid strikes (C4 layer 4) have a 62 Hz thump
  ~20 dB too strong (attack.py's worst case). The body ring is a few
  dB strong 0.4-1 s and weak at 2-3 s on many keys (one decay per band).
- **Partial Arp** (`parp.*`, `arp.*` before patchVersion 2; `PianoVoice::arpBlock`): a creative feature, not part of the
  measured piano. Off must stay bit-identical (`cmp` the renders). It scales each component's
  output gain per control block by its partial (`pnum`, set from `curPn` in `strike`), never its
  state. Its own RNG (`arpRng`): drawing from the voice's would move every beat and noise.
  Group Single + Width 0 is the first version's sound: keep it so (`cmp`). Boosting one partial
  alone sounds resonant and artificial to the owner; negative Boost (a notch, the default:
  −20 dB) or Octaves + Width sound more natural. `tests/partial-arp.json` and the *Harmonic Arp* / *Partial Notch* presets
  exercise it.
- **The kit's arpeggiator / sequencer** (`features.arp`, `arp.*`, docs/MODEL.md): Off must stay
  bit-identical (`cmp`). Played keys keep one voice per key; the kit's notes take their own
  voice (`allocateVoice` tells them apart by `playedPending`, counted in `processEvents`,
  since `NoteOn` has no arp flag). Arp notes lift their key's damper and hold it up each block
  (`arpUp`); `processGlobal` drops it when none does (`arpDampers`), never relying on
  `release()`. `arp.envMode` defaults to Off here (`kitDefault`). Tests: `kit-arp`, `kit-seq`,
  `kit-arp-pedal`, `migrate-v1`; presets *Arp Cascade*, *Sequencer Overtones*. Stress with
  Sequencer on needs Top Dampers All keys (lanes above E6 ring on, correctly).
- **Modulation** (`features.modSlots` 16, `standardLfos` 4, `shapes` 4; docs/MODEL.md): with no
  routes the piano must stay bit-identical (`cmp`). A parameter is a destination only where every
  reader can follow it: strike-time tone settings (per voice), the strings' decay settings and the
  Partial Arp (per voice, per block), Space and Resonance (global); the kit makes the phrase
  envelope one (global). Not Stiffness or Stretch (voices and the sympathetic bank must agree),
  not `play.*` (`processEvents` and the voice both map velocity). Changing the LFO or shape count
  moves Drift/S&H LFOs' random paths (*Tape Wow*'s render). Shape curves in presets must start and
  end level where they restart, or they click. Presets *Tape Wow*, *Tremolo Ballad*, *Living
  Hammer*, *Breathing Strings*, *Notch Sweep*, *Beat Pump*, *Tidal Arp*; tests `mod-*`.
- **The page** (`ui/layout.json`): tabs **Piano** (Playing, Tone, Strings, Space) and
  **Synthesis** (one wide accordion: Partial Arp, the kit's arp panels, LFOs, Shapes, the matrix,
  all folded but the matrix); Output below both. New creative features go in the accordion.
- **Space** (`src/Reverb.h`, `space.*`): early reflections per space (image-source shoebox,
  built in `prepare`, played by `wa::Convolver`) and a 16-line feedback-network tail, so Decay,
  Size and Damping are continuous. Each space has its own instance; a change rings the old one out.
  Reverb (`space.room`) 0 must stay bit-identical to the dry piano: every measurement renders
  with it at 0. `prepare` still draws the old room's noise from the generator the noise bands'
  normalisation uses next: keep those draws. Levels are trimmed per space on a staccato chord
  (`trimDb`, `kWet`): after changing a space, `tools/space.py` gives the levels to re-trim from
  and the RT60s to report.
- Voices read parameters only in `render`, so the strike happens at the
  start of the first render after `start` (`pending`).
- Kit gaps found so far (candidates for synthkit PRs): continuous CC64 for
  half-pedalling (the kit treats it as on/off), release velocity, parameter
  access in `Voice::start`, sostenuto (CC66), an arp flag on the `NoteOn` that `allocateVoice`
  sees (`StartInfo::arp` comes only after the slot is chosen), a random seed per LFO that
  doesn't depend on how many LFOs and shapes there are. API.md doesn't say that an
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
