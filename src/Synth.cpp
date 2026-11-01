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
#include <synthkit/synth.h>

#include <algorithm>
#include <cmath>

#include "PianoData.h"

using namespace sk;
namespace pd = piano_data;

namespace {
constexpr int kMaxPartials = 96;
constexpr int kLanes = 8;
constexpr int kCapacity = 512;            // components per voice: room for a restrike over a sounding note
constexpr int kLastDamper = 88;           // E6: keys above have no dampers, as on a grand
constexpr float kFloorDb = -90.f;         // components quieter than this (dBFS) are dropped
constexpr int kBudget = 4000;             // components sounding across all keys before the quietest are dropped early
constexpr float kRangeDb = 70.f;          // and partials this far below a note's loudest one aren't started
constexpr float kTwoPi = 6.28318530718f;
constexpr float kTrimDb = -10.f;          // headroom: the recordings peak near 0 dBFS on one loud note

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


struct Piano;

struct PianoVoice : Voice {
  explicit PianoVoice(Piano& s) : synth(s) {}
  void prepare(const PrepareInfo& info) override { sr = static_cast<float>(info.sampleRate); }
  void start(const NoteOn& n, const StartInfo& s) override;
  void strike(VoiceContext& ctx, float vel);
  void release() override { damped = true; }
  void render(VoiceContext& ctx, Buffer& out) override;
  bool active() const override { return pending || count > 0 || knocking; }

  void add(float amp, float decayDb, float ratio, float offsetHz, float phase, float damp, float wl, float wr,
           float stereoPhase);
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
  float decay[kCapacity];     // natural decay (dB/s, negative)
  float damp[kCapacity];      // extra decay with the damper down (dB/s, positive)

  // pole inputs last used, to recompute only on change
  float lastPitch = -1, lastSustain = -1, lastUnison = -1, lastDamper = -1;
  bool lastDamped = false;

  // the strike's noise: octave bands of noise, per mic, each fading at its measured rate
  bool knocking = false;
  float nzEnv[2][pd::kNoiseBands] = {}, nzMul[pd::kNoiseBands] = {};
  float nzS1[2][pd::kNoiseBands] = {}, nzS2[2][pd::kNoiseBands] = {};
};

struct Piano : Synth {
  Param touch, dynamics, bright, hammer, unison, stretch, sustain, damper, width, room;

  void defineParams(ParamBuilder& b) override {
    touch = b.add("play.touch").name("Touch").range(-1, 1).def(0).group("Playing");
    dynamics = b.add("play.dynamics").name("Dynamics").range(25, 150).def(100).unit("%").group("Playing");
    bright = b.add("tone.bright").name("Brightness").range(-6, 6).def(0).unit("dB").group("Tone");
    hammer = b.add("tone.hammer").name("Hammer").range(-40, 12).def(0).unit("dB").group("Tone");
    unison = b.add("string.unison").name("Unison").range(0, 300).def(100).unit("%").group("Strings");
    stretch = b.add("string.stretch").name("Stretch").range(0, 200).def(100).unit("%").group("Strings");
    sustain = b.add("string.sustain").name("Sustain").range(25, 400).log().def(100).unit("%").group("Strings");
    damper = b.add("string.damper").name("Dampers").range(25, 400).log().def(100).unit("%").group("Strings");
    width = b.add("space.width").name("Width").range(0, 150).def(100).unit("%").group("Space");
    room = b.add("space.room").name("Room").range(0, 100).def(12).unit("%").group("Space");
  }

  std::unique_ptr<Voice> makeVoice() override { return std::make_unique<PianoVoice>(*this); }

  // The recording's stretch tuning, scaled by Stretch.
  double tune(int note, const NoteContext& n) override {
    return note + n.get(stretch) * 0.01 * measuredCents(note) * 0.01;
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

  // Octave band-pass filters for the strike's noise (RBJ, 0 dB peak), each
  // scaled so white noise through it comes out at unit RMS.
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

    for (int b = 0; b < pd::kNoiseBands; ++b) {
      Band& bd = bands[b];
      float fc = pd::kNoiseBandLow[b] * 1.41421356f;
      if (fc * 1.41421356f > 0.49f * sr) { bd = Band{}; continue; }
      float w0 = kTwoPi * fc / sr, alpha = std::sin(w0) / (2 * 1.41421356f), a0 = 1 + alpha;
      bd.b0 = alpha / a0;
      bd.a1 = -2 * std::cos(w0) / a0;
      bd.a2 = (1 - alpha) / a0;
      float s1 = 0, s2 = 0;
      double var = 0;
      int n = static_cast<int>(sr);
      for (int i = 0; i < n + 2000; ++i) {
        float y = bandStep(bd, rng.bipolar(), s1, s2);
        if (i >= 2000) var += double(y) * y;
      }
      bd.norm = static_cast<float>(1.0 / std::sqrt(var / n));
    }
  }

