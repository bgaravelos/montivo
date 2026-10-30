import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api, crit, fileUrl, tc } from "./api";
import { blockAt, edgeSnaps, flatWords, ghostRestore, punchLabel, selRange, sevRank, spanText, unusedRuns, type Block, type Snap, type WordSel } from "./edit";
import { Icon } from "./icons";
import { LEVEL_RANGE, VOICE_RANGE, type Mix } from "./Mix";
import type { Issue, Overlay, PausePlan, Version, Word } from "./types";

export type TrimAsk = { span: string; edge: "in" | "out"; word: number; words: number; dt: number; label: string };
export type ClipMenu = { x: number; y: number; span: string };
/** A clip dragged to a new place: in front of `before`, or to the end (null). */
export type MoveAsk = { span: string; before: string | null; label: string };

const TRACK_H = { overlays: 30, text: 24, captions: 24, video: 58, audio: 40, music: 40 };
const PLACEMENTS: { k: Overlay["placement"]; label: string; note: string }[] = [
  { k: "full", label: "Full frame", note: "covers the frame, the voice goes on (a cutaway)" },
  { k: "upper", label: "Card", note: "a card above the speaker's head" },
  { k: "pip", label: "Corner", note: "a small card in the corner away from the head" },
];
const RULER_H = 30;
export const ZOOM_MIN = 6;
export const ZOOM_MAX = 240;
const THUMB = { w: 54, h: 96, step: 0.5 }; // the server's filmstrip sprite
const SNAP_PX = 8; // a dragged edge or clip within this many pixels of the playhead, a clip edge or an issue lands on it

type Props = {
  id: string; v: Version | null; blocks: Block[]; words: Record<string, Word[]>; t: number; playing: boolean;
  selected: string | null; selection: string[]; zoom: number | null; setZoom: (z: number | null) => void;
  /** The timeline's pixels per second right now (the fit when zoom is null), for ⌘+ / ⌘− in the editor. */
  ppsRef: React.MutableRefObject<number>; snap: boolean; setSnap: (on: boolean) => void;
  mode: "timeline" | "transcript"; setMode: (m: "timeline" | "transcript") => void; canTrim: boolean;
  onSelect: (key: string | null, how?: "toggle" | "range") => void; onSeek: (a: number, b?: number) => void; onScrub: (t: number) => void;
  onMenu: (m: ClipMenu) => void; onTrim: (t: TrimAsk) => void; onMove: (m: MoveAsk) => void;
  onDelete: (span: string) => void; onDeleteClips: (spans: string[]) => void; onAsk: (span: string) => void;
  onSplit: () => void;
  wordSel: WordSel | null; onWordSel: (s: WordSel | null) => void; onCutWords: () => void;
  /** The faders: the staged mix, the version's own, the live player's state, and what to do with them. */
  mix: Mix | null; base: Mix | null; live: { ready: boolean; active: boolean; failed: string | null };
  next: number; canEdit: string | null; onMix: (m: Mix) => void; onResetMix: () => void; onApplyMix: () => void;
  onMusicPanel: () => void;
  /** A Batch D edit (insert, pauses, overlay): the route, its body (the editor adds `from`), a note for the toast. */
  onEdit: (path: string, body: Record<string, unknown>, note: string) => void;
};

