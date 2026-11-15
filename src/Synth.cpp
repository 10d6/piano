// Piano: a modal piano whose strings are measured from a real one.
//
// Each key is a bank of decaying sine partials. Everything about them comes
// from measurements of the Salamander Grand Piano V3 recordings
// (tools/analyze.py → src/PianoData.h), taken every minor third and
// interpolated between:
//
//   - the partial frequencies, from each string's measured stiffness
//     (inharmonicity B), and the instrument's stretch tuning; in the treble,
//     where each partial actually peaks, and the unison's clusters;
//   - each partial's two-stage decay: a fast "prompt" sound, then a slow
//     "aftersound" (the 2-3 strings of a unison drifting out of phase), and
//     the aftersound's beating;
//   - each partial's starting level at 16 velocities, for both of the
//     recording's microphones, and the phase between them, so loudness,
//     tone and the stereo picture all change with how hard a key is struck,
//     as on the recordings;
//   - how each note swells in, and the strike: from A2 up each measured key's
//     own, recorded at velocity 100 with its partials notched out
//     (PianoStrike.h), brightened by velocity through a shelf as the felt
//     does; below A2 octave bands of noise (the knock and a slower part).
//
// One voice per key, as on a piano: striking a key that is still sounding
// hands its vibration over to the new strike. docs/MODEL.md describes every
// part, and where its numbers come from.
//
// Around the voices, in the synth: the body's ring after each strike, the
// mechanical noises (keys coming up, the pedal), all as octave bands of
// noise; and the strings' sympathetic ring, a bank of every string's lower
// partials that the strings with their dampers up pick up from the playing.
#include <synthkit/synth.h>

#include <algorithm>
#include <cmath>

#include "PianoData.h"
#include "PianoStrike.h"
#include "Reverb.h"

#ifndef BY_FREQUENCY
#define BY_FREQUENCY 0.f   // an experiment, off: inconclusive on held-out keys, which are at their limit anyway (docs/TESTING.md, neighbour.py)
#endif

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
constexpr float kTrimDb = -10.f;
constexpr float kWet = 0.1062f;            // the reverb's level: Hall at a given Reverb % as loud as the old room was          // headroom: the recordings peak near 0 dBFS on one loud note

constexpr float kReleaseDb = -37.f;       // the release and pedal recordings' level against the notes
constexpr float kPedalDownDb = -20.f;     // (the Salamander sfz's own mix: the release takes are normalised)
constexpr float kPedalUpDb = -19.f;
constexpr float kReleaseVelTrack = 0.82f; // how much the release noise follows the strike's velocity, as in the sfz
constexpr int kBankMaxPartial = 16;       // the string bank: each string's partials up to the 16th…
constexpr float kBankMaxHz = 4000.f;      // …and 4 kHz
constexpr float kStrikePointRef = 0.12f;  // Strike Point's default: every key's own measured strike point (pd::strikeX, about 12 %)
constexpr float kCoupling = 0.06f;        // how strongly sound at a free string's partial drives it (1/s)

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
// Stiffness: partial n of a string with inharmonicity B is at n·√((1 + B n²) / (1 + B)) times its
// fundamental, then `dev` cents off that line where it was measured elsewhere. Stiffness scales B
// and those offsets alike (0 %: an ideal, harmonic string; 100 %: as measured).
inline float partialRatio(int n, float B, float devCents, float stiff) {
  if (stiff == 1.f) return n * std::sqrt((1 + B * n * n) / (1 + B)) * std::exp2(devCents / 1200.f);
  float b = B * stiff;
  return n * std::sqrt((1 + b * n * n) / (1 + b)) * std::exp2(stiff * devCents / 1200.f);
}

// Overtone Decay: each component's measured decay times (its frequency / the fundamental)^γ, γ
// from −1 to +1: upper partials ring on longer (bright, steely) or die sooner (dark, gut-like)
// than measured; the fundamental's is kept. The factor stays within ¼…4, so the top keys, which
// have no dampers, still fade within seconds, and no component rings longer than 0.3 dB/s.
inline float overtoneDecayDb(float decayDb, float ratio, float gamma) {
  if (gamma == 0.f || ratio <= 0.f) return decayDb;
  return std::min(decayDb * std::clamp(std::pow(ratio, gamma), 0.25f, 4.f), -0.3f);
}

// Touch Point: a finger (or a strip of felt) resting on the string at the fraction y of its
// length, w of the length wide. It takes energy from partial n where that partial moves under it:
// sin²(nπs) averaged over the finger, ½(1 − cos(2πny)·sinc(nw)). A partial with a node under a
// narrow finger (the 2nd, 4th, 6th… at ½; every third at ⅓) is hardly touched and rings on, a
// harmonic; one with an antinode there takes the whole of the finger's damping. A wide finger
// spans the upper partials' nodes and antinodes alike, and they all take half: a mute.
inline float touchShare(int n, float y, float w) {
  float a = 0.5f * kTwoPi * n * w;
  float sinc = a > 1e-4f ? std::sin(a) / a : 1.f;
  return 0.5f * (1.f - std::cos(kTwoPi * n * y) * sinc);
}

inline float damperRate(int note, int last = kLastDamper) {
  if (note > last) return 0.f;
  float t = std::clamp((note - 21) / 67.f, 0.f, 1.f);
  return 70.f * std::pow(300.f / 70.f, t);   // A0 ≈ 70 (about 0.9 s to silence), E6 ≈ 300
}

// A partial's characteristic decay (dB/s, positive): its modes' where it has them, else its
// aftersound's for a medium strike.
inline float charDecay(int i) {
  const pd::Partial& p = pd::partials[i];
  return std::max(p.nm > 0 ? -p.dch : -p.s2[1], 0.05f);
}

// How much between-key blending goes by frequency rather than by partial number (0-1).
constexpr float kByFrequency = BY_FREQUENCY;
// Restrike: what the hammer's felt takes from the vibration it lands on, in dB, at the partials
// that move most under it. A judgment (nothing here measures a string struck while it rings);
// 0 makes a restrike at 100 % a plain sum of the two strikes, which tools/measure/restrike.py
// checks on a build with it at 0.
#ifndef RESTRIKE_FELT_DB
#define RESTRIKE_FELT_DB 6.f
#endif
constexpr float kRestrikeFeltDb = RESTRIKE_FELT_DB;

// Measured key k's level (per mic, dB, at layer l0 + lf) and log characteristic decay at the
// frequency `rat` times the pitch of `note`: interpolated along k's own partials, in log frequency.
inline void atFrequency(int k, int note, float rat, int l0, float lf, float& lvL, float& lvR, float& logDec) {
  const pd::Key& K = pd::keys[k];
  float x = std::log(rat) + std::log(2.f) * ((note - K.note) / 12.f + (measuredCents(note) - K.cents) / 1200.f);
  auto level = [&](int i, int mic) {
    const auto& L = pd::levels[i];
    return 0.01f * (L[l0][mic] + (L[l0 + 1][mic] - L[l0][mic]) * lf);
  };
  int prev = -1;
  float xp = 0;
  for (int i = K.first; i < K.first + K.count; ++i) {
    const pd::Partial& p = pd::partials[i];
    float xi = std::log(p.n * std::sqrt((1 + K.B * p.n * p.n) / (1 + K.B))) + p.dev * (std::log(2.f) / 1200.f);
    if (xi >= x) {
      float u = prev < 0 ? 0.f : (x - xp) / std::max(xi - xp, 1e-6f);
      int a = prev < 0 ? i : prev;
      lvL = level(a, 0) + (level(i, 0) - level(a, 0)) * u;
      lvR = level(a, 1) + (level(i, 1) - level(a, 1)) * u;
      logDec = std::log(charDecay(a)) + (std::log(charDecay(i)) - std::log(charDecay(a))) * u;
      return;
    }
    prev = i;
    xp = xi;
  }
  int a = prev < 0 ? K.first : prev;   // above the key's last partial: its last one's
  lvL = level(a, 0);
  lvR = level(a, 1);
  logDec = std::log(charDecay(a));
}

struct Piano;

struct PianoVoice : Voice {
  explicit PianoVoice(Piano& s) : synth(s) {
    for (float& g : gPrev) g = 1;
    for (float& g : pnGain) g = 1;
  }
  void prepare(const PrepareInfo& info) override { sr = static_cast<float>(info.sampleRate); }
  void start(const NoteOn& n, const StartInfo& s) override;
  void strike(VoiceContext& ctx, float vel);
  void release() override;
  void render(VoiceContext& ctx, Buffer& out) override;
  bool active() const override { return pending || count > 0 || knocking || striking; }

  // adds a component; with onset > 0 (dB/s) also a short-lived negative twin, so the
  // component swells in as 1 − e^{−t/τ} over the hammer's contact instead of starting at full level
  void add(float amp, float decayDb, float ratio, float offsetHz, float phase, float damp, float wl, float wr,
           float stereoPhase, float onset);
  void addOne(float amp, float decayDb, float ratio, float offsetHz, float phase, float damp, float wl, float wr,
              float stereoPhase, float extraDb);
  void updatePoles(float pitchHz, float sustainScale, float unisonScale, float damperScale, float gamma);
  bool touchBlock(VoiceContext& ctx);
  bool listenBlock(VoiceContext& ctx);
  void cull();
  void arpBlock(VoiceContext& ctx, int frames, int pattern);
  void arpSequence(int scale, int lo, int hi, bool octaves);

  Piano& synth;
  float sr = 48000;
  int note = -1;
  bool pending = false;
  float pendingVel = 0;
  int count = 0;      // components in use; arrays are zero-padded to a multiple of kLanes
  // the soft pedal, while a strike adds its partials: each one's gain at the strike and later, and
  // how fast it moves from one to the other (dB/s; 0: no soft pedal)
  float unaEarly = 1, unaLate = 1, unaExtra = 0;
  bool damped = false;
  bool arpVoice = false;   // started by the kit's arpeggiator or sequencer (StartInfo::arp)

  // components (struct of arrays, so the inner loop vectorises)
  alignas(32) float re[kCapacity], im[kCapacity];   // state: amplitude and phase
  alignas(32) float pr[kCapacity], pi[kCapacity];   // pole: radius · e^{jω}
  alignas(32) float wl[kCapacity], wrr[kCapacity], wri[kCapacity];  // to left / right
  float ratio[kCapacity];     // frequency / the key's pitch
  float offset[kCapacity];    // beat offset (Hz), scaled by the unison setting
  float decay[kCapacity];     // natural decay (dB/s, negative), scaled by Sustain
  float extra[kCapacity];     // extra decay not scaled by Sustain (dB/s, positive): the onset twins
  float damp[kCapacity];      // extra decay with the damper down (dB/s, positive)

  // The partial arpeggiator (arp.*): each component's output gain, ramped across the control
  // block from gPrev to its partial's pnGain. It scales what the component sends to the mics, not
  // its state, so a partial comes back to exactly its natural level when its step has passed.
  unsigned char pnum[kCapacity];   // the partial number each component belongs to
  float gPrev[kCapacity];          // its output gain at the end of the last block (1 when idle)
  float pnGain[kMaxPartials + 1];  // per partial number, this block's target gain
  int curPn = 0;                   // the partial strike() is adding
  bool gainsLive = false;          // gains differ from 1 (or are ramping back): the slower path
  double arpPh = 0;                // steps since the strike
  int arpNext = 0;                 // the next step to choose a partial for
  unsigned char arpHist[4] = {};   // the partial of step k, at k & 3 (0: none)
  unsigned arpRng = 1, strikes = 0;
  int seq[kMaxPartials] = {}, seqN = 0, seqKey = -1;
  bool inSeq[kMaxPartials + 1] = {};   // the partials the pattern covers (in the scale and range)

  // Touch Point (touch.*): the finger's extra decay per partial number (dB/s, positive; all 0
  // with no finger on the string), and the settings it was worked out from
  float touchDb[kMaxPartials + 1] = {};
  float lastTouchPress = 0, lastTouchPoint = -1, lastTouchWidth = -1;
  bool lastTouchSnap = false;

  // Listening Point (listen.*): each partial's share of the note's first half second (set as the
  // strike adds its components), and the gain per partial number the settings and those give
  float listenPow[kMaxPartials + 1] = {};
  float listenGain[kMaxPartials + 1] = {};
  float lastListenDepth = 0, lastListenPoint = -1, lastListenWidth = -1;
  bool listenStruck = false;       // a strike since the gains were worked out
  int struckFrom = -1;             // this block's strike added the components from here on

  // Restrike (string.restrike): which measured mode a component is (its index in pd::modes + 1),
  // so a new strike can add itself to the vibration already there; 0 for the rest (onset twins,
  // the soft pedal's copies, partials without modes), −1 for what a strike has left to fade
  short mid[kCapacity] = {};
  short tagNext = 0;
  float feltKeep[kMaxPartials + 1] = {};
  void foldOld(int j, int at, int old, float fade);

  // pole inputs last used, to recompute only on change
  float lastPitch = -1, lastSustain = -1, lastUnison = -1, lastDamper = -1, lastGamma = 0;
  bool lastDamped = false;

