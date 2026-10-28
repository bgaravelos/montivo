// Shapes served by montivo/server/runs.py (read straight from the run folder)

export type Span = {
  id: string; order: number; turn_id: number; speaker: string; role: string; words: string;
  t_in: number; t_out: number; duration: number; word_from: number; word_to: number;
  why: string; callout: string; contiguous_with_previous: boolean; verbatim: boolean;
  callout_xy: [number, number] | null; // a callout a person pinned on the canvas (montivo.human style)
};

export type Issue = {
  k: number; criterion: string; severity: "blocker" | "major" | "minor" | string; t_s: number; t_end_s: number;
  what: string; fix_op: string; fix_args?: string; params?: Record<string, unknown>;
  check?: string; // a measured fault of the pass gate (loud_edge, edge_word, caption_face, head_frame), not the reviewer's
};

export type PlannedFix = { op: string; target: string; issue: number; severity: string; t_s?: number; args?: Record<string, unknown> | null; note?: string;
  check?: string }; // check: the measured fault behind the fix, empty for a reviewer issue

/** Why a reviewed cut does not pass (review_vN.json pass_gate): measured checks, and the reviewer's own open majors. */
export type Gate = { blocked: boolean; reasons: string[]; checks: string[];
  open_majors: { t_s: number; criterion: string; severity: string }[]; issues: Issue[] };

export type Review = {
  name: string; overall: number; pass: boolean; scores: Record<string, number>; summary: string; issues: Issue[];
  reviewer_pass: boolean; gate: Gate | null; // pass is the gated pass, reviewer_pass the reviewer's own
  fixes: PlannedFix[]; skipped: { issue: number; reason: string }[]; facts_text: string;
  facts: { silences: number; over_face: number; caption_lines: number; cut_edges: number; loud_edges: number };
  cost_usd: number; seconds: number; duration_s: number; clock: boolean; has_copy: boolean;
};

export type Origin = {
  source: "plan" | "review" | "human" | string; from?: number; review?: string | null; action?: string;
  instruction?: string | null; fixes?: PlannedFix[]; result?: HookResult | null;
  challenger?: string | null; why?: string | null; // hook A/B: the span B opens on, and why it was picked
};

/** The planner A/B of a fresh re-plan against v1 (plan_compare_v1_vN.json): one side per planner. */
export type PlanSide = { planner: string; spans: number; runtime_s: number; verbatim_rate: number; unplaced: number; hook: string;
  plan_cost_usd: number | null; plan_seconds: number | null };
export type PlanCompare = { target_s: number; v1: PlanSide } & Record<string, PlanSide | number>;

/** The hook A/B's verdict on the version it made (engine/hook.py): which opener the blind reviewer preferred. */
export type HookResult = {
  winner: "a" | "b"; kept: number; reason: string; margin: number; decisive: boolean;
  a: { version: number; opener: string; overall: number; pass: boolean; hook: number | null };
  b: { version: number; opener: string; overall: number; pass: boolean; hook: number | null };
};

export type Version = {
  n: number; edl: string; cut: string | null; runtime_s: number; duration_s: number | null;
  title_card: boolean; title_card_text: string | null; title_s: number; spans: Span[]; origin: Origin;
  loudness: { integrated_lufs: number; true_peak_dbtp: number; lra_lu: number } | null; captions: number | null;
  callouts: { span: string; text: string; t_in: number; t_out: number; box: number[] | null; placement: string | null;
    head_box: number[] | null; over_face: boolean }[]; review: Review | null;
  look: Look | null; // the caption look this version was rendered with (graphics.normalize_look)
  music: (MusicSetting & { margin_db: number | null; ducked_s: number | null }) | null; // the bed under the speech (engine/music.py)
  caption_lines: CaptionLine[]; caption_style: CaptionStyle | null; caption_style_note: string | null; bytes: number | null;
  punch: boolean; punch_skip: string[]; punch_ins: PunchIn[];
  speaker_pins: Record<string, string>; // reframe_speaker_ids: Voice speaker -> the head the crop follows (a human pin)
  planner: { model: string; name: string } | null; // the paper edit's planner: v1 and every re-plan
  plan_compare: PlanCompare | null;
};

/** A punch-in zoom of a rendered version (EDL `punch_ins`); `key` names it across versions (montivo.human punch --skip). */
export type PunchIn = {
  key: string; kind: "emphasis" | "jump_cut" | string; word: string | null; span: string | null; excess_db: number | null;
  t_in: number; t_out: number; zoom: number;
};

export type CaptionLine = { id: string; text: string; t_in: number; t_out: number; spans: string[]; placement: string | null; over_face: boolean;
  box: number[] | null; head_box: number[] | null; // [x, y, w, h] and the head [x0, y0, x1, y1] in the 1080 x 1920 frame
  heard: string | null }; // heard: the words Voice heard, when the line was retexted (by a human or the reviewer)

