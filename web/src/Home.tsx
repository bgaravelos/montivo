import { useEffect, useMemo, useRef, useState } from "react";
import { api, crit, dur, fileUrl, frameUrl, tc, usd } from "./api";
import { fixText, nameOf, originLabel, parentOf, sevRank } from "./edit";
import { Icon, MarkLoader } from "./icons";
import { PlannerPicker } from "./Panels";
import { askToNotify, ProgressMini } from "./Progress";
import { kitNote } from "./Style";
import type { Budget, CardScore, Costs, LooksInfo, Planner, RunCard, RunState, SavedLook, Version } from "./types";

type Footage = { path: string; name: string; mb: number; duration_s: number | null };
const COSTS: Costs = { plan: 0.05, plan_open: 0.012, plan_s: 35, plan_open_s: 7, review: 0.035, run_per_s: 0.0026, run_fixed: 0.045,
  run_wall_fixed_s: 60, run_wall_per_s: 0.9, review_loop: 0.07, review_loop_s: 85, hook_ab: 0.04 };
const REPO = "https://github.com/bgaravelos/montivo";

/** Starting briefs for the common kinds of footage; the text stays editable. */
const PRESETS: { label: string; hint: string; brief: string; target: number }[] = [
  { label: "Talking head", hint: "hook first, no ums", target: 40, brief: "35 to 45 s vertical reel: what this is and why it matters. Keep the hook in the first line, drop the pauses, the ums and any repeated take (keep the last one), end on the strongest line." },
  { label: "Interview", hint: "the best answers", target: 45, brief: "40 to 50 s vertical highlight of this interview: open on the most surprising answer, keep two or three answers that tell one story, cut the questions unless the answer needs them, end on a line that lands." },
  { label: "Product demo", hint: "result first", target: 35, brief: "30 to 40 s vertical demo: open on the result, then show how it works in the order it happens on screen, keep the moments the speaker points at something, end on what to do next." },
  { label: "Event recap", hint: "energy, takeaway", target: 30, brief: "25 to 35 s vertical recap of this event: open on the most energetic line, keep who is speaking and why it matters, drop greetings and logistics, end on the takeaway." },
];
const LENGTHS = [15, 30, 40, 60];

/** The Meta models every run calls, with the mark each one ships under: the Muse family shares Meta's Muse icon, SAM
 *  has none of its own and goes out under the Meta symbol (official files in public/logos). */
const MODELS: { name: string; does: string; logo: string; open?: boolean }[] = [
  { name: "Muse Spark 1.3", does: "plans the edit and reviews it blind", logo: "/logos/muse.webp" },
  { name: "Muse Voice", does: "words and their timings", logo: "/logos/muse.webp" },
  { name: "SAM 3.1", does: "tracks the speaker for the crop", logo: "/logos/meta-symbol.svg" },
  { name: "Muse Image", does: "title cards and B-roll", logo: "/logos/muse.webp" },
  { name: "Muse Spark Open", does: "the planner on open weights, Baseten", logo: "/logos/muse.webp", open: true },
];

/** What a new run costs and how long it takes, from the clip's length (server COST, measured on runs 014 and 015). */
function estimate(c: Costs, clipS: number, o: { review: boolean; hookAb: boolean; open: boolean }) {
  let usd_ = (c.run_fixed ?? 0.045) + c.run_per_s * clipS;
  let s = (c.run_wall_fixed_s ?? 60) + (c.run_wall_per_s ?? 0.9) * clipS;
  if (o.open) { usd_ -= c.plan - c.plan_open; s -= c.plan_s - c.plan_open_s; }
  if (o.review) { usd_ += c.review_loop ?? 0.07; s += c.review_loop_s ?? 85; }
  if (o.review && o.hookAb) { usd_ += c.hook_ab ?? 0.04; s += (c.review_s ?? 26) + 25; }
  return { usd: usd_, s };
}
const POLL_MS = 2000; // while a project has a job running, its card follows the job
const IDLE_POLL_MS = 15000; // otherwise a slow look for a job started from another tab
const FEW = 7; // up to this many projects the list needs no search or sort

type Sort = "newest" | "best" | "name";
type Only = "all" | "samples" | "yours" | "passed";
const SORTS: { id: Sort; label: string }[] = [{ id: "newest", label: "Newest" }, { id: "best", label: "Best score" }, { id: "name", label: "Name" }];

/** A per-viewer preference in localStorage (the list's sort and filter); private windows and blocked storage fall back. */
function usePref<T extends string>(key: string, fallback: T, ok: readonly T[]): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => {
    try { const x = localStorage.getItem(key) as T | null; return x && ok.includes(x) ? x : fallback; } catch { return fallback; }
  });
  const set = (x: T) => { setV(x); try { localStorage.setItem(key, x); } catch { /* no storage: this tab only */ } };
  return [v, set];
}

