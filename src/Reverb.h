// The Space section's reverb: a hybrid. Each space's early reflections are an impulse response
// (a shoebox room's image sources, built in prepare and played by the kit's convolver); its tail
// is a 16-line feedback delay network, so Decay, Size and Damping are continuous. Every space has
// its own convolver and network: changing Space sends new sound to the new one while the old one
// rings out. docs/MODEL.md (Space) has the design and the numbers.
#pragma once

#include <synthkit/synth.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <vector>

namespace piano_reverb {

using namespace sk;
using namespace sk::dsp;

struct SpaceDef {
  const char* name;
  float dims[3];    // the shoebox (m); 0: no reflections (the plate: a dense burst instead)
  float src[3];     // the piano
  float mic[3];     // the listener (a pair 0.6 m apart across y)
  float reflect;    // a wall's reflection (amplitude per bounce)
  float erLen;      // early reflections kept (s); the last 30 % fade out
  float lateDelay;  // the tail is fed this long after the early part (s)
  float rt;         // RT60 below ~2 kHz at Decay 100 % (s)
  float hfRatio;    // RT60 above ~2 kHz, against rt, at Damping 50 %
  float mfp;        // mean free path (m): the network's delays span 0.6-2.2× its travel time
  float erDb;       // early reflections' energy against the tail's
  float trimDb;     // level trim, from renders of a staccato chord across the keyboard (MODEL.md)
};

inline constexpr SpaceDef kSpaces[] = {
    {"Room", {6, 5, 3}, {2, 1.5f, 1}, {4.2f, 3.4f, 1.5f}, 0.75f, 0.06f, 0.008f, 0.6f, 0.6f, 2.9f, 0, 2.3f},
    {"Chamber", {10, 8, 5}, {3, 2.5f, 1}, {6.5f, 5.5f, 1.6f}, 0.8f, 0.09f, 0.015f, 1.3f, 0.55f, 4.7f, -2, 0.6f},
    {"Hall", {32, 20, 14}, {6, 10, 1.2f}, {16, 9, 2}, 0.85f, 0.14f, 0.03f, 2.2f, 0.5f, 13, -5, 0},
    {"Cathedral", {60, 24, 28}, {8, 12, 1.2f}, {24, 11, 1.7f}, 0.9f, 0.22f, 0.05f, 5.0f, 0.4f, 24, -8, -0.7f},
    {"Plate", {0, 0, 0}, {0, 0, 0}, {0, 0, 0}, 0, 0.012f, 0, 2.4f, 0.85f, 3.4f, -10, 0.3f},
};
inline constexpr int kNumSpaces = static_cast<int>(sizeof(kSpaces) / sizeof(kSpaces[0]));

// The tail: 16 delay lines through a Hadamard matrix, each with a two-band loss (a first-order
// split at 2 kHz) set from the RT60s, four of them slowly modulated (±0.3 ms) so it doesn't ring.
class Fdn {
 public:
  static constexpr int N = 16;

  void prepare(float sampleRate, float mfp) {
    sr = sampleRate;
    float t0 = mfp / 343.f;
    for (int i = 0; i < N; ++i) {   // log-spaced over 0.6-2.2× the travel time, made distinct and odd
      float f = std::pow(2.2f / 0.6f, static_cast<float>(i) / (N - 1));
      int d = static_cast<int>(0.6f * t0 * f * sr) | 1;
      for (int j = 0; j < i; ++j)
        if (static_cast<int>(base[j]) == d) d += 2;
      base[i] = static_cast<float>(d);
      int need = static_cast<int>(base[i] * 2.f + 0.001f * sr) + 4;   // Size 200 % and the modulation
      int sz = 1;
      while (sz < need) sz <<= 1;
      buf[i].assign(static_cast<std::size_t>(sz), 0.f);
      mask[i] = sz - 1;
      cur[i] = base[i];
    }
    xover = 1.f - std::exp(-kTwoPi * 2000.f / sr);
    for (int k = 0; k < 4; ++k) {
      float hz = 0.31f + 0.19f * static_cast<float>(k);
      modC[k] = std::cos(kTwoPi * hz / sr);
      modS[k] = std::sin(kTwoPi * hz / sr);
      modRe[k] = std::cos(1.7f * static_cast<float>(k));
      modIm[k] = std::sin(1.7f * static_cast<float>(k));
    }
    modDepth = 0.0003f * sr;
    clear();
  }

