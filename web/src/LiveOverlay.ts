import type { LiveData, LiveLook } from "./Live";

/** The live preview's graphics in HTML, drawn where the render burns them (engine/preview.py lays them out with the
 *  render's own code): caption lines with the spoken word marked, callout pills, B-roll stills and the title card.
 *  Everything sits in a layer of the frame's own pixels (1080 x 1920 for 9:16), scaled to the viewer. The Pillow
 *  render stays the truth: here fonts and strokes come from CSS, close but not pixel-exact. */
export class LiveOverlay {
  readonly root: HTMLDivElement;
  private stills: { el: HTMLImageElement; a: number; b: number; fade: number }[] = [];
  private lines: { el: HTMLDivElement; words: HTMLSpanElement[]; a: number; b: number; starts: number[]; on: boolean; act: number; mode: string }[] = [];
  private callouts: { el: HTMLDivElement; a: number; b: number; on: boolean }[] = [];
  private title: { el: HTMLDivElement; img: HTMLImageElement; s: number; fade: number } | null = null;

  constructor(private data: LiveData) {
    this.root = document.createElement("div");
    this.root.className = "live-ov";
    this.build();
  }

  setData(data: LiveData) {
    this.data = data;
    this.root.replaceChildren();
    this.stills = []; this.lines = []; this.callouts = []; this.title = null;
    this.build();
  }

  /** Scale the frame-pixel layer to the viewer's width. */
  layout(boxW: number) {
    const k = boxW / this.data.frame.width;
    this.root.style.width = `${this.data.frame.width}px`;
    this.root.style.height = `${this.data.frame.height}px`;
    this.root.style.transform = `scale(${k})`;
  }

  private build() {
    const d = this.data, look = d.look;
    loadFonts(d);
    const familyOf = (lk?: LiveLook) => (lk ? `"montivo-${lk.font}", "Inter Tight", system-ui, sans-serif` : "system-ui");
    const paint = (el: HTMLElement, lk?: LiveLook) => { // the active word and name colours, per element: a clip may have its own look (S4)
      el.style.setProperty("--hl", hex(lk?.highlight || lk?.text || "#FFFFFF"));
      el.style.setProperty("--act-bg", hex(lk?.active_bg ?? "#FFC629"));
      el.style.setProperty("--act-fg", hex(lk?.active_fg ?? "#000000"));
      el.style.textTransform = lk?.upper ? "uppercase" : "none";
    };
    paint(this.root, look);
    for (const s of d.stills ?? []) {
      const el = document.createElement("img");
      el.className = "lo-still";
      el.src = s.url;
      Object.assign(el.style, { left: `${s.x}px`, top: `${s.y}px`, width: `${s.w}px`, height: `${s.h}px`, opacity: "0" });
      this.root.append(el);
      this.stills.push({ el, a: s.t_in, b: s.t_out, fade: s.fade_s || 0 });
    }
    for (const c of d.captions ?? []) {
      const el = document.createElement("div");
      el.className = "lo-line";
      const look = c.look ?? d.look, family = familyOf(look);
      if (c.look) paint(el, look);
      const [x, y, w, h] = c.box;
      const lh = c.ascent + c.descent;
      Object.assign(el.style, {
        left: `${x}px`, top: `${y}px`, width: `${w}px`, height: `${h}px`, fontFamily: family, fontSize: `${c.size}px`,
        lineHeight: `${lh}px`, gap: `${c.space}px`, padding: `${c.pad_y + c.stroke}px ${c.pad_x + c.stroke}px`,
        color: hex(look?.text ?? "#FFFFFF"), display: "none",
      });
      if (look?.box) Object.assign(el.style, { background: hex(look.box), borderRadius: `${Math.max(8, h / 4)}px` });
      if (look?.shadow) el.style.textShadow = `0 ${Math.max(2, c.size / 24)}px ${Math.max(2, c.size / 18)}px rgba(0,0,0,.9)`;
      const words = c.words.map((wd) => {
        const sp = document.createElement("span");
        sp.className = "lo-w";
        sp.textContent = wd.w;
        if (c.stroke && look?.stroke) {
          sp.style.setProperty("-webkit-text-stroke", `${2 * c.stroke}px ${hex(look.stroke)}`);
          sp.style.setProperty("paint-order", "stroke fill");
        }
        if (wd.hl && look?.highlight) sp.dataset.hl = "1";
        sp.style.setProperty("--pad", `${c.pad_y}px ${c.pad_x}px`);
        sp.style.setProperty("--r", `${Math.max(8, Math.round((18 * c.size) / 84))}px`);
        el.append(sp);
        return sp;
      });
      this.root.append(el);
      this.lines.push({ el, words, a: c.t_in, b: c.t_out, on: false, act: -2, mode: look?.active ?? "pill",
        starts: c.words.map((wd, k) => (k === 0 ? c.t_in : wd.t_in)) });
    }
    for (const c of d.callouts ?? []) {
      const el = document.createElement("div");
      el.className = "lo-callout";
      el.textContent = c.text;
      const look = c.look ?? d.look, family = familyOf(look);
      const [x, y, w, h] = c.box;
      Object.assign(el.style, {
        left: `${x}px`, top: `${y}px`, width: `${w}px`, height: `${h}px`, fontFamily: family, fontSize: `${c.size}px`,
        lineHeight: `${c.ascent + c.descent}px`, borderRadius: `${h / 2}px`, display: "none",
        background: hex(look?.callout_bg ?? "#FFC629", 240), color: hex(look?.callout_fg ?? "#000000"),
        border: `4px solid ${hex(look?.callout_fg ?? "#000000")}`,
      });
      this.root.append(el);
      this.callouts.push({ el, a: c.t_in, b: c.t_out, on: false });
    }
    if (d.title) {
      const el = document.createElement("div");
      el.className = "lo-title";
      const img = document.createElement("img");
      if (d.title.url) img.src = d.title.url;
      else { // new title words in a draft: Muse Image draws the card at Make Version; until then the words on ink
        el.classList.add("lo-title-words");
        const w = document.createElement("span");
        w.textContent = d.title.text ?? "";
        el.append(w);
      }
      el.append(img);
      this.root.append(el);
      this.title = { el, img, s: d.title.seconds, fade: d.title.fade_s ?? 0.3 };
    }
    this.apply(-1);
  }

