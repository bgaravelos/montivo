import { useEffect, useState } from "react";
import { api, fileUrl, tc, usd } from "./api";
import { originKind, originLabel, parentOf } from "./edit";
import { Icon } from "./icons";
import type { CaptionLine, Costs, HeadsInfo, Planner, RunState, Version } from "./types";

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
export function CaptionsPanel({ v, t, next, canEdit, onSeek, onApply, onAsk }: {
  v: Version | null; t: number; next: number; canEdit: string | null; onSeek: (a: number) => void;
  onApply: (edits: CaptionEdit[]) => void;
  onAsk?: (line: CaptionLine) => void; // the line goes to the chat as "this" (UX B3)
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
                <dt>Position</dt><dd>{v?.look?.position === "fixed" ? `fixed at y ${v.look.top} (your choice)` : `from y ${st.default_top}, moves off the face`}</dd>
              </dl>
              {v?.caption_style_note && <p className="mute small">Planner's note: {v.caption_style_note}</p>}
              <p className="mute small">Preset{v?.look ? ` ${v.look.preset.replace("_", " ")}` : ""}. Fonts, colours and position: the Style panel in the rail.</p>
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
                {onAsk && <button className="icon-btn lr-edit" title="Ask Montivo about this line"
                  onClick={(e) => { e.stopPropagation(); onAsk(c); }}><Icon name="assistant" size={13} /></button>}
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

/** A text edit for montivo.human text: callouts by span ("" removes one), new title words, the card on or off. */
export type TextEdit = { callouts?: Record<string, string>; title?: string; title_card?: boolean };

export function TextPanel({ state, v, t, next, canEdit, onSeek, onApply }: {
  state: RunState; v: Version | null; t: number; next: number; canEdit: string | null; onSeek: (a: number) => void;
  onApply: (e: TextEdit) => void;
}) {
  const calls = v?.callouts ?? [];
  const [staged, setStaged] = useState<Record<string, string>>({}); // span id -> callout text ("" = removed)
  const [title, setTitle] = useState<string | null>(null);
  const [card, setCard] = useState<boolean | null>(null);
  const [editing, setEditing] = useState<string | null>(null); // a span id or "title"
  const [draft, setDraft] = useState("");
  useEffect(() => { setStaged({}); setTitle(null); setCard(null); setEditing(null); }, [v?.n]);
  if (!v) return <section className="side-panel"><div className="panel-head"><span className="ph-title">Text</span></div></section>;
  const cardOn = v.title_card !== false;
  const callout = (sid: string) => (staged[sid] ?? v.spans.find((x) => x.id === sid)?.callout ?? "").trim();
  const open = (key: string, text: string) => { if (canEdit) return; setEditing(key); setDraft(text); };
  const clean = (x: string) => x.split(/\s+/).filter(Boolean).join(" ");
  const commit = () => {
    if (editing === "title") {
      const x = clean(draft);
      setTitle(x && x !== v.title_card_text ? x : null);
    } else if (editing) {
      const x = clean(draft), was = (v.spans.find((s) => s.id === editing)?.callout ?? "").trim();
      setStaged((s) => { const { [editing]: _, ...rest } = s; return x !== was ? { ...rest, [editing]: x } : rest; });
    }
    setEditing(null);
  };
  const n = Object.keys(staged).length + (title != null ? 1 : 0) + (card != null ? 1 : 0);
  const needsKey = title != null && !state.has_key;
  const apply = () => onApply({ ...(Object.keys(staged).length ? { callouts: staged } : {}), ...(title != null ? { title } : {}), ...(card != null ? { title_card: card } : {}) });
  const editor = (label: string) => (
    <input autoFocus value={draft} maxLength={80} aria-label={label} onFocus={(e) => e.currentTarget.select()}
      onChange={(e) => setDraft(e.target.value)} onBlur={commit}
      onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setEditing(null); } }} />
  );
  return (
    <section className="side-panel">
      <div className="panel-head"><span className="ph-title">Text</span><span className="ph-sub">title and callouts</span></div>
      <div className="panel-body">
        <div className="pn-k">Title card</div>
        {v.title_card_text ? (
          <div className={`title-card ${(card ?? cardOn) ? "" : "off"} ${title != null || card != null ? "staged" : ""}`}>
            <button className="tc-thumb" onClick={() => v.title_s > 0 && onSeek(0)} title="Play the title card">
              {state.media.title_png && <img src={fileUrl(state.id, state.media.title_png)} alt="" />}
            </button>
            {editing === "title" ? editor("Title card words") : (
              <span className="tc-text" onDoubleClick={() => open("title", title ?? v.title_card_text ?? "")}>{title ?? v.title_card_text}</span>
            )}
            {title != null && <span className="lr-was">was: {v.title_card_text} · new words make a new Muse Image card ($0.01)</span>}
            <span className="tc-meta">{v.title_s > 0 ? `0:00 – ${tc(v.title_s)} · Muse Image background` : cardOn ? "on" : "off in this version"}</span>
            <span className="tc-actions">
              <button className="btn quiet small" disabled={!!canEdit} title={canEdit ?? "New words for the card (Muse Image draws them, $0.01)"}
                onClick={() => open("title", title ?? v.title_card_text ?? "")}><Icon name="pencil" size={12} /> Words</button>
              <button className="btn quiet small" disabled={!!canEdit} title={canEdit ?? "The card in front of the cut, on or off ($0)"}
                onClick={() => setCard((c) => { const want = !(c ?? cardOn); return want === cardOn ? null : want; })}>
                {(card ?? cardOn) ? "Turn off" : "Turn on"}</button>
            </span>
          </div>
        ) : <p className="mute small">No title card.</p>}
        <div className="pn-k">Callouts</div>
        <p className="mute small">One callout per clip at most, anchored to the speaker. Double-click to rewrite; empty removes it.</p>
        <div className="list">
          {v.spans.map((sp) => {
            const c = calls.find((x) => x.span === sp.id);
            const text = callout(sp.id);
            const was = (sp.callout ?? "").trim();
            if (!text && !was && !(sp.id in staged) && editing !== sp.id) return null;
            return editing === sp.id ? (
              <div key={sp.id} className="list-row cap-edit"><span className="lr-t mono">{sp.id}</span>{editor(`Callout of ${sp.id}`)}</div>
            ) : (
              <div key={sp.id} role="button" tabIndex={0} className={`list-row cap-row ${c && t >= c.t_in && t < c.t_out ? "on" : ""} ${sp.id in staged ? "staged" : ""}`}
                onClick={() => c && onSeek(c.t_in)} onDoubleClick={() => open(sp.id, text)} onKeyDown={(e) => { if (e.key === "Enter") open(sp.id, text); }}>
                <span className="lr-t mono">{c ? tc(c.t_in) : sp.id}</span>
                <span className="lr-txt">{text ? <b>{text}</b> : <i className="mute">removed</i>}
                  {sp.id in staged && <span className="lr-was">was: {was || "no callout"}</span>}</span>
                <span className="lr-end">
                  <span className="lr-flag">clip {sp.id}</span>
                  <button className="icon-btn lr-edit" disabled={!!canEdit} title={canEdit ?? "Rewrite this callout"}
                    onClick={(e) => { e.stopPropagation(); open(sp.id, text); }}><Icon name="pencil" size={13} /></button>
                </span>
              </div>
            );
          })}
          {!v.spans.some((sp) => (sp.callout ?? "").trim() || sp.id in staged) && <p className="mute small pad">No callouts in this version.</p>}
        </div>
        <label className="field add-callout">Add a callout to
          <select value="" disabled={!!canEdit} onChange={(e) => { const sid = e.target.value; if (sid) open(sid, ""); }}>
            <option value="">a clip…</option>
            {v.spans.filter((sp) => !callout(sp.id)).map((sp) => <option key={sp.id} value={sp.id}>{sp.id} · {sp.words.slice(0, 40)}</option>)}
          </select>
        </label>
      </div>
      {n > 0 && (
        <div className="cap-apply">
          <span>{n} change{n > 1 ? "s" : ""}</span>
          <span className="grow" />
          <button className="btn quiet" onClick={() => { setStaged({}); setTitle(null); setCard(null); }}>Discard</button>
          <button className="btn" disabled={!!canEdit || needsKey}
            title={canEdit ?? (needsKey ? "New title words call Muse Image: set MODEL_API_KEY" : `Makes Version ${next} (${title != null ? "$0.01 for the new card" : "$0"}, a re-render)`)}
            onClick={apply}>Make Version {next}</button>
        </div>
      )}
    </section>
  );
}