const bestOf = (r: RunCard) => Math.max(-1, ...(r.card?.scores ?? []).map((s) => s.overall));
const nameFor = (r: RunCard) => nameOf(r.card?.title_card_text, r.brief);
const isSample = (r: RunCard) => r.kind === "sample" || r.kind === "copy";
const reducedMotion = () => typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
const typing = (t: EventTarget | null) => t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));

type Limits = { footage_s: number | null; upload_mb: number | null; uploads: number | null };

/** A local clip's length from its metadata, or null when the browser cannot read it (the server checks it again). */
function clipSeconds(file: File): Promise<number | null> {
  return new Promise((resolve) => {
    const v = document.createElement("video");
    const url = URL.createObjectURL(file);
    const done = (x: number | null) => { URL.revokeObjectURL(url); v.removeAttribute("src"); resolve(x); };
    const timer = window.setTimeout(() => done(null), 5000);
    v.preload = "metadata";
    v.onloadedmetadata = () => { window.clearTimeout(timer); done(Number.isFinite(v.duration) ? v.duration : null); };
    v.onerror = () => { window.clearTimeout(timer); done(null); };
    v.src = url;
  });
}

/** Home: the best cut playing with the loop that made it, the Meta models, every project as a 9:16 tile, and the new
 *  project sheet (the N key, the button, or a clip dropped anywhere on the page). */
