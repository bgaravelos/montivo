import { useEffect, useRef, useState } from "react";
import { MarkLoader } from "./icons";
import { tc } from "./api";
import type { Job, Progress } from "./types";

/** Re-render every `ms` while `on`, so the clocks and the time-driven bar move between two log lines. */
export function useTick(on: boolean, ms = 250) {
  const [, set] = useState(0);
  useEffect(() => {
    if (!on) return;
    const h = window.setInterval(() => set((n) => n + 1), ms);
    return () => window.clearInterval(h);
  }, [on, ms]);
}

/** The progress at this instant: the job's and the stage's seconds, and the bar's fraction (null: no honest estimate,
 *  the bar stays indeterminate). Between two events the clock runs at `rate`, never more than `cap` past the last line. */
export function measure(p: Progress, now = performance.now()) {
  const adv = p.active ? Math.min(((now - p.recv) / 1000) * p.rate, p.cap ?? Infinity) : 0;
  const stageDone = p.floor >= 1 && p.ceil >= 1; // a finished step keeps its time while the job clock runs on
  const stage = p.stage_elapsed + (stageDone ? 0 : adv);
  const job = p.job_elapsed + adv;
  let frac: number | null;
  if (p.finished || stageDone) frac = 1;
  else if (p.expected_s) frac = Math.min(Math.max(stage / p.expected_s, p.floor), p.ceil);
  else frac = p.floor > 0 ? p.floor : null;
  return { stage, job, frac };
}

/** Ask once for browser notifications, from the click that starts a long job (browsers only ask on a user gesture). */
export function askToNotify() {
  try { if ("Notification" in window && Notification.permission === "default") void Notification.requestPermission(); } catch { /* no API */ }
}

/** A job seen from another tab: the page title carries its progress, and when it ends while the tab is hidden the title
 *  says so (until the tab is seen again) and a browser notification fires if the viewer allowed them. */
export function useJobSignals(name: string, job: Job | null, progress: Progress | null) {
  const was = useRef<string | null>(null); // the id of the job that was running
  const [done, setDone] = useState<string | null>(null);
  useTick(!!job?.running, 1000);
  useEffect(() => {
    if (job?.running) { was.current = job.id; return; }
    if (!job || was.current !== job.id) return;
    was.current = null;
    const ok = !job.rc;
    const text = ok ? `${job.label} is done` : `${job.label} failed`;
    if (!document.hidden) return;
    setDone(ok ? "✓" : "✕");
    try { if ("Notification" in window && Notification.permission === "granted") new Notification(`Montivo · ${name}`, { body: text, icon: "/favicon.svg" }); } catch { /* no API */ }
  }, [job, name]);
  useEffect(() => {
    const seen = () => { if (!document.hidden) setDone(null); };
    document.addEventListener("visibilitychange", seen);
    return () => document.removeEventListener("visibilitychange", seen);
  }, []);
  const m = job?.running && progress?.active ? measure(progress) : null;
  const title = job?.running ? `${m?.frac != null ? `${Math.round(m.frac * 100)}% · ` : ""}${job.label} · Montivo`
    : done ? `${done} ${name} · Montivo` : "Montivo";
  useEffect(() => { document.title = title; }, [title]);
  useEffect(() => () => { document.title = "Montivo"; }, []);
}

const clock = (s: number) => tc(Math.max(s, 0), 0);
export const expTip = (p: Progress) => p.expected_s == null ? "No earlier call of this step to estimate from"
  : p.expected_from === "ledger" ? "Expected from this project's earlier calls of the same step (ledger.json)"
  : "Expected from the measured runs (the server's hints)";

/** The fill; null draws the indeterminate slide. Callers key it by the step's title, so a new step mounts a new bar and
 *  the fill never sweeps back from the last step's 100 %. */
export function Bar({ frac, small }: { frac: number | null; small?: boolean }) {
  return (
    <span className={`pbar ${small ? "small" : ""} ${frac == null ? "indet" : ""}`} role="progressbar"
      aria-valuemin={0} aria-valuemax={100} aria-valuenow={frac == null ? undefined : Math.round(frac * 100)}>
      <i style={frac == null ? undefined : { width: `${(frac * 100).toFixed(1)}%` }} />
    </span>
  );
}

const stepText = (p: Progress) => `${p.title}${p.detail ? ` · ${p.detail}` : ""}`;

/** The top bar: label and its clock, the step, the bar. */
export function ProgressInline({ p }: { p: Progress }) {
  useTick(p.active);
  const m = measure(p);
  return (
    <span className="prog-inline" title={`${stepText(p)} · ${clock(m.stage)}${p.expected_s ? ` of ~${clock(p.expected_s)}` : ""}`}>
      <MarkLoader /> <b>{p.label}</b> <span className="mono">{clock(m.job)}</span>
      <span className="prog-step">{stepText(p)}</span>
      <Bar key={p.title} frac={m.frac} small />
    </span>
  );
}

/** A Home card: the step and a thin bar. */
export function ProgressMini({ p }: { p: Progress }) {
  useTick(p.active, 500);
  const m = measure(p);
  return (
    <div className="prog-mini" title={stepText(p)}>
      <div className="pm-line"><MarkLoader /> <span>{p.label}: {p.title}{p.detail ? ` · ${p.detail}` : ""}</span><span className="mono mute">{clock(m.job)}</span></div>
      <Bar key={p.title} frac={m.frac} small />
    </div>
  );
}
