# How the piano is tested

The piano is judged against **the recordings it was measured from**, against
**what a piano must do** (every note sounds, every note stops), and against
**the owner's ears**, through each PR's preview. This page covers the methods,
the tools, what healthy looks like today, and what to run after which kind of
change. `docs/MODEL.md` explains what is being tested.

## Principles

- **Compare with the recordings, not with intuition.** Almost every
  improvement started as a number that disagreed with the Salamander
  recordings. Problems the owner heard (a hi-hat attack, bell-like high notes,
  hiss, a missing echo, a noisy burst on a few keys) were each found and fixed
  by measuring until the number showed them.
- **Render notes the way they were recorded.** Comparisons render one note
  dry (Room 0) with the **As Recorded** velocity map, so a recorded layer's
  middle velocity plays that layer, and align it to the attack as the
  recordings are aligned (`tools/measure/common.py`). Levels undo the engine's
  −10 dB headroom.
- **Per microphone, averaged over ~0.2 s.** A single mono snapshot is thrown
  off by beating and by the phase between the AB pair's mics.
- **Separate the data from the engine.** When a note is wrong, first check
  whether the *stored* curve already disagrees with the recording (a data
  problem) or the *model* disagrees with the stored curve (an engine problem).
  That's what `tools/measure/partials.py` shows.
- **Look where the error is, not only at totals.** A band's total level can
  be right while its energy is in the wrong place: flat noise between the
  partials (hiss) where the piano has it at the partials. `between.py`
  measures between the partials for that reason.
- **Aggregate before deciding.** Single notes mislead. Decisions about the
  model were made on aggregates over many keys and layers: `between.py` covers
  13 keys × 3 layers.
- **Check every velocity, not a sample of them.** PR #8's first version was
  calibrated on three layers, and the 13th, which the on-screen keyboard
  plays, had 10–25 dB too much noise on a few keys.
- **Measure the old build too.** Keep the previous binary and run the same
  measurement on both. A change is kept only if the aggregate improves, and any
  measure that got worse is reported.
- **Listen.** Before a PR, render what changed. For the treble work, a file
  plays recording | old | new for a few keys.

## The perceptual distance: hearing without listeners

The octave-band measures say where energy is wrong, in dB, but not how much of it a listener
would notice. `tools/measure/perceptual.py` does that from a simple model of the ear, and is the
first number to look at after a change to the sound:

1. Mains hum (50 Hz and harmonics, to 2 kHz) is notched out of both signals.
2. A gammatone filterbank, one band per ERB (the ear's frequency resolution), 40 Hz-16 kHz.
3. Each band's power over 8 ms, every 2 ms: beating and the attack stay; waveform phase and the
   fine structure of noise go.
4. Specific loudness per band (Zwicker's form), above the threshold of hearing, at a playback
   level where a full-scale sine is 100 dB SPL.
5. The difference per band and frame, less 5 % of the louder (about 1 dB, the ear's level
   discrimination).

The distance in a window is the audible difference summed over the recording's loudness there,
in %: the attack (0-80 ms), the early sound (80-500 ms) and the sustain (0.5-3 s). The score is
their mean. Signed versions in low (< 500 Hz), mid (0.5-4 kHz) and high (> 4 kHz) bands say
which way it's wrong. The yardstick is the distance from a recording to the recording one layer
up: the smallest step the recordings take, about 5 %.

Checks of the measure itself:
- **On test signals:** the same partials in other phases 0.3 %; +1 dB 1 %, +3 dB 14 %, +6 dB
  37 %; another draw of the same noise 0.9 % against 5.8 % with the noise missing.
- **Against the history** (`validate_perceptual.py`): every problem the owner heard and a
  commit fixed is a labelled pair. On the keys and part of the note concerned, the build after
  the fix must be nearer the recordings. 7 of 8 are: the hiss (−62 %), the buzzy attack
  (−19 %), the noise at velocity 100 (−16 %), the noisy A1-A2 strikes (−12 %), the 8-16 kHz
  hiss (−6 %), the bell-like high notes (−4 %, weakly: the body's ring is quiet next to the
  partials, so a loudness measure gives it little weight; judge body and noise changes with
  `between.py` too). The eighth, PR #4's "hole" in the treble fundamental, shows as the hole
  closing (sustain mid −17 % → −5 %) while the early sound became 8 % too loud, a wash overall.
- **Against a listener** (`tools/listen/`): a blind test of 10 notes, the recording hidden
  among main, three older builds and an anchor. Rank correlation of rating and distance −0.59
  (−0.43 over the builds alone); of the pairs of builds in one trial, the distance ordered 85 %
  as the listener did (71 % leaving out the hiss build). The listener told the recording from
  the model least well in the low bass, which they rarely hear alone; there the distance is
  the better guide.
- **The recorded strike's test** (PR #10: 12 notes from A2 up, a chord and an arpeggio; main,
  the branch, the recording hidden, an anchor): mean ratings recording 94.9, branch 93.9, main
  88.1, anchor 21.3; rank correlation −0.78 (−0.58 over the builds alone); of the 7 trials where
  the listener rated the builds differently, the distance ordered 6 as they did. The clear
  differences were where main's hiss could be heard: C5 at layers 8 and 13 (95 against 73 and
  68) and the arpeggio (98 against 71), which the distance also put furthest apart. The one
  pair it ordered otherwise was C3 at layer 13 (86 against 96; distance 3.4 against 3.8 %).
  The listener found the test hard: elsewhere the two builds and the recording sounded alike.

