import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { api, crit, dur, fileUrl, frameUrl, tc, usd, useRun, type Seen } from "./api";
import { blockAt, chain, layout, parentOf, sevRank, type Block } from "./edit";
import { Player, type PlayerHandle } from "./Player";
import type { Issue, Job, LogLine, RunState, Stage, Version } from "./types";

type Props = { id: string; replay: number; go: (hash: string) => void };

export function Room({ id, replay, go }: Props) {
  const { state, error, lines, job, seen } = useRun(id, replay);
  const [sel, setSel] = useState<number | null>(null);
  const [span, setSpan] = useState<string | null>(null);
  const [t, setT] = useState(0);
  const [ledger, setLedger] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const player = useRef<PlayerHandle>(null);

  // in replay, a version exists once the log rendered it; live, once it is on disk
  const visible = useMemo(() => {
    if (!state) return [];
    return state.versions.filter((v) => v.cut && (!replay || seen.cuts.has(v.n)))
      .map((v) => (replay && !seen.reviews.has(v.n) ? { ...v, review: null } : v));
  }, [state, replay, seen]);

  const newest = visible.length ? visible[visible.length - 1].n : null;
  const best = state?.loop?.best?.version;
  const seenNewest = useRef<number | null>(null);
  useEffect(() => { // open on the loop's best; follow every version that appears after that (a job, the replay)
    if (!visible.length) return setSel(null);
    const grew = seenNewest.current != null && newest != null && newest > seenNewest.current;
    seenNewest.current = newest;
    if (grew || replay) return setSel(newest);
    setSel((s) => (s != null && visible.some((v) => v.n === s) ? s : best && visible.some((v) => v.n === best) ? best : newest));
  }, [visible.length, newest, best, replay]); // eslint-disable-line

  const v = visible.find((x) => x.n === sel) ?? null;
  const parent = v && state ? parentOf(v, state.versions) : undefined;
  const blocks = useMemo(() => (v && state ? layout(v, parent, state.words) : []), [v, parent, state]);

  if (error) return <div className="empty">Could not open run {id}: {error} <a href="#/">all runs</a></div>;
  if (!state) return <div className="empty">Opening run {id}…</div>;

  const busy = !!job?.running;
  const flash = (m: string) => { setToast(m); window.setTimeout(() => setToast(null), 5000); };
  const act = async (path: string, body: unknown) => {
    try { await api(`/api/runs/${id}/${path}`, body); } catch (e) { flash(String((e as Error).message)); }
  };
  const seek = (a: number, b?: number) => player.current?.seek(a, b);

  return (
    <div className="room">
      <TopBar state={state} replay={replay} go={go} onLedger={() => setLedger(true)} spent={replay ? replaySpent(lines) : null} />
      <aside className="left">
        <Rail state={state} lines={lines} job={job} seen={seen} replay={replay} />
        <Chat state={state} versions={visible} lines={lines} job={job} replay={replay} v={v} span={span} t={t}
          onSend={(text, chips, review) => act("replan", { instruction: text, chips, from: v?.n, review })}
          onStop={() => act("stop", {})} />
      </aside>
      <main className="centre">
        <div className="tabs">
          {visible.map((x) => (
            <button key={x.n} className={`tab ${x.n === sel ? "on" : ""} ${x.review?.pass ? "pass" : ""}`} onClick={() => setSel(x.n)}>
              <span className="tab-v">v{x.n}</span>
              <span className="tab-score">{x.review ? x.review.overall : "·"}</span>
              {x.review?.pass && <span className="tick">✓</span>}
              {x.origin.source === "human" && <span className="hand" title="made by a human action">✎</span>}
            </button>
          ))}
          {busy && <span className="tab ghost-tab"><span className="pulse" /> v{nextN(state)}</span>}
          <span className="grow" />
          {visible.length > 1 && v && (() => {
            const [a, b] = parent ? [parent.n, v.n] : [v.n, visible.find((x) => x.n > v.n)?.n ?? visible[0].n];
            return (
              <button className="btn quiet" onClick={() => go(`#/run/${id}/compare?a=${a}&b=${b}${replay ? `&replay=${replay}` : ""}`)}>
                Compare v{a} | v{b}
              </button>
            );
          })()}
        </div>
        {v?.cut ? (
          <Player ref={player} key={`${v.n}`} src={fileUrl(id, v.cut)} onTime={setT}
            bands={(v.review?.issues ?? []).map((i) => ({ a: i.t_s, b: Math.max(i.t_end_s, i.t_s + 0.3), sev: i.severity }))}
            marks={blocks.filter((b) => b.kind !== "ghost").map((b) => b.t0)} />
        ) : (
          <div className="player-wait">{busy || replay ? <><span className="pulse" /> waiting for the first render</> : "no cut rendered yet"}</div>
        )}
        {v && <VersionLine v={v} parent={parent} />}
      </main>
      <aside className="right">
        <ReviewPanel id={id} state={state} versions={visible} v={v} onSeek={seek} onPick={setSel} />
      </aside>
      <footer className="strip-wrap">
        <Strip blocks={blocks} v={v} t={t} span={span} onSpan={setSpan} onSeek={seek}
          canReview={state.has_key} busy={busy} costs={state.costs}
          onDrop={(s, review) => act("drop", { span: s, from: v?.n, review: review ? 1 : 0 })} />
      </footer>
      {ledger && <Ledger state={state} onClose={() => setLedger(false)} />}
      {toast && <div className="toast" onClick={() => setToast(null)}>{toast}</div>}
    </div>
  );
}

