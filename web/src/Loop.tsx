import { useRef, useState } from "react";
import { crit, dur, fileUrl, frameUrl, tc, usd, useRun } from "./api";
import { chain, fixText, originKind, originLabel, parentOf, projectName, sevRank } from "./edit";
import { Icon } from "./icons";
import { GateNote } from "./Inspector";
import type { PlanSide, RunState, Version } from "./types";

/** The agent loop as one film strip: every version left to right, the frame, the blind score and the reviewer's
 *  worst notes, and between two versions the patch that made the second one (with the score each fix moved). */
export function LoopFilm({ id, replay, go }: { id: string; replay: number; go: (h: string) => void }) {
  const { state } = useRun(id, 0);
  const [all, setAll] = useState(true);
  if (!state) return <div className="empty">Opening project {id}…</div>;

  const base = `#/run/${id}`;
  const cuts = state.versions.filter((v) => v.cut);
  const shown = all ? cuts : cuts.filter((v) => originKind(v) !== "human");
  const reviews = cuts.filter((v) => v.review).length;
  const fixes = cuts.filter((v) => v.origin.source === "review").reduce((a, v) => a + (v.origin.fixes?.length ?? 0), 0);
  const best = state.loop?.best?.version;
  const first = cuts.find((v) => v.review);
  const top = cuts.filter((v) => v.review).reduce<Version | null>((a, b) => (!a || b.review!.overall > a.review!.overall ? b : a), null);

  return (
    <div className="loop-film">
      <header className="top">
        <button className="icon-btn" onClick={() => go(`${base}${replay ? `?replay=${replay}` : ""}`)} title="Back to the editor"><Icon name="back" /></button>
        <a className="mark" href="#/" title="All projects"><img src="/montivo-lockup-dark.svg" alt="Montivo" /></a>
        <span className="divider" />
        <div className="title-block">
          <span className="proj">{projectName(cuts.at(-1) ?? null, state.run.brief)}</span>
          <span className="saved">The agent loop · {cuts.length} versions</span>
        </div>
        <span className="grow" />
        <div className="seg-group">
          <button className={`seg ${all ? "on" : ""}`} onClick={() => setAll(true)}>Every version</button>
          <button className={`seg ${!all ? "on" : ""}`} onClick={() => setAll(false)} title="The planner, the reviewer's fixes and the hook A/B only">Agent only</button>
        </div>
        <button className="btn quiet" onClick={() => go(`${base}${replay ? `?replay=${replay}` : ""}`)}>Open the editor</button>
      </header>

      <div className="lf-sum">
        <span><b>Raw footage</b> {tc(state.media.duration, 0)}</span>
        <Icon name="chevron" size={14} />
        <span>{reviews} blind review{reviews === 1 ? "" : "s"}, {fixes} fix{fixes === 1 ? "" : "es"} applied by the agent</span>
        {first && top && <>
          <Icon name="chevron" size={14} />
          <span>score <b>{first.review!.overall}</b> → <b className={top.review!.pass ? "pass" : ""}>{top.review!.overall}</b>{top.review!.pass ? " pass" : ""}</span>
        </>}
        <span className="grow" />
        <span className="mute mono">{usd(state.ledger.total_cost_usd)} · {dur(state.ledger.wall_seconds)} of model and render time</span>
      </div>

      <div className="lf-strip">
        {shown.map((v, k) => (
          <div key={v.n} className="lf-step" style={{ animationDelay: `${k * 140}ms` }}>
            {k > 0 && <Link state={state} v={v} prev={shown[k - 1]}
              compare={(a, b) => go(`${base}/compare?a=${a}&b=${b}${replay ? `&replay=${replay}` : ""}`)} />}
            <Card id={id} state={state} v={v} best={v.n === best} />
          </div>
        ))}
        {!shown.length && <div className="empty">No rendered version yet.</div>}
      </div>
    </div>
  );
}

function Card({ id, state, v, best }: { id: string; state: RunState; v: Version; best: boolean }) {
  const video = useRef<HTMLVideoElement>(null);
  const r = v.review;
  const parent = parentOf(v, state.versions);
  const delta = r && parent?.review ? r.overall - parent.review.overall : null;
  const issues = r ? [...r.issues].sort((a, b) => sevRank(a.severity) - sevRank(b.severity) || a.t_s - b.t_s) : [];
  const at = (t: number) => { const el = video.current; if (!el) return; el.currentTime = Math.max(0, t - 0.5); el.play().catch(() => {}); };
  const poster = frameUrl(id, v.n, Math.min((v.duration_s ?? v.runtime_s) - 0.2, v.title_s + 1.2), true);
  return (
    <article className={`lf-card ${originKind(v)}`}>
      <div className="lfc-head">
        <span className="lfc-v">v{v.n}</span>
        <span className={`origin ${originKind(v)}`}>{originLabel(v)}</span>
        {best && <span className="ver-best">loop best</span>}
      </div>
      {v.planner && <div className="lfc-planner mute small">{v.planner.name}{v.planner.model.startsWith("meta-models/") ? " · open weights" : ""}</div>}
      <video ref={video} className="lfc-video" src={fileUrl(id, v.cut!)} poster={poster} preload="none" controls playsInline />
      <div className="lfc-score">
        {r ? <>
          <span className={`lfc-n ${r.pass ? "pass" : ""}`}>{r.overall}</span>
          <span className="lfc-of">/100{r.pass && <> · <Icon name="check" size={12} /> pass</>}</span>
          {delta ? <span className={`lfc-d ${delta > 0 ? "up" : "down"}`}>{delta > 0 ? `+${delta}` : delta}</span> : null}
        </> : <span className="mute">not reviewed</span>}
      </div>
      {r && <GateNote r={r} compact />}
      <div className="lfc-meta mute small">{v.spans.length} clips · {tc(v.duration_s ?? v.runtime_s)}{v.punch_ins.length ? ` · ${v.punch_ins.length} punch-ins` : ""}</div>
      {r && (
        <ul className="lfc-issues">
          {issues.slice(0, 3).map((i) => {
            const c = chain(i, v, state.versions);
            const fixed = c.applied && c.after != null && c.before != null && c.after > c.before;
            return (
              <li key={i.k}>
                <button onClick={() => at(i.t_s)} title={i.what}>
                  <span className={`sev ${i.severity}`}>{i.severity}</span>
                  <span className="lfi-crit">{crit(i.criterion)}</span>
                  <span className="mono mute">{tc(i.t_s)}</span>
                  {fixed && <span className="resolved"><Icon name="check" size={11} /> v{c.next!.n}</span>}
                </button>
              </li>
            );
          })}
          {issues.length > 3 && <li className="mute small">+{issues.length - 3} more</li>}
          {!issues.length && <li className="mute small">No notes.</li>}
        </ul>
      )}
    </article>
  );
}