export function Home({ go }: { go: (h: string) => void }) {
  const [runs, setRuns] = useState<RunCard[] | null>(null);
  const [hasKey, setHasKey] = useState(false);
  const [hasOpen, setHasOpen] = useState(false);
  const [keyNote, setKeyNote] = useState<string | null>(null);
  const [budget, setBudget] = useState<Budget | null>(null);
  const [costs, setCosts] = useState<Costs>(COSTS);
  const [footage, setFootage] = useState<Footage[]>([]);
  const [f, setF] = useState("");
  const [upload, setUpload] = useState<string | null>(null);
  const [limits, setLimits] = useState<Limits | null>(null);
  const [sheet, setSheet] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [sort, setSort] = usePref<Sort>("montivo.home.sort", "newest", ["newest", "best", "name"]);
  const [only, setOnly] = usePref<Only>("montivo.home.only", "all", ["all", "samples", "yours", "passed"]);
  const [q, setQ] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const [archived, setArchived] = useState<RunCard[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [toast, setToast] = useState<{ text: string; undo?: string; tone?: string } | null>(null);

  const loadRuns = () => api<{ runs: RunCard[]; has_key: boolean; has_open_key: boolean; costs: Costs; key_note?: string | null; budget?: Budget | null; limits?: Limits | null }>("/api/runs").then((r) => {
    const recv = performance.now();
    setRuns(r.runs.map((x) => (x.progress ? { ...x, progress: { ...x.progress, recv } } : x)));
    setHasKey(r.has_key); setHasOpen(r.has_open_key); if (r.costs) setCosts(r.costs);
    setKeyNote(r.key_note ?? null); setBudget(r.budget ?? null); setLimits(r.limits ?? null);
  });
  const loadArchived = () => api<{ runs: RunCard[] }>("/api/archive").then((r) => setArchived(r.runs)).catch(() => setArchived([]));
  const loadFootage = () => api<{ files: Footage[] }>("/api/footage").then((r) => { setFootage(r.files); if (!f && r.files[0]) setF(r.files[0].path); });
  useEffect(() => { loadRuns(); loadArchived(); loadFootage(); }, []); // eslint-disable-line
  const anyRunning = !!runs?.some((r) => r.job?.running);
  useEffect(() => {
    const h = window.setInterval(loadRuns, anyRunning ? POLL_MS : IDLE_POLL_MS);
    return () => window.clearInterval(h);
  }, [anyRunning]); // eslint-disable-line

  const put = async (file: File) => {
    // the hosted limits, checked before the upload: an 847 MB iPhone clip used to go up whole before the server said no
    const mb = file.size / 1e6;
    if (limits?.upload_mb && mb > limits.upload_mb) {
      setUpload(`${file.name} is ${Math.round(mb)} MB: the demo server takes clips up to ${limits.upload_mb} MB. Record in 1080p or trim it first.`);
      return;
    }
    const secs = limits?.footage_s ? await clipSeconds(file) : null;
    if (limits?.footage_s && secs && secs > limits.footage_s + 0.5) {
      setUpload(`${file.name} is ${Math.round(secs)} s: the demo server takes clips up to ${limits.footage_s} s. Trim it first.`);
      return;
    }
    setUpload(`Uploading ${file.name}…`);
    const safe = file.name.replace(/[^A-Za-z0-9._-]/g, "_").slice(-80);
    const name = /^[A-Za-z0-9]/.test(safe) ? safe : `clip${safe}`;
    const res = await fetch(`/api/footage/${name}`, { method: "PUT", body: file });
    const why = res.ok ? null : await res.json().then((b) => b.error, () => null);
    setUpload(res.ok ? null : `Upload failed (${res.status})${why ? `: ${why}` : ""}`);
    // the server picks a free name (clip-2.mp4 when clip.mp4 exists): earlier projects keep their source
    if (res.ok) { setF((await res.json()).path); loadFootage(); }
  };

  // N opens the sheet, / goes to the search, Esc closes the sheet; a clip dropped anywhere opens it with that clip
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "Escape" && sheet) { setSheet(false); return; }
      if (typing(e.target) || sheet) return;
      if (e.key === "n" || e.key === "N") { e.preventDefault(); setSheet(true); }
      else if (e.key === "/" && searchRef.current) { e.preventDefault(); searchRef.current.focus(); }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [sheet]);
  const hasFiles = (e: React.DragEvent) => Array.from(e.dataTransfer.types).includes("Files");

  const all = (runs ?? []).filter((r) => r.has_cut || r.job?.running); // a new project shows while it works on its first cut
  const hosted = !!budget;
  // the cut to show first: on the hosted demo a sample (or the judge's copy of one), the best passing one
  const featured = [...all].filter((r) => r.card?.score && (!hosted || isSample(r)))
    .sort((x, y) => Number(y.card!.score!.pass) - Number(x.card!.score!.pass) || y.card!.score!.overall - x.card!.score!.overall)[0] ?? null;
  const tabs: { id: Only; label: string }[] = hosted
    ? [{ id: "all", label: "All" }, { id: "samples", label: "Samples" }, { id: "yours", label: "Yours" }]
    : [{ id: "all", label: "All" }, { id: "passed", label: "Passed" }];
  const many = all.length >= FEW;
  const shown = useMemo(() => {
    const words = (many ? q : "").toLowerCase().split(/\s+/).filter(Boolean);
    const hay = (r: RunCard) => `${r.id} ${nameFor(r)} ${r.footage} ${r.brief}`.toLowerCase();
    const keep = (r: RunCard) => only === "all" || !tabs.some((t) => t.id === only)
      || (only === "samples" ? isSample(r) : only === "yours" ? r.kind === "own" : !!r.card?.scores.some((s) => s.pass));
    const out = all.filter((r) => keep(r) && words.every((w) => hay(r).includes(w)));
    const by: Record<Sort, (a: RunCard, b: RunCard) => number> = {
      newest: (a, b) => (b.started ?? "").localeCompare(a.started ?? "") || b.id.localeCompare(a.id),
      best: (a, b) => bestOf(b) - bestOf(a) || b.id.localeCompare(a.id),
      name: (a, b) => nameFor(a).localeCompare(nameFor(b)) || a.id.localeCompare(b.id),
    };
    return out.sort(by[many ? sort : "newest"]);
  }, [all, q, only, sort, many, hosted]); // eslint-disable-line
  const flash = (t: { text: string; undo?: string; tone?: string }) => { setToast(t); window.setTimeout(() => setToast((x) => (x === t ? null : x)), 7000); };
  const archive = async (id: string) => {
    try {
      await api(`/api/runs/${id}/archive`, {});
      flash({ text: `Archived ${id}: moved to runs/_archive, nothing deleted.`, undo: id });
    } catch (e) { flash({ text: (e as Error).message, tone: "error" }); }
    loadRuns(); loadArchived();
  };
  const restore = async (id: string) => {
    try {
      await api(`/api/archive/${id}/restore`, {});
      flash({ text: `Restored ${id}.` });
    } catch (e) { flash({ text: (e as Error).message, tone: "error" }); }
    loadRuns(); loadArchived();
  };

  return (
    <div className={`home ${dragging ? "dragging" : ""}`}
      onDragOver={(e) => { if (hasFiles(e)) { e.preventDefault(); setDragging(true); } }}
      onDragLeave={(e) => { if (e.currentTarget === e.target) setDragging(false); }}
      onDrop={(e) => {
        if (!hasFiles(e)) return;
        e.preventDefault(); setDragging(false);
        const file = e.dataTransfer.files[0];
        if (file) { setSheet(true); put(file); }
      }}>
      <header className="top hm-top">
        <a className="mark" href="#/"><img src="/montivo-lockup-dark.svg" alt="Montivo" /></a>
        <nav className="hm-nav" aria-label="Montivo">
          <a className="on" href="#/" aria-current="page">Projects</a>
          <a href={`${REPO}#how-the-loop-works`} target="_blank" rel="noreferrer">How it works</a>
          <a href={REPO} target="_blank" rel="noreferrer">GitHub</a>
        </nav>
        <span className="grow" />
        {budget && <BudgetMeter b={budget} />}
        <button className="btn hm-new" onClick={() => setSheet(true)}><Icon name="plus" size={14} />New project <kbd>N</kbd></button>
      </header>

      <main className="hm-body">
        {featured ? <Featured r={featured} hosted={hosted} costs={costs} /> : runs && <Intro onNew={() => setSheet(true)} />}

        <section className="hm-projects" aria-label="Projects">
          <div className="hm-bar">
            <h2>Projects</h2>
            <span className="mute">{shown.length < all.length ? `${shown.length} of ${all.length}` : all.length || ""}</span>
            <span className="grow" />
            {all.length > 1 && (
              <div className="seg-group">{tabs.map((o) => <button key={o.id} className={`seg ${only === o.id ? "on" : ""}`} onClick={() => setOnly(o.id)}>{o.label}</button>)}</div>
            )}
            {many && <>
              <label className="search"><Icon name="search" size={14} />
                <input ref={searchRef} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search" aria-label="Search the projects" />
                <kbd>/</kbd>
              </label>
              <select className="sort" value={sort} onChange={(e) => setSort(e.target.value as Sort)} title="Sort the projects">
                {SORTS.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
              </select>
            </>}
          </div>
          {!runs && <div className="hm-grid" aria-busy="true">{[0, 1, 2, 3].map((k) => <div key={k} className="hm-tile skeleton"><div className="hm-poster" /></div>)}</div>}
          {runs && !!all.length && !shown.length && <div className="mute">No project matches. <button className="link" onClick={() => { setQ(""); setOnly("all"); }}>Show all</button></div>}
          {runs && (
            <div className="hm-grid">
              <button className="hm-tile hm-newtile" onClick={() => setSheet(true)}>
                <div className="hm-poster">
                  <div><span className="hm-plus"><Icon name="plus" size={20} /></span><b>New project</b>
                    <span>Drop a talking-head clip{limits?.footage_s ? `, up to ${limits.footage_s} s` : ""}</span></div>
                </div>
              </button>
              {shown.map((r) => <Tile key={r.id} r={r} onArchive={() => archive(r.id)} />)}
            </div>
          )}
          {runs && !all.length && <div className="mute small">No run folder with a cut in runs/ yet: start one with New project.</div>}
          {!!archived.length && (
            <div className="archived">
              <button className="link" onClick={() => setShowArchived(!showArchived)}>
                <Icon name={showArchived ? "chevronDown" : "chevron"} size={13} /> Archived ({archived.length})
              </button>
              {showArchived && archived.map((r) => (
                <div key={r.id} className="arch-row">
                  <span className="arch-name">{nameFor(r)}</span>
                  <span className="mute">{r.id} · {r.footage}{r.card?.score ? ` · ${r.card.score.overall}` : ""}</span>
                  <span className="grow" />
                  <button className="btn quiet" onClick={() => restore(r.id)}>Restore</button>
                </div>
              ))}
            </div>
          )}
        </section>
      </main>

      {sheet && (
        <NewProject go={go} close={() => setSheet(false)} footage={footage} f={f} setF={setF} put={put} upload={upload}
          limits={limits} hasKey={hasKey} hasOpen={hasOpen} keyNote={keyNote} budget={budget} costs={costs} />
      )}
      {dragging && !sheet && <div className="hm-drop" aria-hidden><div><Icon name="plus" size={22} />Drop the clip to start a new project</div></div>}
      {toast && (
        <div className={`toast bottom ${toast.tone ?? ""}`}>
          {toast.text}{toast.undo && <> <button className="link" onClick={() => { const id = toast.undo!; setToast(null); restore(id); }}>Undo</button></>}
        </div>
      )}
    </div>
  );
}