/** The version a running job is making: the newest EDL while it has no cut yet, else the one after it. */
function nextN(state: RunState): number {
  const last = state.versions.at(-1);
  return last ? (last.cut ? last.n + 1 : last.n) : 1;
}

// ------------------------------------------------------------------- top bar

/** In replay the pill counts up with the log: the dollars each model line reports, and the run's own clock. */
function replaySpent(lines: LogLine[]): { usd: number; s: number } {
  let usd = 0, s = 0, sessionEnd = 0, session = -1;
  for (const l of lines) {
    if (l.session !== session) { s += sessionEnd; sessionEnd = 0; session = l.session; }
    sessionEnd = l.t;
    if (/^(done:|review loop:|review: review_v\d+ overall|watch suggests|plan: speech)/.test(l.msg)) continue; // each call is counted once, on its spark: line
    const m = l.msg.match(/\$(\d+\.\d+)/);
    if (m) usd += Number(m[1]);
  }
  return { usd, s: s + sessionEnd };
}

function TopBar({ state, replay, go, onLedger, spent }: { state: RunState; replay: number; go: (h: string) => void; onLedger: () => void; spent: { usd: number; s: number } | null }) {
  const base = `#/run/${state.id}`;
  return (
    <header className="top">
      <a className="mark" href="#/">montivo</a>
      <span className="run-id">run {state.id}</span>
      <span className="brief" title={state.run.brief}>{state.run.brief}</span>
      <span className="models">
        {["Muse Voice", "SAM 3.1", "Muse Spark 1.3", "Muse Image"].map((m) => <span key={m} className="model">{m}</span>)}
      </span>
      <span className="replay">
        <span className="lbl">replay</span>
        {[0, 1, 4].map((s) => (
          <button key={s} className={`seg ${replay === s ? "on" : ""}`} onClick={() => go(s ? `${base}?replay=${s}` : base)}>
            {s ? `${s}x` : "off"}
          </button>
        ))}
      </span>
      <button className="pill" onClick={onLedger} title="ledger: every call, every stage">
        {spent ? <>{usd(spent.usd)} <span className="sep">·</span> {dur(spent.s)}</> : <>{usd(state.ledger.total_cost_usd)} <span className="sep">·</span> {dur(state.ledger.wall_seconds)}</>}
      </button>
    </header>
  );
}

// ------------------------------------------------------------------- stage rail

type Status = "done" | "running" | "pending" | "idle" | "error";

