import { Fragment } from "react";
import { crit, fileUrl, frameUrl, tc, usd } from "./api";
import { chain, originLabel, parentOf, punchLabel, sevRank, type Block } from "./edit";
import { Icon } from "./icons";
import type { Issue, PunchIn, RunState, Version, Word } from "./types";

export type PunchAsk = { on: boolean } | { skip: string };

type Seek = (a: number, b?: number) => void;

/** The right column: what is selected (a clip, the title card, a punch-in), or with nothing selected the whole cut's
 *  review. */
export function Inspector({ id, state, versions, v, block, punch, next, words, busy, canEdit, onSeek, onPick, onDelete, onAsk, onReview, onPunch, onClear }: {
  id: string; state: RunState; versions: Version[]; v: Version | null; block: Block | null; punch: PunchIn | null; next: number;
  words: Record<string, Word[]>; busy: boolean; canEdit: string | null; onSeek: Seek; onPick: (n: number) => void;
  onDelete: (span: string) => void; onAsk: (span: string) => void; onReview: () => void; onPunch: (a: PunchAsk) => void;
  onClear: () => void;
}) {
  if (!v) return <section className="inspector"><div className="insp-empty">Nothing rendered yet.</div></section>;
  if (punch) return <PunchInspector id={id} v={v} p={punch} next={next} canEdit={canEdit} onSeek={onSeek} onPunch={onPunch} onClear={onClear} />;
  if (block?.kind === "span") return <ClipInspector id={id} state={state} v={v} b={block} words={words} busy={busy} canEdit={canEdit} onSeek={onSeek} onDelete={onDelete} onAsk={onAsk} onClear={onClear} />;
  if (block?.kind === "title") return <TitleInspector state={state} v={v} onClear={onClear} />;
  return <CutReview id={id} state={state} versions={versions} v={v} next={next} canEdit={canEdit} onSeek={onSeek} onPick={onPick}
    onReview={onReview} onPunch={onPunch} />;
}

function Head({ title, sub, onClear }: { title: string; sub?: string; onClear?: () => void }) {
  return (
    <div className="panel-head">
      <span className="ph-title">{title}</span>{sub && <span className="ph-sub">{sub}</span>}
      <span className="grow" />
      {onClear && <button className="icon-btn" onClick={onClear} title="Clear selection (Esc)"><Icon name="close" size={14} /></button>}
    </div>
  );
}