  // the strike's noise: octave bands of noise, per mic, each fading at its measured rate
  bool knocking = false;
  float nzEnv[2][pd::kNoiseBands] = {}, nzMul[pd::kNoiseBands] = {};
  float nzEnv2[2][pd::kNoiseBands] = {}, nzMul2[pd::kNoiseBands] = {};   // its slower second part,
  float nzDec2[pd::kNoiseBands] = {};   // dB/s, which the damper stops along with the strings
  bool nzDamped = false;
  float nzS[2][pd::kNoiseBands][2 * pd::kNoiseStages + 4] = {};
  float nzRise = 0, nzRiseMul = 0;   // the noise swells in with the partials: × (1 − nzRise)

  // the strike as recorded (tools/analyze_strike.py), from A2 up: the key's response, played back
  // at its level for this velocity, the knock's (0-40 ms) crossing over to its slower part's
  // (30-50 ms). A few at once, so a fast repeat doesn't cut the last one off.
  struct StrikePlay {
    bool on = false, damped = false;
    int index = 0;
    float pos = 0, step = 1;      // in the response's samples
    float g1[2] = {}, g2[2] = {}, env = 1, envMul = 1;
    float b0 = 1, b1 = 0, a1 = 0, x1[2] = {}, y1[2] = {};   // the brightness shelf, per mic
    // Hardness: the hammer spectrum's ratio, (1 + (f/fc)²) / (1 + (f/fc')²), as two first-order
    // sections, per mic
    bool hard = false;
    float hb0 = 1, hb1 = 0, ha1 = 0, hx[2][2] = {}, hy[2][2] = {};
    // The hammer's surface: up to three second-order sections per mic (two high shelves for Edge and
    // Core, one first-order shelf for Weight) and the gain that keeps the note's loudness
    int surf = 0;
    float sc[3][5] = {}, sz[3][2][2] = {}, sg = 1;
  };
  static constexpr int kStrikeSlots = 4;
  StrikePlay plays[kStrikeSlots];
  bool striking = false;
  void renderStrikes(Buffer& out, int frames, float damperDbPerS);
};

struct Piano : Synth {
  Param touch, dynamics, toneRange, softAttack, softPedal, velMap, bright, attack, hammer, hardness, strikePoint, strikeDepth, body, mech, unison, stretch, stiffness, overtoneDecay, sustain, damper,
      resonance, topDampers, width, room, arpPattern, arpScale, arpLow, arpHigh, arpRate, arpSync, arpDivision,
      arpOverlap, arpBoost, arpOthers, arpGroup, arpWidth, touchPress, touchPoint, touchWidth, restrike, touchSnap, touchDry, listenDepth, listenPoint, listenWidth, spaceType, spaceDecay, spaceSize, spacePredelay,
      spaceDamping, spaceEarly, spaceLowCut, spaceHighCut, hamEdge, hamWeight, hamCore, hamMorph, hamMaterial, hamWear, hamUneven;

  void defineParams(ParamBuilder& b) override {
    touch = b.add("play.touch").name("Touch").range(-1, 1).def(0).group("Playing");
    dynamics = b.add("play.dynamics").name("Dynamics").range(25, 150).def(100).unit("%").group("Playing");
    toneRange = b.add("play.tone").name("Tone Range").range(0, 150).def(100).unit("%").group("Playing");
    velMap = b.add("play.velmap").name("Velocity Map").choices({"Smooth", "As Recorded"}).group("Playing");
    softAttack = b.add("play.softattack").name("Soft Attack").range(0, 600).def(25).unit("%").group("Playing");
    softPedal = b.add("play.softpedal").name("Soft Pedal").range(0, 100).def(0).unit("%").cc(67).group("Playing");
    bright = b.add("tone.bright").name("Brightness").range(-6, 6).def(0).unit("dB").modulatable().group("Tone");
    attack = b.add("tone.attack").name("Attack").range(25, 400).log().def(100).unit("%").modulatable().group("Tone");
    body = b.add("tone.body").name("Body").range(-40, 12).def(0).unit("dB").modulatable().group("Tone");
    hammer = b.add("tone.hammer").name("Hammer").range(-40, 12).def(0).unit("dB").modulatable().group("Tone");
    hardness = b.add("tone.hardness").name("Hardness").range(-100, 100).def(0).unit("%").modulatable().group("Tone");
    strikePoint = b.add("tone.strikepoint").name("Strike Point").range(2, 50).log().def(12).unit("%").modulatable().group("Tone");
    strikeDepth = b.add("tone.strikedepth").name("Strike Depth").range(0, 100).def(0).unit("%").modulatable().group("Tone");
    mech = b.add("tone.mech").name("Key & Pedal").range(-40, 12).def(0).unit("dB").group("Tone");
    unison = b.add("string.unison").name("Unison").range(0, 300).def(100).unit("%").modulatable().group("Strings");
    stretch = b.add("string.stretch").name("Stretch").range(0, 200).def(100).unit("%").group("Strings");
    stiffness = b.add("string.stiffness").name("Stiffness").range(0, 800).def(100).unit("%").group("Strings");
    overtoneDecay = b.add("string.overtonedecay").name("Overtone Decay").range(-100, 100).def(0).unit("%").modulatable().group("Strings");
    sustain = b.add("string.sustain").name("Sustain").range(25, 400).log().def(100).unit("%").modulatable().group("Strings");
    damper = b.add("string.damper").name("Dampers").range(25, 400).log().def(100).unit("%").modulatable().group("Strings");
    topDampers = b.add("string.topdampers").name("Top Dampers").choices({"None above E6", "All keys"}).group("Strings");
    // Restrike (PianoVoice::strike): a key struck while it rings. 0 hands the old vibration over (bit-identical)
    restrike = b.add("string.restrike").name("Restrike").range(0, 100).def(0).unit("%").modulatable().group("Strings");
    resonance = b.add("string.resonance").name("Resonance").range(-40, 24).def(0).unit("dB").modulatable().group("Strings");
    width = b.add("space.width").name("Width").range(0, 150).def(100).unit("%").modulatable().group("Space");
    room = b.add("space.room").name("Reverb").range(0, 100).def(12).unit("%").modulatable().group("Space");
    // the reverb (Reverb.h): early reflections by convolution, a feedback-network tail
    spaceType = b.add("space.type").name("Space").choices({"Room", "Chamber", "Hall", "Cathedral", "Plate"}).def(2).group("Space");
    spaceDecay = b.add("space.decay").name("Decay").range(25, 300).log().def(100).unit("%").modulatable().group("Space");
    spaceSize = b.add("space.size").name("Size").range(50, 200).log().def(100).unit("%").modulatable().group("Space");
    spacePredelay = b.add("space.predelay").name("Pre-delay").range(0, 200).def(0).unit("ms").group("Space");
    spaceDamping = b.add("space.damping").name("Damping").range(0, 100).def(50).unit("%").modulatable().group("Space");
    spaceEarly = b.add("space.early").name("Early").range(-30, 6).def(0).unit("dB").group("Space");
    spaceLowCut = b.add("space.lowcut").name("Low Cut").range(20, 1000).log().def(50).unit("Hz").group("Space");
    spaceHighCut = b.add("space.highcut").name("High Cut").range(1000, 20000).log().def(16000).unit("Hz").modulatable().group("Space");
    // the partial arpeggiator (PianoVoice::arpBlock): Off leaves the piano bit-identical
    arpPattern = b.add("parp.pattern").name("Partial Arp Pattern").choices({"Off", "Up", "Down", "Up & Down", "Random"}).group("Partial Arp");
    arpScale = b.add("parp.scale").name("Partial Arp Partials")
                   .choices({"Major Pentatonic", "Minor Pentatonic", "Major Triad", "All Harmonics", "Odd Harmonics"})
                   .group("Partial Arp");
    arpLow = b.add("parp.low").name("Partial Arp Lowest").range(1, 48).def(3).integer().group("Partial Arp");
    arpHigh = b.add("parp.high").name("Partial Arp Highest").range(2, 96).def(16).integer().group("Partial Arp");
    arpRate = b.add("parp.rate").name("Partial Arp Rate").range(0.25, 20).log().def(5).unit("Hz").modulatable().group("Partial Arp");
    arpSync = b.add("parp.sync").name("Partial Arp Sync").toggle().group("Partial Arp");
    arpDivision = b.add("parp.division").name("Partial Arp Division")
                      .choices({"1/2", "1/4", "1/8", "1/8T", "1/16", "1/16T", "1/32"}).def(4).group("Partial Arp");
    arpOverlap = b.add("parp.overlap").name("Partial Arp Overlap").range(0, 200).def(100).unit("%").modulatable().group("Partial Arp");
    arpBoost = b.add("parp.boost").name("Partial Arp Boost").range(-40, 24).def(-20).unit("dB").modulatable().group("Partial Arp");
    arpOthers = b.add("parp.others").name("Partial Arp Rest").range(-40, 0).def(0).unit("dB").modulatable().group("Partial Arp");
    arpGroup = b.add("parp.group").name("Partial Arp Group").choices({"Single", "Octaves"}).def(1).group("Partial Arp");
    arpWidth = b.add("parp.width").name("Partial Arp Width").range(0, 1).def(0).unit("oct").modulatable().group("Partial Arp");
    // Touch Point (PianoVoice::touchBlock): a finger on the strings. Press 0 leaves the piano bit-identical
    touchPress = b.add("touch.press").name("Touch Press").range(0, 2000).exp(3).def(0).unit("dB/s").modulatable().group("Touch Point");
    touchPoint = b.add("touch.point").name("Touch Point").range(2, 50).log().def(50).unit("%").modulatable().group("Touch Point");
    touchWidth = b.add("touch.width").name("Touch Width").range(0, 30).exp(3).def(1).unit("%").modulatable().group("Touch Point");
    touchSnap = b.add("touch.snap").name("Touch Snap").toggle().def(1).group("Touch Point");
    // Listening Point (PianoVoice::listenBlock): where along the string the sound is taken. Depth 0 leaves the piano bit-identical
    listenDepth = b.add("listen.depth").name("Listening Depth").range(0, 100).def(0).unit("%").modulatable().group("Listening Point");
    listenPoint = b.add("listen.point").name("Listening Point").range(2, 50).log().def(12).unit("%").modulatable().group("Listening Point");
    listenWidth = b.add("listen.width").name("Listening Width").range(0, 20).exp(3).def(0).unit("%").modulatable().group("Listening Point");
    touchDry = b.add("touch.dry").name("Touch Dry").range(0, 100).def(0).unit("%").group("Touch Point");
    // The hammer's surface (PianoVoice::strike; docs/MODEL.md): other surfaces than this piano's felt. At
    // their defaults (and Morph on at Material 0) the piano is bit-identical. Morph on: Material sets
    // Edge, Weight and Core, and their own values aren't read
    hamEdge = b.add("hammer.edge").name("Hammer Edge").range(0, 100).def(0).unit("%").modulatable().group("Hammer Surface");
    hamWeight = b.add("hammer.weight").name("Hammer Weight").range(25, 400).log().def(100).unit("%").modulatable().group("Hammer Surface");
    hamCore = b.add("hammer.core").name("Hammer Core").range(0, 100).def(0).unit("%").modulatable().group("Hammer Surface");
    hamMorph = b.add("hammer.morph").name("Hammer Morph").toggle().group("Hammer Surface");
    hamMaterial = b.add("hammer.material").name("Hammer Material").range(0, 100).def(0).unit("%").modulatable().group("Hammer Surface");
    // Wear: the felt packed hard where the piano is played most. Uneven: every key its own hammer. On top
    // of Edge and Weight wherever those come from (set by hand or by Material); 0 is bit-identical
    hamWear = b.add("hammer.wear").name("Hammer Wear").range(0, 100).def(0).unit("%").modulatable().group("Hammer Surface");
    hamUneven = b.add("hammer.uneven").name("Hammer Uneven").range(0, 100).def(0).unit("%").modulatable().group("Hammer Surface");
    // the kit's arpeggiator and sequencer (features.arp, API.md §4b): a piano's notes end with their
    // dampers, so its notes have no note envelope unless one is chosen
    b.kitDefault("arp.envMode", 0);
  }

