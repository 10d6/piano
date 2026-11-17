// Piano's custom widgets (synthkit API.md §9).
//
// velocityCurve: how velocity becomes loudness and tone, for the last key played
// (C4 until then), drawn from the engine's own mapping (messages vel.query / vel.point),
// with a dot for each note struck (vel.note). It redraws when Touch, Dynamics,
// Tone Range or Velocity Map change.
//
// The graph can be played: pressing it strikes that key at the velocity under the pointer
// (soft at the left, hard at the right) and holds it until released. The on-screen keyboard
// and the computer keys always play velocity 100, where the Playing settings change little,
// so this is how to hear them without a MIDI keyboard.
//
// keyMap: the Keys tab's editor for the key maps (custom state "keys", src/Synth.cpp KeyMaps):
// one bar per key, A0 to C8, for the map chosen above it (Tuning, Unison, Hammer, Level, Sustain,
// Dampers, Stiffness; and, Prepared, Point, Mute, Bolt). Drag
// across the bars to draw (one undo step per drag); double-click a bar to put that key back.
// The strip of keys under the bars plays each key. The buttons fill a map at once: Reset, Smooth,
// Randomize (this map), Prepare (Point, Mute and Bolt: some keys from C2 to C7 get an object on
// their strings) and Age Piano (every other map, a worn piano's pattern: most wear in the middle,
// where a piano is played most), both by Amount.

const NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const noteName = (n) => `${NAMES[n % 12]}${Math.floor(n / 12) - 1}`;
const DB_RANGE = 40;   // the graph shows 0 to −40 dB
const PAD_L = 28, PAD_R = 8;   // the plot's left and right margins (px)

