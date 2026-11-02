// Piano: a modal piano whose strings are measured from a real one.
//
// Each key is a bank of decaying sine partials. Everything about them comes
// from measurements of the Salamander Grand Piano V3 recordings
// (tools/analyze.py → src/PianoData.h), taken every minor third and
// interpolated between:
//
//   - the partial frequencies, from each string's measured stiffness
//     (inharmonicity B), and the instrument's stretch tuning;
//   - each partial's two-stage decay: a fast "prompt" sound, then a slow
//     "aftersound" (the 2-3 strings of a unison drifting out of phase), and
//     the aftersound's beating;
//   - each partial's starting level at 16 velocities, for both of the
//     recording's microphones, and the phase between them, so loudness,
//     tone and the stereo picture all change with how hard a key is struck,
//     as on the recordings.
//
// One voice per key, as on a piano: striking a key that is still sounding
// adds to the vibration that's already there.
//
// Around the voices, in the synth: the body's ring after each strike, the
// mechanical noises (keys coming up, the pedal), all as octave bands of
// noise; and the strings' sympathetic ring, a bank of every string's lower
// partials that the strings with their dampers up pick up from the playing.
#include <synthkit/synth.h>

#include <algorithm>
#include <cmath>

#include "PianoData.h"

using namespace sk;
namespace pd = piano_data;

namespace {
constexpr int kMaxPartials = 96;
constexpr int kLanes = 8;
constexpr int kCapacity = 2048;           // components per voice: room for fast restrikes of the lowest keys
constexpr float kVoiceEndDb = -84.f;      // a voice whose total level falls below this (dBFS) has finished: about 75 dB under a loud note
constexpr int kLastDamper = 88;           // E6: keys above have no dampers, as on a grand
constexpr float kFloorDb = -105.f;        // components quieter than this (dBFS) are dropped
constexpr int kBudget = 4000;             // components sounding across all keys before the quietest are dropped early
constexpr float kRangeDb = 70.f;          // and partials this far below a note's loudest one aren't started
constexpr float kTwoPi = 6.28318530718f;
constexpr float kTrimDb = -10.f;          // headroom: the recordings peak near 0 dBFS on one loud note

constexpr float kReleaseDb = -37.f;       // the release and pedal recordings' level against the notes
constexpr float kPedalDownDb = -20.f;     // (the Salamander sfz's own mix: the release takes are normalised)
constexpr float kPedalUpDb = -19.f;
constexpr float kReleaseVelTrack = 0.82f; // how much the release noise follows the strike's velocity, as in the sfz
constexpr int kBankMaxPartial = 16;       // the string bank: each string's partials up to the 16th…
constexpr float kBankMaxHz = 4000.f;      // …and 4 kHz
constexpr float kCoupling = 0.015f;       // how strongly sound at a free string's partial drives it (1/s)

inline float dbToGain(float db) { return std::pow(10.f, db * 0.05f); }

// Where a key sits between the measured keys.
struct KeySpan {
  int a = 0, b = 0;
  float w = 0;   // 0 = all a, 1 = all b
};
KeySpan spanFor(int note) {
  KeySpan s;
  if (note <= pd::keys[0].note) return s;
  if (note >= pd::keys[pd::kKeys - 1].note) {
    s.a = s.b = pd::kKeys - 1;
    return s;
  }
  for (int i = 0; i + 1 < pd::kKeys; ++i)
    if (note < pd::keys[i + 1].note) {
      s.a = i;
      s.b = i + 1;
      s.w = static_cast<float>(note - pd::keys[i].note) / static_cast<float>(pd::keys[i + 1].note - pd::keys[i].note);
      return s;
    }
  return s;
}

// The recording's stretch tuning at a key, in cents (A4 = 0).
float measuredCents(int note) {
  KeySpan s = spanFor(note);
  return pd::keys[s.a].cents + (pd::keys[s.b].cents - pd::keys[s.a].cents) * s.w;
}
}  // namespace


// The damper on a key: how fast it stops the strings (dB/s); keys above E6 have none.
inline float damperRate(int note) {
  if (note > kLastDamper) return 0.f;
  float t = std::clamp((note - 21) / 67.f, 0.f, 1.f);
  return 70.f * std::pow(300.f / 70.f, t);   // A0 ≈ 70 (about 0.9 s to silence), E6 ≈ 300
}

struct Piano;

struct PianoVoice : Voice {
  explicit PianoVoice(Piano& s) : synth(s) {}
  void prepare(const PrepareInfo& info) override { sr = static_cast<float>(info.sampleRate); }
  void start(const NoteOn& n, const StartInfo& s) override;
  void strike(VoiceContext& ctx, float vel);
  void release() override;
  void render(VoiceContext& ctx, Buffer& out) override;
  bool active() const override { return pending || count > 0 || knocking; }

  // adds a component; with onset > 0 (dB/s) also a short-lived negative twin, so the
  // component swells in as 1 − e^{−t/τ} over the hammer's contact instead of starting at full level
  void add(float amp, float decayDb, float ratio, float offsetHz, float phase, float damp, float wl, float wr,
           float stereoPhase, float onset);
  void addOne(float amp, float decayDb, float ratio, float offsetHz, float phase, float damp, float wl, float wr,
              float stereoPhase, float extraDb);
  void updatePoles(float pitchHz, float sustainScale, float unisonScale, float damperScale);
  void cull();

  Piano& synth;
  float sr = 48000;
  int note = -1;
  bool pending = false;
  float pendingVel = 0;
  int count = 0;      // components in use; arrays are zero-padded to a multiple of kLanes
  bool damped = false;

  // components (struct of arrays, so the inner loop vectorises)
  alignas(32) float re[kCapacity], im[kCapacity];   // state: amplitude and phase
  alignas(32) float pr[kCapacity], pi[kCapacity];   // pole: radius · e^{jω}
  alignas(32) float wl[kCapacity], wrr[kCapacity], wri[kCapacity];  // to left / right
  float ratio[kCapacity];     // frequency / the key's pitch
  float offset[kCapacity];    // beat offset (Hz), scaled by the unison setting
  float decay[kCapacity];     // natural decay (dB/s, negative), scaled by Sustain
  float extra[kCapacity];     // extra decay not scaled by Sustain (dB/s, positive): the onset twins
  float damp[kCapacity];      // extra decay with the damper down (dB/s, positive)

  // pole inputs last used, to recompute only on change
  float lastPitch = -1, lastSustain = -1, lastUnison = -1, lastDamper = -1;
  float dampBaseNow = 0;   // this key's damper rate (dB/s), for its body ring at release
  bool lastDamped = false;

  // the strike's noise: octave bands of noise, per mic, each fading at its measured rate
  bool knocking = false;
  float nzEnv[2][pd::kNoiseBands] = {}, nzMul[pd::kNoiseBands] = {};
  float nzS[2][pd::kNoiseBands][4] = {};
  float nzRise = 0, nzRiseMul = 0;   // the noise swells in with the partials: × (1 − nzRise)   // two filter stages per band, two states each
};

struct Piano : Synth {
  Param touch, dynamics, toneRange, softAttack, velMap, bright, attack, hammer, body, mech, unison, stretch, sustain, damper,
      resonance, width, room;

