# Piano

A modelled grand piano built on [synthkit](https://github.com/10d6/synthkit). It
runs in the browser (one ~1 MB HTML file) and as a VST3 / AU plugin.

There are no samples in it. Each key is a bank of decaying sine partials, and
everything about them is **measured from recordings of a real grand**:

- partial frequencies, from each string's stiffness (inharmonicity), and the
  instrument's stretch tuning (bass up to 22 cents flat, top octave up to 34 sharp);
- each partial's two-stage decay (the fast "prompt" sound, then the slow
  "aftersound") and the aftersound's beating (the unison strings drifting),
  measured separately for soft, medium and loud strikes, since softly struck
  notes sustain relatively longer;
- each partial's first burst at the strike: a short transient above its
  decay line, gone within a fraction of a second;
- each partial's starting level **and phase** at 16 velocities, for both of
  the recording's microphones, so tone, loudness, stereo image and the shape
  of the waveform change with how hard a key is struck. Each partial's beat
  reaches the two microphones at its own angle, so left and right fluctuate
  independently, as with the recording's spaced pair;
- how each note swells in (hammer contact, soundboard response): fitted per
  key from the first 40 ms of the recordings, 2–15 ms;
- the strike's noise (hammer, action and soundboard knock): what the
  recordings hold between the partials in the first quarter second, in octave
  bands per key, velocity and microphone, as the knock itself and a slower
  part after it. The bands' filters are steep (8th order: a top key's knock
  falls ~45 dB over two octaves) and their gains are solved against the
  filters' overlap. Then each key is rendered and its knock compared with the
  recording's, between the partials, and corrected (`tools/calibrate_noise.py`);
- in the treble, where each partial really is: the stiff-string formula puts
  some top-key partials 10-30 cents off (A6's third partial is 11 cents flat
  of it, F#7's 60 sharp), so they're measured from the recordings' first
  0.3 s. And there, a partial is a cluster: the unison's strings tens of Hz
  apart, sometimes with a component well to the side. Where a cluster is wide,
  the model plays its measured components rather than one tone and a beat;
- the body's ring: after each strike the soundboard, case and room ring on,
  as diffuse sound between the strings' partials a couple of dozen dB below
  the note, fading over seconds. Measured per key (from C3 up; below, the
  partials are too dense to separate it), velocity, microphone and octave
  band, and played by one shared bank of band noise. It gives high notes
  their echo-like tail; without it they're a few pure partials, like a bell.
- the mechanical noises: each key's own noise as it comes back up (all 88
  keys were recorded), and the sustain pedal's thump going down and up, as
  octave-band envelopes in 10 ms steps per microphone. They play through the
  body's noise bank, so they cost almost nothing.

**Sympathetic resonance.** Every string's lower partials (to the 16th, below
4 kHz) are also a bank of resonators, at the frequencies the voices play and
with the strings' measured slow decay. A string whose damper is up (all of
them with the pedal down, those above E6 always) and that isn't sounding as a
note of its own is driven by everything the piano plays, so it rings along
where its partials line up with the notes': the octave below a note sets the
upper string going, a fifth sets its twelfth, and so on, and pedalled playing
gets its halo. Pressing the pedal sets the free strings ringing on its own,
as the dampers lift: that ring is calibrated against the pedal recording
(within ~1 dB per band over its 6 s). How strongly played notes drive the
strings isn't in the recordings (they were made note by note), so its
default (**Resonance**, 0 dB) is a judgment, to be tuned by playing: the halo
sits 20–40 dB under a mid-range note a second or two in, closer under high
notes, whose own sound fades sooner than the lower strings' ring. The bank runs at half
the sample rate, and only for strings that are free or still ringing.

The recordings sample every minor third; keys between are interpolated. One
voice per key: striking a key that is still sounding adds to its vibration.

**Velocity.** Velocity is mapped onto each key's measured loudness range
(its softest recording to its loudest) along a smooth curve, whatever the
recorded layers' own velocity spacing, and the tone comes from the recorded
layer with that loudness. The Playing panel shapes it: **Touch** (how hard
you have to play), **Dynamics** (the loudness range), **Tone Range** (how
much the tone darkens as you play softer) and **Soft Attack** (how much
softer notes swell in more slowly), with a graph of loudness and tone across
velocity for the last key played. The page's on-screen keyboard always plays
velocity 100; use a MIDI keyboard to play dynamics.

