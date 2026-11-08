# Piano

A modelled grand piano built on [synthkit](https://github.com/10d6/synthkit). It
runs in the browser (one ~4.6 MB HTML file) and as a VST3 / AU plugin.

How it's modelled, in detail: [`docs/MODEL.md`](docs/MODEL.md). How it's
tested against the real piano: [`docs/TESTING.md`](docs/TESTING.md).

Each key is a bank of decaying sine partials, and everything about them is **measured from
recordings of a real grand**. The one thing taken from the recordings as sound is each key's
strike, 0.3 s with its partials removed (below):

- partial frequencies, from each string's stiffness (inharmonicity), and the
  instrument's stretch tuning (bass up to 22 cents flat, top octave up to 34 sharp);
- each partial as its **modes**: the unison's strings and their polarisations, 1-6 decaying
  sinusoids, each with its own frequency and decay, the same at every velocity, with a level
  and phase per velocity and microphone (`tools/analyze_modes.py`). They are fitted to the
  recordings' envelopes at once, all 16 velocities together, with the engine's own swell and
  the analysis window applied to the model, so the prompt sound, the aftersound and the
  beating come out as the recordings have them rather than being measured one by one. In the
  bass the partials are followed up the series to where they really are (A0's reach two
  semitones sharp of the stiff-string line). Where the modes don't follow a partial better
  than the older description below, mostly in the top treble, it keeps that;
- otherwise, each partial's two-stage decay (the fast "prompt" sound, then the slow
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
- **the strike** (hammer, action and soundboard): from A2 up, each measured key's own, as
  recorded at velocity 100 with its partials notched out (0.3 s per mic). It is the same
  response every time a key is struck (its waveform correlates 0.9-0.99 between neighbouring
  velocities), so it is played as that response, not as noise: noise was heard as hiss in the
  attack of C5, D#5, D#6 and A7. Velocity brightens it as the felt does, through a shelf
  measured per key and layer (a soft strike has 15-30 dB less at 2-4 kHz), and sets its
  level, per mic, for the knock and the slower part after it. Below A2, where the strike is
  masked by the dense partials, it is octave bands of noise measured per key, velocity and
  microphone;
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
them with the pedal down, and those above E6, which have none on a grand) and that isn't sounding as a
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
voice per key: striking a key that is still sounding hands its vibration over
to the new strike. As on a grand, keys above E6 have no dampers and ring on
after release (**Top Dampers** can damp every key instead).

**The hammer.** How a key brightens from soft to loud is mostly the felt: a faster hammer
stays on the string for less time, and its spectrum reaches higher. One spectral corner per key,
rising with the hammer's speed, explains 70-86 % of how the recordings' partials change with
velocity from A0 to C6 (`tools/analyze_hammer.py`). **Hardness** moves that corner (softer felt,
duller and slower to swell in, down to a felt-piano mute; harder, brighter), and the partials and
the strike change with it. **Strike Point** moves where the hammer meets the string (the recorded
piano: about 1/8 of its length). At their defaults the piano is as measured.

**The strings.** **Stiffness** scales their measured inharmonicity, from ideal harmonic strings
(harp-like) through the recorded piano to three times as stiff (bell-like). **Overtone Decay**
makes the upper partials ring on longer than the fundamental (bright, steely) or die sooner
(dark, gut-like). At their defaults the piano is as measured.

**Velocity.** Velocity is mapped onto each key's measured loudness range
(its softest recording to its loudest) along a smooth curve, whatever the
recorded layers' own velocity spacing, and the tone comes from the recorded
layer with that loudness. The Playing panel shapes it: **Touch** (how hard
you have to play), **Dynamics** (the loudness range), **Tone Range** (how
much the tone darkens as you play softer) and **Soft Attack** (how much
softer notes swell in more slowly), with a graph of loudness and tone across
velocity for the last key played. The page's on-screen keyboard always plays
velocity 100; use a MIDI keyboard to play dynamics.

**How close is it?** `tools/measure/perceptual.py` measures how different a note sounds from
its recording, from a model of the ear (an ERB filterbank, loudness, a ~1 dB tolerance), in the
attack, the early sound and the sustain. It agrees with the owner's ears on 7 of the 8 problems
fixed in the history and with a blind listening test (`tools/listen/`). The 30 measured keys
are a median 8.5 % from their recordings (bass 8.7 %, middle 4.9 %, treble 22 %), against 9.8 %
with the strike as noise and 24 % before the modes; two neighbouring velocity layers of the
recordings are 5 % apart. In octave bands (`tools/compare.py`) measured keys land within
0.2–3.0 dB. Keys the model never heard (held out and interpolated) are 25 % from their
recordings: what is particular to a key isn't predictable from its neighbours, and that is the
next thing to work on.
`docs/TESTING.md` has every measure, today's numbers and the known gaps.

For the partials that keep the older description, the unison beat's depth is measured as one
sinusoid at the strongest beat rate, which underestimates how much a real unison's envelope
fluctuates; `BEAT_SCALE` in `tools/make_header.py` (1.8) is calibrated so the model's envelopes
fluctuate about as much as the recordings'.

**Load.** A pedalled glissando over all 88 keys, the heaviest thing a pianist
can do, costs the same with the modes as before them (docs/TESTING.md). A
load guard drops the quietest partials first if more than 4000 are sounding.

## Around the piano