  void defineParams(ParamBuilder& b) override {
    touch = b.add("play.touch").name("Touch").range(-1, 1).def(0).group("Playing");
    dynamics = b.add("play.dynamics").name("Dynamics").range(25, 150).def(100).unit("%").group("Playing");
    toneRange = b.add("play.tone").name("Tone Range").range(0, 150).def(100).unit("%").group("Playing");
    velMap = b.add("play.velmap").name("Velocity Map").choices({"Smooth", "As Recorded"}).group("Playing");
    softAttack = b.add("play.softattack").name("Soft Attack").range(0, 100).def(25).unit("%").group("Playing");
    bright = b.add("tone.bright").name("Brightness").range(-6, 6).def(0).unit("dB").group("Tone");
    attack = b.add("tone.attack").name("Attack").range(25, 400).log().def(100).unit("%").group("Tone");
    body = b.add("tone.body").name("Body").range(-40, 12).def(0).unit("dB").group("Tone");
    hammer = b.add("tone.hammer").name("Hammer").range(-40, 12).def(0).unit("dB").group("Tone");
    mech = b.add("tone.mech").name("Key & Pedal").range(-40, 12).def(0).unit("dB").group("Tone");
    unison = b.add("string.unison").name("Unison").range(0, 300).def(100).unit("%").group("Strings");
    stretch = b.add("string.stretch").name("Stretch").range(0, 200).def(100).unit("%").group("Strings");
    sustain = b.add("string.sustain").name("Sustain").range(25, 400).log().def(100).unit("%").group("Strings");
    damper = b.add("string.damper").name("Dampers").range(25, 400).log().def(100).unit("%").group("Strings");
    resonance = b.add("string.resonance").name("Resonance").range(-40, 12).def(0).unit("dB").group("Strings");
    width = b.add("space.width").name("Width").range(0, 150).def(100).unit("%").group("Space");
    room = b.add("space.room").name("Room").range(0, 100).def(12).unit("%").group("Space");
  }

  std::unique_ptr<Voice> makeVoice() override { return std::make_unique<PianoVoice>(*this); }

  // The recording's stretch tuning, scaled by Stretch.
  double tune(int note, const NoteContext& n) override {
    return note + n.get(stretch) * 0.01 * measuredCents(note) * 0.01;
  }

  // Velocity → loudness and tone, smoothly, across the key's measured range (its softest
  // recording to its loudest), whatever the recorded layers' own velocity spacing. The
  // tone comes from the recorded layer with that loudness, and Tone Range moves it towards the
  // loudest layer (< 100 %) or further down (> 100 %). Dynamics scales the loudness range alone.
  struct VelMap {
    float layer;    // 0-based layer position for tone and decay
    float gainDb;   // level change to apply to that layer's measurements
    float relDb;    // loudness against the hardest strike (for the page's graph)
    float v;        // velocity after Touch, 1…127
  };
  static constexpr float kBelowSoftest = 6.f;
  static constexpr float kVelCurve = 1.5f;   // a feather-light strike: this far under the softest recording
  VelMap velocityMap(int note, float vel01, float touchV, float dynPct, float tonePct, int mapMode = 0) const {
    KeySpan s = spanFor(note);
    const pd::Key& ka = pd::keys[s.a];
    const pd::Key& kb = pd::keys[s.b];
    float loud[pd::kLayers], mono[pd::kLayers];
    for (int i = 0; i < pd::kLayers; ++i) {
      loud[i] = ka.loud[i] + (kb.loud[i] - ka.loud[i]) * s.w;
      mono[i] = i == 0 ? loud[0] : std::max(mono[i - 1], loud[i]);   // made monotonic to invert
    }
    VelMap m;
    m.v = 1.f + 126.f * std::pow(std::clamp(vel01, 0.f, 1.f), std::exp2(touchV));
    float top = mono[pd::kLayers - 1], bottom = mono[0] - kBelowSoftest;
    if (mapMode == 1) {
      // As Recorded: the sample library's own velocity ranges (the middle of each layer's range
      // plays that layer), for comparing with the recordings layer by layer
      float lpL = 0, below = 0;
      if (m.v <= pd::kLayerVel[0]) {
        below = 6.f * std::log2(std::max(m.v, 1.f) / pd::kLayerVel[0]);
      } else if (m.v >= pd::kLayerVel[pd::kLayers - 1]) {
        lpL = pd::kLayers - 1;
      } else {
        int i = 0;
        while (i + 2 < pd::kLayers && m.v >= pd::kLayerVel[i + 1]) ++i;
        lpL = i + (m.v - pd::kLayerVel[i]) / (pd::kLayerVel[i + 1] - pd::kLayerVel[i]);
      }
      int a0 = std::min(static_cast<int>(lpL), pd::kLayers - 2);
      float target = loud[a0] + (loud[a0 + 1] - loud[a0]) * (lpL - a0) + below;
      return finish(m, loud, lpL, target, loud[pd::kLayers - 1], dynPct, tonePct);
    }
    // loudness falls slowly from the top and faster towards the softest touch, (1 − u)^1.5:
    // half velocity is about a third of the range down
    float target = top - (top - bottom) * std::pow(1.f - (m.v - 1.f) / 126.f, kVelCurve);
    float lpL = 0;
    if (target > mono[0]) {
      int i = 0;
      while (i + 2 < pd::kLayers && target > mono[i + 1]) ++i;
      float d = mono[i + 1] - mono[i];
      lpL = std::min(i + (d > 1e-6f ? (target - mono[i]) / d : 1.f), static_cast<float>(pd::kLayers - 1));
    }
    return finish(m, loud, lpL, target, top, dynPct, tonePct);
  }
  // Tone Range moves the tone layer; Dynamics scales the loudness's distance from the top; the
  // level change makes up the difference between the tone layer's own loudness and the target.
  static VelMap finish(VelMap m, const float* loud, float lpL, float target, float top, float dynPct, float tonePct) {
    float last = static_cast<float>(pd::kLayers - 1);
    m.layer = std::clamp(last - (last - lpL) * tonePct * 0.01f, 0.f, last);
    int l0 = std::min(static_cast<int>(m.layer), pd::kLayers - 2);
    float lf = m.layer - l0;
    float loudAtTone = loud[l0] + (loud[l0 + 1] - loud[l0]) * lf;
    float finalDb = top + dynPct * 0.01f * (target - top);
    m.gainDb = finalDb - loudAtTone;
    m.relDb = finalDb - top;
    return m;
  }

  // The page's velocity graph: it asks for a key's curve; each strike reports its point.
  MessageId velQuery, velPoint, velNote;
  void defineMessages(MessageBuilder& b) override {
    velQuery = b.add("vel.query");   // page → engine: [key]
    velPoint = b.add("vel.point");   // engine → page: [index, velocity, dB, tone 0-1]
    velNote = b.add("vel.note");     // engine → page: [key, velocity, dB, tone 0-1] for each strike
  }
  void reportCurve(EventContext& ctx, int key) {
    constexpr int kPoints = 32;
    for (int i = 0; i < kPoints; ++i) {
      float v01 = static_cast<float>(i) / (kPoints - 1);
      VelMap m = velocityMap(key, std::max(v01, 1.f / 127.f), ctx.get(touch), ctx.get(dynamics), ctx.get(toneRange),
                             ctx.choice(velMap));
      ctx.post(velPoint, {static_cast<float>(i), v01 * 127.f, m.relDb, m.layer / (pd::kLayers - 1)});
    }
  }

  // One voice per key: a key that is still sounding is struck again in place.
  int allocateVoice(const NoteOn& n, VoicePool& pool) override {
    for (int i = 0; i < pool.size(); ++i)
      if (pool.note(i) == n.note && pool.state(i) != VoicePool::State::Free &&
          pool.state(i) != VoicePool::State::Fading)
        return i;
    int f = pool.findFree();
    return f >= 0 ? f : pool.defaultAllocate(n);
  }

  // Load guard: voices add up their components each block; when the total
  // passes kBudget, the floor below which components are dropped rises.
  int compsNow = 0;
  float floorDb = kFloorDb;

  // Band-pass filters for the strike's noise: two identical RBJ band-pass
  // stages per octave band (4th order, so loud bands don't spill into quiet
  // ones), each scaled so white noise through it comes out at unit RMS.
  // tools/make_header.py solves the band gains against these same filters.
  struct Band { float b0 = 0, a1 = 0, a2 = 0, norm = 0; };
  Band bands[pd::kNoiseBands];

  // A small room: decorrelated, darkening noise tails, one per side.
  wa::Convolver roomConv;
  float sr = 48000;

