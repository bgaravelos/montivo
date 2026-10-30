// The paper edit as blocks in cut time, with what changed against the version it came from
import type { Issue, Span, Version, Word } from "./types";

export type Block = {
  key: string;
  kind: "title" | "span" | "ghost";
  span?: Span;
  t0: number; // cut time (ghosts: where they used to sit, zero length)
  t1: number;
  before: Word[]; // words the parent version kept and this one dropped, at the start of the span
  kept: Word[];
  after: Word[]; // ... and at its end
  label: string;
};

const key = (turn: number, idx: number) => `${turn}:${idx}`;

function keptSet(v: Version): Set<string> {
  const s = new Set<string>();
  for (const sp of v.spans) for (let i = sp.word_from; i <= sp.word_to; i++) s.add(key(sp.turn_id, i));
  return s;
}

export function parentOf(v: Version, all: Version[]): Version | undefined {
  const from = v.origin.from ?? (v.n > 1 ? v.n - 1 : undefined);
  return from == null ? undefined : all.find((x) => x.n === from);
}

export function layout(v: Version, parent: Version | undefined, words: Record<string, Word[]>): Block[] {
  const tw = (t: number) => words[String(t)] ?? [];
  const cur = keptSet(v);
  const blocks: Block[] = [];
  let t = 0;
  // the render is frame aligned, so its length differs from the spans' sum by a few frames: spread that over the clips
  // so the timeline ends where the video does
  const sum = v.spans.reduce((a, s) => a + s.duration, 0);
  const k = v.duration_s && sum ? (v.duration_s - v.title_s) / sum : 1;
  const f = k > 0.97 && k < 1.03 ? k : 1;
  if (v.title_s > 0) {
    blocks.push({ key: "title", kind: "title", t0: 0, t1: v.title_s, before: [], kept: [], after: [], label: v.title_card_text ?? "title card" });
    t = v.title_s;
  }
  const pSpans = parent?.spans ?? [];
  const overlap = (a: Span, b: Span) => a.turn_id === b.turn_id && a.word_from <= b.word_to && b.word_from <= a.word_to;
  const spanBlocks: { b: Block; pOrder: number }[] = [];
  const struck = new Set<string>(); // a word cut from the middle of a clip (a split) shows once, at the end of the first piece
  for (const sp of v.spans) {
    const all = tw(sp.turn_id);
    const p = pSpans.find((x) => overlap(x, sp));
    const before: Word[] = [];
    const after: Word[] = [];
    if (p) {
      const cut = (i: number) => !cur.has(key(sp.turn_id, i)) && !struck.has(key(sp.turn_id, i)) && !!all[i];
      for (let i = p.word_from; i < sp.word_from; i++) if (cut(i)) before.push(all[i]);
      for (let i = sp.word_to + 1; i <= p.word_to; i++) if (cut(i)) after.push(all[i]);
      for (const w of [...before, ...after]) struck.add(key(sp.turn_id, w.i));
    }
    const kept = all.slice(sp.word_from, sp.word_to + 1);
    const b: Block = { key: sp.id, kind: "span", span: sp, t0: t, t1: t + sp.duration * f, before, kept, after, label: sp.id };
    t += sp.duration * f;
    spanBlocks.push({ b, pOrder: p ? pSpans.indexOf(p) : Infinity });
  }
  // spans of the parent that this version dropped whole, at the place they had
  const ghosts = pSpans
    .map((p, order) => ({ p, order }))
    .filter(({ p }) => !v.spans.some((sp) => overlap(p, sp)))
    .map(({ p, order }) => ({ order, b: { key: `ghost-${p.id}`, kind: "ghost" as const, span: p, t0: 0, t1: 0, before: [], kept: tw(p.turn_id).slice(p.word_from, p.word_to + 1), after: [], label: p.id } }));
  const out: Block[] = [...blocks];
  for (const sb of spanBlocks) {
    for (const g of ghosts.filter((g) => g.order < sb.pOrder && !out.includes(g.b))) {
      g.b.t0 = g.b.t1 = sb.b.t0;
      out.push(g.b);
    }
    out.push(sb.b);
  }
  for (const g of ghosts) if (!out.includes(g.b)) { g.b.t0 = g.b.t1 = t; out.push(g.b); }
  return out;
}

/** What happened to issue k of version n's review: the fix the loop planned, the patch the next version carries,
 *  the score that criterion got there, or why it was skipped. */