**How close is it?** `tools/compare.py` renders notes dry, layer by layer (the
As Recorded velocity map), and compares them with the recordings in octave
bands over time: measured keys land within 2–5 dB (median). `tools/diagnose.py`
measures what's audible at the strike: the attack's swell, its brightness per
velocity (tracks the recordings), the noise between the partials (kept a
little below the recordings) and the partials' pitch. Keys the model never
heard (held out from the data and interpolated) land within 3–4 dB.

The unison beat's depth is measured as one sinusoid at the strongest beat
rate, which underestimates how much a real unison's envelope fluctuates.
`BEAT_SCALE` in `tools/make_header.py` (1.8) is calibrated so the model's
envelopes fluctuate about as much as the recordings' (3.0 against 3.6 dB,
detrended, partials 1–4).

**Load.** A pedalled glissando over all 88 keys, the heaviest thing a pianist
can do, renders at ~2.2× realtime in Chromium; ordinary playing at ~9×. A
load guard drops the quietest partials first if more than 4000 are sounding.

## Measurement data

The model data (`src/PianoData.h`) is derived from the **Salamander Grand
Piano V3** by Alexander Holm, licensed under
[CC BY 3.0](https://creativecommons.org/licenses/by/3.0/). It was changed
from audio recordings into measured model parameters; no audio from it is
included. To measure again:

```
git clone https://github.com/sfzinstruments/SalamanderGrandPiano ../salamander   # ~700 MB
python3 -I tools/analyze.py ../salamander tools/salamander.json          # partials (~10 min)
python3 -I tools/analyze_noise.py ../salamander tools/salamander.json tools/salamander_noise.json
python3 -I tools/analyze_onset.py ../salamander tools/salamander.json tools/salamander_onset.json
python3 -I tools/analyze_body.py ../salamander tools/salamander.json tools/salamander_body.json
python3 -I tools/analyze_mech.py ../salamander tools/salamander_mech.json         # key release and pedal noises
python3 -I tools/make_header.py tools/salamander.json src/PianoData.h
# then build, and calibrate the strike noise against the recordings (2-3 passes, rebuilding between):
python3 -I tools/calibrate_noise.py ../salamander build/piano_render tools/salamander_noise.json
python3 -I tools/make_header.py tools/salamander.json src/PianoData.h
python3 -I tools/compare.py ../salamander build/piano_render compare-out  # check against the recordings
python3 -I tools/diagnose.py ../salamander build/piano_render diag-out    # attack, noise, brightness, pitch
python3 -I tools/measure/run_all.py --sal ../salamander                   # every measurement (tools/measure/README.md)
node tools/bench-web.cjs build-web/web/piano.html tests/glissando-pedal.json  # browser speed
```

(needs `pip install numpy soundfile matplotlib`)

## Playing it

- **In the browser:** every PR gets a preview link (posted on the PR), and
  `main` is at the repo's GitHub Pages site. This needs GitHub Pages turned
  on once: Settings → Pages → Deploy from a branch → `gh-pages` / root.
- **In your DAW:** each PR's CI run has a **plugin-windows**, **plugin-macos**
  and **plugin-linux** download (under *Checks → CI → Summary → Artifacts*).
  Unzip it and follow `INSTALL.md` inside. Tagging a version (`v0.2.0`)
  publishes them as a release.

## Building

```
git clone --recursive <this repo>
cmake -S . -B build && cmake --build build
./build/piano_render --check . --out renders     # every check; WAVs into renders/
```

The browser page and the plugins are built the same way as in synthkit: see
[`synthkit/API.md`](synthkit/API.md) §11–12.

## Layout

| | |
|---|---|
| `synth.json` | identity (name, IDs, plugin codes) and kit features |
| `src/` | the sound engine (`Synth.cpp`) and the measured data (`PianoData.h`, generated) |
| `tools/` | measuring the recordings, comparing with them, browser benchmark |
| `presets/` | factory presets (`init.json` is what a new instance starts on) |
| `tests/` | render scenarios checked in CI |
| `ui/layout.json` | the panel layout (and `ui/custom.js` for custom widgets, `ui/style.css` for styles, or `ui/page.js` for a page of the synth's own: API.md §9a) |
| `synthkit/` | the kit, as a pinned submodule |