  void prepare(const PrepareInfo& info) override {
    sr = static_cast<float>(info.sampleRate);
    int len = static_cast<int>(1.6 * info.sampleRate);
    std::vector<float> l(static_cast<std::size_t>(len)), r(static_cast<std::size_t>(len));
    dsp::Rng rng;
    rng.seed(0x9e3779b9u);
    float lpL = 0, lpR = 0;
    for (int i = 0; i < len; ++i) {
      float t = static_cast<float>(i) / sr;
      float env = std::exp(-t / 0.32f) * (1 - std::exp(-t / 0.004f));
      float c = std::exp(-kTwoPi * (9000.f * std::exp(-t / 0.5f) + 600.f) / sr);   // darker as it fades
      lpL = lpL * c + (1 - c) * rng.bipolar();
      lpR = lpR * c + (1 - c) * rng.bipolar();
      l[static_cast<std::size_t>(i)] = lpL * env;
      r[static_cast<std::size_t>(i)] = lpR * env;
    }
    const float* ch[2] = {l.data(), r.data()};
    roomConv.setImpulse(ch, 2, len, info.sampleRate, true);
    buildBank();

    for (int b = 0; b < pd::kNoiseBands; ++b) {
      Band& bd = bands[b];
      float fc = pd::kNoiseBandLow[b] * 1.41421356f;
      if (fc * 1.41421356f > 0.49f * sr) { bd = Band{}; continue; }
      float w0 = kTwoPi * fc / sr, alpha = std::sin(w0) / (2 * pd::kNoiseQ), a0 = 1 + alpha;
      bd.b0 = alpha / a0;
      bd.a1 = -2 * std::cos(w0) / a0;
      bd.a2 = (1 - alpha) / a0;
      float st[4] = {};
      double var = 0;
      int n = static_cast<int>(sr);
      for (int i = 0; i < n + 2000; ++i) {
        float y = bandStep(bd, rng.bipolar(), st);
        if (i >= 2000) var += double(y) * y;
      }
      bd.norm = static_cast<float>(1.0 / std::sqrt(var / n));
    }
  }

  // one sample of a band: two band-pass stages (transposed direct form II; b1 = 0, b2 = −b0)
  static float bandStep(const Band& b, float x, float* st) {
    for (int k = 0; k < 2; ++k) {
      float y = b.b0 * x + st[2 * k];
      st[2 * k] = -b.a1 * y + st[2 * k + 1];
      st[2 * k + 1] = -b.b0 * x - b.a2 * y;
      x = y;
    }
    return x;
  }

  // The body's ring: after each strike the soundboard, case and room ring on, as diffuse
  // sound between the strings' partials (tools/analyze_body.py). Kept per key here rather than
  // in the voice, so it rings on after a short note's voice has finished. One shared bank of
  // octave-band noise plays it: the keys' powers add per band, as independent noises do.
  static constexpr float kBodyRiseS = 0.03f;   // it builds up over the first tens of ms
  float bodyPow[128][2][pd::kNoiseBands] = {};
  float bodyDec[128][pd::kNoiseBands] = {};     // dB/s
  float bodyRise[128] = {};                     // × (1 − bodyRise)², bodyRise falling from 1
  float bodyDamp[128] = {};                     // the key's damper is down: its rate (dB/s), else 0
  bool bodyOn[128] = {};
  float bodyState[2][pd::kNoiseBands][4] = {};
  dsp::Rng bodyRng;

  void addBody(int key, const float (&powL)[pd::kNoiseBands], const float (&powR)[pd::kNoiseBands],
               const float (&dec)[pd::kNoiseBands]) {
    key = std::clamp(key, 0, 127);
    for (int b = 0; b < pd::kNoiseBands; ++b) {
      bodyPow[key][0][b] += powL[b];
      bodyPow[key][1][b] += powR[b];
      // a strike whose key was already let go (note-on and note-off in one block, the strike
      // coming at the next render) rings damped from the start
      bodyDec[key][b] = bodyDamp[key] > 0 ? std::min(dec[b], -bodyDamp[key]) : dec[b];
    }
    bodyRise[key] = 1;
    bodyOn[key] = true;
  }
  // the key's damper falls: its ring fades as its strings do (keys without dampers ring on)
  void dampBody(int key, float dampDbPerS) {
    key = std::clamp(key, 0, 127);
    bodyDamp[key] = std::max(dampDbPerS, 0.f);
    if (dampDbPerS <= 0) return;
    for (int b = 0; b < pd::kNoiseBands; ++b) bodyDec[key][b] = std::min(bodyDec[key][b], -dampDbPerS);
  }
  void clearBody() {
    for (int k = 0; k < 128; ++k) {
      bodyOn[k] = false;
      for (auto& ch : bodyPow[k]) for (float& v : ch) v = 0;
    }
    for (auto& ch : bodyState) for (auto& b : ch) for (float& v : b) v = 0;
  }


  // ---- mechanical noises: a key coming up, the pedal going down and up (tools/analyze_mech.py).
  // Each is a recorded envelope over octave bands of noise; they play through the body's band bank,
  // their powers adding to its, so they cost almost nothing.
  struct Mech {
    const signed char* env = nullptr;
    int frames = 0;
    float pos = 0;        // samples since it started (negative: still to come in this block)
    float pw[2][pd::kNoiseBands] = {};
    bool on = false;
  };
  static constexpr int kMechSlots = 16;
  Mech mechs[kMechSlots];
  float noteVel[128] = {};
  int pedalTake = 0;

  void startMech(const signed char* env, int frames, const short (&lv)[2][pd::kNoiseBands], float gainDb, int offset) {
    int slot = 0;
    for (int i = 0; i < kMechSlots; ++i) {
      if (!mechs[i].on) { slot = i; break; }
      if (mechs[i].pos > mechs[slot].pos) slot = i;   // all busy: replace the oldest
    }
    Mech& m = mechs[slot];
    m.env = env;
    m.frames = frames;
    m.pos = -static_cast<float>(offset);
    for (int c = 0; c < 2; ++c)
      for (int b = 0; b < pd::kNoiseBands; ++b)
        m.pw[c][b] = lv[c][b] > -14000 ? std::pow(10.f, (0.01f * lv[c][b] + gainDb) * 0.1f) : 0.f;
    m.on = true;
  }
  // a mechanical noise's power at a moment (relative to its peak)
  float mechEnvAt(const Mech& m, float pos) const {
    if (pos <= 0) return 0;
    float f = pos / (pd::kMechFrameS * sr);
    int i = static_cast<int>(f);
    if (i >= m.frames || m.env[i] == -128) return 0;
    float a = m.env[i];
    float b = (i + 1 < m.frames && m.env[i + 1] != -128) ? m.env[i + 1] : a - 20.f;   // the last frame fades out
    return std::pow(10.f, (a + (b - a) * (f - i)) * 0.1f);
  }

