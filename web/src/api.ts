import { useCallback, useEffect, useRef, useState } from "react";
import type { Job, LogLine, RunState } from "./types";

export async function api<T = unknown>(path: string, body?: unknown, method?: string): Promise<T> {
  const res = await fetch(path, {
    method: method ?? (body === undefined ? "GET" : "POST"),
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data as { error?: string }).error ?? `${res.status} ${res.statusText}`);
  return data as T;
}

export const fileUrl = (run: string, path: string) => `/api/runs/${run}/file/${path}`;
export const frameUrl = (run: string, v: number, t: number, clean = false) => `/api/runs/${run}/frame?v=${v}&t=${t.toFixed(1)}${clean ? "&clean=1" : ""}`;

/** In replay, what the log has shown so far: the stages it reached, the cuts rendered and the reviews returned. */
export type Seen = { stages: Set<string>; running: string | null; cuts: Set<number>; reviews: Set<number>; ended: boolean };

const CUT_DONE = /^render: \S*cut_v(\d+)\.mp4 \(/;
const REVIEW_DONE = /^review: review_v(\d+) overall/;

/** The run's state from disk, plus run.log as a live (or replayed) stream of stage-tagged lines. */
export function useRun(id: string, replay: number) {
  const [state, setState] = useState<RunState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lines, setLines] = useState<LogLine[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [seen, setSeen] = useState<Seen>(() => fresh());
  const timer = useRef<number | null>(null);

  const reload = useCallback(() => {
    api<RunState>(`/api/runs/${id}`).then((s) => { setState(s); setJob(s.job); setError(null); }).catch((e) => setError(String(e.message ?? e)));
  }, [id]);

  useEffect(() => {
    reload();
    setLines([]);
    setSeen(fresh());
    const es = new EventSource(`/api/runs/${id}/events${replay ? `?replay=${replay}` : ""}`);
    const later = () => { // a burst of file changes -> one reload
      if (timer.current) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(reload, 350);
    };
    es.addEventListener("line", (ev) => {
      const ln = JSON.parse((ev as MessageEvent).data) as LogLine;
      setLines((prev) => (prev.length > 1500 ? [...prev.slice(-1200), ln] : [...prev, ln]));
      setSeen((s) => {
        const n = { ...s, stages: new Set(s.stages), cuts: new Set(s.cuts), reviews: new Set(s.reviews) };
        if (ln.stage && ln.stage !== "meta" && ln.stage !== "error") { n.stages.add(ln.stage); n.running = ln.stage; }
        const c = ln.msg.match(CUT_DONE); if (c) n.cuts.add(Number(c[1]));
        const r = ln.msg.match(REVIEW_DONE); if (r) n.reviews.add(Number(r[1]));
        return n;
      });
      if (!replay && (CUT_DONE.test(ln.msg) || REVIEW_DONE.test(ln.msg) || ln.msg.startsWith("patch v"))) later();
    });
    es.addEventListener("job", (ev) => { setJob(JSON.parse((ev as MessageEvent).data)); later(); });
    es.addEventListener("changed", later);
    es.addEventListener("replay_end", () => setSeen((s) => ({ ...s, running: null, ended: true })));
    return () => { es.close(); if (timer.current) window.clearTimeout(timer.current); };
  }, [id, replay, reload]);

  return { state, error, lines, job, seen, reload };
}

function fresh(): Seen {
  return { stages: new Set(), running: null, cuts: new Set(), reviews: new Set(), ended: false };
}

// ------------------------------------------------------------------- formatting

export function tc(s: number | null | undefined, digits = 1): string {
  if (s == null || !isFinite(s)) return "–";
  const m = Math.floor(s / 60);
  const r = s - m * 60;
  return `${m}:${r.toFixed(digits).padStart(digits ? 3 + digits : 2, "0")}`;
}

export const usd = (x: number | null | undefined, d = 2) => (x == null ? "–" : `$${x.toFixed(d)}`);

export function dur(s: number): string {
  if (s < 90) return `${Math.round(s)} s`;
  const m = Math.round(s / 60);
  return m < 90 ? `${m} min` : `${(m / 60).toFixed(1)} h`;
}

export const CRITERIA: Record<string, string> = {
  hook_in_1_5s: "Hook in 1.5 s",
  brief_covered: "Brief covered",
  pacing: "Pacing",
  no_cut_mid_word: "No cut mid-word",
  captions_match_speech: "Captions match speech",
  captions_legible: "Captions legible",
  subject_framed: "Subject framed",
  callouts_anchored: "Callouts anchored",
  ending: "Ending",
};
export const crit = (k: string) => CRITERIA[k] ?? k.replace(/_/g, " ");
