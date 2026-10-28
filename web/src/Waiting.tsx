import { useEffect, useMemo, useRef, useState } from "react";
import { fileUrl, tc } from "./api";
import { Icon } from "./icons";
import { Bar, measure, useTick } from "./Progress";
import type { LogLine, Progress, RunState, Span, Word } from "./types";

type Line = { words: Word[]; t: number; e: number };
type Move = { a: number; b: number; what: string };

const VOICE_DONE = /^voice: (\d+) turns, (\d+) words, speakers \[([^\]]*)\]/;
const TRACK_DONE = /^track: ([^:]+): (\d+) ids/;
const MOVE = /^watch: speaker moves ([\d.]+)-([\d.]+)s: (.+)$/;
const PLAN_DONE = /^(spark: plan: completed|edges:)/;
const LINE_WORDS = 4;

/** Caption lines from Voice's words: at most LINE_WORDS words, a new line after a sentence end or a pause over 0.6 s. */
function captionLines(words: Word[]): Line[] {
  const out: Line[] = [];
  let cur: Word[] = [];
  const flush = () => { if (cur.length) out.push({ words: cur, t: cur[0].t, e: cur[cur.length - 1].e }); cur = []; };
  words.forEach((w, k) => {
    if (cur.length && (w.t - cur[cur.length - 1].e > 0.6 || cur.length >= LINE_WORDS)) flush();
    cur.push(w);
    if (/[.?!]$/.test(w.w) || k === words.length - 1) flush();
  });
  return out;
}

/** The viewer while a new project has no cut yet. The source clip plays muted and what the agent finds lands on it as
 *  the log reports it (live or replayed, so replay shows the same story): Voice's words as captions, the moves Watch
 *  noted, the steps done so far, and once the plan is in, the paper edit itself played clip by clip from the source,
 *  before the render exists. Gated on the log lines, not the state, because in replay the state is already finished. */