  // patchVersion 2: the Partial Arp's parameters moved from arp.* to parp.*, which the kit's
  // arpeggiator now owns
  void migratePatch(int fromVersion, PatchData& p) override {
    if (fromVersion < 2)
      for (const char* id : {"pattern", "scale", "low", "high", "rate", "sync", "division", "overlap", "boost",
                             "others", "group", "width"}) {
        std::string from = std::string("arp.") + id;
        if (p.hasParam(from)) p.renameParam(from, std::string("parp.") + id);
      }
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

  // One voice per played key: a key that is still sounding is struck again in place. Notes from
  // the kit's arpeggiator or sequencer take a voice of their own, so its crossfades and overlapping
  // gates can sound two notes of one key at once. NoteOn doesn't say which notes are the kit's,
  // so the synth counts the keys played this host block (processEvents) and the rest are its.
  int allocateVoice(const NoteOn& n, VoicePool& pool) override {
    int key = n.note & 127;
    bool played = playedPending[key] > 0;
    if (played) --playedPending[key];
    if (played || arpType == 0)
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
  // stages per octave band and a 4th-order low-pass just above it (the top
  // keys' knock falls ~45 dB over two octaves, which plain band-passes spill
  // across as hiss), each band scaled so white noise through it comes out at
  // unit RMS.
  // tools/make_header.py solves the band gains against these same filters.
  struct Band {
    float b0 = 0, a1 = 0, a2 = 0, norm = 0;   // the band-pass stages (b1 = 0, b2 = −b0)
    float lp[2][5] = {};                      // the low-pass above it: two biquads b0 b1 b2 a1 a2
    bool hasLp = false;
  };
  Band bands[pd::kNoiseBands];

  // The reverb (Reverb.h): its input and output for one control block.
  piano_reverb::Reverb reverb;
  float revL[32] = {}, revR[32] = {}, wetL[32] = {}, wetR[32] = {};
  float sr = 48000;

  void prepare(const PrepareInfo& info) override {
    sr = static_cast<float>(info.sampleRate);
    for (short& v : modeSlot) v = -1;
    arpOn = info.param("arp.on");
    reverb.prepare(sr);
    // The noise bands' normalisation below draws from this generator after the old room's impulse
    // (1.6 s of two noise channels) did: keep those draws, so the dry piano stays bit-identical.
    dsp::Rng rng;
    rng.seed(0x9e3779b9u);
    for (int i = 0, len = static_cast<int>(1.6 * info.sampleRate); i < 2 * len; ++i) rng.bipolar();
    buildBank();
    for (int c = 0; c < 2; ++c)
      for (int b = 0; b < pd::kNoiseBands; ++b) bandRng[c][b].seed(0x51ed270bu + 977u * static_cast<unsigned>(b + 16 * c));

    for (int b = 0; b < pd::kNoiseBands; ++b) {
      Band& bd = bands[b];
      float fc = pd::kNoiseBandLow[b] * 1.41421356f;
      if (fc * 1.41421356f > 0.49f * sr) { bd = Band{}; continue; }
      float w0 = kTwoPi * fc / sr, alpha = std::sin(w0) / (2 * pd::kNoiseQ), a0 = 1 + alpha;
      bd.b0 = alpha / a0;
      bd.a1 = -2 * std::cos(w0) / a0;
      bd.a2 = (1 - alpha) / a0;
      // a 4th-order Butterworth low-pass at kNoiseLpRatio × the band's lower edge, so a loud band
      // spills little into the quieter ones above it (the knock's spectrum falls with frequency)
      float fl = pd::kNoiseBandLow[b] * pd::kNoiseLpRatio;
      bd.hasLp = fl < 0.45f * sr;
      const float qs[2] = {0.5411961f, 1.3065630f};
      for (int k = 0; k < 2 && bd.hasLp; ++k) {
        float wl = kTwoPi * fl / sr, al = std::sin(wl) / (2 * qs[k]), c = std::cos(wl), n0 = 1 + al;
        bd.lp[k][0] = (1 - c) / 2 / n0;
        bd.lp[k][1] = (1 - c) / n0;
        bd.lp[k][2] = (1 - c) / 2 / n0;
        bd.lp[k][3] = -2 * c / n0;
        bd.lp[k][4] = (1 - al) / n0;
      }
      float st[2 * pd::kNoiseStages + 4] = {};
      double var = 0;
      int n = static_cast<int>(sr);
      for (int i = 0; i < n + 2000; ++i) {
        float y = bandStep(bd, rng.bipolar(), st);
        if (i >= 2000) var += double(y) * y;
      }
      bd.norm = static_cast<float>(1.0 / std::sqrt(var / n));
    }
  }

  // one sample of a band: identical band-pass stages (transposed direct form II; b1 = 0, b2 = −b0)
  static float bandStep(const Band& b, float x, float* st) {
    for (int k = 0; k < pd::kNoiseStages; ++k) {
      float y = b.b0 * x + st[2 * k];
      st[2 * k] = -b.a1 * y + st[2 * k + 1];
      st[2 * k + 1] = -b.b0 * x - b.a2 * y;
      x = y;
    }
    if (b.hasLp)
      for (int k = 0; k < 2; ++k) {
        const float* c = b.lp[k];
        float* s = st + 2 * pd::kNoiseStages + 2 * k;
        float y = c[0] * x + s[0];
        s[0] = c[1] * x - c[3] * y + s[1];
        s[1] = c[2] * x - c[4] * y;
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
  float bodyState[2][pd::kNoiseBands][2 * pd::kNoiseStages + 4] = {};
  dsp::Rng bandRng[2][pd::kNoiseBands];

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
  // Touch Point's Dry: the finger stops the key's ring in the body too. A choice of sound, not
  // physics: the ring is the body and the other strings answering the strike, which a finger on
  // this key's strings doesn't reach. It stays stopped until the key is struck again.
  void touchBody(int key, float dbPerS) {
    key = std::clamp(key, 0, 127);
    for (int b = 0; b < pd::kNoiseBands; ++b) bodyDec[key][b] = std::min(bodyDec[key][b], -dbPerS);
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
  float bB[kBankCap] = {}, bDev[kBankCap] = {};   // for Stiffness: the string's B and the partial's offset
  unsigned char bPn[kBankCap] = {};
  signed char bBand[kBankCap] = {};
  int keyFirst[128] = {}, keyLen[128] = {};      // lanes per key (a multiple of kBankLanes; the padding has bIn = 0)
  bool keyLive[128] = {}, keyPoles[128] = {}, keyDamped[128] = {};
  float liftAmp[pd::kNoiseBands] = {};           // the pedal's lift: amplitude given to each partial, per band
  bool sounding[128] = {};                       // set by voices with partials sounding, each block
  // Restrike: where in the striking voice each mode's component is (−1: nowhere). Filled and
  // emptied within one strike.
  short modeSlot[pd::kModes];
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
  float lastStretch = -1, lastSus = -1, lastDampScale = -1, lastA4 = -1, lastStiff = -1, lastGamma = -2;

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
        if (f0 * rat > kBankMaxHz) break;   // (at Stiffness 100 %: the bank keeps the same partials at any setting)
        while (ia < ea && pd::partials[ia].n < pn) ++ia;
        while (ib < eb && pd::partials[ib].n < pn) ++ib;
        bool hasA = ia < ea && pd::partials[ia].n == pn, hasB = ib < eb && pd::partials[ib].n == pn;
        if (!hasA && !hasB) continue;
        float wa = hasA && hasB ? 1 - s.w : (hasA ? 1.f : 0.f), wb = 1 - wa;
        int pa = hasA ? ia : ib, pb = hasB ? ib : ia;
        float dev = wa * pd::partials[pa].dev + wb * pd::partials[pb].dev;
        rat = partialRatio(pn, B, dev, 1.f);   // as the voices play it
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
        bPn[at] = static_cast<unsigned char>(pn);
        bB[at] = B;
        bDev[at] = dev;
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

  void bankPoles(int key, float stretchPct, float sus, float dampScale, float stiff, float gamma) {
    float f0 = a4Hz * std::exp2((key + stretchPct * 0.01f * measuredCents(key) * 0.01f - 69.f) / 12.f);
    float dmp = keyDamped[key] ? damperRate(key, topDamper) / dampScale : 0.f;
    float rate = 0.5f * sr, nyq = 0.47f * rate;
    for (int i = keyFirst[key]; i < keyFirst[key] + keyLen[key]; ++i) {
      float rat = stiff == 1.f || bIn[i] == 0 ? bRatio[i] : partialRatio(bPn[i], bB[i], bDev[i], stiff);
      float f = f0 * rat;
      float db = overtoneDecayDb(bDecay[i], rat, gamma) / sus - dmp * bDampMul[i];
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
    float stiff = ctx.get(stiffness) * 0.01f, gamma = ctx.get(overtoneDecay) * 0.01f;
    bool changed = stretchPct != lastStretch || sus != lastSus || dampScale != lastDampScale || a4Hz != lastA4 ||
                   stiff != lastStiff || gamma != lastGamma;
    if (changed) {
      lastStretch = stretchPct; lastSus = sus; lastDampScale = dampScale; lastA4 = a4Hz; lastStiff = stiff; lastGamma = gamma;
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
      bool free = bankPedal || bankKeys[key] || key > topDamper;
      bool driven = free && !sounding[key];
      if (keyDamped[key] != !free) {
        keyDamped[key] = !free;
        keyPoles[key] = false;
      }
      if (lift > 0 && key <= topDamper && !bankKeys[key] && !sounding[key]) {
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
      if (!keyPoles[key]) bankPoles(key, stretchPct, sus, dampScale, stiff, gamma);
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

  // the kit's arpeggiator / sequencer (arp.on: 0 Off, 1 Arp, 2 Sequencer). It runs after
  // processEvents, so the keys and pedal followed there are the ones played. Its own notes lift
  // their key's damper as they start (arpStarted) and mark it each block until released
  // (arpUp); a key no arp note holds up any more gets its damper back in processGlobal
  // (arpDampers) unless it's held or the pedal is down. That doesn't rely on release(): the kit
  // also ends arp notes by stealing them or at the end of a phrase.
  Param arpOn;
  int arpType = 0;
  unsigned char playedPending[128] = {};   // keys played this host block, not yet given a voice
  bool arpUp[128] = {}, arpWasUp[128] = {};
  float damperDiv = 1;                     // the Damper parameter's scaling of damperRate
  void arpStarted(int key) {
    key &= 127;
    arpUp[key] = true;
    bodyDamp[key] = 0;   // the damper lifts
  }
  void arpDampers() {
    for (int k = 0; k < 128; ++k) {
      // the kit's notes ignore the pedal themselves, but the body ring is the key's own and follows it
      if (arpWasUp[k] && !arpUp[k] && !keysDown[k] && !pedalDown) dampBody(k, damperRate(k, topDamper) / damperDiv);
      arpWasUp[k] = arpUp[k];
      arpUp[k] = false;
    }
  }
  void damperFalls(EventContext& ctx, int key) {
    dampBody(key, damperRate(key, topDamper) / std::max(ctx.get(damper) * 0.01f, 0.25f));
  }

  // the highest key with a damper: E6 on a grand (the top strings ring on after release, as recorded),
  // or every key, as on many digital pianos (Top Dampers)
  int topDamper = kLastDamper;

  void processEvents(EventContext& ctx) override {
    topDamper = ctx.choice(topDampers) == 1 ? 108 : kLastDamper;
    damperDiv = std::max(ctx.get(damper) * 0.01f, 0.25f);
    arpType = ctx.choice(arpOn);
    for (auto& p : playedPending) p = 0;
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
        // in Arp mode the kit keeps the keys and plays its own notes; otherwise the key gets a voice
        if (arpType != 1 && playedPending[key] < 255) ++playedPending[key];
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
    // Each band and side draws its own noise every sample, sounding or not, so the noise doesn't
    // depend on where blocks start (the host's block size).
    for (int b = 0; b < pd::kNoiseBands; ++b) {
      const Band& bd = bands[b];
      float a0[2], a1[2];
      for (int c = 0; c < 2; ++c) {
        a0[c] = any ? std::sqrt(p0[c][b]) * bd.norm : 0.f;
        a1[c] = any ? std::sqrt(p1[c][b]) * bd.norm : 0.f;
      }
      if (bd.norm == 0 || a0[0] + a0[1] + a1[0] + a1[1] < 1e-9f) {
        for (int i = 0; i < n; ++i) {
          bandRng[0][b].bipolar();
          bandRng[1][b].bipolar();
        }
        continue;
      }
      for (int i = 0; i < n; ++i) {
        float u = static_cast<float>(i + 1) / static_cast<float>(n);
        io.left[i] += bandStep(bd, bandRng[0][b].bipolar(), bodyState[0][b]) * (a0[0] + (a1[0] - a0[0]) * u);
        io.right[i] += bandStep(bd, bandRng[1][b].bipolar(), bodyState[1][b]) * (a0[1] + (a1[1] - a0[1]) * u);
      }
    }
  }

  void processGlobal(GlobalContext& ctx, Buffer& io) override {
    arpDampers();
    renderBody(io);
    renderBank(ctx, io);
    float over = static_cast<float>(compsNow - kBudget) / kBudget;
    floorDb = kFloorDb + std::clamp(over * 40.f, 0.f, 40.f);
    compsNow = 0;
    Ramp w = ctx.ramp(width), mix = ctx.ramp(room);   // both in %
    const int n = std::min(io.frames, 32);
    bool on = false;
    for (int i = 0; i < n; ++i) {
      float l = io.left[i], r = io.right[i];
      float m = 0.5f * (l + r), s = 0.5f * (l - r) * w.at(i) * 0.01f;
      revL[i] = m + s;
      revR[i] = m - s;
      on = on || mix.at(i) > 0;
    }
    piano_reverb::Reverb::Settings st{ctx.choice(spaceType), ctx.get(spaceDecay) * 0.01f, ctx.get(spaceSize) * 0.01f,
                                      ctx.get(spaceDamping),  ctx.get(spacePredelay),      ctx.get(spaceEarly),
                                      ctx.get(spaceLowCut),   ctx.get(spaceHighCut),       on};
    reverb.process(st, revL, revR, wetL, wetR, n);
    for (int i = 0; i < n; ++i) {
      float k = mix.at(i) * 0.01f * kWet;
      io.left[i] = revL[i] + k * wetL[i];
      io.right[i] = revR[i] + k * wetR[i];
    }
  }
};

// ---------------------------------------------------------------- voice

void PianoVoice::add(float amp, float decayDb, float rat, float offHz, float phase, float dmp, float gl, float gr,
                     float stereoPhase, float onset) {
  // a component and its onset twin go in together or not at all: either alone would click.
  // Below −80 dBFS the twin is left out: so quiet a component's start can't be heard as a click.
  if (std::fabs(amp) < 1e-4f) onset = 0;
  // Listening Point's loudness: what this component adds to the note's first half second, roughly
  // (its power × how long it lasts: ½ s for a slow one, 1/k for a fast one; changes no sound)
  listenPow[curPn] += amp * amp * 0.5f * (gl * gl + gr * gr) / (2.f + 0.2302585f * std::fabs(decayDb));
  // the soft pedal: amp × (late − (late − early)·e^{−t/τ}), the second term as a copy that fades
  // faster by unaExtra (and is left out under −80 dBFS, like the onset twin)
  float back = unaExtra > 0 ? amp * (unaLate - unaEarly) : 0.f;
  if (std::fabs(back) < 1e-4f) back = 0;
  if (unaExtra > 0) amp *= back != 0 ? unaLate : unaEarly;
  int each = onset > 0 ? 2 : 1;
  if (count + each * (back != 0 ? 2 : 1) > kCapacity) return;
  short tag = tagNext;
  addOne(amp, decayDb, rat, offHz, phase, dmp, gl, gr, stereoPhase, 0);
  tagNext = 0;   // only the component itself: its twin and the pedal's copies are passing things
  if (onset > 0) addOne(-amp, decayDb, rat, offHz, phase, dmp, gl, gr, stereoPhase, onset);
  if (back != 0) {
    addOne(-back, decayDb, rat, offHz, phase, dmp, gl, gr, stereoPhase, unaExtra);
    if (onset > 0) addOne(back, decayDb, rat, offHz, phase, dmp, gl, gr, stereoPhase, unaExtra + onset);
  }
  tagNext = tag;
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
  pnum[i] = static_cast<unsigned char>(curPn);
  gPrev[i] = pnGain[curPn];
  mid[i] = tagNext;
}

// Restrike: the mode j has just been added at `at` by a new strike, and the vibration from
// before may hold the same mode (same frequency, same decay). Then the part of the old one the
// felt leaves (feltKeep) goes into the new component, which carries both from here on, and the
// old component keeps only the rest, fading as the hammer lands. Two components on one pole are
// one: left = Re(z₁)·wl₁ + Re(z₂)·wl₂ = Re(ZL), right = Re(z₁c₁ + z₂c₂) = Re(ZR) with c the complex
// gain to the right mic; so z = ZL / wl and c = ZR / z, with wl chosen to keep wl² + |c|² = 2 as
// every component has (so |z|² stays the power, for the floor and the voice's end).
void PianoVoice::foldOld(int j, int at, int old, float fade) {
  int i = synth.modeSlot[j];
  if (i < 0 || i >= old || mid[i] != j + 1) return;
  if (ratio[i] != ratio[at] || offset[i] != offset[at] || decay[i] != decay[at] || damp[i] != damp[at]) return;
  float k = feltKeep[pnum[at]];
  const double kd = k, ar = re[at], ai = im[at];
  double kr = kd * re[i], ki = kd * im[i];
  double zlr = ar * wl[at] + kr * wl[i], zli = ai * wl[at] + ki * wl[i];
  // z·c with c = wrr − j·wri
  double zrr = ar * wrr[at] + ai * wri[at] + kr * wrr[i] + ki * wri[i];
  double zri = ai * wrr[at] - ar * wri[at] + ki * wrr[i] - kr * wri[i];
  double pl = zlr * zlr + zli * zli, prw = zrr * zrr + zri * zri;
  if (pl + prw > 1e-24) {
    if (prw > 1e6 * pl) {   // the left mic's parts cancel: the right carries it
      re[at] = static_cast<float>(zrr * 0.70710678);
      im[at] = static_cast<float>(zri * 0.70710678);
      wl[at] = 0;
      wrr[at] = 1.41421356f;
      wri[at] = 0;
    } else {
      double w = std::sqrt(2.0 / (1.0 + prw / pl));
      double zr = zlr / w, zi = zli / w, n = zr * zr + zi * zi;
      re[at] = static_cast<float>(zr);
      im[at] = static_cast<float>(zi);
      wl[at] = static_cast<float>(w);
      wrr[at] = static_cast<float>((zrr * zr + zri * zi) / n);    // c = ZR · conj(z) / |z|²
      wri[at] = static_cast<float>(-(zri * zr - zrr * zi) / n);
    }
  }
  re[i] *= 1 - k;
  im[i] *= 1 - k;
  extra[i] = std::max(extra[i], fade);
  mid[i] = -1;
  synth.modeSlot[j] = -1;
}

void PianoVoice::start(const NoteOn& n, const StartInfo& s) {
  if (n.note != note) {   // a different key in this slot: start clean
    count = 0;
    knocking = false;
    for (auto& ch : nzEnv) for (float& e : ch) e = 0;
    for (auto& ch : nzEnv2) for (float& e : ch) e = 0;
    note = n.note;
  }
  damped = false;
  arpVoice = s.arp;
  if (arpVoice) synth.arpStarted(n.note);
  // The strike needs parameter values, which a voice reads in render(): it
  // happens at the start of the next render, the same sample.
  pendingVel = n.vel;
  pending = true;
}

void PianoVoice::strike(VoiceContext& ctx, float vel) {
  lastPitch = -1;         // recompute every pole
  for (float& v : listenPow) v = 0;
  listenStruck = true;
  // the arpeggiator starts its pattern again with each strike; its own random stream, so the
  // voice's (the beats, the noise) is the same with it on or off
  arpPh = 0;
  arpNext = 0;
  seqKey = -1;
  arpRng = 0x9E3779B9u * static_cast<unsigned>(note + 1) + 0x7F4A7C15u * ++strikes;

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
  float stiff = ctx.get(synth.stiffness) * 0.01f;
  // The hammer (tools/analyze_hammer.py): its force spectrum 1 / (1 + (f / fc)²), with the corner
  // measured per key and layer from how the partials brighten as the felt is struck harder.
  // Hardness moves the corner (a harder felt, a shorter contact: ±3 octaves at ±100 %), and every
  // partial, and the strike, changes by the ratio of the moved spectrum to the measured one.
  // The soft pedal (una corda) shifts a grand's action sideways: the hammers meet the strings on
  // softer, less-packed felt, and from about half way down each hammer misses one string of its
  // unison (where there are three). There are no recordings of it here, so this is the physics
  // applied to what was measured, and a judgment in its sizes (kUna…):
  //   - the felt: Hardness down by kUnaFelt (the hammer's corner 1.8 octaves lower at full pedal).
  //     At 25 it was 0.3-3 % by the perceptual distance at half pedal: not to be heard;
  //   - the unison, from half pedal to full, on the keys with three strings (fading in F#2-C3):
  //     struck together, the strings move in phase, press on the bridge together and lose their
  //     energy fast (the prompt sound); with one left out, a third of the motion is out of phase,
  //     which the bridge hardly feels and which lasts (the aftersound). So each partial starts
  //     kUnaPromptDb lower (two strings of three) and comes back, as fast as its prompt sound
  //     fades, to kUnaAfterDb over its usual level: quieter and rounder at the strike, but
  //     singing on as long (Weinreich 1977). The whole partial takes that gain over time,
  //     g(t) = late − (late − early)·e^{−t/τ}, as a second, faster-fading copy of each component
  //     (PianoVoice::add): scaling its modes one by one would undo the way they were fitted to
  //     add up (C4 came out 2 dB louder at the strike that way).
  // It is read at the strike, as on a piano: notes already ringing keep their tone.
  constexpr float kUnaFelt = 60.f, kUnaPromptDb = -3.5f, kUnaAfterDb = 2.f, kUnaMinRate = 6.f, kUnaMaxRate = 60.f;
  float una = std::clamp(ctx.get(synth.softPedal) * 0.01f, 0.f, 1.f);
  float unaStr = una > 0.5f ? (una - 0.5f) * 2 * std::clamp((note - 42) / 6.f, 0.f, 1.f) : 0.f;
  unaEarly = unaStr > 0 ? dbToGain(kUnaPromptDb * unaStr) : 1.f;
  unaLate = unaStr > 0 ? dbToGain(kUnaAfterDb * unaStr) : 1.f;
  unaExtra = 0;
  // (less in the top two octaves, where Hardness takes far more of a note's level: half by C7)
  float unaFelt = kUnaFelt * una * (1 - 0.5f * std::clamp((note - 72) / 24.f, 0.f, 1.f));
  float hard = ctx.get(synth.hardness) * 0.01f;
  // Uneven (docs/MODEL.md, Wear and Uneven): every key its own hammer, the same each time it is struck.
  // Two fixed numbers per key, −1…1: its voicing (below 0 a softer felt: Hardness down by as much as
  // kUnevenSoft; above 0 a harder one: Edge up by as much as kUnevenEdge, further down) and its
  // mass (kUnevenOct octaves either way). The voicing goes by the square root of Uneven × the key's
  // number: Edge does little at its low end, and half the keys' numbers are small (linear, 19 of 25
  // keys were under 7 % by the distance at Uneven 100 %). Their own hash: nothing is drawn from the
  // voice's generator.
  constexpr float kUnevenSoft = 1.f, kUnevenEdge = 1.f, kUnevenOct = 0.6f, kWearEdge = 0.9f;
  auto keyRandom = [](int key, uint32_t salt) {
    uint32_t h = static_cast<uint32_t>(key) * 2654435761u ^ salt;
    h ^= h >> 15;
    h *= 2246822519u;
    h ^= h >> 13;
    h *= 3266489917u;
    h ^= h >> 16;
    return h * (2.f / 4294967296.f) - 1.f;
  };
  float uneven = std::clamp(ctx.get(synth.hamUneven) * 0.01f, 0.f, 1.f);
  float keyVoicing = 0;
  if (uneven > 0) {
    float r = keyRandom(static_cast<int>(note), 0x9e3779b9u);
    keyVoicing = std::copysign(std::sqrt(uneven * std::fabs(r)), r);
  }
  float keyMass = uneven > 0 ? uneven * keyRandom(static_cast<int>(note), 0x85ebca6bu) : 0.f;
  if (keyVoicing < 0) hard = std::clamp(hard + kUnevenSoft * keyVoicing, -1.f, 1.f);
  auto fcAt = [&](int k) { return pd::hammerFc[k][l0] + (pd::hammerFc[k][l0 + 1] - pd::hammerFc[k][l0]) * lf; };
  float hammerFc = std::exp(std::log(fcAt(s.a)) + (std::log(fcAt(s.b)) - std::log(fcAt(s.a))) * s.w);
  if (una > 0) {
    // A soft strike's corner is already near its fundamental, and 1.8 octaves lower took the
    // fundamental with it: a soft note with the pedal came out 16 dB quieter. The pedal's felt
    // lowers the corner only as far as costs the fundamental kUnaMaxDb.
    constexpr float kUnaMaxDb = 3.f;
    float f0 = 440.f * std::exp2((note - 69) / 12.f);
    float fcUser = hammerFc * std::exp2(3.f * hard), a = f0 / fcUser;
    float fcFloor = f0 / std::sqrt(std::max((1 + a * a) * dbToGain(kUnaMaxDb) - 1, 1e-6f));
    float fcPedal = std::max(fcUser * std::exp2(-0.03f * unaFelt), std::min(fcUser, fcFloor));
    hard = std::clamp(std::log2(fcPedal / hammerFc) / 3.f, -1.f, 1.f);
  }
  float hammerFcNew = hammerFc * std::exp2(3.f * hard);
  auto hammerDb = [&](float f) {
    if (hard == 0) return 0.f;
    float a = f / hammerFc, b = f / hammerFcNew;
    return 20.f * std::log10((1 + a * a) / (1 + b * b));
  };
  // Strike Point: where along the string the hammer strikes. Partial n is excited as sin(nπx): weak
  // where it has a node under the hammer, and of opposite sign either side of one. Each measured
  // key has its own x (pd::strikeX, tools/analyze_point.py: 11.6-12.3 % in the low bass, where
  // the comb shows in the partials) and its own notch depth: past the first antinode the comb has
  // a floor, (sin² + ε²) / (1 + ε²), since the recordings' notches are 6-20 dB deep, not bottomless.
  // The control scales every key's x by setting / 12 %, and each partial changes by the ratio of
  // the moved comb to the key's own, so at 12 % nothing changes.
  float strikeScale = ctx.get(synth.strikePoint) * 0.01f / kStrikePointRef;
  bool strikeMoved = std::fabs(strikeScale - 1.f) > 1e-5f;
  auto combPower = [](float n, float x, float eps) {
    float sn = std::sin(0.5f * kTwoPi * n * x), s2 = sn * sn;
    return n * x <= 0.5f ? std::max(s2, 1e-4f) : (s2 + eps * eps) / (1 + eps * eps);
  };
  // Giving back: the notches differ from node to node (one partial 20 dB under its neighbours, the
  // next 8), so a partial rises by no more than it sits below its neighbours on the recordings
  // (pd::strikeLift), or 6 dB (the comb's broad shape). Past that it would stand clear of them, a
  // resonance no moved hammer makes. The partials before the first antinode follow sin alone.
  //
  // Strike Depth leaves that realism on purpose, two ways at once.
  // Towards an ideal string: the floor falls from the key's ε to 0.03 (notches 30 dB deep) and
  // the most a partial may rise goes from 6 to 12 dB. On its own that is subtle (1-7 % by the
  // perceptual distance, as PR #11's control was against the realistic one: a deeper notch still
  // takes one partial per node).
  // And past any string: every partial's change in dB is multiplied, up to 3× at 100 %, so the
  // notches widen as well as deepen and the partials between them stand out.
  // Loudness: a hammer further in drives the low partials much harder (+9 dB on the fundamental
  // at 50 %) and one near the end hardly at all, so the note's energy over its first half second
  // is kept at the measured piano's (pointComp): both controls change colour, not level.
  float strikeDeep = std::clamp(ctx.get(synth.strikeDepth) * 0.01f, 0.f, 1.f);
  auto pointDbAt = [&](int n, int k, int pIdx, float deep) {
    float ideal = deep, times = 1 + 2 * deep;
    float x0 = pd::strikeX[k], x = std::min(x0 * strikeScale, 0.5f);
    float eps = ideal > 0 ? pd::strikeEps[k] * std::pow(0.03f / pd::strikeEps[k], ideal) : pd::strikeEps[k];
    float db = 10.f * std::log10(combPower(n, x, eps) / combPower(n, x0, eps));
    if (n * x0 > 0.5f) db = std::min(db, std::max(static_cast<float>(pd::strikeLift[pIdx]), 6.f + 6.f * ideal));
    db = std::clamp(db, -24.f - 6.f * ideal, 24.f);
    return times > 1 ? std::clamp(db * times, -60.f, 24.f) : db;
  };
  float pointComp = 0;
  if (strikeMoved) {
    double p0 = 0, p1 = 0;
    for (const auto& side : {std::pair<int, float>{s.a, 1 - s.w}, std::pair<int, float>{s.b, s.w}}) {
      const pd::Key& kk = pd::keys[side.first];
      for (int i = kk.first; i < kk.first + kk.count; ++i) {
        // what a partial adds to the note's first half second: its modes' power at the strike
        // (its level's, without modes) times how long each lasts. At the strike alone, fast modes
        // that are gone in milliseconds would count as loudness
        const pd::Partial& Q = pd::partials[i];
        auto lasts = [](float dec) {   // ∫ 10^(dec·t/10) dt over 0.5 s, dec in dB/s (negative)
          double k = std::max(0.1f, -dec) * 0.2302585;
          return (1 - std::exp(-0.5 * k)) / k;
        };
        double g = 0;
        if (Q.nm > 0) {
          for (int j = Q.fm; j < Q.fm + Q.nm; ++j)
            for (int mic = 0; mic < 2; ++mic) {
              const auto& ML = pd::modeLevels[j];
              g += 0.5 * std::pow(10.0, 0.01f * (ML[l0][mic] + (ML[l0 + 1][mic] - ML[l0][mic]) * lf) / 10.0) * lasts(pd::modes[j].decay);
            }
        } else {
          g = 0.5 * (std::pow(10.0, layerLevel(i, 0) / 10.0) + std::pow(10.0, layerLevel(i, 1) / 10.0)) * lasts(shape(Q.s1));
        }
        g *= side.second;
        p0 += g;
        p1 += g * std::pow(10.0, pointDbAt(pd::partials[i].n, side.first, i, strikeDeep) / 10.0);
      }
    }
    if (p0 > 0 && p1 > 0) pointComp = static_cast<float>(-10.0 * std::log10(p1 / p0));
  }
  auto pointDb = [&](int n, int k, int pIdx) { return strikeMoved ? pointDbAt(n, k, pIdx, strikeDeep) + pointComp : 0.f; };
  // a partial whose node the hammer has crossed starts the other way up: its phase turns by π
  auto pointFlip = [&](int n, int k) {
    if (!strikeMoved) return false;
    float x0 = pd::strikeX[k], x = std::min(x0 * strikeScale, 0.5f);
    return std::sin(0.5f * kTwoPi * n * x) * std::sin(0.5f * kTwoPi * n * x0) < 0;
  };
  // The hammer's surface (docs/MODEL.md, The hammer's surface). A simulated hammer on this piano's
  // strings (tools/hammer_sim.py, tools/analyze_surface.py) gives the force's spectrum, in dB against
  // its level at low frequencies, as
  //   felt   −12 (f / f12)^γ              falling like a cliff: a soft surface is a smooth pulse
  //   rigid  −10 log10(1 + (f / fr)²)     6 dB per octave from a corner the hammer's mass sets
  // and each partial, and the strike, changes by what the surface changes in that spectrum:
  //   Edge    from the felt to a surface that doesn't give (a tack, bare wood): rigid − felt, by its
  //           share, no more than kSurfMaxDb. The rigid hammer's spectrum doesn't depend on its speed,
  //           so at 100 % a note is the same colour at every velocity, only louder;
  //   Core    a hard core under thin felt: Edge from a velocity up. Below it the felt alone is
  //           pressed; above, the hammer hits through (within two layers, as the simulation has it);
  //   Weight  the hammer's mass × μ: the partials under the mass's corner take μ times as much and
  //           those above it are left, a first-order shelf.
  // The note's energy over its first half second is kept (surfComp, as Strike Point's): a rigid
  // hammer drives the treble's strings far harder and a light one everything less, and these
  // controls change colour, not level. Morph on: Material sets all three along felt, worn felt,
  // leather on a light hammer, wood, metal (kMaterial: a judgment), and their own values aren't read.
  constexpr float kSurfMaxDb = 36.f, kSurfStrikeMaxDb = 24.f, kCoreLayers = 1.5f;
  float sEdge, sCore, sMu;
  if (ctx.on(synth.hamMorph)) {
    // Edge %, Core %, log2 of Weight at Material 0, 25, 50, 75, 100 %
    static constexpr float kMaterial[5][3] = {{0, 0, 0}, {10, 45, 0}, {50, 45, -1}, {85, 70, -1.5f}, {100, 100, 0}};
    float m = std::clamp(ctx.get(synth.hamMaterial) * 0.04f, 0.f, 4.f);
    int mi = std::min(static_cast<int>(m), 3);
    float mf = m - mi;
    auto at = [&](int j) { return kMaterial[mi][j] + (kMaterial[mi + 1][j] - kMaterial[mi][j]) * mf; };
    sEdge = at(0) * 0.01f;
    sCore = at(1) * 0.01f;
    sMu = std::exp2(at(2));
  } else {
    sEdge = std::clamp(ctx.get(synth.hamEdge) * 0.01f, 0.f, 1.f);
    sCore = std::clamp(ctx.get(synth.hamCore) * 0.01f, 0.f, 1.f);
    sMu = std::clamp(ctx.get(synth.hamWeight) * 0.01f, 0.25f, 4.f);
  }
  // Wear: felt packed hard by use is a stiffer felt, which is Edge (the simulation's felt ten times
  // stiffer is a rigid surface but for 1-3 dB): up to kWearEdge at D4, a third of that at the ends of
  // the keyboard, which are played least. Uneven's harder keys and each key's mass go on here too.
  float wear = std::clamp(ctx.get(synth.hamWear) * 0.01f, 0.f, 1.f);
  if (wear > 0) {
    float away = (note - 62) / 17.f;
    sEdge += kWearEdge * std::sqrt(wear) * (0.35f + 0.65f * std::exp(-away * away));   // (the root: as Uneven's)
  }
  if (keyVoicing > 0) sEdge += kUnevenEdge * keyVoicing;
  if (wear > 0 || keyVoicing > 0) sEdge = std::min(sEdge, 1.f);
  if (keyMass != 0) sMu = std::clamp(sMu * std::exp2(kUnevenOct * keyMass), 0.25f, 4.f);
  if (std::fabs(sMu - 1.f) < 1e-4f) sMu = 1;
  if (sCore > 0) {   // the layer from which the core is reached: none at 0 %, every one at 100 %
    float from = (pd::kLayers - 1) - (pd::kLayers + 5) * sCore;
    if (lp > from) sEdge += (1 - sEdge) * (1 - std::exp(-(lp - from) / kCoreLayers));
  }
  bool surfOn = sEdge > 0 || sMu != 1;
  float surfF12 = (pd::surfF12[l0] + (pd::surfF12[l0 + 1] - pd::surfF12[l0]) * lf) * std::exp(pd::surfKeySlope * (note - 60));
  float surfGamma = std::clamp(pd::surfGamma[l0] + (pd::surfGamma[l0 + 1] - pd::surfGamma[l0]) * lf + pd::surfGammaSlope * (note - 60), 0.7f, 2.f);
  float surfMassHz = pd::surfMassHz * std::exp(pd::surfMassSlope * (note - 60));
  auto rigidOverFelt = [&](float f) {   // dB
    float r = f / pd::surfRigidHz;
    return 12.f * std::pow(f / surfF12, surfGamma) - 10.f * std::log10(1 + r * r);
  };
  auto surfDbAt = [&](float f) {
    float db = 0;
    if (sEdge > 0) db += sEdge * std::clamp(rigidOverFelt(f), -6.f, kSurfMaxDb);
    if (sMu != 1) {
      float a = f / surfMassHz;
      db += 20.f * std::log10(sMu) + 10.f * std::log10((1 + a * a) / (1 + sMu * sMu * a * a));
    }
    return db;
  };
  float surfComp = 0;
  if (surfOn) {
    double p0 = 0, p1 = 0;
    float f0n = 440.f * std::exp2((note - 69) / 12.f);
    for (const auto& side : {std::pair<int, float>{s.a, 1 - s.w}, std::pair<int, float>{s.b, s.w}}) {
      const pd::Key& kk = pd::keys[side.first];
      for (int i = kk.first; i < kk.first + kk.count; ++i) {
        // as pointComp: each partial's power at the strike times how long it lasts in half a second
        const pd::Partial& Q = pd::partials[i];
        auto lasts = [](float dec) {
          double k = std::max(0.1f, -dec) * 0.2302585;
          return (1 - std::exp(-0.5 * k)) / k;
        };
        double g = 0;
        if (Q.nm > 0) {
          for (int j = Q.fm; j < Q.fm + Q.nm; ++j)
            for (int mic = 0; mic < 2; ++mic) {
              const auto& ML = pd::modeLevels[j];
              g += 0.5 * std::pow(10.0, 0.01f * (ML[l0][mic] + (ML[l0 + 1][mic] - ML[l0][mic]) * lf) / 10.0) * lasts(pd::modes[j].decay);
            }
        } else {
          g = 0.5 * (std::pow(10.0, layerLevel(i, 0) / 10.0) + std::pow(10.0, layerLevel(i, 1) / 10.0)) * lasts(shape(Q.s1));
        }
        g *= side.second * std::pow(10.0, hammerDb(f0n * partialRatio(Q.n, B, Q.dev, stiff)) / 10.0);
        if (strikeMoved) g *= std::pow(10.0, pointDbAt(Q.n, side.first, i, strikeDeep) / 10.0);
        p0 += g;
        p1 += g * std::pow(10.0, surfDbAt(f0n * partialRatio(Q.n, B, Q.dev, stiff)) / 10.0);
      }
    }
    if (p0 > 0 && p1 > 0) surfComp = std::clamp(static_cast<float>(-10.0 * std::log10(p1 / p0)), -24.f, 24.f);
  }
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
  float dampBase = damperRate(note, synth.topDamper);

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
  // Hardness: a harder felt leaves the string sooner, so the note swells in faster (the contact
  // time goes as 1 / the hammer's corner; the swell is partly the soundboard's, so half that)
  float hardSwell = std::exp2(-1.5f * hard);
  float tau = std::max(tauMs * ctx.get(synth.attack) * 0.01f * std::exp2(soft) * hardSwell, 0.05f) * 0.001f;
  // Up to 100 % this is the felt's doing and small (a few ms: 0-0.5 % by the perceptual distance,
  // not to be heard). The range goes on to 600 % as an effect: soft notes swell in over tens to
  // hundreds of ms, no further than kSwellMax.
  constexpr float kSwellMax = 0.3f;
  if (ctx.get(synth.softAttack) > 100.f) tau = std::min(tau, kSwellMax);
  float onset = 8.6858896f / tau;   // dB/s of the twin's extra decay: 1 − e^{−t/τ}
  // Struck again while still sounding: the old vibration hands over to the new one, fading as the
  // new strike swells in. Adding the new partials on top of the old ones would let them cancel
  // where their phases disagree, and a fast repeat could make no attack at all.
  // It fades three times as fast as the new strike swells, as the hammer's contact stops the
  // string, so the two barely overlap.
  //
  // Restrike turns that into what a string does: the new strike is added to the vibration
  // already there, less what the felt takes while it rests on the string. The felt is a brief
  // touch at the strike point, so it takes most from the partials that move most there
  // (kRestrikeFeltDb × sin²(nπx)) and nothing from those with a node under it. Partial by
  // partial the two strikes then meet in or out of step, as their timing has it: no two repeats
  // alike, and a pedalled repeat builds. At 0 % (the default) the handover above is all there is.
  const int old = count;
  const float fade = 3.f * onset;
  const float keep = std::clamp(ctx.get(synth.restrike) * 0.01f, 0.f, 1.f);
  if (keep <= 0 || old == 0) {
    for (int i = 0; i < count; ++i) extra[i] = std::max(extra[i], fade);
  } else {
    float x = std::min((pd::strikeX[s.a] + (pd::strikeX[s.b] - pd::strikeX[s.a]) * s.w) * strikeScale, 0.5f);
    for (int n = 1; n <= kMaxPartials; ++n) {
      float sn = std::sin(0.5f * kTwoPi * n * x);
      feltKeep[n] = keep * dbToGain(-kRestrikeFeltDb * sn * sn);
    }
    for (int i = 0; i < old; ++i)
      if (mid[i] > 0) synth.modeSlot[mid[i] - 1] = static_cast<short>(i);
  }
  nzRise = 1;
  nzRiseMul = std::exp(-1.f / (tau * sr));
  int layerNear = static_cast<int>(std::lround(lp));


  dsp::Rng& r = ctx.rng();
  float pitchNow = static_cast<float>(ctx.pitchHz());
  int ia = ka.first, ea = ka.first + ka.count, ib = kb.first, eb = kb.first + kb.count;
  for (int pn = 1; pn <= kMaxPartials; ++pn) {
    curPn = pn;
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

    float rat = partialRatio(pn, B, wa * A.dev + wb * Bp.dev, stiff);
    float extra = gainDb + missing + brightComp + bright * std::log2(static_cast<float>(pn)) +
                  hammerDb(pitchNow * rat) + wa * pointDb(pn, s.a, pa) + wb * pointDb(pn, s.b, pb);
    if (surfOn) extra += surfDbAt(pitchNow * rat) + surfComp;
    float Ll = wa * layerLevel(pa, 0) + wb * layerLevel(pb, 0);
    float Lr = wa * layerLevel(pa, 1) + wb * layerLevel(pb, 1);
    float logDecay = 0;   // the blended characteristic decay, log(dB/s), for the modes
    if (hasA && hasB) logDecay = wa * std::log(charDecay(pa)) + wb * std::log(charDecay(pb));
    if (hasA && hasB && kByFrequency > 0) {
      // Between measured keys, what a partial takes from each side is partly a matter of its
      // number (the hammer's spectrum, where it strikes the string) and partly of its frequency
      // (the soundboard's response and the microphones', and the strings' losses, which grow
      // with frequency). By frequency: each side's level and decay where that side has a partial
      // at this one's frequency, interpolated along its own partials.
      float fl[2], fr[2], fd[2];
      atFrequency(s.a, note, rat, l0, lf, fl[0], fr[0], fd[0]);
      atFrequency(s.b, note, rat, l0, lf, fl[1], fr[1], fd[1]);
      Ll += kByFrequency * (wa * fl[0] + wb * fl[1] - Ll);
      Lr += kByFrequency * (wa * fr[0] + wb * fr[1] - Lr);
      logDecay += kByFrequency * (wa * fd[0] + wb * fd[1] - logDecay);
    }
    Ll += extra;
    Lr += extra;
    if (std::max(Ll, Lr) < cut) continue;
    float s1 = wa * shape(A.s1) + wb * shape(Bp.s1);
    float s2 = wa * shape(A.s2) + wb * shape(Bp.s2);
    float drop = wa * shape(A.drop) + wb * shape(Bp.drop);
    float beat = wa * A.beat + wb * Bp.beat;
    float beatDb = wa * A.beatDb + wb * Bp.beatDb;
    // phases at the strike, measured per layer and mic, from the nearer measured key
    int pp = hasA && hasB ? (s.w < 0.5f ? pa : pb) : pa;
    constexpr float kPhaseStep = 3.14159265f / 127.f;
    // Strike Point: the phases' key is the one whose nodes count for the turn
    int ppKey = hasA && hasB ? (s.w < 0.5f ? s.a : s.b) : (hasA ? s.a : s.b);
    float flip = pointFlip(pn, ppKey) ? 3.14159265f : 0.f;
    float phL = pd::phases[pp][layerNear][0] * kPhaseStep;
    float stereoPhase = pd::phases[pp][layerNear][1] * kPhaseStep - phL;
    if (flip != 0) phL += flip;

    float gl = dbToGain(Ll), gr = dbToGain(Lr);
    float amp = std::sqrt(0.5f * (gl * gl + gr * gr));
    gl /= amp;
    gr /= amp;
    float dmp = dampBase * (1 + 0.03f * (pn - 1));

    float after = drop < -0.01f ? amp * dbToGain(drop) : amp;
    float prompt = amp - after;
    if (unaStr > 0) unaExtra = std::clamp(-s1, kUnaMinRate, kUnaMaxRate);   // the soft pedal: as fast as the prompt sound fades
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
    // A wide cluster (in the treble, the unison's strings tens of Hz apart, and components off to
    // the side) plays as its measured components, from the nearer measured key, instead of one
    // partial and its beat: they start together, as the hammer strikes the strings together, and
    // drift apart; each reaches the two mics at its own angle, so left and right differ.
    const pd::Partial& P = pd::partials[pp];
    if (P.nm > 0) {
      // The partial as its measured modes (tools/analyze_modes.py): the unison's strings and their
      // polarisations, each decaying at its own rate and a little apart in frequency, so the prompt
      // sound, the aftersound and the beating all come from them, as on the recordings. They come
      // from the nearer measured key; between keys, their levels follow the blended partial level
      // and their decays the blended characteristic decay.
      float shiftL = Ll - layerLevel(pp, 0), shiftR = Lr - layerLevel(pp, 1);
      float decScale = hasA && hasB ? std::exp(logDecay - std::log(charDecay(pp))) : 1.f;
      // the soft pedal's swell-back follows the partial's prompt sound: its loudest mode's decay
      if (unaStr > 0) {
        float topDb = -300.f, topDec = -20.f;
        for (int j = P.fm; j < P.fm + P.nm; ++j) {
          const auto& ML = pd::modeLevels[j];
          float lv = 0.005f * (ML[l0][0] + (ML[l0 + 1][0] - ML[l0][0]) * lf + ML[l0][1] + (ML[l0 + 1][1] - ML[l0][1]) * lf);
          if (lv > topDb) { topDb = lv; topDec = pd::modes[j].decay * decScale; }
        }
        unaExtra = std::clamp(-topDec, kUnaMinRate, kUnaMaxRate);
      }
      for (int j = P.fm; j < P.fm + P.nm; ++j) {
        const auto& ML = pd::modeLevels[j];
        float ml = 0.01f * (ML[l0][0] + (ML[l0 + 1][0] - ML[l0][0]) * lf) + shiftL;
        float mr = 0.01f * (ML[l0][1] + (ML[l0 + 1][1] - ML[l0][1]) * lf) + shiftR;
        if (std::max(ml, mr) < cut) continue;
        float ph0 = pd::modePhases[j][layerNear][0] * kPhaseStep;
        float sp = pd::modePhases[j][layerNear][1] * kPhaseStep - ph0;
        if (flip != 0) ph0 += flip;
        float g0 = dbToGain(ml), g1 = dbToGain(mr);
        float a = std::sqrt(0.5f * (g0 * g0 + g1 * g1));
        int at = count;
        tagNext = static_cast<short>(j + 1);
        add(a, pd::modes[j].decay * decScale, rat, pd::modes[j].off, ph0, dmp, g0 / a, g1 / a, sp, onset);
        tagNext = 0;
        if (keep > 0 && count > at) foldOld(j, at, old, fade);
      }
      continue;
    }
    float clHz[4], clPh[4];
    for (int j = 0; j < P.nc; ++j) {
      clHz[j] = pitchNow * rat * (std::exp2(P.cc[j] / 1200.f) - 1.f);
      clPh[j] = r.uniform(-0.6f, 0.6f);
    }
    auto part = [&](float a, float dec) {
      if (P.nc < 2) {
        withBeat(a, dec);
        return;
      }
      for (int j = 0; j < P.nc; ++j)
        if (P.cw[j] > 0) add(a * std::sqrt(P.cw[j]), dec, rat, clHz[j], phL, dmp, gl, gr, stereoPhase + clPh[j], onset);
    };
    if (prompt > amp * 1e-3f) part(prompt, s1);
    // the strike's first burst above the decay line, gone within a fraction of a second
    float x0 = wa * shape(A.x0) + wb * shape(Bp.x0), s0 = wa * shape(A.s0) + wb * shape(Bp.s0);
    if (x0 > -40.f) part(amp * dbToGain(x0), s0);
    // the measured decay lines run through the beating envelope in dB, and the mean in dB of
    // |a + b·e^{jθ}| is the level of the larger component alone: so the main components carry the
    // measured levels and the partners the beat on top
    part(after, s2);
  }
  // Restrike, for what the new strike had no place for (partials without modes, modes this
  // strike left out, the old strike's passing parts): the kept share goes on as a component of
  // its own, and the rest fades as the hammer lands. With no room left, it all fades.
  if (keep > 0)
    for (int i = 0; i < old; ++i) {
      if (mid[i] > 0) synth.modeSlot[mid[i] - 1] = -1;
      if (mid[i] >= 0) {
        float k = feltKeep[pnum[i]];
        if (count < kCapacity && (re[i] * re[i] + im[i] * im[i]) * k * k > 1e-8f) {
          int c = count++;
          re[c] = re[i] * k; im[c] = im[i] * k; pr[c] = pr[i]; pi[c] = pi[i];
          wl[c] = wl[i]; wrr[c] = wrr[i]; wri[c] = wri[i];
          ratio[c] = ratio[i]; offset[c] = offset[i]; decay[c] = decay[i]; damp[c] = damp[i]; extra[c] = extra[i];
          pnum[c] = pnum[i]; gPrev[c] = gPrev[i]; mid[c] = mid[i];
          re[i] *= 1 - k;
          im[i] *= 1 - k;
        }
        mid[i] = -1;
      }
      extra[i] = std::max(extra[i], fade);
    }
  unaEarly = unaLate = 1;
  unaExtra = 0;
  for (int i = count; i < ((count + kLanes - 1) & ~(kLanes - 1)); ++i)
    re[i] = im[i] = pr[i] = pi[i] = wl[i] = wrr[i] = wri[i] = 0;

  float hg = ctx.get(synth.hammer) + gainDb;
  // the strike as recorded, where the nearer measured key has one: its response, at the level
  // measured for this layer, mic and window, blended between the two keys where both have one
  int kn = s.w < 0.5f ? s.a : s.b, ko = kn == s.a ? s.b : s.a;
  int si = pd::strikeIndex[kn];
  if (si >= 0) {
    int so = pd::strikeIndex[ko];
    float wo = kn == s.a ? s.w : 1.f - s.w;   // the other key's weight
    auto at = [&](const short (&L)[16][2][2], int mic, int win) {
      return 0.01f * (L[l0][mic][win] + (L[l0 + 1][mic][win] - L[l0][mic][win]) * lf);
    };
    int slot = 0;
    for (int i = 0; i < kStrikeSlots; ++i) {
      if (!plays[i].on) { slot = i; break; }
      if (plays[i].pos > plays[slot].pos) slot = i;   // all busy: the one furthest along
    }
    StrikePlay& p = plays[slot];
    p.on = true;
    p.damped = false;
    p.index = si;
    p.step = static_cast<float>(pd::kStrikeRate) / sr;
    p.pos = -static_cast<float>(pd::strikeStart[si]);
    p.env = 1;
    p.envMul = 1;
    // brightness: the felt's, a first-order high shelf with its gain for this layer
    float shelfDb = 0.01f * (pd::strikeShelf[si][l0] + (pd::strikeShelf[si][l0 + 1] - pd::strikeShelf[si][l0]) * lf);
    float g = dbToGain(shelfDb), K = std::tan(0.5f * kTwoPi * std::min(pd::strikeFc[si], 0.45f * sr) / sr);
    p.b0 = (g + K) / (1 + K);
    p.b1 = (K - g) / (1 + K);
    p.a1 = (K - 1) / (1 + K);
    // Hardness: the strike is the body's answer to the hammer's force, so it changes as the
    // partials do (the same ratio of spectra)
    p.hard = hard != 0;
    if (p.hard) {
      float K1 = std::tan(0.5f * kTwoPi * std::min(hammerFc, 0.45f * sr) / sr);
      float K2 = std::tan(0.5f * kTwoPi * std::min(hammerFcNew, 0.45f * sr) / sr);
      p.hb0 = K2 / K1 * (K1 + 1) / (K2 + 1);
      p.hb1 = K2 / K1 * (K1 - 1) / (K2 + 1);
      p.ha1 = (K2 - 1) / (K2 + 1);
      for (auto& v : p.hx) v[0] = v[1] = 0;
      for (auto& v : p.hy) v[0] = v[1] = 0;
    }
    // The hammer's surface: the strike changes as the partials do, as near as a few sections come.
    // Edge and Core: two high shelves that together reach Edge × kSurfStrikeMaxDb (less than the
    // partials' most: the response holds its recording's floor too), centred where rigid − felt is
    // half that. Weight: its shelf exactly. And the gain that keeps the note's loudness.
    p.surf = 0;
    if (surfOn) {
      if (sEdge > 0) {
        float lo = std::log(100.f), hi = std::log(0.45f * sr);
        for (int it = 0; it < 24; ++it) {
          float mid = 0.5f * (lo + hi);
          (rigidOverFelt(std::exp(mid)) < 0.5f * kSurfStrikeMaxDb ? lo : hi) = mid;
        }
        float A = std::pow(10.f, sEdge * 0.5f * kSurfStrikeMaxDb / 40.f), w0 = kTwoPi * std::exp(0.5f * (lo + hi)) / sr;
        float cs = std::cos(w0), al = std::sin(w0) * 0.70710678f, sq = 2 * std::sqrt(A) * al;
        float a0 = (A + 1) - (A - 1) * cs + sq;
        for (int k = 0; k < 2; ++k) {
          float* c = p.sc[p.surf++];
          c[0] = A * ((A + 1) + (A - 1) * cs + sq) / a0;
          c[1] = -2 * A * ((A - 1) + (A + 1) * cs) / a0;
          c[2] = A * ((A + 1) + (A - 1) * cs - sq) / a0;
          c[3] = 2 * ((A - 1) - (A + 1) * cs) / a0;
          c[4] = ((A + 1) - (A - 1) * cs - sq) / a0;
        }
      }
      if (sMu != 1) {
        float K = std::tan(0.5f * kTwoPi * std::min(surfMassHz, 0.45f * sr) / sr);
        float* c = p.sc[p.surf++];
        c[0] = sMu * (K + 1) / (K + sMu);
        c[1] = sMu * (K - 1) / (K + sMu);
        c[2] = 0;
        c[3] = (K - sMu) / (K + sMu);
        c[4] = 0;
      }
      p.sg = dbToGain(surfComp);
      for (auto& sec : p.sz)
        for (auto& v : sec) v[0] = v[1] = 0;
    }
    for (int mic = 0; mic < 2; ++mic) {
      p.x1[mic] = p.y1[mic] = 0;
      float gw[2];
      for (int win = 0; win < 2; ++win) {
        float db = at(pd::strikeGain[si], mic, win);
        if (so >= 0) {   // between two keys with a response: the level blended, the nearer key's response
          float own = at(pd::strikeLevels[si], mic, win);
          db += (own + (at(pd::strikeLevels[so], mic, win) - own) * wo) - own;
        }
        gw[win] = dbToGain(db + hg) * pd::strikeScale[si];
      }
      p.g1[mic] = gw[0];
      p.g2[mic] = gw[1];
    }
    striking = true;
  }
  // otherwise the strike's noise, measured per key, layer, mic and band
  for (int bd = 0; si < 0 && bd < pd::kNoiseBands; ++bd) {
    if (synth.bands[bd].norm == 0) continue;
    for (int mic = 0; mic < 2; ++mic) {
      auto lvl = [&](int k) {
        const auto& L = pd::noiseLevels[k];
        return 0.01f * (L[l0][mic][bd] + (L[l0 + 1][mic][bd] - L[l0][mic][bd]) * lf);
      };
      float db = lvl(s.a) + (lvl(s.b) - lvl(s.a)) * s.w + hg;
      float drop = pd::noiseDrop[s.a][bd] + (pd::noiseDrop[s.b][bd] - pd::noiseDrop[s.a][bd]) * s.w;
      float second = dbToGain(drop);
      if (db > -110) {
        nzEnv[mic][bd] += dbToGain(db) * synth.bands[bd].norm * (1 - second);
        nzEnv2[mic][bd] += dbToGain(db) * synth.bands[bd].norm * second;
      }
    }
    float dec = pd::noiseDecay[s.a][bd] + (pd::noiseDecay[s.b][bd] - pd::noiseDecay[s.a][bd]) * s.w;
    float dec2 = pd::noiseDecay2[s.a][bd] + (pd::noiseDecay2[s.b][bd] - pd::noiseDecay2[s.a][bd]) * s.w;
    nzMul[bd] = std::pow(10.f, dec * 0.05f / sr);
    nzMul2[bd] = std::pow(10.f, dec2 * 0.05f / sr);
    nzDec2[bd] = dec2;
  }
  if (si < 0) {
    knocking = true;
    nzDamped = false;
  }

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

// ---------------------------------------------------------------- the partial arpeggiator

namespace {
// Whether harmonic n's pitch class (relative to the note) is in the scale: within ±35 cents of one
// of its degrees. The 7th harmonic (31 cents flat of the minor seventh) counts; the 11th and 13th,
// halfway between semitones, don't.
bool inScale(int n, int scale) {
  if (scale == 3) return true;               // all harmonics
  if (scale == 4) return (n & 1) == 1;        // odd harmonics
  float c = 1200.f * std::log2(static_cast<float>(n));
  float semi = std::round(c / 100.f);
  if (std::fabs(c - 100.f * semi) > 35.f) return false;
  int pc = static_cast<int>(semi) % 12;
  static constexpr unsigned kSets[3] = {
      (1u << 0) | (1u << 2) | (1u << 4) | (1u << 7) | (1u << 9),    // major pentatonic
      (1u << 0) | (1u << 3) | (1u << 5) | (1u << 7) | (1u << 10),   // minor pentatonic
      (1u << 0) | (1u << 4) | (1u << 7)};                           // major triad
  return (kSets[scale] >> pc) & 1u;
}
}  // namespace

namespace {
int oddPart(int n) {
  while (n > 0 && (n & 1) == 0) n >>= 1;
  return n;
}
}  // namespace

// What the pattern walks through, ascending: the partials in the scale, between Lowest and
// Highest, that this voice is playing (a key's top partials can be missing or above Nyquist).
// Grouped by octaves, a step is a pitch class instead (named by its odd harmonic: 1 for the
// note's own, 3 for its fifth, 5 for its third…), in scale order, and features every partial of
// it in range: E is 5, 10 and 20 together.
void PianoVoice::arpSequence(int scale, int lo, int hi, bool octaves) {
  bool has[kMaxPartials + 1] = {};
  for (int i = 0; i < count; ++i) has[pnum[i]] = true;
  seqN = 0;
  for (int n = 0; n <= kMaxPartials; ++n) inSeq[n] = false;
  for (int n = std::max(lo, 1); n <= std::min(hi, kMaxPartials); ++n) {
    if (!has[n] || !inScale(n, scale)) continue;
    inSeq[n] = true;
    if (!octaves) {
      seq[seqN++] = n;
      continue;
    }
    int o = oddPart(n);
    bool seen = false;
    for (int i = 0; i < seqN; ++i) seen = seen || seq[i] == o;
    if (!seen) seq[seqN++] = o;
  }
  if (octaves) {   // scale order: by the pitch class's cents above the note
    auto pc = [](int o) { float c = 1200.f * std::log2(static_cast<float>(o)); return c - 1200.f * std::floor(c / 1200.f); };
    for (int i = 1; i < seqN; ++i)
      for (int j = i; j > 0 && pc(seq[j]) < pc(seq[j - 1]); --j) std::swap(seq[j], seq[j - 1]);
  }
}

// One control block: advance the pattern and set pnGain, every partial's gain at the block's end.
// Step k starts at k (in steps since the strike) and its partial swells and falls back as
// sin²(π x / W) over W = 1 + Overlap steps, so at 100 % one partial is rising while the last one
// falls (and the two sum to a steady level), at 0 % each has the step to itself, at 200 % three
// sound at once. A featured partial goes from Rest to Boost (dB, against its natural level); the
// sequence's other partials sit at Rest; partials outside the sequence are left alone.
void PianoVoice::arpBlock(VoiceContext& ctx, int frames, int pattern) {
  for (float& g : pnGain) g = 1;
  if (pattern == 0) return;
  int scale = ctx.choice(synth.arpScale);
  int lo = static_cast<int>(ctx.get(synth.arpLow) + 0.5f), hi = static_cast<int>(ctx.get(synth.arpHigh) + 0.5f);
  bool octaves = ctx.choice(synth.arpGroup) == 1;
  int key = (((scale * 128 + lo) * 128 + hi) * 8 + pattern) * 2 + (octaves ? 1 : 0);
  if (key != seqKey) {
    seqKey = key;
    arpSequence(scale, lo, hi, octaves);
  }
  float rate;   // steps per second
  if (ctx.on(synth.arpSync)) {
    static constexpr float kBeats[7] = {2.f, 1.f, 0.5f, 1.f / 3.f, 0.25f, 1.f / 6.f, 0.125f};
    float bpm = static_cast<float>(ctx.transport().bpm);
    rate = std::max(bpm, 1.f) / 60.f / kBeats[std::clamp(ctx.choice(synth.arpDivision), 0, 6)];
  } else {
    rate = ctx.get(synth.arpRate);
  }
  arpPh += static_cast<double>(frames) * rate / sr;
  float ph = static_cast<float>(arpPh);
  int now = static_cast<int>(std::floor(ph));
  while (arpNext <= now) {   // choose each new step's partial as it begins
    int k = arpNext++, n = seqN, pick = 0;
    if (n > 0) {
      int idx = 0;
      if (pattern == 1) {
        idx = k % n;
      } else if (pattern == 2) {
        idx = n - 1 - k % n;
      } else if (pattern == 3) {
        int per = std::max(2 * n - 2, 1);
        int i = k % per;
        idx = i < n ? i : per - i;
      } else {   // random, never the same partial twice running
        arpRng = arpRng * 1664525u + 1013904223u;
        idx = static_cast<int>((arpRng >> 8) % static_cast<unsigned>(n));
        if (n > 1 && seq[idx] == arpHist[(k - 1) & 3] && k > 0) idx = (idx + 1) % n;
      }
      pick = seq[std::min(idx, n - 1)];
    }
    arpHist[k & 3] = static_cast<unsigned char>(pick);
  }
  float W = 1.f + std::clamp(ctx.get(synth.arpOverlap), 0.f, 200.f) * 0.01f;
  float boost = ctx.get(synth.arpBoost), rest = ctx.get(synth.arpOthers);
  float width = std::clamp(ctx.get(synth.arpWidth), 0.f, 1.f);   // octaves either side
  // e: how far each partial is into its swell (0…1). A step features its partial, or (Octaves)
  // every partial of its pitch class in the pattern; Width spreads each featured partial to its
  // neighbours within ±Width octaves, falling as cos² of the distance.
  float e[kMaxPartials + 1] = {};
  for (int k = std::max(0, static_cast<int>(std::floor(ph - W)) + 1); k <= now; ++k) {
    int p = arpHist[k & 3];
    if (p == 0) continue;
    float sw = std::sin(3.14159265f * (ph - static_cast<float>(k)) / W);
    sw *= sw;
    for (int c = 1; c <= kMaxPartials; ++c) {
      if (octaves ? !(inSeq[c] && oddPart(c) == oddPart(p)) : c != p) continue;
      e[c] = std::max(e[c], sw);
      if (width <= 0) continue;
      float lo2 = static_cast<float>(c) * std::exp2(-width), hi2 = static_cast<float>(c) * std::exp2(width);
      for (int q = std::max(1, static_cast<int>(std::ceil(lo2))); q <= std::min(kMaxPartials, static_cast<int>(hi2)); ++q) {
        if (q == c) continue;
        float w = std::cos(1.5707963f * std::log2(static_cast<float>(q) / static_cast<float>(c)) / width);
        e[q] = std::max(e[q], sw * w * w);
      }
    }
  }
  // The pattern's partials go from Rest to Boost; a neighbour Width reaches outside the pattern
  // goes from its own level to Boost; the rest are left alone.
  for (int n = 1; n <= kMaxPartials; ++n) {
    if (inSeq[n])
      pnGain[n] = dbToGain(rest + (boost - rest) * e[n]);
    else if (e[n] > 0)
      pnGain[n] = dbToGain(boost * e[n]);
  }
}

void PianoVoice::release() {
  // the body's ring is damped by the synth, which follows keys and pedal in processEvents; damping
  // it here too could undo that (a release that comes before the strike knows no damper rate yet)
  damped = true;
}

void PianoVoice::updatePoles(float pitchHz, float sustainScale, float unisonScale, float damperScale, float gamma) {
  float nyq = 0.47f * sr;
  for (int i = 0; i < count; ++i) {
    float f = pitchHz * ratio[i] + offset[i] * unisonScale;
    float db = overtoneDecayDb(decay[i], ratio[i], gamma) / sustainScale - extra[i] - (damped ? damp[i] / damperScale : 0.f) -
               touchDb[pnum[i]];
    float rad = f < nyq ? std::pow(10.f, db * 0.05f / sr) : 0.f;
    float w = kTwoPi * f / sr;
    pr[i] = rad * std::cos(w);
    pi[i] = rad * std::sin(w);
  }
}

// Touch Point, once per control block: the finger's damping per partial number, worked out again
// when Press, Point or Width has moved. True when it changed (the poles then follow). A partial's
// components (its modes, their onset twins, the soft pedal's copies) all take the same extra
// decay, so the partial keeps its shape and simply fades faster. Not scaled by Sustain: it is the
// finger's, not the string's.
bool PianoVoice::touchBlock(VoiceContext& ctx) {
  float press = ctx.get(synth.touchPress);
  if (press <= 0 && lastTouchPress <= 0) return false;   // no finger, now or last block
  float y = std::clamp(ctx.get(synth.touchPoint) * 0.01f, 0.02f, 0.5f);
  float w = std::clamp(ctx.get(synth.touchWidth) * 0.01f, 0.f, 0.3f);
  bool snap = ctx.on(synth.touchSnap);
  if (press == lastTouchPress && y == lastTouchPoint && w == lastTouchWidth && snap == lastTouchSnap) return false;
  lastTouchPress = press; lastTouchPoint = y; lastTouchWidth = w; lastTouchSnap = snap;
  // Snap: the nearest point that divides the string into whole parts (1/2, 1/3, 1/4…), where
  // a light finger leaves a harmonic
  if (snap) y = 1.f / std::clamp(std::round(1.f / y), 2.f, 50.f);
  for (int n = 1; n <= kMaxPartials; ++n) touchDb[n] = press > 0 ? press * touchShare(n, y, w) : 0.f;
  return true;
}

// Listening Point, once per control block: where along the string the sound is taken, as a
// pickup would take it. At the fraction y the string's partial n moves by sin(nπy), signed (it
// turns over past each of its nodes), and a pickup w of the string wide averages that to
// sin(nπy)·sinc(nw). Depth blends this with the piano's own sound, which is the bridge's: every
// partial at 1. The blend is scaled so the note's energy over its first half second stays what it
// was (by no more than 12 dB): the point changes colour, not level. It scales what each component
// sends to the mics (the Partial Arp's path), never the vibration, so it can move while notes
// ring and nothing is lost. False at Depth 0.
bool PianoVoice::listenBlock(VoiceContext& ctx) {
  float d = std::clamp(ctx.get(synth.listenDepth) * 0.01f, 0.f, 1.f);
  if (d <= 0) {
    lastListenDepth = 0;
    return false;
  }
  float y = std::clamp(ctx.get(synth.listenPoint) * 0.01f, 0.02f, 0.5f);
  float w = std::clamp(ctx.get(synth.listenWidth) * 0.01f, 0.f, 0.2f);
  if (d == lastListenDepth && y == lastListenPoint && w == lastListenWidth && !listenStruck) return true;
  lastListenDepth = d; lastListenPoint = y; lastListenWidth = w; listenStruck = false;
  double p0 = 0, p1 = 0;
  // sin(nθ) = 2 cos θ · sin((n−1)θ) − sin((n−2)θ), for the point and for the width: two cosines
  // and two sines a block while the point moves, not two sines a partial
  const float ty = 0.5f * kTwoPi * y, tw = 0.5f * kTwoPi * w;
  const float cy = 2 * std::cos(ty), cw = 2 * std::cos(tw);
  float sy0 = 0, sy1 = std::sin(ty), sw0 = 0, sw1 = std::sin(tw);
  for (int n = 1; n <= kMaxPartials; ++n) {
    float a = tw * n;
    float g = (1 - d) + d * sy1 * (a > 1e-4f ? sw1 / a : 1.f);
    float ny = cy * sy1 - sy0, nw = cw * sw1 - sw0;
    sy0 = sy1; sy1 = ny; sw0 = sw1; sw1 = nw;
    listenGain[n] = g;
    p0 += listenPow[n];
    p1 += static_cast<double>(listenPow[n]) * g * g;
  }
  float comp = p0 > 0 && p1 > 0 ? static_cast<float>(std::min(std::sqrt(p0 / p1), 4.0)) : 1.f;
  for (int n = 1; n <= kMaxPartials; ++n) listenGain[n] *= comp;
  return true;
}

void PianoVoice::cull() {
  // Finished: the whole voice is below kVoiceEndDb (each component's gains to the two mics are
  // normalised around 1, so |z|² summed is close to the output power)
  float total = 0;
  for (int k = 0; k < count; ++k) total += re[k] * re[k] + im[k] * im[k];
  if (count > 0 && total < dbToGain(kVoiceEndDb) * dbToGain(kVoiceEndDb)) {
    for (int k = 0; k < count; ++k) {
      re[k] = im[k] = pr[k] = pi[k] = wl[k] = wrr[k] = wri[k] = 0;
      gPrev[k] = 1;
    }
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
      pnum[i] = pnum[j]; gPrev[i] = gPrev[j]; mid[i] = mid[j];
      re[j] = im[j] = pr[j] = pi[j] = wl[j] = wrr[j] = wri[j] = 0;
      gPrev[j] = 1;
    } else {
      ++i;
    }
  }
}

void PianoVoice::render(VoiceContext& ctx, Buffer& out) {
  if (arpVoice && !damped && note >= 0) synth.arpUp[note & 127] = true;   // holds its damper up
  struckFrom = -1;
  if (pending) {
    pending = false;
    struckFrom = count;
    strike(ctx, pendingVel);
  }
  float pitch = static_cast<float>(ctx.pitchHz());
  float sus = ctx.get(synth.sustain) * 0.01f;
  float uni = ctx.get(synth.unison) * 0.01f;
  float dmp = ctx.get(synth.damper) * 0.01f;
  float gam = ctx.get(synth.overtoneDecay) * 0.01f;
  bool touched = touchBlock(ctx);
  if (lastTouchPress > 0) {   // Dry: half the finger's damping (a wide finger's share) at 100 %
    float dry = ctx.get(synth.touchDry) * 0.01f;
    if (dry > 0) synth.touchBody(note, 0.5f * lastTouchPress * dry);
  }
  if (pitch != lastPitch || sus != lastSustain || uni != lastUnison || dmp != lastDamper || damped != lastDamped ||
      gam != lastGamma || touched) {
    updatePoles(pitch, sus, uni, dmp, gam);
    lastPitch = pitch; lastSustain = sus; lastUnison = uni; lastDamper = dmp; lastDamped = damped; lastGamma = gam;
  }

  const int frames = out.frames;
  const int padded = (count + kLanes - 1) & ~(kLanes - 1);
  int arpPattern = ctx.choice(synth.arpPattern);
  bool listening = listenBlock(ctx);
  if (arpPattern > 0 || listening || gainsLive) {
    arpBlock(ctx, frames, arpPattern);
    if (listening) {
      for (int n = 1; n <= kMaxPartials; ++n) pnGain[n] *= listenGain[n];
      // a strike's new components start at their gains (they were added at last block's, or at 1)
      if (struckFrom >= 0)
        for (int i = struckFrom; i < count; ++i) gPrev[i] = pnGain[pnum[i]];
    }
    gainsLive = arpPattern > 0 || listening;   // off: this block ramps every gain back to 1, then the plain path
    const float inv = 1.f / static_cast<float>(std::max(frames, 1));
    for (int c = 0; c < padded; c += kLanes) {
      float r[kLanes], m[kLanes], a[kLanes], b[kLanes], gl[kLanes], gr[kLanes], gi[kLanes], g[kLanes], dg[kLanes];
      for (int k = 0; k < kLanes; ++k) {
        r[k] = re[c + k]; m[k] = im[c + k]; a[k] = pr[c + k]; b[k] = pi[c + k];
        gl[k] = wl[c + k]; gr[k] = wrr[c + k]; gi[k] = wri[c + k];
        float to = c + k < count ? pnGain[pnum[c + k]] : 1.f;
        g[k] = gPrev[c + k];
        dg[k] = (to - g[k]) * inv;
        gPrev[c + k] = to;
      }
      for (int i = 0; i < frames; ++i) {
        float ol[kLanes], orr[kLanes];
        for (int k = 0; k < kLanes; ++k) {
          float nr = r[k] * a[k] - m[k] * b[k];
          float ni = r[k] * b[k] + m[k] * a[k];
          r[k] = nr;
          m[k] = ni;
          g[k] += dg[k];
          ol[k] = nr * gl[k] * g[k];
          orr[k] = (nr * gr[k] + ni * gi[k]) * g[k];
        }
        float sl = ((ol[0] + ol[1]) + (ol[2] + ol[3])) + ((ol[4] + ol[5]) + (ol[6] + ol[7]));
        float sr2 = ((orr[0] + orr[1]) + (orr[2] + orr[3])) + ((orr[4] + orr[5]) + (orr[6] + orr[7]));
        out.left[i] += sl;
        out.right[i] += sr2;
      }
      for (int k = 0; k < kLanes; ++k) { re[c + k] = r[k]; im[c + k] = m[k]; }
    }
  } else
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

  if (striking) renderStrikes(out, frames, damperRate(note, synth.topDamper) / std::max(dmp, 0.25f));

  if (knocking) {
    if (damped && !nzDamped) {   // the key is up: the knock's slower part stops with the strings
      nzDamped = true;
      float rate = damperRate(note, synth.topDamper) / std::max(dmp, 0.25f);
      for (int bd = 0; bd < pd::kNoiseBands; ++bd) nzMul2[bd] = std::pow(10.f, (nzDec2[bd] - rate) * 0.05f / sr);
    }
    dsp::Rng& rnd = ctx.rng();
    // bands that have faded below −100 dB are skipped (and their filters cleared)
    bool live[pd::kNoiseBands];
    for (int bd = 0; bd < pd::kNoiseBands; ++bd) {
      float g = std::max(nzEnv[0][bd] + nzEnv2[0][bd], nzEnv[1][bd] + nzEnv2[1][bd]) /
                std::max(synth.bands[bd].norm, 1e-9f);
      live[bd] = g > 1e-5f;
      if (!live[bd] && g > 0) {
        nzEnv[0][bd] = nzEnv[1][bd] = nzEnv2[0][bd] = nzEnv2[1][bd] = 0;
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
        l += Piano::bandStep(band, xl, nzS[0][bd]) * (nzEnv[0][bd] + nzEnv2[0][bd]);
        r += Piano::bandStep(band, xr, nzS[1][bd]) * (nzEnv[1][bd] + nzEnv2[1][bd]);
        nzEnv[0][bd] *= nzMul[bd];
        nzEnv[1][bd] *= nzMul[bd];
        nzEnv2[0][bd] *= nzMul2[bd];
        nzEnv2[1][bd] *= nzMul2[bd];
      }
      out.add(i, l * rise, r * rise);
    }
    float peak = 0;
    for (int bd = 0; bd < pd::kNoiseBands; ++bd)
      peak = std::max(peak, std::max(nzEnv[0][bd] + nzEnv2[0][bd], nzEnv[1][bd] + nzEnv2[1][bd]));
    if (peak <= 0) {
      knocking = false;
      for (int bd = 0; bd < pd::kNoiseBands; ++bd) {
        nzEnv[0][bd] = nzEnv[1][bd] = nzEnv2[0][bd] = nzEnv2[1][bd] = 0;
        for (float& v : nzS[0][bd]) v = 0;
        for (float& v : nzS[1][bd]) v = 0;
      }
    }
  }
}

void PianoVoice::renderStrikes(Buffer& out, int frames, float damperDbPerS) {
  constexpr float kCross0 = 0.03f * pd::kStrikeRate, kCross1 = 0.05f * pd::kStrikeRate;
  bool any = false;
  for (StrikePlay& p : plays) {
    if (!p.on) continue;
    if (damped && !p.damped) {   // the key is up: what's left of the strike stops with the strings
      p.damped = true;
      p.envMul = std::pow(10.f, -damperDbPerS * 0.05f / sr);
    }
    const short* dl = pd::strikeData[p.index][0];
    const short* dr = pd::strikeData[p.index][1];
    for (int i = 0; i < frames; ++i) {
      float pos = p.pos;
      p.pos += p.step;
      if (pos < 0) continue;
      int j = static_cast<int>(pos);
      if (j + 1 >= pd::kStrikeLen || p.env < 1e-5f) {
        p.on = false;
        break;
      }
      float fr = pos - static_cast<float>(j);
      float w = std::clamp((pos - kCross0) / (kCross1 - kCross0), 0.f, 1.f);
      float xl = (dl[j] + (dl[j + 1] - dl[j]) * fr) * (p.g1[0] + (p.g2[0] - p.g1[0]) * w) * p.env;
      float xr = (dr[j] + (dr[j + 1] - dr[j]) * fr) * (p.g1[1] + (p.g2[1] - p.g1[1]) * w) * p.env;
      float l = p.b0 * xl + p.b1 * p.x1[0] - p.a1 * p.y1[0];
      float r = p.b0 * xr + p.b1 * p.x1[1] - p.a1 * p.y1[1];
      p.x1[0] = xl; p.y1[0] = l;
      p.x1[1] = xr; p.y1[1] = r;
      if (p.hard) {
        for (int st = 0; st < 2; ++st) {
          float nl = p.hb0 * l + p.hb1 * p.hx[st][0] - p.ha1 * p.hy[st][0];
          float nr = p.hb0 * r + p.hb1 * p.hx[st][1] - p.ha1 * p.hy[st][1];
          p.hx[st][0] = l; p.hy[st][0] = nl;
          p.hx[st][1] = r; p.hy[st][1] = nr;
          l = nl;
          r = nr;
        }
      }
      if (p.surf) {
        for (int st = 0; st < p.surf; ++st) {
          const float* c = p.sc[st];
          float nl = c[0] * l + p.sz[st][0][0], nr = c[0] * r + p.sz[st][1][0];
          p.sz[st][0][0] = c[1] * l - c[3] * nl + p.sz[st][0][1];
          p.sz[st][1][0] = c[1] * r - c[3] * nr + p.sz[st][1][1];
          p.sz[st][0][1] = c[2] * l - c[4] * nl;
          p.sz[st][1][1] = c[2] * r - c[4] * nr;
          l = nl;
          r = nr;
        }
        l *= p.sg;
        r *= p.sg;
      }
      p.env *= p.envMul;
      out.add(i, l, r);
    }
    any = any || p.on;
  }
  striking = any;
}

SK_SYNTH(Piano)
