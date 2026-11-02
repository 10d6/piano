// Piano's custom widgets (synthkit API.md §9).
//
// velocityCurve: how velocity becomes loudness and tone, for the last key played
// (C4 until then), drawn from the engine's own mapping (messages vel.query / vel.point),
// with a dot for each note struck (vel.note). It redraws when Touch, Dynamics,
// Tone Range or Velocity Map change.

const NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const noteName = (n) => `${NAMES[n % 12]}${Math.floor(n / 12) - 1}`;
const DB_RANGE = 40;   // the graph shows 0 to −40 dB

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
      if (hits.length > 12) hits = hits.slice(-12);
      draw();
    });
    for (const id of ["play.touch", "play.dynamics", "play.tone", "play.velmap"]) ctx.onParam(id, query);

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

      const L = 28, R = 8, T = 6, B = 18;
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

      caption.textContent = pts.length > 1
        ? `${noteName(key)}: loudness (solid, dB) and tone (dashed, soft → bright) by velocity`
        : "Play a note to see how velocity shapes loudness and tone";
    }

    new ResizeObserver(draw).observe(el);
    draw();
    query();
  });
}
