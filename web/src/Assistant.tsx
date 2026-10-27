import { useEffect, useRef, useState } from "react";
import { api, tc, usd, type Seen } from "./api";
import { sevRank } from "./edit";
import { Icon } from "./icons";
import { PlannerPicker } from "./Panels";
import { ProgressCard } from "./Progress";
import type { Job, LogLine, PlanSide, Planner, Progress, RunState, Stage, Version } from "./types";

// ------------------------------------------------------------------- pipeline checklist

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

const STAGE_TEXT: Record<string, string> = {
  ingest: "Read the footage",
  voice: "Transcribe with word timings",
  track: "Track the speaker",
  watch: "Watch the footage",
  plan: "Plan the edit",
  edges: "Tighten every cut to the speech",
  render: "Render the vertical cut",
  review: "Review the render, blind",
};

function Pipeline({ state, lines, job, seen, replay }: { state: RunState; lines: LogLine[]; job: Job | null; seen: Seen; replay: number }) {
  const [open, setOpen] = useState<string | null>(null);
  const [folded, setFolded] = useState(false);
  const [full, setFull] = useState<LogLine[] | null>(null);
  useEffect(() => { setFull(null); }, [state.id]);
  const rows = state.stages.map((st) => ({ st, ...stageStatus(st, lines, job, seen, replay) }));
  const done = rows.filter((r) => r.status === "done").length;
  const running = rows.find((r) => r.status === "running");
  const toggle = (sid: string) => {
    setOpen((o) => (o === sid ? null : sid));
    if (!full) api<{ lines: LogLine[] }>(`/api/runs/${state.id}/log${replay ? "?replay=1" : ""}`).then((r) => setFull(r.lines)).catch(() => setFull([]));
  };
  const source = replay ? lines : [...(full ?? []), ...lines.filter((l) => !full || l.i >= full.length)];
  return (
    <div className="pipeline">
      <button className="pl-head" onClick={() => setFolded(!folded)}>
        <Icon name={folded ? "chevron" : "chevronDown"} size={14} />
        <span className="pl-title">{running ? STAGE_TEXT[running.st.id] ?? running.st.label : done === rows.length ? "Edit planned, rendered and reviewed" : "Pipeline"}</span>
        <span className="pl-count">{done}/{rows.length}</span>
      </button>
      {!folded && rows.map(({ st, status, note }) => (
        <div key={st.id} className={`pl-row ${status}`}>
          <button className="pl-line" onClick={() => toggle(st.id)} title="Show what this step produced">
            <span className="pl-dot">{status === "done" ? <Icon name="check" size={12} /> : null}</span>
            <span className="pl-name">{STAGE_TEXT[st.id] ?? st.label}</span>
            <span className="pl-model">{st.model.replace(" · blind", "")}</span>
          </button>
          {status === "running" && note && <div className="pl-live">{note}</div>}
          {status === "error" && note && <div className="pl-live err">{note}</div>}
          {open === st.id && (
            <div className="pl-detail">
              <div className="pl-facts">
                {st.artefact && <span>{st.artefact}</span>}
                {st.cost_usd > 0 && <span className="mono">{usd(st.cost_usd, 3)}</span>}
                {st.seconds > 0 && <span className="mono">{st.seconds.toFixed(0)} s</span>}
              </div>
              <div className="pl-log">
                {(() => {
                  const mine = source.filter((l) => l.stage === st.id);
                  return mine.length ? mine.slice(-30).map((l) => <div key={`${l.session}-${l.i}`}><span className="lt">{l.t.toFixed(1)}s</span>{l.msg}</div>)
                    : <div className="mute">{full || replay ? "No log lines for this step." : "Loading…"}</div>;
                })()}
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------- the conversation

type Msg = { who: "you" | "agent" | "tool" | "reviewer" | "pipeline"; text: string; chips?: string[]; v?: number; tone?: string };

const vName = (n: number | undefined) => (n == null ? "the cut" : `Version ${n}`);

function story(state: RunState, versions: Version[], lines: LogLine[], job: Job | null, replay: number): Msg[] {
  const out: Msg[] = [
    { who: "you", text: state.run.brief ?? "", chips: [state.run.footage?.split("/").pop() ?? "footage", `${state.run.target_s ?? 40} s`] },
    { who: "pipeline", text: "" },
  ];
  for (const v of versions) {
    const o = v.origin;
    const fixes = (o.fixes ?? []).map((f) => f.note ?? f.op).filter(Boolean).join("\n");
    if (o.source === "human") {
      const f = o.fixes?.[0];
      const what = o.action === "drop" ? `Delete clip ${f?.target ?? ""}.`
        : o.action === "trim" ? `Trim the ${(f?.args as { edge?: string } | null)?.edge === "in" ? "start" : "end"} of clip ${f?.target ?? ""}.`
        : o.action === "punch" ? (f?.op === "punch_skip" ? "Remove one punch-in." : `Turn the punch-ins ${f?.op === "punch_off" ? "off" : "on"}.`)
        : o.action === "caption" ? `Fix ${o.fixes?.length === 1 ? "a caption line" : `${o.fixes?.length} caption lines`}.`
        : o.action === "text" ? "Edit the title card and callouts."
        : o.action === "reorder" ? `Move clip ${f?.target ?? ""}${(f?.args as { before?: string | null } | null)?.before ? ` before ${(f!.args as { before: string }).before}` : " to the end"}.`
        : o.action === "cut" ? `Delete words in ${[...new Set((o.fixes ?? []).map((x) => x.target))].join(", ")}.`
        : o.action === "pin" ? `Follow ${(f?.args as { head?: string | null } | null)?.head != null ? `head ${(f!.args as { head: string }).head}` : "the speaker map"} when speaker ${f?.target ?? ""} talks.`
        : (o.instruction ?? "Ask Montivo.");
      out.push({ who: "you", text: what, chips: [`v${o.from}`], v: v.n });
      if (fixes) out.push({ who: "tool", text: fixes, v: v.n });
    } else if (o.source === "review") {
      out.push({ who: "tool", text: fixes || "patched", v: v.n });
    } else if (o.source === "hook_ab") {
      out.push({ who: "tool", text: `Hook A/B: ${fixes}`, v: v.n });
    }
    const side = v.plan_compare?.[`v${v.n}`] as PlanSide | undefined;
    const by = v.planner && (o.source === "plan" || o.action === "replan")
      ? ` Planned by ${v.planner.name}${side?.plan_cost_usd != null ? ` in ${side.plan_seconds?.toFixed(1)} s for ${usd(side.plan_cost_usd, 3)}` : ""}.` : "";
    out.push({ who: "agent", text: `${vName(v.n)} is ready: ${v.spans.length} clips, ${tc(v.duration_s ?? v.runtime_s)}.${by}`, v: v.n });
    if (v.review) {
      const r = v.review;
      const top = [...r.issues].sort((a, b) => sevRank(a.severity) - sevRank(b.severity))[0];
      out.push({ who: "reviewer", text: `Scored ${vName(v.n)} ${r.overall}/100${r.pass ? ", a pass" : ""}. ${top ? `Worst at ${tc(top.t_s)}: ${top.what}` : r.summary}`, v: v.n, tone: r.pass ? "pass" : "" });
    }
    const ab = o.source === "hook_ab" ? o.result : null;
    if (ab) {
      out.push({ who: "agent", text: `Hook A/B: keeping ${vName(ab.kept)} (${ab.reason}${ab.decisive ? "" : "; within the reviewer's 10-point noise"}).`, v: v.n });
    }
    if (!replay && state.loop?.stop && v.n === state.loop.passes.at(-1)?.version) {
      const stop = state.loop.stop.split(":")[0];
      out.push({ who: "agent", text: stop === "pass" ? "The review loop stopped: the cut passes." : `The review loop stopped (${state.loop.stop}).` });
    }
  }
  if (job?.running) {
    const session = lines.length ? lines[lines.length - 1].session : -1;
    const notable = lines.filter((l) => l.session === session && /^(patch v|review: review_v\d+ overall|plan: |edges: \d+ cut edges|render: \S*cut_v)/.test(l.msg));
    for (const l of notable.slice(-6)) out.push({ who: "tool", text: l.msg });
  }
  if (job && !job.running && job.rc) out.push({ who: "agent", text: `${job.label} failed (exit ${job.rc}).\n${job.tail.split("\n").slice(-4).join("\n")}`, tone: "error" });
  return out;
}

const WHO: Record<Msg["who"], string> = { you: "You", agent: "Montivo", tool: "Edit", reviewer: "Reviewer", pipeline: "Montivo" };

export function Assistant({ state, versions, lines, job, seen, progress, replay, v, clip, getTime, focusNonce, onSend, onStop }: {
  state: RunState; versions: Version[]; lines: LogLine[]; job: Job | null; seen: Seen; progress: Progress | null; replay: number; v: Version | null;
  clip: string | null; getTime: () => number; focusNonce: number;
  onSend: (text: string, chips: string[], review: number, planner: Planner) => void; onStop: () => void;
}) {
  const [text, setText] = useState("");
  const [useClip, setUseClip] = useState(true);
  const [time, setTime] = useState<number | null>(null);
  const [review, setReview] = useState(true);
  const [planner, setPlanner] = useState<Planner>("spark");
  const msgs = story(state, versions, lines, job, replay);
  const end = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const showProgress = !!progress?.active && (!!job?.running || !!replay);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs.length, job?.running, showProgress, progress?.title]);
  useEffect(() => { if (focusNonce) { setUseClip(true); box.current?.focus(); } }, [focusNonce]);
  const chips = [...(clip && useClip ? [clip] : []), ...(time != null ? [`@${tc(time)}`] : [])];
  const cost = (planner === "open" ? state.costs.plan_open : state.costs.plan) + (review ? state.costs.review : 0);
  const blocked = !state.has_key ? "Asking Montivo calls Muse Spark: set MODEL_API_KEY and restart montivo serve."
    : job?.running ? `${job.label} is running…` : replay ? "Replay is on: turn it off in the menu to edit." : null;
  const send = () => { if (!text.trim() || blocked) return; onSend(text.trim(), chips, review ? 1 : 0, planner); setText(""); setTime(null); };
  return (
    <section className="assistant">
      <div className="panel-head"><span className="ph-title">Assistant</span><span className="ph-sub">{planner === "open" ? "Muse Spark Open" : "Muse Spark 1.3"}</span></div>
      <div className="msgs">
        {msgs.map((m, k) => m.who === "pipeline" ? (
          <div key={k} className="msg agent"><span className="who">Montivo</span><Pipeline state={state} lines={lines} job={job} seen={seen} replay={replay} /></div>
        ) : (
          <div key={k} className={`msg ${m.who} ${m.tone ?? ""}`}>
            <span className="who">{WHO[m.who]}{m.who === "reviewer" && <span className="blind" title="The reviewer gets the rendered video, the brief, the rubric and three measured facts. Never the plan.">blind</span>}</span>
            <span className="txt">{m.text}</span>
            {m.chips && <span className="chips">{m.chips.map((c) => <span key={c} className="chip">{c}</span>)}</span>}
          </div>
        ))}
        {showProgress ? <div className="msg agent"><span className="who">Montivo</span><ProgressCard p={progress!} onStop={job?.running ? onStop : undefined} /></div>
          : job?.running && <div className="msg agent"><span className="who">Montivo</span><span className="txt"><span className="pulse" /> {job.label}… <button className="link" onClick={onStop}>Stop</button></span></div>}
        <div ref={end} />
      </div>
      <div className="composer">
        <div className="chips">
          {clip && <button className={`chip sel ${useClip ? "on" : ""}`} onClick={() => setUseClip(!useClip)} title="Send the selected clip with the message">clip {clip}</button>}
          <button className={`chip sel ${time != null ? "on" : ""}`} onClick={() => setTime(time != null ? null : getTime())} title="Send the playhead time with the message">
            {time != null ? `@${tc(time)}` : "+ playhead"}
          </button>
          {v && <span className="chip" title="Montivo edits from this version">from v{v.n}</span>}
        </div>
        <textarea ref={box} value={text} onChange={(e) => setText(e.target.value)} placeholder={blocked ?? "Ask Montivo… “drop the agency line”, “make it 30 seconds”"}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} disabled={!!blocked} rows={3} />
        <PlannerPicker value={planner} onChange={setPlanner} hasOpen={state.has_open_key} costs={state.costs} />
        <div className="send-row">
          <label className="check small"><input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} /> review the result</label>
          <span className="grow" />
          <span className="cost" title={`${planner === "open" ? "Muse Spark Open" : "Muse Spark 1.3"} plan call${review ? " plus one blind review on Spark 1.3" : ""}, roughly`}>~{usd(cost)}</span>
          <button className="btn" disabled={!!blocked || !text.trim()} onClick={send}>Send</button>
        </div>
      </div>
    </section>
  );
}
