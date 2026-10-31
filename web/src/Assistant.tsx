import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, crit, frameUrl, tc, usd, type Seen } from "./api";
import { selRange, sevRank, speakerName, type FlatWord, type WordSel } from "./edit";
import { Icon, MarkLoader } from "./icons";
import { MishearList, openSuggestions, useHiddenSuggestions } from "./Panels";
import { Bar, expTip, measure, useTick } from "./Progress";
import type { AgentChecks, AgentUi, AskScope, ChatLine, Job, LogLine, PendingCard, PlanSide, Planner, Progress, RunState, Stage, Version } from "./types";

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
            <span className="pl-dot">{status === "done" ? <Icon name="check" size={12} /> : status === "running" ? <MarkLoader /> : null}</span>
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
                    : <div className="mute">{full || replay ? "No log lines for this step." : <><MarkLoader /> Loading…</>}</div>;
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
  | { kind: "text"; who: "montivo" | "reviewer"; text: string; tone?: string; seek?: number[] }
  | { kind: "steps"; title: string; lines: string[]; live?: boolean }
  | { kind: "version"; v: Version; by: string }
  | { kind: "review"; v: Version }
  | { kind: "mishear"; v: Version } // C3: "Did you say ...?" under a planned version
  | { kind: "preview"; card: PendingCard; status: "open" | "applying" | "applied" | "retried" }
  | { kind: "error"; text: string; charged?: number | null; retry?: boolean }
  | { kind: "checks"; v: Version; checks: AgentChecks }; // B1: the $0 checks on a version the chat made, with Fix it

/** The message as typed: the server adds the chips for the router as " (about s3, @00:12.00)". */
const typed = (m: string | null | undefined) => (m ?? "").replace(/ \(about [^()]*\)$/, "");
const vName = (n: number | undefined) => (n == null ? "the cut" : `Version ${n}`);
/** The cut seconds an ask's reply points at (seek ops): a link under the reply plays from there again. */
const seeks = (ui: AgentUi[] | null | undefined) => (ui ?? []).filter((o) => o.op === "seek" && o.at_s != null).map((o) => o.at_s!);
/** The checks in one sentence (agent.py checked_text). */
const checkedText = (c: AgentChecks) => c.ok
  ? `Checked the new cut: ${c.audio ? "no cut lands in speech, " : ""}captions clear of the face, the head in frame.`
  : `Checked the new cut: ${c.reasons.join("; ")}${c.more ? ` (and ${c.more} more)` : ""}.`;
