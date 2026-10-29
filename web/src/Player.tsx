import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { tc } from "./api";
import { Icon } from "./icons";
import { LiveMedia, type LiveData } from "./Live";

export const FPS = 30;

/** The viewer's clock: mm:ss.cc (seconds) or mm:ss:ff (frames at FPS); both pad the minutes so the width never jumps. */
export function timecode(s: number, mode: "sec" | "frames"): string {
  const t = Math.max(0, s || 0);
  if (mode === "frames") {
    const f = Math.round(t * FPS);
    const sec = Math.floor(f / FPS);
    return `${String(Math.floor(sec / 60)).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}:${String(f % FPS).padStart(2, "0")}`;
  }
  const cs = Math.round(t * 100); // hundredths, so 59.996 s reads 01:00.00 and not 00:60.00
  return `${String(Math.floor(cs / 6000)).padStart(2, "0")}:${(((cs % 6000) / 100).toFixed(2)).padStart(5, "0")}`;
}
const RATES = [1, 1.5, 2, 0.5];

export type PlayerHandle = {
  seek: (a: number, b?: number) => void; play: () => void; pause: () => void; toggle: () => void;
  step: (dt: number) => void; shuttle: (dir: -1 | 0 | 1) => void; el: () => HTMLVideoElement | null;
  toggleMute: () => void; fullscreen: () => void; skip: (dir: 1 | -1) => void; // to the previous / next cut
  time: () => number; // the cut second on screen (el() is null in live mode)
};
/** What the transport drives: the <video> of a rendered cut, or the live preview (Live.ts) that plays the EDL. */
type Media = { currentTime: number; paused: boolean; playbackRate: number; duration: number; loop: boolean;
  play: () => Promise<void>; pause: () => void };
type Band = { a: number; b: number; sev: string };

/** The 9:16 viewer with its own transport. seek(a, b) loops a..b until the viewer seeks or plays elsewhere; shuttle()
 *  is J/K/L: each press of L or J doubles the speed forward or backward, K stops. `scrub` adds the old scrub bar with
 *  the review's issue windows (Compare); the editor's timeline does that job there. */
