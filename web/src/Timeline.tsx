import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { crit, tc } from "./api";
import { blockAt, edgeSnaps, punchLabel, sevRank, type Block, type Snap } from "./edit";
import { Icon } from "./icons";
import type { Issue, Version, Word } from "./types";

export type TrimAsk = { span: string; edge: "in" | "out"; word: number; words: number; dt: number; label: string };
export type ClipMenu = { x: number; y: number; span: string };
/** A clip dragged to a new place: in front of `before`, or to the end (null). */
export type MoveAsk = { span: string; before: string | null; label: string };

const TRACK_H = { text: 24, captions: 24, video: 58, audio: 42 };
const RULER_H = 30;
export const ZOOM_MIN = 6;
export const ZOOM_MAX = 240;
const THUMB = { w: 54, h: 96, step: 0.5 }; // the server's filmstrip sprite

type Props = {
  id: string; v: Version | null; blocks: Block[]; words: Record<string, Word[]>; t: number; playing: boolean;
  selected: string | null; zoom: number | null; setZoom: (z: number | null) => void;
  mode: "timeline" | "transcript"; setMode: (m: "timeline" | "transcript") => void; canTrim: boolean;
  onSelect: (key: string | null) => void; onSeek: (a: number, b?: number) => void; onScrub: (t: number) => void;
  onMenu: (m: ClipMenu) => void; onTrim: (t: TrimAsk) => void; onMove: (m: MoveAsk) => void;
};

/** The bottom of the editor: a read-mostly multi-track timeline of the selected version (select, seek, delete from the
 *  menu, trim by dragging a clip edge to a word, move a clip by dragging its body), or the transcript view of the
 *  same edit. */
export function Timeline(p: Props) {
  const { v, mode, setMode, zoom, setZoom } = p;
  const scroller = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, [mode, !!v]); // eslint-disable-line
  const dur = v ? Math.max(v.duration_s ?? v.runtime_s + v.title_s, 1) : 30;
  const fit = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, (width - 40) / dur));
  const pps = zoom ?? fit;
  const zoomTo = (z: number) => setZoom(Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, z)));

  return (
    <div className="timeline">
      <div className="tl-bar">
        <div className="seg-group">
          <button className={`seg ${mode === "timeline" ? "on" : ""}`} onClick={() => setMode("timeline")}>Timeline</button>
          <button className={`seg ${mode === "transcript" ? "on" : ""}`} onClick={() => setMode("transcript")}>Transcript</button>
        </div>
        {v && <span className="tl-info">Version {v.n} · {v.spans.length} clips · {tc(v.duration_s ?? v.runtime_s)}</span>}
        <span className="grow" />
        {mode === "timeline" && (
          <span className="zoom">
            <button className="icon-btn" onClick={() => zoomTo(pps / 1.4)} title="Zoom out (⌘−)"><Icon name="zoomOut" /></button>
            <input type="range" min={Math.log(ZOOM_MIN)} max={Math.log(ZOOM_MAX)} step={0.01} value={Math.log(pps)}
              onChange={(e) => zoomTo(Math.exp(Number(e.target.value)))} aria-label="Timeline zoom" />
            <button className="icon-btn" onClick={() => zoomTo(pps * 1.4)} title="Zoom in (⌘+)"><Icon name="zoomIn" /></button>
            <button className={`icon-btn ${zoom == null ? "on" : ""}`} onClick={() => setZoom(null)} title="Fit the whole cut"><Icon name="fit" /></button>
          </span>
        )}
      </div>
      {!v ? <div className="tl-empty mute">The timeline appears when the first version is rendered.</div>
        : mode === "transcript" ? <Transcript {...p} v={v} />
        : (
          <div className="tl-body">
            <div className="tl-heads">
              <div style={{ height: RULER_H }} />
              <div className="tl-head" style={{ height: TRACK_H.text }}>Text</div>
              <div className="tl-head" style={{ height: TRACK_H.captions }}>Captions</div>
              <div className="tl-head video" style={{ height: TRACK_H.video }}>Video</div>
              <div className="tl-head" style={{ height: TRACK_H.audio }}>Audio</div>
            </div>
            <div className="tl-scroll" ref={scroller}>
              <Canvas {...p} v={v} pps={pps} dur={dur} scroller={scroller} />
            </div>
          </div>
        )}
    </div>
  );
}