function stageStatus(st: Stage, lines: LogLine[], job: Job | null, seen: Seen, replay: number): { status: Status; note: string | null } {
  if (replay) {
    const mine = lines.filter((l) => l.stage === st.id);
    const note = mine.length ? mine[mine.length - 1].msg.replace(/^[a-z]+: /, "") : null;
    if (seen.running === st.id && !seen.ended) return { status: "running", note };
    return seen.stages.has(st.id) ? { status: "done", note: st.artefact ?? note } : { status: "pending", note: null };
  }
  if (job?.running) {
    const session = lines.length ? lines[lines.length - 1].session : -1;
    const cur = lines.filter((l) => l.session === session && l.stage && l.stage !== "meta");
    const last = cur.length ? cur[cur.length - 1].stage : null;
    const mine = cur.filter((l) => l.stage === st.id);
    const note = mine.length ? mine[mine.length - 1].msg.replace(/^[a-z]+: /, "") : null;
    if (last === st.id) return { status: "running", note };
    if (mine.length) return { status: "done", note: st.artefact ?? note };
    if (job.kind === "run") return { status: "pending", note: null };
  }
  if (job && !job.running && job.rc && lines.some((l) => l.stage === "error")) {
    const err = lines.filter((l) => l.stage === "error").at(-1);
    if (err && lines.filter((l) => l.session === err.session).some((l) => l.stage === st.id)) return { status: "error", note: err.msg.split("\n")[0] };
  }
  return { status: st.done ? "done" : "idle", note: st.artefact };
}

function Rail({ state, lines, job, seen, replay }: { state: RunState; lines: LogLine[]; job: Job | null; seen: Seen; replay: number }) {
  const [open, setOpen] = useState<string | null>(null);
  const [full, setFull] = useState<LogLine[] | null>(null);
  useEffect(() => { setFull(null); }, [state.id]);
  const toggle = (sid: string) => {
    setOpen((o) => (o === sid ? null : sid));
    if (!full) api<{ lines: LogLine[] }>(`/api/runs/${state.id}/log${replay ? "?replay=1" : ""}`).then((r) => setFull(r.lines)).catch(() => setFull([]));
  };
  const source = replay ? lines : [...(full ?? []), ...lines.filter((l) => !full || l.i >= full.length)];
  return (
    <section className="rail">
      <div className="rail-head"><span className="h">Agent</span><span className="mute">{replay ? `replay ${replay}x` : job?.running ? job.label : "idle"}</span></div>
      {state.stages.map((st) => {
        const { status, note } = stageStatus(st, lines, job, seen, replay);
        const mine = source.filter((l) => l.stage === st.id);
        return (
          <Fragment key={st.id}>
            <button className={`stage ${status}`} onClick={() => toggle(st.id)}>
              <span className="dot" />
              <span className="st-name">{st.label}</span>
              <span className="st-model">{st.model}</span>
              <span className="st-cost">{status === "done" && st.cost_usd > 0 ? usd(st.cost_usd, 3) : ""}</span>
              <span className="st-note">{status === "pending" ? "" : note ?? ""}</span>
            </button>
            {open === st.id && (
              <div className="stage-log">
                {mine.length ? mine.slice(-40).map((l) => <div key={`${l.session}-${l.i}`}><span className="lt">{l.t.toFixed(1)}s</span>{l.msg}</div>)
                  : <div className="mute">{full ? "no log lines for this stage" : "loading…"}</div>}
              </div>
            )}
          </Fragment>
        );
      })}
    </section>
  );
}

// ------------------------------------------------------------------- chat

type Msg = { who: "you" | "agent" | "tool" | "reviewer"; text: string; chips?: string[]; v?: number; tone?: string };