export function chain(issue: Issue, v: Version, all: Version[]) {
  const rv = v.review!;
  const planned = rv.fixes.find((f) => f.issue === issue.k);
  const skipped = rv.skipped.find((s) => s.issue === issue.k);
  const next = all.find((x) => x.origin.source === "review" && x.origin.from === v.n && x.origin.review === rv.name);
  const applied = next?.origin.fixes?.find((f) => f.issue === issue.k);
  const before = rv.scores[issue.criterion];
  const after = next?.review?.scores[issue.criterion];
  return { planned, skipped, next, applied, before, after };
}

export function blockAt(blocks: Block[], t: number): Block | undefined {
  return blocks.find((b) => b.kind !== "ghost" && t >= b.t0 && t < b.t1) ?? (t >= 0 ? [...blocks].reverse().find((b) => b.kind !== "ghost") : undefined);
}

export const sevRank = (s: string) => (s === "blocker" ? 0 : s === "major" ? 1 : 2);

/** What a clip says as the captions show it (with the hand fixes and the glossary), else the words Voice heard. */
export function spanText(v: Version, sp: Span): string {
  const lines = v.caption_lines.filter((l) => l.spans.includes(sp.id));
  return lines.length ? lines.map((l) => l.text).join(" ") : sp.words;
}

/** A fix op (the reviewer's or the patch the engine applied) in plain words; the op itself stays in the tooltip. */
export function fixText(op: string, target?: string | null): string {
  const clip = target && /^s\d+$/.test(target) ? `clip ${target}` : "the clip";
  switch (op) {
    case "drop_span": return `Drop ${clip}`;
    case "extend_span": return `Lengthen ${clip}`;
    case "trim_span": return `Shorten ${clip}`;
    case "move_cut": return `Move the cut at ${clip}`;
    case "reframe_bias": return `Re-aim the crop on ${clip}`;
    case "caption_fix": case "caption_text": return "Fix the caption text";
    case "caption_move": return "Move the caption";
    case "caption_unforce": return "Let the caption find its place again";
    case "retime_callout": return "Retime the callout";
    case "swap_hook": return "Open on another line";
    case "reorder": return "Change the clip order";
    case "drop_title": return "Drop the title card";
    default: return op.replace(/_/g, " ");
  }
}

/** The newest version made from this one: redo after an undo. */
export function childOf(v: Version, all: Version[]): Version | undefined {
  return [...all].reverse().find((x) => x.n !== v.n && parentOf(x, all)?.n === v.n);
}

/** What made a version, in the editor's words. */
export function originLabel(v: Version): string {
  const o = v.origin;
  if (o.source === "plan") return "planned";
  if (o.source === "review") return "reviewer fix";
  if (o.source === "hook_ab") return "hook A/B";
  if (o.action === "drop") return "your edit · deleted a clip";
  if (o.action === "trim") return "your edit · trimmed a clip";
  if (o.agent && (o.action === "ask" || o.action === "replan")) return "asked Montivo";
  if (o.action === "replan") return "your edit · asked Montivo";
  if (o.action === "ask") return "asked Montivo";
  if (o.action === "caption") return `your edit · ${o.fixes?.length === 1 ? "a caption line" : `${o.fixes?.length ?? ""} caption lines`}`;
  if (o.action === "text") {
    const f = o.fixes ?? [];
    return f.length === 1 && f[0].op.startsWith("title") ? "your edit · the title card" : f.every((x) => x.op === "callout_text")
      ? `your edit · ${f.length === 1 ? "a callout" : `${f.length} callouts`}` : "your edit · title and callouts";
  }
  if (o.action === "reorder") return `your edit · moved clip ${o.fixes?.[0]?.target ?? ""}`;
  if (o.action === "cut") return "your edit · deleted words";
  if (o.action === "restore") return `your edit · restored v${o.from}`;
  if (o.action === "music") return `your edit · ${o.fixes?.[0]?.op === "music_off" ? "music off" : "music"}`;
  if (o.action === "pin") return `your edit · pinned speaker ${o.fixes?.[0]?.target ?? ""}`;
  if (o.action === "speakers") return `your edit · speakers`;
  if (o.action === "punch") {
    const op = o.fixes?.[0]?.op;
    return op === "punch_skip" ? "your edit · removed a punch-in" : `your edit · punch-ins ${op === "punch_off" ? "off" : "on"}`;
  }
  return "your edit";
}

