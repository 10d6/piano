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
| `compare.py`: octave bands over time, median \|error\| | 1.8–4.3 dB (all), 1.2–3.3 dB (first 0.5 s) | 2–5 dB |
| `levels.py`: overall level by time | +1.6 … +3.4 dB | within ~3 dB |
| `groups.py`: partials 1-2 / 3-6 / 7-12 / 13-24 at 0.05 / 0.4 / 1.4 s | within ~3 dB, except 13-24 at 1.4 s (−5 dB) | within ~3 dB |
| `ring.py`: body ring between partials | +2…+5 dB to 1.5 s; −5…−13 dB at 2–3 s | within ~5 dB to 1.5 s |
| `balance.py`: sustain spectrum vs fundamental | 6.7 dB mean \|diff\| | ~5–7 dB |
| `fluctuation.py`: beating, L/R independence | 3.5 dB vs 3.6; correlation 0.52 vs 0.36 | ~3.6 dB; low correlation |
| `decay_by_velocity.py`: stored curves vs recordings | within ~4 dB | within ~2–4 dB |
| `attack.py`: attack bands at 5–500 ms | medians −7…+3 dB; worst +23 dB (62 Hz, soft C4) | no band far above |
| `velocity.py`: even steps; soft→loud changes | even; loudness changes within ~1 dB; A6's 8–16 kHz change too large | match within a few dB |
| `between.py`: between the partials, mean \|diff\| | 4.6 / 3.7 / 3.0 / 6.7 dB at 0–40 ms / –150 ms / –400 ms / –1 s | ≤ ~5 dB to 0.4 s |
| `mechanics.py`: pedal, release noise | pedal 1.0 dB, release 1.5 dB | ~1–3 dB |
| `notes.py`: repeats, orderings, release, lifetime, consistency | every restrike rises (≥ 0.4 dB at 14/s); strikes steady within ~1.5 dB | every strike rises |

Also:

- `partials.py --key K --layer L`: one note, partial by partial, recording |
  model | stored curve.
- `holdout.py --keys 45,60`: rebuild without some measured keys and compare
  them interpolated. Today A2 and C4 left out land at 3.4–4.7 dB median band
  error, against 1.7–2.8 dB when measured: interpolation costs about 1–3 dB.
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
| Pedalled 88-key glissando (worst case) | 2.1× realtime | 2.9× |
| Demo (ordinary playing) | 7.3× | 10.5× |

The cost comes from the sympathetic string bank and the steeper noise filters.
Healthy: the worst case above ~2×.

## What to run after which change

| Change | Run |
|---|---|
| anything | `--check`, and listen to what changed |
| the sound (engine or data) | + `run_all.py`, `bench-web.cjs`; report both in the PR |
| voices, notes, pedal, events | + `stress.py` 300 and 200 overlap, `notes.py` |
| the treble or the noise | + `between.py`, and compare per key at velocity 100 (the on-screen keyboard's) and across layers |
| `analyze.py` | + rerun `analyze_noise`, `analyze_onset`, `analyze_body`, `make_header`, build, `calibrate_noise` ×3 (clearing `adjust1/2` first) |
| the page (UI) | + `browser-test.cjs`, `panel.cjs` |

## Known gaps in the numbers

These are tracked rather than hidden:

- **Body ring:** a few dB strong at 0.4–1 s, and 5–13 dB weak at 2–3 s.
- **Upper partials 13–24** come out ~5 dB weak by 1.4 s.
- **C5 and nearby:** upper partials a few dB strong.
- **A soft C4's 62 Hz thump** is ~20 dB strong.
- **D#6 and A7:** a few dB of hiss at 8–16 kHz, the limit of the noise filters.
- **A6:** its soft strikes have too little 8–16 kHz, so its soft→loud
  brightening there is exaggerated.
- **Sympathetic coupling strength** is a judgment, not a measurement.
