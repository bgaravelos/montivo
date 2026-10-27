import { useEffect, useState } from "react";
import { fileUrl, tc, usd } from "./api";
import { originKind, originLabel, parentOf } from "./edit";
import { Icon } from "./icons";
import type { Costs, Planner, RunState, Version } from "./types";

const mb = (b: number | null | undefined) => (b == null ? "–" : b > 1e9 ? `${(b / 1e9).toFixed(2)} GB` : `${(b / 1e6).toFixed(1)} MB`);

/** Which model writes the paper edit: Muse Spark 1.3, or Muse Spark Open (open weights, served by Baseten). The reviews
 *  always run on Spark 1.3, since they watch the video. */
export function PlannerPicker({ value, onChange, hasOpen, costs, hint }: {
  value: Planner; onChange: (p: Planner) => void; hasOpen: boolean; costs: Costs; hint?: boolean;
}) {
  const open = value === "open";
  return (
    <div className="planner-pick">
      <div className="seg-group" role="radiogroup" aria-label="Planner">
        <button className={`seg ${!open ? "on" : ""}`} role="radio" aria-checked={!open} onClick={() => onChange("spark")}
          title={`Muse Spark 1.3 plans the edit: about ${usd(costs.plan, 3)} and ${costs.plan_s} s`}>Spark 1.3</button>
        <button className={`seg ${open ? "on" : ""}`} role="radio" aria-checked={open} disabled={!hasOpen} onClick={() => onChange("open")}
          title={hasOpen ? `Muse Spark Open (open weights, Baseten) plans the edit: about ${usd(costs.plan_open, 3)} and ${costs.plan_open_s} s`
            : "Muse Spark Open plans on Baseten: set BASETEN_API_KEY and restart montivo serve"}>
          Spark Open <span className="open-tag">open weights</span>
        </button>
      </div>
      {hint && (
        <span className="mute small">
          {open ? `Muse Spark Open on Baseten: about ${usd(costs.plan_open, 3)} and ${costs.plan_open_s} s for the plan`
            : `Muse Spark 1.3: about ${usd(costs.plan, 3)} and ${costs.plan_s} s for the plan`}; the blind review stays on Spark 1.3
        </span>
      )}
    </div>
  );
}

export function MediaPanel({ state }: { state: RunState }) {
  const m = state.media;
  const name = m.footage?.split("/").pop() ?? "source";
  return (
    <section className="side-panel">
      <div className="panel-head"><span className="ph-title">Media</span><span className="ph-sub">1 clip</span></div>
      <div className="panel-body">
        <div className="media-card">
          {m.proxy ? <video src={fileUrl(state.id, m.proxy)} controls muted preload="metadata" /> : <div className="media-ph">no preview</div>}
          <div className="mc-name" title={m.footage ?? ""}>{name}</div>
          <dl className="props">
            <dt>Duration</dt><dd className="mono">{tc(m.duration)}</dd>
            <dt>Frame</dt><dd>{m.width && m.height ? `${m.width} × ${m.height}` : "–"}{m.fps ? ` · ${Math.round(m.fps)} fps` : ""}</dd>
            <dt>Codec</dt><dd>{m.codec ?? "–"}</dd>
            <dt>Size</dt><dd>{mb(m.bytes)}</dd>
            {m.hflip && <><dt>Mirror</dt><dd>flipped horizontally</dd></>}
            <dt>Transcript</dt><dd>{m.words ? `${m.words} words · ${m.speakers} speaker${m.speakers === 1 ? "" : "s"}` : "–"}</dd>
          </dl>
        </div>
        <div className="panel-note">
          <div className="pn-k">Brief</div>
          <p>{state.run.brief}</p>
          <div className="pn-k">Target</div>
          <p>{state.run.target_s ?? 40} s, vertical 9:16</p>
        </div>
      </div>
    </section>
  );
}

export type CaptionEdit = { at: number; text: string };

/** The caption lines of a version; each can be retexted where Voice misheard a word. Edits are staged here and applied
 *  together as the next version (the reviewer's caption_text patch, $0, a re-render), so four fixes are one render. */
