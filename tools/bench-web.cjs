// Times a scenario rendered offline through the page's real AudioWorklet in
// headless Chromium: how many times faster than realtime the browser build runs.
//
//   node tools/bench-web.cjs build-web/web/piano.html tests/glissando-pedal.json [runs]
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

(async () => {
  const [html, scenarioPath, runsArg] = process.argv.slice(2);
  const runs = Number(runsArg || 3);
  const sc = JSON.parse(fs.readFileSync(scenarioPath, "utf8"));
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto("file://" + path.resolve(html));
  await page.waitForFunction(() => window.synthkit && window.synthkit.model);
  const times = await page.evaluate(async ({ sc, runs }) => {
    const sk = window.synthkit;
    const preset = sk.model.info.presets.find((p) => p.path.endsWith("/" + sc.patch + ".json"));
    sk.model.loadPatch(preset.patch);
    for (const [k, v] of Object.entries(sc.params || {})) sk.model.set(k, v);
    const events = sc.events.map((e) => ({
      t: e.t,
      d: e.on !== undefined ? [0x90, e.on, e.vel ?? 100] : e.off !== undefined ? [0x80, e.off, 64] : [0xb0, e.cc, e.value],
    }));
    const out = [];
    for (let i = 0; i < runs; i++) {
      const t0 = performance.now();
      await sk.renderOffline({ events, seconds: sc.length, sampleRate: 48000, transport: { bpm: 120, playing: false, num: 4, den: 4 } });
      out.push((performance.now() - t0) / 1000);
    }
    return out;
  }, { sc, runs });
  const best = Math.min(...times);
  console.log(`${path.basename(scenarioPath)}: ${sc.length} s of audio, best ${best.toFixed(2)} s → ${(sc.length / best).toFixed(1)}x realtime`);
  await browser.close();
})();
