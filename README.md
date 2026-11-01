# Piano

A modelled grand piano built on [synthkit](https://github.com/10d6/synthkit). It
runs in the browser (one ~1 MB HTML file) and as a VST3 / AU plugin.

There are no samples in it. Each key is a bank of decaying sine partials, and
everything about them is **measured from recordings of a real grand**:

- partial frequencies, from each string's stiffness (inharmonicity), and the
  instrument's stretch tuning (bass up to 22 cents flat, top octave up to 34 sharp);
- each partial's two-stage decay: the fast "prompt" sound, then the slow
  "aftersound", and the aftersound's beating (the unison strings drifting);
- each partial's starting level at 16 velocities, for both of the recording's
  microphones, and the phase between them, so tone, loudness and stereo
  image change with how hard a key is struck;
- the strike's noise (hammer, action and soundboard knock): what the
  recordings hold beyond the partials, in octave bands per key, velocity and
  microphone, each with its own decay. The bands' filters are steep
  (4th order) and their gains are solved against the filters' overlap, so
  the noise's spectrum matches the measured one rather than spilling hiss
  into the treble.

The recordings sample every minor third; keys between are interpolated. One
voice per key: striking a key that is still sounding adds to its vibration.

**How close is it?** `tools/compare.py` renders notes dry and compares them
with the recordings in octave bands over time. Measured keys land within
1–4 dB (median) up to A5 and ~6 dB in the top octaves. Keys the model never
heard (held out from the data and interpolated) land within 3–4 dB.

**Load.** A pedalled glissando over all 88 keys, the heaviest thing a pianist
can do, renders at ~5× realtime in Chromium; ordinary playing at 15–25×. A
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
python3 -I tools/make_header.py tools/salamander.json src/PianoData.h
python3 -I tools/compare.py ../salamander build/piano_render compare-out  # check against the recordings
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