/** Fix it: the next ask, made of what the checks measured (agent.py PROMPT reads it). */
const fixText = (c: AgentChecks) => `Fix what the checks found on this version: ${c.reasons.join("; ")}.`;
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : /(s|x|ch|sh)$/.test(w) ? "es" : "s"}`;

/** The selection an ask was about, as chips: "s3", "s3, s5 · 2 clips", "s3 · 4 words", "caption 00:12.0". */
export function scopeChips(s: AskScope | null | undefined): string[] {
  if (!s) return [];
  const out: string[] = [];
  if (s.clips?.length) out.push(s.clips.length === 1 ? s.clips[0] : `${s.clips.slice(0, 3).join(", ")}${s.clips.length > 3 ? " …" : ""} · ${s.clips.length} clips`);
  if (s.words?.length) {
    const n = s.words.reduce((a, w) => a + w.to - w.from + 1, 0);
    const spans = [...new Set(s.words.map((w) => w.span))];
    out.push(`${spans.length > 2 ? `${spans[0]}–${spans.at(-1)}` : spans.join(", ")} · ${plural(n, "word")}`);
  }
  if (s.caption) out.push(`caption ${tc(s.caption.t_in)}`);
  if (s.t != null) out.push(`@${tc(s.t)}`);
  return out;
}

/** Where a proposal stands: applied once a version carries it, applying while its Apply runs, turned down for another
 *  try, else open (an Apply that failed can be clicked again). */
function proposalStatus(id: string, chat: ChatLine[], versions: Version[], job: Job | null): "open" | "applying" | "applied" | "retried" {
  if (versions.some((x) => x.origin.agent?.pending === id)) return "applied";
  const mine = chat.filter((c) => c.role === "user" && c.pending === id);
  if (mine.some((c) => c.kind === "retry")) return "retried";
  if (mine.some((c) => c.kind === "apply") && job?.running && job.kind === "ask") return "applying";
  return "open";
}

function story(state: RunState, versions: Version[], lines: LogLine[], job: Job | null, replay: number): Item[] {
  const chat = state.chat ?? [];
  // a new project's run.json comes a few seconds after the job starts: until then the brief is the job's chat line
  const brief = state.run.brief || chat.find((c) => c.kind === "brief")?.text || "";
  const out: Item[] = [
    { kind: "you", text: brief, chips: [state.run.footage?.split("/").pop() ?? "footage", `${state.run.target_s ?? 40} s`] },
  ];
  // the agent's answers that made no version: a reply, or a proposal waiting for Apply
  const asked = chat.filter((c) => c.role === "agent" && c.after != null && ((c.kind === "ask" && !c.version) || c.kind === "draft" || c.kind === "preview" || c.kind === "error"));
  const failed = job && !job.running && job.rc ? job : null; // the last job, when it failed: Try again runs it once more
  const tail = (j: Job) => j.tail.split("\n").slice(-4).join("\n").trim();
  for (const v of versions) {
    const o = v.origin;
    const fixes = (o.fixes ?? []).map((f) => f.note ?? f.op).filter(Boolean) as string[];
    if (o.source === "human" && o.agent) {
      const applied = !!o.agent.pending; // its message and reply are on the proposal card above
      out.push({ kind: "you", text: applied ? "Apply" : o.agent.retry_of ? "Try something else" : typed(o.agent.message),
        chips: applied ? [] : [...scopeChips(o.agent.scope), `from v${o.from}`] });
      if (o.agent.reply && !applied) out.push({ kind: "text", who: "montivo", text: o.agent.reply, seek: seeks(o.agent.ui) });
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
        : o.action === "pin" ? `Follow ${(f?.args as { head?: string | null } | null)?.head != null ? `head ${(f!.args as { head: string }).head}` : "the speaker map"} when ${speakerName(v, f?.target ?? "")} talks.`
        : o.action === "speakers" ? `Speakers: ${fixes.join("; ")}.`
        : o.action === "restore" ? `Restore Version ${o.from}.`
        : o.action === "edges" ? "Tighten the cut edges again."
        : o.action === "style" ? "Change the caption style."
        : o.instruction ?? (o.action === "replan" ? "Plan the edit again." : "Edit by hand.");
      out.push({ kind: "you", text: what, chips: [`from v${o.from}`] });
      if (fixes.length) out.push({ kind: "steps", title: o.action === "replan" ? "Re-planned the edit" : `Made the edit · ${plural(fixes.length, "step")}`, lines: fixes });
    } else if (o.source === "review" && o.action === "escalate") { // C4: the loop's fixes ran out, one re-plan from its notes
      out.push({ kind: "steps", title: `No fix of the cut could clear the review of v${o.from}: re-planned from its open notes`, lines: fixes.length ? fixes : ["replanned"] });
    } else if (o.source === "review") {
      out.push({ kind: "steps", title: `Applied ${plural(fixes.length || 1, "fix")} from the review of v${o.from}`, lines: fixes.length ? fixes : ["patched"] });
    } else if (o.source === "hook_ab") {
      out.push({ kind: "steps", title: "Tried another opening line", lines: fixes.length ? fixes : ["swap_hook"] });
    }
    const side = v.plan_compare?.[`v${v.n}`] as PlanSide | undefined;
    const by = v.planner && (o.source === "plan" || o.action === "replan")
      ? `Planned by ${v.planner.name}${side?.plan_cost_usd != null ? ` in ${side.plan_seconds?.toFixed(1)} s for ${usd(side.plan_cost_usd, 3)}` : ""}` : "";
    out.push({ kind: "version", v, by });
    if (o.source === "human" && o.agent?.checks) out.push({ kind: "checks", v, checks: o.agent.checks });
    const missing = o.source === "plan" || o.action === "replan" ? v.brief_missing ?? [] : [];
    if ((o.source === "plan" || o.action === "replan") && v.glossary_suggestions?.length) out.push({ kind: "mishear", v });
    if (missing.length) out.push({ kind: "text", who: "montivo", text: `Your brief asks for ${missing.map((x) => `“${x}”`).join(", ")}, but ${missing.length === 1 ? "it is" : "they are"} not in the footage, so the cut goes without ${missing.length === 1 ? "it" : "them"}. The reviewer is told the same.` });
    if (v.review) out.push({ kind: "review", v });
    const ab = o.source === "hook_ab" ? o.result : null;
    if (ab) out.push({ kind: "text", who: "montivo", text: `Keeping ${vName(ab.kept)} (${ab.reason}${ab.judge || ab.decisive ? "" : "; within the reviewer's 10-point noise"}).` });
    if (!replay && state.loop?.stop && v.n === state.loop.passes.at(-1)?.version) {
      const stop = state.loop.stop.split(":")[0];
      const esc = state.loop.escalation;
      out.push({ kind: "text", who: "montivo", text: stop === "pass" ? `The review loop stopped: the cut passes${esc?.pass ? ", after one re-plan from the reviewer's open notes" : ""}.` : `The review loop stopped (${state.loop.stop}).` });
    }
    for (const c of replay ? [] : asked.filter((x) => x.after === v.n)) { // a message that made no version: its reply or proposal
      if (c.kind === "error") {
        const last = failed?.id === c.job;
        out.push({ kind: "error", text: `${c.text ?? ""}${last && failed!.rc! > 0 && tail(failed!) ? `\n${tail(failed!)}` : ""}`, charged: c.charged_usd, retry: last });
        continue;
      }
      out.push({ kind: "you", text: c.retry_of ? "Try something else" : typed(c.message), chips: c.retry_of ? [] : [...scopeChips(c.scope), `v${v.n}`] });
      if (c.text) out.push({ kind: "text", who: "montivo", text: c.text, seek: seeks(c.ui) });
      const card = c.kind === "preview" && typeof c.pending === "object" ? c.pending : null;
      if (card) out.push({ kind: "preview", card, status: proposalStatus(card.id, chat, versions, job) });
    }
  }
  if (job?.running) {
    const session = lines.length ? lines[lines.length - 1].session : -1;
    const notable = lines.filter((l) => l.session === session && /^(patch v|review: review_v\d+ overall|plan: |edges: \d+ cut edges|render: \S*cut_v)/.test(l.msg));
    if (notable.length) out.push({ kind: "steps", title: `${job.label} · ${plural(notable.length, "step")} so far`, lines: notable.slice(-6).map((l) => l.msg), live: true });
  }
  if (failed && !replay && !chat.some((c) => c.kind === "error" && c.job === failed.id && c.after != null)) // its chat line not read yet
    out.push({ kind: "error", text: `${failed.label} ${failed.rc! < 0 ? "was stopped" : `failed (exit ${failed.rc})`}.\n${tail(failed)}`, charged: failed.charged_usd, retry: true });
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
        {live ? <MarkLoader /> : <Icon name={open ? "chevronDown" : "chevron"} size={13} />}
        <span>{title}</span>
      </button>
      {open && lines.map((l, k) => (
        <div key={k} className="fold-step"><Icon name={stepIcon(l)} size={13} /><span>{l}</span></div>
      ))}
    </div>
  );
}

function VersionCard({ id, v, by, current, newest, onPick, onCompare, onRestore }: {
  id: string; v: Version; by: string; current: boolean; newest: boolean; onPick: (n: number) => void; onCompare: (a: number, b: number) => void;
  onRestore: ((n: number) => void) | null;
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
        <span>{plural(v.spans.length, "clip")} · {tc(len)}</span>
        {by && <span className="vc-by">{by}</span>}
      </div>
      <div className="vc-acts">
        <button className="ib" onClick={() => onPick(v.n)} title={`Open Version ${v.n}`}><Icon name="play" size={12} /></button>
        {from != null && <button className="ib" onClick={() => onCompare(from, v.n)} title={`Compare with Version ${from}`}><Icon name="compare" size={14} /></button>}
        {!newest && v.cut && onRestore && <button className="ib" onClick={() => onRestore(v.n)} title={`Restore Version ${v.n}: it comes back as the newest version, nothing is deleted ($0)`}><Icon name="undo" size={13} /></button>}
      </div>
    </div>
  );
}

function ReviewCard({ v, current, onPick, onSeek }: { v: Version; current: boolean; onPick: (n: number) => void; onSeek: (t: number) => void }) {
  const r = v.review!;
  const top = [...r.issues].sort((a, b) => sevRank(a.severity) - sevRank(b.severity) || a.t_s - b.t_s)[0];
  const weakest = Object.entries(r.scores).sort((a, b) => a[1] - b[1])[0];
  const tone = r.pass ? "pass" : r.gate?.blocked ? "held" : "fail";
  const verdict = r.pass ? "Pass" : r.gate?.blocked && r.reviewer_pass ? "Held by the gate" : "No pass";
  const go = (t: number) => (current ? onSeek(t) : onPick(v.n));
  return (
    <div className="rcard">
      <div className="rc-top">
        <span className={`ring ${tone}`} style={{ "--p": `${r.overall}%` } as React.CSSProperties}><span>{r.overall}</span></span>
        <div className="rc-text">
          <b className={tone}>{verdict}</b>
          <span>{vName(v.n)}{weakest && weakest[1] < 10 ? ` · weakest: ${crit(weakest[0])} ${weakest[1]}/10` : " · every criterion at 10"}</span>
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

/** A structural or costly ask before it runs (agent.py preview_card): what changes in plain words, the cost and time,
 *  Apply and one free Try something else. A re-plan's card shows the note and the target: Apply plans it, once. */
function PreviewCard({ card, status, busy, onApply, onRetry }: {
  card: PendingCard; status: "open" | "applying" | "applied" | "retried"; busy: string | null;
  onApply: (id: string) => void; onRetry: (id: string) => void;
}) {
  const open = status === "open";
  return (
    <div className={`pcard ${open ? "" : "done"}`}>
      <div className="pc-head"><Icon name={card.replan ? "spark" : "scissors"} size={13} /><b>{card.replan ? "Re-plan, before it runs" : "Before it runs"}</b>
        <span className="grow" /><span className="mono mute pc-est" title="Estimated cost and time of applying it">{card.usd ? `~${usd(card.usd)}` : "$0"} · ~{card.seconds < 90 ? `${Math.round(card.seconds)} s` : tc(card.seconds, 0)}</span></div>
      <ul>{card.lines.map((l, k) => <li key={k}>{l}</li>)}</ul>
      {open ? (
        <div className="pc-acts">
          <button className="btn small" disabled={!!busy} title={busy ?? "Make the new version"} onClick={() => onApply(card.id)}><Icon name="check" size={12} />Apply</button>
          {card.can_retry && <button className="btn quiet small" disabled={!!busy} title={busy ?? "Montivo proposes another way, once (one routing call, no review)"} onClick={() => onRetry(card.id)}>Try something else</button>}
        </div>
      ) : <div className="pc-state mute small">{status === "applied" ? "Applied" : status === "applying" ? "Applying…" : "Turned down: another try below"}</div>}
    </div>
  );
}

/** Up to three prompts the last review suggests, else the common asks. A click puts the words in the box. */
function suggestions(state: RunState, v: Version | null): string[] {
  if (!v) return []; // nothing to ask about before the first cut
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
          <MarkLoader />
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
        <div className="tr-top"><MarkLoader /><b>{job.label}…</b><span className="grow" /><button className="tr-stop" onClick={onStop}><Icon name="stop" size={10} />Stop</button></div>
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

/** What is selected in the editor right now: clips on the timeline, words in the transcript, a caption line. */
export type Selection = { clips: string[]; words: NonNullable<AskScope["words"]> | null; caption: AskScope["caption"] | null };

/** The transcript's word selection as the agent's scope: one range per clip, with its words. */
export function wordScope(flat: FlatWord[], sel: WordSel | null): Selection["words"] {
  const r = selRange(flat, sel);
  if (!r) return null;
  const out: NonNullable<Selection["words"]> = [];
  for (const x of flat.slice(r[0], r[1] + 1)) {
    const last = out.at(-1);
    if (last && last.span === x.span.id) { last.to = x.w.i; last.text += ` ${x.w.w}`; }
    else out.push({ span: x.span.id, from: x.w.i, to: x.w.i, text: x.w.w });
  }
  return out;
}

/** A message typed while a job runs: it waits here and is sent when the job ends (never dropped, never sent over it).
 *  `retried`: the server said busy once (a job started a moment before): it waits for that one too, then it is refused. */
type Queued = { text: string; review: number; planner: Planner; scope: AskScope; chips: string[]; retried?: boolean };
/** A message the server refused: out of the queue (the next one goes), shown with why until it is taken back. */
type Refused = { text: string; why: string };
const BUSY = /is (still )?running|try again|rendering|being made/i;
const queueKey = (id: string) => `montivo.queue.${id}`;

export function Assistant({ state, versions, lines, job, seen, progress, replay, v, selection, getTime, focusNonce, prefill, onSend, onStop, onPick, onSeek, onCompare, onRestore, onApply, onRetry, onRetryJob, onGlossary }: {
  state: RunState; versions: Version[]; lines: LogLine[]; job: Job | null; seen: Seen; progress: Progress | null; replay: number; v: Version | null;
  selection: Selection; getTime: () => number; focusNonce: number;
  prefill?: { text: string; nonce: number } | null; // Monti's hand-off: an edit request put in the box, not sent
  onSend: (text: string, chips: string[], review: number, planner: Planner, scope: AskScope, latest: boolean) => Promise<true | string>; onStop: () => void; // a string: why it was refused
  onPick: (n: number) => void; onSeek: (t: number) => void; onCompare: (a: number, b: number) => void;
  onRestore: ((n: number) => void) | null; // null while an edit runs or in replay
  onApply: (pending: string, review: number) => void; onRetry: (pending: string) => void; onRetryJob: () => void;
  onGlossary: ((add: Record<string, string>) => void) | null; // C3: accept a suggested name fix (null while an edit runs)
}) {
  const [hiddenSugg, hideSugg] = useHiddenSuggestions(state.id);
  const [text, setText] = useState("");
  const [use, setUse] = useState({ clips: true, words: true, caption: true });
  const [time, setTime] = useState<number | null>(null);
  const [review, setReview] = useState(true);
  const [planner, setPlanner] = useState<Planner>("spark");
  const [queue, setQueue] = useState<Queued[]>(() => {
    try { return (JSON.parse(localStorage.getItem(queueKey(state.id)) ?? "[]") as (Queued & { failed?: boolean })[]).map(({ failed: _, ...q }) => q); } catch { return []; }
  });
  const [refused, setRefused] = useState<Refused[]>([]);
  const items = story(state, versions, lines, job, replay);
  const end = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { // scroll the messages only: scrollIntoView also moved the whole page on a phone
    const list = end.current?.parentElement;
    if (list) list.scrollTop = list.scrollHeight;
  }, [items.length, job?.running, queue.length]);
  const selKey = JSON.stringify(selection);
  useEffect(() => setUse({ clips: true, words: true, caption: true }), [selKey]); // a new selection is in until clicked off
  useEffect(() => { if (focusNonce) { setUse({ clips: true, words: true, caption: true }); box.current?.focus(); } }, [focusNonce]);
  useEffect(() => { if (prefill?.text) { setText(prefill.text); window.setTimeout(() => box.current?.focus(), 30); } }, [prefill?.nonce]); // eslint-disable-line
  useEffect(() => { try { localStorage.setItem(queueKey(state.id), JSON.stringify(queue)); } catch { /* this tab only */ } }, [queue, state.id]);
  useLayoutEffect(() => { // the box grows with the text, up to about six lines
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 150)}px`;
  }, [text]);
  const scope: AskScope = {
    ...(use.clips && selection.clips.length ? { clips: selection.clips } : {}),
    ...(use.words && selection.words?.length ? { words: selection.words } : {}),
    ...(use.caption && selection.caption ? { caption: selection.caption } : {}),
    ...(time != null ? { t: time } : {}),
  };
  const chips = scopeChips(scope);
  const cost = state.has_key ? (state.costs.ask ?? 0.006) + (review ? state.costs.review : 0) : 0;
  const running = !!job?.running;
  const blocked = replay ? "Replay is on: turn it off in the menu to edit." : null;
  // a queued message goes once the job has ended; `waiting` holds the next one until the job it started is seen
  const waiting = useRef(0);
  useEffect(() => { if (running) waiting.current = 0; }, [running]);
  // a send the server refused leaves the queue with why (the next one goes, nothing waits behind it); only "busy" (a job
  // started a moment before) puts it back first, once, to go when that job ends
  const post = (q: Queued, queued: boolean) => {
    waiting.current = Date.now();
    // a queued message edits the newest version (the job it waited for made one), not the one on screen when it was typed
    onSend(q.text, q.chips, q.review, q.planner, q.scope, queued).then((r) => {
      if (r === true) return;
      waiting.current = 0;
      if (BUSY.test(r) && !q.retried) setQueue((x) => [{ ...q, retried: true }, ...x]);
      else setRefused((x) => [...x, { text: q.text, why: r }]);
    });
  };
  useEffect(() => {
    const q = queue[0];
    if (running || replay || !q || Date.now() - waiting.current < 8000) return;
    setQueue(queue.slice(1));
    post(q, true);
  }, [running, replay, queue, refused.length]); // eslint-disable-line -- a refusal lets the next one go at once
  const send = () => {
    if (!text.trim() || blocked) return;
    enqueue({ text: text.trim(), chips, review: review && state.has_key ? 1 : 0, planner, scope });
    setText(""); setTime(null);
  };
  const enqueue = (msg: Queued) => {
    if (running || queue.length) setQueue((q) => [...q, msg]);
    else post(msg, false);
  };
  // Fix it: the checks' faults as the next message on the newest version, no review (the router call is the cost)
  const fixIt = (c: AgentChecks) => enqueue({ text: fixText(c), chips: [], review: 0, planner, scope: {} });
  const askCost = state.costs.ask ?? 0.006;
  const tips = !blocked && !text && !queue.length ? suggestions(state, v) : [];
  const busyNote = running ? `${job!.label} is running` : blocked;
  const nWords = selection.words?.reduce((a, w) => a + w.to - w.from + 1, 0) ?? 0;
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
              {b.items.map((it, j) => it.kind === "text" ? <p key={j} className={`say ${it.tone ?? ""}`}>{it.text}
                  {it.seek?.map((t) => <button key={t} className="seek-at" onClick={() => onSeek(t)} title="Play from here"><Icon name="play" size={10} /> {tc(t)}</button>)}</p>
                : it.kind === "steps" ? <Steps key={j} title={it.title} lines={it.lines} live={it.live} />
                : it.kind === "version" ? <VersionCard key={j} id={state.id} v={it.v} by={it.by} current={it.v.n === v?.n} newest={it.v.n === versions.at(-1)?.n} onPick={onPick} onCompare={onCompare} onRestore={onRestore} />
                : it.kind === "checks" ? (
                  <div key={j} className={`say checks ${it.checks.ok ? "ok" : "warn"}`}>
                    <p>{checkedText(it.checks)}</p>
                    {!it.checks.ok && it.v.n === versions.at(-1)?.n && state.has_key && !blocked && (
                      <div className="err-acts">
                        <button className="btn small" onClick={() => fixIt(it.checks)}
                          title={`Sends “${fixText(it.checks)}” as your next message: one Muse Spark call picks the edits (about ${usd(askCost, 3)}), the render is $0, no review`}>
                          <Icon name="spark" size={12} />Fix it</button>
                        <span className="mute small">about {usd(askCost, 3)}, render $0</span>
                      </div>)}
                  </div>)
                : it.kind === "review" ? <ReviewCard key={j} v={it.v} current={it.v.n === v?.n} onPick={onPick} onSeek={onSeek} />
                : it.kind === "mishear" ? (() => {
                    const items = openSuggestions(it.v, versions.at(-1), hiddenSugg);
                    return items.length ? (
                      <div key={j} className="say">
                        <p>Voice may have misheard {items.length === 1 ? "a name" : `${items.length} names`}. The captions say what it heard until you confirm:</p>
                        <MishearList items={items} disabled={replay ? "Not in a replay" : onGlossary ? null : "An edit is running"}
                          onHide={(s) => hideSugg(s.heard)} onAccept={(s) => onGlossary?.({ [s.heard]: s.written })} />
                      </div>) : null;
                  })()
                : it.kind === "preview" ? <PreviewCard key={j} card={it.card} status={it.status} busy={busyNote}
                    onApply={(pid) => onApply(pid, review && state.has_key ? 1 : 0)} onRetry={onRetry} />
                : it.kind === "error" ? (
                  <div key={j} className="say error">
                    <p>{it.text}</p>
                    <div className="err-acts">
                      {it.retry && <button className="btn small" disabled={!!busyNote} onClick={onRetryJob} title="Run the same action once more">Try again</button>}
                      {!/charged|spent/.test(it.text) && it.charged === 0 && <span className="mute small">Nothing was charged.</span>}
                      {!/charged|spent/.test(it.text) && !!it.charged && <span className="mute small">It spent {usd(it.charged, 3)} before it stopped.</span>}
                    </div>
                  </div>
                ) : null)}
            </div>
          </div>
        ))}
        <div ref={end} />
      </div>
      {tips.length > 0 && <div className="sugg">{tips.map((s) => <button key={s} onClick={() => { setText(s); box.current?.focus(); }}>{s}</button>)}</div>}
      <div className="composer" data-monti="chat.composer">
        <Tray state={state} lines={lines} job={job} seen={seen} progress={progress} replay={replay} onStop={onStop} />
        {refused.map((r, k) => (
          <div key={`r${k}`} className="queued refused" role="status">
            <Icon name="flag" size={12} />
            <span className="q-text" title={r.text}>{r.text}</span>
            <button className="ib" onClick={() => setRefused((x) => x.filter((_, i) => i !== k))} title="Dismiss" aria-label="Dismiss the refused message"><Icon name="close" size={11} /></button>
            <div className="q-foot">
              <span className="small">Not sent: {r.why}</span>
              <button className="btn quiet small" onClick={() => { setRefused((x) => x.filter((_, i) => i !== k)); setText(r.text); box.current?.focus(); }}
                title="Put the message back in the box to change it or send it again">Edit</button>
            </div>
          </div>
        ))}
        {queue.map((q, k) => (
          <div key={k} className="queued">
            <Icon name="clock" size={12} />
            <span className="q-text" title={q.text}>{q.text}</span>
            <button className="ib" onClick={() => setQueue((x) => x.filter((_, i) => i !== k))} title="Take this message back" aria-label="Remove the queued message"><Icon name="close" size={11} /></button>
            <div className="q-foot">
              <span className="mute small">{running ? `Waiting: it goes when ${job!.label} ends.` : k ? "Next after the one above." : "Sending…"}</span>
              {running && k === 0 ? <button className="btn quiet small" onClick={onStop} title="Stop the running job; this message goes as soon as it has stopped">Send now (stops the current job)</button> : null}
            </div>
          </div>
        ))}
        <div className={`cbox ${blocked ? "off" : ""}`}>
          <textarea ref={box} value={text} onChange={(e) => setText(e.target.value)} rows={2}
            placeholder={blocked ?? (running ? `${job!.label} is running: type your next message, it waits and goes when the job ends`
              : state.has_key ? "Ask Montivo… “add some lo-fi music”, “bold captions”, “make it 30 seconds”"
              : `${state.key_note ?? "No MODEL_API_KEY"}: Montivo reads keywords only… “add music”, “bold pop captions”, “delete these clips”`)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} disabled={!!blocked} />
          <div className="cb-ctx">
            {selection.clips.length > 0 && <button className={`pill sel ${use.clips ? "on" : ""}`} onClick={() => setUse({ ...use, clips: !use.clips })}
              title={`Send the selected clip${selection.clips.length > 1 ? "s" : ""} with the message: “this” means ${selection.clips.join(", ")}`}>
              <Icon name="film" size={12} />{selection.clips.length === 1 ? `clip ${selection.clips[0]}` : `${selection.clips.length} clips`}</button>}
            {selection.words?.length ? <button className={`pill sel ${use.words ? "on" : ""}`} onClick={() => setUse({ ...use, words: !use.words })}
              title={`Send the selected words with the message: “${selection.words.map((w) => w.text).join(" … ")}”`}>
              <Icon name="captions" size={12} />{scopeChips({ words: selection.words })[0] ?? `${nWords} words`}</button> : null}
            {selection.caption && <button className={`pill sel ${use.caption ? "on" : ""}`} onClick={() => setUse({ ...use, caption: !use.caption })}
              title={`Send the selected caption line with the message: “${selection.caption.text}”`}>
              <Icon name="captions" size={12} />caption {tc(selection.caption.t_in)}</button>}
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
            <span className="cost" title={state.has_key ? `One Muse Spark 1.3 call picks the edits${review ? ", plus one blind review" : ""}. A re-plan (~${usd(planner === "open" ? state.costs.plan_open : state.costs.plan)} on ${planner === "open" ? "Muse Spark Open" : "Muse Spark 1.3"}), deleting or moving clips, and anything over $0.02 show a preview first: nothing runs until you Apply. Music, look, captions and callouts are $0 and apply at once.` : "Without the key Montivo reads keywords: $0"}>~{usd(cost)}{state.budget ? <span className="mute"> · {usd(state.budget.left)} left</span> : null}</span>
            <button className="send" disabled={!!blocked || !text.trim()} onClick={send} title={running ? "Queue it: it goes when the running job ends (Enter)" : "Send (Enter)"}><Icon name={running ? "clock" : "send"} size={15} /></button>
          </div>
        </div>
      </div>
    </section>
  );
}