So the distance is what to work against while a change is in progress, and listening is for
pull requests: `tools/listen/make_test.py` builds the page (the recording hidden among the
builds given, plus an anchor), `tools/listen/analyze.py` compares the answers with the
distance. Draw most trials from the middle and upper range, where a listener's ear is surest,
and keep the bass from getting further from its recordings on the distance alone.

## Techniques worth knowing

- **Hearing one part alone by subtraction.** Renders are deterministic, so
  rendering twice with only one level changed (Resonance 0 and −40 dB, say) and
  subtracting leaves that part alone, scaled by 1 − 10^(−40/20). Turn down
  anything that shares its noise bank: the body ring shares the mechanical
  noises' bank, and band amplitudes add as √(p₁ + p₂), so the difference isn't
  linear. `mechanics.py` uses this for the release noise and the sympathetic
  ring.
- **Attribution by elimination.** To find what makes an unwanted sound,
  render with each source turned down in turn (Hammer, Body, Key & Pedal,
  Resonance at −40 dB) and see which one removes it.
- **Long spectra for low bands.** A 1024-point frame can't tell 31 Hz from
  70 Hz, and the recordings' 50 Hz hum sits right there. Band levels for
  anything below ~250 Hz come from one long spectrum, with the hum's bins
  (±1 Hz around 50·k) left out.
- **Between the partials.** Leave out each partial (and each cluster
  component) ±max(4 frequency bins, 4 % of f0, 6 Hz) and scale the remaining
  energy up to the whole band. This only works where the gaps are wide next to
  the window's resolution: for a 40 ms window, about D5 up; for 0.1 s, about
  G#3 up. Lower down it measures the partials' own spill.
- **Calibration by resynthesis.** Where an analysis can't be made exact,
  render the model and correct it towards the recording on the same measure
  the error shows on. `calibrate_noise.py` does this for the strike noise.
  Calibrate only what the measure can see. Calibrating A2 on a window too short
  for its partials raised its noise to match their spill, and fast repeats lost
  their attack.
- **Random stress.** Hand-written cases miss the orderings that break things;
  `stress.py` plays hundreds of random fast passages. It found a body ring left
  undamped when a release reached a voice before its strike.
- **Block sizes.** The kit renders every scenario at host blocks of 512, 1, 37
  and 4096 samples and requires the same result. This is how it was found that
  events in `processEvents` count samples from the host block's start, while
  `processGlobal` runs per 32-sample control block.
- **Temporary instrumentation.** For a puzzle (which string still rings?),
  add a `fprintf` to a scratch copy of the engine, render once, and restore.
  Never commit it: the checks fail on I/O on the audio thread.

## The tools

### Render checks (CI, every PR)