// ------------------------------------------------------------------- canvas

function Canvas(p: Props & { v: Version; pps: number; dur: number; scroller: React.RefObject<HTMLDivElement> }) {
  const { v, pps, dur, t, playing, scroller, onScrub } = p;
  const canvas = useRef<HTMLDivElement>(null);
  const width = Math.ceil(dur * pps) + 40;
  const timeAt = useCallback((clientX: number) => {
    const r = canvas.current!.getBoundingClientRect();
    return Math.max(0, Math.min(dur, (clientX - r.left) / pps));
  }, [dur, pps]);
  const scrub = (e: React.PointerEvent) => { // drag on the ruler or the playhead
    if (e.button !== 0) return;
    e.preventDefault();
    onScrub(timeAt(e.clientX));
    const move = (ev: PointerEvent) => onScrub(timeAt(ev.clientX));
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  useEffect(() => { // follow the playhead while playing
    const el = scroller.current;
    if (!el || !playing) return;
    const x = t * pps;
    if (x < el.scrollLeft || x > el.scrollLeft + el.clientWidth - 30) el.scrollLeft = Math.max(0, x - el.clientWidth * 0.2);
  }, [t, pps, playing, scroller]);
  const prevPps = useRef(pps);
  useLayoutEffect(() => { // zoom around the playhead
    const el = scroller.current;
    if (el && prevPps.current !== pps) {
      const screenX = t * prevPps.current - el.scrollLeft;
      el.scrollLeft = Math.max(0, t * pps - screenX);
    }
    prevPps.current = pps;
  }, [pps]); // eslint-disable-line

  return (
    <div className="tl-canvas" ref={canvas} style={{ width }}>
      <Ruler pps={pps} dur={dur} issues={v.review?.issues ?? []} onScrub={scrub} onSeek={p.onSeek} />
      <Tracks id={p.id} v={v} blocks={p.blocks} words={p.words} selected={p.selected} canTrim={p.canTrim} pps={pps} dur={dur}
        timeAt={timeAt} onSelect={p.onSelect} onSeek={p.onSeek} onMenu={p.onMenu} onTrim={p.onTrim} onMove={p.onMove} />
      <div className="playhead" style={{ transform: `translateX(${t * pps}px)` }}>
        <span className="ph-handle" onPointerDown={scrub} />
        <span className="ph-line" />
      </div>
    </div>
  );
}

function tickStep(pps: number): [number, number] {
  for (const s of [0.5, 1, 2, 5, 10, 15, 30, 60]) if (s * pps >= 64) return [s, s >= 5 ? s / 5 : s / 2];
  return [120, 30];
}

const Ruler = memo(function Ruler({ pps, dur, issues, onScrub, onSeek }: {
  pps: number; dur: number; issues: Issue[]; onScrub: (e: React.PointerEvent) => void; onSeek: (a: number, b?: number) => void;
}) {
  const [major, minor] = tickStep(pps);
  const ticks: { x: number; major: boolean; t: number }[] = [];
  for (let k = 0; k * minor <= dur + 1e-6; k++) {
    const s = k * minor;
    ticks.push({ x: s * pps, major: Math.abs(s / major - Math.round(s / major)) < 1e-6, t: s });
  }
  return (
    <div className="ruler" style={{ height: RULER_H }} onPointerDown={onScrub}>
      {ticks.map((k) => (
        <span key={k.t} className={`tick ${k.major ? "major" : ""}`} style={{ left: k.x }}>
          {k.major && <span className="tick-l">{tc(k.t, major < 1 ? 1 : 0).replace(/\.0$/, "")}</span>}
        </span>
      ))}
      {[...issues].sort((a, c) => sevRank(c.severity) - sevRank(a.severity)).map((i) => { // the worst drawn last, on top
        const b = Math.max(i.t_end_s, i.t_s + 0.3);
        return (
          <button key={i.k} className={`marker ${i.severity}`} style={{ left: i.t_s * pps, width: Math.max(8, (b - i.t_s) * pps) }}
            title={`${tc(i.t_s)} ${i.severity} · ${crit(i.criterion)}: ${i.what}`}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => { e.stopPropagation(); onSeek(Math.max(0, i.t_s - 1), i.t_end_s + 0.5); }}>
            <span className="m-flag" />
          </button>
        );
      })}
    </div>
  );
});