export function Waiting({ state, lines, progress }: { state: RunState; lines: LogLine[]; progress: Progress | null }) {
  const video = useRef<HTMLVideoElement>(null);
  const [t, setT] = useState(0);
  const [dur, setDur] = useState(0);
  const [muted, setMuted] = useState(true);
  const [failed, setFailed] = useState<string | null>(null);
  const idx = useRef(0); // the paper edit's clip being played
  const last = useRef(0); // where the preview is: a new source (footage -> proxy) picks up there
  useTick(!!progress?.active, 500);

  const seen = useMemo(() => {
    let proxy = false, voice: RegExpMatchArray | null = null, plan = false;
    const sam: string[] = [], moves: Move[] = [];
    for (const l of lines) {
      if (l.msg.startsWith("ingest: proxy")) proxy = true;
      voice = voice ?? l.msg.match(VOICE_DONE);
      const tr = l.msg.match(TRACK_DONE); if (tr && !sam.includes(tr[1])) sam.push(tr[1]);
      const m = l.msg.match(MOVE); if (m) moves.push({ a: Number(m[1]), b: Number(m[2]), what: m[3] });
      if (PLAN_DONE.test(l.msg)) plan = true;
    }
    return { proxy, voice, sam, moves, plan };
  }, [lines]);

  const footage = state.run.footage?.split("/").pop();
  const src = seen.proxy ? fileUrl(state.id, "proxy.mp4") : footage ? `/api/footage/${encodeURIComponent(footage)}` : null;
  const mirror = !seen.proxy && !!state.run.hflip; // the proxy is made from the mirrored source; the raw clip is not
  const v1 = state.versions.find((v) => v.n === 1);
  const spans: Span[] = useMemo(() => (seen.plan && v1 ? [...v1.spans].sort((a, b) => a.order - b.order) : []), [seen.plan, v1]);
  const paper = spans.length > 0;
  const words = useMemo(() => {
    if (!seen.voice) return [];
    const all = Object.values(state.words).flat().sort((a, b) => a.t - b.t);
    return paper ? all.filter((w) => spans.some((s) => w.t >= s.t_in - 0.05 && w.e <= s.t_out + 0.05)) : all;
  }, [seen.voice, state.words, paper, spans]);
  const caps = useMemo(() => captionLines(words), [words]);

  useEffect(() => { // the paper edit starts on its first clip
    idx.current = 0;
    const el = video.current;
    if (paper && el && el.readyState > 0) el.currentTime = spans[0].t_in;
  }, [paper]); // eslint-disable-line

  useEffect(() => { // the clock and, in the paper edit, the jump from each clip's out point to the next clip's in point
    let raf = 0;
    const tick = () => {
      const el = video.current;
      if (el) {
        const x = el.currentTime;
        if (paper) {
          const s = spans[idx.current % spans.length];
          if (x >= s.t_out - 0.02 || el.ended) {
            idx.current = (idx.current + 1) % spans.length;
            el.currentTime = spans[idx.current].t_in;
            if (el.paused) el.play().catch(() => {});
          } else if (x < s.t_in - 0.3 || x > s.t_out + 0.3) el.currentTime = s.t_in;
        }
        last.current = x;
        setT(x);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [paper, spans]);

  useEffect(() => setFailed(null), [src]);

  const cap = caps.find((l) => t >= l.t - 0.05 && t <= l.e + 0.35) ?? null;
  const move = !paper ? seen.moves.find((m) => t >= m.a && t <= m.b) ?? null : null;
  const span = paper ? spans[idx.current % spans.length] : null;
  const m = progress ? measure(progress) : null;
  const found: string[] = [];
  if (seen.voice) {
    const n = seen.voice[3].split(",").filter((x) => x.trim()).length;
    found.push(`${seen.voice[2]} words heard · ${n} speaker${n === 1 ? "" : "s"}`);
  }
  if (seen.sam.length) found.push(`SAM tracking ${seen.sam.join(" + ")}`);
  if (seen.moves.length) found.push(`${seen.moves.length} move${seen.moves.length === 1 ? "" : "s"} spotted`);
  if (paper) found.push(`${spans.length} clips picked · ${tc(spans.reduce((a, s) => a + s.t_out - s.t_in, 0), 0)}`);
  const total = dur || state.media.duration || 0;

  return (
    <div className="player">
      <div className="frame916 wait-frame">
        {src && !failed ? (
          <video ref={video} src={src} muted={muted} autoPlay playsInline loop={!paper} preload="auto"
            className={mirror ? "mirror" : ""}
            onLoadedMetadata={(e) => {
              const el = e.currentTarget;
              setDur(el.duration || 0);
              const at = paper ? spans[idx.current % spans.length].t_in : last.current;
              if (at > 0 && at < el.duration) el.currentTime = at;
              el.play().catch(() => {});
            }}
            onError={() => setFailed(src)} />
        ) : (
          <div className="wait-brand" aria-hidden>
            <svg viewBox="0 0 64 64" width="96" height="96">
              <path className="wb-l" fill="#F3F0E8" d="M6 6H24V14H14V50H24V58H6Z" />
              <path className="wb-r" fill="#F3F0E8" d="M58 6H40V14H50V50H40V58H58Z" />
              <path className="wb-v" fill="#FFC629" d="M14 14H24L32 28L40 14H50L32 44Z" />
            </svg>
          </div>
        )}
        <div className="wait-shade" />

        <div className="wait-top">
          {progress && m ? (
            <div className="wait-status">
              <div className="ws-line"><span className="pulse" /> <b>{progress.title}</b><span className="mono">{tc(Math.max(m.job, 0), 0)}</span></div>
              {progress.detail && <div className="ws-detail">{progress.detail}</div>}
              <Bar key={progress.title} frac={m.frac} small />
            </div>
          ) : (
            <div className="wait-status"><div className="ws-line"><span className="pulse" /> <b>Getting your footage ready</b></div></div>
          )}
          <div className="wait-found">
            {found.map((f) => <span key={f.split(" ")[1]} className="wf-chip"><Icon name="check" size={12} /> {f}</span>)}
          </div>
        </div>

        {move && <div key={move.a} className="wait-note"><span className="wn-who">Muse Spark saw</span>{move.what}</div>}
        {span && (
          <div key={span.id} className="wait-note">
            <span className="wn-who">Clip {(idx.current % spans.length) + 1} of {spans.length} · {span.role}</span>
            {span.why}
          </div>
        )}
        {cap && (
          <div className="wait-cap">
            {cap.words.map((w) => <span key={w.i} className={t >= w.t && t <= w.e + 0.05 ? "on" : ""}>{w.w} </span>)}
          </div>
        )}

        <div className="wait-foot">
          <span className="wait-tag">{paper ? "Paper edit preview · the render is on its way" : src && !failed ? "Your footage · Montivo is watching" : "Uploading and probing"}</span>
          {src && !failed && (
            <button className="wait-sound" onClick={(e) => { e.stopPropagation(); setMuted((x) => !x); }} title={muted ? "Sound on" : "Sound off"}>
              <Icon name={muted ? "mute" : "volume"} size={15} />
            </button>
          )}
        </div>
        {total > 0 && (
          <div className="wait-strip" title="The source clip: the words Voice heard, the clips the planner kept, where the preview is">
            {words.length > 0 && !paper && words.map((w) => <i key={w.i} className="ws-word" style={{ left: `${(w.t / total) * 100}%` }} />)}
            {spans.map((s) => <i key={s.id} className={`ws-span ${s.id === span?.id ? "on" : ""}`}
              style={{ left: `${(s.t_in / total) * 100}%`, width: `${((s.t_out - s.t_in) / total) * 100}%` }} />)}
            <i className="ws-head" style={{ left: `${Math.min(t / total, 1) * 100}%` }} />
          </div>
        )}
      </div>
    </div>
  );
}