export const Player = forwardRef<PlayerHandle, {
  src: string; onTime?: (t: number) => void; bands?: Band[]; marks?: number[]; compact?: boolean; muted?: boolean;
  label?: string; scrub?: boolean; overlay?: React.ReactNode; onPlaying?: (playing: boolean) => void;
  safe?: { label: string; on: boolean; next: () => void }; // the safe-area guides' switch, on the transport's right
  live?: string; // GET /preview of the version: play its EDL from the edit proxy instead of `src`
}>(function Player({ src, onTime, bands = [], marks = [], compact, muted, label, scrub, overlay, onPlaying, safe, live }, ref) {
  const video = useRef<HTMLVideoElement>(null);
  const lm = useRef<LiveMedia | null>(null);
  const media = (): Media | null => (live ? lm.current : video.current);
  const va = useRef<HTMLVideoElement>(null), vb = useRef<HTMLVideoElement>(null);
  const box = useRef<HTMLDivElement>(null), timg = useRef<HTMLImageElement>(null);
  const [liveData, setLiveData] = useState<LiveData | null>(null);
  const [liveErr, setLiveErr] = useState<string | null>(null);
  const loop = useRef<[number, number] | null>(null);
  const back = useRef(0); // reverse shuttle speed, 0 = off
  const last = useRef(-1);
  const [t, setT] = useState(0);
  const [d, setD] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [looping, setLooping] = useState(false);
  const [rate, setRate] = useState(1); // signed: -4..4, shown when not 1
  const [mute, setMute] = useState(!!muted);
  const [whole, setWhole] = useState(false); // loop the whole cut
  const [mode, setMode] = useState<"sec" | "frames">(() => {
    try { return localStorage.getItem("montivo.viewer.tc") === "frames" ? "frames" : "sec"; } catch { return "sec"; }
  });
  const frame = useRef<HTMLDivElement>(null);

  const calm = () => { // a shuttle speed (J/L) ends with the play or pause; a speed picked on the transport stays
    if (rate > 0 && RATES.includes(rate)) return;
    const m = media(); if (m) m.playbackRate = 1;
    setRate(1);
  };
  const clearLoop = () => { loop.current = null; setLooping(false); };
  const stopBack = () => { back.current = 0; };
  const api: PlayerHandle = {
    seek(a, b) {
      const el = media();
      if (!el) return;
      stopBack();
      el.currentTime = Math.max(0, a);
      loop.current = b != null ? [a, b] : null;
      setLooping(b != null);
      if (b != null) el.play().catch(() => {});
    },
    play: () => { stopBack(); media()?.play().catch(() => {}); },
    pause: () => { stopBack(); calm(); media()?.pause(); },
    toggle() {
      const el = media();
      if (!el) return;
      if (back.current) { stopBack(); setRate(1); return; }
      calm();
      if (el.paused) el.play().catch(() => {}); else el.pause();
    },
    step(dt) {
      const el = media();
      if (!el) return;
      stopBack(); el.pause(); clearLoop();
      el.currentTime = Math.max(0, Math.min(el.duration || 0, el.currentTime + dt));
    },
    shuttle(dir) {
      const el = media();
      if (!el) return;
      if (dir === 0) { stopBack(); el.pause(); el.playbackRate = 1; setRate(1); return; }
      if (dir > 0) {
        stopBack();
        const r = !el.paused && el.playbackRate >= 1 ? Math.min(4, el.playbackRate * 2) : 1;
        el.playbackRate = r; setRate(r);
        el.play().catch(() => {});
      } else {
        el.pause(); el.playbackRate = 1; clearLoop();
        back.current = back.current ? Math.min(4, back.current * 2) : 1;
        setRate(-back.current);
      }
    },
    el: () => video.current,
    time: () => media()?.currentTime ?? 0,
    skip: (dir) => skip(dir),
    toggleMute: () => setMute((m) => !m),
    fullscreen() {
      const f = frame.current;
      if (!f) return;
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      else f.requestFullscreen().catch(() => {});
    },
  };
  useImperativeHandle(ref, () => api);

  useEffect(() => { // live mode: the version's segments and crop path, then the edit proxy on two <video>s
    if (!live) return;
    let on = true, made: LiveMedia | null = null;
    setLiveData(null); setLiveErr(null);
    fetch(live).then((r) => (r.ok ? r.json() : r.text().then((x) => Promise.reject(new Error(x || `preview: ${r.status}`)))))
      .then((data: LiveData) => {
        if (!on || !va.current || !vb.current || !box.current) return;
        made = new LiveMedia(data, [va.current, vb.current], box.current, timg.current);
        made.onstate = () => { const p = !made!.paused; setPlaying(p); onPlaying?.(p); };
        made.muted = mute;
        lm.current = made;
        setLiveData(data); setD(data.duration_s); setT(0); last.current = -1;
        made.ready.catch((e) => on && setLiveErr(String(e.message ?? e)));
      })
      .catch((e) => on && setLiveErr(String(e.message ?? e)));
    const ro = new ResizeObserver(() => lm.current?.layout());
    if (box.current) ro.observe(box.current);
    return () => { on = false; ro.disconnect(); made?.destroy(); if (lm.current === made) lm.current = null; };
  }, [live]); // eslint-disable-line
  useEffect(() => { if (lm.current) lm.current.muted = mute; }, [mute]);

  useEffect(() => { // a smooth playhead for the timeline, not only the 4 Hz timeupdate; also runs the reverse shuttle
    let raf = 0, prev = performance.now();
    const tick = (now: number) => {
      const el = media();
      const dt = (now - prev) / 1000;
      prev = now;
      if (el) {
        if (back.current && el.paused) {
          el.currentTime = Math.max(0, el.currentTime - dt * back.current);
          if (el.currentTime <= 0) { back.current = 0; setRate(1); }
        }
        const cur = el.currentTime;
        if (loop.current && cur >= loop.current[1]) el.currentTime = loop.current[0];
        if (Math.abs(cur - last.current) >= 0.03 || (el.paused && cur !== last.current)) {
          last.current = cur;
          setT(cur);
          onTime?.(cur);
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [onTime, live]);

  const skip = (dir: 1 | -1) => { // to the previous / next clip edge
    const el = media();
    if (!el) return;
    const edges = [0, ...marks, d].sort((a, b) => a - b);
    const to = dir > 0 ? edges.find((m) => m > el.currentTime + 0.05) : [...edges].reverse().find((m) => m < el.currentTime - 0.25);
    api.seek(to ?? (dir > 0 ? d : 0));
  };
  const nextRate = () => { // 1 -> 1.5 -> 2 -> 0.5 -> 1; a running shuttle drops back to 1
    const el = media();
    if (!el) return;
    stopBack();
    const r = rate < 0 || !RATES.includes(rate) ? 1 : RATES[(RATES.indexOf(rate) + 1) % RATES.length];
    el.playbackRate = r; setRate(r);
  };
  const flipMode = () => {
    const m = mode === "sec" ? "frames" : "sec";
    setMode(m);
    try { localStorage.setItem("montivo.viewer.tc", m); } catch { /* this tab only */ }
  };
  const loopBtn = () => {
    if (looping) return clearLoop();
    const el = media();
    if (el) el.loop = !whole;
    setWhole(!whole);
  };
  const scrubTo = (e: React.MouseEvent<HTMLDivElement>) => {
    const el = media();
    if (!el || !d) return;
    const r = e.currentTarget.getBoundingClientRect();
    el.currentTime = Math.max(0, Math.min(d, ((e.clientX - r.left) / r.width) * d));
    clearLoop();
  };

  return (
    <div className={`player ${compact ? "compact" : ""}`}>
      <div className="frame916" ref={frame} onClick={api.toggle}>
        {live ? (
          <div className="live-box" ref={box}>
            <video ref={va} playsInline muted /><video ref={vb} playsInline muted />
            <img ref={timg} className="live-title" alt="" />
            {!liveData && !liveErr && <span className="live-wait">Preparing the live preview…</span>}
            {liveErr && <span className="live-wait err">{liveErr}</span>}
          </div>
        ) : (
          <video ref={video} src={src} preload="auto" playsInline muted={mute}
            onLoadedMetadata={(e) => setD(e.currentTarget.duration)} onPlay={() => { setPlaying(true); onPlaying?.(true); }} onPause={() => { setPlaying(false); onPlaying?.(false); }} />
        )}
        {label && <span className="vlabel">{label}</span>}
        {overlay}
        {!playing && <span className="big-play"><Icon name="play" size={34} /></span>}
      </div>
      {scrub ? (
        <>
          <div className="transport small">
            <button className="tp-btn" onClick={api.toggle} aria-label={playing ? "pause" : "play"}><Icon name={playing ? "pause" : "play"} /></button>
            <span className="tp-time">{tc(t)} <span className="mute">/ {tc(d)}</span></span>
          </div>
          <div className="scrub" onClick={scrubTo}>
            <div className="scrub-track" />
            {d > 0 && marks.map((m, k) => <span key={k} className="mark-tick" style={{ left: `${(m / d) * 100}%` }} />)}
            {d > 0 && bands.map((b, k) => (
              <span key={k} className={`band ${b.sev}`} style={{ left: `${(b.a / d) * 100}%`, width: `${Math.max(0.6, ((b.b - b.a) / d) * 100)}%` }} />
            ))}
            <span className="scrub-fill" style={{ width: d ? `${(t / d) * 100}%` : 0 }} />
            <span className="scrub-head" style={{ left: d ? `${(t / d) * 100}%` : 0 }} />
          </div>
        </>
      ) : (
        <div className="transport">
          <button className="tp-tc" onClick={flipMode} title={mode === "sec" ? "Minutes, seconds and hundredths (click: frames)" : `Minutes, seconds and frames at ${FPS} fps (click: seconds)`}>
            <b>{timecode(t, mode)}</b><span>/ {timecode(d, mode)}</span>{mode === "frames" && <i>fr</i>}
          </button>
          <span className="tp-mid">
            <button className="tp-icon" onClick={() => skip(-1)} title="Previous cut (↑)"><Icon name="skipBack" /></button>
            <button className="tp-icon" onClick={() => api.step(-1 / FPS)} title="Back one frame (←)"><Icon name="frameBack" /></button>
            <button className="tp-play" onClick={api.toggle} title="Play / pause (Space)"><Icon name={playing || rate < 0 ? "pause" : "play"} /></button>
            <button className="tp-icon" onClick={() => api.step(1 / FPS)} title="Forward one frame (→)"><Icon name="frameFwd" /></button>
            <button className="tp-icon" onClick={() => skip(1)} title="Next cut (↓)"><Icon name="skipFwd" /></button>
          </span>
          <span className="tp-right">
            <button className={`tp-tool tp-rate ${rate !== 1 ? "on" : ""}`} onClick={nextRate} title="Playback speed (J · K · L shuttle)">{rate < 0 ? `◀ ${-rate}×` : `${rate}×`}</button>
            <button className={`tp-tool ${looping || whole ? "on" : ""}`} onClick={loopBtn} title={looping ? "Stop looping the clip" : whole ? "Stop looping the cut" : "Loop the cut"}><Icon name="repeat" /></button>
            <button className={`tp-tool ${mute ? "on" : ""}`} onClick={() => setMute(!mute)} title={mute ? "Unmute (⇧M)" : "Mute (⇧M)"}><Icon name={mute ? "mute" : "volume"} /></button>
            {safe && <button className={`tp-tool ${safe.on ? "on" : ""}`} onClick={safe.next} title={`Safe-area guides: ${safe.label} (G cycles Reels, TikTok, Shorts, off)`}><Icon name="safe" /></button>}
            <button className="tp-tool" onClick={api.fullscreen} title="Full screen (F)"><Icon name="fit" /></button>
          </span>
        </div>
      )}
    </div>
  );
});
