import { useEffect, useRef, useState } from "react";
import type { Version } from "./types";

// The timeline's faders, heard live. A version with music has two stems next to its cut (render/cut_vN.speech.m4a and
// .music.m4a, GET /api/runs/{id}/stem): while a fader is moved off the version's own mix, the video plays silent and
// the stems play through Web Audio gains, kept on the video's clock. "Make Version N" renders the same mix ($0).

/** The two faders: dB on the speech against the bed (EDL music.voice_db) and the bed's level where nobody speaks. */
export type Mix = { voice_db: number; level_db: number };

export const VOICE_RANGE: [number, number] = [-12, 6]; // engine/music.py VOICE_RANGE
export const LEVEL_RANGE: [number, number] = [-36, -10]; // engine/music.py LEVEL_RANGE

export const mixOf = (v: Version | null): Mix | null =>
  v?.music ? { voice_db: v.music.voice_db ?? 0, level_db: v.music.level_db } : null;
export const sameMix = (a: Mix | null, b: Mix | null) =>
  (!a && !b) || (!!a && !!b && a.voice_db === b.voice_db && a.level_db === b.level_db);

const db = (x: number) => 10 ** (x / 20);
const DRIFT_S = 0.08; // restart the stems when they drift this far from the video

/** The gains (linear) that make the stems sound like the render of `mix`. The stems are what the render mixed, before
 *  the mix: the speech normalised to -14 LUFS without the voice fader, the bed at its level (render.render_audio). The
 *  render moves the speech by voice_db, adds the bed, then normalises the mix to -14 LUFS: the speech, mostly what the
 *  loudness measures, comes back to about -14 and the bed goes down by voice_db and the bed's share, `margin_db` under
 *  the speech (measured by the render with the version's voice_db; each fader moves it). Measured on a 015 render at
 *  voice +6: speech stem x0.987, bed stem x0.494 (-6.1 dB); unity on both used to play the bed 6 dB too loud. */
export function liveGains(base: Mix, mix: Mix, margin_db: number | null): { voice: number; music: number } {
  const dv = mix.voice_db - base.voice_db, dm = mix.level_db - base.level_db;
  const margin = (margin_db ?? 18) + dv - dm;
  const share = 10 * Math.log10(1 + 10 ** (-margin / 10)); // the bed's part of the mix's loudness, taken off both
  return { voice: db(-share), music: db(dm - mix.voice_db - share) };
}

let shared: AudioContext | null = null;
/** One AudioContext for the page. On iOS, Web Audio follows the ring / silent switch unless the page's audio session is
 *  "playback" (Safari 16.4+): Live mutes its videos and plays every sound through here, so without it a phone on silent
 *  heard nothing in Live while Rendered played (audit 31/10). */
export const audioCtx = () => {
  if (!shared) {
    const session = (navigator as Navigator & { audioSession?: { type: string } }).audioSession;
    if (session) try { session.type = "playback"; } catch { /* older Safari: the switch rules */ }
    shared = new AudioContext();
  }
  return shared;
};

type Stems = { n: number; voice: AudioBuffer; music: AudioBuffer };

/** Loads the version's stems (once it has music) and, while `live`, plays them in place of the video's own audio.
 *  `player` names the player on screen: a new one (V between Live and Rendered remounts it) has a new <video>. */
export function useLiveMix(id: string, v: Version | null, video: () => HTMLVideoElement | null, mix: Mix | null, live: boolean, player = "") {
  const [stems, setStems] = useState<Stems | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const gains = useRef<{ voice: GainNode; music: GainNode; master: GainNode } | null>(null);
  const n = v?.music && v.cut ? v.n : null;

  useEffect(() => { // fetch + decode both stems of this version
    setStems(null); setFailed(null);
    if (n == null) return;
    let on = true;
    const ctx = audioCtx();
    const q = v?.bytes ? `&b=${v.bytes}` : "";
    const load = (track: string) => fetch(`/api/runs/${id}/stem?v=${n}&track=${track}${q}`)
      .then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(`${track} stem: ${r.status}`))))
      .then((b) => ctx.decodeAudioData(b));
    Promise.all([load("voice"), load("music")])
      .then(([voice, music]) => { if (on) setStems({ n, voice, music }); })
      .catch((e) => { if (on) setFailed(String(e.message ?? e)); });
    return () => { on = false; };
  }, [id, n, v?.bytes]); // eslint-disable-line

  const ready = !!stems && stems.n === n;
  const active = live && ready;

  useEffect(() => { // the gains follow the faders smoothly
    const g = gains.current;
    if (!g || !v || !mix) return;
    const base = mixOf(v)!;
    const x = liveGains(base, mix, v.music?.margin_db ?? null);
    const t = audioCtx().currentTime;
    g.voice.gain.setTargetAtTime(x.voice, t, 0.03);
    g.music.gain.setTargetAtTime(x.music, t, 0.03);
  });

  useEffect(() => { // play the stems on the video's clock
    const el = video();
    if (!active || !el || !stems || !v || !mix) return;
    const ctx = audioCtx();
    const master = ctx.createGain();
    const gv = ctx.createGain(), gm = ctx.createGain();
    const x = liveGains(mixOf(v)!, mix, v.music?.margin_db ?? null);
    gv.gain.value = x.voice; gm.gain.value = x.music;
    gv.connect(master); gm.connect(master); master.connect(ctx.destination);
    gains.current = { voice: gv, music: gm, master };
    let srcs: AudioBufferSourceNode[] = [];
    let at = 0, t0 = 0, rate = 1;
    const stop = () => { srcs.forEach((s) => { try { s.stop(); } catch { /* not started */ } s.disconnect(); }); srcs = []; };
    const start = () => {
      stop();
      if (el.paused || el.playbackRate <= 0) return;
      ctx.resume().catch(() => {});
      at = el.currentTime; rate = el.playbackRate; t0 = ctx.currentTime;
      srcs = ([[stems.voice, gv], [stems.music, gm]] as const).map(([buf, g]) => {
        const s = ctx.createBufferSource();
        s.buffer = buf; s.playbackRate.value = rate; s.connect(g);
        if (at < buf.duration) s.start(0, at);
        return s;
      });
    };
    const muted = () => { master.gain.value = el.muted ? 0 : 1; };
    const prevVol = el.volume;
    el.volume = 0; // the video keeps its clock; the stems are what you hear
    muted();
    const evs: [string, () => void][] = [["playing", start], ["pause", stop], ["waiting", stop], ["seeked", start],
      ["ratechange", start], ["volumechange", muted]];
    evs.forEach(([e, f]) => el.addEventListener(e, f));
    if (!el.paused) start();
    const drift = window.setInterval(() => {
      if (!srcs.length || el.paused) return;
      if (Math.abs(at + (ctx.currentTime - t0) * rate - el.currentTime) > DRIFT_S) start();
    }, 250);
    return () => {
      window.clearInterval(drift);
      evs.forEach(([e, f]) => el.removeEventListener(e, f));
      stop();
      master.disconnect();
      gains.current = null;
      el.volume = prevVol || 1;
    };
  }, [active, stems, v?.n, player]); // eslint-disable-line -- the player: after V to Rendered the stems follow its new <video> (audit, 31/10)

  return { ready, active, failed };
}