export function originKind(v: Version): "plan" | "review" | "human" {
  return v.origin.source === "plan" ? "plan" : v.origin.source === "review" || v.origin.source === "hook_ab" ? "review" : "human";
}

/** What a punch-in is, in the editor's words. */
export function punchLabel(p: { kind: string; word: string | null; span: string | null }): string {
  return p.kind === "jump_cut" ? `Jump cut into ${p.span}` : `“${p.word}”`;
}

/** A human name for the project: the title card text, else the brief's first phrase. */
export function projectName(v: Version | null, brief: string | undefined): string {
  return nameOf(v?.title_card_text, brief);
}

/** A project's name: its title card's text, else the brief's first clause. */
export function nameOf(titleCardText: string | null | undefined, brief: string | undefined): string {
  if (titleCardText) return titleCardText;
  const b = (brief ?? "").split(/[.:\n]/)[0].trim();
  return b.length > 52 ? `${b.slice(0, 50).trim()}…` : b || "Untitled";
}

/** Cut-time positions an edge of a span block can snap to while dragging: the word boundaries of its turn, from two
 *  words inside the clip out to the words no other clip keeps, as far as the turn goes (`end`: its last word). */
export type Snap = { word: number; t: number; label: string; end?: boolean };

export function edgeSnaps(b: Block, edge: "in" | "out", v: Version, words: Record<string, Word[]>): Snap[] {
  const sp = b.span!;
  const tw = words[String(sp.turn_id)] ?? [];
  const taken = new Set<number>();
  for (const s of v.spans) if (s.id !== sp.id && s.turn_id === sp.turn_id) for (let i = s.word_from; i <= s.word_to; i++) taken.add(i);
  const out: Snap[] = [];
  const f = sp.duration > 0 ? (b.t1 - b.t0) / sp.duration : 1; // the layout's per-cut scale (frame alignment)
  const at = (src: number) => b.t0 + (src - sp.t_in) * f;
  if (edge === "out") {
    for (let i = sp.word_from + 1; i < tw.length; i++) {
      if (i > sp.word_to && taken.has(i)) break;
      out.push({ word: i, t: i === sp.word_to ? b.t1 : at(tw[i].e), label: tw[i].w, end: i === tw.length - 1 });
    }
  } else {
    for (let i = sp.word_to - 1; i >= 0; i--) {
      if (i < sp.word_from && taken.has(i)) break;
      out.push({ word: i, t: i === sp.word_from ? b.t0 : at(tw[i].t), label: tw[i].w, end: i === 0 });
    }
  }
  return out;
}

/** Where a split at cut time `t` goes: in front of the kept word whose boundary is nearest, each piece keeping at
 *  least two words (the engine's rule). */
export function splitAt(blocks: Block[], t: number): { span: string; word: number; label: string; at: number } | { error: string } {
  const b = blocks.find((x) => x.kind === "span" && t >= x.t0 && t < x.t1);
  if (!b) return { error: blocks.some((x) => x.kind === "title" && t < x.t1) ? "The playhead is on the title card: move it into a clip to split." : "Put the playhead inside a clip to split it." };
  const sp = b.span!;
  const f = sp.duration > 0 ? (b.t1 - b.t0) / sp.duration : 1;
  const at = (src: number) => b.t0 + Math.max(0, src - sp.t_in) * f;
  let best: { word: number; at: number } | null = null;
  for (let k = 2; k <= b.kept.length - 2; k++) { // the second piece starts at kept[k]
    const x = (at(b.kept[k - 1].e) + at(b.kept[k].t)) / 2;
    if (!best || Math.abs(x - t) < Math.abs(best.at - t)) best = { word: b.kept[k].i, at: x };
  }
  if (!best) return { error: `Clip ${sp.id} is too short to split: each piece keeps at least two words.` };
  return { span: sp.id, word: best.word, label: b.kept.find((w) => w.i === best!.word)?.w ?? "", at: best.at };
}

/** A word selection in the transcript: anchor and focus, each `${span id}:${turn word index}`. */
export type WordSel = { a: string; b: string };
export type FlatWord = { k: string; span: Span; w: Word; t: number; e: number }; // t, e: cut time

/** The kept words of every clip in cut order, with their cut time. */
export function flatWords(blocks: Block[]): FlatWord[] {
  const out: FlatWord[] = [];
  for (const b of blocks) {
    if (b.kind !== "span" || !b.span) continue;
    const sp = b.span;
    const f = sp.duration > 0 ? (b.t1 - b.t0) / sp.duration : 1;
    const at = (src: number) => Math.min(b.t1, b.t0 + Math.max(0, src - sp.t_in) * f);
    for (const w of b.kept) out.push({ k: `${sp.id}:${w.i}`, span: sp, w, t: at(w.t), e: at(w.e) });
  }
  return out;
}

