import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, frameUrl, tc, usd, type Seen } from "./api";
import { sevRank } from "./edit";
import { Icon } from "./icons";
import { Bar, expTip, measure, useTick } from "./Progress";
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
  const [folded, setFolded] = useState(true);
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

/** One thing said in the thread: your message, a line of the agent's, a run of tool steps (folded), a version card, a
 *  review card or a failure. Consecutive items of the same speaker share one avatar. */
type Item =
  | { kind: "you"; text: string; chips: string[] }
  | { kind: "text"; who: "montivo" | "reviewer"; text: string; tone?: string }
  | { kind: "steps"; title: string; lines: string[]; live?: boolean }
  | { kind: "version"; v: Version; by: string }
  | { kind: "review"; v: Version }
  | { kind: "error"; text: string };

const vName = (n: number | undefined) => (n == null ? "the cut" : `Version ${n}`);
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;

function story(state: RunState, versions: Version[], lines: LogLine[], job: Job | null, replay: number): Item[] {
  const out: Item[] = [
    { kind: "you", text: state.run.brief ?? "", chips: [state.run.footage?.split("/").pop() ?? "footage", `${state.run.target_s ?? 40} s`] },
  ];
  const asked = (state.chat ?? []).filter((c) => c.role === "agent" && c.kind === "ask" && !c.version && c.after != null);
  for (const v of versions) {
    const o = v.origin;
    const fixes = (o.fixes ?? []).map((f) => f.note ?? f.op).filter(Boolean) as string[];
    if (o.source === "human" && o.agent) {
      out.push({ kind: "you", text: o.agent.message, chips: [`from v${o.from}`] });
      if (o.agent.reply) out.push({ kind: "text", who: "montivo", text: o.agent.reply });
      const lines = [...(o.agent.done.length ? o.agent.done : fixes), ...o.agent.skipped.map((x) => `not done · ${x}`)];
      if (lines.length) out.push({ kind: "steps", title: `${o.agent.router === "keywords" ? "Read the keywords" : "Picked the tools"} · ${plural(o.agent.done.length || fixes.length, "edit")}`, lines });
    } else if (o.source === "human") {
      const f = o.fixes?.[0];
      const what = o.action === "drop" ? `Delete clip ${f?.target ?? ""}.`
        : o.action === "trim" ? `Trim the ${(f?.args as { edge?: string } | null)?.edge === "in" ? "start" : "end"} of clip ${f?.target ?? ""}.`
        : o.action === "punch" ? (f?.op === "punch_skip" ? "Remove one punch-in." : `Turn the punch-ins ${f?.op === "punch_off" ? "off" : "on"}.`)
        : o.action === "caption" ? `Fix ${o.fixes?.length === 1 ? "a caption line" : `${o.fixes?.length} caption lines`}.`
        : o.action === "text" ? "Edit the title card and callouts."
        : o.action === "reorder" ? `Move clip ${f?.target ?? ""}${(f?.args as { before?: string | null } | null)?.before ? ` before ${(f!.args as { before: string }).before}` : " to the end"}.`
        : o.action === "cut" ? `Delete words in ${[...new Set((o.fixes ?? []).map((x) => x.target))].join(", ")}.`
        : o.action === "music" ? (f?.op === "music_off" ? "Take the music out." : (() => {
          const m = (f?.args as { music?: { track: string; level_db: number; voice_db?: number } } | null)?.music;
          const voice = m?.voice_db ? `, voice ${m.voice_db > 0 ? "+" : ""}${m.voice_db} dB` : "";
          return m ? `Music: ${m.track.replace(/_/g, " ")} at ${m.level_db} LUFS${voice}.` : "Put music under the speech."; })())
        : o.action === "pin" ? `Follow ${(f?.args as { head?: string | null } | null)?.head != null ? `head ${(f!.args as { head: string }).head}` : "the speaker map"} when speaker ${f?.target ?? ""} talks.`
        : (o.instruction ?? "Ask Montivo.");
      out.push({ kind: "you", text: what, chips: [`from v${o.from}`] });
      if (fixes.length) out.push({ kind: "steps", title: o.action === "replan" ? "Re-planned the edit" : `Made the edit · ${plural(fixes.length, "step")}`, lines: fixes });
    } else if (o.source === "review") {
      out.push({ kind: "steps", title: `Applied ${plural(fixes.length || 1, "fix")} from the review of v${o.from}`, lines: fixes.length ? fixes : ["patched"] });
    } else if (o.source === "hook_ab") {
      out.push({ kind: "steps", title: "Tried another opening line", lines: fixes.length ? fixes : ["swap_hook"] });
    }
    const side = v.plan_compare?.[`v${v.n}`] as PlanSide | undefined;
    const by = v.planner && (o.source === "plan" || o.action === "replan")
      ? `Planned by ${v.planner.name}${side?.plan_cost_usd != null ? ` in ${side.plan_seconds?.toFixed(1)} s for ${usd(side.plan_cost_usd, 3)}` : ""}` : "";
    out.push({ kind: "version", v, by });
    if (v.review) out.push({ kind: "review", v });
    const ab = o.source === "hook_ab" ? o.result : null;
    if (ab) out.push({ kind: "text", who: "montivo", text: `Keeping ${vName(ab.kept)} (${ab.reason}${ab.decisive ? "" : "; within the reviewer's 10-point noise"}).` });
    for (const c of replay ? [] : asked.filter((x) => x.after === v.n)) { // a message that made no version: its reply
      out.push({ kind: "you", text: c.message ?? "", chips: [`v${v.n}`] });
      out.push({ kind: "text", who: "montivo", text: c.text ?? "" });
    }
    if (!replay && state.loop?.stop && v.n === state.loop.passes.at(-1)?.version) {
      const stop = state.loop.stop.split(":")[0];
      out.push({ kind: "text", who: "montivo", text: stop === "pass" ? "The review loop stopped: the cut passes." : `The review loop stopped (${state.loop.stop}).` });
    }
  }
  if (job?.running) {
    const session = lines.length ? lines[lines.length - 1].session : -1;
    const notable = lines.filter((l) => l.session === session && /^(patch v|review: review_v\d+ overall|plan: |edges: \d+ cut edges|render: \S*cut_v)/.test(l.msg));
    if (notable.length) out.push({ kind: "steps", title: `${job.label} · ${plural(notable.length, "step")} so far`, lines: notable.slice(-6).map((l) => l.msg), live: true });
  }
  if (job && !job.running && job.rc) out.push({ kind: "error", text: `${job.label} failed (exit ${job.rc}).\n${job.tail.split("\n").slice(-4).join("\n")}` });
  return out;
}

