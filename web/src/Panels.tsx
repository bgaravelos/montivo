import { useEffect, useRef, useState } from "react";
import type { PanelFocus } from "./Editor";
import { api, fileUrl, frameUrl, tc, usd } from "./api";
import { flatWords, originKind, originLabel, parentOf, speakerColor, type Block } from "./edit";
import { Icon, MarkLoader } from "./icons";
import { whatChanged } from "./Loop";
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

type MediaTab = "source" | "brief" | "assets";
type Assets = { stills: { id: string; prompt: string | null; placement: string | null; url: string | null }[];
  title: { url: string; text: string | null; on: boolean } | null; covers: { url: string; template: string; t_s: number | null }[];
  music: { track: string; url: string } | null; footage: string | null };

/** Media: the source clip and its whole transcript with what the cut uses and what it leaves out (ME1: word search,
 *  Insert a passage that is not in the cut), the brief as an editable text with "Re-plan with this brief" (ME2), and
 *  the project's assets in one list (ME3). */
export function MediaPanel({ state, v, blocks, canEdit, hasKey, planCost, onSeek, onInsert, onReplan }: {
  state: RunState; v: Version | null; blocks: Block[]; canEdit: string | null; hasKey: boolean; planCost: number;
  onSeek: (t: number) => void; onInsert: (turn: number, a: number, b: number, where: "source" | "playhead") => void;
  onReplan: (brief: string) => void;
}) {
  const m = state.media;
  const name = m.footage?.split("/").pop() ?? "source";
  const [tab, setTabState] = useState<MediaTab>(() => { try { return (localStorage.getItem("montivo.media.tab") as MediaTab) || "source"; } catch { return "source"; } });
  const setTab = (x: MediaTab) => { setTabState(x); try { localStorage.setItem("montivo.media.tab", x); } catch { /* this tab */ } };
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<{ turn: number; a: number; b: number } | null>(null);
  const brief0 = v?.brief_edited ?? state.run.brief ?? "";
  const [brief, setBrief] = useState(brief0);
  useEffect(() => setBrief(brief0), [brief0]);
  const [assets, setAssets] = useState<Assets | null>(null);
  useEffect(() => {
    if (tab !== "assets" || !v || v.draft) return;
    api<Assets>(`/api/runs/${state.id}/assets?v=${v.n}`).then(setAssets).catch(() => setAssets(null));
  }, [tab, v?.n, v?.draft, state.id]); // eslint-disable-line
  useEffect(() => setSel(null), [v?.n]);
  const cutAt = new Map(flatWords(blocks).map((f) => [`${f.span.turn_id}:${f.w.i}`, f.t]));
  const needle = q.trim().toLowerCase();
  const turns = Object.entries(state.words).map(([turn, ws]) => ({ turn: Number(turn), ws })).sort((a, b) => (a.ws[0]?.t ?? 0) - (b.ws[0]?.t ?? 0));
  const hits = needle ? turns.reduce((n, x) => n + x.ws.filter((w) => w.w.toLowerCase().includes(needle)).length, 0) : 0;
  const keptN = cutAt.size, allN = turns.reduce((n, x) => n + x.ws.length, 0);
  const pick = (turn: number, i: number, shift: boolean) => { // unused words only, one turn, one run
    if (shift && sel && sel.turn === turn) {
      const a = Math.min(sel.a, i), b = Math.max(sel.b, i);
      const free = state.words[String(turn)].filter((w) => w.i >= a && w.i <= b).every((w) => !cutAt.has(`${turn}:${w.i}`));
      if (free) return setSel({ turn, a, b });
    }
    setSel({ turn, a: i, b: i });
  };
  const selWords = sel ? state.words[String(sel.turn)].filter((w) => w.i >= sel.a && w.i <= sel.b) : [];
  return (
    <section className="side-panel">
      <div className="panel-head"><span className="ph-title">Media</span><span className="ph-sub">{name}</span></div>
      <div className="panel-body">
        <div className="seg-group st-tabs" role="tablist" aria-label="Media sections">
          {([["source", "Source"], ["brief", "Brief"], ["assets", "Assets"]] as [MediaTab, string][]).map(([k, label]) =>
            <button key={k} role="tab" aria-selected={tab === k} className={`seg ${tab === k ? "on" : ""}`} onClick={() => setTab(k)}>{label}</button>)}
        </div>

        {tab === "source" && <>
          <p className="mute small">The whole recording, in order: <b>bright</b> words are in the cut (click to play there), dim ones are not.
            Click a dim word, Shift-click another of the same passage, then Insert. {keptN} of {allN} words used.</p>
          <input className="st-input" value={q} placeholder="Find a word in the recording" onChange={(e) => setQ(e.target.value)} aria-label="Find a word" />
          {needle && <p className="mute small">{hits} match{hits === 1 ? "" : "es"}</p>}
          <div className="src-text">
            {turns.map(({ turn, ws }) => (
              <p key={turn} className="src-turn">
                <span className="src-t mono" title={`Turn ${turn}`}>{tc(ws[0]?.t ?? 0)}</span>
                {ws.map((w) => {
                  const at = cutAt.get(`${turn}:${w.i}`);
                  const on = sel && sel.turn === turn && w.i >= sel.a && w.i <= sel.b;
                  const hit = needle && w.w.toLowerCase().includes(needle);
                  return <span key={w.i} className={`sw ${at != null ? "kept" : "unused"} ${on ? "wsel" : ""} ${hit ? "hit" : ""}`}
                    title={at != null ? `In the cut at ${tc(at)}` : `${tc(w.t)} in the recording: not in the cut`}
                    onClick={(e) => (at != null ? onSeek(at) : pick(turn, w.i, e.shiftKey))}>{w.w} </span>;
                })}
              </p>
            ))}
          </div>
          {sel && (
            <div className="cap-apply src-apply">
              <span className="sa-what">“{selWords.map((w) => w.w).join(" ").slice(0, 60)}” · {selWords.length} word{selWords.length === 1 ? "" : "s"}, {selWords.length ? (selWords.at(-1)!.e - selWords[0].t).toFixed(1) : 0} s</span>
              <button className="btn quiet" onClick={() => setSel(null)}>Clear</button>
              <button className="btn quiet" disabled={!!canEdit || selWords.length < 2} title={canEdit ?? "Next to the clip under the playhead"}
                onClick={() => { onInsert(sel.turn, sel.a, sel.b, "playhead"); setSel(null); }}>At the playhead</button>
              <button className="btn" disabled={!!canEdit || selWords.length < 2} title={canEdit ?? (selWords.length < 2 ? "A clip keeps at least two words" : "Between the clips around it in the recording ($0)")}
                onClick={() => { onInsert(sel.turn, sel.a, sel.b, "source"); setSel(null); }}>Insert</button>
            </div>
          )}
        </>}

        {tab === "brief" && <>
          <div className="pn-k">Brief</div>
          <textarea className="st-input brief-edit" rows={6} value={brief} maxLength={2000} onChange={(e) => setBrief(e.target.value)} aria-label="The brief" />
          <p className="mute small">{v?.brief_edited ? "This version was planned from an edited brief. " : ""}Muse Spark plans the cut again from these words: a new version, the clips chosen afresh (your captions and callouts carry where the same lines come back).</p>
          <button className="btn wide" disabled={!!canEdit || !hasKey || !brief.trim() || brief.trim() === brief0.trim()}
            title={canEdit ?? (!hasKey ? state.key_note ?? "Set MODEL_API_KEY to plan with Muse Spark" : `One Muse Spark plan call, about ${usd(planCost)}, then a render`)}
            onClick={() => onReplan(brief.trim())}><Icon name="spark" size={13} /> Re-plan with this brief <span className="btn-cost">~{usd(planCost)}</span></button>
          <div className="pn-k">Target</div>
          <p>{state.run.target_s ?? 40} s, vertical 9:16</p>
        </>}

        {tab === "assets" && <>
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
          {v?.draft && <p className="mute small">The draft's assets show once it is a version.</p>}
          {assets && <>
            <div className="pn-k">Title card</div>
            {assets.title ? <div className="asset-row"><img src={assets.title.url} alt="" className="asset-tall" /><span>{assets.title.text}<span className="mute small"> · {assets.title.on ? "on" : "off"} in v{v?.n}</span></span></div>
              : <p className="mute small">None made.</p>}
            <div className="pn-k">B-roll stills</div>
            {assets.stills.length ? <div className="asset-grid">{assets.stills.map((x) => <figure key={x.id} title={x.prompt ?? ""}>
              {x.url ? <img src={x.url} alt="" /> : <span className="media-ph">not drawn yet</span>}<figcaption>{x.prompt}</figcaption></figure>)}</div>
              : <p className="mute small">None in this version: double-click the B-roll lane to add one.</p>}
            <div className="pn-k">Covers</div>
            {assets.covers.length ? <div className="asset-grid">{assets.covers.map((x) => <figure key={x.url}><img src={x.url} alt="" /><figcaption>{x.template}{x.t_s != null ? ` · ${tc(x.t_s)}` : ""}</figcaption></figure>)}</div>
              : <p className="mute small">None yet: Export makes them.</p>}
            <div className="pn-k">Music</div>
            {assets.music ? <div className="asset-row"><audio src={assets.music.url} controls preload="none" /><span>{assets.music.track.replace(/_/g, " ")}</span></div>
              : <p className="mute small">No music in this version.</p>}
          </>}
        </>}
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
// title_style: "header" = the title over the opening speech ($0), "card" = a Muse Image card before the first word
export type TextEdit = { callouts?: Record<string, string>; title?: string; title_card?: boolean; callout_at?: Record<string, number>;
  title_style?: "header" | "card" };
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
  const [style, setStyle] = useState<"header" | "card" | null>(null);
  const [editing, setEditing] = useState<string | null>(null); // a span id or "title"
  const [draft, setDraft] = useState("");
  const [at, setAt] = useState<Record<string, number>>({}); // span id -> seconds into the clip its callout starts
  const [hooks, setHooks] = useState<Hook[] | null>(null);
  const [hooking, setHooking] = useState(false);
  useEffect(() => { setStaged({}); setTitle(null); setCard(null); setStyle(null); setEditing(null); setAt({}); }, [rev]);
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
  const nowStyle = v.title_style ?? "card", wantStyle = style ?? nowStyle, isCard = wantStyle === "card";
  const imageCost = isCard && (title != null || (style === "card" && !state.media.title_png)); // a new Muse Image card
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
  const n = Object.keys(staged).length + timed.filter((sid) => !(sid in staged)).length + (title != null ? 1 : 0) + (card != null ? 1 : 0) + (style != null ? 1 : 0);
  const needsKey = imageCost && !state.has_key;
  const apply = () => onApply({ ...(Object.keys(staged).length ? { callouts: staged } : {}), ...(title != null ? { title } : {}), ...(card != null ? { title_card: card } : {}),
    ...(style != null ? { title_style: style } : {}),
    ...(timed.length ? { callout_at: Object.fromEntries(timed.map((sid) => [sid, at[sid]])) } : {}) });
  const hereSp = clipAt ? v.spans.find((x) => x.id === clipAt.span) ?? null : null;
  const hereHas = hereSp ? !!callout(hereSp.id) : false;
  const askHooks = async () => {
    setHooking(true);
    try { setHooks(await onHooks()); } finally { setHooking(false); }
  };
  const hooksBtn = (
    <button className="btn quiet small" disabled={!!canEdit || !state.has_key || hooking}
      title={canEdit ?? (!state.has_key ? state.key_note ?? "Set MODEL_API_KEY to ask Muse Spark" : `Muse Spark writes three title lines from what the cut says (about ${usd(state.costs?.titles ?? 0.009)})${isCard ? "; picking one makes a new card ($0.01)" : ""}`)}
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
        <div className="pn-k">Title</div>
        <div className="seg-row" role="radiogroup" aria-label="How the title shows">
          {(["header", "card"] as const).map((s) => (
            <button key={s} role="radio" aria-checked={wantStyle === s} className={`chip ${wantStyle === s ? "on" : ""}`} disabled={!!canEdit}
              title={canEdit ?? (s === "header" ? "The title words over the first seconds of speech: the hook starts at once ($0)"
                : "A Muse Image card for 1.5 s before the first word" + (state.media.title_png ? "" : " ($0.01 to make it)"))}
              onClick={() => setStyle(s === nowStyle ? null : s)}>{s === "header" ? "Over the opening" : "Card before"}</button>
          ))}
        </div>
        {v.title_card_text ? (
          <div className={`title-card ${(card ?? cardOn) ? "" : "off"} ${title != null || card != null ? "staged" : ""}`}>
            {nowStyle === "card" && ( // the header is drawn over the cut, nothing to show apart from it
              <button className="tc-thumb" onClick={() => v.title_s > 0 && onSeek(0)} title="Play the title card">
                {state.media.title_png && <img src={fileUrl(state.id, state.media.title_png)} alt="" />}
              </button>
            )}
            {editing === "title" ? editor("Title card words") : (
              <span className="tc-text" onDoubleClick={() => open("title", title ?? v.title_card_text ?? "")}>{title ?? v.title_card_text}</span>
            )}
            {title != null && <span className="lr-was">was: {v.title_card_text}{isCard ? " · new words make a new Muse Image card ($0.01)" : ""}</span>}
            <span className="tc-meta">{v.title_s > 0 ? `0:00 – ${tc(v.title_s)} · Muse Image background`
              : !cardOn ? "off in this version" : nowStyle === "header" ? "over the first 2.2 s of speech, in the look's callout colours" : "on"}</span>
            <span className="tc-actions">
              <button className="btn quiet small" disabled={!!canEdit} title={canEdit ?? (isCard ? "New words for the card (Muse Image draws them, $0.01)" : "New title words ($0)")}
                onClick={() => open("title", title ?? v.title_card_text ?? "")}><Icon name="pencil" size={12} /> Words</button>
              <button className="btn quiet small" disabled={!!canEdit} title={canEdit ?? "The title, on or off ($0)"}
                onClick={() => setCard((c) => { const want = !(c ?? cardOn); return want === cardOn ? null : want; })}>
                {(card ?? cardOn) ? "Turn off" : "Turn on"}</button>
            </span>
          </div>
        ) : <p className="mute small">{title ? <>New title: <b>{title}</b>{isCard ? " ($0.01 at the render)" : ""}</> : "No title."}</p>}
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

type Labels = Record<string, { name?: string; star?: boolean }>;
type VerFilter = "all" | "agent" | "you";

/** V4: every reviewed version's blind score in order, the one on screen marked; a dot picks its version. */
function ScoreLine({ versions, sel, onPick }: { versions: Version[]; sel: number | null; onPick: (n: number) => void }) {
  const pts = versions.filter((x) => x.review);
  if (pts.length < 2) return null;
  const W = 260, H = 46, pad = 6;
  const lo = Math.min(...pts.map((x) => x.review!.overall)) - 4, hi = Math.max(...pts.map((x) => x.review!.overall)) + 4;
  const x = (k: number) => pad + (k * (W - 2 * pad)) / (pts.length - 1);
  const y = (s: number) => H - pad - ((s - lo) * (H - 2 * pad)) / Math.max(1, hi - lo);
  const first = pts[0].review!.overall, best = Math.max(...pts.map((p) => p.review!.overall));
  return (
    <div className="score-line">
      <div className="sl-head"><span className="pn-k">Blind scores</span><span className="mute small">{first} → best {best} over {pts.length} reviews</span></div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label={`Scores ${pts.map((p) => p.review!.overall).join(", ")}`}>
        <polyline points={pts.map((p, k) => `${x(k)},${y(p.review!.overall)}`).join(" ")} fill="none" stroke="var(--accent)" strokeWidth={1.6} />
        {pts.map((p, k) => (
          <g key={p.n} className="sl-dot" onClick={() => onPick(p.n)}>
            <title>{`Version ${p.n}: ${p.review!.overall}${p.review!.pass ? ", passes" : ""}`}</title>
            <circle cx={x(k)} cy={y(p.review!.overall)} r={p.n === sel ? 4.5 : 3} fill={p.review!.pass ? "var(--pass)" : "var(--accent)"}
              stroke={p.n === sel ? "var(--text)" : "none"} strokeWidth={1.5} />
          </g>
        ))}
      </svg>
    </div>
  );
}

export function VersionsPanel({ id, state, versions, sel, onPick, go, replay, opening, canEdit, onRestore }: {
  id: string; state: RunState; versions: Version[]; sel: number | null; onPick: (n: number) => void; go: (h: string) => void; replay: number;
  canEdit: string | null; onRestore: (n: number) => void;
  opening: number | null; // the version the editor opens on (a human edit or the hook A/B's pick, else the loop's best)
}) {
  const [labels, setLabels] = useState<Labels>({});
  const [naming, setNaming] = useState<number | null>(null);
  const [nameDraft, setNameDraft] = useState("");
  const [filter, setFilterState] = useState<VerFilter>(() => { try { return (localStorage.getItem("montivo.versions.filter") as VerFilter) || "all"; } catch { return "all"; } });
  const setFilter = (f: VerFilter) => { setFilterState(f); try { localStorage.setItem("montivo.versions.filter", f); } catch { /* this tab */ } };
  useEffect(() => { api<{ labels: Labels }>(`/api/runs/${id}/labels`).then((r) => setLabels(r.labels)).catch(() => {}); }, [id]);
  const label = (n: number, b: { name?: string; star?: boolean }) =>
    api<{ labels: Labels }>(`/api/runs/${id}/labels`, { version: n, ...b }).then((r) => setLabels(r.labels)).catch(() => {});
  const v = versions.find((x) => x.n === sel);
  const parent = v ? parentOf(v, state.versions) : undefined;
  const pair = v && versions.length > 1 ? (parent && versions.some((x) => x.n === parent.n) ? [parent.n, v.n] : [v.n, versions.find((x) => x.n !== v.n)!.n]) : null;
  const best = state.loop?.best?.version;
  const by = (x: Version) => (originKind(x) === "human" ? "you" : "agent");
  const shown = versions.filter((x) => filter === "all" || by(x) === filter || labels[x.n]?.star);
  const count = (f: VerFilter) => versions.filter((x) => by(x) === f).length;
  return (
    <section className="side-panel">
      <div className="panel-head"><span className="ph-title">Versions</span><span className="ph-sub">{versions.length}</span></div>
      <div className="panel-body">
        <ScoreLine versions={versions} sel={sel} onPick={onPick} />
        <div className="seg-group" role="group" aria-label="Which versions">
          <button className={`seg ${filter === "all" ? "on" : ""}`} onClick={() => setFilter("all")}>All {versions.length}</button>
          <button className={`seg ${filter === "agent" ? "on" : ""}`} onClick={() => setFilter("agent")} title="The planner, the reviewer's fixes and the hook A/B">Agent {count("agent")}</button>
          <button className={`seg ${filter === "you" ? "on" : ""}`} onClick={() => setFilter("you")} title="Your edits and what you asked Montivo for">You {count("you")}</button>
        </div>
        <div className="versions">
          {[...shown].reverse().map((x) => {
            const p = parentOf(x, state.versions);
            const lb = labels[x.n] ?? {};
            const change = whatChanged(state, x);
            return (
              <div key={x.n} role="button" tabIndex={0} className={`ver ${x.n === sel ? "on" : ""} ${originKind(x)}`} onClick={() => onPick(x.n)}
                onKeyDown={(e) => { if (e.key === "Enter" && naming !== x.n) onPick(x.n); }}>
                {x.cut && <img className="ver-poster" src={frameUrl(id, x.n, Math.min((x.duration_s ?? x.runtime_s) - 0.2, x.title_s + 1.2), true)} alt="" loading="lazy" />}
                <span className="ver-body">
                  <span className="ver-top">
                    <button className={`icon-btn ver-star ${lb.star ? "on" : ""}`} title={lb.star ? "Unstar" : "Star this version (it stays in every filter)"}
                      onClick={(e) => { e.stopPropagation(); label(x.n, { star: !lb.star }); }}><Icon name="star" size={13} /></button>
                    {naming === x.n ? (
                      <input className="st-input ver-name-in" autoFocus value={nameDraft} maxLength={40} placeholder={`Version ${x.n}`}
                        onClick={(e) => e.stopPropagation()} onChange={(e) => setNameDraft(e.target.value)}
                        onBlur={() => { label(x.n, { name: nameDraft }); setNaming(null); }}
                        onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") { label(x.n, { name: nameDraft }); setNaming(null); } else if (e.key === "Escape") setNaming(null); }} />
                    ) : (
                      <span className="ver-name" title="Double-click to name it" onDoubleClick={(e) => { e.stopPropagation(); setNaming(x.n); setNameDraft(lb.name ?? ""); }}>
                        {lb.name ? <>{lb.name} <span className="mute">v{x.n}</span></> : `Version ${x.n}`}</span>
                    )}
                    {x.n === opening && <span className="ver-kept" title="The project opens here: the newest version a human edit made or the hook A/B kept, else the review loop's best">opens here</span>}
                    {x.n === best && <span className="ver-best" title="The review loop's highest blind score">loop best</span>}
                    <span className="grow" />
                    {x.review ? <span className={`ver-score ${x.review.pass ? "pass" : ""}`}>{x.review.pass && <Icon name="check" size={12} />}{x.review.overall}</span>
                      : <span className="ver-score none" title="not reviewed">–</span>}
                  </span>
                  <span className="ver-change" title={change.lines.join("\n")}>
                    <span className={`ver-by ${by(x)}`}>{by(x) === "you" ? "You" : "Montivo"}</span>
                    {change.lines[0] ?? originLabel(x)}{change.lines.length > 1 ? ` +${change.lines.length - 1}` : ""}
                  </span>
                  <span className="ver-meta mute">{p ? `from v${p.n} · ` : ""}{x.spans.length} clips · {tc(x.duration_s ?? x.runtime_s)}{x.planner ? ` · ${x.planner.name}` : ""}</span>
                </span>
              </div>
            );
          })}
          {!shown.length && <p className="mute small pad">No version here: {filter === "you" ? "you have not edited this project yet" : "the agent made none"}.</p>}
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

/** Speakers & framing: who the crop follows (every Voice speaker with the head the speaker map gave it, or a human
 *  pin, and every SAM head to pick from; SP4 a click on a face in the viewer stages the same pin), what each speaker
 *  is called (SP1) and which two are one person (SP2). With one voice it shows the tracked head and the crop (SP5). */
export type PinStage = { speaker: string; head: string | null } | null;
export type SpeakerEdit = { names?: Record<string, string>; merge?: Record<string, string | null> };

export function SpeakersPanel({ id, v, next, canEdit, onApply, onSpeakers, rev, live, staged, setStaged }: {
  id: string; v: Version | null; next: number; canEdit: string | null; onApply: (speaker: string, head: string | null) => void;
  onSpeakers: (e: SpeakerEdit) => void; rev: string; live: boolean;
  staged: PinStage; setStaged: (p: PinStage) => void; // the editor holds the pin: a click on a face stages it too (SP4)
}) {
  const [info, setInfo] = useState<HeadsInfo | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [merge, setMerge] = useState<Record<string, string | null>>({});
  useEffect(() => { api<HeadsInfo>(`/api/runs/${id}/heads`).then(setInfo).catch(() => setInfo({ speakers: [], heads: [], video: null })); }, [id]);
  useEffect(() => { setStaged(null); setNames({}); setMerge({}); }, [rev]); // eslint-disable-line
  const pins = v?.speaker_pins ?? {};
  const thumb = (h: string) => `/api/runs/${id}/head/${h}.jpg`;
  const follows = (spk: string) => (pins[spk] ? [pins[spk]] : info?.speakers.find((s) => s.id === spk)?.ids ?? []);
  const nameOf = (spk: string) => names[spk] ?? v?.speaker_names?.[spk] ?? "";
  const intoOf = (spk: string) => (spk in merge ? merge[spk] : v?.speaker_merge?.[spk] ?? null);
  const nEdits = Object.keys(names).length + Object.keys(merge).length;
  const stageName = (spk: string, x: string) => { // a name the version already has is no change
    const was = v?.speaker_names?.[spk] ?? "";
    setStaged(null);
    setNames((n) => { const { [spk]: _, ...rest } = n; return x.trim() === was ? rest : { ...rest, [spk]: x }; });
  };
  const stageMerge = (spk: string, into: string | null) => {
    const was = v?.speaker_merge?.[spk] ?? null;
    setStaged(null);
    setMerge((m) => { const { [spk]: _, ...rest } = m; return into === was ? rest : { ...rest, [spk]: into }; });
  };
  const pinStage = (p: PinStage) => { setNames({}); setMerge({}); setStaged(p); };
  const single = info && info.speakers.length < 2;
  return (
    <section className="side-panel">
      <div className="panel-head"><span className="ph-title">Speakers &amp; framing</span><span className="ph-sub">{info ? `${info.speakers.length} voice${info.speakers.length === 1 ? "" : "s"} · ${info.heads.length} head${info.heads.length === 1 ? "" : "s"}` : ""}</span></div>
      <div className="panel-body">
        {!info && <p className="mute small"><MarkLoader /> Loading…</p>}
        {info && !info.heads.length && <p className="mute small">No head track in this run: the crop follows the person.</p>}
        {single && info.heads.length > 0 && <>
          <p className="mute small">One voice: the 9:16 crop follows the head SAM tracked, smoothed so it pans without jumps. Pause on the video to see the head on the frame.</p>
          <div className="spk-heads">
            {info.heads.map((h) => <span key={h.id} className="head on solo" title={`Head ${h.id}: tracked on ${h.frames} frames`}>
              <img src={thumb(h.id)} alt="" loading="lazy" /><span>head {h.id} · {h.frames} frames</span></span>)}
          </div>
        </>}
        {info && info.speakers.length >= 1 && (
          <p className="mute small">{single ? "Name the voice: the transcript, the chat and the clip inspector use it." : "When a speaker talks, the 9:16 crop follows their head. The speaker map picked one per voice; pin another head where it got it wrong, or click a face on the paused video."}</p>
        )}
        {info?.speakers.map((s) => {
          const cur = follows(intoOf(s.id) ?? s.id);
          const st = staged?.speaker === s.id ? staged : null;
          const into = intoOf(s.id);
          return (
            <div key={s.id} className={`spk-card ${st || s.id in names || s.id in merge ? "staged" : ""}`}>
              <div className="spk-top">
                <span className="spk-id" style={{ borderColor: speakerColor(into ?? s.id) }}>{s.id}</span>
                <input className="st-input spk-name" value={nameOf(s.id)} maxLength={40} placeholder="Name, e.g. Billy" disabled={!!canEdit || !!into}
                  aria-label={`Name of speaker ${s.id}`} onChange={(e) => stageName(s.id, e.target.value)} />
                <span className="mute small">{s.words} words</span>
              </div>
              {s.who && <div className="spk-who mute small" title="Watch's description of who speaks; it can name the wrong person (run 014): trust the faces">watch's guess: {s.who}</div>}
              {info.speakers.length >= 2 && (
                <label className="field spk-merge">Same person as
                  <select value={into ?? ""} disabled={!!canEdit} onChange={(e) => stageMerge(s.id, e.target.value || null)}>
                    <option value="">no one: a voice of their own</option>
                    {info.speakers.filter((x) => x.id !== s.id && !intoOf(x.id)).map((x) => <option key={x.id} value={x.id}>{x.id}{nameOf(x.id) ? ` · ${nameOf(x.id)}` : ""}</option>)}
                  </select>
                </label>
              )}
              {info.speakers.length >= 2 && info.heads.length > 0 && !into && <>
                <div className="spk-now mute small">
                  {pins[s.id] ? <>pinned to head {pins[s.id]} by you</> : cur.length ? <>speaker map: head {cur.join(", ")} ({s.method ?? "map"})</> : "no head mapped"}
                </div>
                <div className="spk-heads">
                  {info.heads.map((h) => {
                    const on = st ? st.head === h.id : cur.includes(h.id);
                    return (
                      <button key={h.id} className={`head ${on ? "on" : ""}`} disabled={!!canEdit}
                        title={canEdit ?? `Head ${h.id}${h.speaker ? ` (mapped to ${h.speaker})` : ""}: follow it when ${s.id} talks`}
                        onClick={() => pinStage(cur.includes(h.id) && !pins[s.id] ? null : pins[s.id] === h.id ? null : { speaker: s.id, head: h.id })}>
                        <img src={thumb(h.id)} alt="" loading="lazy" />
                        <span>{h.id}</span>
                      </button>
                    );
                  })}
                </div>
                {pins[s.id] && !st && (
                  <button className="link small" disabled={!!canEdit} onClick={() => pinStage({ speaker: s.id, head: null })}>Back to the speaker map</button>
                )}
              </>}
              {into && <div className="mute small">The crop follows {into}'s head while {s.id} talks; the transcript shows {nameOf(into) || into}.</div>}
            </div>
          );
        })}
      </div>
      {(staged || nEdits > 0) && (
        <div className="cap-apply">
          <span>{staged ? (staged.head ? `${staged.speaker} → head ${staged.head}` : `${staged.speaker} → speaker map`) : `${nEdits} change${nEdits > 1 ? "s" : ""}`}</span>
          <span className="grow" />
          <button className="btn quiet" onClick={() => { setStaged(null); setNames({}); setMerge({}); }}>Discard</button>
          <button className="btn" disabled={!!canEdit} title={canEdit ?? (live ? LIVE_TIP : `Makes Version ${next} ($0, a re-render)`)}
            onClick={() => staged ? onApply(staged.speaker, staged.head)
              : onSpeakers({ ...(Object.keys(names).length ? { names: Object.fromEntries(Object.entries(names).map(([k, x]) => [k, x.trim()])) } : {}),
                ...(Object.keys(merge).length ? { merge } : {}) })}>{live ? "Apply" : `Make Version ${next}`}</button>
        </div>
      )}
    </section>
  );
}
