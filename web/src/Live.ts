import { LiveOverlay } from "./LiveOverlay";
import { audioCtx } from "./Mix";
import type { Version } from "./types";

/** One render segment as GET /preview gives it: source in/out, its place in the cut, the crop x of every source
 *  frame and the punch-in zoom (engine/punch.zoom_at's curve over segment-local seconds, about an anchor in frame px). */
export type LiveSeg = {
  index: number; t_in: number; t_out: number; frames: number; cut_t0: number; span_ids: string[];
  crop: { w: number; h: number; y: number; xs: number[] };
  zoom?: { base: number; anchor: [number, number]; events: { a: number; b: number; c: number; d: number; z: number }[] };
};
export type LiveLook = {
  font: string; text: string; stroke: string; active: string; active_fg: string; active_bg: string; highlight: string;
  box: string; shadow: boolean; upper: boolean; callout_bg: string; callout_fg: string;
};
export type LiveData = {
  version: number | null; fps: number; duration_s: number; segments: LiveSeg[];
  source: { width: number; height: number }; frame: { aspect: string; width: number; height: number };
  title: { seconds: number; url: string; fade_s?: number } | null;
  media: { video: string; audio: string; width: number; height: number; bytes: number };
  gain_db: number | null;
  look?: LiveLook;
  fonts?: Record<string, { name: string; url: string }>;
  captions?: { id: string; t_in: number; t_out: number; box: [number, number, number, number]; placement: string;
    words: { w: string; t_in: number; t_out: number; hl: boolean }[];
    size: number; stroke: number; space: number; pad_x: number; pad_y: number; ascent: number; descent: number }[];
  callouts?: { span: string; text: string; t_in: number; t_out: number; box: [number, number, number, number]; size: number; ascent: number; descent: number }[];
  stills?: { id: string; placement: string; t_in: number; t_out: number; x: number; y: number; w: number; h: number; fade_s: number; url: string }[];
  draft?: { id: string; base: number; ops: number; n: number };
  version_entry?: Version;
};

const LEAD_S = 0.03; // audio and picture start this far ahead of "now", so the first buffer is never late
const RAMP_S = 0.005; // a short gain ramp at every cut edge: no click (the render crossfades 20 ms, engine/render.cut_graph)
const HARD_DRIFT_S = 0.25; // the picture this far off the audio clock seeks; less is pulled in by the playback rate
const PREROLL_S = 0.15; // the next segment's <video> starts playing (hidden) this far ahead of its cut: play() takes 30-80 ms

type Stats = { swaps: { seg: number; late_ms: number; ready: number; by: string }[]; drift: number[]; firstFrameMs: number[]; ticks: number };

/** The cut played from the edit proxy without a render: the audio leads (one AudioBufferSourceNode per segment on the
 *  Web Audio clock, the render's loudness gain into a limiter), two <video> elements take turns on the picture (the
 *  next one waits paused on its in point), each frame's 9:16 crop is a CSS transform from the render's own crop path,
 *  a punch-in a CSS scale under the graphics, and the graphics are HTML (LiveOverlay). It answers the part of
 *  HTMLVideoElement the Player uses (currentTime, paused, play, pause, playbackRate, duration, loop, muted), so the
 *  Player's transport drives it unchanged. Times are cut seconds. setData() swaps in another EDL of the same source
 *  (a draft edit) without loading the media again. */
export class LiveMedia {
  duration: number;
  paused = true;
  loop = false;
  stats: Stats = { swaps: [], drift: [], firstFrameMs: [], ticks: 0 };
  onstate: (() => void) | null = null;
  private buffer: AudioBuffer | null = null;
  private master: GainNode | null = null;
  private level: GainNode | null = null;
  private nodes: AudioBufferSourceNode[] = [];
  private anchorCtx = 0;
  private anchorT = 0;
  private at = 0; // the cut time while paused
  private rate = 1;
  private mute = false;
  private cued: [number, number] = [-1, -1]; // the segment each <video> stands on
  private shown = 0;
  private raf = 0;
  private sizes = { w: 0, h: 0 };
  private playCalled = 0;
  private dead = false;
  private vids: [HTMLVideoElement, HTMLVideoElement];
  private zoom: HTMLDivElement;
  private overlay: LiveOverlay;
  readonly ready: Promise<void>;

