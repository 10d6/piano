# Measurements

Each of these answered a question that came up while building the piano, by
measuring the model against the Salamander recordings (or, for note handling,
against what a piano should do). They're kept so the answers can be checked
again after any change.

Run from the repo root, after a native build (and with the recordings cloned,
see the main README):

```
python3 -I tools/measure/run_all.py --sal ../salamander     # everything, summaries only (~1.5 min)
python3 -I tools/measure/<name>.py --sal ../salamander      # one, in full
```

Every script takes `--render PATH` (default `build/piano_render`) and `--sal DIR`
(default `$PIANO_SAL`, else `../salamander`). Notes are rendered dry and layer
by layer (the As Recorded velocity map), so a recorded layer's middle velocity
plays that layer.

| Script | Question | Healthy (today's values: docs/TESTING.md) |
|---|---|---|
| `perceptual.py` | **How different does it sound?** A perceptual distance from a model of the ear (ERB filterbank, loudness, ~1 dB tolerance) in the attack, early sound and sustain, against the distance between neighbouring recorded layers. The first number to look at; validated on the history (`validate_perceptual.py`) and a listening test (`../listen/`). `--compare a.json b.json` shows two runs side by side. | median well under ~15 %; the yardstick (one layer apart) is ~5 % |
| `validate_perceptual.py <dir>` | Does the distance hear what the owner heard? Every fix in the history must rank nearer the recordings than the build before it, where the problem was. | 7 of 8 (docs/TESTING.md) |
| `../compare.py` | Does each note match its recording, band by band over time? | median band error 2–5 dB |
| `partials.py --key K --layer L` | For one note, partial by partial: is a gap in the **data** (stored curve ≠ recording) or the **engine** (model ≠ stored curve)? | model ≈ stored; stored ≈ recording within a few dB |
| `levels.py` | Does the overall level fall off at the right rate? | median within ~3 dB at every time |
| `groups.py` | Are the upper partials (7–12, 13–24) at the right level over time? Too strong sounds buzzy; too weak, dull. | within ~3 dB |
| `ring.py` | Is the body's ring (the echo-like tail between the partials) there, at the right level? | within ~5 dB for the first 1.5 s (5-13 dB weak at 2-3 s: known) |
| `balance.py` | Is the sustain's spectral balance right relative to the fundamental (the "higher-pitched resonance")? | mean \|difference\| ~5-7 dB; C5 region known weak |
| `fluctuation.py` | Do envelopes fluctuate (beat) as much as the real piano's, and do left and right fluctuate independently? | ~3.6 dB as recorded; L/R correlation ~0.5 (recordings ~0.36) |
| `decay_by_velocity.py` | Do the stored decay curves fit soft and loud strikes alike? (data only) | within ~2-4 dB at every layer |
| `attack.py` | Is the attack free of excess hiss or knock, band by band? | median within a few dB; no band far above |
| `velocity.py` | Is the velocity response even, and does the model change from soft to loud as the recordings do? | even dB steps; changes match within a few dB |
| `between.py` | Is what lies between the partials (the strike, its slower part, the body's ring) at the right level, early and late? Too much early on the top keys is hiss; a band's total can be right while this is wrong. | mean near 0, \|mean\| ≤ ~3 dB up to 0.4 s with the recorded strike (8-16 kHz hardest); body a few dB strong after 0.4 s |
| `mechanics.py` | Do the pedal's thump and ring, the pedal-up noise and each key's release noise match their recordings; how loud is the sympathetic ring a note sets going, relative to the note? | pedal bands within ~1–3 dB over 6 s; release ~2 dB (noise varies take to take); sympathetic ring 20–40 dB under mid notes at 1–2 s |
| `notes.py` | Do fast repeats always sound, do event orderings behave, how fast do dampers stop notes, when do voices end, are strikes consistent? | every strike rises (also at 14/s), strikes steady within ~1.5 dB; strike spread ≤ ~1 dB per channel |
| `holdout.py --keys 45,60 [--perceptual] [--define NAME=VALUE]` | How good are the keys the model never heard (interpolated)? `--perceptual` judges them with the perceptual distance; `--define` builds the engine with a compile-time setting, for experiments on the interpolation | 3.4–4.7 dB, about 1–3 dB worse than measured; `--perceptual` about 25 %, which is as far as a real neighbouring key is (`neighbour.py`): not a gap to close |
| `neighbour.py [--keys …]` | Is a left-out key's distance the interpolation's fault? Its measured neighbours' recordings, played at its pitch, against its own | about the same as `holdout.py --perceptual` (today 24.6 % against 25.1 %): then there is nothing left to learn from the neighbours |
| `../stress.py build/piano_render 300` (and `overlap`) | Does anything keep sounding after random fast playing is released? | 0 failures |
| `panel.cjs build-web/web/piano.html` | Does the page's velocity graph work and play, and what velocity does the on-screen keyboard play? | graph caption shows a key; keyboard velocity 100; graph pressed at left, middle, right: 19, 64, 121 |
| `settings.py` | Can the Playing settings, Stiffness and Resonance be heard? Each at its ends against the default, by the perceptual distance; the sympathetic strings' level under the sound (no recordings) | Playing: plain (15 %+) at velocity 30-64, small at 100, nothing at 127; Resonance at 0 dB about 10 dB under a pedalled tail |
| `../diagnose.py` | Attack swell time, attack brightness, noise between partials, partial pitch | rise 10–20 ms; partial pitch within ~2 cents |
| `../bench-web.cjs` | Browser speed | worst case (pedalled 88-key glissando) > ~2× realtime |
| `strikepoint.py` | What does Strike Point do to each key's partials? Against the default, with no recordings: do they follow the key's own comb (`../analyze_point.py`), and is any partial left standing clear of its neighbours (a comb-filter ring)? | off the model ≤ ~2.5 dB rms; new bumps ≤ ~10 dB from A2 up (Strike Point 6-20 %, Strike Depth 0; `--depth D` sweeps Strike Depth, where bumps are intended) |
| `softpedal.py` | What does the soft pedal do to a note (level over time, brightness, distance from no pedal)? No recordings: there are none with it down | quieter at the strike (1-6 dB), sustain within ~2 dB; 11-22 % from no pedal at full |
| `touch.py` | What does Touch Point do to a note (the low partials against the untouched note at 1/2 and 1/3 of the string; a mute's fall time; distance from untouched)? No recordings: it isn't part of the measured piano | partials with a node under the finger within ~1 dB, the others 20 dB or more down; 25-54 % from untouched; mute 40 dB down in 0.3-1.3 s |
| `listen.py` | What does Listening Point do to a note (partials against sin(nπy), the level kept, distance from Depth 0 at four points, and the *Wandering Ear* preset's movement)? No recordings | partials within ~1.5 dB of sin(nπy) away from nodes; level within −4…+1 dB; 5-34 % still, 27 % and more moving |
| `restrike.py` | What does Restrike do when a key is struck while it rings (loud then soft; fast uneven repeats; even repeats; the key let go)? With `--felt0`, is a restrike the sum of its strikes? No recordings | loud then soft 29 % and more from 0 %; fast repeats differ 9-48 %; weakest attack no lower than about −5 dB; even repeats and no pedal near 0 %; the sum 46 dB or better on modal keys |

`docs/TESTING.md` describes the methods behind these, what to run after which
change, and the known gaps. Some lessons these taught (details in the git
history):

- Compare per mic and averaged over ~0.2 s. A single mono snapshot is thrown off
  by beating and by the two mics' phase.
- Several bugs only showed as "everything else is too loud": check the
  fundamental first (`partials.py`).
- Look at a stored curve before blaming the engine, and at the engine's
  components before blaming the data.
- To hear one part of the sound alone, render twice with only its level
  changed (e.g. Resonance 0 and −40 dB) and subtract: everything else cancels,
  since renders are deterministic. Turn down anything that shares its noise
  bank (the body's ring shares the mechanical noises'), or the difference
  isn't linear.
- Low octave bands need long spectra: a 1024-point frame can't tell 31 Hz from
  70 Hz, and the recordings' 50 Hz hum sits right there.
- A measure fitted or calibrated where it can't see (between the partials of a
  bass key with a short window) quietly does harm: check every rule's range.
- Check every velocity layer, not three: per-layer fits can jump 20 dB between
  neighbours.