export function selRange(flat: FlatWord[], sel: WordSel | null): [number, number] | null {
  if (!sel) return null;
  const a = flat.findIndex((x) => x.k === sel.a);
  const b = flat.findIndex((x) => x.k === sel.b);
  return a < 0 || b < 0 ? null : [Math.min(a, b), Math.max(a, b)];
}

export type CutRange = { span: string; from: number; to: number };
/** What deleting the selected words does: the engine op it goes through (a whole clip: drop; a clip's start or end:
 *  trim, reviewer rules; anything else, or several clips: cut, one version), the words, the seconds, a line per clip. */
export type CutAsk = {
  n: number; seconds: number; text: string; ranges: CutRange[]; lines: string[]; error: string | null;
  path: "drop" | "trim" | "cut"; body: Record<string, unknown>;
  clips?: string[]; // a group delete of whole clips (multi-select on the timeline)
};

export function cutAsk(v: Version, flat: FlatWord[], sel: WordSel, words: Record<string, Word[]>): CutAsk | null {
  const r = selRange(flat, sel);
  if (!r) return null;
  const picked = flat.slice(r[0], r[1] + 1);
  const ranges: CutRange[] = [];
  for (const x of picked) {
    const last = ranges.at(-1);
    if (last && last.span === x.span.id) last.to = x.w.i;
    else ranges.push({ span: x.span.id, from: x.w.i, to: x.w.i });
  }
  let seconds = 0;
  let error: string | null = null;
  const lines: string[] = [];
  const kinds: ("whole" | "in" | "out" | "middle")[] = [];
  for (const g of ranges) {
    const sp = v.spans.find((s) => s.id === g.span)!;
    const tw = words[String(sp.turn_id)] ?? [];
    const head = g.from === sp.word_from;
    const tail = g.to === sp.word_to;
    const kind = head && tail ? "whole" : head ? "in" : tail ? "out" : "middle";
    kinds.push(kind);
    const kept = sp.word_to - sp.word_from + 1 - (g.to - g.from + 1);
    seconds += kind === "whole" ? sp.duration : kind === "in" ? tw[g.to + 1].t - sp.t_in
      : kind === "out" ? sp.t_out - tw[g.from - 1].e : tw[g.to + 1].t - tw[g.from - 1].e;
    if ((kind === "in" || kind === "out") && kept < 2) error ??= `Clip ${sp.id} would keep one word: a clip keeps at least two. Select the whole clip to delete it.`;
    if (kind === "middle" && (g.from - sp.word_from < 2 || sp.word_to - g.to < 2)) {
      const lone = g.from - sp.word_from < 2 ? tw[sp.word_from] : tw[sp.word_to];
      error ??= `Clip ${sp.id} would keep “${lone?.w ?? ""}” alone: each piece keeps at least two words. Delete that word too, or keep one more.`;
    }
    lines.push(kind === "whole" ? `${sp.id}: the whole clip is deleted, the cuts on either side tightened to the speech.`
      : kind === "middle" ? `${sp.id}: splits in two around the deleted words, both new cuts tightened to the speech.`
      : `${sp.id}: trimmed at the ${kind === "in" ? "start" : "end"}; like the reviewer's own trims, the edge may settle on a sentence end one or two words away, never back into the deleted words.`);
  }
  if (kinds.every((k) => k === "whole") && ranges.length >= v.spans.length) error ??= "That deletes every clip.";
  const one = ranges.length === 1 ? ranges[0] : null;
  const route: Pick<CutAsk, "path" | "body"> = one && kinds[0] === "whole" ? { path: "drop", body: { span: one.span } }
    : one && kinds[0] === "in" ? { path: "trim", body: { span: one.span, edge: "in", word: one.to + 1 } }
    : one && kinds[0] === "out" ? { path: "trim", body: { span: one.span, edge: "out", word: one.from - 1 } }
    : { path: "cut", body: { words: ranges } };
  const text = picked.map((x) => x.w.w).join(" ");
  if (route.path === "cut") route.body.text = text.slice(0, 120);
  return { n: picked.length, seconds, text, ranges, lines, error, ...route };
}