  constructor(public data: LiveData, private box: HTMLElement) {
    this.duration = data.duration_s;
    this.ready = fetch(data.media.audio)
      .then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(`edit audio: ${r.status}`))))
      .then((b) => audioCtx().decodeAudioData(b))
      .then((buf) => { this.buffer = buf; });
    this.zoom = document.createElement("div");
    this.zoom.className = "live-zoom";
    this.vids = [document.createElement("video"), document.createElement("video")];
    for (const [k, v] of this.vids.entries()) {
      v.muted = true;
      v.playsInline = true;
      v.preload = "auto";
      v.src = data.media.video;
      v.style.opacity = k === 0 ? "1" : "0";
      this.zoom.append(v);
      this.watchFrames(k);
    }
    this.overlay = new LiveOverlay(data);
    box.replaceChildren(this.zoom, this.overlay.root);
    this.layout();
    this.cue(0, this.segAt(0), 0);
    const tick = () => { this.stats.ticks++; this.drive(); this.raf = requestAnimationFrame(tick); };
    this.raf = requestAnimationFrame(tick);
    (window as unknown as { __montivoLive?: LiveMedia }).__montivoLive = this;
  }

  destroy() {
    this.dead = true;
    cancelAnimationFrame(this.raf);
    this.stopAudio();
    this.master?.disconnect();
    for (const v of this.vids) { v.pause(); v.removeAttribute("src"); v.load(); }
    this.box.replaceChildren();
  }

  /** Another EDL of the same source (a draft edit): same media, new segments and graphics, the playhead kept. */
  setData(data: LiveData) {
    const t = Math.min(this.currentTime, data.duration_s);
    this.data = data;
    this.duration = data.duration_s;
    this.overlay.setData(data);
    this.cued = [-1, -1];
    this.layout();
    this.at = t;
    if (!this.paused) this.start(t);
    this.drive();
  }

  get currentTime(): number {
    if (this.paused) return this.at;
    return Math.min(this.duration, this.anchorT + (audioCtx().currentTime - this.anchorCtx) * this.rate);
  }
  set currentTime(t: number) {
    const x = Math.max(0, Math.min(this.duration, t));
    this.at = x;
    if (!this.paused) this.start(x);
    else this.drive();
  }
  get playbackRate() { return this.rate; }
  set playbackRate(r: number) {
    const t = this.currentTime;
    this.rate = r > 0 ? r : 1;
    if (!this.paused) this.start(t);
  }
  get muted() { return this.mute; }
  set muted(m: boolean) {
    this.mute = m;
    if (this.master) this.master.gain.setTargetAtTime(m ? 0 : 1, audioCtx().currentTime, 0.01);
  }

  async play(): Promise<void> {
    if (!this.paused) return;
    const ctx = audioCtx();
    await ctx.resume();
    await this.ready;
    if (this.dead) return;
    if (this.at >= this.duration - 1e-3) this.at = 0;
    this.paused = false;
    this.playCalled = performance.now();
    this.start(this.at);
    this.onstate?.();
  }
  pause() {
    if (this.paused) return;
    this.at = this.currentTime;
    this.paused = true;
    this.stopAudio();
    for (const v of this.vids) v.pause();
    this.drive();
    this.onstate?.();
  }

  /** The output chain once: mute -> the render's loudness gain -> a limiter (the render's -1.5 dBTP ceiling). */
  private chain(): GainNode {
    const ctx = audioCtx();
    if (!this.master) {
      this.master = ctx.createGain();
      this.level = ctx.createGain();
      const lim = ctx.createDynamicsCompressor();
      lim.threshold.value = -1.5; lim.knee.value = 0; lim.ratio.value = 20; lim.attack.value = 0.002; lim.release.value = 0.05;
      this.master.connect(this.level).connect(lim).connect(ctx.destination);
    }
    this.level!.gain.value = Math.pow(10, (this.data.gain_db ?? 0) / 20);
    this.master.gain.value = this.mute ? 0 : 1;
    return this.master;
  }

  /** (Re)start the audio at cut time t: every segment from there on scheduled at once on the context's clock. */
  private start(t: number) {
    const ctx = audioCtx();
    this.stopAudio();
    const out = this.chain();
    const now = ctx.currentTime + LEAD_S;
    this.anchorCtx = now;
    this.anchorT = t;
    const buf = this.buffer;
    if (!buf) return;
    const fps = this.data.fps;
    for (const [k, s] of this.data.segments.entries()) {
      const c1 = s.cut_t0 + s.frames / fps;
      if (c1 <= t) continue;
      const from = Math.max(t, s.cut_t0);
      const when = now + (from - s.cut_t0 > 0 ? 0 : (s.cut_t0 - t) / this.rate);
      const off = s.t_in + (from - s.cut_t0);
      const dur = c1 - from;
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.playbackRate.value = this.rate;
      const g = ctx.createGain();
      const end = when + dur / this.rate;
      g.gain.setValueAtTime(k > 0 && from === s.cut_t0 ? 0 : 1, when);
      g.gain.linearRampToValueAtTime(1, when + RAMP_S);
      g.gain.setValueAtTime(1, Math.max(when + RAMP_S, end - RAMP_S));
      g.gain.linearRampToValueAtTime(k < this.data.segments.length - 1 ? 0 : 1, end);
      src.connect(g).connect(out);
      src.start(when, off, dur);
      this.nodes.push(src);
    }
  }
  private stopAudio() {
    for (const n of this.nodes) { try { n.stop(); } catch { /* not started */ } n.disconnect(); }
    this.nodes = [];
  }

  /** The segment playing at cut time t; -1 on the title card. */
  segAt(t: number): number {
    const segs = this.data.segments;
    if (!segs.length || t < segs[0].cut_t0) return -1;
    for (let k = segs.length - 1; k >= 0; k--) if (t >= segs[k].cut_t0 - 1e-6) return k;
    return 0;
  }
  private srcTime(k: number, t: number): number {
    const s = this.data.segments[k];
    return s.t_in + Math.max(0, Math.min(s.frames / this.data.fps - 0.5 / this.data.fps, t - s.cut_t0));
  }
  private cue(slot: number, k: number, t: number) {
    const v = this.vids[slot];
    if (!this.data.segments.length) return;
    const at = k < 0 ? this.data.segments[0].t_in : this.srcTime(k, t);
    this.cued[slot] = Math.max(k, 0);
    if (Math.abs(v.currentTime - at) > 0.5 / this.data.fps) v.currentTime = at;
  }

  /** Every animation frame: end or loop, the graphics, which <video> shows, the next one cued, the drift held. */
  private drive() {
    const fps = this.data.fps;
    let t = this.currentTime;
    if (!this.paused && t >= this.duration - 1e-3) {
      if (this.loop) { this.start(0); t = 0; } else { this.at = this.duration; this.paused = true; this.stopAudio(); for (const v of this.vids) v.pause(); this.onstate?.(); }
    }
    this.overlay.apply(t);
    if (!this.data.segments.length) return;
    const k = this.segAt(this.paused ? t : t + 0.6 / fps); // playing: a frame callback may swap just before the audio's cut
    let want = Math.max(k, 0);
    const ahead = this.data.segments[this.cued[this.shown]]; // a frame callback already swapped to the next segment
    if (!this.paused && this.cued[this.shown] === want + 1 && ahead && ahead.cut_t0 - t < 0.15) want += 1;
    if (this.cued[this.shown] !== want) {
      const other = 1 - this.shown;
      if (this.cued[other] === want) this.swap(t, k >= 0, "frame");
      else this.cue(this.shown, k, t);
    }
    this.punch(want, t);
    const v = this.vids[this.shown];
    if (k >= 0) {
      const s = this.srcTime(want, t);
      const drift = v.currentTime - s;
      if (this.paused) {
        if (!v.paused) v.pause();
        if (Math.abs(drift) > 0.5 / fps && !v.seeking) v.currentTime = s;
      } else {
        if (v.paused) { v.playbackRate = this.rate; v.play().catch(() => {}); }
        if (Math.abs(drift) > HARD_DRIFT_S) v.currentTime = s;
        else v.playbackRate = this.rate * (1 - Math.max(-0.08, Math.min(0.08, drift * 2)));
        if (!v.seeking && this.stats.drift.length < 20000) this.stats.drift.push(drift);
      }
    } else if (!v.paused) v.pause();
    const next = want + 1; // the next cut's <video> waits on its in point, and runs hidden for the last PREROLL_S
    const other = 1 - this.shown;
    if (next >= this.data.segments.length) return;
    const nx = this.data.segments[next], o = this.vids[other];
    const lead = nx.cut_t0 - t;
    if (this.cued[other] !== next) {
      o.pause();
      this.cued[other] = next;
      o.currentTime = this.paused || lead > PREROLL_S ? nx.t_in : Math.max(0, nx.t_in - lead);
      return;
    }
    if (this.paused || lead > PREROLL_S + 0.05) {
      if (!o.paused) { o.pause(); if (!o.seeking) o.currentTime = nx.t_in; }
      return;
    }
    if (o.seeking) return;
    const drift = o.currentTime - (nx.t_in - lead);
    if (o.paused) {
      if (lead > 0.02) { // start it where the cut will find it
        if (Math.abs(drift) > 1 / this.data.fps) o.currentTime = Math.max(0, nx.t_in - lead);
        o.playbackRate = this.rate; o.play().catch(() => {});
      }
    } else o.playbackRate = this.rate * (1 - Math.max(-0.25, Math.min(0.25, drift * 3)));
  }

  /** The punch-in of segment k at cut time t: engine/punch.zoom_at, a scale about the anchor (the render's zoompan
   *  after the crop and before the graphics, so captions and callouts never zoom). */
  private punch(k: number, t: number) {
    const z = this.data.segments[k]?.zoom;
    if (!z) { this.zoom.style.transform = ""; return; }
    const u = t - this.data.segments[k].cut_t0;
    let s = 1;
    for (const e of z.events) {
      const up = e.b <= e.a ? 1 : Math.min(1, Math.max(0, (u - e.a) / (e.b - e.a)));
      const down = Math.min(1, Math.max(0, (e.d - u) / Math.max(1e-9, e.d - e.c)));
      s += (e.z - 1) * up * down;
    }
    const k2 = this.sizes.w / this.data.frame.width;
    this.zoom.style.transformOrigin = `${z.anchor[0] * k2}px ${z.anchor[1] * k2}px`;
    this.zoom.style.transform = `scale(${z.base * s})`;
  }

  /** The cut: the <video> waiting on the next segment's in point takes over. The shown one calls it from its own
   *  frame callback when its next frame lies past the segment's out point (frame-exact), the animation frame when it
   *  did not (a seek, a stalled decoder). */
  private swap(t: number, play: boolean, by: string) {
    const other = 1 - this.shown;
    const v = this.vids[other];
    const seg = this.data.segments[this.cued[other]];
    this.stats.swaps.push({ seg: this.cued[other], late_ms: Math.round((t - seg.cut_t0) * 1000), ready: v.readyState, by });
    if (play && !this.paused) { v.playbackRate = this.rate; v.play().catch(() => {}); }
    v.style.opacity = "1";
    this.vids[this.shown].style.opacity = "0";
    this.vids[this.shown].pause();
    this.shown = other;
  }

  /** The crop of the frame each <video> is about to show, set on that frame (requestVideoFrameCallback). */
  private watchFrames(slot: number) {
    const v = this.vids[slot] as HTMLVideoElement & { requestVideoFrameCallback?: (cb: (now: number, m: { mediaTime: number }) => void) => number };
    const place = (mediaTime: number) => {
      const s = this.data.segments[this.cued[slot]];
      if (!s) return;
      if (slot === this.shown && !this.paused && mediaTime >= s.t_out - 0.5 / this.data.fps
          && this.cued[1 - slot] === this.cued[slot] + 1) {
        this.swap(this.currentTime, true, "video");
        return;
      }
      const i = Math.max(0, Math.min(s.crop.xs.length - 1, Math.round((mediaTime - s.t_in) * this.data.fps)));
      this.transform(v, s, s.crop.xs[i] ?? 0);
      if (this.playCalled && slot === this.shown && !this.paused) {
        this.stats.firstFrameMs.push(Math.round(performance.now() - this.playCalled));
        this.playCalled = 0;
      }
    };
    if (v.requestVideoFrameCallback) {
      const on = (_: number, m: { mediaTime: number }) => { place(m.mediaTime); if (!this.dead) v.requestVideoFrameCallback!(on); };
      v.requestVideoFrameCallback(on);
    } else {
      v.addEventListener("timeupdate", () => place(v.currentTime));
      v.addEventListener("seeked", () => place(v.currentTime));
    }
  }
  private transform(v: HTMLVideoElement, s: LiveSeg, x: number) {
    const k = this.sizes.w / s.crop.w;
    v.style.width = `${this.data.source.width * k}px`;
    v.style.height = `${this.data.source.height * k}px`;
    v.style.transform = `translate3d(${-x * k}px, ${-s.crop.y * k}px, 0)`;
  }
  layout() {
    const r = this.box.getBoundingClientRect();
    this.sizes = { w: r.width, h: r.height };
    this.overlay.layout(r.width);
    for (const [slot, v] of this.vids.entries()) {
      const s = this.data.segments[this.cued[slot]] ?? this.data.segments[0];
      if (s) this.transform(v, s, s.crop.xs[Math.max(0, Math.min(s.crop.xs.length - 1, Math.round((v.currentTime - s.t_in) * this.data.fps)))] ?? 0);
    }
  }
}