export function register(sk) {
  sk.widget("velocityCurve", (el, ctx) => {
    el.classList.add("vel-curve");
    const canvas = document.createElement("canvas");
    const caption = document.createElement("div");
    caption.className = "vel-caption";
    el.append(canvas, caption);

    let key = 60;
    const points = [];        // [velocity, dB, tone]
    let hits = [];            // recent strikes: {v, db, tone, t}
    let pending = 0;

    const query = () => {
      clearTimeout(pending);
      pending = setTimeout(() => ctx.send("vel.query", key), 30);
    };

    ctx.onMessage("vel.point", (i, v, db, tone) => {
      points[i] = [v, db, tone];
      draw();
    });
    ctx.onMessage("vel.note", (k, v, db, tone) => {
      if (k !== key) {
        key = k;
        points.length = 0;
        query();
      }
      hits.push({ v, db, tone, t: performance.now() });
      el.dataset.lastVelocity = String(Math.round(v));   // what the engine struck (tools/measure/panel.cjs reads it)
      if (hits.length > 12) hits = hits.slice(-12);
      draw();
    });
    for (const id of ["play.touch", "play.dynamics", "play.tone", "play.velmap"]) ctx.onParam(id, query);

    // playing the graph: the velocity under the pointer, on the key the graph shows
    let held = -1;
    let heldV = 0;
    const velAt = (e) => {
      const r = canvas.getBoundingClientRect();
      const u = (e.clientX - r.left - PAD_L) / Math.max(1, r.width - PAD_L - PAD_R);
      return Math.max(1, Math.min(127, Math.round(u * 127)));
    };
    const letGo = () => {
      if (held >= 0) ctx.noteOff(held);
      held = -1;
      draw();
    };
    canvas.style.cursor = "pointer";
    canvas.style.touchAction = "none";
    canvas.addEventListener("pointerdown", (e) => {
      if (e.button) return;
      e.preventDefault();
      letGo();
      held = key;
      heldV = velAt(e);
      canvas.setPointerCapture(e.pointerId);
      ctx.noteOn(held, heldV);
      draw();
    });
    canvas.addEventListener("pointerup", letGo);
    canvas.addEventListener("pointercancel", letGo);

    function draw() {
      const css = getComputedStyle(el);
      const col = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
      const accent = col("--accent", "#8a5a2b");
      const ink = col("--ink", "#222");
      const soft = col("--ink-soft", "#777");
      const edge = col("--edge", "#ccc");

      const w = el.clientWidth || 260;
      const h = 120;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      const g = canvas.getContext("2d");
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, w, h);

      const L = PAD_L, R = PAD_R, T = 6, B = 18;
      const X = (v) => L + (v / 127) * (w - L - R);
      const Yd = (db) => T + (Math.min(0, Math.max(-DB_RANGE, db)) / -DB_RANGE) * (h - T - B);
      const Yt = (tone) => T + (1 - tone) * (h - T - B);

      g.font = "10px system-ui, sans-serif";
      g.strokeStyle = edge;
      g.fillStyle = soft;
      g.lineWidth = 1;
      for (const db of [0, -10, -20, -30, -40]) {
        g.beginPath();
        g.moveTo(L, Yd(db) + 0.5);
        g.lineTo(w - R, Yd(db) + 0.5);
        g.stroke();
        g.fillText(`${db}`, 2, Yd(db) + 3);
      }
      g.fillText("velocity →", w - R - 52, h - 4);

      const pts = points.filter(Boolean);
      if (pts.length > 1) {
        // tone: from the softest recording (0) to the loudest (1)
        g.strokeStyle = soft;
        g.setLineDash([3, 3]);
        g.beginPath();
        pts.forEach(([v, , tone], i) => (i ? g.lineTo(X(v), Yt(tone)) : g.moveTo(X(v), Yt(tone))));
        g.stroke();
        g.setLineDash([]);
        // loudness
        g.strokeStyle = accent;
        g.lineWidth = 2;
        g.beginPath();
        pts.forEach(([v, db], i) => (i ? g.lineTo(X(v), Yd(db)) : g.moveTo(X(v), Yd(db))));
        g.stroke();
      }
      // the notes just played, fading
      const now = performance.now();
      for (const hit of hits) {
        const age = Math.min(1, (now - hit.t) / 4000);
        g.globalAlpha = 1 - 0.8 * age;
        g.fillStyle = ink;
        g.beginPath();
        g.arc(X(hit.v), Yd(hit.db), 3, 0, 2 * Math.PI);
        g.fill();
      }
      g.globalAlpha = 1;
      if (held >= 0) {   // the velocity being played from the graph
        g.strokeStyle = ink;
        g.lineWidth = 1;
        g.beginPath();
        g.moveTo(X(heldV) + 0.5, T);
        g.lineTo(X(heldV) + 0.5, h - B);
        g.stroke();
      }

      caption.textContent = held >= 0
        ? `${noteName(key)} at velocity ${heldV}`
        : pts.length > 1
          ? `${noteName(key)}: loudness (solid, dB) and tone (dashed, soft → bright) by velocity. Press the graph to play this key: soft at the left, hard at the right (the keys below always play velocity 100).`
          : "Play a note to see how velocity shapes loudness and tone";
    }

    new ResizeObserver(draw).observe(el);
    draw();
    query();
  });

  sk.widget("keyMap", (el, ctx) => keyMapWidget(el, ctx));
}

// --- key maps ---------------------------------------------------------------------------------