/** The bottom of the editor: a read-mostly multi-track timeline of the selected version (select, seek, delete from the
 *  menu, trim by dragging a clip edge to a word, move a clip by dragging its body), or the transcript view of the
 *  same edit (select words by clicking or dragging over them, Shift-click extends, ⌫ deletes them). */
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
  // fit leaves a pixel spare: clientWidth is rounded, so a canvas of exactly that width could overflow by a fraction, show
  // a horizontal scrollbar, push a vertical one into .tl-body, narrow this and refit, 30 times a second
  const fit = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, (width - 42) / dur));
  const pps = zoom ?? fit;
  p.ppsRef.current = pps;
  const zoomTo = (z: number) => setZoom(Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, z)));
  const [bar, setBar] = useState<Bar | null>(null); // a selected still, a ghost clip or the new-still prompt
  useEffect(() => setBar(null), [v?.n]);
  const plan = usePausePlan(p.id, v);

  return (
    <div className="timeline">
      <div className="tl-bar">
        <div className="seg-group">
          <button className={`seg ${mode === "timeline" ? "on" : ""}`} onClick={() => setMode("timeline")}><Icon name="film" size={13} />Timeline</button>
          <button className={`seg ${mode === "transcript" ? "on" : ""}`} onClick={() => setMode("transcript")}><Icon name="transcript" size={13} />Transcript</button>
        </div>
        {v && mode === "timeline" && (() => {
          const b = p.blocks.find((x) => x.key === p.selected && x.kind === "span");
          const many = p.selection.filter((k) => v.spans.some((s) => s.id === k));
          const pick = many.length > 1 ? `${many.length} clips` : b ? `clip ${b.span!.id}` : "Select a clip first";
          const locked = !p.canTrim ? "Not while an edit runs or in replay" : null;
          const del = !b && many.length < 2 ? pick : locked ?? (many.length >= v.spans.length || v.spans.length < 2 ? "That is every clip" : null);
          return (
            <span className="tl-tools">
              <button className="icon-btn" disabled={!!locked} onClick={p.onSplit} title={locked ?? "Split the clip at the playhead (S · ⌘K)"}><Icon name="scissors" /></button>
              <button className="icon-btn" disabled={!b} onClick={() => b && p.onSeek(b.t0, b.t1)} title={b ? `Play ${pick} on a loop` : pick}><Icon name="repeat" /></button>
              <button className="icon-btn" disabled={!b} onClick={() => b && p.onAsk(b.span!.id)} title={b ? `Ask Montivo about ${pick}` : pick}><Icon name="spark" /></button>
              <button className="icon-btn" disabled={!!del} onClick={() => (many.length > 1 ? p.onDeleteClips(many) : b && p.onDelete(b.span!.id))} title={del ?? `Delete ${pick} (⌫)`}><Icon name="trash" /></button>
              <PausesButton plan={plan} locked={locked} onEdit={p.onEdit} />
            </span>
          );
        })()}
        {v && <span className="tl-info">Version {v.n} · {v.spans.length} clips · {tc(v.duration_s ?? v.runtime_s)}</span>}
        <span className="grow" />
        {v && p.mix && p.base && (p.mix.voice_db !== p.base.voice_db || p.mix.level_db !== p.base.level_db) && (
          <span className="mix-apply" title="The mix is normalised to −14 LUFS after the faders, so what moves is the balance between the tracks">
            <span className={`mix-live ${p.live.active ? "on" : ""}`}>{p.live.active ? "live" : p.live.failed ? "no preview" : "loading…"}</span>
            <span>Voice {dbs(p.mix.voice_db)} · Music {p.mix.level_db} LUFS</span>
            <button className="btn quiet small" onClick={p.onResetMix}>Reset</button>
            <button className="btn small" disabled={!!p.canEdit} title={p.canEdit ?? `Makes Version ${p.next} with this mix ($0, a re-render)`}
              onClick={p.onApplyMix}>Make Version {p.next}</button>
          </span>
        )}
        {mode === "timeline" && (
          <button className={`icon-btn ${p.snap ? "on" : ""}`} onClick={() => p.setSnap(!p.snap)} aria-pressed={p.snap}
            title={`Snapping ${p.snap ? "on" : "off"}: a dragged edge or clip lands on the playhead, clip edges and review issues (N). Hold ⇧ while dragging to move freely. Trims always land on a word.`}>
            <Icon name="magnet" />
          </button>
        )}
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
      {v && mode === "timeline" && bar && <EditBar {...p} v={v} bar={bar} setBar={setBar} />}
      {!v ? <div className="tl-empty mute">The timeline appears when the first version is rendered.</div>
        : mode === "transcript" ? <Transcript {...p} v={v} />
        : (
          <div className="tl-body">
            <div className="tl-heads">
              <div style={{ height: RULER_H }} />
              <div className="tl-head" style={{ height: TRACK_H.overlays }}><Icon name="media" size={13} />B-roll
                <button className="head-add" disabled={!p.canTrim || !v.cut} onClick={() => setBar({ kind: "new" })}
                  title={!p.canTrim ? "Not while an edit runs or in replay" : "Add a Muse Image still at the playhead ($0.01)"}><Icon name="plus" size={11} /></button>
              </div>
              <div className="tl-head" style={{ height: TRACK_H.text }}><Icon name="text" size={13} />Text</div>
              <div className="tl-head" style={{ height: TRACK_H.captions }}><Icon name="captions" size={13} />Captions</div>
              <div className="tl-head video" style={{ height: TRACK_H.video }}><Icon name="film" size={13} />Video</div>
              <Fader label="Voice" icon="wave" height={TRACK_H.audio} unit="dB" range={VOICE_RANGE}
                value={p.mix?.voice_db ?? 0} base={p.base?.voice_db ?? 0} none={!p.mix} disabled={!p.mix || !!p.canEdit}
                title={!p.mix ? "The cut is normalised to −14 LUFS for Reels, TikTok and Shorts: with one track there is nothing to balance. Add music to set the voice against it."
                  : p.canEdit ?? "The voice against the music, heard live (double-click: back to this version's)"}
                onChange={(x) => p.mix && p.onMix({ ...p.mix, voice_db: x })} />
              <Fader label="Music" icon="music" height={TRACK_H.music} unit="LUFS" range={LEVEL_RANGE}
                value={p.mix?.level_db ?? -20} base={p.base?.level_db ?? -20} none={!p.mix} disabled={!p.mix || !!p.canEdit}
                title={!p.mix ? "No music in this version: add a track in the Music panel" : p.canEdit ?? "The bed's level where nobody speaks, heard live; it still ducks under the voice (double-click: back to this version's)"}
                onChange={(x) => p.mix && p.onMix({ ...p.mix, level_db: x })} />
            </div>
            <div className="tl-scroll" ref={scroller}>
              <Canvas {...p} v={v} pps={pps} dur={dur} scroller={scroller} bar={bar} setBar={setBar} plan={plan} />
            </div>
          </div>
        )}
    </div>
  );
}

// ------------------------------------------------------------------- canvas

