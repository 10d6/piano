// The page in Chromium, played: checks the velocity graph fills in, that the on-screen
// keyboard plays (at velocity 100, whatever the click height or computer key), and that
// nothing goes to the console but a web-font request; saves a screenshot.
//
//   node tools/measure/panel.cjs build-web/web/piano.html [out.png]
const path = require("path");
const { chromium } = require("playwright");

(async () => {
  const [html, png = "panel.png"] = process.argv.slice(2);
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1440, height: 1000 } });
  const errs = [];
  p.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") errs.push(m.text()); });
  p.on("pageerror", (e) => errs.push(String(e)));
  await p.goto("file://" + path.resolve(html));
  await p.waitForFunction(() => window.synthkit && window.synthkit.model);
  await p.mouse.click(700, 500);
  await p.waitForTimeout(800);
  for (const k of ["a", "s", "d", "f", "g"]) { await p.keyboard.down(k); await p.waitForTimeout(150); await p.keyboard.up(k); }
  // the on-screen keyboard's velocity, clicking a key near its top, middle and bottom
  const key = await p.$(".keyboard [data-note], .keyboard .key, .kb-keys > *");
  const box = await key.boundingBox();
  const vels = [];
  for (const frac of [0.1, 0.5, 0.95]) {
    await p.mouse.move(box.x + box.width / 2, box.y + box.height * frac);
    await p.mouse.down(); await p.waitForTimeout(120);
    vels.push(await p.$eval(".kb-vel", (e) => e.textContent));
    await p.mouse.up(); await p.waitForTimeout(80);
  }
  await p.waitForTimeout(600);
  await p.screenshot({ path: png });
  const caption = await p.$eval(".vel-caption", (e) => e.textContent).catch(() => "(no velocity graph)");
  const bad = errs.filter((e) => !/ERR_TUNNEL_CONNECTION_FAILED|fonts\.g/.test(e));
  console.log(`velocity graph: ${caption}`);
  console.log(`on-screen keyboard velocity (top, middle, bottom of a key): ${vels.join(", ")}`);
  console.log(`console problems: ${bad.length ? bad.join(" | ") : "none"}`);
  console.log(`screenshot: ${png}`);
  await b.close();
  process.exit(bad.length ? 1 : 0);
})();