  // ---- the strings' sympathetic ring. Every string's lower partials (to the 16th, below 4 kHz) are
  // a bank of resonators at the same frequencies the voices play, with the strings' measured slow
  // decay. A string whose damper is up (the pedal is down, its key is held, or it has no damper)
  // and which isn't sounding as a voice of its own is driven by everything the piano plays, so it
  // picks up the partials near its own: an octave below sets the upper string's fundamental going,
  // a fifth sets the twelfth's, and so on. Lifting the dampers with the pedal sets the free strings
  // ringing too (pedalRing). Damped strings fade at the damper's rate.
  static constexpr int kBankLanes = 4;
  static constexpr int kBankCap = 88 * kBankMaxPartial;
  // The bank runs at half the sample rate (its partials are all below 4 kHz): the input is averaged
  // over pairs of samples and the output interpolated back, two samples late.
  float dAcc = 0, dPrevL = 0, dPrevR = 0, dCurL = 0, dCurR = 0;
  int dPhase = 0;
  float dIn[kControlBlock / 2 + 1] = {}, dOutL[kControlBlock / 2 + 1] = {}, dOutR[kControlBlock / 2 + 1] = {};
  alignas(32) float bRe[kBankCap] = {}, bIm[kBankCap] = {}, bPr[kBankCap] = {}, bPi[kBankCap] = {};
  alignas(32) float bWl[kBankCap] = {}, bWr[kBankCap] = {}, bWi[kBankCap] = {}, bIn[kBankCap] = {};
  float bRatio[kBankCap] = {}, bDecay[kBankCap] = {}, bDampMul[kBankCap] = {};
  signed char bBand[kBankCap] = {};
  int keyFirst[128] = {}, keyLen[128] = {};      // lanes per key (a multiple of kBankLanes; the padding has bIn = 0)
  bool keyLive[128] = {}, keyPoles[128] = {}, keyDamped[128] = {};
  float liftAmp[pd::kNoiseBands] = {};           // the pedal's lift: amplitude given to each partial, per band
  bool sounding[128] = {};                       // set by voices with partials sounding, each block
  bool liftPending = false;
  float a4Hz = 440.f;
  dsp::Rng liftRng;   // its own stream, so the lift's phases don't depend on the block size
  // The bank follows keys and pedal at their own sample in the host's block (processEvents sees the
  // whole host block at once; processGlobal runs per control block): changes wait here till then.
  struct Change { int at; int what; int key; };   // what: 0 key down, 1 key up, 2 pedal down, 3 pedal up
  static constexpr int kMaxChanges = 512;
  Change changes[kMaxChanges];
  int nChanges = 0, hostPos = 0;
  bool bankKeys[128] = {};
  bool bankPedal = false;
  void applyChange(const Change& c) {
    if (c.what == 0) bankKeys[c.key] = true;
    else if (c.what == 1) bankKeys[c.key] = false;
    else if (c.what == 2) { liftPending = !bankPedal; bankPedal = true; }
    else bankPedal = false;
  }
  void queueChange(int at, int what, int key) {
    Change c{at, what, key};
    if (nChanges < kMaxChanges) changes[nChanges++] = c;
    else applyChange(c);
  }
  float lastStretch = -1, lastSus = -1, lastDampScale = -1, lastA4 = -1;

  // string partial frequencies and decays, as the voices' strikes compute them (medium strike)
  void buildBank() {
    int at = 0;
    for (int key = 21; key <= 108; ++key) {
      keyFirst[key] = at;
      KeySpan s = spanFor(key);
      const pd::Key& ka = pd::keys[s.a];
      const pd::Key& kb = pd::keys[s.b];
      float B = std::exp(std::log(ka.B) + (std::log(kb.B) - std::log(ka.B)) * s.w);
      float f0 = 440.f * std::exp2((key + measuredCents(key) * 0.01f - 69.f) / 12.f);
      int ia = ka.first, ea = ka.first + ka.count, ib = kb.first, eb = kb.first + kb.count;
      constexpr int kLayer = 9;
      for (int pn = 1; pn <= kBankMaxPartial; ++pn) {
        float rat = pn * std::sqrt((1 + B * pn * pn) / (1 + B));
        if (f0 * rat > kBankMaxHz) break;
        while (ia < ea && pd::partials[ia].n < pn) ++ia;
        while (ib < eb && pd::partials[ib].n < pn) ++ib;
        bool hasA = ia < ea && pd::partials[ia].n == pn, hasB = ib < eb && pd::partials[ib].n == pn;
        if (!hasA && !hasB) continue;
        float wa = hasA && hasB ? 1 - s.w : (hasA ? 1.f : 0.f), wb = 1 - wa;
        int pa = hasA ? ia : ib, pb = hasB ? ib : ia;
        float s2 = wa * pd::partials[pa].s2[1] + wb * pd::partials[pb].s2[1];
        float lvl[2];
        for (int mic = 0; mic < 2; ++mic)
          lvl[mic] = 0.01f * (wa * pd::levels[pa][kLayer][mic] + wb * pd::levels[pb][kLayer][mic]);
        float gl = dbToGain(lvl[0]), gr = dbToGain(lvl[1]);
        float norm = std::sqrt(0.5f * (gl * gl + gr * gr));
        int pp = hasA && hasB ? (s.w < 0.5f ? pa : pb) : pa;
        constexpr float kPhaseStep = 3.14159265f / 127.f;
        float st = (pd::phases[pp][kLayer][1] - pd::phases[pp][kLayer][0]) * kPhaseStep;
        bRatio[at] = rat;
        bDecay[at] = std::min(s2, -0.5f);
        bDampMul[at] = 1 + 0.03f * (pn - 1);
        bWl[at] = gl / norm;
        bWr[at] = gr / norm * std::cos(st);
        bWi[at] = -gr / norm * std::sin(st);
        bIn[at] = 1;
        int band = static_cast<int>(std::floor(std::log2(f0 * rat / pd::kNoiseBandLow[0])));
        bBand[at] = static_cast<signed char>(std::clamp(band, 0, pd::kNoiseBands - 1));
        ++at;
      }
      while ((at - keyFirst[key]) % kBankLanes) bIn[at++] = 0;   // padding lanes stay silent
      keyLen[key] = at - keyFirst[key];
    }
    // the lift: each band's free strings together ring at the measured level 0.3-1.0 s after it
    double pw[pd::kNoiseBands] = {};
    for (int key = 21; key <= kLastDamper; ++key)
      for (int i = keyFirst[key]; i < keyFirst[key] + keyLen[key]; ++i) {
        if (bIn[i] == 0) continue;
        double m = 0;
        for (int k = 0; k < 8; ++k) m += std::pow(10.0, bDecay[i] * (0.3 + 0.1 * k) / 10.0) / 8;
        pw[bBand[i]] += 0.5 * m;   // a unit partial's power, averaged over the two mics
      }
    for (int b = 0; b < pd::kNoiseBands; ++b) {
      double target = 0.5 * (std::pow(10.0, pd::pedalRing[0][b] * 0.001) + std::pow(10.0, pd::pedalRing[1][b] * 0.001)) *
                      std::pow(10.0, (kPedalDownDb + kTrimDb) / 10.0);
      liftAmp[b] = pw[b] > 0 && pd::kNoiseBandLow[b] < kBankMaxHz ? static_cast<float>(std::sqrt(target / pw[b])) : 0.f;
    }
  }

  void bankPoles(int key, float stretchPct, float sus, float dampScale) {
    float f0 = a4Hz * std::exp2((key + stretchPct * 0.01f * measuredCents(key) * 0.01f - 69.f) / 12.f);
    float dmp = keyDamped[key] ? damperRate(key) / dampScale : 0.f;
    float rate = 0.5f * sr, nyq = 0.47f * rate;
    for (int i = keyFirst[key]; i < keyFirst[key] + keyLen[key]; ++i) {
      float f = f0 * bRatio[i];
      float db = bDecay[i] / sus - dmp * bDampMul[i];
      float rad = bIn[i] != 0 && f < nyq ? std::pow(10.f, db * 0.05f / rate) : 0.f;
      bPr[i] = rad * std::cos(kTwoPi * f / rate);
      bPi[i] = rad * std::sin(kTwoPi * f / rate);
    }
    keyPoles[key] = true;
  }

  void clearBank() {
    for (int i = 0; i < kBankCap; ++i) bRe[i] = bIm[i] = 0;
    for (bool& l : keyLive) l = false;
    dAcc = dPrevL = dPrevR = dCurL = dCurR = 0;
    dPhase = 0;
  }