export function VersionsPanel({ id, state, versions, sel, onPick, go, replay, opening, canEdit, onRestore }: {
  id: string; state: RunState; versions: Version[]; sel: number | null; onPick: (n: number) => void; go: (h: string) => void; replay: number;
  canEdit: string | null; onRestore: (n: number) => void;
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
        {v && v.cut && v.n !== versions.at(-1)?.n && (
          <button className="btn wide" disabled={!!canEdit} onClick={() => onRestore(v.n)}
            title={canEdit ?? `Version ${v.n} comes back as the newest version, rendered again; the versions after it stay`}>
            <Icon name="undo" /> Restore Version {v.n} <span className="btn-cost">$0</span>
          </button>
        )}
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

/** Who the crop follows: every Voice speaker with the head the speaker map gave it (or a human pin), and every SAM
 *  head to pin instead. One pin makes the next version (montivo.human pin), $0. */
export function SpeakersPanel({ id, v, next, canEdit, onApply }: {
  id: string; v: Version | null; next: number; canEdit: string | null; onApply: (speaker: string, head: string | null) => void;
}) {
  const [info, setInfo] = useState<HeadsInfo | null>(null);
  const [staged, setStaged] = useState<{ speaker: string; head: string | null } | null>(null);
  useEffect(() => { api<HeadsInfo>(`/api/runs/${id}/heads`).then(setInfo).catch(() => setInfo({ speakers: [], heads: [], video: null })); }, [id]);
  useEffect(() => { setStaged(null); }, [v?.n]);
  const pins = v?.speaker_pins ?? {};
  const thumb = (h: string) => `/api/runs/${id}/head/${h}.jpg`;
  const follows = (spk: string) => (pins[spk] ? [pins[spk]] : info?.speakers.find((s) => s.id === spk)?.ids ?? []);
  return (
    <section className="side-panel">
      <div className="panel-head"><span className="ph-title">Speakers</span><span className="ph-sub">{info ? `${info.speakers.length} voices · ${info.heads.length} heads` : ""}</span></div>
      <div className="panel-body">
        {!info && <p className="mute small">Loading…</p>}
        {info && (info.speakers.length < 2 || !info.heads.length) && (
          <p className="mute small">{!info.heads.length ? "No head track in this run: the crop follows the person." : "One voice: the crop follows the head SAM tracked, nothing to pin."}</p>
        )}
        {info && info.speakers.length >= 2 && info.heads.length > 0 && <>
          <p className="mute small">When a speaker talks, the 9:16 crop follows their head. The speaker map picked one per voice; pin another head where it got it wrong.</p>
          {info.speakers.map((s) => {
            const cur = follows(s.id);
            const st = staged?.speaker === s.id ? staged : null;
            return (
              <div key={s.id} className={`spk-card ${st ? "staged" : ""}`}>
                <div className="spk-top">
                  <span className="spk-id">{s.id}</span>
                  <span className="spk-who" title="Watch's description of who speaks; it can name the wrong person (run 014): trust the faces">
                    {s.who ? <>watch's guess: {s.who}</> : "unnamed"}</span>
                  <span className="mute small">{s.words} words</span>
                </div>
                <div className="spk-now mute small">
                  {pins[s.id] ? <>pinned to head {pins[s.id]} by you</> : cur.length ? <>speaker map: head {cur.join(", ")} ({s.method ?? "map"})</> : "no head mapped"}
                </div>
                <div className="spk-heads">
                  {info.heads.map((h) => {
                    const on = st ? st.head === h.id : cur.includes(h.id);
                    return (
                      <button key={h.id} className={`head ${on ? "on" : ""}`} disabled={!!canEdit}
                        title={canEdit ?? `Head ${h.id}${h.speaker ? ` (mapped to ${h.speaker})` : ""}: follow it when ${s.id} talks`}
                        onClick={() => setStaged(cur.includes(h.id) && !pins[s.id] ? null : pins[s.id] === h.id ? null : { speaker: s.id, head: h.id })}>
                        <img src={thumb(h.id)} alt="" loading="lazy" />
                        <span>{h.id}</span>
                      </button>
                    );
                  })}
                </div>
                {pins[s.id] && !st && (
                  <button className="link small" disabled={!!canEdit} onClick={() => setStaged({ speaker: s.id, head: null })}>Back to the speaker map</button>
                )}
              </div>
            );
          })}
        </>}
      </div>
      {staged && (
        <div className="cap-apply">
          <span>{staged.head ? `${staged.speaker} → head ${staged.head}` : `${staged.speaker} → speaker map`}</span>
          <span className="grow" />
          <button className="btn quiet" onClick={() => setStaged(null)}>Discard</button>
          <button className="btn" disabled={!!canEdit} title={canEdit ?? `Makes Version ${next} with the crop on that head ($0, a re-render)`}
            onClick={() => onApply(staged.speaker, staged.head)}>Make Version {next}</button>
        </div>
      )}
    </section>
  );
}