const MAP_KEYS = 88, MAP_LOW = 21;
// each map: its range, neutral value, how a value reads, and the Randomize / Age Piano spreads
const MAPS = {
  tune: {
    label: "Tuning", lo: -50, hi: 50, neutral: 0, unit: "cents",
    text: (v) => (Math.abs(v) < 0.05 ? "in tune" : `${v > 0 ? "+" : ""}${v.toFixed(1)} cents`),
    info: "Each key's pitch, in cents (±50). A worn piano drifts key by key, and flat more than sharp.",
  },
  unison: {
    label: "Unison", lo: 0, hi: 4, neutral: 1, unit: "×",
    text: (v) => `${Math.round(v * 100)} % of Unison`,
    info: "How far out of tune each key's strings are with each other, as a share of the Unison setting (0-400 %): above the line a key beats and shimmers more, below it rings cleaner.",
  },
  hammer: {
    label: "Hammer", lo: -1, hi: 1, neutral: 0, unit: "",
    text: (v) => (Math.abs(v) < 0.005 ? "as set" : v > 0 ? `harder ${Math.round(v * 100)} %` : `softer ${Math.round(-v * 100)} %`),
    info: "Each key's felt: above the line harder and brighter (packed by use: Edge up), below it softer and duller (Hardness down).",
  },
  level: {
    label: "Level", lo: -18, hi: 6, neutral: 0, unit: "dB",
    text: (v) => (Math.abs(v) < 0.05 ? "as set" : `${v > 0 ? "+" : ""}${v.toFixed(1)} dB`),
    info: "How loud each key is against its neighbours (−18 to +6 dB): uneven regulation, a weak or dead note.",
  },
  sustain: {
    label: "Sustain", lo: 0.25, hi: 4, neutral: 1, log: true, unit: "×",
    text: (v) => `${Math.round(v * 100)} % of Sustain`,
    info: "How long each key rings, as a share of the Sustain setting (25-400 %): a worn bridge or a tired string dies early.",
  },
  damper: {
    label: "Dampers", lo: 0.25, hi: 32, neutral: 1, log: true, unit: "×",
    text: (v) => (v >= 31.5 ? "rings on, as if it had no damper" : `damper ${v >= 1 ? v.toFixed(v < 10 ? 1 : 0) + "× as slow" : (1 / v).toFixed(1) + "× as quick"}`),
    info: "How long each key's damper takes to stop it when you let go, × the Dampers setting: above the line a worn, leaky damper lets the note ring on (at the top, as if it had none); below it, a firmer one stops it quicker. Keys above E6 have no dampers unless Top Dampers is All keys.",
  },
  stiff: {
    label: "Stiffness", lo: 0, hi: 4, neutral: 1, unit: "×",
    text: (v) => `${Math.round(v * 100)} % of Stiffness`,
    info: "How stiff each key's strings are, × the Stiffness setting (0-400 %): a string replaced with a heavier gauge, or a corroded one, rings more bell-like than its neighbours. It applies to the notes you strike next.",
  },
  // the prepared piano: an object on the strings (src/Synth.cpp, Prep)
  point: {
    label: "Point", group: "Prepared", lo: 0.02, hi: 0.5, neutral: 0.13, log: true, unit: "",
    text: (v) => { const n = Math.round(1 / v); return `${(v * 100).toFixed(1)} % along the string${Math.abs(v * n - 1) < 0.002 ? ` (1/${n})` : ""}`; },
    info: "Where along each key's string its Mute or Bolt sits, as a share of the string from the nearer end (2-50 %; it does nothing alone). At 1/2, 1/3, 1/4… (the bars snap there) a mute leaves a harmonic: the partials with a node there ring on. Faded bars are keys with nothing on them.",
  },
  mute: {
    label: "Mute", group: "Prepared", lo: 0, hi: 1, neutral: 0, unit: "",
    text: (v) => (v <= 0 ? "none" : `${Math.round(v * 100)} %`),
    info: "A rubber or felt wedge between each key's strings, at its Point (0-100 %): the note thuds with its pitch and dies quickly, most on the partials that move at the Point. It applies to the notes you strike next.",
  },
  bolt: {
    label: "Bolt", group: "Prepared", lo: 0, hi: 1, neutral: 0, unit: "",
    text: (v) => (v <= 0 ? "none" : `${Math.round(v * 100)} % (${(3 * v * v).toFixed(2)}× the string's mass)`),
    info: "A bolt or screw clamped to each key's strings at its Point (0-100 %): its mass pulls every partial down by its own amount, so the note goes inharmonic, gong-like, and lower (at 100 %, an octave or so). It applies to the notes you strike next.",
  },
};
const MAP_IDS = Object.keys(MAPS);
const mapName = (i) => noteName(i + MAP_LOW);
const isBlack = (i) => [1, 3, 6, 8, 10].includes((i + MAP_LOW) % 12);
const clampTo = (m, v) => Math.max(m.lo, Math.min(m.hi, v));