function Canvas(p: Props & { v: Version; pps: number; dur: number; scroller: React.RefObject<HTMLDivElement> } & BarProps & { plan: PausePlan | null }) {
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
  const tNow = useRef(t);
  tNow.current = t;
  const getT = useCallback(() => tNow.current, []);
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
      <Tracks id={p.id} v={v} blocks={p.blocks} words={p.words} selected={p.selected} selection={p.selection} canTrim={p.canTrim} pps={pps} dur={dur}
        snap={p.snap} getT={getT}
        timeAt={timeAt} onSelect={p.onSelect} onSeek={p.onSeek} onMenu={p.onMenu} onTrim={p.onTrim} onMove={p.onMove}
        mix={p.mix} base={p.base} onMusicPanel={p.onMusicPanel} bar={p.bar} setBar={p.setBar} plan={p.plan} />
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

/** The filmstrip sprite and its frame count. A new url (every draft edit makes one) keeps the last sprite on screen
 *  until the new one has loaded, so the tiles swap in place instead of blinking out. */
function useSprite(url: string | null): { url: string; frames: number } | null {
  const [s, setS] = useState<{ url: string; frames: number } | null>(null);
  useEffect(() => {
    if (!url) return setS(null);
    let live = true;
    const img = new Image();
    img.onload = () => { if (live) setS({ url, frames: Math.max(1, Math.round(img.naturalWidth / THUMB.w)) }); };
    img.src = url;
    return () => { live = false; };
  }, [url]);
  return s;
}

function usePeaks(url: string | null): { rate: number; peaks: number[] } | null {
  const [d, setD] = useState<{ rate: number; peaks: number[] } | null>(null);
  useEffect(() => { // like useSprite: the last waveform stays until the new one arrives
    if (!url) return setD(null);
    let live = true;
    fetch(url).then((r) => (r.ok ? r.json() : null)).then((x) => { if (live) setD(x); }).catch(() => {});
    return () => { live = false; };
  }, [url]);
  return d;
}

type Drag = { key: string; edge: "in" | "out"; snaps: Snap[]; snap: Snap | null; current: number; target: number | null };
type Move = { key: string; slot: number; t: number; target: number | null }; // a clip being dragged: its insertion slot among the others
const MOVE_PX = 6; // a press that travels further than this is a drag, not a click

type TrackProps = Pick<Props, "id" | "blocks" | "words" | "selected" | "selection" | "canTrim" | "onSelect" | "onSeek" | "onMenu" | "onTrim" | "onMove"
  | "mix" | "base" | "onMusicPanel" | "snap"> & BarProps & {
  v: Version; pps: number; dur: number; timeAt: (x: number) => number; getT: () => number; plan: PausePlan | null;
};

const Tracks = memo(function Tracks(p: TrackProps) {
  const { id, v, blocks, words, pps, selected, selection, canTrim, onSelect, onSeek, onMenu, onTrim, onMove, timeAt, getT } = p;
  const q = v.bytes ? `&b=${v.bytes}` : "";
  // a draft (live preview) has no cut: its thumbnails and waveform come from the source through its clips
  const dq = v.draft ? `draft=1&k=${hashKey(v.spans.map((s) => `${s.id}:${s.t_in}:${s.t_out}`).join("|"))}` : "";
  const sprite = v.cut ? `/api/runs/${id}/filmstrip?v=${v.n}${q}` : v.draft ? `/api/runs/${id}/filmstrip?${dq}` : null;
  const strip = useSprite(sprite);
  // with music the cut's audio is two stems: the voice lane draws the speech alone, the music lane the ducked bed
  const peaks = usePeaks(v.cut ? `/api/runs/${id}/peaks?v=${v.n}${v.music ? "&track=voice" : ""}${q}` : v.draft ? `/api/runs/${id}/peaks?${dq}` : null);
  const bedPeaks = usePeaks(v.cut && v.music ? `/api/runs/${id}/peaks?v=${v.n}&track=music${q}` : null);
  const top = peaks ? Math.max(...peaks.peaks, 0.01) : 1; // both lanes on the voice's scale: the bed looks as loud as it is
  const gain = (a?: number, b?: number) => 10 ** (((a ?? 0) - (b ?? 0)) / 20);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [move, setMove] = useState<Move | null>(null);
  const live = blocks.filter((b) => b.kind !== "ghost");
  const tileW = (TRACK_H.video - 6) * (9 / 16);
  const multi = selection.length > 1;
  // snapping: the nearest of the playhead, the other clips' edges and the review's issues within SNAP_PX (⇧: free)
  const snapTo = (x: number, skip: string, free: boolean): number | null => {
    if (!p.snap || free) return null;
    const targets = [getT(), ...live.filter((b) => b.key !== skip).flatMap((b) => [b.t0, b.t1]), ...(v.review?.issues ?? []).map((i) => i.t_s)];
    let best: number | null = null;
    for (const g of targets) if (Math.abs(g - x) * pps <= SNAP_PX && (best == null || Math.abs(g - x) < Math.abs(best - x))) best = g;
    return best;
  };

  const startTrim = (e: React.PointerEvent, b: Block, edge: "in" | "out") => {
    e.stopPropagation();
    e.preventDefault();
    const snaps = edgeSnaps(b, edge, v, words);
    const cur = edge === "out" ? b.span!.word_to : b.span!.word_from;
    let d: Drag = { key: b.key, edge, snaps, snap: null, current: cur, target: null };
    setDrag(d);
    onSelect(b.key);
    const move = (ev: PointerEvent) => {
      const raw = timeAt(ev.clientX);
      const target = snapTo(raw, b.key, ev.shiftKey);
      const x = target ?? raw;
      let best: Snap | null = null;
      for (const s of snaps) if (!best || Math.abs(s.t - x) < Math.abs(best.t - x)) best = s;
      d = { ...d, snap: best, target };
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
    const slotAt = (cx: number, free: boolean) => {
      const raw = timeAt(cx);
      const target = snapTo(raw, b.key, free);
      const at = target ?? raw;
      const slot = others.filter((o) => (o.t0 + o.t1) / 2 < at).length;
      return { slot, t: slot < others.length ? others[slot].t0 : others.at(-1)?.t1 ?? 0, target };
    };
    const mv = (ev: PointerEvent) => {
      if (!m && Math.abs(ev.clientX - x0) < MOVE_PX) return;
      m = { key: b.key, ...slotAt(ev.clientX, ev.shiftKey) };
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
      {/* B-roll: the Muse Image stills over the cut (a person's ask only) */}
      <div className="track overlays" style={{ height: TRACK_H.overlays }} onPointerDown={(e) => { if (e.target === e.currentTarget) { p.setBar(null); onSeek(timeAt(e.clientX)); } }}
        onDoubleClick={(e) => { if (e.target === e.currentTarget && canTrim && v.cut) { onSeek(timeAt(e.clientX)); p.setBar({ kind: "new" }); } }}>
        {v.overlays.filter((o) => o.shown && o.t_in != null).map((o) => (
          <button key={o.id} className={`item still ${o.placement} ${p.bar?.kind === "overlay" && p.bar.id === o.id ? "sel" : ""}`}
            style={{ left: o.t_in! * pps, width: Math.max(14, (o.t_out! - o.t_in!) * pps - 1) }}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => { p.setBar({ kind: "overlay", id: o.id }); onSeek(o.t_in!, o.t_out!); }}
            title={`B-roll ${o.id} · ${placementLabel(o.placement)} · ${tc(o.t_in)}–${tc(o.t_out)}\n“${o.prompt ?? ""}”`}>
            {o.image && <img src={fileUrl(id, o.image)} alt="" loading="lazy" />}
            <span>{o.prompt}</span>
          </button>
        ))}
        {!v.overlays.some((o) => o.shown) && v.cut && <span className="track-hint mute">Double-click to add a Muse Image still at a moment</span>}
      </div>
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
          const sel = selected === b.key || selection.includes(b.key);
          const isDrag = drag?.key === b.key;
          return (
            <div key={b.key} className={`clip ${b.kind} ${b.span?.role ?? ""} ${sel ? "sel" : ""} ${sel && multi ? "multi" : ""}`} style={{ left: b.t0 * pps + 1, width: w }}
              onPointerDown={(e) => { // a click selects and leaves the playhead where it is (double-click: seek there)
                if (e.button !== 0) return;
                if (b.kind === "span" && (e.shiftKey || e.metaKey || e.ctrlKey)) { onSelect(b.key, e.shiftKey ? "range" : "toggle"); return; }
                onSelect(b.key);
                if (b.kind === "span" && canTrim) startMove(e, b);
              }}
              onDoubleClick={(e) => onSeek(b.kind === "title" ? 0 : timeAt(e.clientX))}
              onContextMenu={(e) => { e.preventDefault(); if (b.kind === "span") { onSelect(b.key); onMenu({ x: e.clientX, y: e.clientY, span: b.span!.id }); } }}
              title={b.kind === "span" ? `${b.span!.id} · ${b.span!.role} · ${(b.t1 - b.t0).toFixed(1)} s\n${spanText(v, b.span!)}` : "Title card"}>
              <div className="film">
                {strip && Array.from({ length: n }, (_, k) => {
                  const at = b.t0 + ((k + 0.5) * tileW) / pps;
                  const idx = Math.min(strip.frames - 1, Math.max(0, Math.floor(at / THUMB.step)));
                  return <span key={k} className="tile" style={{ left: k * tileW, width: tileW, backgroundImage: `url(${strip.url})`,
                    backgroundSize: `${strip.frames * tileW}px 100%`, backgroundPosition: `-${idx * tileW}px 0` }} />;
                })}
              </div>
              <span className="clip-label">
                <b>{b.kind === "title" ? "Title" : b.span!.id}</b>
                {b.kind === "span" && w > 70 && <span className="cl-words">{spanText(v, b.span!)}</span>}
              </span>
              {b.kind === "span" && canTrim && ((sel && !multi) || isDrag) && <>
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
          <button key={b.key} className={`ghost-mark ${p.bar?.kind === "ghost" && p.bar.key === b.key ? "sel" : ""}`} style={{ left: b.t0 * pps }}
            onPointerDown={(e) => e.stopPropagation()} onClick={() => p.setBar({ kind: "ghost", key: b.key })}
            title={`${b.span!.id} was deleted from Version ${v.origin.from}: “${b.span!.words}” (click to bring it back)`} />
        ))}
        {(p.plan?.pauses ?? []).filter((x) => x.t_cut != null).map((x) => (
          <span key={`${x.span}:${x.word}`} className="pause-mark" style={{ left: x.t_cut! * pps, width: Math.max(3, x.was_s * pps) }}
            title={`${x.was_s.toFixed(2)} s pause after “${x.after}”: Remove pauses cuts it to ${x.keep_s} s`} />
        ))}
        {move && (() => {
          const b = live.find((x) => x.key === move.key)!;
          const spans = live.filter((x) => x.kind === "span");
          const same = move.slot === spans.findIndex((x) => x.key === move.key);
          return <>
            {move.target != null && <span className="snap-line" style={{ left: move.target * pps }} />}
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
          const end = grow && drag.snap.end ? ` · the ${drag.edge === "out" ? "end" : "start"} of the speaker's turn` : "";
          return <>
            {drag.target != null && <span className="snap-line" style={{ left: drag.target * pps }} />}
            <span className={`trim-zone ${grow ? "grow" : "cut"}`} style={{ left: a * pps, width: Math.max(1, (z - a) * pps) }} />
            <span className="trim-tip" style={{ left: drag.snap.t * pps }}>
              {n === 0 ? "no change" : `${grow ? "+" : "−"}${n} word${n === 1 ? "" : "s"} · ${drag.edge === "out" ? "ends on" : "starts on"} “${drag.snap.label}”${end}`}
            </span>
          </>;
        })()}
      </div>
      {/* audio: the voice (the cut's own audio, or its speech stem when there is music) and the music bed under it */}
      <div className="track audio" style={{ height: TRACK_H.audio }} onPointerDown={(e) => { onSelect(null); onSeek(timeAt(e.clientX)); }}>
        <Wave peaks={peaks} pps={pps} height={TRACK_H.audio} width={Math.ceil(p.dur * pps)} top={top}
          gain={gain(p.mix?.voice_db, p.base?.voice_db)} />
      </div>
      <div className="track audio music" style={{ height: TRACK_H.music }} onPointerDown={(e) => { if (e.target === e.currentTarget || (e.target as HTMLElement).tagName === "CANVAS") { onSelect(null); onSeek(timeAt(e.clientX)); } }}>
        {v.music ? <>
          <Wave peaks={bedPeaks} pps={pps} height={TRACK_H.music} width={Math.ceil(p.dur * pps)} top={top} color="--wave-music"
            gain={gain(p.mix?.level_db, p.base?.level_db)} />
          <button className="music-tag" onPointerDown={(e) => e.stopPropagation()} onClick={p.onMusicPanel} title="Change the track or the duck in the Music panel">
            <Icon name="music" size={11} /><span className="mt-name">{v.music.track.replace(/_/g, " ")}</span> · ducked {v.music.duck_db} dB under the voice
          </button>
        </> : (
          <button className="music-add" onPointerDown={(e) => e.stopPropagation()} onClick={p.onMusicPanel}>
            <Icon name="plus" size={11} /> Add music
          </button>
        )}
      </div>
    </div>
  );
});

const dbs = (x: number) => `${x > 0 ? "+" : x < 0 ? "−" : "±"}${Math.abs(x)} dB`;

/** A track head with its fader: the value moves the track live; double-click puts it back on the version's own. */
function Fader({ label, icon, height, unit, range, value, base, none, disabled, title, onChange }: {
  label: string; icon: string; height: number; unit: "dB" | "LUFS"; range: [number, number]; value: number; base: number;
  none: boolean; disabled: boolean; title: string; onChange: (x: number) => void; // none: the version has no music
}) {
  const moved = value !== base;
  return (
    <div className={`tl-head fader-head ${disabled ? "off" : ""}`} style={{ height }} title={title}>
      <div className="fh-top"><Icon name={icon} size={13} />{label}
        <span className={`fh-val ${moved ? "moved" : ""}`}>{none ? "" : unit === "dB" ? dbs(value) : `${value}`}</span>
      </div>
      <input type="range" className="fader" min={range[0]} max={range[1]} step={0.5} value={value} disabled={disabled}
        aria-label={`${label} level`} onChange={(e) => onChange(Number(e.target.value))} onDoubleClick={() => onChange(base)} />
    </div>
  );
}

function Wave({ peaks, pps, height, width, top: topIn, gain = 1, color = "--wave" }: {
  peaks: { rate: number; peaks: number[] } | null; pps: number; height: number; width: number; top?: number; gain?: number; color?: string;
}) {
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
    g.fillStyle = css.getPropertyValue(color).trim() || "#4f8a6b";
    const top = topIn ?? Math.max(...peaks.peaks, 0.01);
    const mid = height / 2;
    for (let x = 0; x < w; x++) {
      const a = Math.floor((x / pps) * peaks.rate), b = Math.max(a + 1, Math.floor(((x + 1) / pps) * peaks.rate));
      let m = 0;
      for (let i = a; i < b && i < peaks.peaks.length; i++) if (peaks.peaks[i] > m) m = peaks.peaks[i];
      const h = Math.max(0.5, (Math.min(1, Math.sqrt((m * gain) / top)) * (height - 6)) / 2);
      g.fillRect(x, mid - h, 1, h * 2);
    }
  }, [peaks, pps, height, width, topIn, gain, color]);
  return peaks ? <canvas ref={ref} className="wave" /> : <span className="wave-wait mute">waveform…</span>;
}

