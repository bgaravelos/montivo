import { useEffect, useRef, useState } from "react";
import type { PanelFocus } from "./Editor";
import { api, fileUrl, tc, usd } from "./api";
import { originKind, originLabel, parentOf } from "./edit";
import { Icon, MarkLoader } from "./icons";
import type { CaptionLine, Costs, HeadsInfo, Planner, RunState, Version } from "./types";

/** A3: in Live a panel's Apply goes into the draft with the timeline's edits. */
export const LIVE_TIP = "Into the draft: it plays at once; Make Version in the top bar renders every draft edit once ($0)";

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

/** Lowercase, punctuation off: how the glossary matches words (captions.norm). */
const normTok = (w: string) => w.toLowerCase().replace(/[^\p{L}\p{N}']+/gu, "");
const toks = (s: string) => s.split(/\s+/).filter(Boolean);
const bare = (s: string) => s.replace(/[.,!?;:]+$/, "");

/** C2: the one run of words a line edit changed ("meta meta" -> "Four Meta"), as a glossary entry, or null when the edit
 *  changes more than one run (or only punctuation). */
export function phraseFix(was: string, now: string): { heard: string; written: string } | null {
  const a = toks(was), b = toks(now);
  let p = 0;
  while (p < a.length && p < b.length && normTok(a[p]) === normTok(b[p])) p++;
  let q = 0;
  while (q < a.length - p && q < b.length - p && normTok(a[a.length - 1 - q]) === normTok(b[b.length - 1 - q])) q++;
  const heard = bare(a.slice(p, a.length - q).join(" ")), written = bare(b.slice(p, b.length - q).join(" "));
  return heard && written && normTok(heard) !== normTok(written) ? { heard, written } : null;
}

/** The lines that say these words (as the glossary would match them). */
export function linesSaying(lines: CaptionLine[], words: string): CaptionLine[] {
  const want = toks(words).map(normTok).filter(Boolean);
  if (!want.length) return [];
  return lines.filter((c) => {
    const have = toks(c.text).map(normTok);
    return have.some((_, i) => want.every((w, k) => have[i + k] === w));
  });
}

/** C3: why a line wants a look: the reviewer said its words do not match the speech, or the words Voice heard in the
 *  cut differ from it. */
function toCheck(v: Version | null, c: CaptionLine): string[] {
  const r = v?.review;
  if (!r) return [];
  const out = r.issues.filter((i) => i.criterion === "captions_match_speech" && i.t_s >= c.t_in - 0.25 && i.t_s < c.t_out + 0.25)
    .map((i) => `reviewer: ${i.what}`);
  for (const d of r.heard_diff ?? []) if (d.t >= c.t_in - 0.05 && d.t < c.t_out) out.push(`Voice heard “${d.heard || "nothing"}” for “${d.caption}”`);
  return out;
}

/** The caption lines of a version: retext a line where Voice misheard a word (here, or everywhere it says it: the run's
 *  glossary), find and replace, split or join lines, and the lines worth a second look. Edits are staged here and
 *  applied together (the reviewer's caption_text patch, $0); in Live they go into the draft. */
export function CaptionsPanel({ v, t, next, canEdit, onSeek, onApply, onAsk, focus, rev, live, onStyle, onGlossary, onBreak }: {
  v: Version | null; t: number; next: number; canEdit: string | null; onSeek: (a: number) => void; focus?: PanelFocus | null;
  rev: string; live: boolean; // what the staged lines belong to (a version, or the draft as it stands); Live: Apply goes to the draft
  onApply: (edits: CaptionEdit[]) => void;
  onAsk?: (line: CaptionLine) => void; // the line goes to the chat as "this" (UX B3)
  onStyle: () => void; // C1: the Style panel
  onGlossary: (add: Record<string, string>, drop?: string[]) => void; // C2: correct everywhere
  onBreak: (at: number, join: boolean) => void; // C4: a new line at a word, or a line back on the one before
}) {
  const lines = v?.caption_lines ?? [];
  const st = v?.caption_style;
  const font = st?.font_path?.split("/").pop()?.replace(/\.(ttf|otf)$/, "").replace(/-/g, " ");
  const [staged, setStaged] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [find, setFind] = useState<{ find: string; replace: string } | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [follow, setFollowState] = useState(() => { try { return localStorage.getItem("montivo.captions.follow") !== "off"; } catch { return true; } });
  const setFollow = (on: boolean) => { setFollowState(on); try { localStorage.setItem("montivo.captions.follow", on ? "on" : "off"); } catch { /* this tab */ } };
  useEffect(() => { setStaged({}); setEditing(null); }, [rev]);
  const n = Object.keys(staged).length;
  const open = (id: string, text: string) => { if (canEdit) return; setEditing(id); setDraft(staged[id] ?? text); };
  const body = useRef<HTMLDivElement>(null);
  const show = (id: string) => window.requestAnimationFrame(() =>
    body.current?.querySelector(`[data-line="${CSS.escape(id)}"]`)?.scrollIntoView({ block: "nearest" }));
  useEffect(() => { // A2: the line picked on the timeline, scrolled into view (and its text box open on Enter)
    const id = focus?.key.startsWith("cap:") ? focus.key.slice(4) : null;
    const c = id ? lines.find((x) => x.id === id) : null;
    if (!c) return;
    if (focus!.edit) open(c.id, c.text);
    setPicked(c.id);
    show(c.id);
  }, [focus?.nonce]); // eslint-disable-line
  const active = lines.find((c) => t >= c.t_in && t < c.t_out)?.id ?? null;
  useEffect(() => { // C5: the line being said stays in view, never while you type or search
    if (follow && active && !editing && !find) show(active);
  }, [active]); // eslint-disable-line
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
  /** C4: Enter inside an unchanged line splits it in front of the word at the cursor; ⌫ at its start joins it to the
   *  line before. Anything else in the box is a retext. */
  const lineKey = (e: React.KeyboardEvent<HTMLInputElement>, c: CaptionLine) => {
    const el = e.currentTarget, pos = el.selectionStart ?? 0, words = c.words ?? [];
    const same = toks(el.value).join(" ") === toks(c.text).join(" ") && !c.heard && el.selectionStart === el.selectionEnd;
    if (e.key === "Enter" && same && pos > 0 && pos < el.value.trimEnd().length) {
      const k = toks(el.value.slice(0, pos)).length - (/\S$/.test(el.value.slice(0, pos)) && /^\S/.test(el.value.slice(pos)) ? 1 : 0);
      if (k > 0 && k < words.length) { e.preventDefault(); setEditing(null); onBreak(words[k][1] + 0.005, false); return; }
    }
    if (e.key === "Backspace" && same && pos === 0 && words.length && lines.indexOf(c) > 0) {
      e.preventDefault(); setEditing(null); onBreak(words[0][1] + 0.005, true); return;
    }
    if (e.key === "Enter") { e.preventDefault(); commit(c.id, c.text); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setEditing(null); }
  };
  const checks = new Map(lines.map((c) => [c.id, toCheck(v, c)] as const).filter(([, r]) => r.length));
  const nextCheck = () => {
    const ids = lines.filter((c) => checks.has(c.id));
    const to = ids.find((c) => c.t_in > t + 0.05) ?? ids[0];
    if (!to) return;
    setPicked(to.id); onSeek(to.t_in + 0.01); show(to.id);
  };
  const matches = find?.find.trim() ? linesSaying(lines.filter((c) => !c.heard), find.find) : [];
  const gloss = Object.entries(v?.glossary ?? {});
  return (
    <section className="side-panel">
      <div className="panel-head">
        <span className="ph-title">Captions</span><span className="ph-sub">{lines.length} lines</span>
        <span className="grow" />
        {checks.size > 0 && <button className="btn quiet small" onClick={nextCheck} title="The next line the reviewer or Voice's second listen flagged">
          <span className="chk-dot" /> Next to check ({checks.size})</button>}
        <button className={`icon-btn ${find ? "on" : ""}`} aria-pressed={!!find} title="Find and replace in every line"
          onClick={() => setFind(find ? null : { find: "", replace: "" })}><Icon name="search" size={14} /></button>
        <button className={`icon-btn ${follow ? "on" : ""}`} aria-pressed={follow} onClick={() => setFollow(!follow)}
          title={`Follow the playhead: ${follow ? "on" : "off"} (never while you type)`}><Icon name="follow" size={14} /></button>
      </div>
      <div className="panel-body">
        <div className="cap-style-line">
          {st ? <span>{v?.look ? v.look.preset.replace("_", " ") : "Marker"} · {font ?? "Inter Tight"} · {v?.look?.size ?? st.font ?? "–"} px · up to {v?.look?.max_words ?? st.max_words ?? 4} words</span>
            : <span className="mute">Rendered without captions</span>}
          <button className="link small" onClick={onStyle}>Edit style →</button>
        </div>
        {find && (
          <div className="cap-find">
            <input autoFocus value={find.find} placeholder="Find (as Voice heard it)" aria-label="Find"
              onChange={(e) => setFind({ ...find, find: e.target.value })} onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); setFind(null); } }} />
            <input value={find.replace} placeholder="Replace with" aria-label="Replace with" onChange={(e) => setFind({ ...find, replace: e.target.value })} />
            <button className="btn small" disabled={!matches.length || !find.replace.trim() || !!canEdit}
              title={canEdit ?? "Every line that says it, now and after a re-plan (the run's glossary); lines you retexted keep your words"}
              onClick={() => { onGlossary({ [find.find.trim()]: find.replace.trim() }); setFind(null); }}>
              Replace in {matches.length} line{matches.length === 1 ? "" : "s"}</button>
          </div>
        )}
        {lines.length > 0 && !find && <p className="mute small">Double-click a line to fix a word Voice misheard. In the box: Enter at a word splits the line, ⌫ at the start joins it to the line before.</p>}
        <div className="list" ref={body}>
          {(find?.find.trim() ? matches : lines).map((c) => {
            const fix = staged[c.id] && !c.heard ? phraseFix(c.text, staged[c.id]) : null;
            const others = fix ? linesSaying(lines.filter((x) => x.id !== c.id && !x.heard), fix.heard) : [];
            const why = checks.get(c.id);
            return editing === c.id ? (
              <div key={c.id} data-line={c.id} className="list-row cap-edit">
                <span className="lr-t mono">{tc(c.t_in)}</span>
                <input autoFocus value={draft} maxLength={200} aria-label={`Caption at ${tc(c.t_in)}`}
                  onChange={(e) => setDraft(e.target.value)} onBlur={() => commit(c.id, c.text)} onKeyDown={(e) => lineKey(e, c)} />
              </div>
            ) : (
              <div key={c.id} data-line={c.id} role="button" tabIndex={0}
                className={`list-row cap-row ${c.id === active ? "on" : ""} ${staged[c.id] ? "staged" : ""} ${picked === c.id || focus?.key === `cap:${c.id}` ? "picked" : ""}`}
                onClick={() => { setPicked(c.id); onSeek(c.t_in); }} onDoubleClick={() => open(c.id, c.text)}
                onKeyDown={(e) => { if (e.key === "Enter") open(c.id, c.text); }}>
                <span className="lr-t mono">{why ? <span className="chk-dot" title={why.join("\n")} /> : null}{tc(c.t_in)}</span>
                <span className="lr-txt">
                  {staged[c.id] ?? c.text}
                  {staged[c.id] ? <span className="lr-was">was: {c.text}</span>
                    : c.heard && c.heard !== c.text ? <span className="lr-was" title="The words Voice heard; the line was corrected">Voice: {c.heard}</span> : null}
                  {why && !staged[c.id] && <span className="lr-was chk">{why[0]}</span>}
                  {others.length > 0 && (
                    <span className="lr-all">Also in {others.length} other line{others.length === 1 ? "" : "s"}:
                      <button className="link small" disabled={!!canEdit} onClick={(e) => {
                        e.stopPropagation();
                        setStaged((s) => { const { [c.id]: _, ...rest } = s; return rest; });
                        onGlossary({ [fix!.heard]: fix!.written });
                      }} title="The run's glossary: every line that says it, now and after a re-plan">Correct all “{fix!.heard}” → “{fix!.written}”</button>
                    </span>
                  )}
                </span>
                <span className="lr-end">
                  {staged[c.id] ? <span className="lr-flag edited">edited</span> : c.over_face && <span className="lr-flag" title="moved off the face">moved</span>}
                  <button className="icon-btn lr-edit" disabled={!!canEdit} title={canEdit ?? "Edit this line"}
                    onClick={(e) => { e.stopPropagation(); open(c.id, c.text); }}><Icon name="pencil" size={13} /></button>
                  {onAsk && <button className="icon-btn lr-edit" title="Ask Montivo about this line"
                    onClick={(e) => { e.stopPropagation(); onAsk(c); }}><Icon name="assistant" size={13} /></button>}
                </span>
              </div>
            );
          })}
          {!lines.length && <p className="mute small pad">No caption lines in this version.</p>}
          {find?.find.trim() && !matches.length && <p className="mute small pad">No line says “{find.find}”{lines.some((c) => c.heard) ? " (lines you retexted keep your words)" : ""}.</p>}
        </div>
        {gloss.length > 0 && (
          <details className="cap-gloss">
            <summary className="mute small">Corrected everywhere ({gloss.length})</summary>
            {gloss.map(([h, w]) => (
              <div key={h} className="cg-row"><span>“{h}” → <b>“{w}”</b></span>
                <button className="icon-btn" disabled={!!canEdit} title="Take it out: the lines read as Voice heard them" onClick={() => onGlossary({}, [h])}><Icon name="close" size={12} /></button></div>
            ))}
          </details>
        )}
        <p className="mute small">Your fixes stay through every style change: a retexted line keeps its words as one line, whatever the font, size or words per line.</p>
      </div>
      {n > 0 && (
        <div className="cap-apply">
          <span>{n} line{n > 1 ? "s" : ""} edited</span>
          <span className="grow" />
          <button className="btn quiet" onClick={() => setStaged({})}>Discard</button>
          <button className="btn" disabled={!!canEdit} title={canEdit ?? (live ? LIVE_TIP : `Makes Version ${next} with the new text ($0, a re-render)`)} onClick={apply}>
            {live ? "Apply" : `Make Version ${next}`}
          </button>
        </div>
      )}
    </section>
  );
}

