import { useEffect, useMemo, useState } from "react";
import { api, frameUrl, tc, usd } from "./api";
import { nameOf } from "./edit";
import { Icon } from "./icons";
import { PlannerPicker } from "./Panels";
import { ProgressMini } from "./Progress";
import type { CardScore, Costs, Planner, RunCard } from "./types";

type Footage = { path: string; name: string; mb: number };
const COSTS: Costs = { plan: 0.05, plan_open: 0.012, plan_s: 35, plan_open_s: 7, review: 0.035, run_per_s: 0.004 };
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

/** All run folders, and the new-run form (footage, brief, target, review loop). */
export function Home({ go }: { go: (h: string) => void }) {
  const [runs, setRuns] = useState<RunCard[] | null>(null);
  const [hasKey, setHasKey] = useState(false);
  const [hasOpen, setHasOpen] = useState(false);
  const [costs, setCosts] = useState<Costs>(COSTS);
  const [planner, setPlanner] = useState<Planner>("spark");
  const [footage, setFootage] = useState<Footage[]>([]);
  const [f, setF] = useState("");
  const [brief, setBrief] = useState("");
  const [target, setTarget] = useState(40);
  const [hflip, setHflip] = useState(false);
  const [review, setReview] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [upload, setUpload] = useState<string | null>(null);
  const [sort, setSort] = usePref<Sort>("montivo.home.sort", "newest", ["newest", "best", "name"]);
  const [only, setOnly] = usePref<Only>("montivo.home.only", "all", ["all", "reviewed", "passed"]);
  const [q, setQ] = useState("");
  const [archived, setArchived] = useState<RunCard[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [toast, setToast] = useState<{ text: string; undo?: string; tone?: string } | null>(null);

  const loadRuns = () => api<{ runs: RunCard[]; has_key: boolean; has_open_key: boolean; costs: Costs }>("/api/runs").then((r) => {
    const recv = performance.now();
    setRuns(r.runs.map((x) => (x.progress ? { ...x, progress: { ...x.progress, recv } } : x)));
    setHasKey(r.has_key); setHasOpen(r.has_open_key); if (r.costs) setCosts(r.costs);
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
    setUpload(`uploading ${file.name}…`);
    const name = file.name.replace(/[^A-Za-z0-9._-]/g, "_");
    const res = await fetch(`/api/footage/${name}`, { method: "PUT", body: file });
    setUpload(res.ok ? null : `upload failed (${res.status})`);
    if (res.ok) { setF(`footage/${name}`); load(); }
  };
  const start = async () => {
    setErr(null);
    try {
      const r = await api<{ id: string }>("/api/runs", { footage: f, brief, target, hflip, review, planner });
      go(`#/run/${r.id}`);
    } catch (e) { setErr((e as Error).message); }
  };
  const all = (runs ?? []).filter((r) => r.has_cut || r.job?.running); // a new project shows while it works on its first cut
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
          {!runs && <div className="mute">loading…</div>}
          {runs && !all.length && <div className="mute">No run folder with a cut in runs/ yet. Start one, or unpack the sample run (README, “For judges”).</div>}
          {runs && !!all.length && !shown.length && <div className="mute">No project matches. <button className="link" onClick={() => { setQ(""); setOnly("all"); }}>Show all</button></div>}
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
          {!hasKey && <div className="warn">MODEL_API_KEY is not set: finished projects open, and deleting or trimming a clip renders without a model, but a new project needs the key.</div>}
          <label>Footage
            <select value={f} onChange={(e) => setF(e.target.value)}>
              {footage.map((x) => <option key={x.path} value={x.path}>{x.name} · {x.mb} MB</option>)}
            </select>
          </label>
          <label className="drop-zone" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); const file = e.dataTransfer.files[0]; if (file) put(file); }}>
            {upload ?? "or drop a clip here (it goes to footage/)"}
            <input type="file" accept="video/*" onChange={(e) => { const file = e.target.files?.[0]; if (file) put(file); }} />
          </label>
          <label>Brief
            <textarea rows={5} value={brief} onChange={(e) => setBrief(e.target.value)} placeholder="35 to 45 s vertical reel: what this is and why it matters. Keep the hook…" />
          </label>
          <div className="row">
            <label>Target <input type="number" min={10} max={180} value={target} onChange={(e) => setTarget(Number(e.target.value))} /> s</label>
            <label className="check"><input type="checkbox" checked={hflip} onChange={(e) => setHflip(e.target.checked)} /> mirror (selfie footage)</label>
            <label className="check"><input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} /> blind review loop</label>
          </div>
          <div className="field">Planner
            <PlannerPicker value={planner} onChange={setPlanner} hasOpen={hasOpen} costs={costs} hint />
          </div>
          <div className="row">
            <span className="mute">about {usd(0.48 - (planner === "open" ? costs.plan - costs.plan_open : 0))} for a 150 s clip with the review loop (SAM is half of it)</span>
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