type Block = { who: "you" | "montivo" | "reviewer"; items: Item[] };

function blocks(items: Item[]): Block[] {
  const out: Block[] = [];
  for (const it of items) {
    const who = it.kind === "you" ? "you" : it.kind === "review" || (it.kind === "text" && it.who === "reviewer") ? "reviewer" : "montivo";
    const last = out.at(-1);
    if (who !== "you" && last?.who === who) last.items.push(it);
    else out.push({ who, items: [it] });
  }
  return out;
}

/** The icon of one tool step, from what the step says it did. */
function stepIcon(line: string): string {
  const s = line.toLowerCase();
  if (/caption/.test(s)) return "captions";
  if (/music|bed\b/.test(s)) return "music";
  if (/hook|opening/.test(s)) return "hook";
  if (/punch/.test(s)) return "punch";
  if (/callout|title/.test(s)) return "callout";
  if (/style|look|font/.test(s)) return "style";
  if (/reorder|move|before /.test(s)) return "move";
  if (/review/.test(s)) return "eyeOff";
  if (/plan/.test(s)) return "spark";
  if (/render/.test(s)) return "film";
  if (/frame|crop|head|reframe/.test(s)) return "person";
  if (/edge|trim|cut|drop|word|split/.test(s)) return "scissors";
  return "dot";
}

function Steps({ title, lines, live }: { title: string; lines: string[]; live?: boolean }) {
  const [open, setOpen] = useState(!!live);
  return (
    <div className={`fold ${open ? "open" : ""}`}>
      <button className="fold-head" onClick={() => setOpen(!open)}>
        {live ? <span className="spin" /> : <Icon name={open ? "chevronDown" : "chevron"} size={13} />}
        <span>{title}</span>
      </button>
      {open && lines.map((l, k) => (
        <div key={k} className="fold-step"><Icon name={stepIcon(l)} size={13} /><span>{l}</span></div>
      ))}
    </div>
  );
}