/** Deleting several whole clips (Shift / ⌘ click on the timeline): one version through the transcript delete's `cut`
 *  path, which drops each whole clip and tightens the cuts that now meet. */
export function clipsAsk(v: Version, ids: string[], words: Record<string, Word[]>): CutAsk {
  const spans = v.spans.filter((s) => ids.includes(s.id));
  const ranges = spans.map((s) => ({ span: s.id, from: s.word_from, to: s.word_to }));
  const text = spans.map((s) => (words[String(s.turn_id)] ?? []).slice(s.word_from, s.word_to + 1).map((w) => w.w).join(" ")).join(" … ");
  const n = spans.reduce((a, s) => a + s.word_to - s.word_from + 1, 0);
  return {
    n, seconds: spans.reduce((a, s) => a + s.duration, 0), text, ranges, clips: spans.map((s) => s.id),
    lines: spans.map((s) => `${s.id}: the whole clip is deleted (${s.duration.toFixed(1)} s).`).concat("The cuts that now meet are tightened to the speech; roles follow the position."),
    error: spans.length >= v.spans.length ? "That deletes every clip." : null,
    path: "cut", body: { words: ranges, text: text.slice(0, 120) },
  };
}

/** The words of the recording this version does not use, as runs per Voice turn in recording order (`t`: the first
 *  word's source second): the transcript shows them grey, to put back with montivo.human insert. */
export function unusedRuns(v: Version, words: Record<string, Word[]>): { turn: number; from: number; to: number; t: number }[] {
  const kept = keptSet(v);
  const out: { turn: number; from: number; to: number; t: number }[] = [];
  for (const [turn, ws] of Object.entries(words)) {
    let run: { turn: number; from: number; to: number; t: number } | null = null;
    for (const w of ws) {
      if (kept.has(key(Number(turn), w.i))) { run = null; continue; }
      if (run && w.i === run.to + 1) run.to = w.i;
      else { run = { turn: Number(turn), from: w.i, to: w.i, t: w.t }; out.push(run); }
    }
  }
  return out.sort((a, b) => a.t - b.t);
}

/** A ghost clip (a span the parent had and this version dropped) as an insert: its longest run of words no clip uses
 *  now (two at least) and the clip it goes back in front of (the next one after the place it had, else the end). */
export function ghostRestore(v: Version, g: Block, blocks: Block[], words: Record<string, Word[]>):
  { from: number; to: number; before: string; text: string; error: string | null } {
  const sp = g.span!;
  const kept = keptSet(v);
  let best: [number, number] | null = null;
  let a: number | null = null;
  for (let i = sp.word_from; i <= sp.word_to + 1; i++) {
    const free = i <= sp.word_to && !kept.has(key(sp.turn_id, i));
    if (free && a == null) a = i;
    if (!free && a != null) { if (!best || i - 1 - a > best[1] - best[0]) best = [a, i - 1]; a = null; }
  }
  const at = blocks.indexOf(g);
  const next = blocks.slice(at + 1).find((b) => b.kind === "span");
  const before = next?.span!.id ?? "end";
  if (!best || best[1] === best[0]) return { from: sp.word_from, to: sp.word_to, before, text: "", error: "its words are in other clips now" };
  const tw = words[String(sp.turn_id)] ?? [];
  return { from: best[0], to: best[1], before, text: tw.slice(best[0], best[1] + 1).map((w) => w.w).join(" "), error: null };
}

/** SP1, SP2: what a Voice speaker is called on version v: merged into another speaker (one person Voice split in two),
 *  that one; then the name a person gave it, else the label. */
export const speakerOf = (v: { speaker_names?: Record<string, string>; speaker_merge?: Record<string, string> } | null, spk: string) =>
  v?.speaker_merge?.[spk] ?? spk;
export const speakerName = (v: { speaker_names?: Record<string, string>; speaker_merge?: Record<string, string> } | null, spk: string) => {
  const s = speakerOf(v, spk);
  return v?.speaker_names?.[s] ?? s;
};
/** SP3: one colour per speaker, the same on the timeline strip and the Speakers panel. */
export const SPEAKER_COLORS = ["#4FA3FF", "#FF8A3D", "#B57BFF", "#5FD08E", "#FF5A8A", "#E8D44D"];
export const speakerColor = (spk: string) =>
  SPEAKER_COLORS[[...spk].reduce((n, c) => n + c.charCodeAt(0), 0) % SPEAKER_COLORS.length]; // A, B, C ... in order