  void renderBank(GlobalContext& ctx, Buffer& io) {
    const int n = io.frames;
    float stretchPct = ctx.get(stretch), sus = ctx.get(sustain) * 0.01f;
    float dampScale = std::max(ctx.get(damper) * 0.01f, 0.25f);
    bool changed = stretchPct != lastStretch || sus != lastSus || dampScale != lastDampScale || a4Hz != lastA4;
    if (changed) {
      lastStretch = stretchPct; lastSus = sus; lastDampScale = dampScale; lastA4 = a4Hz;
      for (bool& p : keyPoles) p = false;
    }
    float drive = kCoupling * dbToGain(ctx.get(resonance)) * 2.f / sr;   // per half-rate step
    int applied = 0;
    while (applied < nChanges && changes[applied].at < hostPos + n) applyChange(changes[applied++]);
    if (applied > 0) {
      for (int i = applied; i < nChanges; ++i) changes[i - applied] = changes[i];
      nChanges -= applied;
    }
    hostPos += n;
    float lift = liftPending ? dbToGain(ctx.get(mech)) : 0.f;
    // the input, averaged over pairs of samples
    int steps = 0, ph = dPhase;
    float inPow = 0;
    for (int i = 0; i < n; ++i) {
      float x = 0.5f * (io.left[i] + io.right[i]);
      inPow += x * x;
      dAcc += x;
      if (++ph == 2) {
        dIn[steps] = 0.5f * dAcc;
        dOutL[steps] = dOutR[steps] = 0;
        ++steps;
        dAcc = 0;
        ph = 0;
      }
    }
    dsp::Rng& rng = liftRng;
    for (int key = 21; key <= 108; ++key) {
      bool free = bankPedal || bankKeys[key] || key > kLastDamper;
      bool driven = free && !sounding[key];
      if (keyDamped[key] != !free) {
        keyDamped[key] = !free;
        keyPoles[key] = false;
      }
      if (lift > 0 && key <= kLastDamper && !bankKeys[key] && !sounding[key]) {
        for (int i = keyFirst[key]; i < keyFirst[key] + keyLen[key]; ++i) {
          if (bIn[i] == 0) continue;
          float a = liftAmp[bBand[i]] * lift, phase = rng.uniform(0.f, kTwoPi);
          bRe[i] += a * std::cos(phase);
          bIm[i] += a * std::sin(phase);
        }
        keyLive[key] = true;
      }
      // nothing to drive it (under −120 dBFS) and nothing left ringing: skip it
      if (!keyLive[key] && (!driven || inPow < 1e-12f * static_cast<float>(n))) continue;
      if (!keyPoles[key]) bankPoles(key, stretchPct, sus, dampScale);
      float c = driven ? drive : 0.f;
      float energy = 0;
      for (int ch = keyFirst[key]; ch < keyFirst[key] + keyLen[key]; ch += kBankLanes) {
        constexpr int L = kBankLanes;
        float r[L], m[L], a[L], b[L], gl[L], gr[L], gi[L], cin[L];
        for (int k = 0; k < L; ++k) {
          r[k] = bRe[ch + k]; m[k] = bIm[ch + k]; a[k] = bPr[ch + k]; b[k] = bPi[ch + k];
          gl[k] = bWl[ch + k]; gr[k] = bWr[ch + k]; gi[k] = bWi[ch + k]; cin[k] = bIn[ch + k] * c;
        }
        for (int i = 0; i < steps; ++i) {
          float x = dIn[i];
          float ol[L], orr[L];
          for (int k = 0; k < L; ++k) {
            float nr = r[k] * a[k] - m[k] * b[k] + cin[k] * x;
            float ni = r[k] * b[k] + m[k] * a[k];
            r[k] = nr;
            m[k] = ni;
            ol[k] = nr * gl[k];
            orr[k] = nr * gr[k] + ni * gi[k];
          }
          dOutL[i] += (ol[0] + ol[1]) + (ol[2] + ol[3]);
          dOutR[i] += (orr[0] + orr[1]) + (orr[2] + orr[3]);
        }
        for (int k = 0; k < L; ++k) {
          bRe[ch + k] = r[k];
          bIm[ch + k] = m[k];
          energy += r[k] * r[k] + m[k] * m[k];
        }
      }
      keyLive[key] = energy > 1e-10f;   // −100 dBFS
      if (!keyLive[key] && !driven)
        for (int i = keyFirst[key]; i < keyFirst[key] + keyLen[key]; ++i) bRe[i] = bIm[i] = 0;
    }
    // back to the full rate: linear between the half-rate outputs, two samples late
    int k = 0;
    for (int i = 0; i < n; ++i) {
      float l, r;
      if (++dPhase == 2) {
        dPhase = 0;
        dPrevL = dCurL; dPrevR = dCurR;
        dCurL = dOutL[k]; dCurR = dOutR[k];
        ++k;
        l = dPrevL; r = dPrevR;
      } else {
        l = 0.5f * (dPrevL + dCurL); r = 0.5f * (dPrevR + dCurR);
      }
      io.left[i] += l;
      io.right[i] += r;
    }
    liftPending = false;
    for (bool& v : sounding) v = false;
  }

  // Which keys are down and whether the pedal is, followed here so a key's ring is damped when
  // its damper falls, whether or not its voice is still sounding (a very quiet note's voice can
  // finish while the key is held, and then its note-off has no voice to release).
  int keysDown[128] = {};
  bool pedalDown = false;
  void damperFalls(EventContext& ctx, int key) {
    dampBody(key, damperRate(key) / std::max(ctx.get(damper) * 0.01f, 0.25f));
  }

  void processEvents(EventContext& ctx) override {
    for (int i = 0; i < nChanges; ++i) applyChange(changes[i]);   // (none left over normally)
    nChanges = 0;
    hostPos = 0;
    for (auto& msg : ctx.messages())
      if (msg.is(velQuery)) reportCurve(ctx, static_cast<int>(msg.value()));
    for (auto& e : ctx.in()) {
      int offset = std::max(e.sample, 0);   // within the host's block: the noises and the bank follow it
      float mechDb = ctx.get(mech) + kTrimDb;
      if (e.isCC(120) || e.isCC(123)) {   // all sound off / all notes off (the kit's buttons too)
        for (int k = 0; k < 128; ++k) if (keysDown[k]) queueChange(offset, 1, k);
        for (int& k : keysDown) k = 0;
        if (e.isCC(120)) {
          clearBody();
          clearBank();
          for (Mech& m : mechs) m.on = false;
        } else {
          for (int k = 0; k < 128; ++k) damperFalls(ctx, k);
        }
      }
      if (e.isCC(64)) {
        bool down = e.value >= 0.5f;     // as the kit reads the pedal
        if (pedalDown && !down) {
          for (int k = 0; k < 128; ++k) if (keysDown[k] == 0) damperFalls(ctx, k);
          startMech(pd::pedalUpEnv[pedalTake & 1], pd::kPedalUpFrames, pd::pedalUpLevels[pedalTake & 1],
                    kPedalUpDb + mechDb, offset);
          ++pedalTake;
        } else if (!pedalDown && down) {
          startMech(pd::pedalDownEnv[pedalTake & 1], pd::kPedalDownFrames, pd::pedalDownLevels[pedalTake & 1],
                    kPedalDownDb + mechDb, offset);
        }
        if (down != pedalDown) queueChange(offset, down ? 2 : 3, 0);
        pedalDown = down;
      }
      int key = e.note & 127;
      if (e.isNoteOn() && e.vel > 0) {
        keysDown[key] = 1;
        bodyDamp[key] = 0;   // the damper lifts
        queueChange(offset, 0, key);
        noteVel[key] = e.vel;
      }
      else if (e.isNoteOff() || (e.isNoteOn() && e.vel <= 0)) {
        if (keysDown[key] && key >= 21 && key <= 108) {
          // the key coming up (and its damper landing), louder after a harder strike, as in the sfz
          float velDb = kReleaseVelTrack * 40.f * std::log10(std::max(noteVel[key], 0.01f));
          startMech(pd::releaseEnv[key - 21], pd::kReleaseFrames, pd::releaseLevels[key - 21],
                    kReleaseDb + velDb + mechDb, offset);
        }
        keysDown[key] = 0;
        queueChange(offset, 1, key);
        if (!pedalDown) damperFalls(ctx, key);
      }
      if (e.isNoteOn()) {
        VelMap m = velocityMap(e.note, e.vel, ctx.get(touch), ctx.get(dynamics), ctx.get(toneRange), ctx.choice(velMap));
        ctx.post(velNote, {static_cast<float>(e.note), e.vel * 127.f, m.relDb, m.layer / (pd::kLayers - 1)});
      }
      ctx.out(e);
    }
  }

