import { useEffect, useMemo, useState } from "react";
import { api, dur, frameUrl, tc, usd } from "./api";
import { nameOf } from "./edit";
import { Icon, MarkLoader } from "./icons";
import { PlannerPicker } from "./Panels";
import { askToNotify, ProgressMini } from "./Progress";
import type { Budget, CardScore, Costs, Planner, RunCard } from "./types";

type Footage = { path: string; name: string; mb: number; duration_s: number | null };
const COSTS: Costs = { plan: 0.05, plan_open: 0.012, plan_s: 35, plan_open_s: 7, review: 0.035, run_per_s: 0.0026, run_fixed: 0.045,
  run_wall_fixed_s: 60, run_wall_per_s: 0.9, review_loop: 0.07, review_loop_s: 85, hook_ab: 0.04 };

/** Starting briefs for the common kinds of footage; the text stays editable. */
const PRESETS: { label: string; brief: string; target: number }[] = [
  { label: "Talking head", target: 40, brief: "35 to 45 s vertical reel: what this is and why it matters. Keep the hook in the first line, drop the pauses, the ums and any repeated take (keep the last one), end on the strongest line." },
  { label: "Interview", target: 45, brief: "40 to 50 s vertical highlight of this interview: open on the most surprising answer, keep two or three answers that tell one story, cut the questions unless the answer needs them, end on a line that lands." },
  { label: "Product demo", target: 35, brief: "30 to 40 s vertical demo: open on the result, then show how it works in the order it happens on screen, keep the moments the speaker points at something, end on what to do next." },
  { label: "Event recap", target: 30, brief: "25 to 35 s vertical recap of this event: open on the most energetic line, keep who is speaking and why it matters, drop greetings and logistics, end on the takeaway." },
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

type Sort = "newest" | "best" | "name";
type Only = "all" | "reviewed" | "passed";
const SORTS: { id: Sort; label: string }[] = [{ id: "newest", label: "Newest" }, { id: "best", label: "Best score" }, { id: "name", label: "Name" }];
const ONLY: { id: Only; label: string }[] = [{ id: "all", label: "All" }, { id: "reviewed", label: "Reviewed" }, { id: "passed", label: "Passed" }];

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

/** All run folders, and the new-run form (footage, brief, target, review loop). */
export function Home({ go }: { go: (h: string) => void }) {
  const [runs, setRuns] = useState<RunCard[] | null>(null);
  const [hasKey, setHasKey] = useState(false);
  const [hasOpen, setHasOpen] = useState(false);
  const [keyNote, setKeyNote] = useState<string | null>(null);
  const [budget, setBudget] = useState<Budget | null>(null);
  const [costs, setCosts] = useState<Costs>(COSTS);
  const [introGone, setIntroGone] = useState(() => { try { return localStorage.getItem("montivo.intro") === "gone"; } catch { return false; } });
  const [planner, setPlanner] = useState<Planner>("spark");
  const [footage, setFootage] = useState<Footage[]>([]);
  const [f, setF] = useState("");
  const [brief, setBrief] = useState("");
  const [target, setTarget] = useState(40);
  const [hflip, setHflip] = useState(false);
  const [review, setReview] = useState(true);
  const [punch, setPunch] = useState(false);
  const [hookAb, setHookAb] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [upload, setUpload] = useState<string | null>(null);
  const [limits, setLimits] = useState<Limits | null>(null);
  const [sort, setSort] = usePref<Sort>("montivo.home.sort", "newest", ["newest", "best", "name"]);
  const [only, setOnly] = usePref<Only>("montivo.home.only", "all", ["all", "reviewed", "passed"]);
  const [q, setQ] = useState("");
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
  const load = () => {
    loadRuns();
    loadArchived();
    api<{ files: Footage[] }>("/api/footage").then((r) => { setFootage(r.files); if (!f && r.files[0]) setF(r.files[0].path); });
  };
  useEffect(load, []); // eslint-disable-line
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
    setUpload(`uploading ${file.name}…`);
    const safe = file.name.replace(/[^A-Za-z0-9._-]/g, "_").slice(-80);
    const name = /^[A-Za-z0-9]/.test(safe) ? safe : `clip${safe}`;
    const res = await fetch(`/api/footage/${name}`, { method: "PUT", body: file });
    const why = res.ok ? null : await res.json().then((b) => b.error, () => null);
    setUpload(res.ok ? null : `upload failed (${res.status})${why ? `: ${why}` : ""}`);
    // the server picks a free name (clip-2.mp4 when clip.mp4 exists): earlier projects keep their source
    if (res.ok) { setF((await res.json()).path); load(); }
  };
  const start = async () => {
    askToNotify(); // the first cut takes minutes: a notification when it is ready
    setErr(null);
    try {
      const r = await api<{ id: string }>("/api/runs", { footage: f, brief, target, hflip, review, planner, punch, hook_ab: review && hookAb });
      go(`#/run/${r.id}`);
    } catch (e) { setErr((e as Error).message); }
  };
  const clip = footage.find((x) => x.path === f) ?? null;
  const est = clip?.duration_s ? estimate(costs, clip.duration_s, { review, hookAb, open: planner === "open" }) : null;
  const all = (runs ?? []).filter((r) => r.has_cut || r.job?.running); // a new project shows while it works on its first cut
  const sample = [...all].filter((r) => r.card?.score).sort((x, y) => Number(y.card!.score!.pass) - Number(x.card!.score!.pass)
    || y.card!.score!.overall - x.card!.score!.overall)[0] ?? all[0]; // the best passing cut is the one to start from
  const shown = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    const hay = (r: RunCard) => `${r.id} ${nameFor(r)} ${r.footage} ${r.brief}`.toLowerCase();
    const out = all.filter((r) => (only === "all" || (only === "reviewed" ? bestOf(r) >= 0 : !!r.card?.scores.some((s) => s.pass)))
      && words.every((w) => hay(r).includes(w)));
    const by: Record<Sort, (a: RunCard, b: RunCard) => number> = {
      newest: (a, b) => (b.started ?? "").localeCompare(a.started ?? "") || b.id.localeCompare(a.id),
      best: (a, b) => bestOf(b) - bestOf(a) || b.id.localeCompare(a.id),
      name: (a, b) => nameFor(a).localeCompare(nameFor(b)) || a.id.localeCompare(b.id),
    };
    return out.sort(by[sort]);
  }, [all, q, only, sort]); // eslint-disable-line
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
    <div className="home">
      <header className="top">
        <a className="mark" href="#/"><img src="/montivo-lockup-dark.svg" alt="Montivo" /></a>
        <span className="brief">An agent-first video editor on Meta models: Spark plans and reviews its own cut blind, Voice keeps time, SAM runs the camera.</span>
      </header>
      <div className="home-body">
        <section className="runs">
          <div className="runs-bar">
            <div className="h">Projects <span className="mute">{shown.length < all.length ? `${shown.length} of ${all.length}` : all.length || ""}</span></div>
            <span className="grow" />
            <label className="search"><Icon name="search" size={14} />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name, run, footage, brief" />
            </label>
            <div className="seg-group">{ONLY.map((o) => <button key={o.id} className={`seg ${only === o.id ? "on" : ""}`} onClick={() => setOnly(o.id)}>{o.label}</button>)}</div>
            <select className="sort" value={sort} onChange={(e) => setSort(e.target.value as Sort)} title="Sort the projects">
              {SORTS.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
            </select>
          </div>
          {!runs && <div className="mute"><MarkLoader /> Loading…</div>}
          {runs && !all.length && <div className="mute">No run folder with a cut in runs/ yet. Start one, or unpack the sample run (README, “For judges”).</div>}
          {runs && !!all.length && !shown.length && <div className="mute">No project matches. <button className="link" onClick={() => { setQ(""); setOnly("all"); }}>Show all</button></div>}
          {budget && !introGone && sample && (
            <div className="start-here">
              <div className="sh-text">
                <b>Start here</b>
                <span>Open <a href={`#/run/${sample.id}`}>{nameFor(sample)}</a>: the agent already cut it and its blind reviewer scored it. Then ask for a change in the chat, for example:</span>
                <ul>
                  <li>“Make it 30 seconds” <span className="btn-cost">~{usd((costs.ask ?? 0.006) + costs.review)}</span></li>
                  <li>“Add some chill music under it” <span className="btn-cost">~{usd(costs.ask ?? 0.006)}</span></li>
                  <li>Try another hook, in the Review panel <span className="btn-cost">~{usd(costs.hook_ab ?? 2 * costs.review)}</span></li>
                  <li>Captions, style, music and timeline edits by hand <span className="btn-cost free">free</span></li>
                </ul>
                <span className="mute small">Your first edit makes your own copy: the sample stays as it is. Every edit is a new version you can go back to.</span>
              </div>
              <div className="sh-acts">
                <a className="btn" href={`#/run/${sample.id}`}>Open the sample</a>
                <button className="link small" onClick={() => { setIntroGone(true); try { localStorage.setItem("montivo.intro", "gone"); } catch { /* private mode */ } }}>Hide</button>
              </div>
            </div>
          )}
          <div className="run-grid">
            {shown.map((r) => (
              <a key={r.id} className="run-card" href={`#/run/${r.id}`}>
                <button className="icon-btn rc-archive" disabled={!!r.job?.running}
                  title={r.job?.running ? "A job is running on this project" : "Archive: move to runs/_archive (restorable, nothing is deleted)"}
                  onClick={(e) => { e.preventDefault(); e.stopPropagation(); archive(r.id); }}><Icon name="archive" size={15} /></button>
                <div className="rc-poster">
                  {r.card && <img src={frameUrl(r.id, r.card.shown, r.card.poster_t, true)} alt="" loading="lazy" />}
                  {r.card?.duration_s != null && <span className="rc-dur mono">{tc(r.card.duration_s, 0)}</span>}
                </div>
                <div className="rc-body">
                  <div className="rc-name" title={r.brief}>{nameOf(r.card?.title_card_text, r.brief)}</div>
                  <div className="rc-meta mute"><span className="rc-id">{r.id}</span> · {r.footage}</div>
                  <div className="rc-brief">{r.brief}</div>
                  <div className="rc-scores">
                    {r.card?.score ? <Badge s={r.card.score} shown={r.card.score_is_shown} /> : <span className="mute">not reviewed</span>}
                    {r.card && r.card.scores.length > 1 && <Sparkline scores={r.card.scores} />}
                    {r.stop && <span className="mute rc-stop">{r.stop.split(":")[0]}</span>}
                  </div>
                  {r.progress?.active && <ProgressMini p={r.progress} />}
                  <div className="rc-foot mute">
                    {r.card ? `Version ${r.card.shown} of ${r.versions.length} · ` : r.versions.length ? `${r.versions.length} versions · ` : ""}
                    {usd(r.cost_usd)}{r.job?.running && !r.progress ? " · running" : ""}
                  </div>
                </div>
              </a>
            ))}
          </div>
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
        <section className="new-run">
          <div className="h">New project</div>
          {!hasKey && <div className="warn">{keyNote ?? "MODEL_API_KEY is not set: finished projects open, and deleting or trimming a clip renders without a model, but a new project needs the key."}</div>}
          {budget && hasKey && <div className="mute small">Demo budget for Meta model calls: {usd(budget.left)} of {usd(budget.cap)} left, shared by everyone on this server.</div>}
          <label>Footage
            <select value={f} onChange={(e) => setF(e.target.value)}>
              {footage.map((x) => <option key={x.path} value={x.path}>{x.name} · {x.duration_s ? `${tc(x.duration_s, 0)} · ` : ""}{x.mb} MB</option>)}
            </select>
          </label>
          {clip && (
            <div className="clip-preview">
              <video key={clip.path} src={`/api/footage/${encodeURIComponent(clip.name)}`} controls muted preload="metadata" playsInline />
              <span className="mute">{clip.duration_s ? `${tc(clip.duration_s, 0)} of footage` : "length unknown"} · {clip.mb} MB</span>
            </div>
          )}
          <label className="drop-zone" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); const file = e.dataTransfer.files[0]; if (file) put(file); }}>
            {upload ?? (limits?.upload_mb || limits?.footage_s
              ? `or drop a clip here (up to ${[limits.footage_s && `${limits.footage_s} s`, limits.upload_mb && `${limits.upload_mb} MB`].filter(Boolean).join(", ")})`
              : "or drop a clip here (it goes to footage/)")}
            <input type="file" accept="video/*" onChange={(e) => { const file = e.target.files?.[0]; if (file) put(file); }} />
          </label>
          <div className="presets">
            {PRESETS.map((p) => <button key={p.label} className="chip sel" title={p.brief} onClick={() => { setBrief(p.brief); setTarget(p.target); }}>{p.label}</button>)}
          </div>
          <label>Brief
            <textarea rows={5} value={brief} onChange={(e) => setBrief(e.target.value)} placeholder="35 to 45 s vertical reel: what this is and why it matters. Keep the hook…" />
          </label>
          <div className="row">
            <label>Target <input type="number" min={10} max={180} value={target} onChange={(e) => setTarget(Number(e.target.value))} /> s</label>
            <label className="check"><input type="checkbox" checked={hflip} onChange={(e) => setHflip(e.target.checked)} /> mirror (selfie footage)</label>
            <label className="check"><input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} /> blind review loop</label>
            <label className="check" title="Zooms on stressed words and an alternating scale on jump cuts; ffmpeg only, $0">
              <input type="checkbox" checked={punch} onChange={(e) => setPunch(e.target.checked)} /> punch-ins</label>
            <label className={`check ${review ? "" : "off"}`} title="After the loop, a second opening line reviewed blind against the first; the better one is kept">
              <input type="checkbox" checked={review && hookAb} disabled={!review} onChange={(e) => setHookAb(e.target.checked)} /> hook A/B</label>
          </div>
          <div className="field">Planner
            <PlannerPicker value={planner} onChange={setPlanner} hasOpen={hasOpen} costs={costs} hint />
          </div>
          <div className="row">
            <span className="mute" title="From the clip's length, measured on runs 014 and 015 (SAM at 5 fps is about half of it); a review loop that needs a third pass costs a little more">
              {est ? <>about {usd(est.usd)} and {dur(est.s)} for this {tc(clip!.duration_s!, 0)} clip</> : "pick a clip to see the cost"}
            </span>
            <span className="grow" />
            <button className="btn" disabled={!hasKey || !f || !brief.trim()} onClick={start}>Create project</button>
          </div>
          {err && <div className="warn">{err}</div>}
        </section>
      </div>
      {toast && (
        <div className={`toast bottom ${toast.tone ?? ""}`}>
          {toast.text}{toast.undo && <> <button className="link" onClick={() => { const id = toast.undo!; setToast(null); restore(id); }}>Undo</button></>}
        </div>
      )}
    </div>
  );
}

