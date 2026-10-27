import { useEffect, useState } from "react";
import { api, usd } from "./api";
import { PlannerPicker } from "./Panels";
import { ProgressMini } from "./Progress";
import type { Costs, Planner, RunCard } from "./types";

type Footage = { path: string; name: string; mb: number };
const COSTS: Costs = { plan: 0.05, plan_open: 0.012, plan_s: 35, plan_open_s: 7, review: 0.035, run_per_s: 0.004 };
const POLL_MS = 2000; // while a project has a job running, its card follows the job
const IDLE_POLL_MS = 15000; // otherwise a slow look for a job started from another tab

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

  const loadRuns = () => api<{ runs: RunCard[]; has_key: boolean; has_open_key: boolean; costs: Costs }>("/api/runs").then((r) => {
    const recv = performance.now();
    setRuns(r.runs.map((x) => (x.progress ? { ...x, progress: { ...x.progress, recv } } : x)));
    setHasKey(r.has_key); setHasOpen(r.has_open_key); if (r.costs) setCosts(r.costs);
  });
  const load = () => {
    loadRuns();
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
  const shown = (runs ?? []).filter((r) => r.has_cut || r.job?.running); // a new project shows while it works on its first cut

  return (
    <div className="home">
      <header className="top">
        <a className="mark" href="#/"><img src="/montivo-lockup-dark.svg" alt="Montivo" /></a>
        <span className="brief">An agent-first video editor on Meta models: Spark plans and reviews its own cut blind, Voice keeps time, SAM runs the camera.</span>
      </header>
      <div className="home-body">
        <section className="runs">
          <div className="h">Projects</div>
          {!runs && <div className="mute">loading…</div>}
          {runs && !shown.length && <div className="mute">No run folder with a cut in runs/ yet. Start one, or unpack the sample run (README, “For judges”).</div>}
          <div className="run-grid">
            {shown.map((r) => (
              <a key={r.id} className="run-card" href={`#/run/${r.id}`}>
                <div className="rc-top"><span className="rc-id">{r.id}</span><span className="mute">{r.footage}</span></div>
                <div className="rc-brief">{r.brief}</div>
                <div className="rc-scores">
                  {r.scores.length ? r.scores.map((s, k) => <span key={k} className="rc-score">{k > 0 && <i>→</i>}{s}</span>) : <span className="mute">not reviewed</span>}
                  {r.stop && <span className="mute rc-stop">{r.stop.split(":")[0]}</span>}
                </div>
                {r.progress?.active && <ProgressMini p={r.progress} />}
                <div className="rc-foot mute">{r.versions.length ? `v${r.versions.join(" v")} · ` : ""}{usd(r.cost_usd)}{r.job?.running && !r.progress ? " · running" : ""}</div>
              </a>
            ))}
          </div>
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
    </div>
  );
}
