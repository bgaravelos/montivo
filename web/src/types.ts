// Shapes served by montivo/server/runs.py (read straight from the run folder)

export type Span = {
  id: string; order: number; turn_id: number; speaker: string; role: string; words: string;
  t_in: number; t_out: number; duration: number; word_from: number; word_to: number;
  why: string; callout: string; contiguous_with_previous: boolean; verbatim: boolean;
  callout_xy: [number, number] | null; // a callout a person pinned on the canvas (montivo.human style)
  look?: Look | null; // a look of the clip's own for its captions and callout (S4, montivo.human style --clip)
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
  name: string; carried_from?: number | null; overall: number; pass: boolean; scores: Record<string, number>; summary: string; issues: Issue[];
  reviewer_pass: boolean; gate: Gate | null; // pass is the gated pass, reviewer_pass the reviewer's own
  fixes: PlannedFix[]; skipped: { issue: number; reason: string }[]; facts_text: string;
  heard_diff?: { t: number; caption: string; heard: string }[]; // caption words vs the words Voice heard in the cut (C3)
  facts: { silences: number; over_face: number; caption_lines: number; cut_edges: number; loud_edges: number };
  cost_usd: number; seconds: number; duration_s: number; clock: boolean; has_copy: boolean;
};

export type Origin = {
  source: "plan" | "review" | "human" | string; from?: number; review?: string | null; action?: string;
  instruction?: string | null; fixes?: PlannedFix[]; result?: HookResult | null;
  challenger?: string | null; why?: string | null; // hook A/B: the span B opens on, and why it was picked
  agent?: AgentRecord | null; // Ask Montivo: the message, the agent's reply and the ops it ran (montivo/agent.py)
};

/** What Ask Montivo did with one message (the version's patch `agent`, montivo/agent.py). */
export type AgentRecord = { message: string; reply: string; router: string; ops: { op: string }[]; done: string[]; skipped: string[];
  pending?: string; scope?: AskScope; retry_of?: string }; // pending: the proposal this version applied

/** What the editor selected when they asked: the agent reads it as "this" (montivo/agent.py norm_scope). */
export type AskScope = { clips?: string[]; words?: { span: string; from: number; to: number; text: string }[];
  caption?: { t_in: number; t_out: number; text: string }; t?: number };

/** A structural or costly ask waiting for Apply (agent.py preview_card): what changes, cost and time. */
export type PendingCard = { id: string; lines: string[]; usd: number; seconds: number; clips_before: number; runtime_before: number;
  runtime_after: number; replan: boolean; review: number; can_retry: boolean };

/** One line of ui/chat.jsonl: your messages and the agent's replies (an `ask` reply without a version is shown from here). */
export type ChatLine = { t: number; role: "user" | "agent"; kind?: string; text?: string; version?: number | null;
  after?: number | null; message?: string | null; router?: string | null;
  pending?: PendingCard | string | null; scope?: AskScope | null; retry_of?: string | null; // a preview line's card; a user line's proposal id
  job?: string; charged_usd?: number | null }; // an error line: the job that failed and what it spent

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
  draft?: boolean; // the live preview's open draft (server/draft.py): no cut yet, the timeline's media come from the source
  n: number; edl: string; cut: string | null; runtime_s: number; duration_s: number | null;
  title_card: boolean; title_card_text: string | null; title_s: number; spans: Span[]; origin: Origin;
  loudness: { integrated_lufs: number; true_peak_dbtp: number; lra_lu: number } | null; captions: number | null;
  callouts: { span: string; text: string; t_in: number; t_out: number; box: number[] | null; placement: string | null;
    head_box: number[] | null; over_face: boolean }[]; review: Review | null;
  look: Look | null; // the caption look this version was rendered with (graphics.normalize_look)
  music: (MusicSetting & { voice_db: number; margin_db: number | null; ducked_s: number | null; ducked: [number, number][] }) | null; // the bed under the speech (engine/music.py)
  glossary?: Record<string, string>; // the run's caption corrections {"heard words": "written words"} (C2)
  highlight?: { words: string[]; shown: number; sentences: number }; // the coloured names: which, how many on screen, in how many sentences (S2)
  caption_lines: CaptionLine[]; caption_style: CaptionStyle | null; caption_style_note: string | null; brief_missing?: string[]; bytes: number | null;
  punch: boolean; punch_skip: string[]; punch_ins: PunchIn[];
  speaker_pins: Record<string, string>; // reframe_speaker_ids: Voice speaker -> the head the crop follows (a human pin)
  speaker_names?: Record<string, string>; // SP1: "A" -> "Billy"
  speaker_merge?: Record<string, string>; // SP2: "B" -> "A", one person Voice split in two
  brief_edited?: string | null; // ME2: the brief this version was re-planned from (else the run's)
  planner: { model: string; name: string } | null; // the paper edit's planner: v1 and every re-plan
  overlays: Overlay[]; // the B-roll stills (engine/broll.py): cut seconds from the render, null when not shown
  plan_compare: PlanCompare | null;
};

/** A B-roll still over the cut (EDL `overlays`): a Muse Image picture a person asked for, anchored to a source second. */
export type Overlay = {
  id: string; placement: "full" | "upper" | "pip"; style: string | null; prompt: string | null; image: string | null;
  dur: number; span: string | null; t_in: number | null; t_out: number | null; shown: boolean; why: string | null;
};

/** What "Remove pauses" would shorten (GET /pauses, montivo.human pauses --dry-run). */
export type PausePlan = {
  over_s: number; keep_s: number; count: number; saved_s: number;
  pauses: { span: string; word: number; after: string; before: string; was_s: number; keep_s: number; saved_s: number; t_cut: number | null }[];
  skipped: { span: string; why: string; was_s: number }[];
};