  void renderBody(Buffer& io) {
    const int n = io.frames;
    float p0[2][pd::kNoiseBands] = {}, p1[2][pd::kNoiseBands] = {};
    float riseMul = std::exp(-static_cast<float>(n) / (kBodyRiseS * sr));
    bool any = false;
    for (int k = 0; k < 128; ++k) {
      if (!bodyOn[k]) continue;
      float r0 = 1 - bodyRise[k], r1 = 1 - bodyRise[k] * riseMul;
      bodyRise[k] *= riseMul;
      float peak = 0;
      for (int b = 0; b < pd::kNoiseBands; ++b) {
        float m = std::pow(10.f, bodyDec[k][b] * 0.1f * static_cast<float>(n) / sr);   // power decay over the block
        for (int c = 0; c < 2; ++c) {
          float& pw = bodyPow[k][c][b];
          p0[c][b] += pw * r0 * r0;
          pw *= m;
          p1[c][b] += pw * r1 * r1;
          peak = std::max(peak, pw);
        }
      }
      if (peak < 3e-10f) {   // −95 dB: done
        bodyOn[k] = false;
        for (auto& ch : bodyPow[k]) for (float& v : ch) v = 0;
      }
      any = true;
    }
    for (Mech& m : mechs) {
      if (!m.on) continue;
      float e0 = mechEnvAt(m, m.pos), e1 = mechEnvAt(m, m.pos + static_cast<float>(n));
      m.pos += static_cast<float>(n);
      for (int c = 0; c < 2; ++c)
        for (int b = 0; b < pd::kNoiseBands; ++b) {
          p0[c][b] += m.pw[c][b] * e0;
          p1[c][b] += m.pw[c][b] * e1;
        }
      if (m.pos > 0 && mechEnvAt(m, m.pos) == 0) m.on = false;
      any = true;
    }
    if (!any) return;
    for (int b = 0; b < pd::kNoiseBands; ++b) {
      const Band& bd = bands[b];
      if (bd.norm == 0) continue;
      float a0[2], a1[2];
      for (int c = 0; c < 2; ++c) {
        a0[c] = std::sqrt(p0[c][b]) * bd.norm;
        a1[c] = std::sqrt(p1[c][b]) * bd.norm;
      }
      if (a0[0] + a0[1] + a1[0] + a1[1] < 1e-9f) continue;
      for (int i = 0; i < n; ++i) {
        float u = static_cast<float>(i + 1) / static_cast<float>(n);
        io.left[i] += bandStep(bd, bodyRng.bipolar(), bodyState[0][b]) * (a0[0] + (a1[0] - a0[0]) * u);
        io.right[i] += bandStep(bd, bodyRng.bipolar(), bodyState[1][b]) * (a0[1] + (a1[1] - a0[1]) * u);
      }
    }
  }

  void processGlobal(GlobalContext& ctx, Buffer& io) override {
    renderBody(io);
    renderBank(ctx, io);
    float over = static_cast<float>(compsNow - kBudget) / kBudget;
    floorDb = kFloorDb + std::clamp(over * 40.f, 0.f, 40.f);
    compsNow = 0;
    Ramp w = ctx.ramp(width), mix = ctx.ramp(room);   // both in %
    for (int i = 0; i < io.frames; ++i) {
      float l = io.left[i], r = io.right[i];
      float m = 0.5f * (l + r), s = 0.5f * (l - r) * w.at(i) * 0.01f;
      l = m + s;
      r = m - s;
      float wl = l, wr = r;
      roomConv.process(wl, wr);
      float k = mix.at(i) * 0.01f;
      io.left[i] = l + k * wl;
      io.right[i] = r + k * wr;
    }
  }
};

// ---------------------------------------------------------------- voice

void PianoVoice::add(float amp, float decayDb, float rat, float offHz, float phase, float dmp, float gl, float gr,
                     float stereoPhase, float onset) {
  // a component and its onset twin go in together or not at all: either alone would click.
  // Below −80 dBFS the twin is left out: so quiet a component's start can't be heard as a click.
  if (std::fabs(amp) < 1e-4f) onset = 0;
  if (count + (onset > 0 ? 2 : 1) > kCapacity) return;
  addOne(amp, decayDb, rat, offHz, phase, dmp, gl, gr, stereoPhase, 0);
  if (onset > 0) addOne(-amp, decayDb, rat, offHz, phase, dmp, gl, gr, stereoPhase, onset);
}

void PianoVoice::addOne(float amp, float decayDb, float rat, float offHz, float phase, float dmp, float gl, float gr,
                        float stereoPhase, float extraDb) {
  int i = count++;
  re[i] = amp * std::cos(phase);
  im[i] = amp * std::sin(phase);
  wl[i] = gl;
  wrr[i] = gr * std::cos(stereoPhase);    // right = Re(z · e^{jφ}) · gain
  wri[i] = -gr * std::sin(stereoPhase);
  ratio[i] = rat;
  offset[i] = offHz;
  decay[i] = decayDb;
  extra[i] = extraDb;
  damp[i] = dmp;
  pr[i] = pi[i] = 0;
}

void PianoVoice::start(const NoteOn& n, const StartInfo&) {
  if (n.note != note) {   // a different key in this slot: start clean
    count = 0;
    knocking = false;
    for (auto& ch : nzEnv) for (float& e : ch) e = 0;
    note = n.note;
  }
  damped = false;
  // The strike needs parameter values, which a voice reads in render(): it
  // happens at the start of the next render, the same sample.
  pendingVel = n.vel;
  pending = true;
}