/** The hosted demo's spend cap as a meter: what is left of the Meta model budget everyone on the server shares. */
function BudgetMeter({ b }: { b: Budget }) {
  const frac = b.cap > 0 ? Math.max(0, Math.min(1, b.left / b.cap)) : 0;
  return (
    <span className={`hm-budget ${frac < 0.2 ? "low" : ""}`} title="Demo budget for Meta model calls, shared by everyone on this server">
      <span className="hm-meter"><i style={{ width: `${frac * 100}%` }} /></span>
      {usd(b.left)} of {usd(b.cap)} left
    </span>
  );
}

/** The Meta models every run calls, each under its own mark. */
function Models() {
  return (
    <ul className="hm-models" aria-label="Built on Meta models">
      {MODELS.map((m) => (
        <li key={m.name} className={m.open ? "open" : ""}>
          <img src={m.logo} alt="" width={20} height={20} />
          <span><b>{m.name}</b> {m.does}</span>
          {m.open && <span className="open-tag">open weights</span>}
        </li>
      ))}
    </ul>
  );
}

/** No cut to show yet (a fresh install): what Montivo does and the models, with the way in. */
function Intro({ onNew }: { onNew: () => void }) {
  return (
    <section className="hm-hero hm-intro">
      <div className="hm-hero-r">
        <div className="hm-eyebrow">Agent-first video editor</div>
        <h1>Raw footage in, <em>a cut that reviewed itself</em> out.</h1>
        <p className="hm-sub">Muse Spark plans the edit from your footage, a blind reviewer scores the render against a rubric, and the agent re-cuts until the cut passes.</p>
        <div className="hm-acts"><button className="btn" onClick={onNew}><Icon name="plus" size={14} />New project</button></div>
        <Models />
      </div>
    </section>
  );
}