function story(state: RunState, versions: Version[], lines: LogLine[], job: Job | null, replay: number): Msg[] {
  const out: Msg[] = [{ who: "you", text: state.run.brief ?? "", chips: [state.run.footage?.split("/").pop() ?? "footage", `${state.run.target_s ?? 40} s`] }];
  for (const v of versions) {
    const o = v.origin;
    if (o.source === "human") {
      out.push({ who: "you", text: o.action === "drop" ? `Drop ${o.fixes?.[0]?.target ?? "a span"}.` : (o.instruction ?? "Re-plan."), chips: [`v${o.from}`], v: v.n });
      out.push({ who: "tool", text: o.fixes?.map((f) => `${f.op} ${f.target}: ${f.note ?? ""}`).join("\n") ?? "", v: v.n });
    } else if (o.source === "review") {
      out.push({ who: "tool", text: (o.fixes ?? []).map((f) => `${f.op} ${f.target}: ${f.note ?? ""}`).join("\n") || "patched", v: v.n });
    }
    out.push({ who: "agent", text: `v${v.n} rendered: ${v.spans.length} spans, ${tc(v.duration_s ?? v.runtime_s)}${v.loudness ? `, ${v.loudness.integrated_lufs} LUFS` : ""}.`, v: v.n });
    if (v.review) {
      const r = v.review;
      const top = [...r.issues].sort((a, b) => sevRank(a.severity) - sevRank(b.severity))[0];
      out.push({ who: "reviewer", text: `v${v.n}: ${r.overall}${r.pass ? " pass" : ""}. ${top ? `${top.severity} at ${tc(top.t_s)}: ${top.what}` : r.summary}`, v: v.n, tone: r.pass ? "pass" : "" });
    }
    if (!replay && state.loop?.stop && v.n === state.loop.passes.at(-1)?.version) out.push({ who: "agent", text: `Loop stopped: ${state.loop.stop}.` });
  }
  if (job?.running) {
    const session = lines.length ? lines[lines.length - 1].session : -1;
    const notable = lines.filter((l) => l.session === session && /^(patch v|review: review_v\d+ overall|plan: |edges: \d+ cut edges|render: \S*cut_v)/.test(l.msg));
    for (const l of notable.slice(-6)) out.push({ who: "tool", text: l.msg });
  }
  if (job && !job.running && job.rc) out.push({ who: "agent", text: `${job.label} failed (exit ${job.rc}).\n${job.tail.split("\n").slice(-4).join("\n")}`, tone: "error" });
  return out;
}

function Chat({ state, versions, lines, job, replay, v, span, t, onSend, onStop }: {
  state: RunState; versions: Version[]; lines: LogLine[]; job: Job | null; replay: number; v: Version | null;
  span: string | null; t: number; onSend: (text: string, chips: string[], review: number) => void; onStop: () => void;
}) {
  const [text, setText] = useState("");
  const [useSpan, setUseSpan] = useState(true);
  const [useTime, setUseTime] = useState(false);
  const [review, setReview] = useState(true);
  const msgs = story(state, versions, lines, job, replay);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs.length]);
  const chips = [...(span && useSpan ? [span] : []), ...(useTime ? [`@${tc(t)}`] : [])];
  const cost = state.costs.plan + (review ? state.costs.review : 0);
  const blocked = !state.has_key ? "Re-plan calls Muse Spark: set MODEL_API_KEY and restart montivo-ui" : job?.running ? `${job.label} is running` : replay ? "replay mode" : null;
  const send = () => { if (!text.trim() || blocked) return; onSend(text.trim(), chips, review ? 1 : 0); setText(""); };
  return (
    <section className="chat">
      <div className="msgs">
        {msgs.map((m, k) => (
          <div key={k} className={`msg ${m.who} ${m.tone ?? ""}`}>
            <span className="who">{m.who === "you" ? "You" : m.who === "reviewer" ? "Reviewer · blind" : m.who === "tool" ? "patch" : "Montivo"}</span>
            <span className="txt">{m.text}</span>
            {m.chips && <span className="chips">{m.chips.map((c) => <span key={c} className="chip">{c}</span>)}</span>}
          </div>
        ))}
        {job?.running && <div className="msg agent"><span className="who">Montivo</span><span className="txt"><span className="pulse" /> {job.label} running… <button className="link" onClick={onStop}>stop</button></span></div>}
        <div ref={end} />
      </div>
      <div className="composer">
        <div className="chips">
          {span && <button className={`chip sel ${useSpan ? "on" : ""}`} onClick={() => setUseSpan(!useSpan)}>{span}</button>}
          <button className={`chip sel ${useTime ? "on" : ""}`} onClick={() => setUseTime(!useTime)}>@{tc(t)}</button>
          {v && <span className="chip">from v{v.n}</span>}
        </div>
        <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder={blocked ?? "Re-run with an instruction: “drop the agency line”, “make it 30 seconds”"}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} disabled={!!blocked} rows={2} />
        <div className="send-row">
          <label className="check"><input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} /> review the result</label>
          <span className="grow" />
          <span className="cost">~{usd(cost)}</span>
          <button className="btn" disabled={!!blocked || !text.trim()} onClick={send}>Re-plan ⏎</button>
        </div>
      </div>
    </section>
  );
}