// ------------------------------------------------------------------- tracks

function useSprite(url: string | null): number | null {
  const [n, setN] = useState<number | null>(null);
  useEffect(() => {
    setN(null);
    if (!url) return;
    const img = new Image();
    img.onload = () => setN(Math.max(1, Math.round(img.naturalWidth / THUMB.w)));
    img.src = url;
  }, [url]);
  return n;
}

function usePeaks(url: string | null): { rate: number; peaks: number[] } | null {
  const [d, setD] = useState<{ rate: number; peaks: number[] } | null>(null);
  useEffect(() => {
    setD(null);
    if (!url) return;
    let live = true;
    fetch(url).then((r) => (r.ok ? r.json() : null)).then((x) => { if (live) setD(x); }).catch(() => {});
    return () => { live = false; };
  }, [url]);
  return d;
}

type Drag = { key: string; edge: "in" | "out"; snaps: Snap[]; snap: Snap | null; current: number };
type Move = { key: string; slot: number; t: number }; // a clip being dragged: its insertion slot among the others
const MOVE_PX = 6; // a press that travels further than this is a drag, not a click

type TrackProps = Pick<Props, "id" | "blocks" | "words" | "selected" | "canTrim" | "onSelect" | "onSeek" | "onMenu" | "onTrim" | "onMove"> & {
  v: Version; pps: number; dur: number; timeAt: (x: number) => number;
};