// ------------------------------------------------------------------- transcript (the paper edit)

function Transcript({ v, blocks, t, selected, onSelect, onSeek, onMenu, wordSel, onWordSel, onCutWords, words, canTrim, onEdit }: Props & { v: Version }) {
  const scroller = useRef<HTMLDivElement>(null);
  const issues = v.review?.issues ?? [];
  const playing = useMemo(() => blockAt(blocks, t), [blocks, t]);
  useEffect(() => {
    const el = scroller.current?.querySelector(".block.playing") as HTMLElement | null;
    el?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
  }, [playing?.key]);
  const lastLive = blocks.filter((x) => x.kind !== "ghost").at(-1);
  const flat = useMemo(() => flatWords(blocks), [blocks]);
  const byKey = useMemo(() => new Map(flat.map((x) => [x.k, x])), [flat]);
  const range = selRange(flat, wordSel);
  const picked = range ? flat.slice(range[0], range[1] + 1) : [];
  const inSel = useMemo(() => new Set(picked.map((x) => x.k)), [range?.[0], range?.[1], flat]); // eslint-disable-line
  const drag = useRef<string | null>(null); // the anchor while the mouse is down on a word
  useEffect(() => {
    const up = () => { drag.current = null; };
    window.addEventListener("pointerup", up);
    return () => window.removeEventListener("pointerup", up);
  }, []);
  const down = (e: React.PointerEvent, k: string, at: number) => {
    if (e.button !== 0) return;
    e.preventDefault(); // no native text selection: the words are the selection
    e.stopPropagation();
    if (e.shiftKey && wordSel) onWordSel({ a: wordSel.a, b: k });
    else { onWordSel({ a: k, b: k }); onSeek(at); }
    drag.current = e.shiftKey && wordSel ? wordSel.a : k;
  };
  const enter = (k: string) => { if (drag.current) onWordSel({ a: drag.current, b: k }); };
  const secs = picked.length ? picked.reduce((a, x, j) => a + (x.e - x.t) + (j && picked[j - 1].span.id === x.span.id ? x.t - picked[j - 1].e : 0), 0) : 0;
  // the words this version does not use, in recording order: select some and put them back in
  const unused = useMemo(() => unusedRuns(v, words), [v, words]);
  const [src, setSrc] = useState<{ turn: number; a: number; b: number } | null>(null);
  const srcDrag = useRef<{ turn: number; a: number } | null>(null);
  useEffect(() => setSrc(null), [v.n]);
  useEffect(() => {
    const up = () => { srcDrag.current = null; };
    window.addEventListener("pointerup", up);
    return () => window.removeEventListener("pointerup", up);
  }, []);
  const srcDown = (e: React.PointerEvent, turn: number, i: number) => {
    if (e.button !== 0) return;
    e.preventDefault();
    onWordSel(null);
    const a = e.shiftKey && src && src.turn === turn ? src.a : i;
    setSrc({ turn, a: Math.min(a, i), b: Math.max(a, i) });
    srcDrag.current = { turn, a };
  };
  const srcEnter = (turn: number, i: number) => {
    const d = srcDrag.current;
    if (d && d.turn === turn) setSrc({ turn, a: Math.min(d.a, i), b: Math.max(d.a, i) });
  };
  const srcWords = src ? (words[String(src.turn)] ?? []).slice(src.a, src.b + 1) : [];
  const srcFree = src ? unused.find((r) => r.turn === src.turn && r.from <= src.a && src.b <= r.to) : null;
  const insert = (where: "source" | "playhead") => {
    if (!src || !srcFree) return;
    let before: string | undefined;
    if (where === "playhead") {
      const live = blocks.filter((b) => b.kind === "span");
      const at = blockAt(blocks, t);
      const k = at && at.kind === "span" ? live.indexOf(at) + (t > (at.t0 + at.t1) / 2 ? 1 : 0) : 0;
      before = live[k]?.span!.id ?? "end";
    }
    const text = srcWords.map((w) => w.w).join(" ");
    onEdit("insert", { turn: src.turn, word_from: src.a, word_to: src.b, ...(before ? { before } : {}), text: text.slice(0, 60) },
      `Adding back “${text.slice(0, 50)}${text.length > 50 ? "…" : ""}” ${before ? (before === "end" ? "at the end" : `before ${before}`) : "where it sits in the recording"} ($0, a re-render)…`);
    setSrc(null);
  };
  return (
    <div className="strip">
      {src ? (
        <div className="strip-note sel-note">
          <b>{srcWords.length} word{srcWords.length === 1 ? "" : "s"}</b> not in the cut · about {srcWords.length ? (srcWords.at(-1)!.e - srcWords[0].t).toFixed(1) : 0} s
          <button className="btn small" disabled={!canTrim || srcWords.length < 2 || !srcFree} onClick={() => insert("source")}
            title={srcWords.length < 2 ? "A clip keeps at least two words" : "Between the clips around it in the recording"}><Icon name="plus" size={12} /> Insert</button>
          <button className="btn small quiet" disabled={!canTrim || srcWords.length < 2 || !srcFree} onClick={() => insert("playhead")}
            title="Next to the clip under the playhead">At the playhead</button>
          <button className="btn small quiet" onClick={() => setSrc(null)}>Clear</button>
        </div>
      ) : picked.length ? (
        <div className="strip-note sel-note">
          <b>{picked.length} word{picked.length === 1 ? "" : "s"}</b> selected · about {secs.toFixed(1)} s
          <button className="btn small danger" onClick={onCutWords}><Icon name="trash" size={12} /> Delete</button>
          <span className="mute">⌫ · Shift-click extends · Esc clears</span>
        </div>
      ) : (
        <div className="strip-note mute">Click or drag over words to select them, ⌫ deletes them · struck words were cut from Version {v.origin.from ?? v.n - 1} · grey words at the end are not in the cut: select some to put them back</div>
      )}
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
                  {b.kind === "ghost" ? b.kept.map((w) => <s key={w.i} className="w cut">{w.w + " "}</s>) : b.kept.map((w) => {
                    const k = `${b.span!.id}:${w.i}`;
                    const fw = byKey.get(k);
                    return <span key={w.i} className={`w pick ${inSel.has(k) ? "wsel" : ""}`} data-k={k}
                      onPointerDown={(e) => down(e, k, (fw?.t ?? b.t0) + 0.01)} onPointerEnter={() => enter(k)} onClick={(e) => e.stopPropagation()}>{w.w + " "}</span>;
                  })}
                  {b.after.map((w) => <s key={`a${w.i}`} className="w cut">{w.w + " "}</s>)}
                </>}
              </div>
              {b.span?.callout && b.kind === "span" && <div className="b-callout">callout · {b.span.callout}</div>}
            </div>
          );
        })}
        {unused.length > 0 && (
          <div className="block unused">
            <div className="b-top"><span className="b-id">Not in the cut</span><span className="b-role">recording order</span></div>
            <div className="b-words">
              {unused.map((r) => (
                <span key={`${r.turn}:${r.from}`} className="u-run" title={`Turn ${r.turn} · ${tc(r.t)} in the recording`}>
                  {(words[String(r.turn)] ?? []).slice(r.from, r.to + 1).map((w) => {
                    const on = src && src.turn === r.turn && w.i >= src.a && w.i <= src.b;
                    return <span key={w.i} className={`w unused-w ${on ? "wsel" : ""}`}
                      onPointerDown={(e) => srcDown(e, r.turn, w.i)} onPointerEnter={() => srcEnter(r.turn, w.i)}>{w.w + " "}</span>;
                  })}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------- Batch D: pauses, B-roll stills, ghost clips

type Bar = { kind: "new" } | { kind: "overlay"; id: string } | { kind: "ghost"; key: string };
type BarProps = { bar: Bar | null; setBar: (b: Bar | null) => void };
const placementLabel = (k: string) => PLACEMENTS.find((x) => x.k === k)?.label ?? k;

/** What "Remove pauses" would shorten in this version (GET /pauses, a dry run of montivo.human pauses), $0. */
function usePausePlan(id: string, v: Version | null): PausePlan | null {
  const [plan, setPlan] = useState<PausePlan | null>(null);
  const n = v?.cut ? v.n : null;
  useEffect(() => {
    setPlan(null);
    if (n == null) return;
    let live = true;
    api<PausePlan>(`/api/runs/${id}/pauses?v=${n}`).then((x) => { if (live) setPlan(x); }).catch(() => {});
    return () => { live = false; };
  }, [id, n]);
  return plan;
}

function PausesButton({ plan, locked, onEdit }: { plan: PausePlan | null; locked: string | null; onEdit: Props["onEdit"] }) {
  const n = plan?.count ?? 0;
  const why = locked ?? (!plan ? "Looking for pauses…" : n ? null : `No pause over ${plan.over_s} s in this version`);
  return (
    <button className="btn quiet small pauses-btn" disabled={!!why}
      onClick={() => plan && onEdit("pauses", { over: plan.over_s, keep: plan.keep_s },
        `Shortening ${n} pause${n === 1 ? "" : "s"} to ${plan.keep_s} s (−${plan.saved_s.toFixed(1)} s, $0, a re-render)…`)}
      title={why ?? `Shorten every pause over ${plan!.over_s} s to about ${plan!.keep_s} s: the clip splits at the pause, no word is removed. The reviewer may still give a dramatic pause back.`}>
      <Icon name="wave" size={12} />{n ? `${n} pause${n === 1 ? "" : "s"}, −${plan!.saved_s.toFixed(1)} s` : "No pauses"}
    </button>
  );
}

/** The bar under the toolbar for what is selected on the B-roll track or the ghost clips: a new still's prompt, a
 *  still's place and length, or a deleted clip to bring back. */
function EditBar(p: Props & BarProps & { v: Version; bar: Bar }) {
  const { v, bar, setBar, onEdit, t, canTrim, words, blocks } = p;
  const [prompt, setPrompt] = useState("");
  const [placement, setPlacement] = useState<Overlay["placement"]>("full");
  const [dur, setDur] = useState(2.5);
  const locked = !canTrim ? "Not while an edit runs or in replay" : null;
  if (bar.kind === "new") {
    const go = () => {
      const q = prompt.trim();
      if (!q) return;
      onEdit("overlay", { at: Number(t.toFixed(2)), prompt: q, placement, dur },
        `Muse Image is making the still for ${tc(t)} (about 10 s, $0.01), then a re-render…`);
      setBar(null); setPrompt("");
    };
    return (
      <div className="edit-bar">
        <Icon name="media" size={13} /><b>B-roll at {tc(t)}</b>
        <input className="eb-prompt" autoFocus value={prompt} placeholder="What should the still show? e.g. a flow diagram of the four models"
          onChange={(e) => setPrompt(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") go(); if (e.key === "Escape") setBar(null); e.stopPropagation(); }} />
        <Placement value={placement} onChange={setPlacement} />
        <select value={dur} onChange={(e) => setDur(Number(e.target.value))} aria-label="Seconds on screen">
          {[1.5, 2, 2.5, 3, 4, 5].map((x) => <option key={x} value={x}>{x} s</option>)}
        </select>
        <button className="btn small" disabled={!!locked || !prompt.trim()} onClick={go} title={locked ?? "One Muse Image call, $0.01"}>Make it</button>
        <button className="icon-btn" onClick={() => setBar(null)} title="Close"><Icon name="close" /></button>
      </div>
    );
  }
  if (bar.kind === "overlay") {
    const o = v.overlays.find((x) => x.id === bar.id);
    if (!o) return null;
    const set = (body: Record<string, unknown>, note: string) => { onEdit("overlay", { set: o.id, ...body }, note); setBar(null); };
    return (
      <div className="edit-bar">
        {o.image && <img className="eb-thumb" src={fileUrl(p.id, o.image)} alt="" />}
        <b>{o.id}</b><span className="eb-text" title={o.prompt ?? ""}>{o.prompt}</span>
        <Placement value={o.placement} disabled={!!locked}
          onChange={(k) => k !== o.placement && set({ placement: k }, `B-roll ${o.id} as ${placementLabel(k).toLowerCase()} ($0, a re-render)…`)} />
        <button className="btn small quiet" disabled={!!locked || o.dur <= 1} onClick={() => set({ dur: Math.max(0.8, o.dur - 0.5) }, `B-roll ${o.id} shorter ($0)…`)}>−0.5 s</button>
        <span className="mute">{o.dur} s</span>
        <button className="btn small quiet" disabled={!!locked || o.dur >= 8} onClick={() => set({ dur: Math.min(8, o.dur + 0.5) }, `B-roll ${o.id} longer ($0)…`)}>+0.5 s</button>
        <button className="btn small quiet" disabled={!!locked} onClick={() => set({ at: Number(t.toFixed(2)) }, `B-roll ${o.id} to ${tc(t)} ($0)…`)}
          title="Start it at the playhead">To {tc(t)}</button>
        <button className="btn small danger" disabled={!!locked} onClick={() => { onEdit("overlay", { remove: o.id }, `Taking B-roll ${o.id} out ($0)…`); setBar(null); }}>
          <Icon name="trash" size={12} /> Remove</button>
        <button className="icon-btn" onClick={() => setBar(null)} title="Close"><Icon name="close" /></button>
      </div>
    );
  }
  const g = blocks.find((b) => b.key === bar.key);
  if (!g?.span) return null;
  const r = ghostRestore(v, g, blocks, words);
  return (
    <div className="edit-bar">
      <Icon name="undo" size={13} /><b>{g.span.id} deleted</b>
      <span className="eb-text">“{r.text || g.span.words}”</span>
      <button className="btn small" disabled={!!locked || !!r.error} title={locked ?? r.error ?? `Back ${r.before === "end" ? "at the end" : `before ${r.before}`}, where it was ($0, a re-render)`}
        onClick={() => { onEdit("insert", { turn: g.span!.turn_id, word_from: r.from, word_to: r.to, before: r.before, text: r.text.slice(0, 60) },
          `Bringing ${g.span!.id} back ($0, a re-render)…`); setBar(null); }}>Bring it back</button>
      {r.error && <span className="mute">{r.error}</span>}
      <button className="icon-btn" onClick={() => setBar(null)} title="Close"><Icon name="close" /></button>
    </div>
  );
}

function Placement({ value, onChange, disabled }: { value: string; onChange: (k: Overlay["placement"]) => void; disabled?: boolean }) {
  return (
    <span className="seg-group small">
      {PLACEMENTS.map((x) => (
        <button key={x.k} className={`seg ${value === x.k ? "on" : ""}`} disabled={disabled} title={x.note} onClick={() => onChange(x.k)}>{x.label}</button>
      ))}
    </span>
  );
}

/** A short stable key for a string (a draft's clips), so a changed draft fetches its media again. */
function hashKey(x: string): string {
  let h = 2166136261;
  for (let i = 0; i < x.length; i++) h = Math.imul(h ^ x.charCodeAt(i), 16777619);
  return (h >>> 0).toString(36);
}
