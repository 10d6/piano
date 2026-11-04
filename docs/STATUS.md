# Where things stand (4 November 2026)

A snapshot for picking the work up again. `docs/MODEL.md` and `docs/TESTING.md` remain the
reference for how each part works and today's numbers.

## Done and merged (PR #9)

- **A perceptual distance** (`tools/measure/perceptual.py`): how different a note sounds from its
  recording, from a model of the ear. It agrees with the owner's ears on 7 of 8 fixes in the
  history (`validate_perceptual.py`) and with two blind listening tests (`tools/listen/`): rank
  correlation −0.59 in the first; in the second the owner preferred the branch in 8 of 13 trials
  (84 against 74.5), and the distance picked the same build in 9 of 13.
- **Partials as fitted modes** (`tools/analyze_modes.py`): 1-6 decaying sinusoids per partial,
  shared by all velocities, fitted to the recordings in the engine's own terms. Bass partials are
  now where they really are (the stiff-string line was up to two semitones off at A0).
- **Distance from the recordings, measured keys:** median 24.4 % → 9.8 % (bass 19.4 → 8.3,
  middle 23.2 → 6.8, treble 38.1 → 32.6; neighbouring recorded layers are ~5 % apart).
- Found by listening, fixed: attack noise on C5/D#5 (the knock had been re-measured against the
  modes), a weak bass strike (`calibrate_attack.py`).

## On branch `tonal-body` (not merged)

The owner heard a noisy sound lingering for about a quarter second on C5, on main and the branch
alike. Measured: between the partials the recordings hold **tones** (spectral flatness 0.36,
where band noise is 0.54), and the model played that energy as band noise.

- `tools/measure/tonality.py`: tone or noise between the partials, against the recordings.
- `tools/analyze_residual.py`: the tones between every key's partials.
- The body's ring now drives **260 resonators** at those tones' frequencies (Synth.cpp
  `renderBodyTones`) instead of being heard as noise. Tonality now matches the recordings
  (0.40 / 0.32 against 0.36 / 0.31, was 0.53 / 0.54). Distance about unchanged (mean 15.0 →
  14.8 %; treble 32.6 → 31.8, C5 layer 8 10.5 → 12.0). Browser cost about 8 % in ordinary
  playing, none in the worst case.
- Tried and left off: each key's own tones in its voice (the attack's 250 Hz went 10 dB strong;
  `VOICE_TONES=1`).
- Slightly worse than main on the branch: the body ring at 2-3 s (5-15 dB weak, was 5-13) and
  the sustain balance (6.8 dB, was 5.1).
- **The owner's verdict: C5 sounds about the same.** So the tonal ring, though measurably closer
  to the recordings, did not fix what was heard. Not ready to merge.

## Open problems, in the order I'd take them

1. **C5's noisy attack and early sound.** Measured: the knock is 5-13 dB too strong at 4-8 kHz
   in the first 40 ms and up to ~8 dB at 8 kHz to 100 ms, on main and branch alike. C5 sits just
   below where the knock is calibrated; extending the calibration down made it worse (raising the
   too-weak 1-2 kHz spills upward). This is now the likeliest cause of what's heard, more than the
   ring. Next: a knock model whose bands don't spill (e.g. the resonator-style shaping used for
   the ring, or steeper filters for that region), then an A/B on C5 alone, knock off / main /
   recording, to confirm by ear before changing anything else.
2. **The keys between the measured ones** (58 of 88) are no nearer their recordings than before
   the modes (holdout 26.3 % on both). What is particular to a key isn't predictable from its
   neighbours; the next step there is to judge them on plausibility rather than on matching a
   recording none of them has.
3. **The treble** (C6 up, ~32 %): most of its partials keep the older description, because the
   modal fit rarely beats it on noisy envelopes.
4. **What the distance can't see:** noise against tone within a band (now covered by
   tonality.py for the ring, not for the knock), and the body ring's weight. Listening caught
   both; keep A/B pages for anything in the attack or the ring.

## How to resume

- Toolchain: CLAUDE.md "Fresh workspace". Recordings: `../salamander` (README).
- The branch's data are in `tools/salamander*.json`; `make_header.py` and a build reproduce it.
- Before a PR: `run_all.py` (now with perceptual and tonality), `stress.py`, `bench-web.cjs`,
  and a labelled A/B page for the owner on the notes in question.