export function CaptionsPanel({ v, t, next, canEdit, onSeek, onApply }: {
  v: Version | null; t: number; next: number; canEdit: string | null; onSeek: (a: number) => void;
  onApply: (edits: CaptionEdit[]) => void;
}) {
  const lines = v?.caption_lines ?? [];
  const st = v?.caption_style;
  const font = st?.font_path?.split("/").pop()?.replace(/\.(ttf|otf)$/, "").replace(/-/g, " ");
  const [staged, setStaged] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  useEffect(() => { setStaged({}); setEditing(null); }, [v?.n]);
  const n = Object.keys(staged).length;
  const open = (id: string, text: string) => { if (canEdit) return; setEditing(id); setDraft(staged[id] ?? text); };
  const commit = (id: string, text: string) => {
    const clean = draft.split(/\s+/).filter(Boolean).join(" ");
    setStaged((s) => {
      const { [id]: _, ...rest } = s;
      return clean && clean !== text ? { ...rest, [id]: clean } : rest;
    });
    setEditing(null);
  };
  const apply = () => {
    const edits = lines.filter((c) => staged[c.id]).map((c) => ({ at: (c.t_in + c.t_out) / 2, text: staged[c.id] }));
    if (edits.length) onApply(edits);
  };
  return (
    <section className="side-panel">
      <div className="panel-head"><span className="ph-title">Captions</span><span className="ph-sub">{lines.length} lines</span></div>
      <div className="panel-body">
        <div className="style-card">
          <div className="pn-k">Style</div>
          {st ? (
            <>
              <div className="cap-sample" style={{ fontSize: Math.min(26, (st.font ?? 84) / 3.4) }}>Captions look like this</div>
              <dl className="props">
                <dt>Font</dt><dd>{font ?? "–"} · {st.font ?? "–"} px</dd>
                <dt>Line</dt><dd>up to {st.max_words ?? 4} words</dd>
                <dt>Position</dt><dd>lower third (y {st.default_top}), moves off the face</dd>
              </dl>
              {v?.caption_style_note && <p className="mute small">Planner's note: {v.caption_style_note}</p>}
              <p className="mute small">Style presets are not editable yet: the renderer has one caption style.</p>
            </>
          ) : <p className="mute small">This version was rendered without captions.</p>}
        </div>
        {lines.length > 0 && <p className="mute small">Double-click a line (or its pencil) to fix a word Voice misheard; the timing stays.</p>}
        <div className="list">
          {lines.map((c) => editing === c.id ? (
            <div key={c.id} className="list-row cap-edit">
              <span className="lr-t mono">{tc(c.t_in)}</span>
              <input autoFocus value={draft} maxLength={200} aria-label={`Caption at ${tc(c.t_in)}`} onFocus={(e) => e.currentTarget.select()}
                onChange={(e) => setDraft(e.target.value)} onBlur={() => commit(c.id, c.text)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") { e.preventDefault(); commit(c.id, c.text); }
                  else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setEditing(null); }
                }} />
            </div>
          ) : (
            <div key={c.id} role="button" tabIndex={0} className={`list-row cap-row ${t >= c.t_in && t < c.t_out ? "on" : ""} ${staged[c.id] ? "staged" : ""}`}
              onClick={() => onSeek(c.t_in)} onDoubleClick={() => open(c.id, c.text)}
              onKeyDown={(e) => { if (e.key === "Enter") open(c.id, c.text); }}>
              <span className="lr-t mono">{tc(c.t_in)}</span>
              <span className="lr-txt">
                {staged[c.id] ?? c.text}
                {staged[c.id] ? <span className="lr-was">was: {c.text}</span>
                  : c.heard && c.heard !== c.text ? <span className="lr-was" title="The words Voice heard; the line was corrected">Voice: {c.heard}</span> : null}
              </span>
              <span className="lr-end">
                {staged[c.id] ? <span className="lr-flag edited">edited</span> : c.over_face && <span className="lr-flag" title="moved off the face">moved</span>}
                <button className="icon-btn lr-edit" disabled={!!canEdit} title={canEdit ?? "Edit this line"}
                  onClick={(e) => { e.stopPropagation(); open(c.id, c.text); }}><Icon name="pencil" size={13} /></button>
              </span>
            </div>
          ))}
          {!lines.length && <p className="mute small pad">No caption lines in this version.</p>}
        </div>
      </div>
      {n > 0 && (
        <div className="cap-apply">
          <span>{n} line{n > 1 ? "s" : ""} edited</span>
          <span className="grow" />
          <button className="btn quiet" onClick={() => setStaged({})}>Discard</button>
          <button className="btn" disabled={!!canEdit} title={canEdit ?? `Makes Version ${next} with the new text ($0, a re-render)`} onClick={apply}>
            Make Version {next}
          </button>
        </div>
      )}
    </section>
  );
}

