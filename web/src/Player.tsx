import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { tc } from "./api";

export type PlayerHandle = { seek: (a: number, b?: number) => void; play: () => void; pause: () => void; el: () => HTMLVideoElement | null };
type Band = { a: number; b: number; sev: string };

/** The 9:16 player: custom transport with the review's issue windows as bands on the scrub bar and the cuts as
 *  ticks. seek(a, b) loops a..b until the viewer seeks or presses play elsewhere. */
export const Player = forwardRef<PlayerHandle, {
  src: string; onTime?: (t: number) => void; bands?: Band[]; marks?: number[]; compact?: boolean; muted?: boolean; label?: string;
}>(function Player({ src, onTime, bands = [], marks = [], compact, muted, label }, ref) {
  const video = useRef<HTMLVideoElement>(null);
  const loop = useRef<[number, number] | null>(null);
  const last = useRef(-1);
  const [t, setT] = useState(0);
  const [d, setD] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [looping, setLooping] = useState(false);

  useImperativeHandle(ref, () => ({
    seek(a, b) {
      const el = video.current;
      if (!el) return;
      el.currentTime = a;
      loop.current = b != null ? [a, b] : null;
      setLooping(b != null);
      if (b != null) el.play().catch(() => {});
    },
    play: () => { video.current?.play().catch(() => {}); },
    pause: () => video.current?.pause(),
    el: () => video.current,
  }));

  useEffect(() => { // a smooth playhead for the strip, not only the 4 Hz timeupdate
    let raf = 0;
    const tick = () => {
      const el = video.current;
      if (el) {
        const now = el.currentTime;
        if (loop.current && now >= loop.current[1]) el.currentTime = loop.current[0];
        if (Math.abs(now - last.current) >= 0.05) { // ~20 Hz is smooth enough and keeps the room from re-rendering per frame
          last.current = now;
          setT(now);
          onTime?.(now);
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [onTime]);

  const toggle = () => {
    const el = video.current;
    if (!el) return;
    if (el.paused) el.play().catch(() => {}); else el.pause();
  };
  const scrub = (e: React.MouseEvent<HTMLDivElement>) => {
    const el = video.current;
    if (!el || !d) return;
    const r = e.currentTarget.getBoundingClientRect();
    el.currentTime = Math.max(0, Math.min(d, ((e.clientX - r.left) / r.width) * d));
    loop.current = null;
    setLooping(false);
  };

  return (
    <div className={`player ${compact ? "compact" : ""}`}>
      <div className="frame916" onClick={toggle}>
        <video ref={video} src={src} preload="auto" playsInline muted={muted}
          onLoadedMetadata={(e) => setD(e.currentTarget.duration)} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} />
        {label && <span className="vlabel">{label}</span>}
        {!playing && <span className="big-play">▶</span>}
      </div>
      <div className="transport">
        <button className="tp-btn" onClick={toggle} aria-label={playing ? "pause" : "play"}>{playing ? "❚❚" : "▶"}</button>
        <span className="tp-time">{tc(t)} <span className="mute">/ {tc(d)}</span></span>
        {looping && <button className="tp-loop" onClick={() => { loop.current = null; setLooping(false); }}>loop ✕</button>}
      </div>
      <div className="scrub" onClick={scrub}>
        <div className="scrub-track" />
        {d > 0 && marks.map((m, k) => <span key={k} className="mark-tick" style={{ left: `${(m / d) * 100}%` }} />)}
        {d > 0 && bands.map((b, k) => (
          <span key={k} className={`band ${b.sev}`} style={{ left: `${(b.a / d) * 100}%`, width: `${Math.max(0.6, ((b.b - b.a) / d) * 100)}%` }} />
        ))}
        <span className="scrub-fill" style={{ width: d ? `${(t / d) * 100}%` : 0 }} />
        <span className="scrub-head" style={{ left: d ? `${(t / d) * 100}%` : 0 }} />
      </div>
    </div>
  );
});