// ------------------------------------------------------------------- centre

function VersionLine({ v, parent }: { v: Version; parent?: Version }) {
  const o = v.origin;
  const how = o.source === "plan" ? "planned by Spark" : o.source === "human" ? (o.action === "drop" ? `human drop from v${o.from}` : `human re-plan from v${o.from}`) : `patched from v${o.from} by the review`;
  return (
    <div className="vline">
      <span><b>v{v.n}</b> {how}</span>
      <span>{v.spans.length} spans · {tc(v.duration_s ?? v.runtime_s)}{parent?.duration_s && v.duration_s ? ` (${(v.duration_s - parent.duration_s >= 0 ? "+" : "")}${(v.duration_s - parent.duration_s).toFixed(1)} s)` : ""}</span>
      {v.loudness && <span>{v.loudness.integrated_lufs} LUFS · {v.loudness.true_peak_dbtp} dBTP</span>}
      {v.title_card ? <span>title “{v.title_card_text}”</span> : <span className="mute">no title card</span>}
    </div>
  );
}

// ------------------------------------------------------------------- review panel

function ReviewPanel({ id, state, versions, v, onSeek, onPick }: {
  id: string; state: RunState; versions: Version[]; v: Version | null; onSeek: (a: number, b?: number) => void; onPick: (n: number) => void;
}) {
  const reviewed = versions.filter((x) => x.review);
  const r = v?.review ?? null;
  const prevScores = v ? parentOf(v, state.versions)?.review?.scores : undefined;
  return (
    <section className="review">
      <div className="rv-head">
        <span className="h">Review</span>
        <span className="badge" title="The reviewer gets the rendered mp4, the brief, the rubric and three measured facts. Never the plan or the EDL.">blind</span>
        <span className="grow" />
        <span className="mute" title="Measured: the same cut reviewed twice can differ by about 10 points.">±10 between reviews</span>
      </div>
      <div className="history">
        {reviewed.length ? reviewed.map((x, k) => (
          <Fragment key={x.n}>
            {k > 0 && <span className="arrow">→</span>}
            <button className={`score ${x.n === v?.n ? "on" : ""} ${x.review!.pass ? "pass" : ""}`} onClick={() => onPick(x.n)}>
              <span className="sv">v{x.n}</span><span className="sn">{x.review!.overall}</span>
            </button>
          </Fragment>
        )) : <span className="mute">no review yet</span>}
        {state.loop?.stop && reviewed.length > 0 && <span className="stop">stop: {state.loop.stop.split(":")[0]}</span>}
      </div>
      {!r && v && <div className="mute pad">v{v.n} has not been reviewed.</div>}
      {r && v && (
        <div className="rv-body">
          <div className="facts">
            <span className="fact" title={r.facts_text}>silence gaps <b>{r.facts.silences}</b></span>
            <span className="fact" title={r.facts_text}>captions over the face <b>{r.facts.over_face}</b></span>
            <span className="fact" title={r.facts_text}>loud cut edges <b>{r.facts.loud_edges}/{r.facts.cut_edges}</b></span>
          </div>
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
          <div className="issues">
            {[...r.issues].sort((a, b) => sevRank(a.severity) - sevRank(b.severity) || a.t_s - b.t_s).map((i) => (
              <IssueCard key={i.k} id={id} issue={i} v={v} all={state.versions} onSeek={onSeek} />
            ))}
            {!r.issues.length && <div className="mute pad">No issues: {r.pass ? "the cut passes." : "nothing flagged."}</div>}
          </div>
          <div className="rv-cost mute">review {usd(r.cost_usd, 3)} · {r.seconds?.toFixed(0)} s · {r.duration_s?.toFixed(1)} s watched</div>
        </div>
      )}
    </section>
  );
}