// the bars' height runs from the range's bottom to its top with neutral in the middle (each half
// linear), so the line is always at the same place and both directions have room to draw
// (in log terms for Sustain and Dampers, whose settings are logarithmic)
const lin = (m, v) => (m.log ? Math.log(v) : v);
const unlin = (m, x) => (m.log ? Math.exp(x) : x);
// a map whose neutral is its bottom (Mute, Bolt: none) runs from the bottom up instead
const toUnit = (m, v) => {
  const x = lin(m, v), n = lin(m, m.neutral), lo = lin(m, m.lo), hi = lin(m, m.hi);
  if (m.lo === m.neutral) return (x - lo) / (hi - lo);
  return x >= n ? 0.5 + 0.5 * (x - n) / (hi - n) : 0.5 * (x - lo) / (n - lo);
};
const fromUnit = (m, u) => {
  const n = lin(m, m.neutral), lo = lin(m, m.lo), hi = lin(m, m.hi);
  if (m.lo === m.neutral) return unlin(m, lo + u * (hi - lo));
  return unlin(m, u >= 0.5 ? n + (u - 0.5) * 2 * (hi - n) : lo + u * 2 * (n - lo));
};

function neutralMaps() {
  const o = {};
  for (const id of MAP_IDS) o[id] = new Array(MAP_KEYS).fill(MAPS[id].neutral);
  return o;
}
function readMaps(j) {
  const o = neutralMaps();
  if (j && typeof j === "object")
    for (const id of MAP_IDS)
      if (Array.isArray(j[id])) for (let i = 0; i < MAP_KEYS; i++) if (Number.isFinite(j[id][i])) o[id][i] = clampTo(MAPS[id], j[id][i]);
  return o;
}

