# How the piano is modelled

This explains what the engine does and where each number comes from. The
README has the short version; `docs/TESTING.md` explains how the result is
checked against the real piano.

## The approach

Each note is a **sum of decaying sine partials** (modal synthesis). What isn't a partial is
the strike (the hammer, the action and the soundboard's response in the first 0.3 s), played
from A2 up as each key's own recorded response with its partials removed, 0.3 s per measured
key (see The strike, below); band noise for the strike below A2, for the body's ring and for
the mechanism. Around the notes there is a bank of free strings for sympathetic resonance.
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
  `tools/measure/holdout.py` checks how well that works.

## A note: the partials

Each key is a set of **components**. A component is a complex one-pole
resonator `z ← z·r·e^{jω}`, with gains from its real and imaginary parts to
the left and right outputs. The voice keeps the components in
structure-of-arrays form in lanes of 8, so the inner loop vectorises (WASM
SIMD in the browser). Its state starts at the measured amplitude and phase.
Everything about a component comes from measurement.

### Frequencies

- **Stiffness and tuning.** `analyze.py` fits each sampled key's f0 and
  inharmonicity B to its partial peaks, `f_n = n·f0·√(1 + B n²)`. The fit
  uses one 3 s spectrum of the four loudest layers summed: peaks within ±6 %
  of f0 of the prediction, weighted least squares on (f_n/n)² = f0²(1 + B n²),
  iterated. The key's pitch is the recording's own **stretch tuning**: bass
  up to 22 cents flat, top up to 34 sharp. It's scaled by the Stretch
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

  Only A5 and up have been re-analysed with the refinement and clusters so
  far. The other keys' data predates them, and their offsets are 0.

### Modes: the partial fitted as the engine plays it

Most partials (about three quarters, and most of the sound below C6) are now played as their
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

### Decay: prompt, aftersound and first burst

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

### The swell at the strike

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
  twice as slowly.

### Levels and phases per velocity and microphone

For each partial, layer and mic, `analyze.py` reads the complex amplitude over
a 40 ms window from 15 ms (later for low keys, whose analysis window is
longer). It brings it back to t = 0 along the partial's own two-stage decay,
which gives a **level** (centi-dB in the header) and a **phase** (int8). The
two mics' gains and the phase between them place each partial in the stereo
image, as the AB pair heard it. Phases matter: with all partials in sine phase
the sum was a buzzy pulse train.

Where a cluster's power is wider than the envelope window takes in, its levels
are raised by the difference (0–3 dB in practice).

### Unison beating

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

### Between the sampled keys

A key between two sampled keys takes from both: weights by position, B and
onset τ interpolated in log, cents linearly. A modal partial takes its modes from the nearer
key (see Modes, above). Each partial's level is the modes' power at the strike where it has
modes, so the blend compares like with like (analyze.py's own was read at its own frequency,
which in the bass could be a few Hz off the partial and up to 30 dB low). A partial measured on only one
side fades out towards the other (−24 dB at the far end). Phases and clusters
come from the nearer key, since phases can't be averaged meaningfully.

### Brightness, range and cut

- **Brightness** tilts the partials by dB per octave of partial number, and
  compensates the overall level so loudness stays put.
- Partials more than **70 dB** under the note's loudest at that velocity
  aren't started (`kRangeDb`). Components under **−105 dBFS** are dropped
  (`kFloorDb`).

## Velocity

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
`vel.note`) is drawn from the same function.

## The strike

What the recordings hold **between the partials** in the first 0.3 s: the hammer's knock, the
key and action, the soundboard's response. It used to be played as octave bands of random
noise on every key. But it isn't noise: it is the same response every time the key is struck.

- Between neighbouring velocity layers its waveform correlates **0.9-0.99** from D#2 up (0.6-0.9
  between layers 8 and 16), with no time shift.
- Between the partials it is tone-like: spectral flatness ~0.3, where band noise is ~0.55.
- Played as noise, whatever a band's level got wrong was heard as **hiss**: the white noise
  heard in the attack of C5, D#5, D#6 and A7 (in the A/B of the recorded strike against the
  band noise, the noise was 8-17 dB too strong in some band on each of them).