function VersionCard({ id, v, by, current, onPick, onCompare }: {
  id: string; v: Version; by: string; current: boolean; onPick: (n: number) => void; onCompare: (a: number, b: number) => void;
}) {
  const len = v.duration_s ?? v.runtime_s;
  const poster = v.title_s + Math.max(0, len - v.title_s) / 3;
  const from = v.origin.from;
  return (
    <div className={`vcard ${current ? "on" : ""}`} title={current ? "On screen now" : undefined}>
      <button className="vc-thumb" onClick={() => onPick(v.n)} title={`Open Version ${v.n}`}>
        {v.cut && <img src={frameUrl(id, v.n, poster, true)} alt="" loading="lazy" />}
      </button>
      <div className="vc-text">
        <b>Version {v.n}</b>
        <span>{plural(v.spans.length, "clip")} · {tc(len)}{v.loudness ? ` · ${v.loudness.integrated_lufs} LUFS` : ""}</span>
        {by && <span className="vc-by">{by}</span>}
      </div>
      <div className="vc-acts">
        <button className="ib" onClick={() => onPick(v.n)} title={`Open Version ${v.n}`}><Icon name="play" size={12} /></button>
        {from != null && <button className="ib" onClick={() => onCompare(from, v.n)} title={`Compare with Version ${from}`}><Icon name="compare" size={14} /></button>}
      </div>
    </div>
  );
}

function ReviewCard({ v, current, onPick, onSeek }: { v: Version; current: boolean; onPick: (n: number) => void; onSeek: (t: number) => void }) {
  const r = v.review!;
  const top = [...r.issues].sort((a, b) => sevRank(a.severity) - sevRank(b.severity) || a.t_s - b.t_s)[0];
  const scores = Object.values(r.scores);
  const full = scores.filter((s) => s >= 10).length;
  const tone = r.pass ? "pass" : r.gate?.blocked ? "held" : "fail";
  const verdict = r.pass ? "Pass" : r.gate?.blocked && r.reviewer_pass ? "Held by the gate" : "No pass";
  const go = (t: number) => (current ? onSeek(t) : onPick(v.n));
  return (
    <div className="rcard">
      <div className="rc-top">
        <span className={`ring ${tone}`} style={{ "--p": `${r.overall}%` } as React.CSSProperties}><span>{r.overall}</span></span>
        <div className="rc-text">
          <b className={tone}>{verdict}</b>
          <span>{vName(v.n)} · {full} of {scores.length} criteria at 10</span>
        </div>
      </div>
      {top ? (
        <button className="rc-issue" onClick={() => go(top.t_s)} title={current ? "Go there" : `Open Version ${v.n}`}>
          <span className={`rc-sev ${top.severity}`} /><span><span className="at">{tc(top.t_s)}</span> {top.what}</span>
        </button>
      ) : <p className="rc-sum">{r.summary}</p>}
    </div>
  );
}

/** Up to three prompts the last review suggests, else the common asks. A click puts the words in the box. */
function suggestions(state: RunState, v: Version | null): string[] {
  const out: string[] = [];
  const s = v?.review?.scores ?? {};
  const len = v ? v.duration_s ?? v.runtime_s : 0;
  if ((s.hook_in_1_5s ?? 10) < 8) out.push("Open on a stronger line");
  if ((s.pacing ?? 10) < 8) out.push("Tighten the pacing");
  if ((s.ending ?? 10) < 8) out.push("End on the strongest line");
  if (state.run.target_s && len > state.run.target_s + 5) out.push(`Make it ${state.run.target_s} seconds`);
  if (v && !v.music) out.push("Add some music under it");
  for (const x of ["Make it 30 seconds", "Drop the weakest clip", "End on the strongest line"]) if (!out.includes(x)) out.push(x);
  return out.slice(0, 3);
}

/** The agent's live work, glued to the top of the input: the step, its clock and bar, the steps done, Stop. Idle, the
 *  pipeline checklist folded to one line. */
function Tray({ state, lines, job, seen, progress, replay, onStop }: {
  state: RunState; lines: LogLine[]; job: Job | null; seen: Seen; progress: Progress | null; replay: number; onStop: () => void;
}) {
  const p = progress?.active && (job?.running || replay) ? progress : null;
  useTick(!!p?.active);
  if (p) {
    const m = measure(p);
    return (
      <div className="tray live">
        <div className="tr-top">
          <span className="spin" />
          <b>{p.title}</b>
          {p.detail && <span className="tr-detail">{p.detail}</span>}
          <span className="tr-clock" title={expTip(p)}>{tc(m.stage, 0)}{p.expected_s ? ` / ~${tc(p.expected_s, 0)}` : ""}</span>
        </div>
        <Bar key={p.title} frac={m.frac} small />
        <div className="tr-chain">
          {p.steps.slice(-4).map((s, k) => <span key={k} className="ok" title={`${s.seconds.toFixed(0)} s`}><Icon name="check" size={11} />{s.title}</span>)}
          <span className="run">{p.title}</span>
          <span className="grow" />
          <span className="tr-job">{p.label} · {tc(m.job, 0)}</span>
          {p.replay ? <span className="tr-job">replay</span> : job?.running && <button className="tr-stop" onClick={onStop}><Icon name="stop" size={10} />Stop</button>}
        </div>
      </div>
    );
  }
  if (job?.running) {
    return (
      <div className="tray live">
        <div className="tr-top"><span className="spin" /><b>{job.label}…</b><span className="grow" /><button className="tr-stop" onClick={onStop}><Icon name="stop" size={10} />Stop</button></div>
      </div>
    );
  }
  return <div className="tray"><Pipeline state={state} lines={lines} job={job} seen={seen} replay={replay} /></div>;
}