const planner = (model: string) => (model.startsWith("meta-models/") ? "Muse Spark Open" : "Muse Spark 1.3");

/** What made `v`, in a few lines: the reviewer's fixes (each with the criterion score it moved), a human edit, a
 *  re-plan or a hook A/B; `who` names the author ("Agent", "Hook A/B", "You"). The Loop film's links and the Versions
 *  panel's cards (V1) show the same. */
export function whatChanged(state: RunState, v: Version): { who: string; lines: string[] } {
  const o = v.origin;
  const parent = parentOf(v, state.versions);
  const side = v.plan_compare?.[`v${v.n}`] as PlanSide | undefined;
  let who = "You", lines: string[] = [];
  if (o.source === "plan") {
    who = "Agent";
    lines = [`the first cut, planned by ${v.planner?.name ?? "Muse Spark"}`];
  } else if (o.source === "review") {
    who = "Agent";
    lines = (o.fixes ?? []).map((f) => {
      const pr = parent?.review;
      const issue = [...(pr?.issues ?? []), ...(pr?.gate?.issues ?? [])].find((i) => i.k === f.issue);
      const moved = issue ? ` · ${crit(issue.criterion)} ${pr!.scores[issue.criterion]} → ${v.review?.scores[issue.criterion] ?? "?"}` : "";
      return `${f.check ? "measured · " : ""}${fixText(f.op, f.target)}${moved}`;
    });
  } else if (o.source === "hook_ab") {
    who = "Hook A/B";
    const r = o.result;
    lines = [`opens on ${o.challenger ?? "another clip"}${o.why ? ` (${o.why})` : ""}`,
      r ? `${r.b.overall} vs ${r.a.overall}: kept v${r.kept}${r.judge ? ` (judge: ${r.judge.verdict})` : r.decisive ? "" : ", within the noise"}` : "reviewing…"];
  } else if (o.action === "replan") {
    const fresh = !o.instruction || o.instruction.startsWith("plan again from scratch");
    const one = v.plan_compare?.v1;
    lines = [fresh ? "a fresh plan from the same brief (planner A/B)" : o.instruction!,
      v.planner ? `${v.planner.name}${side?.plan_cost_usd != null ? ` · ${usd(side.plan_cost_usd, 3)} · ${side.plan_seconds?.toFixed(1)} s` : ""}` : "",
      one?.plan_cost_usd != null ? `v1 on ${planner(one.planner)}: ${usd(one.plan_cost_usd, 3)} · ${one.plan_seconds?.toFixed(1)} s` : ""].filter(Boolean);
  } else {
    const own = originLabel(v).replace(/^your edit · /, "");
    // a draft, a kit or a style edit has no label of its own: the patch's notes say what it did
    lines = own !== "your edit" ? [own] : o.instruction ? [o.instruction.replace(/\.$/, "")]
      : (o.fixes ?? []).map((f) => f.note ?? f.op).filter((x): x is string => !!x).slice(0, 4);
    if (!lines.length) lines = [own];
    if (o.action === "caption") lines = [`${o.fixes?.length ?? 0} caption line${o.fixes?.length === 1 ? "" : "s"} retexted`];
  }
  return { who, lines };
}

function Link({ state, v, prev, compare }: { state: RunState; v: Version; prev: Version; compare: (a: number, b: number) => void }) {
  const parent = parentOf(v, state.versions);
  const kind = originKind(v);
  const jump = parent && parent.n !== prev.n ? `from v${parent.n}` : null;
  const { who, lines } = whatChanged(state, v);
  return (
    <div className={`lf-link ${kind}`}>
      <span className="lfl-arrow"><Icon name="chevron" size={16} /></span>
      <span className="lfl-who">{who}{jump && <span className="mute"> · {jump}</span>}</span>
      {lines.map((l, k) => <span key={k} className="lfl-line" title={l}>{l}</span>)}
      <button className="lfl-cmp" onClick={() => compare((parent ?? prev).n, v.n)}
        title={`Version ${(parent ?? prev).n} and ${v.n} side by side, parked on a moment the reviewer flagged`}>
        <Icon name="compare" size={13} /> Compare
      </button>
    </div>
  );
}
