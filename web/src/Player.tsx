import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { tc } from "./api";
import { Icon } from "./icons";

export const FPS = 30;

export type PlayerHandle = {
  seek: (a: number, b?: number) => void; play: () => void; pause: () => void; toggle: () => void;
  step: (dt: number) => void; shuttle: (dir: -1 | 0 | 1) => void; el: () => HTMLVideoElement | null;
};
type Band = { a: number; b: number; sev: string };

/** The 9:16 viewer with its own transport. seek(a, b) loops a..b until the viewer seeks or plays elsewhere; shuttle()
 *  is J/K/L: each press of L or J doubles the speed forward or backward, K stops. `scrub` adds the old scrub bar with
 *  the review's issue windows (Compare); the editor's timeline does that job there. */
export const Player = forwardRef<PlayerHandle, {
  src: string; onTime?: (t: number) => void; bands?: Band[]; marks?: number[]; compact?: boolean; muted?: boolean;
  label?: string; scrub?: boolean; overlay?: React.ReactNode; onPlaying?: (playing: boolean) => void;
}>(function Player({ src, onTime, bands = [], marks = [], compact, muted, label, scrub, overlay, onPlaying }, ref) {
  const video = useRef<HTMLVideoElement>(null);
  const loop = useRef<[number, number] | null>(null);
  const back = useRef(0); // reverse shuttle speed, 0 = off
  const last = useRef(-1);
  const [t, setT] = useState(0);
  const [d, setD] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [looping, setLooping] = useState(false);
  const [rate, setRate] = useState(1); // signed: -4..4, shown when not 1

  const clearLoop = () => { loop.current = null; setLooping(false); };
  const stopBack = () => { back.current = 0; };
  const api: PlayerHandle = {
    seek(a, b) {
      const el = video.current;
      if (!el) return;
      stopBack();
      el.currentTime = Math.max(0, a);
      loop.current = b != null ? [a, b] : null;
      setLooping(b != null);
      if (b != null) el.play().catch(() => {});
    },
    play: () => { stopBack(); video.current?.play().catch(() => {}); },
    pause: () => { stopBack(); setRate(1); if (video.current) { video.current.pause(); video.current.playbackRate = 1; } },
    toggle() {
      const el = video.current;
      if (!el) return;
      if (back.current) { stopBack(); setRate(1); return; }
      el.playbackRate = 1; setRate(1);
      if (el.paused) el.play().catch(() => {}); else el.pause();
    },
    step(dt) {
      const el = video.current;
      if (!el) return;
      stopBack(); el.pause(); clearLoop();
      el.currentTime = Math.max(0, Math.min(el.duration || 0, el.currentTime + dt));
    },
    shuttle(dir) {
      const el = video.current;
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
  };
  useImperativeHandle(ref, () => api);

  useEffect(() => { // a smooth playhead for the timeline, not only the 4 Hz timeupdate; also runs the reverse shuttle
    let raf = 0, prev = performance.now();
    const tick = (now: number) => {
      const el = video.current;
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
  }, [onTime]);

  const skip = (dir: 1 | -1) => { // to the previous / next clip edge
    const el = video.current;
    if (!el) return;
    const edges = [0, ...marks, d].sort((a, b) => a - b);
    const to = dir > 0 ? edges.find((m) => m > el.currentTime + 0.05) : [...edges].reverse().find((m) => m < el.currentTime - 0.25);
    api.seek(to ?? (dir > 0 ? d : 0));
  };
  const scrubTo = (e: React.MouseEvent<HTMLDivElement>) => {
    const el = video.current;
    if (!el || !d) return;
    const r = e.currentTarget.getBoundingClientRect();
    el.currentTime = Math.max(0, Math.min(d, ((e.clientX - r.left) / r.width) * d));
    clearLoop();
  };

  return (
    <div className={`player ${compact ? "compact" : ""}`}>
      <div className="frame916" onClick={api.toggle}>
        <video ref={video} src={src} preload="auto" playsInline muted={muted}
          onLoadedMetadata={(e) => setD(e.currentTarget.duration)} onPlay={() => { setPlaying(true); onPlaying?.(true); }} onPause={() => { setPlaying(false); onPlaying?.(false); }} />
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
          <span className="tp-tc"><b>{tc(t, 2)}</b> <span className="mute">/ {tc(d, 2)}</span></span>
          <span className="tp-mid">
            <button className="tp-icon" onClick={() => skip(-1)} title="Previous clip"><Icon name="skipBack" /></button>
            <button className="tp-icon" onClick={() => api.step(-1 / FPS)} title="Back one frame (←)"><Icon name="frameBack" /></button>
            <button className="tp-play" onClick={api.toggle} title="Play / pause (Space)"><Icon name={playing || rate < 0 ? "pause" : "play"} size={18} /></button>
            <button className="tp-icon" onClick={() => api.step(1 / FPS)} title="Forward one frame (→)"><Icon name="frameFwd" /></button>
            <button className="tp-icon" onClick={() => skip(1)} title="Next clip"><Icon name="skipFwd" /></button>
          </span>
          <span className="tp-right">
            {rate !== 1 && <span className="tp-rate">{rate > 0 ? `${rate}×` : `◀ ${-rate}×`}</span>}
            {looping && <button className="tp-loop" onClick={clearLoop} title="Stop looping">Loop ✕</button>}
          </span>
        </div>
      )}
    </div>
  );
});
