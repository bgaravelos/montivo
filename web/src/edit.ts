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
  if (o.action === "replan") return "your edit · asked Montivo";
  if (o.action === "caption") return `your edit · ${o.fixes?.length === 1 ? "a caption line" : `${o.fixes?.length ?? ""} caption lines`}`;
  if (o.action === "text") {
    const f = o.fixes ?? [];
    return f.length === 1 && f[0].op.startsWith("title") ? "your edit · the title card" : f.every((x) => x.op === "callout_text")
      ? `your edit · ${f.length === 1 ? "a callout" : `${f.length} callouts`}` : "your edit · title and callouts";
  }
  if (o.action === "reorder") return `your edit · moved clip ${o.fixes?.[0]?.target ?? ""}`;
  if (o.action === "cut") return "your edit · deleted words";
  if (o.action === "pin") return `your edit · pinned speaker ${o.fixes?.[0]?.target ?? ""}`;
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
 *  words inside the clip out to the words no other clip keeps. */
export type Snap = { word: number; t: number; label: string };

export function edgeSnaps(b: Block, edge: "in" | "out", v: Version, words: Record<string, Word[]>): Snap[] {
  const sp = b.span!;
  const tw = words[String(sp.turn_id)] ?? [];
  const taken = new Set<number>();
  for (const s of v.spans) if (s.id !== sp.id && s.turn_id === sp.turn_id) for (let i = s.word_from; i <= s.word_to; i++) taken.add(i);
  const out: Snap[] = [];
  const at = (src: number) => b.t0 + (src - sp.t_in);
  if (edge === "out") {
    for (let i = sp.word_from + 1; i < tw.length; i++) {
      if (i > sp.word_to && taken.has(i)) break;
      if (i > sp.word_to + 25) break;
      out.push({ word: i, t: i === sp.word_to ? b.t1 : at(tw[i].e), label: tw[i].w });
    }
  } else {
    for (let i = sp.word_to - 1; i >= 0; i--) {
      if (i < sp.word_from && taken.has(i)) break;
      if (i < sp.word_from - 25) break;
      out.push({ word: i, t: i === sp.word_from ? b.t0 : at(tw[i].t), label: tw[i].w });
    }
  }
  return out;
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
