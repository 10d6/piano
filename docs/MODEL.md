# How the piano is modelled

This explains what the engine does and where each number comes from. The
README has the short version; `docs/TESTING.md` explains how the result is
checked against the real piano.

## Contents

- [Overview: the signal path](#overview-the-signal-path)
- [The approach](#the-approach)
  - [The data pipeline](#the-data-pipeline)
- [The recordings](#the-recordings)
- [The measured piano](#the-measured-piano)
  - [The partials](#the-partials)
  - [The strike](#the-strike)
  - [The body's ring](#the-bodys-ring)
  - [Mechanical noises](#mechanical-noises)
  - [Sympathetic resonance: the free strings](#sympathetic-resonance-the-free-strings)
  - [Voices, dampers and the rest](#voices-dampers-and-the-rest)
- [Controls on the measured piano](#controls-on-the-measured-piano)
  - [Velocity](#velocity)
  - [Brightness, range and cut](#brightness-range-and-cut)
  - [The hammer's spectrum and Hardness](#the-hammers-spectrum-and-hardness)
  - [Strike Point](#strike-point)
  - [Strike Depth](#strike-depth)
  - [The soft pedal](#the-soft-pedal)
  - [The strings: Stiffness and Overtone Decay](#the-strings-stiffness-and-overtone-decay)
  - [A key struck while it rings: Restrike](#a-key-struck-while-it-rings-restrike)
- [Space](#space)
- [Creative additions](#creative-additions)
  - [Touch Point](#touch-point)
  - [Listening Point](#listening-point)
  - [The partial arpeggiator](#the-partial-arpeggiator)
  - [The kit's arpeggiator and sequencer](#the-kits-arpeggiator-and-sequencer)
  - [Modulation](#modulation)
- [What isn't modelled (yet)](#what-isnt-modelled-yet)
- [Parameters](#parameters)
- [Appendix A: Glossary](#appendix-a-glossary)
- [Appendix B: Design history](#appendix-b-design-history)
  - [The body's ring: calibrating level and decay by resynthesis](#the-bodys-ring-calibrating-level-and-decay-by-resynthesis)
  - [Overtone Decay: a physical loss law](#overtone-decay-a-physical-loss-law)
  - [Space: the 1.6 s noise room it replaced](#space-the-16-s-noise-room-it-replaced)
  - [Strike Depth: the first Strike Point (PR #11)](#strike-depth-the-first-strike-point-pr-11)

## Overview: the signal path

A note travels through these stages. Each has a section below.

1. **Velocity** becomes a recorded *layer* (the tone) and a level (`Piano::velocityMap`; Velocity).
2. **The partials** of the two measured keys nearest the note are blended into a set of decaying
   sinusoids, with per-microphone levels and phases (The partials). The controls that reshape them at
   the strike are Brightness, Hardness, Strike Point, Strike Depth, Stiffness, Soft Pedal and Restrike.
3. **The strike** (the hammer, the action and the soundboard) is added: a recorded response from A2 up,
   band noise below (The strike).
4. **The body's ring** and the **mechanical noises** are band noise shared by all keys, in the synth
   rather than the voice (The body's ring; Mechanical noises).
5. **The free strings** are driven by the mix of everything above and ring in sympathy (Sympathetic
   resonance).
6. **Width and the reverb** come last (Space).

Controls that go beyond the measured piano (Touch Point, Listening Point, the arpeggiators and the
modulation matrix) are under Creative additions. Every control that departs from the measured piano
defaults to the measured sound, bit for bit; each section says so for its own control. Terms such as
*partial*, *mode*, *layer* and *perceptual distance* are in the Glossary at the end; the stages the
analysis tools feed are in The data pipeline.

## The approach

Each note is a **sum of decaying sine partials** (modal synthesis). What isn't a partial is
the strike (the hammer, the action and the soundboard's response in the first 0.3 s), played
from A2 up as each key's own recorded response with its partials removed, 0.3 s per measured
key (see The strike, below); band noise for the strike below A2, for the body's ring and for
the mechanism. Around the notes there is a bank of free strings for sympathetic resonance,
and around the piano a generated reverb (Space, below: the recordings are close-miked).
Apart from those strikes there is no audio from the recordings in the synth. The work is
split in two:

1. **Analysis** (`tools/*.py`, offline, Python). It measures the recordings of
   a real grand and writes small tables: `tools/salamander*.json`, turned into
   `src/PianoData.h` by `tools/make_header.py`. The tables come to a few
   hundred kB of source and compile to much less.
2. **Engine** (`src/Synth.cpp`, C++ on synthkit). It plays those tables back in
   real time, in the browser (WASM, AudioWorklet) and as a plugin.

The rule throughout is to **measure first, then model**. Each part of the
sound is measured from the recordings; the model gets only the structure
needed to reproduce it. Where a part couldn't be measured, this document says
so. Every part was then checked by rendering the model and comparing it with
the recordings (TESTING.md). Several measurements were later corrected by
comparing renders with the recordings: *calibration by resynthesis*.

### The data pipeline

```
recordings ─ analyze.py ── salamander.json ─ analyze_onset.py ─ salamander_onset.json
           │                     │                                     │
           │                     └──── analyze_modes.py (modes into salamander.json) ──┬─ analyze_noise.py ─ salamander_noise.json ─┐
           │                                                                           └─ analyze_body.py ── salamander_body.json ──┤
           └ analyze_mech.py ─────────────────────────────────────────────────────────────────────────────── salamander_mech.json ─┤
                                                                                                                                    │
                                                    make_header.py → src/PianoData.h → build ───────────────────────────────────────┘
                                                    calibrate_noise.py (renders; adjusts salamander_noise.json; repeat ×3; keys below A2 only)
                                                    analyze_strike.py (recordings + a build → salamander_strike.npz; then make_header, build)
          salamander.json ─ analyze_hammer.py ─ salamander_hammer.json (the hammer's corner; into make_header)
          salamander.json ─ analyze_point.py ── salamander_point.json (each key's strike point and comb; into make_header)
                                                    choose_modes.py (renders; per key, modes or not; then make_header, build)
```

The commands are in the README. Everything downstream of `analyze.py` uses its
partials, so rerun those steps after it; the noise analysis uses the modes' envelopes
(`analyze.partial_env`), so it follows `analyze_modes.py`. The modal fit takes about 40 minutes
on two cores and caches each key in `<tmp>/piano-modes/`. Clear the noise corrections before
recalibrating if the noise analysis changed. `analyze.py` takes about 25
minutes on one core; run halves of the key list in parallel and `--merge`
them.

## The recordings

**Salamander Grand Piano V3** by Alexander Holm (a Yamaha C5 grand), CC BY
3.0, from the sfz conversion at `github.com/sfzinstruments/SalamanderGrandPiano`:

- 30 keys, every minor third from A0 to C8, each at **16 velocity layers**,
  48 kHz / 24-bit, through **two AKG C414s in an AB pair** about 12 cm above
  the strings. The model keeps the two microphones apart throughout: every
  level, phase and noise band is per mic.
- `rel1`–`rel88`: each key's mechanical noise as it comes back up. These are
  normalised, so their level comes from the sfz's own mix, −37 dB.
- `pedalD1/2`, `pedalU1/2`: the sustain pedal going down and up (sfz mix −20
  and −19 dB). `pedalD` lasts 6 s, mostly the free strings ringing after the
  dampers lift.
- `harm*`: each note's residual ring after its damper lands. Not used yet.

Things to know about these recordings:

- The note files are aligned to their attack: the analysis starts 2 ms before
  the mono signal first crosses 5 % of its peak (`analyze.load`).
- They hold the recording room's **mains hum** (50 Hz and harmonics) and some
  rumble, which dominate the top notes' late tails. Analyses leave the hum's
  frequency bins out, and the model doesn't reproduce it.
- The **58 keys between** the sampled ones are interpolated (see below).
  `tools/measure/holdout.py` checks how well that works: a key left out lands 25 % from its
  recording, and `tools/measure/neighbour.py` shows a real neighbouring key's recording, played
  at its pitch, is as far (24.6 %). So the interpolation has nothing left to learn from the
  neighbours, and this is not listed as a gap (docs/TESTING.md).

## The measured piano

What the recordings hold and how the engine plays it back: the partials, the strike, the body's
ring, the mechanical noises, the sympathetic strings and the voices.

### The partials

Each key is a set of **components**. A component is a complex one-pole
resonator `z ← z·r·e^{jω}`, with gains from its real and imaginary parts to
the left and right outputs. The voice keeps the components in
structure-of-arrays form in lanes of 8, so the inner loop vectorises (WASM
SIMD in the browser). Its state starts at the measured amplitude and phase.
Everything about a component comes from measurement.

#### Frequencies

- **Stiffness and tuning.** `analyze.py` fits each sampled key's f0 and
  inharmonicity B to its partial peaks, `f_n = n·f0·√(1 + B n²)`. The fit
  uses one 3 s spectrum of the four loudest layers summed: peaks within ±6 %
  of f0 of the prediction, weighted least squares on (f_n/n)² = f0²(1 + B n²),
  iterated. The key's pitch is the recording's own **stretch tuning**: bass
  up to 22 cents flat, top up to 53 sharp (A7 +40, C8 +53: the header's `cents` are the
  fundamental's, f0·√(1 + B), against equal temperament). It's scaled by the Stretch
  parameter in `tune()`. Where the top keys' fits fail, `make_header.repair`
  replaces B or cents along the line through the two keys below.
- **Where partials really are** (`refine_partials`). In the treble the formula
  is 10–30 cents off for some partials. Early in A6's note, its third partial
  peaks 15 cents flat of it, and the predicted frequency sits in a valley 30 dB
  below the peak. So each partial is moved to its own peak within ±60 cents,
  in the first 0.3 s, where the string's partial is strongest. A peak counts
  if it stands 25 dB above the spectrum's median and 15 dB above its
  surroundings. The header stores the offset as `dev` (cents) and the engine
  applies it.
- **Clusters** (`clusters`). A top key's partial is the unison's two or three
  strings, tens of Hz apart, sometimes with a component well to the side: A6's
  third partial has 23 % of its power 59 cents up. From the first 0.5 s,
  `analyze.py` records up to four components within ±70 cents and 20 dB of the
  strongest, at least 3 Hz apart, each with its share of the power. A cluster
  counts as **wide** if at least 15 % of its power is off the main component
  and it spreads over 6 Hz or more. Wide clusters are played as their
  components: they start in phase, since the hammer strikes all the strings
  together, then drift apart. Each component reaches the mics at its own
  angle (±0.6 rad), so left and right fluctuate differently. Narrower ones
  use the beat model below.

  Clusters were recorded for A5 and up only. Below A5, `analyze_modes.py` moves each partial to
  its peak in a 3 s spectrum (`refine_long`), so `dev` is set there too: nearly every partial of
  every key has a nonzero offset.

#### Modes: the partial fitted as the engine plays it

Most partials (1073 of the 1251, 86 %, and most of the sound below C6) are now played as their
**modes**, fitted by `tools/analyze_modes.py`. The subsections after this one describe the
older description, which the remaining partials keep.

A struck unison's partial is a handful of modes: the two or three strings, each vibrating in
two polarisations, coupled through the bridge. Each is a decaying sinusoid with its own
frequency and decay rate, the same at every velocity (once the hammer has left, the strings are
a linear system); the strike only sets how much of each mode there is, in what phase, at each
mic. The prompt sound and the aftersound are such modes, a fast one and a slow one (Weinreich),
and two close in frequency beat. So a partial is fitted as

    z(layer, mic, t) = Σ_m c[layer, mic, m] · e^{s_m t} · (1 − e^{−(t−d)/τ})

- **s_m = −α_m + j2πΔf_m** per mode: decay and offset from the partial's frequency, shared by
  all 16 layers and both mics. 1-4 modes (6 from C5 up, where the unison's strings and their
  polarisations spread wider), each added only while it lowers the error by more than 15 %.
- **c** per layer, mic and mode: level and phase.
- **The swell** (τ, d per key and layer) from the onset fit, as the engine plays it.

**How it's fitted.** Against each layer's and mic's heterodyned envelope (`analyze.envelope`),
with the envelope's triangular window applied to the model exactly, so model and recording are
compared in the same terms. The modes start from a subspace estimate (ESPRIT) over the loudest
layers, the best few sets of its candidates, and analyze.py's decay shape and beat; then
**variable projection** refines them (for given modes, the amplitudes are a linear
least-squares problem, solved for all signals at once). The error is weighted by
level^−0.9 (each moment counts about as it's heard then, against the note at that time) and
evenly in log time, so the first 80 ms, the next 0.4 s and the seconds after weigh alike, as
the perceptual measure's three windows do.

Guards:
- **Where the partial really is.** Below A5 each partial is first moved to its peak in a 3 s
  spectrum (within ±60 cents and ±0.4 f0): heterodyned a few Hz off, a partial is seen only
  through the window's skirt. C1's 15th partial is 7 Hz (25 cents) above the stiff-string
  line, and its old level and decay were far off for that reason.
- **The fit window** runs from when the envelope's window has cleared the swell until the
  partial, smoothed over 0.2 s, last stands 10 dB over the recording's floor. (Ending at the
  first time it dips there, a beat's trough, cut some fits to a fraction of a second.)
- **No growing back.** A fast mode may not rise more than 30 dB over its level at the fit's
  start by t = 0, nor more than 12 dB 20 ms in. The bass's long window starts the fit ~0.1 s
  in, and modes free to grow towards the strike made A0's attack 30 % too loud.
- **Modes that swell** far above the fit's start before it (large modes nearly cancelling) are
  refused, and no mode rings longer than 0.3 dB/s.
- **The modes must beat the old description.** In dB against the recording's envelope at the
  fit's points, weighted the same way; a partial whose modes don't keeps the older fields.
  The median dB error is 0.5-1 dB in the bass and middle against 2-5 dB for the decay shapes.

**In the engine.** Each mode is one component and its swell twin. The partial's frequency is
the modes' energy-weighted mean (so `dev` is set for every modal partial), and the modes'
offsets from it are the unison's spread, which **Unison** scales (0 % puts them on one
frequency). The engine's t = 0 is the swell's start, `delay` into the recording, so each mode's
phase is advanced to it and its level lowered along its decay. Layers where the partial was too
weak to fit take the nearest fitted layer's modes, moved by the difference in the partial's
measured level. Between measured keys, the modes come from the nearer key, their levels move to
the blended partial level and their decays scale to the blended characteristic decay (the
modes' energy-weighted mean). A modal partial is at most 4-6 modes, 8-12 components, against
up to 18 for the older description's prompt, burst and aftersound with their beating partners.

**The bass's strike, calibrated** (`calibrate_attack.py`). Below A3 the envelope's window is
long (45 ms at A2), so the fit starts 50-100 ms in and the strike before that is extrapolated:
it came out 2-3 dB weak on A1, D#2 and A2 and 1.5 dB weak on C3-F#3 (the owner heard less
punch than on main). Each key is rendered at four layers, its 10-40 ms level in octave bands
compared with the recording's, and corrected over three passes, interpolated across layers.
The engine plays a correction as one more mode per partial in the band, on its strongest
mode's frequency, in phase (or in opposition), fading at 100 dB/s. The strike is now within
about ±1 dB of the recordings on most keys (C1 +2-3, A1 −2).

#### Decay: prompt, aftersound and first burst

A piano partial decays in two stages:

- **The prompt sound** fades fast, while the unison's strings move together
  and drive the bridge hard.
- **The aftersound** follows, slower, as they drift out of phase.

`analyze.py` measures each partial's envelope by **heterodyning**: it shifts
the partial down to 0 Hz and averages with a triangular window, T =
clamp(5/f0, 12 ms, 200 ms), short enough to keep neighbouring partials apart.
It then fits the envelope in dB:

- **A hinge** (`hinge_fit`): two continuous lines (s1, s2 in dB/s) meeting at
  a knee. Points are weighted 1/(t + 0.3 s), so the loud start, which is heard
  most, isn't flattened by a long quiet tail. A refit of the tail keeps late
  slopes honest.
- **A first burst above that line** (`fit_curve`): level x0 and fast decay
  s0, the strike's transient before the strings settle, gone within 200 ms.

The fits are made per **velocity group**, because soft strikes sustain
relatively longer:

| Group | Layers | Centre layer (0-based, `kShapeLayer`) |
|---|---|---|
| soft | 2, 4, 6 | 3 |
| medium | 8, 10, 12 | 9 |
| loud | 14, 15, 16 | 14 |

Within a group, the layers' envelopes are shifted onto each other and
averaged before fitting. Each is first smoothed over a window growing with
time (`smooth_beats`), so beats don't drag the line into their troughs. The
engine interpolates between the groups by tone layer.

In the engine each part of a partial (prompt, burst, aftersound) is its own
component. All share **one frequency**, so they don't beat against each other
at the knee.

#### The swell at the strike

A partial doesn't start at full level: it swells in as **1 − e^{−t/τ}**, over
the hammer's contact and the soundboard's response.

- **Analysis** (`analyze_onset.py`): it fits τ and a start delay d to the first
  40 ms of each key and layer, using the measured partials (levels, phases,
  frequencies, decays) with a free gain. `make_header.clean_onsets` keeps good
  fits, takes each key's median and clamps it to 1–15 ms.
- **Engine:** each component gets a **negative twin**, the same component
  with an extra decay of 8.686/τ dB/s, so the pair sums to (1 − e^{−t/τ}) times
  the component. The measured phases are advanced by d, so the swell lines up
  with t = 0. **Attack** scales τ. **Soft Attack** lengthens it for gentler
  strikes: τ·2^(soft·2·(1 − v)), so at 100 % a half-velocity strike swells
  twice as slowly. That is the felt's doing and a few ms: 0-0.5 % by the perceptual distance,
  not to be heard. The control runs on to 600 % as an effect (τ no longer than 0.3 s): soft and
  medium notes swell in over tens to hundreds of ms (16-30 % at velocity 30-64, 2-10 % at 100).

#### Levels and phases per velocity and microphone

For each partial, layer and mic, `analyze.py` reads the complex amplitude over
a 40 ms window from 15 ms (later for low keys, whose analysis window is
longer). It brings it back to t = 0 along the partial's own two-stage decay,
which gives a **level** (centi-dB in the header) and a **phase** (int8). The
two mics' gains and the phase between them place each partial in the stereo
image, as the AB pair heard it. Phases matter: with all partials in sine phase
the sum was a buzzy pulse train.

Where a cluster's power is wider than the envelope window takes in, its levels
are raised by the difference (0–3 dB in practice).

#### Unison beating

`beat_of` measures each partial's beating from the residual of its envelope
after the decay line:

- The residual's slow curvature is removed first (a cubic).
- The strongest periodicity at 0.3–8 Hz is found, and its depth is measured
  as a sinusoid.

This one-sinusoid depth underestimates how much a real unison fluctuates, so
`make_header` scales it by `BEAT_SCALE` (1.8). That value is calibrated on
`tools/measure/fluctuation.py`: about 3.5 dB against the recordings' 3.6.

In the engine, each part of a partial gets two **beating partners** at q of
its level, from the measured depth:

- one heard only on the left mic, at +bt Hz;
- one heard only on the right, at a rate 0.8–1.25 × that.

So the two channels beat independently, as the spaced pair does. Each
partial's rate is randomised within ±25 % of the measured one, so beats don't
line up across partials. Partners start nearly in phase with their partial
(±0.3 rad), so a strike is never cancelled. The main component sits at −bt/2,
so the pair is centred on the measured frequency. **Unison** scales every
offset: 0 % is strings in perfect tune.

#### Between the sampled keys

A key between two sampled keys takes from both: weights by position, B and
onset τ interpolated in log, cents linearly. A modal partial takes its modes from the nearer
key (see Modes, above). Each partial's level is the modes' power at the strike where it has
modes, so the blend compares like with like (analyze.py's own was read at its own frequency,
which in the bass could be a few Hz off the partial and up to 30 dB low). A partial measured on only one
side fades out towards the other (−24 dB at the far end). Phases and clusters
come from the nearer key, since phases can't be averaged meaningfully.

### The strike

What the recordings hold **between the partials** in the first 0.3 s: the hammer's knock, the
key and action, the soundboard's response. It used to be played as octave bands of random
noise on every key. But it isn't noise: it is the same response every time the key is struck.

- Between neighbouring velocity layers its waveform correlates **0.9-0.99** from D#2 up (0.6-0.9
  between layers 8 and 16), with no time shift.
- Between the partials it is tone-like: spectral flatness ~0.3, where band noise is ~0.55.
- Played as noise, whatever a band's level got wrong was heard as **hiss**: the white noise
  heard in the attack of C5, D#5, D#6 and A7 (in the A/B of the recorded strike against the
  band noise, the noise was 8-17 dB too strong in some band on each of them).

#### From A2 up: the recorded strike (`tools/analyze_strike.py`)

- **One response per measured key and mic**: its velocity-100 recording (layer 13, what the
  page's on-screen keyboard plays) with every partial notched out, 0.3 s, faded out from
  0.15 s (the body's ring carries on after that). The notches: every partial and cluster
  component the model plays, then the stiff-string series up to 22 kHz, ±max(16 Hz, 4 % of
  f0, 1.5 × the modes' spread + 8 Hz), raised-cosine edges; zero-phase over one long FFT, so nothing between
  them is touched. Not the mains hum: notches that narrow smear the strike's burst at those
  frequencies over hundreds of ms, as faint tones at 250, 300, 350 Hz… In PR #10's strikes they
  stood 1-6 dB under the strike's own 125 Hz-1 kHz from 40 ms on; without them, the treble came
  0.4-2.4 points nearer its recordings (mean 12.1 → 11.8 %). Stored as 16-bit samples at 48 kHz in
  `src/PianoStrike.h` (generated by `make_header.py`): 22 keys, 1.3 MB.
- **Velocity changes its brightness as the felt does.** A soft strike's knock has 15-30 dB less
  at 2-4 kHz than a velocity-100 one, a loud one 5-14 dB more. A first-order high shelf
  follows that within 1-3 dB per octave band (125 Hz-8 kHz, first 40 ms; with level alone,
  3-11 dB): one corner frequency per key (250 Hz-2.5 kHz, mostly 1-2 kHz) and a gain per layer
  (about −10…−24 dB at layer 1, +4…+15 at layer 16), smoothed across the layers and kept within
  −24…+18 dB, since the softest layers' top bands are the recordings' floor. A more flexible
  shelf (second order, a corner per layer) fitted no better.
- **And its level**, per layer and mic, in two windows: the knock (0-40 ms) and its slower part
  (40-150 ms), measured after the shelf and smoothed across the layers. The engine crosses from
  one gain to the other over 30-50 ms.
- **What it doesn't keep:** the finer changes of the strike's spectrum with velocity. Exact
  per-layer strikes would bring the attack another ~2 points nearer the recordings in the middle
  and ~4 in the treble (offline: 4.5 / 5.2 % against 6.6 / 9.2 % for one response), but cost 16
  times the data; a second response (layers 6 and 14, crossfaded) won only about a point.
- **Where it starts.** Recordings and renders are compared aligned to their attack (2 ms before
  the mono signal first crosses 5 % of its peak). The response starts where that point falls in
  the engine's own render of the key at layer 13, strike off: 0.7-2 ms before the render's
  start, so its first samples are skipped (`strikeStart`).
- **In the engine** (`PianoVoice::renderStrikes`): the nearer measured key's response, read at
  the host's sample rate (linear interpolation), through the shelf, at the window gains for
  the tone layer, with the level blended between the two measured keys where both have one.
  Up to four play at once per voice, so a fast repeat doesn't cut the last one off. After the
  key comes up, what is left of it fades at the damper's rate. **Hammer** scales it.
- **Below A2** the partials are too dense to leave much between them (30-47 % of the spectrum is
  notched at A0-F#2), and the strike is masked by them: offline, the recorded strike made no
  difference to the perceptual distance there (8.5 against 8.6 % in the bass) or was a little
  worse (C1 at layer 12, 9.5 → 11.2 %). Those keys keep the band noise below.

#### Below A2: band noise (`tools/analyze_noise.py`)

- **Bands.** Ten octave bands from 31.25 Hz to 16 kHz. Each is two RBJ
  band-pass stages (Q 1.2) followed by a 4th-order Butterworth low-pass at 1.7×
  the band's lower edge, scaled so white noise comes out at unit RMS. The
  steep upper side matters: a top key's knock falls 35–45 dB over an octave or
  two between its partials, and plain band-passes spilled it upward as hiss
  that no gain setting could undo. The band gains are **solved against the
  filters' overlap** (`band_matrix`, `solve_gains`), in **dB**, so each band's
  output, spill included, matches the measured spectrum as closely as it can.
  Solving in power let a quiet band silence its loud neighbour, which left
  holes 20–30 dB deep.
- **Two stages.** A band holds **the knock** (decay s1, often hundreds of
  dB/s) and then **a slower part** (s2, at least 40 dB/s), `noiseDrop` dB
  down at the strike. One line through both faded the knock too slowly, which
  was hiss, and lost the slower part. The slower part sits among the strings
  and **stops with the damper**; the knock itself isn't damped.
- **In the voice.** Each voice has its own noise per band and mic, swelling in
  with the partials' τ. The levels are per key, layer and mic, interpolated
  like the partials. **Hammer** scales it.

How it's measured (`analyze_noise.py`). It subtracts analyze.py's partial description (level,
decay shape and first burst), not the modes: the modes are fitted from where the envelope's
window clears the swell, so in the first milliseconds they hold less of a partial than its
burst does, and measured against them the difference became knock (heard as noise in the
attack of C5 and D#5):

1. **Frames.** 1024-point frames every 2 ms over 0–250 ms. In each band, the
   recording's power minus what the partial model accounts for.
2. **Between the partials only.** This residual is scaled to its share that
   lies *between* the partials. The share comes from one long spectrum with
   each partial (and cluster component) ±max(16 Hz, 4 % of f0) left out
   (`between_ratio`). In the treble the partials' skirts don't fit the narrow
   partial model, and before this they had become flat hiss: 15–25 dB too
   much at 8–16 kHz on the top keys. This only works where the gaps between
   partials are wide next to the partials' skirts, from about G#3 up. Lower
   down, "between" is mostly the partials' own spill: counting it as knock made
   A1 and A2's strikes noisy and uneven, and fast repeats lost their attack. So
   lower keys keep the earlier rule instead: in bands that hold partials, a
   residual that fades like a string is cut by up to 25 dB, and below the
   fundamental by up to 18 dB.
3. **Two-stage fit.** A hinge fit per band gives s1, s2 and the drop between
   them. These are pooled over the louder layers and both mics, weighted by
   level.
4. **Level at the strike.** The peak frame, taken back to the attack's start
   along the pooled s1 (at most 6 dB).
5. **Smoothed across velocity.** The level is smoothed across the 16 layers: a
   median of three, then a quadratic, kept within 6 dB of the data. Per-layer
   fits jumped 20 dB and more between neighbouring layers, which was heard as
   white noise on some keys at some velocities.

Then **calibration by resynthesis** (`calibrate_noise.py`). It skips keys with a recorded strike, and its windows
start at A2, so today it changes nothing; it is kept for the band noise should the recorded strike's
lowest key move:

- It renders every measured key at layers 4, 8, 12 and 16. Between the
  partials, it compares 0–40 ms (the knock) and 40–150 ms (the slower part)
  with the recording. Each window is used only on keys whose partials stand
  far enough apart for it to see between them: the knock from about D5 up,
  the slower part from about G#3 up.
- Below a key's fundamental there are no partials, so there the whole band is
  compared, on every key from A2 up.
- It corrects each key's two stages per band by 60 % of the median error, run
  for 3–4 passes with a rebuild between. The corrections (`adjust1`,
  `adjust2`) live in `salamander_noise.json`, and `make_header` applies them.

Calibrating the ring's level and decay over its first second was tried and dropped (Design history).

### The body's ring

After each strike the soundboard, case and room ring on. This is diffuse
sound between the partials, a couple of dozen dB under the note, fading over
seconds. It gives high notes their **echo-like tail**; without it they're a few
pure partials and sound like a bell.

- **Measured** (`analyze_body.py`): energy between the partials in octave
  bands, over windows from 0.1 to 3 s.
  - Each partial (and cluster component) is left out ±max(4 bins, 4 % of f0,
    6 Hz), and the result is scaled back up for the bins removed.
  - Mains hum is left out, and the recordings' own floor is subtracted (the
    last 0.4 s of the quietest C8 and A7 takes).
  - Values must be at least 6 dB above that floor.
  - Each band gets one decay (a common slope over the louder layers and both
    mics) and a level at the strike per layer and mic.
  - Below C3 the partials are too dense to separate the ring, so those keys
    use C3's.
- **Played** by one bank of band noise shared by all keys, in the synth rather
  than the voice. Each key adds its band powers, which add as independent
  noises do, with a 30 ms rise. The ring therefore outlives a short note's
  voice.
- **Damped** with the key: the synth follows keys and the pedal in
  `processEvents`. A key's ring ends under −95 dB. **Body** scales it.

The noise generators draw a fresh sample every time, sounding or not, so the
output doesn't depend on the host's block size.

#### What the ring is (measured for a string-based model, not adopted)

Between the partials, 0.5-2.5 s after the strike, the recordings hold mostly **tones** (30-93 % of
the energy in narrow peaks, by band) fading at **5-20 dB/s**, far slower than a soundboard's or a
room's: strings ringing along. Part of it is the free strings above E6 (on C6 at velocity 100,
half the tonal power lies on their partials, against 1 % by chance); the rest is likely the damped
strings' imperfect stop and their back lengths. The model's own free strings (the sympathetic
bank) give 20-60 dB less than that ring, and the band noise is 5-9 dB strong at 0.3-1 s, 7-19 dB
weak at 2-3 s and almost all noise. But with the recording's own ring in its place
(`tools/listen/ab_ring.py`) the owner heard no difference from today's, so it was left as it is.
A string-based ring would make the coupling (`kCoupling`) measurable from the free strings' ring.

### Mechanical noises

From the release and pedal recordings (`analyze_mech.py`): each recording
becomes an **envelope** in 10 ms frames (its total power, under its peak),
plus **band gains per mic**. The band gains come from one long spectrum of the
whole recording, so the lowest band is resolved, with the hum left out.

| Noise | When | Level |
|---|---|---|
| Key release (rel1–88) | each key's note-off | sfz −37 dB, following the strike's velocity (82 % of 40·log10 v, with v from 0 to 1: full velocity is 0 dB, half about −10 dB), since the kit has no release velocity |
| Pedal down (pedalD) | CC64 rising past half, takes alternating | the thump only (the recording less its string ring), sfz −20 dB |
| Pedal up (pedalU) | CC64 falling past half | sfz −19 dB |

They play through the body's noise bank, so they cost almost nothing, and
start at their event's sample. **Key & Pedal** scales them.

### Sympathetic resonance: the free strings

Every string's lower partials (up to the 16th, below 4 kHz) form a bank of
about 850 resonators. Each is at the frequency the voices play (B, stretch,
`dev`, and concert pitch, read from the voices), with the string's measured
aftersound decay for a medium strike.

- **Free and driven.** A string is free when its damper is up: the pedal is
  down, its key is held, or it's above the top damper. A free string that
  isn't sounding as a voice of its own is **driven by everything the piano
  plays**: the mono mix of the voices, body and mechanical noise, before the
  room. So it picks up the partials near its own. The octave below a note sets
  the upper string going, a fifth sets its twelfth, and so on. A voice driving
  its own string would double it, so voices report `sounding[note]` each
  block.
- **Coupling.** The drive is z += K·x/fs, with K = 0.06/s (`kCoupling`)
  scaled by **Resonance** (−40…+24 dB). It is a judgment: the recordings were made note by
  note, so no two notes sound together in them. K was 0.015/s, and Resonance (then to +12 dB)
  could not be heard: on a short arpeggio with the pedal held the sympathetic strings were
  43 dB under all the sound while it played and 22 dB under it in the tail. Now
  (`tools/measure/settings.py`):

  | Resonance | under all the sound, 0.5-1.5 s | in the tail, 4-5 s | the tail is louder by |
  |---|---|---|---|
  | −12 dB (what the old K gave at 0 dB) | 43 dB | 22 dB | 0.1 dB |
  | 0 dB | 31 dB | 10 dB | 0.6 dB |
  | +12 dB | 19 dB | 2 dB | 4.8 dB |
  | +24 dB | 9 dB | 0.3 dB | 14.9 dB |

  The default is a halo just to be heard after the notes; the top of the range is a wash as
  loud as the notes' own tails. Without the pedal nothing changes that can be heard (a chord
  differs from before 78 dB under its sound), and the distances from the recordings are the same.
  One measurement points the same way, further than this goes: the free strings above E6 that
  aren't at one of the note's partials ring 34-45 dB louder in the single-note recordings than
  the bank makes them (C3-C6, 71 strings standing 10 dB clear of their surroundings). That is
  the strike shaking them through the bridge, which this drive (the sound at the string's own
  frequency) doesn't model, so it isn't a value for K.
- **The pedal's lift.** When the pedal goes down, the strings whose dampers
  lift get a random-phase kick per partial. Its size per band is set so the
  free strings together ring as `pedalD` does over 0.3–1.0 s. The model
  matches the recording within about 1 dB per band over its 6 s, decay
  included.
- **Damped** strings fade at their damper's rate. The bank runs at **half the
  sample rate** (input averaged over pairs, output interpolated back, two
  samples late), in lanes of 4. A string is skipped when it has nothing
  driving it and nothing left ringing above −100 dBFS.
- **Timing.** Key and pedal changes reach the bank at their own sample,
  through a queue. `processEvents` sees the whole host block at once, while
  `processGlobal` runs per 32-sample control block.

### Voices, dampers and the rest

- **One voice per key** (`allocateVoice`).
  - Striking a key that's still sounding **hands the old vibration over**: the
    old components fade three times as fast as the new strike swells, so fast
    repeats can't cancel. **Restrike** adds the new strike to the old vibration
    instead (Restrike, below).
  - A voice holds up to 2048 components.
  - It ends when its total level is under −84 dBFS.
- **Dampers** take 70 dB/s at A0 rising to 300 dB/s at E6, 3 % faster per
  partial number. **Dampers** scales them.
  - Above E6 a grand has no dampers: those notes ring on after release, fading
    as when held. **Top Dampers → All keys** damps every key instead, as many
    digital pianos do.
- **Load guard.** When more than 4000 components sound across all voices, the
  floor below which components are dropped rises (by up to 40 dB).
- **Space.** **Width** is mid/side width. **Reverb** (`space.room`) is how much of the space is
  heard; the measurements are close-miked, so 0 is the dry piano, and then bit-identical to the
  piano before the reverb existed. The space is in **Space, below**.
- **Headroom.** Output is the recordings' level −10 dB (`kTrimDb`); one loud
  recorded note peaks near 0 dBFS.

## Controls on the measured piano

Controls that change the sound around what was measured. At their defaults the output is the
measured piano's.

### Velocity

`Piano::velocityMap` is the one place velocity becomes **tone** (which
recorded layer) and **level**:

- Velocity first goes through **Touch**: v' = 1 + 126·u^(2^touch).
- With the **Smooth** map, it's placed on each key's measured loudness range,
  from 6 dB below its softest recording up to its loudest, along
  top − range·(1 − u)^1.5. Half velocity is about a third of the way down.
- The tone is the recorded layer with that loudness, so tone follows loudness
  whatever the library's uneven layer spacing.
- **Tone Range** moves the tone layer towards the loudest (below 100 %) or
  further down (above). **Dynamics** scales the loudness range alone.
- **As Recorded** uses the sample library's own velocity ranges instead. The
  comparisons with the recordings use it, so a layer's middle velocity plays
  that layer.

The page's graph (`ui/custom.js`, messages `vel.query`, `vel.point`,
`vel.note`) is drawn from the same function. **It can be played**: pressing it strikes the key
it shows at the velocity under the pointer, soft at the left and hard at the right, until
released. The on-screen keyboard and the computer keys always play velocity 100 (the kit's
keyboard has no velocity), and these settings shape soft and medium notes: at 100 the ends of
Touch, Dynamics, Tone Range and the map are 0-15 % from the default by the perceptual distance,
at 30-64 they are 12-100 % (`tools/measure/settings.py`). Without the graph or a MIDI keyboard
the panel can hardly be heard, which is how the owner found it.

### Brightness, range and cut

- **Brightness** tilts the partials by dB per octave of partial number, and
  compensates the overall level so loudness stays put.
- Partials more than **70 dB** under the note's loudest at that velocity
  aren't started (`kRangeDb`). Components under **−105 dBFS** are dropped
  (`kFloorDb`).

### The hammer's spectrum and Hardness

Two controls change the hammer itself, around what was measured: **Hardness** (here) and **Strike Point**
(the next section). **Strike Depth**, after that, is Strike Point's unrealistic extreme. At their defaults
(0 % and 12 %) the output is exactly that of the measured piano (bit for bit).

**The hammer's spectrum** (`tools/analyze_hammer.py`). A felt hammer's force on the string is a
pulse lasting a millisecond or a few. Its spectrum is flat up to a corner near the inverse of the
contact time and falls above it. The felt stiffens as it is compressed, so a faster hammer stays
on the string for less time and the corner moves up: that is why a piano brightens as it is
played harder. Modelled as

    |F(f)| ∝ 1 / (1 + (f / fc)²)          (−12 dB per octave above fc)
    fc = fc13 · (v / v13)^α               (v: the hammer's speed, against layer 13's)

and fitted per measured key to the 16 layers' partial levels: each layer's change against layer
13, partial by partial, up to a level per layer. The hammer's speed per layer is read from the two
lowest partials, well below the corner. **One corner and one exponent per key explain 70-86 % of
how the partials change from soft to loud from A0 to C6** (rms 4.6-9.3 dB → 1.9-4.9 dB); above C6,
where the top keys have few, noisy partials, 20-94 %. α is near 2 up to C6 (1.4-2.8) and near 1
above; the corner at layer 13 is 3-12 kHz, at layer 1 a few hundred Hz. Both are smoothed across
the keys (running medians), and the corner per layer (`hammerFc`) kept rising with the layer and
within 300 Hz-24 kHz; C8 takes A7's.

**Hardness** (`tone.hardness`, −100…+100 %) moves the corner, ±3 octaves at ±100 %: a harder felt
is a shorter contact. Everything the hammer drives changes by the ratio of the moved spectrum to
the measured one, (1 + (f/fc)²) / (1 + (f/fc')²):

- each partial, at its frequency, at the strike;
- the recorded strike (the body's answer to the hammer's force), through that ratio as two
  first-order sections;
- the swell: contact time goes as 1 / fc, and the swell is partly the soundboard's, so τ is
  scaled by (fc / fc')^½ (2.8 times slower at −100 %, faster at +100 %).

Softer is strong: at velocity 80, −100 % takes 18-28 dB off the attack above 2 kHz from C2 to C5,
and the treble's fundamentals, above the moved corner, lose 14-18 dB (a felt-piano mute). Harder
saturates: +100 % adds 4-6 dB above 2 kHz, since the measured hammer is already flat up to its
corner at medium velocity, and a spectrum can't brighten past flat. A harder surface than felt (a
hammer that rings, or partials above the recordings' floor) would need more than this model.
The strike below A2 (band noise) isn't changed by it.

### Strike Point

**Strike Point** (`tone.strikepoint`, 2-50 %; 12 % is every key as recorded) moves where the
hammer meets the string. A hammer at the fraction x of the string excites partial n as sin(nπx):
the partials with a node under the hammer are weak, and those either side of a node start in
opposite senses.

*Where the recordings' hammers are* (`tools/analyze_point.py` → `salamander_point.json` →
`strikeX`, `strikeEps`, `strikeLift` in the header). The comb is fitted per measured key to the
partials' levels at the strike, over the louder layers together, with a smooth trend in log n:

    comb(n) = |sin(nπx)|                              up to the first antinode (n·x ≤ ½)
            = √( (sin²(nπx) + ε²) / (1 + ε²) )         beyond it

- **The low bass, A0-F#2, shows it**: x = 11.65, 11.68, 11.85, 12.30, 12.18, 12.03, 12.20,
  12.03 % of the string, the same within 0.1 points whichever layer is fitted, explaining 16-59 %
  of the partial-to-partial variation over the first 40 partials. Each key needs its own x: 0.3
  points turn the comb a quarter of its period by the 25th partial (12 % for every key
  explains 7-46 % with the floor fitted, and 1 % on A0 and 2 % on F#1 with the 30 dB notches
  the control used to assume).
- **The notches have a floor** (ε): the node partials sit 7-23 dB under their neighbours, not
  bottomless. ε is pooled over those keys (0.10: a partial or two per key see it). The notches
  also differ from node to node, so each partial keeps its **lift**: how far it sits below the
  median of its neighbours (n ± 1…3) on the recordings.
- **From A2 up the comb is weak in these recordings**: with x near the bass's the fits want
  ε ≥ 0.55 (notches of 2-5 dB; only the first dip, near partial 8, shows, 5-8 dB), and with x
  free they find nothing steady. Those keys take the bass's median x (12.03 %) and ε = 0.55: a
  judgment, the deepest notch the middle supports. Where the real strike point is up there (it
  moves towards the end on most grands) these recordings don't say.

*What the control does.* It scales every key's x by setting / 12 %, and each partial changes by
the ratio of the moved comb to the key's own, so at 12 % nothing changes (bit for bit):

- **Level.** A partial coming off a node rises by no more than its lift or 6 dB, whichever is
  more (the comb's broad shape), and only past the first antinode (n·x > ½; before it the comb
  is sin alone), so none is raised clear of its neighbours on the strength of a notch that wasn't
  there; a partial landing on a node falls to the floor (−20 dB in the low bass, −6 above).
  Between measured keys each side's ratio is blended like its levels.
- **Phase.** A partial whose node the hammer has crossed turns by π (all its modes, both mics),
  so the strike's waveform changes as well as its spectrum.
- Nearer the end is thinner and brighter: at 6 % partials 1-4 lose 5.9, 5.4, 4.5 and 3.3 dB.
  Further in is fuller and hollower: at 20 % they change by +4.1, +2.8, +0.5 and −3.1 dB, and
  every fifth partial sits on a node.

`tools/measure/strikepoint.py` sweeps it: between 6 and 20 % the partials follow the model within 0.1-2.5 dB rms,
and from A2 up no partial ends more than 4-10 dB clear of its neighbours (3-14 dB in the bass,
where what is left is mostly a partial between two new notches).

*Loudness.* A hammer further in drives the low partials much harder (the fundamental +8.7 dB at
50 %) and one near the end hardly at all (−11.8 dB at 3 %). The note's energy over its first half
second (each mode's power at the strike times how long it lasts) is kept at the measured
piano's, so the control changes colour, not level: within ±1 dB over the first second on most
keys (C2 at 2 %: +4.3 dB). Power at the strike alone doesn't work for this: fast modes that are
gone in milliseconds count as loudness, and notes came out up to 12 dB loud.

*Range.* 2-50 % (4-25 % before), on a log scale. At 50 % the hammer is at the middle of the
string and the even partials have their nodes there: hollow, towards a clarinet.

What it doesn't do: the hammer's force itself changes with where it strikes (reflections from the
near end come back sooner and throw it off earlier), and the recorded strike (the knock) isn't
moved. A hammer-string simulation would give both.

### Strike Depth

**Strike Depth** (`tone.strikedepth`, 0-100 %, default 0) leaves that realism on purpose. The
owner liked how far the first Strike Point went: more extreme, a different instrument. It does
two things at once, and nothing while Strike Point is at 12 %:

- **Towards an ideal string**: the comb's floor falls from the key's ε to 0.03
  (ε × (0.03/ε)^depth: notches 30 dB deep at 100 %) and the most a partial may rise goes from 6 to
  12 dB (or its lift, if more).
- **Past any string**: every partial's change in dB is multiplied by 1 + 2 × depth (3× at
  100 %; falls end at −60 dB, rises at +24), so the notches widen as well as deepen and the
  partials between them stand out. Loudness is kept as above (within −6…+7 dB at 100 %).

The first version did only the first of these, and the owner could hardly hear it. Measured, it
was 1-7 % from the realistic hammer by the perceptual distance, and so was PR #11's control: a
deeper notch still takes one partial per node. What made that control sound extreme was mostly
the tilt of the low partials, which the realistic one has too. With the multiplying, from C2 to
C5 and Strike Point 2-50 %, Depth is 4-21 % from the realistic hammer at 25 %, 10-50 % at 50 %
and 14-81 % at 100 % (C6: 7-49 % at 100 %).

At Strike Point 50 % and Depth 100 % partials 2 and 4 are 16-50 dB down and the fundamental 19-24
dB up before the loudness correction: only the odd partials are left. The *Hollow Strike* preset
is that; *Glass Strike* (3 %, Depth 70 %) is the other end. `tests/strike-depth.json` plays the
first.

What the first version of this control did, and why it was replaced: Design history.

### The soft pedal

**Soft Pedal** (`play.softpedal`, 0-100 %, MIDI controller 67) is a grand's una corda: the action
shifts sideways, so the hammers meet the strings on softer, less-packed felt, and from about half
way each hammer misses one string of its three. The Salamander set has no recordings with it
down, so this is the physics applied to what was measured, with sizes that are a judgment
(`kUna…` in `PianoVoice::strike`). `tools/measure/softpedal.py` reports what it does.

- **The felt** (the whole pedal travel, every key): Hardness comes down by up to 60 %, the
  hammer's corner 1.8 octaves lower, and with it the partials, the recorded strike and the swell
  (Hardness, above). Half as much by C7: Hardness takes far more of a top note's level. And
  no lower than costs the fundamental 3 dB: a soft strike's corner is already near its
  fundamental, and a soft note with the pedal came out 16 dB quieter (now 4-6 dB at velocity 30).
- **The unison** (from half pedal to full, on the keys with three strings: fading in from F#2
  to C3). Struck together, a unison's strings move in phase, press on the bridge together and
  lose their energy fast: the prompt sound. With one string left out, a third of the motion is
  out of phase, which the bridge hardly feels and which lasts: the aftersound (Weinreich 1977).
  So each partial starts 3.5 dB lower (two strings of three) and comes back, as fast as its
  prompt sound fades (its loudest mode's decay, 6-60 dB/s), to 2 dB over its usual level.
- That gain over time is applied to the whole partial, g(t) = late − (late − early)·e^{−t/τ}, as
  a second, faster-fading copy of each component. Scaling a partial's modes one by one (the fast
  ones down, the slow ones up) undoes the way they were fitted to add up: C4 came out 2 dB
  *louder* at the strike that way. The copies double a soft note's components.
- **Read at the strike**, as on a piano: notes already ringing keep their tone when the pedal
  moves.

Measured (velocity 90, against the same note without the pedal): at full pedal a note is
1-6 dB quieter over its first 0.1 s (about 3-6 dB from C3 up), within −1.6…+1.2 dB at 0.5-1 s and
−2…+1.6 dB at 2-3 s, its first 0.3 s 1-23 % lower in centroid, and 11-22 % away by the
perceptual distance. At half pedal (the felt alone) it is 3-8 % away: a shade. The first
version's felt (25 %) was 0.3-3 % at half pedal, not to be heard.

Not modelled: the two-string keys (the hammer still meets both), the sympathetic ring of the
string left out as a tone of its own, and the pedal's own noise.

### The strings: Stiffness and Overtone Decay

Two controls change the strings around what was measured; at their defaults (100 % and 0 %) the
output is exactly the measured piano's (bit for bit). Both reach the voices and the sympathetic
string bank alike.

**Stiffness** (`string.stiffness`, 0-800 %) scales the strings' inharmonicity. Partial n of a
string is at n·√((1 + B n²) / (1 + B)) times its fundamental, then `dev` cents off that line where
it was measured elsewhere (`partialRatio`); Stiffness scales B and those offsets alike. At 0 % the
strings are ideal, the partials at whole multiples of the fundamental (harp-like; organ-like in the
bass); at 300 % they spread three times as far (800 %, the top of the range, eight times) (C4's 12th partial: +33 cents as measured, +253 at
300 %), towards bells. The middle of the keyboard has little stiffness to scale (0 % and 300 %
are 1-2 % from the measured piano at C3-C4 by the perceptual distance, 6-11 % at C2 and C6), so
the range runs to 800 % (6-19 %; the distance sees loudness, not pitch, and underrates this).
It applies at the strike, so to the notes struck next; the stretch tuning
(Stretch) is kept as it is.

**Overtone Decay** (`string.overtonedecay`, −100…+100 %) changes how long the upper partials ring
against the fundamental: each component's measured decay times (its frequency / the
fundamental)^γ, γ = the setting / 100, the factor kept within ¼…4 and no component slower than
0.3 dB/s (`overtoneDecayDb`, applied where the poles are set, so it acts on sounding notes too).
Below 0 the upper partials ring on (bright, steely: on C3 at −100 % the 6th partial fades at
2 dB/s against the fundamental's 5.7); above, they die sooner (dark, like gut or nylon: C5's 6th
at 27 dB/s, measured 16). Even at Sustain 400 % and −100 %, the undamped top keys are silent
within 20 s.

Overtone Decay scales the measured decays rather than moving a fitted loss law (why: Design history).

### A key struck while it rings: Restrike

Not measured: no recording here holds a key struck while it still sounds. With **Restrike**
(`string.restrike`) at 0 %, the default, the handover above is all there is and the output is
bit-identical to the piano without it.

**The physics.** A hammer meeting a string that already moves adds its own motion to it: to
first order the result is the old vibration plus the new strike. Partial by partial the two
meet in step or out of it, as the moment of the strike has it, so no two repeats come out
alike, a repeat under the pedal builds on the last, and now and then a strike lands against
the string and comes out weaker. And the felt rests on the string for a few milliseconds: a
brief touch at the strike point, which takes most from the partials that move most there and
nothing from those with a node under it (Touch Point's rule).

**In the engine** (`PianoVoice::strike`, `foldOld`). At Restrike r each old partial n keeps

    k(n) = r × 10^(−6 dB × sin²(nπx) / 20)

of itself (x: the key's strike point, as Strike Point has moved it), and the rest fades as the
hammer lands, as the whole of it does at 0 %. The 6 dB is a judgment (`kRestrikeFeltDb`).

- **Where the partial is modes** (most of the keyboard), a mode's frequency and decay are the
  same on every strike, whatever the velocity. So the kept part goes *into* the new strike's
  component for that mode, and one component carries both strikes from then on: two components
  on one pole are one, with the gains to the two mics worked out again for the sum. A key
  struck a hundred times holds no more components than one struck twice.
- **Elsewhere** (partials without modes, modes a softer strike leaves out, the old strike's
  onset twins) the kept part goes on as a component of its own. These build up over repeats
  until they fade under the floor: C4, which has no modes, levels off at 683 components in a
  ten-a-second trill under the pedal (259 at 0 %), far under the 2048 a voice holds; with no room
  left, the old part fades as at 0 %.

The strike's knock and the body's ring are added as on any strike. What the hammer itself does
differently on a moving string (a harder or softer blow as the string comes towards it or
goes away) isn't modelled: that needs a simulated hammer.

Measured (`tools/measure/restrike.py`, C2-C6, under the pedal, Restrike 100 % against 0 %):

| | Restrike 0 % | Restrike 100 % |
|---|---|---|
| loud, then soft (velocity 115, then 35 half a second later): the second after | the soft note alone | −2.4…+5.2 dB, **29-151 %** from 0 % by the perceptual distance |
| fast uneven repeats (velocity 90, 70-130 ms apart): strike to strike, levels matched | 3-11 % | **9-48 %** |
| … the first six partials' spread over the strikes | 0.0-0.5 dB | 1.3-3.5 dB |
| … the weakest strike's first 30 ms against the first strike's | −0.1…+0.2 dB | −4.6…+0.1 dB |
| … the last strike against the first | +0.0…+0.4 dB | +0.9…+5.3 dB |
| even repeats, four a second: the passage's distance from 0 % | | 1-4 % |
| the key let go between strikes, no pedal | | 0 % (0-2 % with Dampers at 400 %) |

So it is heard where old vibration is still strong when the hammer lands: a soft strike on a
loud note, fast repeats, the pedal down, the undamped top keys. **At an even four strikes a
second it changes almost nothing**: by then the old vibration has faded far, and evenly spaced
strikes meet it the same way each time. Without the pedal the dampers have stopped it.

The sum itself is checked on a build with the felt at 0 dB (`-DRESTRIKE_FELT_DB=0.f`): two
strikes at Restrike 100 % against the two rendered alone and added. On keys whose partials are
all modes the difference is 46-74 dB under the sum (C3, C5; strikes 50, 137 and 500 ms apart at
different velocities). On C2, C4 and C6 it is 24-47 dB, which is not the restrike's error: there
each strike draws its own beat rates (partials without modes), and below A2 the strike is noise.

Cost: nothing at 0 %. At 100 %, 36 pedalled keys struck three times rendered in the same time
(+1 %); a ten-a-second trill on one key took up to 18 % longer.

Preset *Struck Again* (with Dampers at 400 %, so it can be heard without a pedal: press the
velocity graph at its right, then at its left). Test: `restrike-keep`.

## Space

A hybrid reverb (`src/Reverb.h`), generated, with no recorded impulses: each space's **early
reflections** are an impulse response played by the kit's convolver, and its **tail** is a
feedback delay network, so the tail's length, size and colour are continuous controls (a
convolver's impulse is fixed once audio runs).

| Space | Shoebox (m) | RT60 | Treble RT60 | Early part | Tail fed after |
|---|---|---|---|---|---|
| Room | 6 × 5 × 3 | 0.6 s | × 0.6 | 60 ms, as loud as the tail | 8 ms |
| Chamber | 10 × 8 × 5 | 1.3 s | × 0.55 | 90 ms, −2 dB | 15 ms |
| Hall (default) | 32 × 20 × 14 | 2.2 s | × 0.5 | 140 ms, −5 dB | 30 ms |
| Cathedral | 60 × 24 × 28 | 5.0 s | × 0.4 | 220 ms, −8 dB | 50 ms |
| Plate | (none) | 2.4 s | × 0.85 | a 12 ms bright burst, −10 dB | 0 |

- **Early reflections.** Image sources of a shoebox up to 14 bounces, from the piano to a pair of
  listeners 0.6 m apart, relative to the direct sound (which is the dry piano). Each bounce
  reflects 0.75-0.9 of the amplitude and goes through a first-order low-pass at 9 kHz; each image
  is jittered by 3 cm per bounce so the pattern isn't too regular; the last 30 % of the window
  fades into the tail.
- **Tail.** 16 delay lines through an orthogonal Hadamard matrix (lossless), their lengths
  log-spaced over 0.6-2.2 times the space's mean free path in travel time (4V/S for Room, Chamber and Hall; Cathedral's is set to 24 m, Plate's to 3.4 m). Each line
  loses, per pass, what its length takes at the RT60s below and above a first-order split at
  2 kHz, so the decay is exact for any length. Four lines are modulated ±0.3 ms at 0.3-0.9 Hz
  against ringing. Left and right take orthogonal sign patterns of the lines (decorrelated).
- **Controls.** **Decay** scales both RT60s (25-300 %). **Size** scales the delays (50-200 %,
  gliding over 150 ms; the RT60 stays). **Damping** moves the treble RT60 by 2^((50 − d)/30) from
  the space's own (at most 1.2 × the RT60). **Pre-delay** (0-200 ms) delays both parts.
  **Early** sets the reflections' level. **Low Cut** and **High Cut** are Butterworth filters on
  the wet signal only.
- **Switching.** Every space has its own convolver and network. Changing Space fades the input
  over to the new one in 10 ms; the old one rings out and then stops (its RT60 × 1.3). Reverb 0
  stops them all the same way, so the reverb costs nothing when off.
- **Level.** Each space is normalised to one energy for an impulse (weighted by a one-pole
  low-pass at 800 Hz, about a piano's spectrum), shared by the early part and the tail by the
  table's ratio. Then each was trimmed on renders of a staccato chord across the keyboard to
  within 0.1 dB of the old room's level at the same setting (`trimDb`; `kWet` sets Hall's), so
  Reverb means about what it meant before. `tools/space.py` reports each space's level against
  Hall's: re-trim from it after changing a space.
- Measured on that chord by `tools/space.py` (500-1000 Hz, T20): Room 0.64 s, Chamber 1.11 s, Hall 1.94 s, Cathedral
  4.44 s, Plate 1.89 s (somewhat under the design: the dry notes are still sounding into it).
  Hall at Decay 50 % / 200 %: 0.97 / 3.99 s; Damping 0 / 100 % moves its 4-8 kHz RT60 from 2.31
  to 0.72 s; Size 50-200 % leaves the RT60 within 3 %.
- **Cost.** About 2.6 % of a core for one space (native), against 2.8 % for the old room's 1.6 s
  convolver; two while one rings out after a change.

## Creative additions

Not part of the measured piano. Each is off by default, and off leaves the output bit-identical to the
piano without it. Touch Point, Listening Point and the partial arpeggiator are per-note effects on the
partials; the kit's arpeggiator and sequencer and the modulation matrix come from synthkit.

### Touch Point

Not part of the measured piano: with **Press** at 0 (the default) the output is bit-identical to
the piano without it. A finger resting on the strings, as a player reaches into a grand to sound
harmonics or mute a note (`touch.*`, `PianoVoice::touchBlock`).

**The physics.** A finger at the fraction y of the string, w of its length wide, takes energy
from partial n where that partial moves under it: sin²(nπs) averaged over the finger,

    share(n) = ½ (1 − cos(2πny) · sinc(nw))          sinc(x) = sin(πx) / (πx)

(`touchShare`). A partial with a node under a narrow finger is hardly touched and rings on; one
with an antinode there takes all of the finger's damping. At y = ½ the even partials have their
node there, and the note becomes its octave harmonic; at ⅓ every third partial is left (an
octave and a fifth up), at ¼ every fourth. A wider finger spans the upper partials' nodes and
antinodes alike, so they all take half: with a fingertip (1 %) a harmonic's own upper partials
fade a little sooner (the 6th at 1.8 dB/s more at Press 600), and at 15-30 % it is a strip of
felt or the flat of a hand, a mute.

**In the engine.** Each partial's components fade faster by Press × share(n) dB/s: one more
term where the poles are set (`updatePoles`), looked up by the partial number each component
carries (`pnum`). All of a partial's components (its modes, their onset twins, the soft pedal's
copies) take the same extra decay, so the partial keeps its shape and simply fades faster; its
modes are never scaled one against another. Sustain doesn't scale it: it is the finger's, not
the string's. The table is worked out again only when a setting moves.

| Control | ID | |
|---|---|---|
| Press | `touch.press` | how fast the finger stops what moves under it, 0-2000 dB/s. 100-300: the note melts into its harmonic over half a second; 500 and up: a ping, then the harmonic |
| Point | `touch.point` | where it rests, 2-50 % of the string |
| Width | `touch.width` | how much of the string it covers, 0-30 % |
| Snap | `touch.snap` | On (the default): Point goes to the nearest 1/k of the string (k = round(1 / Point), 2-50), so it steps through the harmonics. Off: exactly where Point says |
| Dry | `touch.dry` | 0-100 %, default 0: how much of the finger's damping reaches the key's ring in the body (below) |

Press, Point and Width are modulation destinations, read per voice each control block like the
strings' decay settings, so a note can be touched after it is struck (a Shape, the wheel or
aftertouch on Press: *Late Touch*) and each note can have its own harmonic (Random or Key on
Point: *Chance Harmonics*). **What the finger takes doesn't come back**: it stops the string's
partials themselves, so a finger sliding along a ringing string leaves less and less, as on a
real one. (The Partial Arp is the opposite: it scales what is heard and leaves the vibration.)

Measured (`tools/measure/touch.py`: C2-C6 at velocity 100, against the same note untouched, 0.4-0.9 s in):

| | partials with a node under the finger | the others | distance from untouched |
|---|---|---|---|
| Press 600, Point 50 % (1/2), Width 1 % | −0.1…−1.1 dB | −21 dB or more | 25-50 % |
| Press 600, Point 33.3 % (1/3), Width 1 % | −0.2…−1.1 dB | −31 dB or more | 38-54 % |
| the mute: Press 1500, Point 8 %, Width 20 %, Snap off | | 40 dB down in 0.3-1.3 s (untouched: 1.4-3.2 s) | 36-79 % |

Cost: nothing when off. On, a note is cheaper than untouched, since the stopped partials are
dropped (36 pedalled notes over 8 s rendered in 1.4 s with the finger at ½ and 1.2 s with an LFO
moving it, against 2.0 s untouched).

What it doesn't do:
- **The body's ring and the strike aren't touched.** The knock is the hammer's and the body's,
  and the ring is measured from notes left to sound. After a mute it is what remains: 35-43 dB
  under the strike a second in, and the slow end of the mute's 0.3-1.3 s above (with Body at
  −40 dB: 0.2-0.5 s). A muted string would feed the body less than a sounding one, by an amount
  these recordings can't give. **Dry** (`touch.dry`, default 0 %) is the choice of sound: the
  key's ring fades faster by Press × ½ × Dry (a wide finger's share), in `Piano::touchBody`, and
  stays stopped until the key is struck again. It isn't physics (the finger doesn't reach the
  body or the other strings). At 100 % the mute above is 40 dB down in 0.2-0.5 s (0.3-1.3 s
  without); on a harmonic it changes 1-4 % by the distance. *Felt Mute* uses it.
- **The sympathetic strings aren't touched** (the finger is on the keys played). Once a touched
  note's voice has ended, its string in the bank is free again while the key or the pedal is
  down, and can ring along with other notes.
- **The strike is the same** with the finger down. On a real string the finger also changes what
  the hammer can set going in the first place (a little, for a light touch).
- A finger doesn't move a partial's pitch here, as a heavy one does on a real string.

Presets: *Octave Harmonics*, *Felt Mute*, *Late Touch*, *Chance Harmonics*. Tests:
`touch-point`, `mod-touch`.

### Listening Point

Not part of the measured piano: with **Depth** at 0 (the default) the output is bit-identical to
the piano without it. Where along the string the sound is taken, as a pickup on an electric
instrument takes it (`listen.*`, `PianoVoice::listenBlock`).

**The physics.** At the fraction y of its length the string's partial n moves by sin(nπy):
nothing at the partial's nodes, most at its antinodes, and the other way up past each node. A
pickup w of the string wide averages that to

    heard(n) = sin(nπy) · sinc(nw)

Near the end of the string the low partials hardly move, and the sound is thin and bright; at
the middle every second partial is silent. It is Strike Point's comb again, on the way out
instead of on the way in. The piano's own sound comes from the bridge, where the measured levels
already are what they are: every partial at 1.

**In the engine.** Each partial's gain to the mics is c · ((1 − Depth) + Depth · heard(n)), signed,
so a partial passes smoothly through silence and turns over as the point crosses its node. It
is applied where the Partial Arp's gains are (`pnGain`, ramped over each control block), and
multiplies with them. c keeps the note's loudness: the note's energy over its first half second
(each component's power × how long it lasts, summed per partial as the strike adds them,
`listenPow`) stays what it was, by no more than 12 dB.

Unlike Touch Point it scales what is heard and never the vibration, so **nothing is lost when
the point moves**: Depth, Point and Width are modulation destinations, read per voice each
control block.

| Control | ID | |
|---|---|---|
| Depth | `listen.depth` | 0-100 %: the piano's own sound to the listening point alone |
| Point | `listen.point` | 2-50 % of the string |
| Width | `listen.width` | 0-20 %: wider quietens the high partials |

Measured (`tools/measure/listen.py`: C2-C6 at velocity 100, Depth 100 %, against Depth 0):

| | |
|---|---|
| partials 2-6 against the 1st, away from nodes | within 1.3 dB of sin(nπy) (mean 0.2 dB) |
| level over the first half second | −4.0…+1.1 dB (the low end where the 12 dB limit is reached: Point 4 % in the treble) |
| distance, Point 50 % / 25 % / 12 % / 4 % | 12-30 / 5-10 / 7-20 / 14-34 % |
| moving (*Wandering Ear*: the point between about 3 and 31 % at 0.25 Hz, C2-C5) | 27-136 % from the plain note window by window (0.25 s, levels matched); partials 1-6 swing 14-16 dB |

**A still point is subtle in the middle of its range** (5-20 % at 12-25 %: a notch takes one
partial per node, Strike Depth's lesson); the ends are plainer, and the sound is in the
movement. Cost: the Partial Arp's (the same path): 16-30 % on 36 pedalled notes, where the arp
measured 18-32 % on the same machine; a moving point costs two sines and two cosines a block more.

What it doesn't do:
- **The strike, the body's ring and the sympathetic strings are heard as before**: they aren't
  this string's vibration. At Point 2-6 %, where the partials are thinned most, the strike
  stands out more.
- **Loudness is kept from the strike's balance of partials**, not followed through the note:
  late in a note, when only the low partials are left, a point near the end is quieter than the
  plain note (as a pickup there would be).
- A magnetic pickup's own distortion (an electric piano's bark) isn't modelled.

Presets: *Wandering Ear*, *Opening Ear*, *Bridge Pickup*. Tests: `listening-point`, `mod-listen`.

### The partial arpeggiator

Not part of the measured piano: with **Arp Pattern** Off (the default) the output is
bit-identical to the piano without it. Turned on, each note's partials take their turn, a
melody inside the note (`PianoVoice::arpBlock`): by default dug out (a moving notch), or brought
forward with a positive Boost.

- **Which partials.** Those whose harmonic number lands, as a pitch class against the note,
  within ±35 cents of a degree of the chosen scale (`inScale`): Major Pentatonic is 1, 2, 3, 4,
  5, 6, 8, 9, 10, 12, 16, 18, 20, 24, 27…; Minor Pentatonic takes the 7th harmonic (31 cents
  flat) as its seventh; also Major Triad, every harmonic, odd harmonics. Only between Lowest
  and Highest, and only partials the voice is playing (a high key's top partials are absent or
  above Nyquist). The pitches are the strings' own, so stiffness sharpens the upper ones.
- **Grouping.** Single features one partial per step (as first built: a boosted partial
  stands out as a sine, resonant and electronic). Octaves (the default) makes each step a
  pitch class, named by its odd harmonic and walked in scale order (C D E G over C, from
  harmonics 3-16), and moves all its partials in range together: C is 4, 8 and 16, G is 3, 6
  and 12. **Width** spreads each featured partial to its neighbours within ±Width octaves,
  weighted cos² of the distance; a neighbour outside the pattern goes from its own level
  (not Rest) towards Boost. Single with Width 0 renders exactly as the first version did.
- **Timing.** Steps run from the strike, at Rate (Hz) or synced to the tempo at Division. Step
  k's partial rises and falls as sin²(π x / W) over W = 1 + Overlap steps: at 100 % one
  partial is rising while the last falls, and the two sum to a steady level.
- **Level.** A featured partial goes from Rest to Boost (dB against its natural level); the
  pattern's other partials sit at Rest; partials outside it are untouched. Boost defaults to
  −20 dB, a moving notch, which the owner found more natural than boosting (the *Partial
  Notch* preset is the first version's settings with it: Single, Up & Down, Rest −6 dB, Sustain
  200 %). *Harmonic Arp* boosts gently instead: Octaves, Width ¼ octave, +8 dB, Rest 0.
- **How.** Each component keeps its partial number (`pnum`). Per control block, the gain each
  partial should have at the block's end is computed, and each component's output to the mics
  ramps to it from the last block's gain. Only the output is scaled, not the oscillator's state,
  so a partial's natural decay carries on underneath and it comes back exactly to its own
  level. Each voice has its own random stream for Random, so the beats and noise don't change.
  It costs about 7 % more CPU when on, nothing when off.

### The kit's arpeggiator and sequencer

synthkit's arpeggiator and step sequencer (`features.arp`, API.md §4b), off by default
(`arp.on`), so the piano is unchanged until it's turned on. They run after the synth's
`processEvents`, so the keys, pedal, body ring and sympathetic bank there follow the keys
actually played; the piano handles the arpeggiator's own notes in three places:

- **Voices.** Played keys keep one voice per key (a restrike in place). The arpeggiator's notes
  take a voice of their own, so a Crossfade or a gate over 100 % can sound two notes of one key
  at once. `NoteOn` doesn't say which notes are the kit's in `allocateVoice`, so
  `processEvents` counts the keys played in the host block (`playedPending`; none in Arp mode,
  where the kit keeps the keys) and any other note is the arpeggiator's.
- **Dampers.** An arp note lifts its key's damper as it starts (`arpStarted`: the body ring is
  undamped) and holds it up each control block until released (`arpUp`). A key that no arp note
  holds up any more gets its damper back in `processGlobal` (`arpDampers`) unless it's held or
  the pedal is down. This doesn't rely on `release()`: the kit also ends notes by stealing them
  and at the end of a phrase. The arp notes themselves ignore the pedal (the kit's rule); the
  body ring follows it. Keys above E6 have no dampers (Top Dampers), so a sequencer's lanes up
  there ring on.
- **Note envelope.** The piano's default for `arp.envMode` is Off (`kitDefault`): a note ends
  with its damper when the gate ends. Multiply and Replace cut the piano's ring at the
  envelope's end; Crossfade fades between notes over the strike.

`arp.*` were the Partial Arp's IDs until patchVersion 2; `migratePatch` renames them to
`parp.*`, and `tests/migrate-v1.json` checks that an old patch renders as it did.

### Modulation

synthkit's mod matrix (`features.modSlots`: 16 routes), four standard LFOs
(`features.standardLfos`) and four Shapes (`features.shapes`: drawn, tempo-locked curves, API.md
§6a), API.md §6. With no routes every value is the parameter's own, so the
piano is byte for byte as before (`cmp`). Which parameters are destinations follows where the
engine reads them:

| Read | Destinations | What a route does |
|---|---|---|
| at the strike (`PianoVoice::strike`) | `tone.bright`, `tone.attack`, `tone.hammer`, `tone.hardness`, `tone.strikepoint`, `tone.strikedepth`, `tone.body`, `string.restrike` | sets each note's tone as it's struck (per-voice sources: velocity, key, random, a Per Voice LFO); a note that's ringing keeps its tone |
| each control block, per voice | `string.sustain`, `string.unison`, `string.damper`, `string.overtonedecay`; `touch.press`, `touch.point`, `touch.width`; `listen.depth`, `listen.point`, `listen.width`; `parp.rate`, `parp.overlap`, `parp.boost`, `parp.others`, `parp.width` | moves ringing notes: `updatePoles` runs on any change, so a moving route recomputes every component's pole each block (about 10 % slower with 36 notes ringing under the pedal) |
| each control block, globally (`processGlobal`) | `string.resonance`, `space.room`, `space.width`, `space.decay`, `space.size`, `space.damping`, `space.highcut` | global modulation only: per-voice sources count as 0. The sympathetic bank reads Sustain, Dampers and Overtone Decay globally too (not Unison: the bank has no beating), so a per-voice route to those moves the struck strings but not the free ones |
| kit, with the arpeggiator | `arp.phraseAttack`, `arp.phraseDecay`, `arp.phraseSustain`, `arp.phraseRelease` (synthkit #26) and the kit's own arp destinations (`arp.gate`, `arp.swing`, `arp.evolve`, `arp.pattern`, `arp.xfade`, `arp.keyLevel`, `arp.seqLevel`) | the phrase envelope (each sequencer lane's too) follows global modulation once per host block; a moved Sustain is reached at the Decay rate. Per-voice sources count as 0 |
| kit | `sk.pitch`, `sk.level` | vibrato and tremolo, through `ctx.pitchHz()` and after the voice. The string bank's concert pitch is worked out from both pitch and Hz, so vibrato doesn't retune the free strings. The body's ring and the bank live in the synth, so `sk.level` doesn't reach them |

Not destinations: Stiffness and Stretch (the voices and the sympathetic bank must agree on the
strings' tuning), the velocity controls (`play.*`: `processEvents` maps velocity for the body
ring with global modulation, the voice with its own, and they must agree), Pre-delay (whole-sample
jumps click), the choices, Low Cut and Key & Pedal.

LFOs and Shapes are destinations too (`lfoN.rate`, `.phase`, `.depth`; `shapeN.rate`, `.offset`,
`.depth`; synthkit #27): a Shape can speed up an LFO, velocity can set a Per Voice LFO's depth.
An LFO driven by a Shape (or by a later LFO) follows it a block (32 samples) behind, per note
for per-note ones (synthkit #28). A Global LFO is one wave shared by all notes, so per-note
sources (velocity, key, random, a Note-triggered Shape) can set its Depth, each note its own,
but not its Rate or Phase; a global Shape's Rate, Offset and Depth follow shared sources only.

Sources: a Shape is per voice when triggered by Note (so it can give each strike its own curve
on a strike-time destination) and global otherwise (First Note, Bar, Beat, Free, Manual), which
is what the global destinations above follow. Shapes, LFOs and the matrix run in the kit; the
piano reads only the modulated values (`ctx.get`).

Seeding: the kit's Drift and S&H LFO shapes draw from a random stream that depends on how many
LFOs and shapes the synth has. Going from three LFOs to four moved *Tape Wow*'s drift to a
different (equally random) path; nothing else changed. Changing `standardLfos` or `shapes` again
would do the same.

The page puts all of this, the Partial Arp and the kit's arpeggiator in a **Synthesis** tab:
one wide accordion (`ui/layout.json`), every panel folded but the matrix. The pattern panel's
fold toggle needed a kit fix (synthkit #26: it rewrote its own title).

## What isn't modelled (yet)

- **The dampers' imperfect stop.** On a real piano a residual ring continues
  after the damper lands (the `harm*` samples).
- **Half-pedalling.** The kit treats CC64 as on/off.
- **Sostenuto**, **release velocity.** (The soft pedal is modelled, not measured: above.)
- **Strings coupling each other** beyond the bank's one-way drive. The bank's
  output doesn't feed back into the voices.
- **The body ring follows one decay per band.** It's a few dB strong at
  0.4–1 s and weak at 2–3 s on many keys.
- **Partials 13–24** come out about 5 dB weak at 0.4 s.
- **A6's** attack brightens less from soft to loud than the recordings at 2-4 kHz.
- **The strike's finer changes with velocity** (one response per key, shaped by a shelf and two
  gains), and **between the measured keys** (the nearer key's response).
- **The top octave and a half** (C6 up) is the weakest measured region (22 %; C8 42 %). C5 and
  nearby, long the weakest, is at 5.5 % since the recorded strike.
- **A soft C4's 62 Hz thump** is ~20 dB too strong.
- **The room.** The recordings are close-miked; the Space reverb is generated (image sources
  and a feedback network), not measured from a hall.
- **The treble's partials** mostly keep the older description: above C6 the modal fit seldom
  beats it on these noisy envelopes.

## Parameters

Every parameter, by the ID a patch stores, with the section that explains it.

| Parameter | ID | What it changes |
|---|---|---|
| Velocity Map | `play.velmap` | Smooth / As Recorded (Velocity, above) |
| Touch, Dynamics, Tone Range, Soft Attack | `play.touch`, `play.dynamics`, `play.tone`, `play.softattack` | velocity → level, tone, swell |
| Soft Pedal | `play.softpedal` | una corda (CC 67): softer felt, then one string of three left out (The soft pedal, above) |
| Brightness | `tone.bright` | partial tilt, dB per octave of partial number |
| Attack | `tone.attack` | swell time τ |
| Hammer | `tone.hammer` | the strike's level (recorded from A2 up, band noise below) |
| Hardness | `tone.hardness` | the hammer's felt: its spectral corner, ±3 octaves (Hardness, above) |
| Strike Point | `tone.strikepoint` | where the hammer meets the string: each key's own strike point × setting / 12 % (12 %: as recorded) |
| Strike Depth | `tone.strikedepth` | how far Strike Point goes: 0 % what the recordings support; higher, an ideal string's comb and every change multiplied (3× at 100 %) |
| Body | `tone.body` | body ring level |
| Key & Pedal | `tone.mech` | mechanical noises, and the pedal's lift ring |
| Unison | `string.unison` | beat offsets and cluster spreads |
| Stretch | `string.stretch` | stretch tuning, 0 = equal temperament |
| Stiffness | `string.stiffness` | the strings' inharmonicity, % of measured (0: harmonic) |
| Overtone Decay | `string.overtonedecay` | upper partials' decay against the fundamental's (The strings, above) |
| Sustain | `string.sustain` | all string decays (voices and bank) |
| Dampers | `string.damper` | damper speed |
| Restrike | `string.restrike` | a key struck while it rings: 0 % hands the old vibration over (the default), 100 % adds the new strike to it, less what the felt takes |
| Top Dampers | `string.topdampers` | None above E6 / All keys |
| Resonance | `string.resonance` | sympathetic drive |
| Width, Reverb, Space… | `space.*` | stereo width; the reverb (Space, above) |
| Touch Point | `touch.*` | a finger on the strings: harmonics and mutes (Touch Point, above); Press 0 by default |
| Listening Point | `listen.*` | where along the string the sound is taken (Listening Point, above); Depth 0 by default |
| Partial Arp | `parp.*` | the partial arpeggiator (The partial arpeggiator, above); Off by default. `arp.*` until patchVersion 2 |
| Arpeggiator / Sequencer | `arp.*` | synthkit's (API.md §4b); Off by default (The kit's arpeggiator and sequencer, above) |

## Appendix A: Glossary

- **Partial.** One frequency component of a string's sound, numbered n from the fundamental. Its
  frequency is near n·f0 but stretched by stiffness (inharmonicity B).
- **Mode.** A decaying sinusoid with its own frequency offset and decay rate. A partial is a few modes:
  the unison's strings and their polarisations (The partials, Modes).
- **Component.** One complex one-pole resonator that a voice runs. A mode is one component; so are a
  partial's older-description parts (prompt, burst, aftersound), its beating partners, its swell twin and
  the soft pedal's copies.
- **Layer (velocity layer).** One of the 16 velocities recorded per key. Layers are counted from 1 in
  prose (layer 13 is the one the on-screen keyboard's velocity 100 plays) and from 0 in the code.
- **AB pair.** The recordings' two microphones. Every level, phase and noise band is kept per mic.
- **Envelope, heterodyne.** A partial's level over time, read by shifting it down to 0 Hz and averaging
  with a triangular window.
- **Prompt sound, aftersound.** A partial's fast first decay while the unison's strings move together,
  then its slower decay as they drift apart.
- **Unison.** The two or three strings a key's hammer strikes together.
- **Cluster.** In the treble, a partial that is several components tens of Hz apart.
- **Swell (τ).** How a partial rises at the strike, as 1 − e^{−t/τ}.
- **Strike.** What the recordings hold between the partials in the first 0.3 s: the hammer's knock, the
  action and the soundboard's response.
- **Body ring.** The diffuse sound of the soundboard, case and room after a strike.
- **Free strings, sympathetic bank.** The strings whose dampers are up ring in sympathy with what is
  playing; the bank is the engine's model of them.
- **Perceptual distance.** The project's measure, in %, of how far a render is from its recording
  (`tools/measure/perceptual.py`, docs/TESTING.md). Lower is closer.
- **Calibration by resynthesis.** Rendering the model, comparing with the recordings and correcting the
  stored measurement.
- **Bit-identical (`cmp`).** A control at its default produces exactly the same samples as the build
  without it, checked by comparing renders.

## Appendix B: Design history

Earlier versions and ideas that were tried and dropped, moved out of the sections above so those
describe only what the engine does now.

### The body's ring: calibrating level and decay by resynthesis

Tried and dropped: calibrating the body ring's level and decay over its first second the same way. One line can't follow it, and the long tail high notes need came out 17–27 dB too weak at 2–3 s.

### Overtone Decay: a physical loss law

A physical loss law was tried first: string losses as b1 + b3 f² (air and internal friction), fitted per key to the partials' decays. It doesn't hold on these recordings (each partial's decay mixes a fast and a slow mode of the unison; the fit's error is 50-90 %, and b3's sign changes from key to key), so Overtone Decay scales the measured decays instead of moving a fitted law.

### Space: the 1.6 s noise room it replaced

The Space reverb replaced a 1.6 s generated noise room (one convolver, a 0.32 s amplitude decay, so an RT60 of 2.2 s, low-passed towards 600 Hz as it faded).

### Strike Depth: the first Strike Point (PR #11)

*Before this* (PR #11) every key was taken to strike at 12 %, with notches 30 dB deep, and a partial could rise 12 dB. Its comb missed the bass strings' own above about the 12th partial, and from A2 up it undid notches that aren't there: partials 8, 16, 17 and 25 came up as single ringing overtones (up to 29 dB clear of their neighbours on F#4, 10-17 dB from A2 to C4), like a comb filter.