/** A text edit for montivo.human text: callouts by span ("" removes one), new title words, the card on or off. */
// callout_at: seconds into the clip where its callout starts (T1, a callout at the playhead)
export type TextEdit = { callouts?: Record<string, string>; title?: string; title_card?: boolean; callout_at?: Record<string, number> };
type Hook = { text: string; why: string };

export function TextPanel({ state, v, t, next, canEdit, onSeek, onApply, focus, rev, live, clipAt, onHooks }: {
  state: RunState; v: Version | null; t: number; next: number; canEdit: string | null; onSeek: (a: number) => void; focus?: PanelFocus | null;
  rev: string; live: boolean;
  onApply: (e: TextEdit) => void;
  clipAt: { span: string; at: number } | null; // the clip under the playhead and how far into it (T1)
  onHooks: () => Promise<Hook[] | null>; // T3: three title lines from Muse Spark, no version
}) {
  const calls = v?.callouts ?? [];
  const [staged, setStaged] = useState<Record<string, string>>({}); // span id -> callout text ("" = removed)
  const [title, setTitle] = useState<string | null>(null);
  const [card, setCard] = useState<boolean | null>(null);
  const [editing, setEditing] = useState<string | null>(null); // a span id or "title"
  const [draft, setDraft] = useState("");
  const [at, setAt] = useState<Record<string, number>>({}); // span id -> seconds into the clip its callout starts
  const [hooks, setHooks] = useState<Hook[] | null>(null);
  const [hooking, setHooking] = useState(false);
  useEffect(() => { setStaged({}); setTitle(null); setCard(null); setEditing(null); setAt({}); }, [rev]);
  const body = useRef<HTMLDivElement>(null);
  /** T1: a callout at a moment: the clip there gets one starting at that second (its words box opens), or its
   *  callout moves to start there. */
  const calloutHere = (sid: string, sec: number) => {
    if (canEdit || !v) return;
    const sp = v.spans.find((x) => x.id === sid);
    if (!sp) return;
    setAt((a) => ({ ...a, [sid]: Math.round(sec * 100) / 100 }));
    const has = (staged[sid] ?? sp.callout ?? "").trim();
    if (!has) { setEditing(sid); setDraft(""); }
    window.requestAnimationFrame(() => body.current?.querySelector(`[data-span="${sid}"]`)?.scrollIntoView({ block: "nearest" }));
  };
  useEffect(() => { // T1: a double-click on the timeline's Text lane (new:<span>:<seconds into it>)
    const m = focus?.key.match(/^new:(s\d+):([\d.]+)$/);
    if (m) calloutHere(m[1], Number(m[2]));
  }, [focus?.nonce]); // eslint-disable-line
  useEffect(() => { // A2: the callout picked on the timeline, scrolled into view (its text box open on Enter)
    const sid = focus?.key.startsWith("callout:") ? focus.key.slice(8) : null;
    const sp = sid ? v?.spans.find((x) => x.id === sid) : null;
    if (!sp) return;
    if (focus!.edit && !canEdit) { setEditing(sp.id); setDraft((staged[sp.id] ?? sp.callout ?? "").trim()); }
    window.requestAnimationFrame(() => body.current?.querySelector(`[data-span="${sp.id}"]`)?.scrollIntoView({ block: "nearest" }));
  }, [focus?.nonce]); // eslint-disable-line
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
  const timed = Object.keys(at).filter((sid) => callout(sid)); // a time for a clip left without words is dropped
  const n = Object.keys(staged).length + timed.filter((sid) => !(sid in staged)).length + (title != null ? 1 : 0) + (card != null ? 1 : 0);
  const needsKey = title != null && !state.has_key;
  const apply = () => onApply({ ...(Object.keys(staged).length ? { callouts: staged } : {}), ...(title != null ? { title } : {}), ...(card != null ? { title_card: card } : {}),
    ...(timed.length ? { callout_at: Object.fromEntries(timed.map((sid) => [sid, at[sid]])) } : {}) });
  const hereSp = clipAt ? v.spans.find((x) => x.id === clipAt.span) ?? null : null;
  const hereHas = hereSp ? !!callout(hereSp.id) : false;
  const askHooks = async () => {
    setHooking(true);
    try { setHooks(await onHooks()); } finally { setHooking(false); }
  };
  const hooksBtn = (
    <button className="btn quiet small" disabled={!!canEdit || !state.has_key || hooking}
      title={canEdit ?? (!state.has_key ? state.key_note ?? "Set MODEL_API_KEY to ask Muse Spark" : `Muse Spark writes three title lines from what the cut says (about ${usd(state.costs?.titles ?? 0.009)}); picking one makes a new card ($0.01)`)}
      onClick={askHooks}>{hooking ? <MarkLoader size={12} /> : <Icon name="spark" size={12} />} Suggest 3 hooks · ~{usd(state.costs?.titles ?? 0.009)}</button>
  );
  const hookList = hooks && (
    <div className="hook-opts">
      {hooks.map((h) => (
        <button key={h.text} className={`hook-opt ${title === h.text ? "on" : ""}`} disabled={!!canEdit} title={h.why}
          onClick={() => { setTitle(h.text === v.title_card_text ? null : h.text); if (!cardOn) setCard(true); }}>
          <b>{h.text}</b><span className="mute small">{h.why}</span>
        </button>
      ))}
    </div>
  );
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
        ) : <p className="mute small">{title ? <>New title card: <b>{title}</b> ($0.01 at the render)</> : "No title card."}</p>}
        <div className="st-row">{hooksBtn}</div>
        {hookList}
        <div className="pn-k">Callouts</div>
        <p className="mute small">One callout per clip at most, anchored to the speaker. Double-click to rewrite; empty removes it. Double-click the Text lane to add one at that moment.</p>
        <button className="btn small" disabled={!!canEdit || !hereSp}
          title={canEdit ?? (!hereSp ? "Put the playhead in a clip" : hereHas ? `Clip ${hereSp.id}'s callout starts at the playhead` : `A callout on clip ${hereSp.id} from the playhead`)}
          onClick={() => clipAt && calloutHere(clipAt.span, clipAt.at)}>
          <Icon name="callout" size={12} /> {hereSp ? (hereHas ? `Start ${hereSp.id}'s callout at ${tc(t)}` : `New callout at ${tc(t)} · ${hereSp.id}`) : "New callout at the playhead"}</button>
        <div className="list" ref={body}>
          {v.spans.map((sp) => {
            const c = calls.find((x) => x.span === sp.id);
            const text = callout(sp.id);
            const was = (sp.callout ?? "").trim();
            if (!text && !was && !(sp.id in staged) && editing !== sp.id) return null;
            return editing === sp.id ? (
              <div key={sp.id} data-span={sp.id} className="list-row cap-edit"><span className="lr-t mono">{sp.id}</span>{editor(`Callout of ${sp.id}`)}</div>
            ) : (
              <div key={sp.id} data-span={sp.id} role="button" tabIndex={0}
                className={`list-row cap-row ${c && t >= c.t_in && t < c.t_out ? "on" : ""} ${sp.id in staged ? "staged" : ""} ${focus?.key === `callout:${sp.id}` ? "picked" : ""}`}
                onClick={() => c && onSeek(c.t_in)} onDoubleClick={() => open(sp.id, text)} onKeyDown={(e) => { if (e.key === "Enter") open(sp.id, text); }}>
                <span className="lr-t mono">{c ? tc(c.t_in) : sp.id}</span>
                <span className="lr-txt">{text ? <b>{text}</b> : <i className="mute">removed</i>}
                  {sp.id in staged && <span className="lr-was">was: {was || "no callout"}</span>}
                  {sp.id in at && text && <span className="lr-was">starts {at[sp.id].toFixed(1)} s into the clip</span>}</span>
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
          <button className="btn quiet" onClick={() => { setStaged({}); setTitle(null); setCard(null); setAt({}); }}>Discard</button>
          <button className="btn" disabled={!!canEdit || needsKey}
            title={canEdit ?? (needsKey ? "New title words call Muse Image: set MODEL_API_KEY" : live ? `${LIVE_TIP.replace("($0)", title != null ? "($0.01 for the new card)" : "($0)")}`
              : `Makes Version ${next} (${title != null ? "$0.01 for the new card" : "$0"}, a re-render)`)}
            onClick={apply}>{live ? "Apply" : `Make Version ${next}`}</button>
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
export function SpeakersPanel({ id, v, next, canEdit, onApply, rev, live }: {
  id: string; v: Version | null; next: number; canEdit: string | null; onApply: (speaker: string, head: string | null) => void;
  rev: string; live: boolean;
}) {
  const [info, setInfo] = useState<HeadsInfo | null>(null);
  const [staged, setStaged] = useState<{ speaker: string; head: string | null } | null>(null);
  useEffect(() => { api<HeadsInfo>(`/api/runs/${id}/heads`).then(setInfo).catch(() => setInfo({ speakers: [], heads: [], video: null })); }, [id]);
  useEffect(() => { setStaged(null); }, [rev]);
  const pins = v?.speaker_pins ?? {};
  const thumb = (h: string) => `/api/runs/${id}/head/${h}.jpg`;
  const follows = (spk: string) => (pins[spk] ? [pins[spk]] : info?.speakers.find((s) => s.id === spk)?.ids ?? []);
  return (
    <section className="side-panel">
      <div className="panel-head"><span className="ph-title">Speakers</span><span className="ph-sub">{info ? `${info.speakers.length} voices · ${info.heads.length} heads` : ""}</span></div>
      <div className="panel-body">
        {!info && <p className="mute small"><MarkLoader /> Loading…</p>}
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
          <button className="btn" disabled={!!canEdit} title={canEdit ?? (live ? LIVE_TIP : `Makes Version ${next} with the crop on that head ($0, a re-render)`)}
            onClick={() => onApply(staged.speaker, staged.head)}>{live ? "Apply" : `Make Version ${next}`}</button>
        </div>
      )}
    </section>
  );
}