### From A2 up: the recorded strike (`tools/analyze_strike.py`)

- **One response per measured key and mic**: its velocity-100 recording (layer 13, what the
  page's on-screen keyboard plays) with every partial notched out, 0.3 s, faded out from
  0.15 s (the body's ring carries on after that). The notches: every partial and cluster
  component the model plays, then the stiff-string series up to 22 kHz, ±max(16 Hz, 4 % of
  f0, the modes' spread), raised-cosine edges; zero-phase over one long FFT, so nothing between
  them is touched. Not the mains hum: notches that narrow smear the strike's burst at those
  frequencies over hundreds of ms, as faint tones at 250, 300, 350 Hz… In PR #10's strikes they
  stood 1-6 dB under the strike's own 125 Hz-1 kHz from 40 ms on; without them, the treble came
  0.4-2.4 points nearer its recordings (mean 12.1 → 11.8 %). Stored as 16-bit samples at 48 kHz in
  `src/PianoStrike.h` (generated by `make_header.py`): 22 keys, 1.3 MB.
- **Velocity changes its brightness as the felt does.** A soft strike's knock has 15-30 dB less
  at 2-4 kHz than a velocity-100 one, a loud one 5-14 dB more. A first-order high shelf
  follows that within 1-3 dB per octave band (125 Hz-8 kHz, first 40 ms; with level alone,
  3-11 dB): one corner frequency per key (250 Hz-2.5 kHz, mostly 1-2 kHz) and a gain per layer
  (about −10…−24 dB at layer 1, +5…+16 at layer 16), smoothed across the layers and kept within
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

### Below A2: band noise (`tools/analyze_noise.py`)

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

**Tried and dropped:** calibrating the body ring's level and decay over its
first second the same way. One line can't follow it, and the long tail high
notes need came out 17–27 dB too weak at 2–3 s.

## The hammer: Hardness and Strike Point

Two controls change the hammer itself, around what was measured. At their defaults (0 % and 12 %)
the output is exactly that of the measured piano (bit for bit).

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

**Strike Point** (`tone.strikepoint`, 4-25 % of the string's length) moves where the hammer meets
the string. Partial n is excited as |sin(nπx)|; the recordings' x is about 1/8.3 from A0 to C4
(docs/TESTING.md), so each partial changes by the ratio to x = 12 %, kept within −30…+12 dB near
the nodes (|sin| no lower than 0.03). Nearer the end is thinner and brighter (C4's attack centroid
500 → 940 Hz at 4 %), further in darker and hollower (440 Hz at 25 %). Above C4 the real strike
point is unknown (it moves towards the end on most grands), so there it changes the tone around
the recordings without claiming where the hammer was.

## The strings: Stiffness and Overtone Decay

Two controls change the strings around what was measured; at their defaults (100 % and 0 %) the
output is exactly the measured piano's (bit for bit). Both reach the voices and the sympathetic
string bank alike.

**Stiffness** (`string.stiffness`, 0-300 %) scales the strings' inharmonicity. Partial n of a
string is at n·√((1 + B n²) / (1 + B)) times its fundamental, then `dev` cents off that line where
it was measured elsewhere (`partialRatio`); Stiffness scales B and those offsets alike. At 0 % the
strings are ideal, the partials at whole multiples of the fundamental (harp-like; organ-like in the
bass); at 300 % they spread three times as far (C4's 12th partial: +33 cents as measured, +253 at
300 %), towards bells. It applies at the strike, so to the notes struck next; the stretch tuning
(Stretch) is kept as it is.

**Overtone Decay** (`string.overtonedecay`, −100…+100 %) changes how long the upper partials ring
against the fundamental: each component's measured decay times (its frequency / the
fundamental)^γ, γ = the setting / 100, the factor kept within ¼…4 and no component slower than
0.3 dB/s (`overtoneDecayDb`, applied where the poles are set, so it acts on sounding notes too).
Below 0 the upper partials ring on (bright, steely: on C3 at −100 % the 6th partial fades at
2 dB/s against the fundamental's 5.7); above, they die sooner (dark, like gut or nylon: C5's 6th
at 27 dB/s, measured 16). Even at Sustain 400 % and −100 %, the undamped top keys are silent
within 20 s.

A physical loss law was tried first: string losses as b1 + b3 f² (air and internal friction),
fitted per key to the partials' decays. It doesn't hold on these recordings (each partial's decay
mixes a fast and a slow mode of the unison; the fit's error is 50-90 %, and b3's sign changes from
key to key), so Overtone Decay scales the measured decays instead of moving a fitted law.

## The body's ring

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

### What the ring is (measured for a string-based model, not adopted)

Between the partials, 0.5-2.5 s after the strike, the recordings hold mostly **tones** (30-93 % of
the energy in narrow peaks, by band) fading at **5-20 dB/s**, far slower than a soundboard's or a
room's: strings ringing along. Part of it is the free strings above E6 (on C6 at velocity 100,
half the tonal power lies on their partials, against 1 % by chance); the rest is likely the damped
strings' imperfect stop and their back lengths. The model's own free strings (the sympathetic
bank) give 20-60 dB less than that ring, and the band noise is 5-9 dB strong at 0.3-1 s, 7-19 dB
weak at 2-3 s and almost all noise. But with the recording's own ring in its place
(`tools/listen/ab_ring.py`) the owner heard no difference from today's, so it was left as it is.
A string-based ring would make the coupling (`kCoupling`) measurable from the free strings' ring.

## Mechanical noises

From the release and pedal recordings (`analyze_mech.py`): each recording
becomes an **envelope** in 10 ms frames (its total power, under its peak),
plus **band gains per mic**. The band gains come from one long spectrum of the
whole recording, so the lowest band is resolved, with the hum left out.

| Noise | When | Level |
|---|---|---|
| Key release (rel1–88) | each key's note-off | sfz −37 dB, following the strike's velocity (82 % of 40·log10 v), since the kit has no release velocity |
| Pedal down (pedalD) | CC64 rising past half, takes alternating | the thump only (the recording less its string ring), sfz −20 dB |
| Pedal up (pedalU) | CC64 falling past half | sfz −19 dB |

They play through the body's noise bank, so they cost almost nothing, and
start at their event's sample. **Key & Pedal** scales them.

## Sympathetic resonance: the free strings

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
- **Coupling.** The drive is z += K·x/fs, with K = 0.015/s (`kCoupling`)
  scaled by **Resonance**. It is a judgment: the recordings were made note by
  note, so no two notes sound together in them. The free strings above E6 do
  ring along in every recording, though (What the ring is, above), so it could
  be measured from them. The default puts the halo 20–40 dB under a
  mid-range note a second or two in.
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

## Voices, dampers and the rest

- **One voice per key** (`allocateVoice`).
  - Striking a key that's still sounding **hands the old vibration over**: the
    old components fade three times as fast as the new strike swells, so fast
    repeats can't cancel.
  - A voice holds up to 2048 components.
  - It ends when its total level is under −84 dBFS.
- **Dampers** take 70 dB/s at A0 rising to 300 dB/s at E6, 3 % faster per
  partial number. **Dampers** scales them.
  - Above E6 a grand has no dampers: those notes ring on after release, fading
    as when held. **Top Dampers → All keys** damps every key instead, as many
    digital pianos do.
- **Load guard.** When more than 4000 components sound across all voices, the
  floor below which components are dropped rises (by up to 40 dB).
- **Space.** **Width** is mid/side width. **Room** is a short generated room:
  1.6 s decorrelated noise tails per side, a 4 ms rise, a 0.32 s decay, and a
  low-pass falling from ~9.6 kHz towards 600 Hz as the tail fades. The
  measurements are close-miked, so Room 0 is the dry piano.
- **Headroom.** Output is the recordings' level −10 dB (`kTrimDb`); one loud
  recorded note peaks near 0 dBFS.

## Parameters

| Parameter | ID | What it changes |
|---|---|---|
| Velocity Map | `play.velmap` | Smooth / As Recorded (Velocity, above) |
| Touch, Dynamics, Tone Range, Soft Attack | `play.touch`, `play.dynamics`, `play.tone`, `play.softattack` | velocity → level, tone, swell |
| Brightness | `tone.bright` | partial tilt, dB per octave of partial number |
| Attack | `tone.attack` | swell time τ |
| Hammer | `tone.hammer` | the strike's level (recorded from A2 up, band noise below) |
| Hardness | `tone.hardness` | the hammer's felt: its spectral corner, ±3 octaves (The hammer, above) |
| Strike Point | `tone.strikepoint` | where the hammer meets the string, % of its length (12 %: as recorded) |
| Body | `tone.body` | body ring level |
| Key & Pedal | `tone.mech` | mechanical noises, and the pedal's lift ring |
| Unison | `string.unison` | beat offsets and cluster spreads |
| Stretch | `string.stretch` | stretch tuning, 0 = equal temperament |
| Stiffness | `string.stiffness` | the strings' inharmonicity, % of measured (0: harmonic) |
| Overtone Decay | `string.overtonedecay` | upper partials' decay against the fundamental's (The strings, above) |
| Sustain | `string.sustain` | all string decays (voices and bank) |
| Dampers | `string.damper` | damper speed |
| Top Dampers | `string.topdampers` | None above E6 / All keys |
| Resonance | `string.resonance` | sympathetic drive |
| Width, Room | `space.width`, `space.room` | stereo width, small room |
| Partial Arp | `arp.*` | the partial arpeggiator (Creative: the partial arpeggiator, below); Off by default |

## Creative: the partial arpeggiator

Not part of the measured piano: with **Arp Pattern** Off (the default) the output is
bit-identical to the piano without it. Turned on, each note's partials take turns standing
out, a melody inside the note (`PianoVoice::arpBlock`).

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
  pattern's other partials sit at Rest; partials outside it are untouched. Negative Boost
  digs a moving notch instead (the *Partial Notch* preset), which the owner found the more
  natural of the two; *Harmonic Arp* is Octaves, Width ¼ octave, +8 dB, Rest 0.
- **How.** Each component keeps its partial number (`pnum`). Per control block, the gain each
  partial should have at the block's end is computed, and each component's output to the mics
  ramps to it from the last block's gain. Only the output is scaled, not the oscillator's state,
  so a partial's natural decay carries on underneath and it comes back exactly to its own
  level. Each voice has its own random stream for Random, so the beats and noise don't change.
  It costs about 7 % more CPU when on, nothing when off.

## The data pipeline

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
                                                    choose_modes.py (renders; per key, modes or not; then make_header, build)
```

The commands are in the README. Everything downstream of `analyze.py` uses its
partials, so rerun those steps after it; the noise analysis uses the modes' envelopes
(`analyze.partial_env`), so it follows `analyze_modes.py`. The modal fit takes about 40 minutes
on two cores and caches each key in `<tmp>/piano-modes/`. Clear the noise corrections before
recalibrating if the noise analysis changed. `analyze.py` takes about 25
minutes on one core; run halves of the key list in parallel and `--merge`
them.

## What isn't modelled (yet)

- **The dampers' imperfect stop.** On a real piano a residual ring continues
  after the damper lands (the `harm*` samples).
- **Half-pedalling.** The kit treats CC64 as on/off.
- **The soft pedal**, **sostenuto**, **release velocity.**
- **Strings coupling each other** beyond the bank's one-way drive. The bank's
  output doesn't feed back into the voices.
- **The body ring follows one decay per band.** It's a few dB strong at
  0.4–1 s and weak at 2–3 s on many keys.
- **Partials 13–24** come out about 5 dB weak by 1.4 s.
- **A6's** attack brightens less from soft to loud than the recordings at 2-4 kHz.
- **The strike's finer changes with velocity** (one response per key, shaped by a shelf and two
  gains), and **between the measured keys** (the nearer key's response).
- **The top octave and a half** (C6 up) is the weakest measured region (22 %; C8 42 %). C5 and
  nearby, long the weakest, is at 5.5 % since the recorded strike.
- **A soft C4's 62 Hz thump** is ~20 dB too strong.
- **The keys between the measured ones** gain little from the modes (docs/TESTING.md, holdout):
  what is particular to a key isn't predictable from its neighbours.
- **The treble's partials** mostly keep the older description: above C6 the modal fit seldom
  beats it on these noisy envelopes.