  void clear() {
    for (int i = 0; i < N; ++i) {
      std::fill(buf[i].begin(), buf[i].end(), 0.f);
      lp[i] = 0;
    }
    pos = 0;
  }

  // Per block: the RT60s (s) below and above 2 kHz, the size (×), and the block's length.
  void set(float rtLow, float rtHigh, float size, int frames) {
    float k = 1.f - std::exp(-static_cast<float>(frames) / (0.15f * sr));   // Size glides over ~150 ms
    float inv = 1.f / static_cast<float>(std::max(frames, 1));
    for (int i = 0; i < N; ++i) {
      float target = base[i] * size;
      float to = cur[i] + (target - cur[i]) * k;
      step[i] = (to - cur[i]) * inv;
      gLow[i] = std::pow(10.f, -3.f * to / (rtLow * sr));
      gHigh[i] = std::pow(10.f, -3.f * to / (rtHigh * sr));
    }
  }

  void process(float inL, float inR, float& outL, float& outR) {
    float y[N];
    float mod[4];
    for (int k = 0; k < 4; ++k) {   // rotating phasors: the four lines' modulation
      float re = modRe[k] * modC[k] - modIm[k] * modS[k];
      float im = modRe[k] * modS[k] + modIm[k] * modC[k];
      modRe[k] = re;
      modIm[k] = im;
      mod[k] = im * modDepth;
    }
    float ol = 0, orr = 0;
    for (int i = 0; i < N; ++i) {
      cur[i] += step[i];
      float d = cur[i] + (i < 4 ? mod[i] : 0.f);
      int di = static_cast<int>(d);
      float fr = d - static_cast<float>(di);
      const float* b = buf[i].data();
      float a0 = b[(pos - di) & mask[i]], a1 = b[(pos - di - 1) & mask[i]];
      float v = a0 + (a1 - a0) * fr;
      lp[i] += xover * (v - lp[i]);
      y[i] = gLow[i] * lp[i] + gHigh[i] * (v - lp[i]);
      // two orthogonal sign patterns (Hadamard rows 1 and 2) take the outputs, decorrelated
      ol += (i & 1) ? -v : v;
      orr += (i & 2) ? -v : v;
    }
    // y ← H y / 4: the fast Walsh-Hadamard transform, orthogonal (lossless) at this scale
    for (int h = 1; h < N; h <<= 1)
      for (int i = 0; i < N; i += h << 1)
        for (int j = i; j < i + h; ++j) {
          float a = y[j], b = y[j + h];
          y[j] = a + b;
          y[j + h] = a - b;
        }
    for (int i = 0; i < N; ++i) {
      float in = (i & 1) ? inR : inL;
      if (i & 4) in = -in;
      buf[i][static_cast<std::size_t>(pos & mask[i])] = flushDenormal(0.25f * y[i] + 0.35f * in);
    }
    ++pos;
    outL = 0.25f * ol;
    outR = 0.25f * orr;
  }

