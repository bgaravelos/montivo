// Shapes served by montivo/server/runs.py (read straight from the run folder)

export type Span = {
  id: string; order: number; turn_id: number; speaker: string; role: string; words: string;
  t_in: number; t_out: number; duration: number; word_from: number; word_to: number;
  why: string; callout: string; contiguous_with_previous: boolean; verbatim: boolean;
};

export type Issue = {
  k: number; criterion: string; severity: "blocker" | "major" | "minor" | string; t_s: number; t_end_s: number;
  what: string; fix_op: string; fix_args?: string; params?: Record<string, unknown>;
};

export type PlannedFix = { op: string; target: string; issue: number; severity: string; t_s?: number; args?: Record<string, unknown>; note?: string };

export type Review = {
  name: string; overall: number; pass: boolean; scores: Record<string, number>; summary: string; issues: Issue[];
  fixes: PlannedFix[]; skipped: { issue: number; reason: string }[]; facts_text: string;
  facts: { silences: number; over_face: number; caption_lines: number; cut_edges: number; loud_edges: number };
  cost_usd: number; seconds: number; duration_s: number; clock: boolean; has_copy: boolean;
};

export type Origin = {
  source: "plan" | "review" | "human" | string; from?: number; review?: string | null; action?: string;
  instruction?: string | null; fixes?: PlannedFix[];
};

export type Version = {
  n: number; edl: string; cut: string | null; runtime_s: number; duration_s: number | null;
  title_card: boolean; title_card_text: string | null; title_s: number; spans: Span[]; origin: Origin;
  loudness: { integrated_lufs: number; true_peak_dbtp: number; lra_lu: number } | null; captions: number | null;
  callouts: { span: string; text: string; t_in: number; t_out: number }[]; review: Review | null;
};

export type Stage = { id: string; label: string; model: string; artefact: string | null; cost_usd: number; seconds: number; done: boolean };

export type Word = { w: string; i: number; t: number; e: number };

export type Job = { id: string; run: string; kind: string; label: string; started: number; ended: number | null; rc: number | null; running: boolean; tail: string };

export type LogLine = { i: number; t: number; msg: string; stage: string | null; session: number };

export type RunState = {
  id: string;
  run: { footage?: string; brief?: string; target_s?: number; hflip?: boolean; started?: string };
  summary: Record<string, unknown> | null;
  ledger: { total_cost_usd: number; wall_seconds: number; stages: Record<string, { seconds: number; cost_usd: number; calls: number; tokens_in: number; tokens_out: number }> };
  loop: { stop: string; passes: { version: number; overall: number; pass: boolean }[]; best: { version: number; overall: number } | null } | null;
  versions: Version[]; words: Record<string, Word[]>; stages: Stage[]; job: Job | null; has_key: boolean;
  costs: { plan: number; review: number; run_per_s: number };
};

export type RunCard = {
  id: string; footage: string; brief: string; started: string | null; versions: number[]; scores: number[];
  stop: string | null; cost_usd: number | null; has_cut: boolean; job: Job | null;
};