`./build/piano_render --check . --out build/renders` runs:

- **The kit's generic checks:** every preset loads, survives a save/load,
  renders at 44.1, 48 and 96 kHz and goes silent after all-notes-off. The
  output matches across block sizes. 25 random patches stay finite,
  allocation-free and end in silence. Any allocation, lock or NaN on the audio
  thread fails.
- **The scenarios in `tests/`:**

| Scenario | What it guards |
|---|---|
| `chord.json` | a chord sounds within 20 ms, is still above −48 dB at 1.4 s, silent 2.1 s after release, limiter under 1 dB |
| `pedal.json` | a pedalled phrase still rings at 3.5 s and has stopped 2.8 s after the pedal lifts |
| `restrike.json` | restrikes of a sounding key, silent at the end |
| `fast-repeats.json` | 20 fast strikes, each above −45 dB in its first 20 ms (a strike that didn't sound) |
| `undamped-treble.json` | a key above E6 rings on after release (no damper) |
| `glissando-pedal.json` | the heaviest load: rings at 4.9 s, stops after the pedal |
| `pedal-noise.json` | the pedal's thump and the strings' ring when it goes down; silence after it comes up |
| `demo.json` | a musical passage: sounds, limiter under 3 dB |

CI also builds the browser page and runs `synthkit/web/test/browser-test.cjs`
in Chromium. It boots on desktop and phone sizes with no console errors, plays
from the computer keyboard and a MIDI keyboard, and renders through the
worklet. CI then compares the browser's render of `tests/chord.json` with the
native one: the worst 100 ms must be under −40 dB (it's about −72). Finally
it builds and checks the plugins (pluginval, auval).

### Measurements against the recordings (`tools/measure/`)

`python3 -I tools/measure/run_all.py --sal ../salamander` runs all of these in
about 1.5 minutes and prints the summaries; `tools/measure/README.md` describes
each. Run it after any change to the sound.

| Measure | Today | Healthy |
|---|---|---|
| `perceptual.py`: perceptual distance, 30 keys × 4 layers | median 8.5 %, mean 12.1 %, attack 8.9 (main before the recorded strike: 9.8 / 15.0 / 12.8; before the modes: 24.4 / 26.5); bass 8.7, middle 5.0, treble 23.0 % (main: 8.7 / 6.9 / 29.2, split at C3 and C6); no note more than 0.3 points further from its recording than on main | median well under ~15 %; yardstick 5.4 % |
| `compare.py`: octave bands over time, median \|error\| | 0.2–3.2 dB (all), 0.2–2.3 dB (first 0.5 s) | 2–5 dB |
| `levels.py`: overall level by time | −0.8 … −0.0 dB | within ~3 dB |
| `groups.py`: partials 1-2 / 3-6 / 7-12 / 13-24 at 0.05 / 0.4 / 1.4 s | within ~1 dB, except 13-24 at 0.4 s (−5 dB) | within ~3 dB |
| `ring.py`: body ring between partials | +0…+7 dB to 1.5 s; −5…−13 dB at 2–3 s | within ~5 dB to 1.5 s |
| `balance.py`: sustain spectrum vs fundamental | 5.1 dB mean \|diff\| | ~5–7 dB |
| `fluctuation.py`: beating, L/R independence | 4.1 dB vs 3.6; correlation 0.55 vs 0.36 | ~3.6 dB; low correlation |
| `decay_by_velocity.py`: stored curves vs recordings | within ~2 dB | within ~2–4 dB |
| `attack.py`: attack bands at 5–500 ms | medians −6…+0 dB (62 Hz −1.1, was −4.1); worst +19 dB (62 Hz, soft C4), +8 dB at 8 kHz (was +18) | no band far above |
| `velocity.py`: even steps; soft→loud changes | even; loudness changes within ~1 dB; the attack brightens from soft to loud within ~4-10 dB of the recordings at 2–8 kHz (A6 still ~10-15 dB short at 2-4 kHz) | match within a few dB |
| `between.py`: between the partials, mean \|diff\| | 1.9 / 2.3 / 2.7 / 6.5 dB at 0–40 ms / –150 ms / –400 ms / –1 s (main: 4.6 / 3.8 / 3.1 / 6.9) | ≤ ~5 dB to 0.4 s |
| `mechanics.py`: pedal, release noise | pedal 1.0 dB, release 1.5 dB | ~1–3 dB |
| `notes.py`: repeats, orderings, release, lifetime, consistency | every restrike rises (≥ 1.2 dB at 14/s; A2-C5 now 2.2-7.9 dB, were 1.3-6.6) but A0's at 14/s (−0.5 dB: the A0 recordings themselves build for ~60 ms, 1.5-1.8 dB louder at 50-68 ms than at 10-40 ms, and now the model does too); strikes steady within ~1.5 dB; the same strike repeated now varies by 0.0-0.1 dB in its first 40 ms from A2 up (was 0.3-1.8 with random noise) | every strike rises (from A1) |

Also:

- `partials.py --key K --layer L`: one note, partial by partial, recording |
  model | stored curve.
- `holdout.py --keys 45,60`: rebuild without some measured keys and compare
  them interpolated. Today A2 and C4 left out land at 3.3–5.0 dB median band
  error (main before the modes: 3.4–4.7), against 0.1–0.8 dB when measured.
  With `--perceptual`, six keys left out at once (F#1 … F#6, each then 6
  semitones from its neighbours, twice the real gap) are 26.3 % from their
  recordings, the same as main before the modes (26.3 %), against 5.5 % when
  measured (main: 18 %). With the recorded strike (left-out keys take the nearer
  key's response): 25.3 % mean, attack 19.8 (26.0 / 21.0 before it, measured on the same
  machine), measured 7.9 % (9.2). **The modes made the 30 measured keys much nearer
  their recordings, but not yet the 58 between them**: what a key holds of its
  own (where the hammer strikes against its partials, how its unison beats) its
  neighbours can't predict, and the per-partial levels of a key differ from its
  neighbours' by 5-10 dB here and there. A strike-position comb (partial levels
  ∝ |sin(π n x0)|, x0 ≈ 1/8.3 from A0 to C4) explains 35-60 % of that
  variation, but x0 is nearly the same on neighbouring keys, so it doesn't help
  between them. Blending partly by frequency rather than partial number
  (`BY_FREQUENCY` in Synth.cpp) was inconclusive (25.6 % against 26.3 %
  perceptually, a little worse in octave bands at A2) and is off.
- `../diagnose.py`: attack rise time, attack brightness, noise between the
  partials, partial pitch (within ~2 cents).
- `panel.cjs`: plays the page in Chromium. The velocity graph must fill in,
  the on-screen keyboard plays velocity 100, and nothing reaches the console.

### Note handling: `tools/stress.py`

`python3 -I tools/stress.py build/piano_render 300` (and `… 200 overlap`)
plays random passages of fast repeats, chords, overlapping notes and pedal
changes. Keys run A0–E6, so every key has a damper. It reports any passage
still sounding (above −80 dBFS) 3 s after everything was released. Healthy: 0
failures in both modes. A failing passage is saved as `/tmp/stuck_<seed>.json`
to render and investigate.

### Speed: `tools/bench-web.cjs`

`node tools/bench-web.cjs build-web/web/piano.html tests/glissando-pedal.json`
renders a scenario offline through the page's real AudioWorklet in headless
Chromium. The machine's speed varies, so compare with main's build on the same
machine. Today:

| Scenario | This build | Main, same machine |
|---|---|---|
| Pedalled 88-key glissando (worst case) | 0.6× realtime (10.9 s for 7 s) | 0.6× (12.0 s) |
| Demo (ordinary playing) | 2.1× | 1.9× |

(Measured on a slower machine than before: there, main's worst case had been 2.1×.) The modes
cost the same as the description they replace: a modal partial is 4-12 components, where the
prompt, burst and aftersound with their beating partners were up to 18. The cost comes from the
sympathetic string bank and the steeper noise filters. Healthy: the worst case no slower than
main's on the same machine. The recorded strike is about 9 % cheaper than the band noise it replaces (a sample read and a
shelf per mic, against ten bands of filtered noise). The page is 4.6 MB (2.9 MB before the
recorded strike: 1.3 MB of it is the strikes, 22 keys × 2 mics × 0.3 s of 16-bit samples;
2.3 MB before the modes).

### Controls that change the model: defaults stay exact

Hardness and Strike Point change the piano around what was measured, so their defaults must leave
it exactly as measured: renders of `tests/*.json` with the previous build and this one are compared
byte for byte (`cmp`). What they do is checked by sweeping them (attack level, centroid and share
above 2 kHz per key; MODEL.md has the numbers) and by listening (a page of renders on the PR).

## What to run after which change

| Change | Run |
|---|---|
| anything | `--check`, and listen to what changed |
| the sound (engine or data) | + `run_all.py` (the perceptual distance first), `bench-web.cjs`; report both in the PR, with a listening test from `tools/listen/make_test.py` |
| the interpolation between keys | + `holdout.py --perceptual` over several keys at once |
| voices, notes, pedal, events | + `stress.py` 300 and 200 overlap, `notes.py` |
| the treble or the noise | + `between.py`, and compare per key at velocity 100 (the on-screen keyboard's) and across layers |
| a control that changes the model (Hardness, Strike Point) | + renders at default against the previous build, byte for byte; a sweep and a page of renders |
| the recorded strike (`analyze_strike.py`) | + `make_header`, build, `perceptual.py`, `velocity.py` (its brightening from soft to loud), `between.py`, `holdout.py --perceptual`, and `tools/listen/ab_strike.py` for an A/B by ear; rerun it after `analyze.py` or `analyze_modes.py` (its notches follow the partials) |
| `analyze.py` | + rerun `analyze_onset`, `analyze_modes` (clear its cache), `analyze_noise`, `analyze_body`, `make_header`, build, `calibrate_noise` ×3, `choose_modes` |
| `analyze_modes.py` | + `analyze_noise`, `analyze_body`, `make_header`, build, `calibrate_noise` ×3, `choose_modes`, `calibrate_attack` ×3 (clear `attack_adjust` first), then `perceptual.py` and `holdout.py --perceptual` |
| the page (UI) | + `browser-test.cjs`, `panel.cjs` |

## Known gaps in the numbers

These are tracked rather than hidden:

- **The keys between the measured ones** are as far from what they should be as before the
  modes (holdout, above): the next thing to work on.
- **The treble** (C6 up, 32.6 %): most of its partials are too noisy for the modal fit to beat
  the older description, which they keep.
- **The bass's strike correction** (`calibrate_attack.py`) matches the recordings' 10-40 ms
  level, and the owner heard the strike as weak without it, but the distance's attack score
  for C3-F#3 went slightly up with it (9.0 → 10.1 %): it places the energy as a fast mode on
  each partial, which isn't quite where the recording has it.
- **The distance can't tell noise from tone within a band.** In this PR's first version the
  strike noise was re-measured against the modes, which hold less of a partial's energy in its
  first milliseconds than the older burst does; the difference became knock, up to 25 dB too
  strong at 1-2 kHz on C5 and D#5. The owner heard it as noise in the attack; the distance
  said those notes were better, since the band levels were right. So after any change to the
  noise or to how partials start, compare the knock levels per key with main's
  (`salamander_noise.json`), not only the distance and `between.py`'s averages.
- **Body ring:** a few dB strong at 0.4–1 s, and 5–13 dB weak at 2–3 s.
- **Upper partials 13–24** come out ~5 dB weak at 0.4 s.
- **A soft C4's 62 Hz thump** is ~20 dB strong.
- **The strike's finer changes with velocity.** One response per key (velocity 100), shaped by
  a shelf and two gains: exact at velocity 100, ~2 points of attack distance short of the
  exact per-layer strikes in the middle and ~4 in the treble at other velocities (offline).
- **A6:** the attack brightens less from soft to loud than the recordings at 2-4 kHz.
- **A7 at 8 kHz:** a little too much between the partials 40-150 ms in, from the body ring's
  band noise (it is there with the strike off too).
- **Sympathetic coupling strength** is a judgment, not a measurement.