 private:
  std::vector<float> buf[N];
  int mask[N] = {};
  int pos = 0;
  float sr = 48000;
  float base[N] = {}, cur[N] = {}, step[N] = {}, gLow[N] = {}, gHigh[N] = {}, lp[N] = {};
  float xover = 0;
  float modC[4] = {}, modS[4] = {}, modRe[4] = {}, modIm[4] = {}, modDepth = 0;
};

// The early reflections of one space, as a stereo impulse response (direct sound left out: the
// piano is close-miked). Image sources up to the window's length, each bounce a little darker
// (a first-order low-pass at 9 kHz per bounce) and jittered a few cm so the pattern isn't too
// regular. The plate has none: a 12 ms burst of bright, dense noise instead.
inline void buildEarly(const SpaceDef& s, float sr, std::vector<float>& l, std::vector<float>& r) {
  int len = static_cast<int>(s.erLen * sr) + 2;
  l.assign(static_cast<std::size_t>(len), 0.f);
  r.assign(static_cast<std::size_t>(len), 0.f);
  dsp::Rng rng;
  rng.seed(0x2545F491u + static_cast<unsigned>(s.erLen * 1e4f));
  if (s.dims[0] <= 0) {
    float lpL = 0, lpR = 0, c = std::exp(-kTwoPi * 12000.f / sr);
    for (int i = 0; i < len; ++i) {
      float t = static_cast<float>(i) / sr;
      float env = (1 - std::exp(-t / 0.0007f)) * std::exp(-t / 0.004f);
      lpL = lpL * c + (1 - c) * rng.bipolar();
      lpR = lpR * c + (1 - c) * rng.bipolar();
      l[static_cast<std::size_t>(i)] = lpL * env;
      r[static_cast<std::size_t>(i)] = lpR * env;
    }
    return;
  }
  constexpr int kMaxOrder = 14;
  std::vector<float> bl(static_cast<std::size_t>(len) * (kMaxOrder + 1), 0.f), br(bl.size(), 0.f);
  const float c = 343.f;
  float micY[2] = {s.mic[1] - 0.3f, s.mic[1] + 0.3f};
  auto dist = [&](const float* p, float my) {
    float dx = p[0] - s.mic[0], dy = p[1] - my, dz = p[2] - s.mic[2];
    return std::sqrt(dx * dx + dy * dy + dz * dz);
  };
  float d0 = dist(s.src, s.mic[1]);
  int lim[3];
  for (int a = 0; a < 3; ++a) lim[a] = static_cast<int>((d0 + s.erLen * c) / (2.f * s.dims[a])) + 1;
  for (int ix = -lim[0]; ix <= lim[0]; ++ix)
    for (int px = 0; px < 2; ++px)
      for (int iy = -lim[1]; iy <= lim[1]; ++iy)
        for (int py = 0; py < 2; ++py)
          for (int iz = -lim[2]; iz <= lim[2]; ++iz)
            for (int pz = 0; pz < 2; ++pz) {
              int order = std::abs(2 * ix - px) + std::abs(2 * iy - py) + std::abs(2 * iz - pz);
              if (order == 0 || order > kMaxOrder) continue;
              float jit = 0.03f * static_cast<float>(order);
              float p[3] = {(px ? -s.src[0] : s.src[0]) + 2.f * static_cast<float>(ix) * s.dims[0] + jit * rng.bipolar(),
                            (py ? -s.src[1] : s.src[1]) + 2.f * static_cast<float>(iy) * s.dims[1] + jit * rng.bipolar(),
                            (pz ? -s.src[2] : s.src[2]) + 2.f * static_cast<float>(iz) * s.dims[2] + jit * rng.bipolar()};
              float amp0 = std::pow(s.reflect, static_cast<float>(order));
              for (int m = 0; m < 2; ++m) {
                float d = dist(p, micY[m]);
                float t = (d - d0) / c * sr;
                if (t < 0 || t >= static_cast<float>(len - 2)) continue;
                int ti = static_cast<int>(t);
                float fr = t - static_cast<float>(ti);
                float a = amp0 * d0 / d;
                std::vector<float>& b = m ? br : bl;
                std::size_t o = static_cast<std::size_t>(order) * static_cast<std::size_t>(len);
                b[o + static_cast<std::size_t>(ti)] += a * (1 - fr);
                b[o + static_cast<std::size_t>(ti) + 1] += a * fr;
              }
            }
  // each order through the low-pass once per bounce (Horner: the highest order goes through most)
  float lc = std::exp(-kTwoPi * 9000.f / sr);
  for (int m = 0; m < 2; ++m) {
    std::vector<float>& b = m ? br : bl;
    std::vector<float>& out = m ? r : l;
    std::vector<float> acc(static_cast<std::size_t>(len), 0.f);
    for (int order = kMaxOrder; order >= 1; --order) {
      float z = 0;
      std::size_t o = static_cast<std::size_t>(order) * static_cast<std::size_t>(len);
      for (int i = 0; i < len; ++i) {
        float x = acc[static_cast<std::size_t>(i)] + b[o + static_cast<std::size_t>(i)];
        z = z * lc + (1 - lc) * x;
        acc[static_cast<std::size_t>(i)] = z;
      }
    }
    out = acc;
  }
  for (int i = 0; i < len; ++i) {   // fade the last 30 % into the tail
    float u = (static_cast<float>(i) / static_cast<float>(len) - 0.7f) / 0.3f;
    if (u <= 0) continue;
    float w = 0.5f + 0.5f * std::cos(kPi * std::min(u, 1.f));
    l[static_cast<std::size_t>(i)] *= w;
    r[static_cast<std::size_t>(i)] *= w;
  }
}

// One space: its early reflections, its tail, and how long it still has to ring.
struct Space {
  wa::Convolver early;
  Fdn late;
  float erGain = 1, lateGain = 1;
  float feed = 0;      // input gain, ramped (0 when another space is selected or the mix is 0)
  long ringing = 0;    // samples left before it can stop
  bool idle = true;
};

class Reverb {
 public:
  void prepare(float sampleRate) {
    sr = sampleRate;
    int ring = 1;
    while (ring < static_cast<int>(0.32f * sr)) ring <<= 1;   // pre-delay 200 ms + late delay
    inL.assign(static_cast<std::size_t>(ring), 0.f);
    inR.assign(static_cast<std::size_t>(ring), 0.f);
    ringMask = ring - 1;
    for (int k = 0; k < kNumSpaces; ++k) {
      const SpaceDef& d = kSpaces[k];
      Space& s = spaces[k];
      std::vector<float> l, r;
      buildEarly(d, sr, l, r);
      // energies weighted like a piano's spectrum (a one-pole low-pass at 800 Hz), so the spaces
      // sound about as loud as each other at the same Reverb setting
      const float wc = std::exp(-kTwoPi * 800.f / sr);
      double el = 0;
      {
        float zl = 0, zr = 0;
        for (std::size_t i = 0; i < l.size() + static_cast<std::size_t>(0.05f * sr); ++i) {
          float xl = i < l.size() ? l[i] : 0.f, xr = i < l.size() ? r[i] : 0.f;
          zl = zl * wc + (1 - wc) * xl;
          zr = zr * wc + (1 - wc) * xr;
          el += 0.5 * (zl * zl + zr * zr);
        }
      }
      const float* ch[2] = {l.data(), r.data()};
      s.early.setImpulse(ch, 2, static_cast<int>(l.size()), static_cast<double>(sr), false);
      s.late.prepare(sr, d.mfp);
      // the tail's energy for an impulse, at Decay, Size and Damping as defaults
      s.late.set(d.rt, d.rt * d.hfRatio, 1.f, 1);
      double et = 0;
      int n = static_cast<int>(d.rt * 1.5f * sr);
      float zl = 0, zr = 0;
      for (int i = 0; i < n; ++i) {
        float a = i == 0 ? 1.f : 0.f, ol, orr;
        s.late.process(a, a, ol, orr);
        zl = zl * wc + (1 - wc) * ol;
        zr = zr * wc + (1 - wc) * orr;
        et += 0.5 * (zl * zl + zr * zr);
      }
      s.late.clear();
      // every space the same energy for an impulse (early + tail = 1), split by erDb
      double erShare = std::pow(10.0, d.erDb / 10.0), total = (1.0 + erShare) * std::pow(10.0, -d.trimDb / 10.0);
      s.lateGain = static_cast<float>(std::sqrt(1.0 / total / std::max(et, 1e-12)));
      s.erGain = static_cast<float>(std::sqrt(erShare / total / std::max(el, 1e-12)));
      s.feed = 0;
      s.ringing = 0;
      s.idle = true;
    }
    for (int c = 0; c < 2; ++c) {
      lowCut[c].prepare(sr);
      lowCut[c].setType(wa::BiquadFilter::Type::Highpass);
      highCut[c].prepare(sr);
      highCut[c].setType(wa::BiquadFilter::Type::Lowpass);
    }
    pos = 0;
  }