void PianoVoice::strike(VoiceContext& ctx, float vel) {
  lastPitch = -1;         // recompute every pole

  // velocity → tone layer and level (Piano::velocityMap)
  Piano::VelMap vm = synth.velocityMap(note, vel, ctx.get(synth.touch), ctx.get(synth.dynamics),
                                                ctx.get(synth.toneRange), ctx.choice(synth.velMap));
  float v = vm.v;
  float lp = vm.layer;
  int l0 = std::min(static_cast<int>(lp), pd::kLayers - 2);
  float lf = lp - l0;
  // the decay shape: between the soft, medium and loud measurements
  int gi = lp < pd::kShapeLayer[1] ? 0 : 1;
  float gw = std::clamp((lp - pd::kShapeLayer[gi]) / (pd::kShapeLayer[gi + 1] - pd::kShapeLayer[gi]), 0.f, 1.f);
  auto shape = [&](const float (&arr)[3]) { return arr[gi] + (arr[gi + 1] - arr[gi]) * gw; };

  KeySpan s = spanFor(note);
  const pd::Key& ka = pd::keys[s.a];
  const pd::Key& kb = pd::keys[s.b];
  float B = std::exp(std::log(ka.B) + (std::log(kb.B) - std::log(ka.B)) * s.w);

  auto layerLevel = [&](int pIdx, int mic) {
    const auto& L = pd::levels[pIdx];
    return 0.01f * (L[l0][mic] + (L[l0 + 1][mic] - L[l0][mic]) * lf);
  };
  float gainDb = vm.gainDb + kTrimDb;
  float bright = ctx.get(synth.bright);
  // keep a note's loudness when Brightness tilts it: compare the power with and without the tilt
  float brightComp = 0;
  if (std::fabs(bright) > 1e-3f) {
    double p0 = 0, p1 = 0;
    for (int i = ka.first; i < ka.first + ka.count; ++i) {
      float lv = 0.5f * (layerLevel(i, 0) + layerLevel(i, 1));
      double g = std::pow(10.0, lv / 10.0);
      p0 += g;
      p1 += g * std::pow(10.0, bright * std::log2(static_cast<float>(pd::partials[i].n)) / 10.0);
    }
    if (p0 > 0 && p1 > 0) brightComp = static_cast<float>(-10.0 * std::log10(p1 / p0));
  }

  // dampers: faster in the treble, slower and weaker in the bass
  float dampBase = damperRate(note);

  // the loudest partial at this velocity, for the range cut
  float loudest = -200.f;
  for (const pd::Key* k : {&ka, &kb})
    for (int i = k->first; i < k->first + k->count; ++i)
      loudest = std::max(loudest, std::max(layerLevel(i, 0), layerLevel(i, 1)));
  float cut = std::max(kFloorDb + 6, loudest + gainDb - kRangeDb);

  // How long each partial takes to swell in (the hammer's contact, the soundboard's response):
  // measured per key and layer (tools/analyze_onset.py), scaled by Attack.
  auto tauAt = [&](int k) {
    return pd::onsetMs[k][l0] + (pd::onsetMs[k][l0 + 1] - pd::onsetMs[k][l0]) * lf;
  };
  float tauMs = std::exp(std::log(tauAt(s.a)) + (std::log(tauAt(s.b)) - std::log(tauAt(s.a))) * s.w);
  // Soft Attack: softer strikes swell in more slowly (the felt stays on the string longer);
  // at 100 % a strike at half velocity takes twice as long as the hardest
  float soft = ctx.get(synth.softAttack) * 0.01f * 2.f * (1.f - (v - 1.f) / 126.f);
  float tau = std::max(tauMs * ctx.get(synth.attack) * 0.01f * std::exp2(soft), 0.05f) * 0.001f;
  float onset = 8.6858896f / tau;   // dB/s of the twin's extra decay: 1 − e^{−t/τ}
  // Struck again while still sounding: the old vibration hands over to the new one, fading as the
  // new strike swells in. Adding the new partials on top of the old ones would let them cancel
  // where their phases disagree, and a fast repeat could make no attack at all.
  // It fades three times as fast as the new strike swells, as the hammer's contact stops the
  // string, so the two barely overlap.
  for (int i = 0; i < count; ++i) extra[i] = std::max(extra[i], 3.f * onset);
  nzRise = 1;
  nzRiseMul = std::exp(-1.f / (tau * sr));
  int layerNear = static_cast<int>(std::lround(lp));

  dampBaseNow = dampBase;

  dsp::Rng& r = ctx.rng();
  int ia = ka.first, ea = ka.first + ka.count, ib = kb.first, eb = kb.first + kb.count;
  for (int pn = 1; pn <= kMaxPartials; ++pn) {
    while (ia < ea && pd::partials[ia].n < pn) ++ia;
    while (ib < eb && pd::partials[ib].n < pn) ++ib;
    bool hasA = ia < ea && pd::partials[ia].n == pn;
    bool hasB = ib < eb && pd::partials[ib].n == pn;
    if (!hasA && !hasB) continue;
    float wa = hasA && hasB ? 1 - s.w : (hasA ? 1.f : 0.f);
    float wb = 1 - wa;
    int pa = hasA ? ia : ib, pb = hasB ? ib : ia;
    const pd::Partial& A = pd::partials[pa];
    const pd::Partial& Bp = pd::partials[pb];
    // a partial measured on only one side fades out towards the other
    float missing = (hasA && hasB) ? 0.f : -24.f * (hasA ? s.w : 1 - s.w);

    float extra = gainDb + missing + brightComp + bright * std::log2(static_cast<float>(pn));
    float Ll = wa * layerLevel(pa, 0) + wb * layerLevel(pb, 0) + extra;
    float Lr = wa * layerLevel(pa, 1) + wb * layerLevel(pb, 1) + extra;
    if (std::max(Ll, Lr) < cut) continue;
    float s1 = wa * shape(A.s1) + wb * shape(Bp.s1);
    float s2 = wa * shape(A.s2) + wb * shape(Bp.s2);
    float drop = wa * shape(A.drop) + wb * shape(Bp.drop);
    float beat = wa * A.beat + wb * Bp.beat;
    float beatDb = wa * A.beatDb + wb * Bp.beatDb;
    // phases at the strike, measured per layer and mic, from the nearer measured key
    int pp = hasA && hasB ? (s.w < 0.5f ? pa : pb) : pa;
    constexpr float kPhaseStep = 3.14159265f / 127.f;
    float phL = pd::phases[pp][layerNear][0] * kPhaseStep;
    float stereoPhase = pd::phases[pp][layerNear][1] * kPhaseStep - phL;

    float gl = dbToGain(Ll), gr = dbToGain(Lr);
    float amp = std::sqrt(0.5f * (gl * gl + gr * gr));
    gl /= amp;
    gr /= amp;
    float rat = pn * std::sqrt((1 + B * pn * pn) / (1 + B));
    float dmp = dampBase * (1 + 0.03f * (pn - 1));

    float after = drop < -0.01f ? amp * dbToGain(drop) : amp;
    float prompt = amp - after;
    float q = 0;
    if (beat > 0.05f) {
      float rr = dbToGain(beatDb);
      q = (rr - 1) / (rr + 1);
    }
    // each partial's unison drifts on its own: a beat rate within ±25 % of the measured one
    float bt = q > 0.02f ? beat * r.uniform(0.75f, 1.25f) : 0.f;
    // The prompt, the first burst and the main aftersound are one mode decaying at different
    // rates: they share a frequency, so they don't beat against each other (which would dig a
    // hole at the knee, where prompt and aftersound are equally loud). Only the second
    // aftersound component, bt away, beats against them.
    float main = -0.5f * bt;
    // The unison's strings beat against each other from the strike on, not only in the aftersound:
    // each part of the partial (prompt, first burst, aftersound) has beating partners at q of its
    // level. Each mic hears its own partner, at its own rate near the measured one, so the two
    // channels beat independently, as with the recording's spaced pair; both start (nearly) in
    // phase with the partial, since the hammer strikes the unison's strings together, so a strike
    // is never cancelled in either channel.
    float btR = bt * r.uniform(0.8f, 1.25f);
    float phBL = r.uniform(-0.3f, 0.3f), phBR = r.uniform(-0.3f, 0.3f);
    auto withBeat = [&](float a, float dec) {
      add(a, dec, rat, main, phL, dmp, gl, gr, stereoPhase, onset);
      if (q > 0.02f && a * q > 3e-5f) {   // a beat under −90 dBFS can't be heard
        add(a * q, dec, rat, main + bt, phL + phBL, dmp, gl, 0.f, stereoPhase, onset);
        add(a * q, dec, rat, main + btR, phL + phBR, dmp, 0.f, gr, stereoPhase, onset);
      }
    };
    if (prompt > amp * 1e-3f) withBeat(prompt, s1);
    // the strike's first burst above the decay line, gone within a fraction of a second
    float x0 = wa * shape(A.x0) + wb * shape(Bp.x0), s0 = wa * shape(A.s0) + wb * shape(Bp.s0);
    if (x0 > -40.f) withBeat(amp * dbToGain(x0), s0);
    // the measured decay lines run through the beating envelope in dB, and the mean in dB of
    // |a + b·e^{jθ}| is the level of the larger component alone: so the main components carry the
    // measured levels and the partners the beat on top
    withBeat(after, s2);
  }
  for (int i = count; i < ((count + kLanes - 1) & ~(kLanes - 1)); ++i)
    re[i] = im[i] = pr[i] = pi[i] = wl[i] = wrr[i] = wri[i] = 0;

  // the strike's noise, measured per key, layer, mic and band
  float hg = ctx.get(synth.hammer) + gainDb;
  for (int bd = 0; bd < pd::kNoiseBands; ++bd) {
    if (synth.bands[bd].norm == 0) continue;
    for (int mic = 0; mic < 2; ++mic) {
      auto lvl = [&](int k) {
        const auto& L = pd::noiseLevels[k];
        return 0.01f * (L[l0][mic][bd] + (L[l0 + 1][mic][bd] - L[l0][mic][bd]) * lf);
      };
      float db = lvl(s.a) + (lvl(s.b) - lvl(s.a)) * s.w + hg;
      if (db > -110) nzEnv[mic][bd] += dbToGain(db) * synth.bands[bd].norm;
    }
    float dec = pd::noiseDecay[s.a][bd] + (pd::noiseDecay[s.b][bd] - pd::noiseDecay[s.a][bd]) * s.w;
    nzMul[bd] = std::pow(10.f, dec * 0.05f / sr);
  }
  knocking = true;

  // the body's ring, measured per key, layer, mic and band; handed to the synth's bank
  {
    float pl[pd::kNoiseBands], pr[pd::kNoiseBands], dec[pd::kNoiseBands];
    float bg = ctx.get(synth.body) + gainDb;
    for (int bd = 0; bd < pd::kNoiseBands; ++bd) {
      float pw[2];
      for (int mic = 0; mic < 2; ++mic) {
        auto lvl = [&](int k) {
          const auto& L = pd::bodyLevels[k];
          return 0.01f * (L[l0][mic][bd] + (L[l0 + 1][mic][bd] - L[l0][mic][bd]) * lf);
        };
        float db = lvl(s.a) + (lvl(s.b) - lvl(s.a)) * s.w + bg;
        pw[mic] = db > -120 ? std::pow(10.f, db * 0.1f) : 0.f;
      }
      pl[bd] = pw[0];
      pr[bd] = pw[1];
      dec[bd] = pd::bodyDecay[s.a][bd] + (pd::bodyDecay[s.b][bd] - pd::bodyDecay[s.a][bd]) * s.w;
    }
    synth.addBody(note, pl, pr, dec);
  }
}