function PlannerMenu({ value, onChange, hasOpen, state }: { value: Planner; onChange: (p: Planner) => void; hasOpen: boolean; state: RunState }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const off = () => setOpen(false);
    window.addEventListener("pointerdown", off);
    return () => window.removeEventListener("pointerdown", off);
  }, [open]);
  const c = state.costs;
  const opts: { id: Planner; name: string; note: string; off?: string }[] = [
    { id: "spark", name: "Muse Spark 1.3", note: `about ${usd(c.plan, 3)} · ${c.plan_s} s` },
    { id: "open", name: "Muse Spark Open", note: `open weights on Baseten · about ${usd(c.plan_open, 3)} · ${c.plan_open_s} s`,
      off: hasOpen ? undefined : "set BASETEN_API_KEY and restart montivo serve" },
  ];
  return (
    <div className="pmenu" onPointerDown={(e) => e.stopPropagation()}>
      <button className="cb-btn" onClick={() => setOpen(!open)} title="Which model plans the edit">
        <Icon name="spark" size={13} />{value === "open" ? "Spark Open" : "Spark 1.3"}<Icon name="chevronUp" size={12} />
      </button>
      {open && (
        <div className="pm-list">
          <div className="menu-label">Plans the edit</div>
          {opts.map((o) => (
            <button key={o.id} className={`pm-item ${value === o.id ? "on" : ""}`} disabled={!!o.off} title={o.off}
              onClick={() => { onChange(o.id); setOpen(false); }}>
              <span className="pm-check">{value === o.id && <Icon name="check" size={13} />}</span>
              <span><b>{o.name}</b><span>{o.off ?? o.note}</span></span>
            </button>
          ))}
          <div className="pm-foot">The blind review stays on Spark 1.3.</div>
        </div>
      )}
    </div>
  );
}