const Tracks = memo(function Tracks(p: TrackProps) {
  const { id, v, blocks, words, pps, selected, canTrim, onSelect, onSeek, onMenu, onTrim, onMove, timeAt } = p;
  const q = v.bytes ? `&b=${v.bytes}` : "";
  const sprite = v.cut ? `/api/runs/${id}/filmstrip?v=${v.n}${q}` : null;
  const frames = useSprite(sprite);
  const peaks = usePeaks(v.cut ? `/api/runs/${id}/peaks?v=${v.n}${q}` : null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [move, setMove] = useState<Move | null>(null);
  const live = blocks.filter((b) => b.kind !== "ghost");
  const tileW = (TRACK_H.video - 6) * (9 / 16);

  const startTrim = (e: React.PointerEvent, b: Block, edge: "in" | "out") => {
    e.stopPropagation();
    e.preventDefault();
    const snaps = edgeSnaps(b, edge, v, words);
    const cur = edge === "out" ? b.span!.word_to : b.span!.word_from;
    let d: Drag = { key: b.key, edge, snaps, snap: null, current: cur };
    setDrag(d);
    onSelect(b.key);
    const move = (ev: PointerEvent) => {
      const x = timeAt(ev.clientX);
      let best: Snap | null = null;
      for (const s of snaps) if (!best || Math.abs(s.t - x) < Math.abs(best.t - x)) best = s;
      d = { ...d, snap: best };
      setDrag(d);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      setDrag(null);
      const s = d.snap;
      if (!s || s.word === cur) return;
      const tw = words[String(b.span!.turn_id)] ?? [];
      const n = edge === "out" ? s.word - cur : cur - s.word; // > 0 = more words
      const dt = edge === "out" ? s.t - b.t1 : b.t0 - s.t;
      const keepEdge = edge === "out" ? `ends on “${tw[s.word]?.w ?? ""}”` : `starts on “${tw[s.word]?.w ?? ""}”`;
      onTrim({ span: b.span!.id, edge, word: s.word, words: n, dt, label: keepEdge });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const startMove = (e: React.PointerEvent, b: Block) => { // press on a clip body: a click selects, a drag moves it
    const spans = live.filter((x) => x.kind === "span");
    const others = spans.filter((x) => x.key !== b.key);
    const from = spans.findIndex((x) => x.key === b.key);
    const x0 = e.clientX;
    let m: Move | null = null;
    const slotAt = (cx: number) => {
      const at = timeAt(cx);
      const slot = others.filter((o) => (o.t0 + o.t1) / 2 < at).length;
      return { slot, t: slot < others.length ? others[slot].t0 : others.at(-1)?.t1 ?? 0 };
    };
    const mv = (ev: PointerEvent) => {
      if (!m && Math.abs(ev.clientX - x0) < MOVE_PX) return;
      m = { key: b.key, ...slotAt(ev.clientX) };
      setMove(m);
    };
    const up = () => {
      window.removeEventListener("pointermove", mv);
      window.removeEventListener("pointerup", up);
      setMove(null);
      if (!m || m.slot === from) return;
      const before = others[m.slot]?.span!.id ?? null;
      onMove({ span: b.span!.id, before, label: before ? `before ${before}` : "to the end" });
    };
    window.addEventListener("pointermove", mv);
    window.addEventListener("pointerup", up);
  };

  return (
    <div className="tracks" onPointerDown={(e) => { if (e.target === e.currentTarget) { onSelect(null); onSeek(timeAt(e.clientX)); } }}>
      {/* text: title card + callouts */}
      <div className="track" style={{ height: TRACK_H.text }} onPointerDown={(e) => { if (e.target === e.currentTarget) { onSelect(null); onSeek(timeAt(e.clientX)); } }}>
        {v.title_s > 0 && (
          <button className={`item text title ${selected === "title" ? "sel" : ""}`} style={{ left: 0, width: v.title_s * pps - 1 }}
            onClick={() => { onSelect("title"); onSeek(0); }} title={`Title card: ${v.title_card_text}`}>
            <span>T&nbsp;&nbsp;{v.title_card_text}</span>
          </button>
        )}
        {v.callouts.map((c, k) => (
          <button key={k} className="item text callout" style={{ left: c.t_in * pps, width: Math.max(4, (c.t_out - c.t_in) * pps - 1) }}
            onClick={() => onSeek(c.t_in)} title={`Callout “${c.text}” · clip ${c.span}`}>
            <span>{c.text}</span>
          </button>
        ))}
      </div>
      {/* captions */}
      <div className="track" style={{ height: TRACK_H.captions }} onPointerDown={(e) => { if (e.target === e.currentTarget) { onSelect(null); onSeek(timeAt(e.clientX)); } }}>
        {v.caption_lines.map((c) => (
          <button key={c.id} className="item caption" style={{ left: c.t_in * pps, width: Math.max(3, (c.t_out - c.t_in) * pps - 1) }}
            onClick={() => onSeek(c.t_in + 0.01)} title={`${tc(c.t_in)} “${c.text}”`}>
            <span>{c.text}</span>
          </button>
        ))}
      </div>
      {/* video: one clip per span, filmstrip */}
      <div className="track video" style={{ height: TRACK_H.video }} onPointerDown={(e) => { if (e.target === e.currentTarget) { onSelect(null); onSeek(timeAt(e.clientX)); } }}>
        {live.map((b) => {
          const w = Math.max(2, (b.t1 - b.t0) * pps - 2);
          const n = Math.ceil(w / tileW);
          const sel = selected === b.key;
          const isDrag = drag?.key === b.key;
          return (
            <div key={b.key} className={`clip ${b.kind} ${b.span?.role ?? ""} ${sel ? "sel" : ""}`} style={{ left: b.t0 * pps + 1, width: w }}
              onPointerDown={(e) => {
                if (e.button !== 0) return;
                onSelect(b.key); onSeek(b.kind === "title" ? 0 : b.t0 + 0.02);
                if (b.kind === "span" && canTrim) startMove(e, b);
              }}
              onContextMenu={(e) => { e.preventDefault(); if (b.kind === "span") { onSelect(b.key); onMenu({ x: e.clientX, y: e.clientY, span: b.span!.id }); } }}
              title={b.kind === "span" ? `${b.span!.id} · ${b.span!.role} · ${(b.t1 - b.t0).toFixed(1)} s\n${b.span!.words}` : "Title card"}>
              <div className="film">
                {sprite && frames != null && Array.from({ length: n }, (_, k) => {
                  const at = b.t0 + ((k + 0.5) * tileW) / pps;
                  const idx = Math.min(frames - 1, Math.max(0, Math.floor(at / THUMB.step)));
                  return <span key={k} className="tile" style={{ left: k * tileW, width: tileW, backgroundImage: `url(${sprite})`,
                    backgroundSize: `${frames * tileW}px 100%`, backgroundPosition: `-${idx * tileW}px 0` }} />;
                })}
              </div>
              <span className="clip-label">
                <b>{b.kind === "title" ? "Title" : b.span!.id}</b>
                {b.kind === "span" && w > 70 && <span className="cl-words">{b.span!.words}</span>}
              </span>
              {b.kind === "span" && canTrim && (sel || isDrag) && <>
                <span className="trim-h in" onPointerDown={(e) => startTrim(e, b, "in")} title="Drag to trim or extend the start (snaps to words)" />
                <span className="trim-h out" onPointerDown={(e) => startTrim(e, b, "out")} title="Drag to trim or extend the end (snaps to words)" />
              </>}
            </div>
          );
        })}
        {v.punch_ins.map((pi) => { // the zooms this render made: select one to see it or remove it
          const key = `punch:${pi.key}`;
          return (
            <button key={pi.key} className={`punch ${pi.kind} ${selected === key ? "sel" : ""}`}
              style={{ left: pi.t_in * pps, width: Math.max(12, (pi.t_out - pi.t_in) * pps - 1) }}
              onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => { e.stopPropagation(); onSelect(key); onSeek(Math.max(0, pi.t_in - 0.6), pi.t_out + 0.4); }}
              title={`Punch-in ${pi.zoom}× · ${punchLabel(pi)} · ${tc(pi.t_in)}`}>
              <span>{pi.zoom}×{pi.kind === "emphasis" && pi.word ? ` ${pi.word}` : ""}</span>
            </button>
          );
        })}
        {blocks.filter((b) => b.kind === "ghost").map((b) => (
          <span key={b.key} className="ghost-mark" style={{ left: b.t0 * pps }} title={`${b.span!.id} was deleted from Version ${v.origin.from}: “${b.span!.words}”`} />
        ))}
        {move && (() => {
          const b = live.find((x) => x.key === move.key)!;
          const spans = live.filter((x) => x.kind === "span");
          const same = move.slot === spans.findIndex((x) => x.key === move.key);
          return <>
            <span className="move-ghost" style={{ left: b.t0 * pps + 1, width: Math.max(2, (b.t1 - b.t0) * pps - 2) }} />
            <span className={`move-mark ${same ? "same" : ""}`} style={{ left: move.t * pps - 1 }} />
            <span className="trim-tip" style={{ left: move.t * pps }}>
              {same ? "drop elsewhere to move it" : `move ${b.span!.id} ${move.slot < spans.length - 1 ? `before ${spans.filter((x) => x.key !== move.key)[move.slot].span!.id}` : "to the end"}`}
            </span>
          </>;
        })()}
        {drag?.snap && (() => {
          const b = live.find((x) => x.key === drag.key)!;
          const edgeT = drag.edge === "out" ? b.t1 : b.t0;
          const a = Math.min(edgeT, drag.snap.t), z = Math.max(edgeT, drag.snap.t);
          const grow = drag.edge === "out" ? drag.snap.t > edgeT : drag.snap.t < edgeT;
          const n = Math.abs(drag.snap.word - drag.current);
          return <>
            <span className={`trim-zone ${grow ? "grow" : "cut"}`} style={{ left: a * pps, width: Math.max(1, (z - a) * pps) }} />
            <span className="trim-tip" style={{ left: drag.snap.t * pps }}>
              {n === 0 ? "no change" : `${grow ? "+" : "−"}${n} word${n === 1 ? "" : "s"} · ${drag.edge === "out" ? "ends on" : "starts on"} “${drag.snap.label}”`}
            </span>
          </>;
        })()}
      </div>
      {/* audio: waveform of the rendered cut */}
      <div className="track audio" style={{ height: TRACK_H.audio }} onPointerDown={(e) => { onSelect(null); onSeek(timeAt(e.clientX)); }}>
        <Wave peaks={peaks} pps={pps} height={TRACK_H.audio} width={Math.ceil(p.dur * pps)} />
      </div>
    </div>
  );
});

function Wave({ peaks, pps, height, width }: { peaks: { rate: number; peaks: number[] } | null; pps: number; height: number; width: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = ref.current;
    if (!c || !peaks) return;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.min(width, 30000);
    c.width = w * dpr;
    c.height = height * dpr;
    c.style.width = `${w}px`;
    const g = c.getContext("2d")!;
    g.scale(dpr, dpr);
    g.clearRect(0, 0, w, height);
    const css = getComputedStyle(c);
    g.fillStyle = css.getPropertyValue("--wave").trim() || "#4f8a6b";
    const top = Math.max(...peaks.peaks, 0.01);
    const mid = height / 2;
    for (let x = 0; x < w; x++) {
      const a = Math.floor((x / pps) * peaks.rate), b = Math.max(a + 1, Math.floor(((x + 1) / pps) * peaks.rate));
      let m = 0;
      for (let i = a; i < b && i < peaks.peaks.length; i++) if (peaks.peaks[i] > m) m = peaks.peaks[i];
      const h = Math.max(0.5, (Math.sqrt(m / top) * (height - 6)) / 2);
      g.fillRect(x, mid - h, 1, h * 2);
    }
  }, [peaks, pps, height, width]);
  return peaks ? <canvas ref={ref} className="wave" /> : <span className="wave-wait mute">waveform…</span>;
}

// ------------------------------------------------------------------- transcript (the paper edit)

function Transcript({ v, blocks, t, selected, onSelect, onSeek, onMenu }: Props & { v: Version }) {
  const scroller = useRef<HTMLDivElement>(null);
  const issues = v.review?.issues ?? [];
  const playing = useMemo(() => blockAt(blocks, t), [blocks, t]);
  useEffect(() => {
    const el = scroller.current?.querySelector(".block.playing") as HTMLElement | null;
    el?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
  }, [playing?.key]);
  const lastLive = blocks.filter((x) => x.kind !== "ghost").at(-1);
  return (
    <div className="strip">
      <div className="strip-note mute">Struck words were cut from Version {v.origin.from ?? v.n - 1} · flags are the reviewer's notes at cut time</div>
      <div className="blocks" ref={scroller}>
        {blocks.map((b) => {
          const len = Math.max(b.t1 - b.t0, 0.001);
          const pins = b.kind === "ghost" ? [] : issues.filter((i) => i.t_s >= b.t0 && (i.t_s < b.t1 || (b === lastLive && i.t_s >= b.t1)));
          const grow = b.kind === "ghost" ? 0 : Math.max(len, 1.2);
          const isSel = b.key === selected && b.kind !== "ghost";
          return (
            <div key={b.key} className={`block ${b.kind} ${b.span?.role ?? ""} ${isSel ? "sel" : ""} ${playing === b ? "playing" : ""}`}
              style={{ flexGrow: grow, flexBasis: b.kind === "ghost" ? 140 : `${grow * 34}px` }}
              onClick={() => { if (b.kind === "span") { onSelect(b.key); onSeek(b.t0 + 0.02); } else if (b.kind === "title") { onSelect("title"); onSeek(0); } }}
              onContextMenu={(e) => { if (b.kind === "span") { e.preventDefault(); onSelect(b.key); onMenu({ x: e.clientX, y: e.clientY, span: b.span!.id }); } }}>
              <div className="pins">
                {pins.map((i) => (
                  <button key={i.k} className={`pin ${i.severity}`} style={{ left: `${Math.min(100, ((i.t_s - b.t0) / len) * 100)}%` }}
                    title={`${tc(i.t_s)} ${i.severity} ${crit(i.criterion)}: ${i.what}`}
                    onClick={(e) => { e.stopPropagation(); onSeek(Math.max(0, i.t_s - 1), i.t_end_s + 0.5); }} />
                ))}
                {playing === b && <span className="head" style={{ left: `${Math.min(100, ((t - b.t0) / len) * 100)}%` }} />}
              </div>
              <div className="b-top">
                <span className="b-id">{b.kind === "title" ? "Title" : b.label}</span>
                {b.span && b.kind === "span" && <span className="b-role">{b.span.role}</span>}
                {b.kind === "ghost" && <span className="b-role">deleted</span>}
                <span className="b-t">{b.kind === "ghost" ? "" : tc(b.t0)}</span>
              </div>
              <div className="b-words">
                {b.kind === "title" ? <span className="w title-w">{b.label}</span> : <>
                  {b.before.map((w) => <s key={`b${w.i}`} className="w cut">{w.w + " "}</s>)}
                  {b.kind === "ghost" ? b.kept.map((w) => <s key={w.i} className="w cut">{w.w + " "}</s>) : b.kept.map((w) => <span key={w.i} className="w">{w.w + " "}</span>)}
                  {b.after.map((w) => <s key={`a${w.i}`} className="w cut">{w.w + " "}</s>)}
                </>}
              </div>
              {b.span?.callout && b.kind === "span" && <div className="b-callout">callout · {b.span.callout}</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