**Space.** The recordings are close-miked, so the room is added: a reverb with five spaces
(Room, Chamber, **Hall** by default, Cathedral, Plate). Each space's early reflections come from
a model of the room, played by synthkit's convolver; its tail is a smooth feedback network, so
**Decay**, **Size** and **Damping** move freely, with **Pre-delay**, **Early** (the reflections'
level) and **Low/High Cut** on the reverb only. **Reverb** at 0 is the dry piano as measured.
Try the *Concert Hall* and *Cathedral* presets.

**Partial Arp** (a creative extra, off by default): each note's partials take their turn, as a
melody inside the note. By default a turn digs the partials out (a moving notch, −20 dB);
positive **Boost** brings them forward instead. The pattern walks a scale through the harmonic
series (pentatonic, minor pentatonic, triad, all or odd harmonics), one partial at a time or a
whole pitch with its octaves (**Group**), spread to the neighbouring partials by **Width**. Try
the *Partial Notch* and *Harmonic Arp* presets. Off, the piano is as measured.

**Arpeggiator and sequencer** (synthkit's, off by default): **Arp** plays the keys you hold one
after another (up, down, random, chord, or a pattern drawn in the piano roll or generated);
**Sequencer** lets each key sound and adds its own pattern of notes at intervals above it, in
any scale. Each note is a full piano strike that ends with its damper (the note envelope is Off
by default; Crossfade and the others are there too). Keys above E6 have no dampers, so notes the
arpeggiator plays up there ring on, as on a grand. Try the *Arp Cascade* and *Sequencer
Overtones* presets.

docs/MODEL.md has how both work.

## Measurement data

The model data (`src/PianoData.h`) is derived from the **Salamander Grand
Piano V3** by Alexander Holm, licensed under
[CC BY 3.0](https://creativecommons.org/licenses/by/3.0/). It was changed
from audio recordings into measured model parameters, except for each
measured key's strike from A2 up (`src/PianoStrike.h`): 0.3 s per microphone of
its velocity-100 recording with the partials notched out. To measure again:

```
git clone https://github.com/sfzinstruments/SalamanderGrandPiano ../salamander   # ~700 MB
python3 -I tools/analyze.py ../salamander tools/salamander.json          # partials (~25 min; see --merge)
python3 -I tools/analyze_onset.py ../salamander tools/salamander.json tools/salamander_onset.json
python3 -I tools/analyze_modes.py ../salamander tools/salamander.json --procs 2   # modes (~40 min; cached per key)
python3 -I tools/analyze_noise.py ../salamander tools/salamander.json tools/salamander_noise.json
python3 -I tools/analyze_body.py ../salamander tools/salamander.json tools/salamander_body.json
python3 -I tools/analyze_mech.py ../salamander tools/salamander_mech.json         # key release and pedal noises
python3 -I tools/analyze_hammer.py tools/salamander.json tools/salamander_hammer.json   # the hammer's spectrum (from the partials)
python3 -I tools/make_header.py tools/salamander.json src/PianoData.h
# then build, and calibrate the band noise against the recordings (3 passes, make_header and a build between;
# it skips keys with a recorded strike, so today it changes nothing):
python3 -I tools/calibrate_noise.py ../salamander build/piano_render tools/salamander_noise.json
python3 -I tools/make_header.py tools/salamander.json src/PianoData.h
python3 -I tools/choose_modes.py ../salamander build/piano_render tools/salamander.json   # per key: modes or not
python3 -I tools/make_header.py tools/salamander.json src/PianoData.h                    # and build again
python3 -I tools/calibrate_attack.py ../salamander build/piano_render tools/salamander.json  # the bass's strike (3 passes, make_header and a build between)
python3 -I tools/analyze_strike.py ../salamander build/piano_render tools/salamander.json tools/salamander_strike.npz  # the recorded strike, A2 up
python3 -I tools/make_header.py tools/salamander.json src/PianoData.h                    # writes PianoStrike.h too; build again
python3 -I tools/measure/perceptual.py --sal ../salamander                              # how different it sounds
python3 -I tools/compare.py ../salamander build/piano_render compare-out  # check against the recordings
python3 -I tools/diagnose.py ../salamander build/piano_render diag-out    # attack, noise, brightness, pitch
python3 -I tools/measure/run_all.py --sal ../salamander                   # every measurement (tools/measure/README.md)
node tools/bench-web.cjs build-web/web/piano.html tests/glissando-pedal.json  # browser speed
python3 -I tools/space.py build/piano_render                                   # the reverb's levels and decay times
```

(needs `pip install numpy scipy soundfile matplotlib`; the browser tools need
Playwright with Chromium)

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
| `src/` | the sound engine (`Synth.cpp`), the reverb (`Reverb.h`), the measured data (`PianoData.h`) and the recorded strikes (`PianoStrike.h`), both generated |
| `docs/` | how it's modelled (`MODEL.md`) and tested (`TESTING.md`) |
| `tools/` | measuring the recordings, calibrating, comparing with them, stress test, browser benchmark, the reverb's levels and decays (`space.py`) |
| `tools/measure/` | every measurement against the recordings (`README.md` there), the perceptual distance first |
| `tools/listen/` | a blind listening test page against the recordings, and its analysis against the perceptual distance; A/Bs of the strike (`ab_strike.py`) and the body's ring (`ab_ring.py`) against their recorded versions |
| `presets/` | factory presets (`init.json` is what a new instance starts on) |
| `tests/` | render scenarios checked in CI |
| `ui/layout.json` | the panel layout (and `ui/custom.js` for custom widgets, `ui/style.css` for styles, or `ui/page.js` for a page of the synth's own: API.md §9a) |
| `synthkit/` | the kit, as a pinned submodule |
