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
  // the live preview (Live.ts) in place of `src`: GET /preview's URL for a version, or a draft's data already fetched
  live?: string | LiveData;
  labelTone?: string;
  liveMix?: { level_db: number; voice_db: number } | null; // the timeline's faders while they are moved (live mode)
  liveSwap?: LiveData | null; // A4: the same cut with the Style panel's staged look, played in place while it is staged
  // where a remounted player picks up (the Live / Rendered switch of one version): read once, when the media is ready
  startAt?: { t: number; play: boolean } | null;
}>(function Player({ src, onTime, bands = [], marks = [], compact, muted, label, scrub, overlay, onPlaying, safe, live, labelTone, liveMix, liveSwap, startAt }, ref) {
  const video = useRef<HTMLVideoElement>(null);
  const lm = useRef<LiveMedia | null>(null);
  const media = (): Media | null => (live ? lm.current : video.current);
  const box = useRef<HTMLDivElement>(null);
  const liveSwapRef = useRef(liveSwap);
  liveSwapRef.current = liveSwap;
  const startRef = useRef(startAt ?? null); // taken by the first media that loads, then gone
  const onPlayingRef = useRef(onPlaying);
  onPlayingRef.current = onPlaying;
  const takeStart = (m: Media) => {
    const s = startRef.current;
    startRef.current = null;
    if (!s) return;
    m.currentTime = Math.max(0, Math.min(s.t, (m.duration || s.t)));
    setT(m.currentTime);
    if (s.play) m.play().catch(() => {});
  };
  const loaded = useRef<LiveData | null>(null); // the data the player loaded or a draft edit handed it
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
  // element full screen (desktops, iPad), else the iPhone's video-only full screen, which Live (two swapped videos) cannot use
  const canFullscreen = !!document.documentElement.requestFullscreen || (!live && "webkitEnterFullscreen" in HTMLVideoElement.prototype);

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
      // Live: a play still waiting for the audio decode counts as playing, so Space twice pauses (audit, 31/10)
      const starting = el instanceof LiveMedia && el.starting;
      if (el.paused && !starting) el.play().catch(() => {}); else el.pause();
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
    fullscreen() { // iPhone Safari has no element full screen: only a <video> can go full screen there (Rendered)
      const f = frame.current, el = video.current as (HTMLVideoElement & { webkitEnterFullscreen?: () => void }) | null;
      if (!f) return;
      if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
      else if (f.requestFullscreen) f.requestFullscreen().catch(() => {});
      else if (el?.webkitEnterFullscreen) el.webkitEnterFullscreen();
    },
  };
  useImperativeHandle(ref, () => api);

  useEffect(() => { // live mode: the segments, crop path and graphics, then the edit proxy on two <video>s
    if (!live) { lm.current?.destroy(); lm.current = null; return; }
    let on = true;
    setLiveErr(null);
    const got: Promise<LiveData> = typeof live === "string"
      ? fetch(live).then((r) => (r.ok ? r.json() : r.text().then((x) => Promise.reject(new Error(x || `preview: ${r.status}`)))))
      : Promise.resolve(live);
    got.then((data) => {
      if (!on || !box.current) return;
      const m = lm.current;
      // the same source as what plays (a version's first draft edit, a discard back to it, every later edit): swapped
      // in place, so the playhead stays and the edit audio is not fetched and decoded again (audit, 31/10)
      if (m && m.data.media.video === data.media.video && m.data.media.audio === data.media.audio) {
        loaded.current = data;
        if (liveSwapRef.current || m.data === data) return;
        m.setData(data);
        setLiveData(data); setD(data.duration_s); last.current = -1;
        return;
      }
      m?.destroy();
      setLiveData(null);
      const made = new LiveMedia(data, box.current);
      made.onstate = () => { const p = !made.paused; setPlaying(p); onPlaying?.(p); };
      made.muted = mute;
      lm.current = made;
      loaded.current = data;
      setLiveData(data); setD(data.duration_s); setT(0); last.current = -1;
      takeStart(made);
      made.ready.catch((e) => on && lm.current === made && setLiveErr(String(e.message ?? e)));
    }).catch((e) => on && setLiveErr(String(e.message ?? e)));
    return () => { on = false; };
  }, [live]); // eslint-disable-line
  // unmounted (the Live / Rendered switch, another version): a player that was playing says it stopped, else the editor
  // kept `playing` and hid the Style canvas, the head picker and the paused preview until the next play / pause
  useEffect(() => () => { lm.current?.destroy(); lm.current = null; onPlayingRef.current?.(false); }, []);
  useEffect(() => { // the live box follows the frame's size
    if (!live || !box.current) return;
    const ro = new ResizeObserver(() => lm.current?.layout());
    ro.observe(box.current);
    return () => ro.disconnect();
  }, [!!live]); // eslint-disable-line
  useEffect(() => { // A4: a staged look over what is loaded, and back to it when the look is applied or dropped
    const m = lm.current, want = liveSwap ?? loaded.current;
    if (!m || !want || m.data === want) return;
    m.setData(want);
    setLiveData(want); setD(want.duration_s); last.current = -1;
  }, [liveSwap]); // eslint-disable-line
  useEffect(() => { if (lm.current) lm.current.muted = mute; }, [mute]);
  useEffect(() => { lm.current?.setMix(liveMix ?? null); }, [liveMix?.level_db, liveMix?.voice_db, liveData]); // eslint-disable-line

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
          <>
            <div className="live-box" ref={box} />
            {!liveData && !liveErr && <span className="live-wait">Preparing the live preview…</span>}
            {liveErr && <span className="live-wait err">{liveErr}</span>}
          </>
        ) : (
          <video ref={video} src={src} preload="auto" playsInline muted={mute}
            onLoadedMetadata={(e) => { setD(e.currentTarget.duration); takeStart(e.currentTarget); }} onPlay={() => { setPlaying(true); onPlaying?.(true); }} onPause={() => { setPlaying(false); onPlaying?.(false); }} />
        )}
        {label && <span className={`vlabel ${labelTone ?? ""}`}>{label}</span>}
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
        <div className="transport" data-monti="viewer.transport">
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
            {safe && <button data-monti="viewer.safe" className={`tp-tool ${safe.on ? "on" : ""}`} onClick={safe.next} title={`Safe-area guides: ${safe.label} (G cycles Reels, TikTok, Shorts, off)`}><Icon name="safe" /></button>}
            {canFullscreen && <button className="tp-tool" onClick={api.fullscreen} title="Full screen (F)"><Icon name="fit" /></button>}
          </span>
        </div>
      )}
    </div>
  );
});