void PianoVoice::release() {
  damped = true;
  synth.dampBody(note, dampBaseNow / std::max(lastDamper > 0 ? lastDamper : 1.f, 0.25f));
}

void PianoVoice::updatePoles(float pitchHz, float sustainScale, float unisonScale, float damperScale) {
  float nyq = 0.47f * sr;
  for (int i = 0; i < count; ++i) {
    float f = pitchHz * ratio[i] + offset[i] * unisonScale;
    float db = decay[i] / sustainScale - extra[i] - (damped ? damp[i] / damperScale : 0.f);
    float rad = f < nyq ? std::pow(10.f, db * 0.05f / sr) : 0.f;
    float w = kTwoPi * f / sr;
    pr[i] = rad * std::cos(w);
    pi[i] = rad * std::sin(w);
  }
}

void PianoVoice::cull() {
  // Finished: the whole voice is below kVoiceEndDb (each component's gains to the two mics are
  // normalised around 1, so |z|² summed is close to the output power)
  float total = 0;
  for (int k = 0; k < count; ++k) total += re[k] * re[k] + im[k] * im[k];
  if (count > 0 && total < dbToGain(kVoiceEndDb) * dbToGain(kVoiceEndDb)) {
    for (int k = 0; k < count; ++k) re[k] = im[k] = pr[k] = pi[k] = wl[k] = wrr[k] = wri[k] = 0;
    count = 0;
    return;
  }
  const float thr = dbToGain(synth.floorDb) * dbToGain(synth.floorDb);
  int i = 0;
  while (i < count) {
    if (re[i] * re[i] + im[i] * im[i] < thr) {
      int j = --count;
      re[i] = re[j]; im[i] = im[j]; pr[i] = pr[j]; pi[i] = pi[j];
      wl[i] = wl[j]; wrr[i] = wrr[j]; wri[i] = wri[j];
      ratio[i] = ratio[j]; offset[i] = offset[j]; decay[i] = decay[j]; damp[i] = damp[j]; extra[i] = extra[j];
      re[j] = im[j] = pr[j] = pi[j] = wl[j] = wrr[j] = wri[j] = 0;
    } else {
      ++i;
    }
  }
}

void PianoVoice::render(VoiceContext& ctx, Buffer& out) {
  if (pending) {
    pending = false;
    strike(ctx, pendingVel);
  }
  float pitch = static_cast<float>(ctx.pitchHz());
  float sus = ctx.get(synth.sustain) * 0.01f;
  float uni = ctx.get(synth.unison) * 0.01f;
  float dmp = ctx.get(synth.damper) * 0.01f;
  if (pitch != lastPitch || sus != lastSustain || uni != lastUnison || dmp != lastDamper || damped != lastDamped) {
    updatePoles(pitch, sus, uni, dmp);
    lastPitch = pitch; lastSustain = sus; lastUnison = uni; lastDamper = dmp; lastDamped = damped;
  }

  const int frames = out.frames;
  const int padded = (count + kLanes - 1) & ~(kLanes - 1);
  for (int c = 0; c < padded; c += kLanes) {
    float r[kLanes], m[kLanes], a[kLanes], b[kLanes], gl[kLanes], gr[kLanes], gi[kLanes];
    for (int k = 0; k < kLanes; ++k) {
      r[k] = re[c + k]; m[k] = im[c + k]; a[k] = pr[c + k]; b[k] = pi[c + k];
      gl[k] = wl[c + k]; gr[k] = wrr[c + k]; gi[k] = wri[c + k];
    }
    for (int i = 0; i < frames; ++i) {
      float ol[kLanes], orr[kLanes];
      for (int k = 0; k < kLanes; ++k) {
        float nr = r[k] * a[k] - m[k] * b[k];
        float ni = r[k] * b[k] + m[k] * a[k];
        r[k] = nr;
        m[k] = ni;
        ol[k] = nr * gl[k];
        orr[k] = nr * gr[k] + ni * gi[k];
      }
      float sl = ((ol[0] + ol[1]) + (ol[2] + ol[3])) + ((ol[4] + ol[5]) + (ol[6] + ol[7]));
      float sr2 = ((orr[0] + orr[1]) + (orr[2] + orr[3])) + ((orr[4] + orr[5]) + (orr[6] + orr[7]));
      out.left[i] += sl;
      out.right[i] += sr2;
    }
    for (int k = 0; k < kLanes; ++k) { re[c + k] = r[k]; im[c + k] = m[k]; }
  }
  cull();
  synth.compsNow += count;
  if (count > 0 && note >= 0 && note < 128) {
    synth.sounding[note] = true;
    // concert pitch, for the string bank (only a real change: rounding differs a little per voice)
    float a4 = pitch / std::exp2((static_cast<float>(ctx.pitch()) - 69.f) / 12.f);
    if (std::fabs(a4 - synth.a4Hz) > 0.01f) synth.a4Hz = a4;
  }

  if (knocking) {
    dsp::Rng& rnd = ctx.rng();
    // bands that have faded below −100 dB are skipped (and their filters cleared)
    bool live[pd::kNoiseBands];
    for (int bd = 0; bd < pd::kNoiseBands; ++bd) {
      float g = std::max(nzEnv[0][bd], nzEnv[1][bd]) / std::max(synth.bands[bd].norm, 1e-9f);
      live[bd] = g > 1e-5f;
      if (!live[bd] && g > 0) {
        nzEnv[0][bd] = nzEnv[1][bd] = 0;
        for (float& v : nzS[0][bd]) v = 0;
        for (float& v : nzS[1][bd]) v = 0;
      }
    }
    for (int i = 0; i < frames; ++i) {
      float xl = rnd.bipolar(), xr = rnd.bipolar(), l = 0, r = 0;
      float rise = 1 - nzRise;
      nzRise *= nzRiseMul;
      for (int bd = 0; bd < pd::kNoiseBands; ++bd) {
        if (!live[bd]) continue;
        const auto& band = synth.bands[bd];
        l += Piano::bandStep(band, xl, nzS[0][bd]) * nzEnv[0][bd];
        r += Piano::bandStep(band, xr, nzS[1][bd]) * nzEnv[1][bd];
        nzEnv[0][bd] *= nzMul[bd];
        nzEnv[1][bd] *= nzMul[bd];
      }
      out.add(i, l * rise, r * rise);
    }
    float peak = 0;
    for (int bd = 0; bd < pd::kNoiseBands; ++bd) peak = std::max(peak, std::max(nzEnv[0][bd], nzEnv[1][bd]));
    if (peak <= 0) {
      knocking = false;
      for (int bd = 0; bd < pd::kNoiseBands; ++bd) {
        nzEnv[0][bd] = nzEnv[1][bd] = 0;
        for (float& v : nzS[0][bd]) v = 0;
        for (float& v : nzS[1][bd]) v = 0;
      }
    }
  }
}

SK_SYNTH(Piano)
