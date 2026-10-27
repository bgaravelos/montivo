// The Montivo logo sting: the in and out brackets close in, the V drops between them and makes the M, a playhead sweeps
// the wordmark on, and the version tag counts v1 -> v2. Every frame is drawn by render(t), so the capture is exact.
//
//   node assets/brand/src/sting.mjs            # needs playwright-core (PLAYWRIGHT_CORE=/path) and ffmpeg
//
// Writes sting/montivo-sting-16x9.mp4 and montivo-sting-9x16.mp4 (30 fps, 2.4 s, H.264).
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, readdirSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { homedir, tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const BRAND = join(HERE, "..");
const FPS = 30, SECONDS = 2.4;

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

// the lockup's geometry straight from the built SVG: viewBox, brackets, V, wordmark
const lockup = readFileSync(join(BRAND, "svg/montivo-lockup-dark.svg"), "utf8");
const view = lockup.match(/viewBox="([^"]+)"/)[1].split(" ").map(Number);
const [brackets, vee, word] = [...lockup.matchAll(/<path fill="[^"]+" d="([^"]+)"/g)].map((m) => m[1]);
const C = JSON.parse(readFileSync(join(BRAND, "tokens.json"), "utf8")).color;

const page = (w, h, logoW) => `<!doctype html><html><head><style>
  @font-face { font-family: JBM; font-weight: 700; src: url(data:font/ttf;base64,${readFileSync(join(BRAND, "fonts/JetBrainsMono-700.ttf")).toString("base64")}); }
  html, body { margin: 0; width: ${w}px; height: ${h}px; background: ${C.ink.hex}; overflow: hidden; }
  #stage { position: absolute; left: 50%; top: 50%; width: ${logoW}px; transform: translate(calc(-50% - ${logoW * 0.068}px), -50%); }  /* centre the logo and the tag together */
  svg { display: block; width: 100%; overflow: visible; }
  #tag { position: absolute; font: 700 ${logoW * 0.052}px/1 JBM; background: ${C.marker.hex}; color: ${C.ink.hex};
         padding: .14em .42em; border-radius: .24em; transform-origin: left center; }
</style></head><body><div id="stage">
  <svg viewBox="${view.join(" ")}">
    <defs><clipPath id="wclip"><rect id="wrect" x="${view[0]}" y="${view[1] - 10}" width="0" height="${view[3] + 20}"/></clipPath>
          <clipPath id="vclip"><rect id="vrect" x="0" y="0" width="64" height="0"/></clipPath></defs>
    <g id="bl"><path fill="${C.paper.hex}" d="${brackets.split(" M")[0]}"/></g>
    <g id="br"><path fill="${C.paper.hex}" d="M${brackets.split(" M")[1]}"/></g>
    <path fill="${C.marker.hex}" d="${vee}" clip-path="url(#vclip)"/>
    <path fill="${C.paper.hex}" d="${word}" clip-path="url(#wclip)"/>
    <rect id="ph" y="${view[1] - 4}" width="2.4" height="${view[3] + 8}" fill="${C.marker.hex}"/>
  </svg><span id="tag">v1</span></div>
<script>
  const V = ${JSON.stringify(view)};
  const clamp = (x) => Math.max(0, Math.min(1, x));
  const seg = (t, a, b) => clamp((t - a) / (b - a));
  const out3 = (x) => 1 - Math.pow(1 - x, 3);
  const inout = (x) => x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
  const back = (x) => { const c = 1.9; return 1 + (c + 1) * Math.pow(x - 1, 3) + c * Math.pow(x - 1, 2); };
  const wordX0 = 73, wordX1 = V[0] + V[2];  // where the wordmark's ink starts and ends
  window.render = (t) => {
    // 1. in and out points close in (0 to .38 s)
    const b = out3(seg(t, 0, .38));
    bl.style.transform = "translateX(" + (-26 * (1 - b)) + "px)"; br.style.transform = "translateX(" + (26 * (1 - b)) + "px)";
    bl.style.opacity = br.style.opacity = b;
    // 2. the V drops in from the top and makes the M (.26 to .62 s)
    vrect.setAttribute("height", 64 * inout(seg(t, .26, .62)));
    // 3. a playhead sweeps the wordmark on (.62 to 1.18 s), then leaves
    const s = inout(seg(t, .62, 1.18));
    const x = wordX0 - 4 + (wordX1 - wordX0 + 8) * s;
    wrect.setAttribute("width", Math.max(0, x - V[0]));
    ph.setAttribute("x", x - 1.2);
    ph.style.opacity = t < .62 ? 0 : 1 - seg(t, 1.18, 1.34);
    // 4. the version tag pops after the wordmark (1.24 s) and counts to v2 (1.66 s)
    const p = seg(t, 1.24, 1.5);
    tag.style.left = "calc(100% + " + (${logoW} * .03) + "px)";
    tag.style.bottom = ((V[1] + V[3] - 58) / V[3] * 100) + "%";  // on the wordmark's baseline (y = 58)
    tag.style.opacity = p > 0 ? 1 : 0;
    tag.style.transform = "scale(" + (p > 0 ? back(p) : 0) + ")";
    tag.textContent = t >= 1.66 ? "v2" : "v1";
    const flip = seg(t, 1.66, 1.8);
    tag.style.filter = flip > 0 && flip < 1 ? "brightness(" + (1 + .25 * Math.sin(flip * Math.PI)) + ")" : "none";
  };
  render(0);
</script></body></html>`;

const { chromium } = playwright();
const shell = process.env.CHROME || readdirSync(join(homedir(), "Library/Caches/ms-playwright"))
  .filter((d) => d.startsWith("chromium_headless_shell-")).sort().reverse()
  .map((d) => join(homedir(), "Library/Caches/ms-playwright", d, "chrome-headless-shell-mac-arm64/chrome-headless-shell"))
  .find(existsSync);
const browser = await chromium.launch({ executablePath: shell });
mkdirSync(join(BRAND, "sting"), { recursive: true });
for (const [name, w, h, logoW] of [["16x9", 1920, 1080, 980], ["9x16", 1080, 1920, 760]]) {
  const tab = await browser.newPage({ viewport: { width: w, height: h } });
  await tab.setContent(page(w, h, logoW));
  await tab.evaluate(() => document.fonts.ready);
  const dir = mkdtempSync(join(tmpdir(), "sting-"));
  const n = Math.round(FPS * SECONDS);
  for (let i = 0; i < n; i++) {
    await tab.evaluate((t) => window.render(t), i / FPS);
    await tab.screenshot({ path: join(dir, `f${String(i).padStart(4, "0")}.png`) });
  }
  const out = join(BRAND, "sting", `montivo-sting-${name}.mp4`);
  execFileSync("ffmpeg", ["-v", "error", "-y", "-framerate", String(FPS), "-i", join(dir, "f%04d.png"), "-c:v", "libx264",
    "-pix_fmt", "yuv420p", "-crf", "16", "-preset", "slow", "-movflags", "+faststart", out]);
  rmSync(dir, { recursive: true });
  console.log(`sting ${out} (${n} frames)`);
  await tab.close();
}
await browser.close();