type Step = { v: Version; worst: string | null; worstWhat: string | null; sev: string | null; made: string | null; who: string | null };

/** The versions that led to the one a project opens on, reviewed ones only, with each one's worst note and what made it. */
function loopSteps(state: RunState, shown: number): Step[] {
  const vs = state.versions.filter((v) => v.cut);
  const path: Version[] = [];
  for (let v = vs.find((x) => x.n === shown); v; v = parentOf(v, vs)) {
    if (path.some((p) => p.n === v!.n)) break; // a restore can point back: never loop
    path.unshift(v);
  }
  const reviewed = path.filter((v) => v.review);
  const keep = reviewed.length > 3 ? [reviewed[0], reviewed[reviewed.length - 2], reviewed[reviewed.length - 1]] : reviewed;
  return keep.map((v, k) => {
    const issues = [...(v.review!.issues ?? []), ...(v.review!.gate?.issues ?? [])].sort((a, b) => sevRank(a.severity) - sevRank(b.severity) || a.t_s - b.t_s);
    const top = issues[0] ?? null;
    // what made this version: every version between the previous step and this one
    const from = k ? path.indexOf(keep[k - 1]) + 1 : path.length;
    const between = path.slice(from, path.indexOf(v) + 1);
    const made = between.map((x) => {
      if (x.origin.source === "review") {
        const f = x.origin.fixes ?? [];
        return f.length ? `${fixText(f[0].op, f[0].target)}${f.length > 1 ? ` +${f.length - 1}` : ""}` : "reviewer fix";
      }
      return originLabel(x).replace(/^your edit · /, "");
    });
    const agent = between.every((x) => x.origin.source === "review" || x.origin.source === "hook_ab");
    return { v, worst: top ? crit(top.criterion) : null, worstWhat: top?.what ?? null, sev: top?.severity ?? null,
      made: made.length ? made.slice(0, 2).join(" · ") : null, who: made.length ? (agent ? "Agent" : "You") : null };
  });
}

/** The best cut, playing, with the loop that made it: every reviewed version's score and worst note, and between two
 *  the fix that made the second. */
function Featured({ r, hosted, costs }: { r: RunCard; hosted: boolean; costs: Costs }) {
  const card = r.card!;
  const [state, setState] = useState<RunState | null>(null);
  const [paused, setPaused] = useState(reducedMotion); // WCAG 2.2.2: a loop that plays this long can be paused
  const video = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    let live = true;
    setState(null);
    api<RunState>(`/api/runs/${r.id}`).then((s) => { if (live) setState(s); }).catch(() => { /* the hero keeps the card's facts */ });
    return () => { live = false; };
  }, [r.id, card.shown]);
  useEffect(() => { const v = video.current; if (!v) return; if (paused) v.pause(); else v.play().catch(() => setPaused(true)); }, [paused]);
  const steps = state ? loopSteps(state, card.shown) : [];
  const first = steps[0]?.v.review?.overall ?? card.scores[0]?.overall;
  const last = card.score;
  const human = steps.some((s) => s.who === "You");
  const open = `#/run/${r.id}`;
  return (
    <section className="hm-hero">
      <div className="hm-feat">
        <video ref={video} src={fileUrl(r.id, `cut_v${card.shown}.mp4`)} poster={frameUrl(r.id, card.shown, card.poster_t, true)}
          autoPlay={!paused} muted loop playsInline preload="metadata" aria-label={`${nameFor(r)}, version ${card.shown}`} />
        <span className="hm-feat-v">Version {card.shown}{card.duration_s != null ? ` · ${tc(card.duration_s, 0)}` : ""}</span>
        {last && <Ring s={last} lg />}
        <button className="hm-feat-pp icon-btn" onClick={() => setPaused(!paused)} aria-label={paused ? "Play" : "Pause"} title={paused ? "Play" : "Pause"}>
          <Icon name={paused ? "play" : "pause"} size={15} />
        </button>
      </div>
      <div className="hm-hero-r">
        <div>
          <div className="hm-eyebrow">{hosted ? "Sample · open it and ask for a change" : "Best cut so far"}</div>
          <h1>Montivo checks its own cut, <em>then fixes it.</em></h1>
        </div>
        <p className="hm-sub">
          Muse Spark plans the edit from the footage, a blind reviewer scores the render against a rubric, and the agent
          re-cuts until the cut passes.{first != null && last && first !== last.overall
            ? ` ${nameFor(r)} went from ${first} to ${last.overall}${state && !human ? " with no human edit" : ""}.` : ""}
        </p>
        {steps.length > 1 && (
          <ol className="hm-loop" aria-label="The agent loop">
            {steps.map((s, k) => (
              <li key={s.v.n}>
                {k > 0 && (
                  <span className="hm-arrow">
                    <svg width="40" height="12" viewBox="0 0 40 12" aria-hidden><path d="M1 6h36m-5-4 5 4-5 4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
                    {s.made && <span title={s.made}><b>{s.who}</b> {s.made}</span>}
                  </span>
                )}
                <div className={`hm-lv ${k === steps.length - 1 ? "last" : ""} ${s.v.review!.pass ? "pass" : ""}`}>
                  <div className="hm-lv-top">
                    <img src={frameUrl(r.id, s.v.n, Math.min((s.v.duration_s ?? s.v.runtime_s) - 0.2, s.v.title_s + 1.2), true)} alt="" loading="lazy" />
                    <div>
                      <div className="hm-lv-v">v{s.v.n}{k === 0 ? " · first cut" : s.v.review!.pass ? " · passes" : ""}</div>
                      <div className={`hm-lv-score ${s.v.review!.pass ? "pass" : "fail"}`}>{s.v.review!.overall}</div>
                    </div>
                  </div>
                  <div className="hm-lv-note" title={s.worstWhat ?? undefined}>
                    <span className={`hm-dot ${s.sev ?? "ok"}`} />
                    {s.worst ? <span><b>{s.worst}</b> {s.worstWhat}</span> : <span>No open notes{s.v.review!.gate && !s.v.review!.gate.blocked ? ", gate clear" : ""}</span>}
                  </div>
                </div>
              </li>
            ))}
          </ol>
        )}
        <div className="hm-acts">
          <a className="btn" href={open}>Open in the editor</a>
          <a className="btn quiet" href={`${open}/loop`}>Watch the loop</a>
          {hosted && <span className="mute small">Your first edit makes your own copy. The sample stays as it is.</span>}
        </div>
        {hosted && (
          <p className="hm-try mute small">
            Then ask in the chat: “Make it 30 seconds” <span className="btn-cost">~{usd((costs.ask ?? 0.006) + costs.review)}</span> ·
            “Add some chill music” <span className="btn-cost">~{usd(costs.ask ?? 0.006)}</span> ·
            captions, style, music and timeline edits by hand <span className="btn-cost free">free</span>
          </p>
        )}
        <Models />
      </div>
    </section>
  );
}

