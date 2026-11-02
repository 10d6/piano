# Measurements

Each of these answered a question that came up while building the piano, by
measuring the model against the Salamander recordings (or, for note handling,
against what a piano should do). They're kept so the answers can be checked
again after any change.

Run from the repo root, after a native build (and with the recordings cloned,
see the main README):

```
python3 -I tools/measure/run_all.py --sal ../salamander     # everything, summaries only (~10 min)
python3 -I tools/measure/<name>.py --sal ../salamander      # one, in full
```

Every script takes `--render PATH` (default `build/piano_render`) and `--sal DIR`
(default `$PIANO_SAL`, else `../salamander`). Notes are rendered dry and layer
by layer (the As Recorded velocity map), so a recorded layer's middle velocity
plays that layer.

| Script | Question | Healthy (at the time of writing) |
|---|---|---|
| `../compare.py` | Does each note match its recording, band by band over time? | median band error 2–5 dB |
| `partials.py --key K --layer L` | For one note, partial by partial: is a gap in the **data** (stored curve ≠ recording) or the **engine** (model ≠ stored curve)? | model ≈ stored; stored ≈ recording within a few dB |
| `levels.py` | Does the overall level fall off at the right rate? | median within ~3 dB at every time |
| `groups.py` | Are the upper partials (7–12, 13–24) at the right level over time? Too strong sounds buzzy; too weak, dull. | within ~3 dB |
| `ring.py` | Is the body's ring (the echo-like tail between the partials) there, at the right level? | within ~5 dB for the first 1.5 s |
| `balance.py` | Is the sustain's spectral balance right relative to the fundamental (the "higher-pitched resonance")? | mean \|difference\| ~5 dB; C5 region known weak |
| `fluctuation.py` | Do envelopes fluctuate (beat) as much as the real piano's, and do left and right fluctuate independently? | ~3.6 dB as recorded; L/R correlation ~0.6 (recordings ~0.3) |
| `decay_by_velocity.py` | Do the stored decay curves fit soft and loud strikes alike? (data only) | within ~2 dB at every layer |
| `attack.py` | Is the attack free of excess hiss or knock, band by band? | median within a few dB; no band far above |
| `velocity.py` | Is the velocity response even, and does the model change from soft to loud as the recordings do? | even dB steps; changes match within a few dB |
| `notes.py` | Do fast repeats always sound, do event orderings behave, how fast do dampers stop notes, when do voices end, are strikes consistent? | every strike rises; strike spread ≤ ~1 dB per channel |
| `holdout.py --keys 45,60` | How good are the keys the model never heard (interpolated)? | within ~1–2 dB of the measured build |
| `../stress.py build/piano_render 300` (and `overlap`) | Does anything keep sounding after random fast playing is released? | 0 failures |
| `panel.cjs build-web/web/piano.html` | Does the page's velocity graph work, and what velocity does the on-screen keyboard play? | graph caption shows a key; velocity 100 |
| `../diagnose.py` | Attack swell time, attack brightness, noise between partials, partial pitch | rise 10–20 ms; noise at or below the recordings |
| `../bench-web.cjs` | Browser speed | worst case (pedalled 88-key glissando) > ~2.5× realtime |

Some lessons these taught (details in the git history):

- Compare per mic and averaged over ~0.2 s. A single mono snapshot is thrown off
  by beating and by the two mics' phase.
- Several bugs only showed as "everything else is too loud": check the
  fundamental first (`partials.py`).
- Look at a stored curve before blaming the engine, and at the engine's
  components before blaming the data.
