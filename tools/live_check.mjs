// The live preview's go / no-go check (web/src/Live.ts): plays one version in headless Chrome through the editor
// (?live=1) and measures what the eye would catch at the cuts. $0, needs a running `montivo serve` and playwright-core.
//
//   node tools/live_check.mjs RUN [--base http://localhost:8765] [--seeks 6]
//
// Prints JSON: every swap at a cut (how late, by the frame callback or the animation frame), the picture's offset
// from the audio clock per displayed frame (frames), frames shown from past a segment's out point, freezes (two
// displayed frames more than 2.5 frame times apart), and seek-to-play latency at a few points.
import { createRequire } from "node:module";
import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

function playwright() {
  const require = createRequire(import.meta.url);
  if (process.env.PLAYWRIGHT_CORE) return require(process.env.PLAYWRIGHT_CORE);
  try { return require("playwright-core"); } catch {}
  const npx = join(homedir(), ".npm/_npx");
  for (const d of existsSync(npx) ? readdirSync(npx) : []) {
    const p = join(npx, d, "node_modules/playwright-core");
    if (existsSync(p)) return require(p);
  }
  throw new Error("playwright-core not found: set PLAYWRIGHT_CORE=/path/to/playwright-core");
}

const args = process.argv.slice(2);
const run = args[0];
if (!run) { console.error("usage: node tools/live_check.mjs RUN [--base URL] [--seeks N]"); process.exit(2); }
const opt = (name, dflt) => { const i = args.indexOf(name); return i > 0 ? args[i + 1] : dflt; };
const base = opt("--base", "http://localhost:8765");
const seeks = Number(opt("--seeks", "6"));
const chrome = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"; // H.264 needs Chrome, not Chromium

const { chromium } = playwright();
const browser = await chromium.launch({ executablePath: chrome, headless: true, args: ["--autoplay-policy=no-user-gesture-required"] });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
await page.goto(`${base}/#/run/${run}?live=1`);
await page.waitForFunction(() => window.__montivoLive?.buffer, null, { timeout: 120_000 });

const out = await page.evaluate(async ({ seeks }) => {
  const L = window.__montivoLive;
  const fps = L.data.fps, segs = L.data.segments;
  const vids = [...document.querySelectorAll(".live-box video")];
  const frames = [];
  let logging = false;
  vids.forEach((v, slot) => {
    const on = (now, m) => {
      if (logging && L.shown === slot) frames.push({ slot, now, media: m.mediaTime, cut: L.currentTime, seg: L.cued[slot] });
      v.requestVideoFrameCallback(on);
    };
    v.requestVideoFrameCallback(on);
  });
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  L.stats = { swaps: [], drift: [], firstFrameMs: [], ticks: 0 };
  L.currentTime = 0;
  await sleep(400);
  logging = true;
  const t0 = performance.now();
  await L.play();
  while (!L.paused && performance.now() - t0 < (L.duration + 5) * 1000) await sleep(100);
  logging = false;
  const wall = (performance.now() - t0) / 1000;

  const off = [], beyond = [], freezes = [];
  for (let i = 0; i < frames.length; i++) {
    const f = frames[i], s = segs[f.seg];
    if (f.cut >= s.cut_t0) {
      const want = s.t_in + Math.min(f.cut - s.cut_t0, s.frames / fps);
      off.push((f.media - want) * fps);
    }
    if (f.media >= s.t_out - 0.25 / fps || f.media < s.t_in - 0.25 / fps) beyond.push({ seg: f.seg, media: +f.media.toFixed(3) });
    if (i > 0 && f.now - frames[i - 1].now > 2.5 * 1000 / fps) freezes.push({ at_cut: +f.cut.toFixed(2), ms: Math.round(f.now - frames[i - 1].now) });
  }
  const abs = off.map(Math.abs).sort((a, b) => a - b);
  const q = (p) => (abs.length ? +abs[Math.floor(p * (abs.length - 1))].toFixed(2) : null);

  const seekMs = [];
  for (let k = 0; k < seeks; k++) {
    const p = ((k + 0.37) / seeks) * L.duration;
    L.pause(); L.currentTime = p; await sleep(500);
    L.stats.firstFrameMs = [];
    await L.play();
    const s0 = performance.now();
    while (!L.stats.firstFrameMs.length && performance.now() - s0 < 3000) await sleep(5);
    seekMs.push(L.stats.firstFrameMs[0] ?? null);
    await sleep(300);
  }
  L.pause();
  return {
    version: L.data.version, duration_s: L.duration, segments: segs.length, wall_s: +wall.toFixed(2),
    ticks_per_s: +(L.stats.ticks / wall).toFixed(1), frames_shown: frames.length, expected_frames: Math.round(L.duration * fps),
    swaps: L.stats.swaps, offset_frames: { p50: q(0.5), p95: q(0.95), p99: q(0.99), max: q(1) },
    frames_past_cut: beyond.length, beyond: beyond.slice(0, 8), freezes, seek_to_play_ms: seekMs,
  };
}, { seeks });
out.errors = errors;
console.log(JSON.stringify(out, null, 2));
await browser.close();