/** A score as a ring: green when it passes the blind review and the gate, orange when it does not. */
function Ring({ s, lg }: { s: CardScore; lg?: boolean }) {
  return (
    <span className={`hm-ring ${s.pass ? "pass" : "fail"} ${lg ? "lg" : ""}`} style={{ ["--p" as string]: s.overall }}
      title={`Blind review of version ${s.v}: ${s.overall}/100${s.pass ? ", passes" : ", does not pass"}`}>
      <b>{s.overall}</b>
    </span>
  );
}

/** A project as a 9:16 tile: the cut plays on hover, its score, a bar per version (green passes, orange does not, grey
 *  not reviewed) and the first score to the best. */
function Tile({ r, onArchive }: { r: RunCard; onArchive: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [hot, setHot] = useState(false);
  const c = r.card;
  const scoreOf = new Map((c?.scores ?? []).map((s) => [s.v, s]));
  const first = c?.scores[0];
  const top = c?.scores.reduce<CardScore | null>((a, b) => (!a || b.overall >= a.overall ? b : a), null);
  const play = (on: boolean) => {
    setHot(on);
    const v = video.current;
    if (!v) return;
    if (on && !reducedMotion()) { v.currentTime = 0; v.play().catch(() => {}); } else v.pause();
  };
  const running = !!r.job?.running;
  return (
    <a className="hm-tile" href={`#/run/${r.id}`} onMouseEnter={() => play(true)} onMouseLeave={() => play(false)}
      onFocus={() => play(true)} onBlur={() => play(false)}>
      <div className="hm-poster">
        {c ? <>
          <img src={frameUrl(r.id, c.shown, c.poster_t, true)} alt="" loading="lazy" />
          <video ref={video} src={hot ? fileUrl(r.id, `cut_v${c.shown}.mp4`) : undefined} muted loop playsInline preload="none" className={hot ? "on" : ""} />
          {c.score && <Ring s={c.score} />}
          {c.duration_s != null && <span className="hm-dur mono">{tc(c.duration_s, 0)}</span>}
        </> : (
          <div className="hm-working"><MarkLoader size={48} label="Working on the first cut" /></div>
        )}
        {r.kind === "sample" && <span className="hm-tag sample">Sample</span>}
        {r.kind === "copy" && <span className="hm-tag">Your copy</span>}
        {r.progress?.active && <div className="hm-prog"><ProgressMini p={r.progress} /></div>}
        {r.kind !== "sample" && (
          <button className="icon-btn hm-archive" disabled={running} aria-label="Archive"
            title={running ? "A job is running on this project" : "Archive: move to runs/_archive (restorable, nothing is deleted)"}
            onClick={(e) => { e.preventDefault(); e.stopPropagation(); onArchive(); }}><Icon name="archive" size={15} /></button>
        )}
      </div>
      <div className="hm-name" title={r.brief}>{nameFor(r)}</div>
      {!!r.versions.length && (
        <div className="hm-vchain" aria-hidden>
          {r.versions.map((n) => { const s = scoreOf.get(n); return <i key={n} className={s ? (s.pass ? "pass" : "fail") : ""} />; })}
        </div>
      )}
      <div className="hm-meta">
        <span>{r.versions.length} version{r.versions.length === 1 ? "" : "s"}
          {first && top ? (top.overall !== first.overall ? ` · ${first.overall} → ${top.overall}` : ` · ${top.overall}`) : c ? " · not reviewed" : running ? " · working" : ""}</span>
        <span>{usd(r.cost_usd)}</span>
      </div>
    </a>
  );
}

/** The new project sheet: footage as thumbnails, the kind of video (fills the brief), the brief, the length, the
 *  advanced switches folded, and the cost, time and Create always in view. */
function NewProject({ go, close, footage, f, setF, put, upload, limits, hasKey, hasOpen, keyNote, budget, costs }: {
  go: (h: string) => void; close: () => void; footage: Footage[]; f: string; setF: (p: string) => void; put: (file: File) => void;
  upload: string | null; limits: Limits | null; hasKey: boolean; hasOpen: boolean; keyNote: string | null; budget: Budget | null; costs: Costs;
}) {
  const [kind, setKind] = useState<string | null>(null);
  const [brief, setBrief] = useState("");
  const [target, setTarget] = useState(40);
  const [hflip, setHflip] = useState(false);
  const [review, setReview] = useState(true);
  const [punch, setPunch] = useState(false);
  const [hookAb, setHookAb] = useState(false);
  const [planner, setPlanner] = useState<Planner>("spark");
  const [kits, setKits] = useState<SavedLook[]>([]);
  const [kit, setKitId] = useState<string | null>(() => { try { return localStorage.getItem("montivo.kit"); } catch { return null; } });
  const setKit = (x: string | null) => {
    setKitId(x);
    try { if (x) localStorage.setItem("montivo.kit", x); else localStorage.removeItem("montivo.kit"); } catch { /* this tab only */ }
  };
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => { panel.current?.focus(); }, []);
  // S3: the looks and brand kits this person saved in the Style panel; the first cut can start with one
  useEffect(() => { api<LooksInfo>("/api/looks").then((r) => setKits(r.saved)).catch(() => setKits([])); }, []);
  const picked = kits.find((x) => x.id === kit) ?? null;
  const clip = footage.find((x) => x.path === f) ?? null;
  const est = clip?.duration_s ? estimate(costs, clip.duration_s, { review, hookAb, open: planner === "open" }) : null;
  const ready = hasKey && !!f && !!brief.trim() && !busy;
  const start = async () => {
    if (!ready) return;
    askToNotify(); // the first cut takes minutes: a notification when it is ready
    setErr(null); setBusy(true);
    try {
      const r = await api<{ id: string }>("/api/runs", { footage: f, brief, target, hflip, review, planner, punch, hook_ab: review && hookAb,
        ...(picked ? { kit: picked.id } : {}) });
      go(`#/run/${r.id}`);
    } catch (e) { setErr((e as Error).message); setBusy(false); }
  };
  const on = [picked && `kit ${picked.name}`, review && "blind review loop", punch && "punch-ins", review && hookAb && "hook A/B", hflip && "mirrored",
    planner === "open" ? "Spark Open planner" : "Spark 1.3 planner"].filter(Boolean).join(" · ");
  const steps = ["Voice", "SAM 3.1", planner === "open" ? "Spark Open plan" : "Spark plan", "render", ...(review ? ["blind review"] : []), ...(review && hookAb ? ["hook A/B"] : [])];
  return (
    <div className="hm-sheet-wrap" onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}>
      <div className="hm-sheet" role="dialog" aria-modal="true" aria-labelledby="hm-sheet-h" tabIndex={-1} ref={panel}
        onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); start(); } }}>
        <div className="hm-sheet-h">
          <h2 id="hm-sheet-h">New project</h2>
          <span className="grow" />
          <button className="icon-btn" onClick={close} aria-label="Close"><Icon name="close" size={16} /></button>
        </div>
        <div className="hm-sheet-b">
          {!hasKey && <div className="warn">{keyNote ?? "MODEL_API_KEY is not set: finished projects open, and deleting or trimming a clip renders without a model, but a new project needs the key."}</div>}
          <div>
            <div className="hm-lbl">Footage <span>{limits?.footage_s || limits?.upload_mb
              ? `up to ${[limits.footage_s && `${limits.footage_s} s`, limits.upload_mb && `${limits.upload_mb} MB`].filter(Boolean).join(" and ")} on this server`
              : "clips in footage/"}</span></div>
            <div className="hm-clips">
              {footage.map((x) => (
                <button key={x.path} className={`hm-clip ${x.path === f ? "sel" : ""}`} onClick={() => setF(x.path)} aria-pressed={x.path === f} title={x.name}>
                  <video src={`/api/footage/${encodeURIComponent(x.name)}#t=1`} muted preload="metadata" playsInline />
                  <span className="mono">{x.name.replace(/\.[^.]+$/, "")}{x.duration_s ? ` · ${tc(x.duration_s, 0)}` : ""}</span>
                </button>
              ))}
              <label className="hm-clip hm-clip-drop">
                <span><b>Drop a clip</b>or browse</span>
                <input type="file" accept="video/*" onChange={(e) => { const file = e.target.files?.[0]; if (file) put(file); }} />
              </label>
            </div>
            {upload && <div className={`small ${/failed|takes/.test(upload) ? "warn" : "mute"}`}>{upload}</div>}
          </div>
          <div>
            <div className="hm-lbl">What kind of video <span>fills in the brief, you can edit it</span></div>
            <div className="hm-kinds">
              {PRESETS.map((p) => (
                <button key={p.label} className={`hm-kind ${kind === p.label ? "on" : ""}`} aria-pressed={kind === p.label}
                  onClick={() => { setKind(p.label); setBrief(p.brief); setTarget(p.target); }}><b>{p.label}</b>{p.hint}</button>
              ))}
            </div>
          </div>
          <label className="hm-field"><span className="hm-lbl">Brief</span>
            <textarea rows={4} value={brief} onChange={(e) => setBrief(e.target.value)} placeholder="35 to 45 s vertical reel: what this is and why it matters. Keep the hook…" />
          </label>
          <div>
            <div className="hm-lbl">Length</div>
            <div className="hm-len">
              {LENGTHS.map((s) => <button key={s} className={target === s ? "on" : ""} aria-pressed={target === s} onClick={() => setTarget(s)}>{s} s</button>)}
              <label className={!LENGTHS.includes(target) ? "on" : ""}>
                <input type="number" min={10} max={180} placeholder="other" value={LENGTHS.includes(target) ? "" : target}
                  onChange={(e) => { const n = Number(e.target.value); if (n > 0) setTarget(n); }} aria-label="Another target length in seconds" /> s
              </label>
            </div>
          </div>
          {kits.length > 0 && (
            <div>
              <div className="hm-lbl">Brand kit <span>a look you saved in the Style panel, with its music and title card</span></div>
              <div className="hm-len hm-kits">
                <button className={picked ? "" : "on"} aria-pressed={!picked} onClick={() => setKit(null)}>None</button>
                {kits.map((x) => {
                  const note = kitNote(x);
                  return (
                    <button key={x.id} className={picked?.id === x.id ? "on" : ""} aria-pressed={picked?.id === x.id}
                      onClick={() => setKit(x.id)} title={note ? `${x.name}: its look, ${note}` : `${x.name}: its caption look`}>
                      <i className="hm-kit-sw" aria-hidden="true" style={{ color: x.look.text, background: x.look.box || "#141414",
                        boxShadow: `inset 0 -3px 0 ${x.look.active === "none" ? x.look.text : x.look.active_bg}` }}>Aa</i>
                      {x.name}{note && <span className="mute"> · {note}</span>}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
          <details className="hm-adv">
            <summary><b>Advanced</b><span className="mute"> · {on}</span></summary>
            <div className="hm-adv-b">
              <label className="check"><input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} /> blind review loop</label>
              <label className="check" title="Zooms on stressed words and an alternating scale on jump cuts; ffmpeg only, $0">
                <input type="checkbox" checked={punch} onChange={(e) => setPunch(e.target.checked)} /> punch-ins</label>
              <label className={`check ${review ? "" : "off"}`} title="After the loop, a second opening line reviewed blind against the first; the better one is kept">
                <input type="checkbox" checked={review && hookAb} disabled={!review} onChange={(e) => setHookAb(e.target.checked)} /> hook A/B</label>
              <label className="check"><input type="checkbox" checked={hflip} onChange={(e) => setHflip(e.target.checked)} /> mirror (selfie footage)</label>
              <div className="hm-field"><span className="hm-lbl">Planner</span><PlannerPicker value={planner} onChange={setPlanner} hasOpen={hasOpen} costs={costs} hint /></div>
            </div>
          </details>
          {err && <div className="warn">{err}</div>}
        </div>
        <div className="hm-sheet-f">
          <div className="hm-est" title="From the clip's length, measured on runs 014 and 015; a review loop that needs a third pass costs a little more">
            <b>{est ? `About ${usd(est.usd)} · ${dur(est.s)}` : "Pick a clip to see the cost"}</b>
            {budget && hasKey && <span className="mute small">{usd(budget.left)} of the demo budget left</span>}
            <span className="hm-pipe">{steps.map((s) => <i key={s}>{s}</i>)}</span>
          </div>
          <span className="grow" />
          <button className="btn quiet" onClick={close}>Cancel</button>
          <button className="btn" disabled={!ready} onClick={start}
            title={!hasKey ? "Needs the model key" : !brief.trim() ? "Write a brief or pick a kind of video" : "Create project (⌘↵)"}>
            {busy ? <MarkLoader mono /> : null}Create project <kbd>⌘↵</kbd>
          </button>
        </div>
      </div>
    </div>
  );
}