  /** Show what is on screen at cut time t. */
  apply(t: number) {
    for (const s of this.stills) {
      const on = t >= s.a && t < s.b;
      const o = !on ? 0 : s.fade > 0 ? Math.min(1, (t - s.a) / s.fade, (s.b - t) / s.fade) : 1;
      s.el.style.opacity = String(o);
    }
    for (const ln of this.lines) {
      const on = t >= ln.a && t < ln.b;
      if (on !== ln.on) { ln.el.style.display = on ? "flex" : "none"; ln.on = on; }
      if (!on) continue;
      let k = 0;
      while (k + 1 < ln.starts.length && t >= ln.starts[k + 1]) k++;
      if (k === ln.act) continue;
      ln.act = k;
      ln.words.forEach((sp, i) => {
        const said = ln.mode === "karaoke" ? i <= k : i === k;
        sp.className = `lo-w ${said && ln.mode !== "none" ? `act-${ln.mode}` : ""}`;
      });
    }
    for (const c of this.callouts) {
      const on = t >= c.a && t < c.b;
      if (on !== c.on) { c.el.style.display = on ? "flex" : "none"; c.on = on; }
    }
    if (this.title) {
      const { el, img, s, fade } = this.title;
      el.style.display = t >= 0 && t < s ? "block" : "none";
      img.style.opacity = String(Math.max(0, Math.min(1, (s - t) / Math.max(1e-3, fade))));
    }
  }
}

/** #RRGGBB or #RRGGBBAA (the looks' colours) as CSS; `alpha` caps the alpha (0-255). */
function hex(c: string, alpha = 255): string {
  const m = /^#([0-9a-f]{6})([0-9a-f]{2})?$/i.exec(c);
  if (!m) return c;
  const a = Math.min(alpha, m[2] ? parseInt(m[2], 16) : 255);
  return `#${m[1]}${a.toString(16).padStart(2, "0")}`;
}

const loaded = new Set<string>();
/** The look's font from the server (montivo/fonts, OFL) as an @font-face, once per page. */
function loadFonts(d: LiveData) {
  for (const [key, f] of Object.entries(d.fonts ?? {})) {
    if (loaded.has(key)) continue;
    loaded.add(key);
    const face = new FontFace(`montivo-${key}`, `url(${f.url})`);
    face.load().then((x) => document.fonts.add(x)).catch(() => loaded.delete(key));
  }
}