/** The caption and callout look (engine/graphics.py Look): a preset and every choice on top of it. */
export type Look = {
  preset: string; font: string; size: number; case: "as_said" | "upper"; max_words: number; text: string; stroke: string;
  stroke_w: number; shadow: boolean; active: "pill" | "color" | "karaoke" | "none"; active_fg: string; active_bg: string;
  highlight: string; box: string; position: "auto" | "fixed"; top: number; callout_bg: string; callout_fg: string;
  highlight_add: string[]; highlight_off: string[];
};
/** A music bed (EDL `music`): a bundled track, its level where nobody speaks (LUFS) and the drop under speech (dB). */
export type MusicSetting = { track: string; level_db: number; duck_db: number };
export type MusicInfo = {
  tracks: { id: string; name: string; mood: string; loop_s: number; licence: string; author: string; source: string; url: string }[];
  default: { level_db: number; duck_db: number }; level_range: [number, number]; duck_range: [number, number];
};
export type LooksInfo = { presets: { id: string; name: string; look: Look }[]; fonts: { id: string; name: string }[]; default: Look };

export type CaptionStyle = { font?: number; max_words?: number; default_top?: number; font_path?: string; glossary?: Record<string, string> };

export type Media = {
  footage: string | null; width: number | null; height: number | null; fps: number | null; duration: number | null;
  bytes: number | null; codec: string | null; hflip: boolean; proxy: string | null; title_png: string | null;
  words: number; turns: number; speakers: number;
};

export type Stage = { id: string; label: string; model: string; artefact: string | null; cost_usd: number; seconds: number; done: boolean };

export type Word = { w: string; i: number; t: number; e: number };

export type Job = { id: string; run: string; kind: string; label: string; started: number; ended: number | null; rc: number | null; running: boolean; tail: string };

export type LogLine = { i: number; t: number; msg: string; stage: string | null; session: number };

/** Where a running job (or the replay) is, from its run.log lines (server runs.progress). The bar moves by time between
 *  floor and ceil (the checkpoints the log reached) against expected_s; the clock advances at `rate` log seconds per
 *  wall second, at most `cap` seconds past the last line (replay), until the next event. `recv`: when it arrived. */
export type Progress = {
  label: string; kind: string | null; version: number | null; stage: string | null; title: string; detail: string | null;
  floor: number; ceil: number; expected_s: number | null; expected_from: "ledger" | "hint" | null;
  stage_elapsed: number; job_elapsed: number; steps: { stage: string; title: string; seconds: number }[];
  finished: boolean; error: boolean; failed?: boolean; active: boolean; rate: number; cap: number | null; replay?: boolean;
  recv: number;
};

export type RunState = {
  id: string;
  run: { footage?: string; brief?: string; target_s?: number; hflip?: boolean; started?: string };
  media: Media;
  summary: Record<string, unknown> | null;
  ledger: { total_cost_usd: number; wall_seconds: number; stages: Record<string, { seconds: number; cost_usd: number; calls: number; tokens_in: number; tokens_out: number }> };
  loop: { stop: string; passes: { version: number; overall: number; pass: boolean }[]; best: { version: number; overall: number } | null } | null;
  versions: Version[]; words: Record<string, Word[]>; stages: Stage[]; job: Job | null; has_key: boolean; has_open_key: boolean;
  costs: Costs; key_note?: string | null; budget?: Budget | null;
};

/** Rough prices and times for the button hints (server COST). */
export type Costs = { plan: number; plan_open: number; plan_s: number; plan_open_s: number; review: number; run_per_s: number;
  review_s?: number; review_prep_s?: number; image_s?: number; render_s_per_frame?: number;
  run_fixed?: number; run_wall_fixed_s?: number; run_wall_per_s?: number; review_loop?: number; review_loop_s?: number; hook_ab?: number; look?: number };

export type Planner = "spark" | "open";

export type RunCard = {
  id: string; footage: string; brief: string; started: string | null; versions: number[]; scores: number[];
  stop: string | null; cost_usd: number | null; has_cut: boolean; job: Job | null; progress: Progress | null;
  card: ProjectCard | null;
};

/** A project's card on Home (runs.project_card): the version the editor opens on, its poster time, its blind score
 *  (or the best one when it has none) and every reviewed version's score in order. */
export type ProjectCard = {
  shown: number; title_card_text: string | null; duration_s: number | null; poster_t: number;
  score: CardScore | null; score_is_shown: boolean; scores: CardScore[];
};
export type CardScore = { v: number; overall: number; pass: boolean };

/** The Speakers panel (runs.heads): Voice speakers with the heads the speaker map gave them, and every SAM head. */
export type HeadsInfo = {
  speakers: { id: string; who: string | null; ids: string[]; method: string | null; words: number }[];
  heads: { id: string; t: number; box: number[]; frames: number; speaker: string | null }[];
  video: string | null;
};

/** The hosted demo's spend cap on Meta model calls (null on a laptop). */
export type Budget = { cap: number; spent: number; left: number };