export function Assistant({ state, versions, lines, job, seen, progress, replay, v, clip, getTime, focusNonce, onSend, onStop, onPick, onSeek, onCompare }: {
  state: RunState; versions: Version[]; lines: LogLine[]; job: Job | null; seen: Seen; progress: Progress | null; replay: number; v: Version | null;
  clip: string | null; getTime: () => number; focusNonce: number;
  onSend: (text: string, chips: string[], review: number, planner: Planner) => void; onStop: () => void;
  onPick: (n: number) => void; onSeek: (t: number) => void; onCompare: (a: number, b: number) => void;
}) {
  const [text, setText] = useState("");
  const [useClip, setUseClip] = useState(true);
  const [time, setTime] = useState<number | null>(null);
  const [review, setReview] = useState(true);
  const [planner, setPlanner] = useState<Planner>("spark");
  const items = story(state, versions, lines, job, replay);
  const end = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [items.length, job?.running]);
  useEffect(() => { if (focusNonce) { setUseClip(true); box.current?.focus(); } }, [focusNonce]);
  useLayoutEffect(() => { // the box grows with the text, up to about six lines
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 150)}px`;
  }, [text]);
  const chips = [...(clip && useClip ? [clip] : []), ...(time != null ? [`@${tc(time)}`] : [])];
  const cost = state.has_key ? (state.costs.ask ?? 0.006) + (review ? state.costs.review : 0) : 0;
  const running = !!job?.running;
  const blocked = running ? "Montivo is working…" : replay ? "Replay is on: turn it off in the menu to edit." : null;
  const send = () => { if (!text.trim() || blocked) return; onSend(text.trim(), chips, review && state.has_key ? 1 : 0, planner); setText(""); setTime(null); };
  const tips = !blocked && !text ? suggestions(state, v) : [];
  return (
    <section className="assistant">
      <div className="panel-head"><span className="ph-title">Assistant</span><span className="ph-sub">{planner === "open" ? "Muse Spark Open" : "Muse Spark 1.3"}</span></div>
      <div className="msgs">
        {blocks(items).map((b, k) => b.who === "you" ? b.items.map((it, j) => it.kind === "you" && (
          <div key={`${k}-${j}`} className="you">
            <div className="bub">{it.text}</div>
            {it.chips.length > 0 && <div className="ctx">{it.chips.map((c) => <span key={c} className="pill">{c}</span>)}</div>}
          </div>
        )) : (
          <div key={k} className={`bot ${b.who}`}>
            {b.who === "montivo" ? <img className="av" src="/favicon.svg" alt="" />
              : <span className="av rev" title="The reviewer gets the rendered video, the brief, the rubric and measured facts. Never the plan."><Icon name="eyeOff" size={14} /></span>}
            <div className="bot-body">
              <span className="name">{b.who === "montivo" ? "Montivo" : <>Reviewer <span className="blind">blind</span></>}</span>
              {b.items.map((it, j) => it.kind === "text" ? <p key={j} className={`say ${it.tone ?? ""}`}>{it.text}</p>
                : it.kind === "steps" ? <Steps key={j} title={it.title} lines={it.lines} live={it.live} />
                : it.kind === "version" ? <VersionCard key={j} id={state.id} v={it.v} by={it.by} current={it.v.n === v?.n} onPick={onPick} onCompare={onCompare} />
                : it.kind === "review" ? <ReviewCard key={j} v={it.v} current={it.v.n === v?.n} onPick={onPick} onSeek={onSeek} />
                : it.kind === "error" ? <p key={j} className="say error">{it.text}</p> : null)}
            </div>
          </div>
        ))}
        <div ref={end} />
      </div>
      {tips.length > 0 && <div className="sugg">{tips.map((s) => <button key={s} onClick={() => { setText(s); box.current?.focus(); }}>{s}</button>)}</div>}
      <div className="composer">
        <Tray state={state} lines={lines} job={job} seen={seen} progress={progress} replay={replay} onStop={onStop} />
        <div className={`cbox ${blocked ? "off" : ""}`}>
          <textarea ref={box} value={text} onChange={(e) => setText(e.target.value)} rows={2}
            placeholder={blocked ?? (state.has_key ? "Ask Montivo… “add some lo-fi music”, “bold captions”, “make it 30 seconds”"
              : `${state.key_note ?? "No MODEL_API_KEY"}: Montivo reads keywords only… “add music”, “bold pop captions”, “punch-ins on”`)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} disabled={!!blocked} />
          <div className="cb-ctx">
            {clip && <button className={`pill sel ${useClip ? "on" : ""}`} onClick={() => setUseClip(!useClip)} title="Send the selected clip with the message"><Icon name="film" size={12} />clip {clip}</button>}
            <button className={`pill sel ${time != null ? "on" : ""}`} onClick={() => setTime(time != null ? null : getTime())} title="Send the playhead time with the message">
              <Icon name="clock" size={12} />{time != null ? `@ ${tc(time)}` : "playhead"}
            </button>
            {v && <span className="pill" title="Montivo edits from this version">from v{v.n}</span>}
          </div>
          <div className="cb-row">
            <PlannerMenu value={planner} onChange={setPlanner} hasOpen={state.has_open_key} state={state} />
            <button className={`cb-btn ${review && state.has_key ? "on" : ""}`} onClick={() => setReview(!review)} disabled={!state.has_key}
              title={!state.has_key ? "The blind review calls Muse Spark: it needs MODEL_API_KEY" : review ? "The blind reviewer scores the result (click to skip)" : "No review of the result (click to add one)"}>
              <Icon name="eyeOff" size={13} />Review{review && state.has_key ? "" : " off"}
            </button>
            <span className="grow" />
            <span className="cost" title={state.has_key ? `One Muse Spark 1.3 call picks the edits${review ? ", plus one blind review" : ""}; a re-plan adds ~${usd(planner === "open" ? state.costs.plan_open : state.costs.plan)} on ${planner === "open" ? "Muse Spark Open" : "Muse Spark 1.3"}. Music, look, captions and clip edits are $0.` : "Without the key Montivo reads keywords: $0"}>~{usd(cost)}</span>
            {running && !replay
              ? <button className="send stop" onClick={onStop} title="Stop the running job"><Icon name="stop" size={11} /></button>
              : <button className="send" disabled={!!blocked || !text.trim()} onClick={send} title="Send (Enter)"><Icon name="send" size={15} /></button>}
          </div>
        </div>
      </div>
    </section>
  );
}