  // one sample of a band-pass (transposed direct form II; b1 = 0, b2 = −b0)
  static float bandStep(const Band& b, float x, float& s1, float& s2) {
    float y = b.b0 * x + s1;
    s1 = -b.a1 * y + s2;
    s2 = -b.b0 * x - b.a2 * y;
    return y;
  }

  void processGlobal(GlobalContext& ctx, Buffer& io) override {
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
                     float stereoPhase) {
  if (count >= kCapacity) {
    // full (many fast restrikes): replace the quietest component
    int q = 0;
    float qm = re[0] * re[0] + im[0] * im[0];
    for (int i = 1; i < count; ++i) {
      float m = re[i] * re[i] + im[i] * im[i];
      if (m < qm) { qm = m; q = i; }
    }
    if (qm > amp * amp) return;
    re[q] = re[count - 1]; im[q] = im[count - 1]; pr[q] = pr[count - 1]; pi[q] = pi[count - 1];
    wl[q] = wl[count - 1]; wrr[q] = wrr[count - 1]; wri[q] = wri[count - 1];
    ratio[q] = ratio[count - 1]; offset[q] = offset[count - 1]; decay[q] = decay[count - 1]; damp[q] = damp[count - 1];
    --count;
  }
  int i = count++;
  re[i] = amp * std::cos(phase);
  im[i] = amp * std::sin(phase);
  wl[i] = gl;
  wrr[i] = gr * std::cos(stereoPhase);    // right = Re(z · e^{jφ}) · gain
  wri[i] = -gr * std::sin(stereoPhase);
  ratio[i] = rat;
  offset[i] = offHz;
  decay[i] = decayDb;
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

  // velocity → position among the recorded layers
  float gamma = std::exp2(ctx.get(synth.touch));
  float v = 127.f * std::pow(std::clamp(vel, 0.f, 1.f), gamma);
  float lp = 0, below = 0;
  if (v <= pd::kLayerVel[0]) {
    below = 12.f * std::log2(std::max(v, 1.f) / pd::kLayerVel[0]) * 0.5f;   // softer than the softest layer
  } else if (v >= pd::kLayerVel[pd::kLayers - 1]) {
    lp = pd::kLayers - 1;
  } else {
    int i = 0;
    while (i + 2 < pd::kLayers && v >= pd::kLayerVel[i + 1]) ++i;
    lp = i + (v - pd::kLayerVel[i]) / (pd::kLayerVel[i + 1] - pd::kLayerVel[i]);
  }
  int l0 = std::min(static_cast<int>(lp), pd::kLayers - 2);
  float lf = lp - l0;

  KeySpan s = spanFor(note);
  const pd::Key& ka = pd::keys[s.a];
  const pd::Key& kb = pd::keys[s.b];
  float B = std::exp(std::log(ka.B) + (std::log(kb.B) - std::log(ka.B)) * s.w);

  auto layerLevel = [&](int pIdx, int mic) {
    const auto& L = pd::levels[pIdx];
    return 0.01f * (L[l0][mic] + (L[l0 + 1][mic] - L[l0][mic]) * lf);
  };
  auto keyLoud = [&](const pd::Key& k) { return k.loud[l0] + (k.loud[l0 + 1] - k.loud[l0]) * lf; };

  // Dynamics: scale the distance from the loudest layer
  float loudNow = keyLoud(ka) + (keyLoud(kb) - keyLoud(ka)) * s.w;
  float loudTop = ka.loud[pd::kLayers - 1] + (kb.loud[pd::kLayers - 1] - ka.loud[pd::kLayers - 1]) * s.w;
  float dyn = ctx.get(synth.dynamics) * 0.01f;
  float gainDb = (dyn - 1.f) * (loudNow - loudTop) + below * dyn + kTrimDb;
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
  float dampBase = 0;
  if (note <= kLastDamper) {
    float t = std::clamp((note - 21) / 67.f, 0.f, 1.f);
    dampBase = 35.f * std::pow(300.f / 35.f, t);   // dB/s: A0 ≈ 35, E6 ≈ 300
  }

  // the loudest partial at this velocity, for the range cut
  float loudest = -200.f;
  for (const pd::Key* k : {&ka, &kb})
    for (int i = k->first; i < k->first + k->count; ++i)
      loudest = std::max(loudest, std::max(layerLevel(i, 0), layerLevel(i, 1)));
  float cut = std::max(kFloorDb + 6, loudest + gainDb - kRangeDb);

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
    float s1 = wa * A.s1 + wb * Bp.s1;
    float s2 = wa * A.s2 + wb * Bp.s2;
    float drop = wa * A.drop + wb * Bp.drop;
    float beat = wa * A.beat + wb * Bp.beat;
    float beatDb = wa * A.beatDb + wb * Bp.beatDb;
    float stereoPhase = (s.w < 0.5f ? A : Bp).phase;

    float gl = dbToGain(Ll), gr = dbToGain(Lr);
    float amp = std::sqrt(0.5f * (gl * gl + gr * gr));
    gl /= amp;
    gr /= amp;
    float rat = pn * std::sqrt((1 + B * pn * pn) / (1 + B));
    float dmp = dampBase * (1 + 0.03f * (pn - 1));
    constexpr float kStart = -1.5707963f;   // sine phase: each partial starts from rest

    float after = drop < -0.01f ? amp * dbToGain(drop) : amp;
    float prompt = amp - after;
    if (prompt > amp * 1e-3f) add(prompt, s1, rat, 0, kStart, dmp, gl, gr, stereoPhase);
    float q = 0;
    if (beat > 0.05f) {
      float rr = dbToGain(beatDb);
      q = (rr - 1) / (rr + 1);
    }
    if (q > 0.02f) {
      float a = after / (1 + q), b = after * q / (1 + q);
      add(a, s2, rat, -0.5f * beat, kStart, dmp, gl, gr, stereoPhase);
      add(b, s2, rat, 0.5f * beat, kStart + r.uniform(-1.5f, 1.5f), dmp, gl, gr, stereoPhase);
    } else {
      add(after, s2, rat, 0, kStart, dmp, gl, gr, stereoPhase);
    }
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
}

void PianoVoice::updatePoles(float pitchHz, float sustainScale, float unisonScale, float damperScale) {
  float nyq = 0.47f * sr;
  for (int i = 0; i < count; ++i) {
    float f = pitchHz * ratio[i] + offset[i] * unisonScale;
    float db = decay[i] / sustainScale - (damped ? damp[i] / damperScale : 0.f);
    float rad = f < nyq ? std::pow(10.f, db * 0.05f / sr) : 0.f;
    float w = kTwoPi * f / sr;
    pr[i] = rad * std::cos(w);
    pi[i] = rad * std::sin(w);
  }
}

void PianoVoice::cull() {
  const float thr = dbToGain(synth.floorDb) * dbToGain(synth.floorDb);
  int i = 0;
  while (i < count) {
    if (re[i] * re[i] + im[i] * im[i] < thr) {
      int j = --count;
      re[i] = re[j]; im[i] = im[j]; pr[i] = pr[j]; pi[i] = pi[j];
      wl[i] = wl[j]; wrr[i] = wrr[j]; wri[i] = wri[j];
      ratio[i] = ratio[j]; offset[i] = offset[j]; decay[i] = decay[j]; damp[i] = damp[j];
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

  if (knocking) {
    dsp::Rng& rnd = ctx.rng();
    for (int i = 0; i < frames; ++i) {
      float xl = rnd.bipolar(), xr = rnd.bipolar(), l = 0, r = 0;
      for (int bd = 0; bd < pd::kNoiseBands; ++bd) {
        const auto& band = synth.bands[bd];
        l += Piano::bandStep(band, xl, nzS1[0][bd], nzS2[0][bd]) * nzEnv[0][bd];
        r += Piano::bandStep(band, xr, nzS1[1][bd], nzS2[1][bd]) * nzEnv[1][bd];
        nzEnv[0][bd] *= nzMul[bd];
        nzEnv[1][bd] *= nzMul[bd];
      }
      out.add(i, l, r);
    }
    float peak = 0;
    for (int bd = 0; bd < pd::kNoiseBands; ++bd) peak = std::max(peak, std::max(nzEnv[0][bd], nzEnv[1][bd]) / std::max(synth.bands[bd].norm, 1.f));
    if (peak < 1e-6f) {
      knocking = false;
      for (int bd = 0; bd < pd::kNoiseBands; ++bd) nzEnv[0][bd] = nzEnv[1][bd] = nzS1[0][bd] = nzS1[1][bd] = nzS2[0][bd] = nzS2[1][bd] = 0;
    }
  }
}

SK_SYNTH(Piano)