export function TextPanel({ state, v, t, onSeek }: { state: RunState; v: Version | null; t: number; onSeek: (a: number) => void }) {
  const calls = v?.callouts ?? [];
  return (
    <section className="side-panel">
      <div className="panel-head"><span className="ph-title">Text</span><span className="ph-sub">title and callouts</span></div>
      <div className="panel-body">
        <div className="pn-k">Title card</div>
        {v?.title_card_text ? (
          <button className={`title-card ${v.title_s > 0 ? "" : "off"}`} onClick={() => v.title_s > 0 && onSeek(0)}>
            {state.media.title_png && <img src={fileUrl(state.id, state.media.title_png)} alt="" />}
            <span className="tc-text">{v.title_card_text}</span>
            <span className="tc-meta">{v.title_s > 0 ? `0:00 – ${tc(v.title_s)} · Muse Image background` : "off in this version (the reviewer dropped it)"}</span>
          </button>
        ) : <p className="mute small">No title card.</p>}
        <div className="pn-k">Callouts</div>
        <div className="list">
          {calls.map((c, k) => (
            <button key={k} className={`list-row ${t >= c.t_in && t < c.t_out ? "on" : ""}`} onClick={() => onSeek(c.t_in)}>
              <span className="lr-t mono">{tc(c.t_in)}</span>
              <span className="lr-txt"><b>{c.text}</b></span>
              <span className="lr-flag">clip {c.span}</span>
            </button>
          ))}
          {!calls.length && <p className="mute small">No callouts in this version.</p>}
        </div>
      </div>
    </section>
  );
}

export function VersionsPanel({ id, state, versions, sel, onPick, go, replay, opening }: {
  id: string; state: RunState; versions: Version[]; sel: number | null; onPick: (n: number) => void; go: (h: string) => void; replay: number;
  opening: number | null; // the version the editor opens on (a human edit or the hook A/B's pick, else the loop's best)
}) {
  const v = versions.find((x) => x.n === sel);
  const parent = v ? parentOf(v, state.versions) : undefined;
  const pair = v && versions.length > 1 ? (parent && versions.some((x) => x.n === parent.n) ? [parent.n, v.n] : [v.n, versions.find((x) => x.n !== v.n)!.n]) : null;
  const best = state.loop?.best?.version;
  return (
    <section className="side-panel">
      <div className="panel-head"><span className="ph-title">Versions</span><span className="ph-sub">{versions.length}</span></div>
      <div className="panel-body">
        <div className="versions">
          {[...versions].reverse().map((x) => {
            const p = parentOf(x, state.versions);
            return (
              <button key={x.n} className={`ver ${x.n === sel ? "on" : ""} ${originKind(x)}`} onClick={() => onPick(x.n)}>
                <span className="ver-top">
                  <span className="ver-name">Version {x.n}</span>
                  {x.n === opening && <span className="ver-kept" title="The project opens here: the newest version a human edit made or the hook A/B kept, else the review loop's best">opens here</span>}
                  {x.n === best && <span className="ver-best" title="The review loop's highest blind score">loop best</span>}
                  <span className="grow" />
                  {x.review ? <span className={`ver-score ${x.review.pass ? "pass" : ""}`}>{x.review.pass && <Icon name="check" size={12} />}{x.review.overall}</span>
                    : <span className="ver-score none" title="not reviewed">–</span>}
                </span>
                <span className="ver-meta">
                  <span className={`origin ${originKind(x)}`}>{originLabel(x)}</span>
                  {p && <span className="mute">from Version {p.n}</span>}
                </span>
                <span className="ver-meta mute">{x.spans.length} clips · {tc(x.duration_s ?? x.runtime_s)}{x.planner ? ` · ${x.planner.name}` : ""}</span>
              </button>
            );
          })}
        </div>
        {versions.length > 1 && (
          <button className="btn quiet wide" onClick={() => go(`#/run/${id}/loop${replay ? `?replay=${replay}` : ""}`)}>
            <Icon name="loop" /> Watch the agent loop
          </button>
        )}
        {pair && (
          <button className="btn quiet wide" onClick={() => go(`#/run/${id}/compare?a=${pair[0]}&b=${pair[1]}${replay ? `&replay=${replay}` : ""}`)}>
            <Icon name="compare" /> Compare Version {pair[0]} and {pair[1]}
          </button>
        )}
      </div>
    </section>
  );
}