/** The blind score of the version the card shows, or the project's best (named) when that version has none. */
function Badge({ s, shown }: { s: CardScore; shown: boolean }) {
  return (
    <span className={`rc-badge ${s.pass ? "pass" : ""}`}
      title={`${shown ? "Blind review of the version the editor opens on" : `Best blind review, Version ${s.v}`}: ${s.overall}/100${s.pass ? ", passes" : ""}`}>
      {s.pass && <Icon name="check" size={12} />}<b>{s.overall}</b>{!shown && <span>v{s.v}</span>}
    </span>
  );
}

/** Every reviewed version's blind score in version order, 0 to 100 bottom to top; a passing review is a green dot. */
function Sparkline({ scores }: { scores: CardScore[] }) {
  const w = 76, h = 24, pad = 3;
  const x = (k: number) => pad + (k * (w - 2 * pad)) / Math.max(scores.length - 1, 1);
  const y = (o: number) => pad + (1 - o / 100) * (h - 2 * pad);
  return (
    <svg className="rc-spark" width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-label="Blind scores by version">
      <title>{scores.map((s) => `v${s.v} ${s.overall}`).join(" → ")}</title>
      <polyline points={scores.map((s, k) => `${x(k)},${y(s.overall)}`).join(" ")} />
      {scores.map((s, k) => <circle key={s.v} cx={x(k)} cy={y(s.overall)} r={k === scores.length - 1 ? 2.6 : 1.8} className={s.pass ? "pass" : ""} />)}
    </svg>
  );
}