function keyMapWidget(el, ctx) {
  el.classList.add("keymap");
  el.innerHTML = `
    <div class="keymap-bar">
      <div class="keymap-tabs" role="tablist"></div>
      <div class="keymap-hint">Drag across the bars to draw; double-click a bar to reset that key.</div>
    </div>
    <div class="keymap-plot">
      <canvas class="keymap-canvas"></canvas>
      <div class="keymap-readout" aria-live="polite"></div>
    </div>
    <canvas class="keymap-keys"></canvas>
    <div class="keymap-tools">
      <button data-act="reset" title="Put every key of this map back as set">Reset</button>
      <button data-act="smooth" title="Average each key with its neighbours">Smooth</button>
      <button data-act="random" title="Give every key of this map its own random value, by Amount">Randomize</button>
      <button data-act="prepare" title="Prepare the piano: put a rubber mute or a bolt, each at a point of its own, on some keys from C2 to C7 (at Amount 100 %, about half of them). Point, Mute and Bolt are drawn afresh; the other maps are left as they are">Prepare</button>
      <button data-act="age" title="Fill every map but the Prepared ones with a worn piano's pattern, by Amount: drifting tuning, beating unisons, packed hammers and shorter rings in the middle, uneven levels, slow dampers (now and then one that hardly stops), now and then a replaced string">Age Piano</button>
      <label class="keymap-amount">Amount <input type="range" min="0" max="100" value="50"><span>50 %</span></label>
    </div>
    <div class="keymap-info"></div>`;
  const tabs = el.querySelector(".keymap-tabs");
  const readout = el.querySelector(".keymap-readout");
  const canvas = el.querySelector(".keymap-canvas");
  const keys = el.querySelector(".keymap-keys");
  const info = el.querySelector(".keymap-info");
  const amountIn = el.querySelector(".keymap-amount input");
  const amountOut = el.querySelector(".keymap-amount span");

  let maps = readMaps(ctx.custom("keys"));
  let current = "tune";
  let hover = -1;
  let flash = new Map();   // key index → time it was last struck
  let drawing = false, lastI = -1, lastV = 0;   // a stroke across the bars
  let held = -1;   // the key being played from the strip
  const H = 160, KH = 26;

  for (const id of MAP_IDS) {
    if (MAPS[id].group && MAPS[id].group !== MAPS[MAP_IDS[MAP_IDS.indexOf(id) - 1]]?.group) {
      const g = document.createElement("span");
      g.className = "keymap-group";
      g.textContent = MAPS[id].group;
      tabs.append(g);
    }
    const b = document.createElement("button");
    b.textContent = MAPS[id].label;
    b.dataset.map = id;
    b.setAttribute("role", "tab");
    b.addEventListener("click", () => { current = id; refresh(); });
    tabs.append(b);
  }
  amountIn.addEventListener("input", () => { amountOut.textContent = `${amountIn.value} %`; });

  const commit = () => ctx.setCustom("keys", maps);
  ctx.onCustom("keys", (j) => { if (!drawing) { maps = readMaps(j); refresh(); } });
  ctx.onMessage("vel.note", (k) => { flash.set(k - MAP_LOW, performance.now()); draw(); setTimeout(draw, 700); });

  // drawing
  const geom = () => canvas.getBoundingClientRect();
  const keyAt = (x, r) => Math.max(0, Math.min(MAP_KEYS - 1, Math.floor(((x - r.left) / r.width) * MAP_KEYS)));
  const valAt = (y, r) => {
    const m = MAPS[current];
    let u = 1 - (y - r.top - 6) / (r.height - 12);
    u = Math.max(0, Math.min(1, u));
    const u0 = toUnit(m, m.neutral);
    if (Math.abs(u - u0) < 0.012) u = u0;   // snaps to the line
    let v = clampTo(m, fromUnit(m, u));
    if (current === "point") {   // and Point to the string's whole divisions, where harmonics are
      const n = Math.round(1 / v);
      if (n >= 2 && n <= 16 && Math.abs(v * n - 1) < 0.04) v = 1 / n;
    }
    return v;
  };
  canvas.style.touchAction = "none";
  canvas.addEventListener("pointerdown", (e) => {
    if (e.button) return;
    e.preventDefault();
    const r = geom();
    drawing = true;
    canvas.setPointerCapture(e.pointerId);
    lastI = keyAt(e.clientX, r);
    lastV = valAt(e.clientY, r);
    maps[current][lastI] = lastV;
    hover = lastI;
    refresh();
  });
  canvas.addEventListener("pointermove", (e) => {
    const r = geom();
    const i = keyAt(e.clientX, r);
    hover = i;
    if (drawing) {
      const v = valAt(e.clientY, r);
      // fill every key between the last point and this one, so a fast stroke leaves no gaps
      const n = Math.abs(i - lastI);
      for (let s = 0; s <= n; s++) {
        const k = lastI + Math.sign(i - lastI) * s;
        maps[current][k] = n ? lastV + (v - lastV) * (s / n) : v;
      }
      lastI = i;
      lastV = v;
    }
    refresh();
  });
  const endDraw = () => { if (drawing) { drawing = false; commit(); } };
  canvas.addEventListener("pointerup", endDraw);
  canvas.addEventListener("pointercancel", endDraw);
  canvas.addEventListener("pointerleave", () => { if (!drawing) { hover = -1; refresh(); } });
  canvas.addEventListener("dblclick", (e) => {
    const i = keyAt(e.clientX, geom());
    maps[current][i] = MAPS[current].neutral;
    commit();
    refresh();
  });

  // the strip of keys plays them
  const letGo = () => { if (held >= 0) ctx.noteOff(held + MAP_LOW); held = -1; draw(); };
  keys.style.touchAction = "none";
  keys.style.cursor = "pointer";
  keys.addEventListener("pointerdown", (e) => {
    if (e.button) return;
    e.preventDefault();
    letGo();
    held = keyAt(e.clientX, keys.getBoundingClientRect());
    keys.setPointerCapture(e.pointerId);
    ctx.noteOn(held + MAP_LOW, 100);
    hover = held;
    refresh();
  });
  keys.addEventListener("pointerup", letGo);
  keys.addEventListener("pointercancel", letGo);

  // the tools
  const amount = () => Number(amountIn.value) / 100;
  const rnd = () => Math.random() * 2 - 1;
  const worn = (i) => { const a = (i + MAP_LOW - 62) / 17; return 0.35 + 0.65 * Math.exp(-a * a); };   // as Wear: most at D4
  const spread = {
    tune: (a) => 20 * a * rnd(),
    unison: (a) => 1 + 3 * a * Math.random(),
    hammer: (a) => a * rnd(),
    level: (a) => 6 * a * rnd(),
    point: () => 0.05 * 6 ** Math.random(),
    mute: (a) => a * Math.random(),
    bolt: (a) => a * Math.random(),
    sustain: (a) => 2 ** (a * rnd()),
    damper: (a) => 2 ** (2 * a * rnd()),
    stiff: (a) => 1 + 0.8 * a * rnd(),
  };
  el.querySelector(".keymap-tools").addEventListener("click", (e) => {
    const act = e.target.closest("button")?.dataset.act;
    if (!act) return;
    const a = amount(), m = MAPS[current], arr = maps[current];
    if (act === "reset") arr.fill(m.neutral);
    else if (act === "smooth") {
      const old = arr.slice();
      for (let i = 0; i < MAP_KEYS; i++) arr[i] = 0.25 * old[Math.max(0, i - 1)] + 0.5 * old[i] + 0.25 * old[Math.min(MAP_KEYS - 1, i + 1)];
    } else if (act === "random") {
      for (let i = 0; i < MAP_KEYS; i++) arr[i] = clampTo(m, spread[current](a));
    } else if (act === "prepare") {
      // as for a prepared piano: some keys from C2 to C7 get an object, a mute or a bolt, at a
      // point of their own (now and then a whole division of the string, for a harmonic)
      for (let i = 0; i < MAP_KEYS; i++) {
        maps.point[i] = MAPS.point.neutral;
        maps.mute[i] = 0;
        maps.bolt[i] = 0;
        const note = i + MAP_LOW;
        if (note < 36 || note > 96 || Math.random() >= 0.5 * a) continue;
        maps.point[i] = Math.random() < 0.3 ? 1 / (2 + Math.floor(8 * Math.random())) : 0.05 * 6 ** Math.random();
        if (Math.random() < 0.55) maps.bolt[i] = 0.25 + 0.65 * Math.random();
        else maps.mute[i] = 0.4 + 0.6 * Math.random();
      }
    } else if (act === "age") {
      // a piano long without a tuner or a technician: every map at once
      for (let i = 0; i < MAP_KEYS; i++) {
        const w = worn(i);
        // pitch falls as the strings lose tension, unevenly: flat most where it's played most
        maps.tune[i] = clampTo(MAPS.tune, a * (-10 * w * Math.random() + 7 * rnd()));
        // the strings of a key drift apart: more in the middle
        maps.unison[i] = clampTo(MAPS.unison, 1 + a * (0.4 + 2.6 * w * Math.random()));
        // hammers packed hard where they're used, a few left softer
        maps.hammer[i] = clampTo(MAPS.hammer, a * (0.7 * w * Math.random() + 0.25 * rnd()));
        // uneven regulation, and now and then a weak note
        maps.level[i] = clampTo(MAPS.level, a * (2.5 * rnd() - (Math.random() < 0.06 ? 4 + 6 * Math.random() : 0)));
        // shorter rings, most in the middle
        maps.sustain[i] = clampTo(MAPS.sustain, 1 - a * (0.1 + 0.3 * w * Math.random()));
        // dampers slow with wear, and now and then one that hardly stops its note
        maps.damper[i] = clampTo(MAPS.damper, Math.random() < 0.05 * a ? 16 + 16 * Math.random() : 2 ** (1.5 * a * Math.random()));
        // now and then a string replaced with another gauge
        maps.stiff[i] = clampTo(MAPS.stiff, Math.random() < 0.05 * a ? 2.5 + 1.5 * Math.random() : 1);
      }
    }
    commit();
    refresh();
  });

  function refresh() {
    for (const b of tabs.children) {
      const on = b.dataset.map === current;
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    }
    const m = MAPS[current];
    const shown = held >= 0 ? held : hover;
    // the value under the pointer, over the bars' corner: it never moves the layout
    readout.textContent = shown >= 0 ? `${mapName(shown)}: ${m.text(maps[current][shown])}` : "";
    readout.hidden = shown < 0;
    readout.classList.toggle("left", shown > MAP_KEYS * 0.75);   // out of the way of the keys it describes
    info.textContent = m.info;
    el.dataset.map = current;   // for tests
    draw();
  }

  function draw() {
    const css = getComputedStyle(el);
    const col = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
    const accent = col("--accent", "#8a5a2b"), ink = col("--ink", "#222"), soft = col("--ink-soft", "#777");
    const edge = col("--edge", "#ccc"), field = col("--field", "#fff"), cap = col("--cap", "#333");
    const w = Math.max(200, el.clientWidth);
    const dpr = window.devicePixelRatio || 1;
    const size = (c, h) => {
      c.width = w * dpr; c.height = h * dpr; c.style.width = `${w}px`; c.style.height = `${h}px`;
      const g = c.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h); return g;
    };
    const m = MAPS[current], arr = maps[current];
    const cw = w / MAP_KEYS;
    // the bars
    let g = size(canvas, H);
    const T = 6, Bm = 6, yOf = (u) => T + (1 - u) * (H - T - Bm), y0 = yOf(toUnit(m, m.neutral));
    for (let i = 0; i < MAP_KEYS; i++) {
      if (isBlack(i)) { g.fillStyle = edge; g.globalAlpha = 0.25; g.fillRect(i * cw, 0, cw, H); g.globalAlpha = 1; }
    }
    g.strokeStyle = edge; g.lineWidth = 1;
    for (const u of [0, 0.25, 0.75, 1]) { g.beginPath(); g.moveTo(0, Math.round(yOf(u)) + 0.5); g.lineTo(w, Math.round(yOf(u)) + 0.5); g.stroke(); }
    const now = performance.now();
    for (let i = 0; i < MAP_KEYS; i++) {
      const y = yOf(toUnit(m, arr[i]));
      const lit = now - (flash.get(i) || -1e9) < 600;
      g.fillStyle = i === hover || i === held || lit ? ink : accent;
      // Point does nothing on a key with nothing on its strings: faded there
      g.globalAlpha = current === "point" && !(maps.mute[i] > 0 || maps.bolt[i] > 0) ? 0.3 : 1;
      g.fillRect(i * cw + 0.5, Math.min(y, y0), Math.max(1, cw - 1), Math.max(1.5, Math.abs(y - y0)));
      g.globalAlpha = 1;
    }
    g.strokeStyle = soft; g.beginPath(); g.moveTo(0, Math.round(y0) + 0.5); g.lineTo(w, Math.round(y0) + 0.5); g.stroke();
    g.fillStyle = soft; g.font = "10px system-ui, sans-serif";
    const label = {
      tune: ["+50 cents", "−50 cents"], unison: ["400 %", "0 %"], hammer: ["harder", "softer"], level: ["+6 dB", "−18 dB"],
      sustain: ["400 %", "25 %"], damper: ["rings on (×32)", "×0.25"], stiff: ["400 %", "0 %"],
      point: ["50 % (the middle)", "2 % (near the end)"], mute: ["100 %", "none"], bolt: ["100 %", "none"],
    }[current];
    g.fillText(label[0], 3, T + 9);
    g.fillText(label[1], 3, H - Bm - 2);
    // the keys
    g = size(keys, KH + 12);
    for (let i = 0; i < MAP_KEYS; i++) {
      const black = isBlack(i);
      g.fillStyle = i === held ? accent : black ? cap : field;
      g.fillRect(i * cw, 0, cw, KH);
      g.strokeStyle = edge; g.strokeRect(i * cw + 0.5, 0.5, cw - 1, KH - 1);
      if ((i + MAP_LOW) % 12 === 0 && i * cw + 16 < w) { g.fillStyle = soft; g.font = "9px system-ui, sans-serif"; g.fillText(mapName(i), i * cw + 1, KH + 10); }
    }
  }

  new ResizeObserver(draw).observe(el);
  refresh();
}