/** A punch-in zoom of a rendered version (EDL `punch_ins`); `key` names it across versions (montivo.human punch --skip). */
export type PunchIn = {
  key: string; kind: "emphasis" | "jump_cut" | string; word: string | null; span: string | null; excess_db: number | null;
  t_in: number; t_out: number; zoom: number;
};

export type CaptionLine = { id: string; text: string; t_in: number; t_out: number; spans: string[]; placement: string | null; over_face: boolean;
  box: number[] | null; head_box: number[] | null; // [x, y, w, h] and the head [x0, y0, x1, y1] in the 1080 x 1920 frame
  heard: string | null; // heard: the words Voice heard, when the line was retexted (by a human or the reviewer)
  words?: [string, number][] }; // each word and its first cut second (a hand line break anchors there, C4)

/** The caption and callout look (engine/graphics.py Look): a preset and every choice on top of it. */
export type Look = {
  preset: string; font: string; size: number; case: "as_said" | "upper"; max_words: number; text: string; stroke: string;
  stroke_w: number; shadow: boolean; active: "pill" | "color" | "karaoke" | "none"; active_fg: string; active_bg: string;
  highlight: string; box: string; position: "auto" | "fixed"; top: number; callout_bg: string; callout_fg: string;
  highlight_add: string[]; highlight_off: string[];
};
/** A music bed (EDL `music`): a bundled track, its level where nobody speaks (LUFS), the drop under speech (dB) and the
 *  timeline's Voice fader (dB on the speech against it). */
// start_s: where in the loop the bed starts; fade_out_s: its fade at the end (M4); duck_edits: windows held down or
// lifted by hand, in cut seconds (M5, engine/music.py edit_ducks; a structural edit carries them with their words, follow_cut)
export type MusicSetting = { track: string; level_db: number; duck_db: number; voice_db?: number; start_s?: number; fade_out_s?: number; duck_edits?: DuckEdit[] };
export type DuckEdit = { a: number; b: number; duck: boolean };
export type MusicInfo = {
  tracks: { id: string; name: string; mood: string; loop_s: number; licence: string; author: string; source: string; url: string }[];
  default: { level_db: number; duck_db: number }; level_range: [number, number]; duck_range: [number, number];
};
// with music or the title card a saved look is a brand kit (S3, engine/kit.py): one click puts all of it on a version
export type SavedLook = { id: string; name: string; look: Look; saved: string; music?: MusicSetting | "off"; title_card?: boolean };
export type LooksInfo = { presets: { id: string; name: string; look: Look }[]; fonts: { id: string; name: string }[]; default: Look; saved: SavedLook[] };

export type CaptionStyle = { font?: number; max_words?: number; default_top?: number; font_path?: string; glossary?: Record<string, string> };

export type Media = {
  footage: string | null; width: number | null; height: number | null; fps: number | null; duration: number | null;
  bytes: number | null; codec: string | null; hflip: boolean; proxy: string | null; title_png: string | null;
  words: number; turns: number; speakers: number;
};

export type Stage = { id: string; label: string; model: string; artefact: string | null; cost_usd: number; seconds: number; done: boolean };

export type Word = { w: string; i: number; t: number; e: number };

export type Job = { id: string; run: string; kind: string; label: string; started: number; ended: number | null; rc: number | null; running: boolean; tail: string;
  charged_usd?: number | null }; // what the run's ledger grew by during the job, once it ended

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
  costs: Costs; key_note?: string | null; budget?: Budget | null; chat?: ChatLine[];
};

/** Rough prices and times for the button hints (server COST). */
export type Costs = { ask?: number; titles?: number; plan: number; plan_open: number; plan_s: number; plan_open_s: number; review: number; run_per_s: number;
  review_s?: number; review_prep_s?: number; image_s?: number; render_s_per_frame?: number;
  run_fixed?: number; run_wall_fixed_s?: number; run_wall_per_s?: number; review_loop?: number; review_loop_s?: number; hook_ab?: number; look?: number; cover?: number;
  cover_design?: number; cover_mask?: number };

export type Planner = "spark" | "open";

export type RunCard = {
  id: string; footage: string; brief: string; started: string | null; versions: number[]; scores: number[];
  stop: string | null; cost_usd: number | null; has_cut: boolean; job: Job | null; progress: Progress | null;
  card: ProjectCard | null;
  kind?: "sample" | "copy" | "own"; // the hosted demo only: a seeded sample, this judge's copy of one, or their own project
};

/** A project's card on Home (runs.project_card): the version the editor opens on, its poster time, its blind score
 *  (or the best one when it has none) and every reviewed version's score in order. */
export type ProjectCard = {
  shown: number; title_card_text: string | null; duration_s: number | null; poster_t: number;
  score: CardScore | null; score_is_shown: boolean; scores: CardScore[];
};
export type CardScore = { v: number; overall: number; pass: boolean };

/** The Speakers panel (runs.heads): Voice speakers with the heads the speaker map gave them, and every SAM head. */
/** SP4: the tracked heads on screen at a cut second (GET /heads/at), frame pixels [x0, y0, x1, y1]. */
export type HeadsAt = { t: number; src_t: number | null; speaker: string | null; span: string | null; heads: { id: string; box: number[] }[] };

export type HeadsInfo = {
  speakers: { id: string; who: string | null; ids: string[]; method: string | null; words: number }[];
  heads: { id: string; t: number; box: number[]; frames: number; speaker: string | null }[];
  video: string | null;
};

/** The hosted demo's spend cap on Meta model calls (null on a laptop). */
export type Budget = { cap: number; spent: number; left: number };