function ClipInspector({ id, state, v, b, words, busy, canEdit, onSeek, onDelete, onAsk, onClear }: {
  id: string; state: RunState; v: Version; b: Block; words: Record<string, Word[]>; busy: boolean; canEdit: string | null;
  onSeek: Seek; onDelete: (span: string) => void; onAsk: (span: string) => void; onClear: () => void;
}) {
  const sp = b.span!;
  const all = v.review?.issues ?? [];
  const last = b.t1 >= (v.duration_s ?? b.t1) - 0.05;
  const issues = all.filter((i) => i.t_s >= b.t0 - 0.05 && (i.t_s < b.t1 || (last && i.t_s >= b.t1)))
    .sort((a, c) => sevRank(a.severity) - sevRank(c.severity) || a.t_s - c.t_s);
  const call = v.callouts.find((c) => c.span === sp.id);
  const tw = words[String(sp.turn_id)] ?? [];
  const deleteBlocked = canEdit ?? (busy ? "an edit is running" : v.spans.length < 2 ? "the only clip" : null);
  return (
    <section className="inspector">
      <Head title={`Clip ${sp.id}`} sub={sp.role} onClear={onClear} />
      <div className="panel-body">
        <div className="insp-thumb-row">
          <button className="insp-thumb" onClick={() => onSeek(b.t0 + 0.02)} title="Go to the clip's start">
            <img src={frameUrl(id, v.n, b.t0 + Math.min(0.5, (b.t1 - b.t0) / 2))} alt="" />
          </button>
          <dl className="props">
            <dt>In</dt><dd className="mono">{tc(b.t0, 2)}</dd>
            <dt>Out</dt><dd className="mono">{tc(b.t1, 2)}</dd>
            <dt>Duration</dt><dd className="mono">{(b.t1 - b.t0).toFixed(2)} s</dd>
            <dt>Source</dt><dd className="mono" title="Where the clip sits in the source footage">{tc(sp.t_in, 2)} – {tc(sp.t_out, 2)}</dd>
            <dt>Speaker</dt><dd>{sp.speaker}</dd>
          </dl>
        </div>
        <div className="insp-sec">
          <div className="pn-k">What is said</div>
          <p className="said">
            {b.before.map((w) => <s key={`b${w.i}`} className="w cut">{w.w} </s>)}
            {tw.slice(sp.word_from, sp.word_to + 1).map((w) => <span key={w.i}>{w.w} </span>)}
            {b.after.map((w) => <s key={`a${w.i}`} className="w cut">{w.w} </s>)}
          </p>
          {(b.before.length > 0 || b.after.length > 0) && <p className="mute small">Struck words were cut from Version {v.origin.from}.</p>}
        </div>
        {sp.why && <div className="insp-sec"><div className="pn-k">Why the planner kept it</div><p>{sp.why}</p></div>}
        {(call || sp.callout) && <div className="insp-sec"><div className="pn-k">Callout</div><p><b>{call?.text ?? sp.callout}</b>{call && <span className="mute"> · {tc(call.t_in)} – {tc(call.t_out)}</span>}</p></div>}
        <div className="insp-actions">
          <button className="btn quiet" onClick={() => onAsk(sp.id)} title="Ask Montivo about this clip"><Icon name="spark" /> Ask Montivo</button>
          <button className="btn danger-quiet" disabled={!!deleteBlocked} title={deleteBlocked ?? "Delete clip (⌫)"} onClick={() => onDelete(sp.id)}><Icon name="trash" /> Delete clip</button>
        </div>
        <div className="insp-sec">
          <div className="pn-k">Review notes in this clip</div>
          {!v.review && <p className="mute small">Version {v.n} has not been reviewed.</p>}
          {v.review && !issues.length && <p className="mute small">The reviewer flagged nothing here.</p>}
          <div className="issues">{issues.map((i) => <IssueCard key={i.k} id={id} issue={i} v={v} all={state.versions} onSeek={onSeek} />)}</div>
        </div>
      </div>
    </section>
  );
}

function TitleInspector({ state, v, onClear }: { state: RunState; v: Version; onClear: () => void }) {
  return (
    <section className="inspector">
      <Head title="Title card" sub={`${v.title_s.toFixed(1)} s`} onClear={onClear} />
      <div className="panel-body">
        {state.media.title_png && <img className="title-img" src={fileUrl(state.id, state.media.title_png)} alt="" />}
        <div className="insp-sec"><div className="pn-k">Text</div><p><b>{v.title_card_text}</b></p></div>
        <p className="mute small">Background by Muse Image 1.0, text set by the renderer. It opens the cut for {v.title_s.toFixed(1)} s and fades out.</p>
      </div>
    </section>
  );
}