  struct Settings {
    int type;
    float decay, size, damping, predelayMs, earlyDb, lowCutHz, highCutHz;
    bool on;   // the mix is above 0 somewhere in this block
  };

  // Adds the wet signal (unscaled by the mix) into wl/wr for the block's input l/r. Returns false
  // when every space is idle: nothing was added.
  bool process(const Settings& st, const float* l, const float* r, float* wl, float* wr, int frames) {
    int type = std::clamp(st.type, 0, kNumSpaces - 1);
    bool any = false;
    for (int k = 0; k < kNumSpaces; ++k) {
      Space& s = spaces[k];
      bool feeding = st.on && k == type;
      if (feeding) {
        if (s.idle) s.late.clear();
        s.idle = false;
      }
      if (s.idle) continue;
      any = true;
    }
    if (!any) {
      std::fill(wl, wl + frames, 0.f);
      std::fill(wr, wr + frames, 0.f);
      return false;
    }
    float pre = std::clamp(st.predelayMs, 0.f, 200.f) * 0.001f * sr;
    float er = dbToGain(st.earlyDb);
    for (int c = 0; c < 2; ++c) {
      lowCut[c].set(st.lowCutHz, -3.0103f);
      highCut[c].set(st.highCutHz, -3.0103f);
    }
    for (int i = 0; i < frames; ++i) {
      inL[static_cast<std::size_t>(pos & ringMask)] = l[i];
      inR[static_cast<std::size_t>(pos & ringMask)] = r[i];
      wl[i] = wr[i] = 0;
      ++pos;
    }
    float fadeStep = 1.f / (0.01f * sr);   // 10 ms input fades between spaces
    for (int k = 0; k < kNumSpaces; ++k) {
      Space& s = spaces[k];
      if (s.idle) continue;
      const SpaceDef& d = kSpaces[k];
      bool feeding = st.on && k == type;
      float rt = d.rt * st.decay;
      float rtHigh = std::min(rt * d.hfRatio * std::exp2((50.f - st.damping) / 30.f), rt * 1.2f);
      s.late.set(rt, rtHigh, st.size, frames);
      int preEarly = static_cast<int>(pre);
      int preLate = static_cast<int>(pre + d.lateDelay * sr);
      float target = feeding ? 1.f : 0.f;
      for (int i = 0; i < frames; ++i) {
        long at = pos - frames + i;
        s.feed += std::clamp(target - s.feed, -fadeStep, fadeStep);
        float g = s.feed;
        float el = inL[static_cast<std::size_t>((at - preEarly) & ringMask)] * g;
        float erR = inR[static_cast<std::size_t>((at - preEarly) & ringMask)] * g;
        float ll = inL[static_cast<std::size_t>((at - preLate) & ringMask)] * g;
        float lr = inR[static_cast<std::size_t>((at - preLate) & ringMask)] * g;
        s.early.process(el, erR);
        float tl, tr;
        s.late.process(ll, lr, tl, tr);
        wl[i] += er * s.erGain * el + s.lateGain * tl;
        wr[i] += er * s.erGain * erR + s.lateGain * tr;
      }
      if (feeding || s.feed > 0) {
        // rings on for its RT60 ×1.3 after the input stops (-78 dB), plus the early part
        s.ringing = static_cast<long>((std::max(rt, rtHigh) * 1.3f + d.erLen) * sr) + preLate;
      } else {
        s.ringing -= frames;
        if (s.ringing <= 0) {
          s.idle = true;
          s.late.clear();
        }
      }
    }
    for (int i = 0; i < frames; ++i) {
      wl[i] = highCut[0].process(lowCut[0].process(wl[i]));
      wr[i] = highCut[1].process(lowCut[1].process(wr[i]));
    }
    for (int c = 0; c < 2; ++c) {
      lowCut[c].flush();
      highCut[c].flush();
    }
    return true;
  }

 private:
  float sr = 48000;
  Space spaces[kNumSpaces];
  std::vector<float> inL, inR;
  long pos = 0;
  int ringMask = 0;
  wa::BiquadFilter lowCut[2], highCut[2];
};

}  // namespace piano_reverb