function IssueCard({ id, issue, v, all, onSeek }: { id: string; issue: Issue; v: Version; all: Version[]; onSeek: (a: number, b?: number) => void }) {
  const c = chain(issue, v, all);
  const fixOp = c.planned ? `${c.planned.op} ${c.planned.target}` : issue.fix_op && issue.fix_op !== "none" ? issue.fix_op : null;
  const resolved = c.applied && c.after != null && c.before != null && c.after > c.before;
  const loop = () => onSeek(Math.max(0, issue.t_s - 1), issue.t_end_s + 0.5);
  return (
    <div className={`issue ${issue.severity}`}>
      <div className="i-top">
        <button className="time" onClick={loop} title="play this moment">{tc(issue.t_s)}</button>
        <span className={`sev ${issue.severity}`}>{issue.severity}</span>
        <span className="crit">{crit(issue.criterion)}</span>
        {resolved && <span className="resolved">resolved in v{c.next!.n}</span>}
      </div>
      <div className="i-body">
        <button className="frame" onClick={loop} title="the frame the reviewer saw (its clock is burned in)">
          <img src={frameUrl(id, v.n, issue.t_s + 0.1)} alt={`v${v.n} at ${tc(issue.t_s)}`} loading="lazy" />
        </button>
        <ol className="steps">
          <li><span className="k">saw</span><span>{issue.what}</span></li>
          <li><span className="k">op</span>{fixOp ? <code>{fixOp}</code> : <span className="mute">none proposed</span>}</li>
          <li><span className="k">patch</span>{c.applied ? <span>{c.applied.note}</span>
            : c.skipped ? <span className="mute">skipped: {c.skipped.reason}</span>
            : c.planned ? <span className="mute">planned, not applied (the loop stopped)</span> : <span className="mute">–</span>}</li>
          <li><span className="k">result</span>{c.next && c.after != null ? <span>{crit(issue.criterion)} {c.before} → <b>{c.after}</b> in v{c.next.n}</span> : <span className="mute">–</span>}</li>
        </ol>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------- paper edit strip

function Strip({ blocks, v, t, span, onSpan, onSeek, onDrop, canReview, busy, costs }: {
  blocks: Block[]; v: Version | null; t: number; span: string | null; onSpan: (s: string | null) => void;
  onSeek: (a: number, b?: number) => void; onDrop: (s: string, review: boolean) => void; canReview: boolean; busy: boolean;
  costs: RunState["costs"];
}) {
  const [review, setReview] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const issues = v?.review?.issues ?? [];
  const playing = blockAt(blocks, t);
  useEffect(() => { // keep the playing block in view
    const el = scroller.current?.querySelector(".block.playing") as HTMLElement | null;
    el?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
  }, [playing?.key]);
  if (!v) return <div className="strip empty-strip mute">The paper edit appears when the first version is planned.</div>;
  const selected = v.spans.find((s) => s.id === span);
  return (
    <div className="strip">
      <div className="strip-head">
        <span className="h">Paper edit</span>
        <span className="mute">v{v.n} · struck = cut from v{v.origin.from ?? v.n - 1} · pins = review issues at cut time</span>
        <span className="grow" />
        {selected ? (
          <>
            <span className="sel-txt">{selected.id} · {selected.role} · {selected.duration.toFixed(1)} s</span>
            <label className="check" title={canReview ? "" : "needs MODEL_API_KEY"}><input type="checkbox" checked={review && canReview} disabled={!canReview} onChange={(e) => setReview(e.target.checked)} /> then review ~{usd(costs.review)}</label>
            <button className="btn danger" disabled={busy || v.spans.length < 2} onClick={() => onDrop(selected.id, review && canReview)}>
              Drop {selected.id} → new version {review && canReview ? "" : "· $0"}
            </button>
            <button className="btn quiet" onClick={() => onSpan(null)}>✕</button>
          </>
        ) : <span className="mute">select a block to drop it</span>}
      </div>
      <div className="blocks" ref={scroller}>
        {blocks.map((b) => {
          const len = Math.max(b.t1 - b.t0, 0.001);
          const pins = b.kind === "ghost" ? [] : issues.filter((i) => i.t_s >= b.t0 && (i.t_s < b.t1 || (b === blocks.filter((x) => x.kind !== "ghost").at(-1) && i.t_s >= b.t1)));
          const grow = b.kind === "ghost" ? 0 : Math.max(len, 1.2);
          const isSel = b.span && b.span.id === span && b.kind === "span";
          return (
            <div key={b.key} className={`block ${b.kind} ${b.span?.role ?? ""} ${isSel ? "sel" : ""} ${playing === b ? "playing" : ""}`}
              style={{ flexGrow: grow, flexBasis: b.kind === "ghost" ? 140 : `${grow * 34}px` }}
              onClick={() => { if (b.kind === "span") { onSpan(b.span!.id); onSeek(b.t0 + 0.02); } else if (b.kind === "title") onSeek(0); }}>
              <div className="pins">
                {pins.map((i) => (
                  <button key={i.k} className={`pin ${i.severity}`} style={{ left: `${Math.min(100, ((i.t_s - b.t0) / len) * 100)}%` }}
                    title={`${tc(i.t_s)} ${i.severity} ${crit(i.criterion)}: ${i.what}`}
                    onClick={(e) => { e.stopPropagation(); onSeek(Math.max(0, i.t_s - 1), i.t_end_s + 0.5); }} />
                ))}
                {playing === b && <span className="head" style={{ left: `${Math.min(100, ((t - b.t0) / len) * 100)}%` }} />}
              </div>
              <div className="b-top">
                <span className="b-id">{b.kind === "title" ? "title" : b.label}</span>
                {b.span && b.kind === "span" && <span className="b-role">{b.span.role}</span>}
                {b.kind === "ghost" && <span className="b-role">dropped</span>}
                <span className="b-t">{b.kind === "ghost" ? "" : tc(b.t0)}</span>
              </div>
              <div className="b-words">
                {b.kind === "title" ? <span className="w title-w">{b.label}</span> : <>
                  {b.before.map((w) => <s key={`b${w.i}`} className="w cut">{w.w + " "}</s>)}
                  {b.kind === "ghost" ? b.kept.map((w) => <s key={w.i} className="w cut">{w.w + " "}</s>) : b.kept.map((w) => <span key={w.i} className="w">{w.w + " "}</span>)}
                  {b.after.map((w) => <s key={`a${w.i}`} className="w cut">{w.w + " "}</s>)}
                </>}
              </div>
              {b.span?.callout && b.kind === "span" && <div className="b-callout">callout · {b.span.callout}</div>}
              {b.span?.why && b.kind === "span" && <div className="b-why" title={b.span.why}>{b.span.why}</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------- ledger drawer

function Ledger({ state, onClose }: { state: RunState; onClose: () => void }) {
  const rows = Object.entries(state.ledger.stages);
  return (
    <div className="drawer-bg" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head"><span className="h">Ledger · run {state.id}</span><button className="btn quiet" onClick={onClose}>✕</button></div>
        <p className="mute">Every model call and ffmpeg pass on this run folder, from ledger.json. It includes every review and re-render made while building the engine on this footage.</p>
        <table className="ledger">
          <thead><tr><th>stage</th><th>calls</th><th>tokens in / out</th><th>seconds</th><th>cost</th></tr></thead>
          <tbody>
            {rows.map(([k, s]) => (
              <tr key={k}><td>{k}</td><td>{s.calls}</td><td>{s.tokens_in || s.tokens_out ? `${(s.tokens_in / 1000).toFixed(1)}K / ${(s.tokens_out / 1000).toFixed(1)}K` : "–"}</td>
                <td>{s.seconds.toFixed(1)}</td><td>{usd(s.cost_usd, 4)}</td></tr>
            ))}
          </tbody>
          <tfoot><tr><td>total</td><td /><td /><td>{dur(state.ledger.wall_seconds)}</td><td>{usd(state.ledger.total_cost_usd, 4)}</td></tr></tfoot>
        </table>
      </div>
    </div>
  );
}