function PunchInspector({ id, v, p, next, canEdit, onSeek, onPunch, onClear }: {
  id: string; v: Version; p: PunchIn; next: number; canEdit: string | null; onSeek: Seek; onPunch: (a: PunchAsk) => void;
  onClear: () => void;
}) {
  const play = () => onSeek(Math.max(0, p.t_in - 0.6), p.t_out + 0.4);
  const peak = p.kind === "emphasis" ? p.t_in + Math.min(0.3, (p.t_out - p.t_in) / 2) : p.t_in + 0.3;
  return (
    <section className="inspector">
      <Head title="Punch-in" sub={p.kind === "jump_cut" ? "jump cut" : "emphasis"} onClear={onClear} />
      <div className="panel-body">
        <div className="insp-thumb-row">
          <button className="insp-thumb" onClick={play} title="Play the punch-in">
            <img src={frameUrl(id, v.n, peak)} alt="" />
          </button>
          <dl className="props">
            <dt>On</dt><dd>{punchLabel(p)}</dd>
            <dt>In</dt><dd className="mono">{tc(p.t_in, 2)}</dd>
            <dt>Out</dt><dd className="mono">{tc(p.t_out, 2)}</dd>
            <dt>Zoom</dt><dd className="mono">{p.zoom}×</dd>
            {p.excess_db != null && <><dt>Level</dt><dd>{p.excess_db >= 0 ? "+" : ""}{p.excess_db} dB over the cut's median</dd></>}
          </dl>
        </div>
        <p className="mute small">
          {p.kind === "jump_cut"
            ? `Two takes of the same shot meet here, so this one plays ${p.zoom}× closer and the cut reads as a choice.`
            : `A ${p.zoom}× zoom about your head on a word you lean on (or a name such as Montivo). Captions never zoom.`}
        </p>
        <div className="insp-actions">
          <button className="btn danger-quiet" disabled={!!canEdit} title={canEdit ?? `Makes Version ${next} without it; later versions, the review loop and re-plans keep it out`}
            onClick={() => onPunch({ skip: p.key })}>
            <Icon name="trash" /> Remove punch-in <span className="btn-cost">$0</span>
          </button>
        </div>
      </div>
    </section>
  );
}

function PunchToggle({ v, next, canEdit, onPunch }: { v: Version; next: number; canEdit: string | null; onPunch: (a: PunchAsk) => void }) {
  const n = v.punch_ins.length, gone = v.punch_skip.length;
  const what = v.punch ? `${n} in this version${gone ? ` · ${gone} removed by hand` : ""}` : "Zooms on stressed words and jump cuts";
  return (
    <div className="punch-row">
      <div className="pr-text">
        <span className="pr-title">Punch-ins</span>
        <span className="mute small">{what}</span>
      </div>
      <button className={`switch ${v.punch ? "on" : ""}`} role="switch" aria-checked={v.punch} aria-label="Punch-ins" disabled={!!canEdit}
        title={canEdit ?? `Turn the punch-ins ${v.punch ? "off" : "on"}: makes Version ${next} ($0, a re-render)`}
        onClick={() => onPunch({ on: !v.punch })}>
        <span className="knob" />
      </button>
    </div>
  );
}

function CutReview({ id, state, versions, v, next, canEdit, onSeek, onPick, onReview, onPunch }: {
  id: string; state: RunState; versions: Version[]; v: Version; next: number; canEdit: string | null; onSeek: Seek;
  onPick: (n: number) => void; onReview: () => void; onPunch: (a: PunchAsk) => void;
}) {
  const reviewed = versions.filter((x) => x.review);
  const r = v.review;
  const parent = parentOf(v, state.versions);
  const prevScores = parent?.review?.scores;
  return (
    <section className="inspector">
      <div className="panel-head">
        <span className="ph-title">Review</span>
        <span className="badge" title="The reviewer gets the rendered video, the brief, the rubric and three measured facts. Never the plan or the edit list.">blind</span>
        <span className="grow" />
        <span className="ph-sub">Version {v.n}</span>
      </div>
      <div className="panel-body">
        <div className="vsum">
          <span className={`origin ${v.origin.source === "plan" ? "plan" : v.origin.source === "review" ? "review" : "human"}`}>{originLabel(v)}</span>
          <span>{v.spans.length} clips · {tc(v.duration_s ?? v.runtime_s)}{parent?.duration_s && v.duration_s ? ` (${v.duration_s - parent.duration_s >= 0 ? "+" : ""}${(v.duration_s - parent.duration_s).toFixed(1)} s)` : ""}</span>
          {v.loudness && <span title={`true peak ${v.loudness.true_peak_dbtp} dBTP`}>{v.loudness.integrated_lufs} LUFS</span>}
        </div>
        {v.cut && <PunchToggle v={v} next={next} canEdit={canEdit} onPunch={onPunch} />}
        {reviewed.length > 0 && (
          <div className="history" title="The same cut reviewed twice can differ by about 10 points.">
            {reviewed.map((x, k) => (
              <Fragment key={x.n}>
                {k > 0 && <span className="arrow">→</span>}
                <button className={`score ${x.n === v.n ? "on" : ""} ${x.review!.pass ? "pass" : ""}`} onClick={() => onPick(x.n)} title={`Version ${x.n}`}>
                  <span className="sv">v{x.n}</span><span className="sn">{x.review!.overall}</span>
                </button>
              </Fragment>
            ))}
            {state.loop?.stop && <span className="stop" title={state.loop.stop}>{state.loop.stop.split(":")[0] === "pass" ? "passed" : `stopped: ${state.loop.stop.split(":")[0]}`}</span>}
          </div>
        )}
        {!r && (
          <div className="insp-empty-card">
            <Icon name="spark" size={22} />
            <div className="iec-title">Version {v.n} has not been reviewed yet</div>
            <p>The blind reviewer watches the rendered video, scores it against the rubric and proposes fixes; Montivo applies them and renders again.</p>
            {canEdit ? <span className="mute small">{canEdit}.</span>
              : <button className="btn" disabled={!state.has_key} onClick={onReview} title={state.has_key ? "" : "needs MODEL_API_KEY"}>Review this version <span className="btn-cost">~{usd(state.costs.review)}</span></button>}
          </div>
        )}
        {r && (
          <>
            <div className="scores">
              {Object.entries(r.scores).map(([k, s]) => {
                const d = prevScores?.[k] != null ? s - prevScores[k] : 0;
                return (
                  <div key={k} className="bar-row">
                    <span className="bl">{crit(k)}</span>
                    <span className="bar"><span className={`fill ${s <= 4 ? "low" : s <= 7 ? "mid" : ""}`} style={{ width: `${s * 10}%` }} /></span>
                    <span className="bn">{s}{d ? <i className={d > 0 ? "up" : "down"}>{d > 0 ? `+${d}` : d}</i> : null}</span>
                  </div>
                );
              })}
            </div>
            <p className="summary">{r.summary}</p>
            <div className="facts" title={r.facts_text}>
              <span className="fact">silent gaps <b>{r.facts.silences}</b></span>
              <span className="fact">captions over the face <b>{r.facts.over_face}</b></span>
              <span className="fact">loud cuts <b>{r.facts.loud_edges}</b> of {r.facts.cut_edges}</span>
            </div>
            <div className="pn-k">Notes</div>
            <div className="issues">
              {[...r.issues].sort((a, b) => sevRank(a.severity) - sevRank(b.severity) || a.t_s - b.t_s).map((i) => (
                <IssueCard key={i.k} id={id} issue={i} v={v} all={state.versions} onSeek={onSeek} />
              ))}
              {!r.issues.length && <p className="mute small">No notes: {r.pass ? "the cut passes." : "nothing flagged."}</p>}
            </div>
            <div className="rv-cost mute">review {usd(r.cost_usd, 3)} · {r.seconds?.toFixed(0)} s · watched {r.duration_s?.toFixed(1)} s</div>
          </>
        )}
      </div>
    </section>
  );
}

export function IssueCard({ id, issue, v, all, onSeek }: { id: string; issue: Issue; v: Version; all: Version[]; onSeek: Seek }) {
  const c = chain(issue, v, all);
  const fixOp = c.planned ? `${c.planned.op} ${c.planned.target}` : issue.fix_op && issue.fix_op !== "none" ? issue.fix_op : null;
  const resolved = c.applied && c.after != null && c.before != null && c.after > c.before;
  const loop = () => onSeek(Math.max(0, issue.t_s - 1), issue.t_end_s + 0.5);
  return (
    <div className={`issue ${issue.severity}`}>
      <div className="i-top">
        <button className="time" onClick={loop} title="Play this moment in a loop">{tc(issue.t_s)}</button>
        <span className={`sev ${issue.severity}`}>{issue.severity}</span>
        <span className="crit">{crit(issue.criterion)}</span>
        {resolved && <span className="resolved"><Icon name="check" size={12} /> fixed in v{c.next!.n}</span>}
      </div>
      <div className="i-body">
        <button className="frame" onClick={loop} title="The frame the reviewer saw (its clock is burned in)">
          <img src={frameUrl(id, v.n, issue.t_s + 0.1)} alt={`Version ${v.n} at ${tc(issue.t_s)}`} loading="lazy" />
        </button>
        <ol className="steps">
          <li><span className="k">saw</span><span>{issue.what}</span></li>
          <li><span className="k">fix</span>{fixOp ? <code>{fixOp}</code> : <span className="mute">none proposed</span>}</li>
          <li><span className="k">patch</span>{c.applied ? <span>{c.applied.note}</span>
            : c.skipped ? <span className="mute">skipped: {c.skipped.reason}</span>
            : c.planned ? <span className="mute">planned, not applied (the loop stopped)</span> : <span className="mute">–</span>}</li>
          <li><span className="k">result</span>{c.next && c.after != null ? <span>{crit(issue.criterion)} {c.before} → <b>{c.after}</b> in v{c.next.n}</span> : <span className="mute">–</span>}</li>
        </ol>
      </div>
    </div>
  );
}
