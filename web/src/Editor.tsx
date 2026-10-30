import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, dur, fileUrl, tc, usd, useRun } from "./api";
import { Assistant, wordScope, type Selection } from "./Assistant";
import { EditDialog, LedgerDialog, ShortcutsDialog } from "./Dialogs";
import { ExportPage } from "./Export";
import { childOf, clipsAsk, cutAsk, flatWords, layout, parentOf, projectName, splitAt, type CutAsk, type WordSel } from "./edit";
import { Icon, MarkLoader } from "./icons";
import { Inspector, type PunchAsk } from "./Inspector";
import { Palette, type Command } from "./Palette";
import { mixOf, sameMix, useLiveMix, type Mix } from "./Mix";
import { MusicPanel } from "./Music";
import { askToNotify, ProgressInline, useJobSignals } from "./Progress";
import { GUIDE_NAME, GUIDES, SafeArea, type Guide } from "./SafeArea";
import { CaptionsPanel, MediaPanel, SpeakersPanel, TextPanel, VersionsPanel } from "./Panels";
import { FPS, Player, type PlayerHandle } from "./Player";
import { draftCount, EMPTY_DRAFT, StyleCanvas, StylePanel, usePreview, type StyleDraft } from "./Style";
import { Timeline, ZOOM_MAX, ZOOM_MIN, type ClipMenu, type MoveAsk, type TrimAsk } from "./Timeline";
import { Waiting } from "./Waiting";
import type { LiveData } from "./Live";
import { ShareMenu } from "./Share";
import type { LogLine, RunState, Version } from "./types";

type Props = { id: string; replay: number; go: (hash: string) => void };
type PanelId = "assistant" | "media" | "captions" | "style" | "text" | "music" | "speakers" | "versions";
type Dialog = { kind: "delete"; span: string } | { kind: "title" } | { kind: "words"; ask: CutAsk } | { kind: "trim"; ask: TrimAsk } | { kind: "move"; ask: MoveAsk } | { kind: "export" } | { kind: "ledger" } | { kind: "keys" } | { kind: "palette" };

const PANELS: { id: PanelId; label: string; icon: string }[] = [
  { id: "assistant", label: "Assistant", icon: "assistant" },
  { id: "media", label: "Media", icon: "media" },
  { id: "captions", label: "Captions", icon: "captions" },
  { id: "style", label: "Style", icon: "style" },
  { id: "text", label: "Text", icon: "text" },
  { id: "music", label: "Music", icon: "music" },
  { id: "speakers", label: "Speakers", icon: "speakers" },
  { id: "versions", label: "Versions", icon: "versions" },
];

/** Where undo / redo left the editor: the version, and the newest version then (a newer one since makes it stale). */
type Current = { version: number | null; newest: number | null };
const currentKey = (id: string) => `montivo.current.${id}`;
// the requests that make a version from the one on screen: after an undo they branch from an older version
const MAKES_VERSION = ["drop", "trim", "reorder", "cut", "split", "punch", "caption", "glossary", "linebreak", "text", "style", "music", "pin", "ask", "review", "hook",
  "insert", "pauses", "overlay", "kit", "op"];

const typing = (el: EventTarget | null) => {
  const e = el as HTMLElement | null;
  return !!e && (e.tagName === "INPUT" || e.tagName === "TEXTAREA" || e.tagName === "SELECT" || e.isContentEditable);
};
// a focused control Space should press (a button, a link, a summary, a role="button" row), not the player's play/pause;
// the clips, captions and markers on the timeline canvas stay on play/pause (select a clip, Space plays), as in an NLE
const CONTROL = "button, a[href], summary, [role=button], [role=checkbox], [role=switch], [role=tab], [role=menuitem], [role=option], [role=radio]";
const pressable = (el: EventTarget | null): HTMLElement | null => {
  const e = el instanceof Element ? el.closest<HTMLElement>(CONTROL) : null;
  return e && !e.closest(".tl-canvas") ? e : null;
};

/** The editor: top bar, icon rail + one panel, the 9:16 viewer, the inspector and the timeline. The agent loop stays in
 *  view through the Assistant (the pipeline checklist and every version's story), the score chip and the review
 *  markers on the ruler; every edit makes a new version, so undo and redo walk the version tree. */
export function Editor({ id, replay, go }: Props) {
  const { state, error, lines, job, seen, progress } = useRun(id, replay);
  const [sel, setSel] = useState<number | null>(null);
  const [selKey, setSelKey] = useState<string | null>(null); // a span id or "title"
  const [selKeys, setSelKeys] = useState<string[]>([]); // the clips selected together (Shift / ⌘ click, ⌘A)
  const [snap, setSnapState] = useState<boolean>(() => { try { return localStorage.getItem("montivo.timeline.snap") !== "off"; } catch { return true; } });
  const setSnap = (on: boolean) => { setSnapState(on); try { localStorage.setItem("montivo.timeline.snap", on ? "on" : "off"); } catch { /* this tab only */ } };
  const ppsRef = useRef(30);
  const splitFrom = useRef<number | null>(null); // a split asked from this version: select its second piece when it lands
  const [current, setCurrent] = useState<Current | null | undefined>(replay ? null : undefined); // undefined: still loading
  const [wordSel, setWordSel] = useState<WordSel | null>(null); // words selected in the transcript view
  const [capSel, setCapSel] = useState<Selection["caption"]>(null); // a caption line picked in the Captions panel for the chat
  const [t, setT] = useState(0);
  const tRef = useRef(0);
  const [playing, setPlaying] = useState(false);
  const [panel, setPanel] = useState<PanelId>("assistant");
  const [tlMode, setTlMode] = useState<"timeline" | "transcript">("timeline");
  const [zoom, setZoom] = useState<number | null>(null);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [menu, setMenu] = useState(false);
  const [ctx, setCtx] = useState<ClipMenu | null>(null);
  const [askNonce, setAskNonce] = useState(0);
  const [toast, setToast] = useState<Toast | null>(null);
  const [guide, setGuideState] = useState<Guide>(() => {
    try { const g = localStorage.getItem("montivo.viewer.guide") as Guide | null; return g && GUIDES.includes(g) ? g : "off"; } catch { return "off"; }
  });
  const setGuide = (g: Guide) => { setGuideState(g); try { localStorage.setItem("montivo.viewer.guide", g); } catch { /* this tab only */ } };
  const nextGuide = () => setGuide(GUIDES[(GUIDES.indexOf(guide) + 1) % GUIDES.length]);
  const player = useRef<PlayerHandle>(null);
  // the live preview (Live.ts): the viewer plays the EDL from the source, and the structural edits (split, trim, move,
  // delete, words, insert, pauses) go to a draft (server/draft.py) that plays at once; "Make Version N" renders it
  const [liveOn, setLiveOnState] = useState(() => { try { return localStorage.getItem("montivo.live") !== "0"; } catch { return true; } });
  const setLive = (on: boolean) => { setLiveOnState(on); try { localStorage.setItem("montivo.live", on ? "1" : "0"); } catch { /* this tab only */ } };
  const live0 = liveOn && !replay;
  const [draftData, setDraftData] = useState<LiveData | null>(null);
  const [draftSum, setDraftSum] = useState<DraftSum | null>(null);
  const [styleDraft, setStyleDraft] = useState<StyleDraft>(EMPTY_DRAFT);
  // A2: a caption line or callout picked on the timeline: its panel opens on it (nonce: the same one picked again)
  const [focus, setFocus] = useState<PanelFocus | null>(null);

  // in replay, a version exists once the log rendered it; live, once it is on disk
  const visible = useMemo(() => {
    if (!state) return [];
    return state.versions.filter((v) => v.cut && (!replay || seen.cuts.has(v.n)))
      .map((v) => (replay && !seen.reviews.has(v.n) ? { ...v, review: null } : v));
  }, [state, replay, seen]);

  const newest = visible.length ? visible[visible.length - 1].n : null;
  // open on the newest version a human edit made or the hook A/B kept (both come after the loop: runs/012-open opened
  // on the loop's v3 while the hook had kept v4), else the loop's best, else the newest
  const kept = useMemo(() => {
    const ns = visible.flatMap((v) => v.origin.source === "human" ? [v.n]
      : v.origin.source === "hook_ab" && v.origin.result?.kept != null ? [v.origin.result.kept] : []);
    const ok = ns.filter((n) => visible.some((v) => v.n === n));
    return ok.length ? Math.max(...ok) : null;
  }, [visible]);
  const best = state?.loop?.best?.version;
  // a reload opens where undo / redo left the editor, while no version was made since
  const resumed = current?.version != null && current.newest === newest && visible.some((x) => x.n === current.version) ? current.version : null;
  const opening = resumed ?? kept ?? (best && visible.some((v) => v.n === best) ? best : newest);
  useEffect(() => { // ui/current.json, else this browser's own (a hosted sample is shared: the server keeps none there)
    if (replay) return setCurrent(null);
    let local: Current | null = null;
    try { local = JSON.parse(localStorage.getItem(currentKey(id)) ?? "null"); } catch { /* none */ }
    api<Current>(`/api/runs/${id}/current`).then((c) => setCurrent(c?.version != null ? c : local)).catch(() => setCurrent(local));
  }, [id, replay]);
  // when a hook A/B finishes, show the version it kept (B renders first, the verdict lands on it later)
  const last = visible.at(-1);
  const hookKept = last?.origin.source === "hook_ab" ? last.origin.result?.kept ?? null : null;
  const seenHook = useRef<number | null>(null);
  useEffect(() => {
    if (hookKept != null && seenHook.current == null && !replay && visible.some((x) => x.n === hookKept)) setSel(hookKept);
    seenHook.current = hookKept;
  }, [hookKept]); // eslint-disable-line
  const seenNewest = useRef<number | null>(null);
  useEffect(() => { // open on `opening`; follow every version that appears after that (a job, the replay)
    if (!visible.length) return setSel(null);
    if (current === undefined) return; // wait for where undo left the editor
    const grew = seenNewest.current != null && newest != null && newest > seenNewest.current;
    seenNewest.current = newest;
    if (grew || replay) return setSel(newest);
    setSel((s) => (s != null && visible.some((v) => v.n === s) ? s : opening));
  }, [visible.length, newest, opening, replay, current === undefined]); // eslint-disable-line

  const draftV = live0 && draftData?.version_entry ? draftData.version_entry : null;
  const v = draftV ?? visible.find((x) => x.n === sel) ?? null;
  const parent = v && state ? parentOf(v, state.versions) : undefined;
  const blocks = useMemo(() => (v && state ? layout(v, parent, state.words) : []), [v, parent, state]);
  const block = blocks.find((b) => b.key === selKey && b.kind !== "ghost") ?? null;
  const punch = v && selKey?.startsWith("punch:") ? v.punch_ins.find((x) => `punch:${x.key}` === selKey) ?? null : null;
  useEffect(() => { // a selection the new version no longer has goes; a split just made selects its second piece
    if (!v) return;
    const fx = v.origin.action === "split" && splitFrom.current === v.origin.from ? v.origin.fixes?.[0] : undefined;
    const ids = (fx?.args as { ids?: string[] } | undefined)?.ids;
    if (ids?.length === 2) {
      splitFrom.current = null;
      setSelKey(ids[1]); setSelKeys([ids[1]]);
      const b = blocks.find((x) => x.key === ids[1]);
      if (b) window.setTimeout(() => player.current?.seek(b.t0 + 0.01), 50);
      flash(`Split: ${ids[0]} | ${ids[1]}. The cut plays the same; select a piece to move or delete it.`);
      return;
    }
    if (selKey && !blocks.some((b) => b.key === selKey && b.kind !== "ghost") && !itemIn(v, selKey)) setSelKey(null);
    setSelKeys((ks) => ks.filter((k) => v.spans.some((sp) => sp.id === k)));
  }, [v?.n]); // eslint-disable-line
  useEffect(() => setWordSel(null), [v?.n, tlMode]);
  useEffect(() => setCapSel(null), [v?.n]);
  // what the chat's "this" means: the selected clips, the transcript words, the caption line (UX B3)
  const selection = useMemo<Selection>(() => ({
    clips: selKeys.length ? selKeys : block?.kind === "span" ? [block.span!.id] : [],
    words: wordScope(flatWords(blocks), wordSel), caption: capSel,
  }), [selKeys, block, blocks, wordSel, capSel]);
  // what the panels' staged edits belong to: the version, or the draft as it stands (an edit into it clears them)
  const rev = draftV ? `d${draftData?.draft?.id}-${draftSum?.ops.length ?? 0}` : `v${v?.n}`;
  useEffect(() => setStyleDraft(EMPTY_DRAFT), [rev]);
  // the timeline's faders: staged against the version's own mix and heard live through its stems (Mix.ts)
  const mixBase = mixOf(v);
  const [mixDraft, setMixDraft] = useState<Mix | null>(null);
  useEffect(() => setMixDraft(null), [rev]);
  const mix = mixDraft ?? mixBase;
  const mixStaged = !!mixDraft && !sameMix(mixDraft, mixBase);
  const getVideo = useCallback(() => player.current?.el() ?? null, []);
  const live = useLiveMix(id, v, getVideo, mix, mixStaged);
  const styling = panel === "style" && !!(v?.cut || v?.draft) && !replay;
  const staged = draftCount(styleDraft, v);
  const { p: stylePreview, busy: previewBusy } = usePreview(id, v, t, styleDraft, styling && staged > 0 && !playing);
  // A4: in Live the staged look plays at once (the same cut laid out with it by the server); paused, the still above
  // stays what the render burns
  const [styled, setStyled] = useState<LiveData | null>(null);
  const styleKey = live0 && styling && staged > 0 && v ? JSON.stringify([rev, styleDraft.look, styleDraft.callouts, styleDraft.clips]) : null;
  useEffect(() => {
    if (!styleKey || !v) return setStyled(null);
    let on = true;
    const tm = window.setTimeout(() => {
      api<LiveData>(`/api/runs/${id}/preview/styled`, { ...(v.draft ? { draft: true } : { v: v.n }), look: styleDraft.look, callouts: styleDraft.callouts, clips: styleDraft.clips })
        .then((d) => { if (on) setStyled(d); }).catch(() => { if (on) setStyled(null); });
    }, 250);
    return () => { on = false; window.clearTimeout(tm); };
  }, [styleKey]); // eslint-disable-line

  const undoTo = v && state ? visible.find((x) => x.n === parent?.n) : undefined;
  const redoTo = v ? childOf(v, visible) : undefined;
  const busy = !!job?.running;
  useJobSignals(state ? projectName(v, state.run.brief) : id, replay ? null : job, progress);
  const canEdit = replay ? "Replay is on" : busy ? `${job!.label} is running` : null;

  const flash = useCallback((text: string, tone?: string, action?: Toast["action"]) => {
    setToast({ text, tone, action }); window.setTimeout(() => setToast(null), action ? 6000 : 4500);
  }, []);
  const forkFrom = v && newest != null && v.n < newest && !replay ? v.n : null;
  const act = useCallback(async (path: string, body: unknown) => {
    if (["ask", "review", "hook"].includes(path)) askToNotify(); // the jobs that take a minute or more
    if (forkFrom != null && MAKES_VERSION.includes(path) && state)
      flash(`Version ${nextN(state)} branches from Version ${forkFrom}, the one you stepped back to; Versions ${forkFrom + 1}–${newest} stay in Versions.`);
    try {
      const r = await api<{ job?: { run?: string } }>(`/api/runs/${id}/${path}`, body);
      if (r.job?.run && r.job.run !== id) go(`#/run/${r.job.run}`); // the hosted demo edits the judge's own copy of a sample
      return true;
    } catch (e) { flash(String((e as Error).message), "error"); return false; }
  }, [id, flash, go, forkFrom, newest, state]);
  /** The open draft: GET /draft settles a commit that has ended (its version exists now: no draft), then its preview. */
  const loadDraft = useCallback(async (settle = true) => {
    try {
      if (settle) {
        const sum = await api<{ draft: DraftSum | null }>(`/api/runs/${id}/draft`);
        setDraftSum(sum.draft);
        if (!sum.draft) return setDraftData(null);
      }
      setDraftData(await api<LiveData>(`/api/runs/${id}/draft/preview`));
    } catch (e) { setDraftData(null); flash(String((e as Error).message), "error"); }
  }, [id, flash]);
  useEffect(() => { if (live0) loadDraft(); else { setDraftData(null); setDraftSum(null); } }, [live0, newest, loadDraft]);
  /** One structural edit into the draft (started from the version on screen), in milliseconds and $0. */
  const draftOp = useCallback(async (op: string, args: Record<string, unknown>) => {
    try {
      madeDraft.current = true;
      const r = await api<{ draft: DraftSum | null }>(`/api/runs/${id}/draft`, { op, args, ...(draftSum ? {} : { from: v?.n }) });
      setDraftSum(r.draft);
      await loadDraft(false);
      return true;
    } catch (e) { flash(String((e as Error).message), "error"); return false; }
  }, [id, draftSum, v?.n, loadDraft, flash]);
  /** The editor's edit routes: in live mode the structural ones go to the draft; a draft blocks the rest until it is a
   *  version (they edit a version, and the draft is not one yet). */
  const edit = useCallback(async (path: string, body: Record<string, unknown>) => {
    const to = live0 ? DRAFT_OPS[path] : undefined;
    if (to) return draftOp(to.op, to.args(body));
    if (draftV && MAKES_VERSION.includes(path))
      return (flash(`Make Version ${draftData?.draft?.n ?? ""} first: the draft's ${edits(draftSum?.ops.length ?? 0)} are not a version yet.`), false);
    const gen = OP_ROUTE[path]; // a newer panel edit with no route of its own: POST /op runs its pure op as a version
    if (gen) return act("op", { op: gen.op, args: gen.args(body), from: body.from, review: body.review });
    return act(path, body);
  }, [live0, draftOp, draftV, draftData, draftSum, flash, act]);
  const commitDraft = async () => {
    try {
      await api(`/api/runs/${id}/draft/commit`, { review: 0 });
      flash(`Making Version ${draftData?.draft?.n ?? ""}: one render of the draft's ${edits(draftSum?.ops.length ?? 0)} ($0).`);
    } catch (e) { flash(String((e as Error).message), "error"); }
  };
  /** A look from words: in Live, Muse Spark suggests it (one call, no version) and it goes into the draft (A3). */
  const stylePrompt = async (text: string) => {
    if (!live0) return edit("style", { prompt: text, from: v?.n });
    try {
      flash("Muse Spark is picking the look…");
      const r = await api<{ look: Record<string, unknown>; why: string; prompt: string }>(`/api/runs/${id}/style/suggest`,
        draftSum ? { prompt: text, draft: true } : { prompt: text, v: v?.n });
      return draftOp("style", { look: r.look, prompt: r.prompt, why: r.why });
    } catch (e) { flash(String((e as Error).message), "error"); return false; }
  };
  const discardDraft = async () => {
    try { await api(`/api/runs/${id}/draft`, undefined, "DELETE"); setDraftSum(null); setDraftData(null); }
    catch (e) { flash(String((e as Error).message), "error"); }
  };
  /** A1: an edit started from a panel leaves the panel open (a second change needs no search for it); the toast says
   *  what is happening and opens the chat on a click. A draft edit (Live) says nothing: the viewer already plays it. */
  const madeDraft = useRef(false);
  const made = async (p: Promise<boolean>) => {
    madeDraft.current = false;
    const ok = await p;
    if (!ok || madeDraft.current || panel === "assistant" || !state) return;
    flash(`Making Version ${nextN(state)}…`, undefined, { label: "Open chat", run: () => setPanel("assistant") });
  };
  const shareLink = async (n: number) => {
    try {
      const r = await api<{ path: string }>(`/api/runs/${id}/share`, { version: n });
      const url = `${window.location.origin}${r.path}`;
      let copied = false;
      try { await navigator.clipboard.writeText(url); copied = true; } catch { /* no clipboard permission */ }
      flash(`${copied ? "Link copied" : "Share link"}: ${url}`);
    } catch (e) { flash(String((e as Error).message), "error"); }
  };
  const seek = useCallback((a: number, b?: number) => player.current?.seek(a, b), []);
  const scrub = useCallback((x: number) => { player.current?.pause(); player.current?.seek(x); }, []);
  const onTime = useCallback((x: number) => { tRef.current = x; setT(x); }, []);
  const getTime = useCallback(() => tRef.current, []);
  // the playhead for a key: the video's own clock (onTime follows it on animation frames, which stall in a hidden tab)
  const playhead = () => player.current?.time() ?? tRef.current;
  const blocksRef = useRef(blocks);
  blocksRef.current = blocks;
  const selRef = useRef({ key: selKey, keys: selKeys });
  selRef.current = { key: selKey, keys: selKeys };
  /** A click selects one clip; ⌘ click adds or removes one, ⇧ click selects the run from the last clicked one. */
  const select = useCallback((k: string | null, how?: "toggle" | "range") => {
    setCtx(null); setWordSel(null);
    const spans = blocksRef.current.filter((b) => b.kind === "span").map((b) => b.key);
    if (k == null) { setSelKey(null); setSelKeys([]); return; }
    if (!how || !spans.includes(k)) { setSelKey(k); setSelKeys(spans.includes(k) ? [k] : []); return; }
    const cur = selRef.current;
    const have = cur.keys.length ? cur.keys : cur.key && spans.includes(cur.key) ? [cur.key] : [];
    if (how === "toggle") {
      const next = have.includes(k) ? have.filter((x) => x !== k) : [...have, k];
      setSelKeys(next); setSelKey(next.includes(k) ? k : next.at(-1) ?? null);
      return;
    }
    const anchor = cur.key && spans.includes(cur.key) ? cur.key : k;
    const [a, z] = [spans.indexOf(anchor), spans.indexOf(k)].sort((x, y) => x - y);
    setSelKeys(spans.slice(a, z + 1)); setSelKey(anchor);
  }, []);
  const pickWords = useCallback((w: WordSel | null) => { setWordSel(w); if (w) { setSelKey(null); setSelKeys([]); setCtx(null); } }, []);
  const askCut = () => { // the transcript selection -> the confirm: drop, trim or cut, whichever the words need
    if (!v || !state || !wordSel) return;
    if (canEdit) return flash(`Cannot delete now: ${canEdit.toLowerCase()}.`);
    const a = cutAsk(v, flatWords(blocks), wordSel, state.words);
    if (a && live0) { // live: no confirm, the draft takes it (⌘Z undoes it)
      if (a.error) return flash(a.error, "error");
      setWordSel(null);
      return void edit(a.path, a.body);
    }
    if (a) setDialog({ kind: "words", ask: a });
  };
  const ask = useCallback((span: string) => { setSelKey(span); setPanel("assistant"); setAskNonce((n) => n + 1); setCtx(null); }, []);
  const askDelete = useCallback((span: string) => {
    setCtx(null);
    if (live0) return void edit("drop", { span });
    setDialog({ kind: "delete", span });
  }, [live0, edit]);
  const askDeleteClips = (ids: string[]) => { // several whole clips: one version through the transcript delete's cut
    if (!v || !state) return;
    if (canEdit) return flash(`Cannot delete now: ${canEdit.toLowerCase()}.`);
    const order = v.spans.map((s) => s.id).filter((x) => ids.includes(x));
    if (order.length === 1) return askDelete(order[0]);
    setCtx(null);
    const a = clipsAsk(v, order, state.words);
    if (live0) { if (a.error) return flash(a.error, "error"); setSelKeys([]); return void edit(a.path, a.body); }
    setDialog({ kind: "words", ask: a });
  };
  const doSplit = () => { // S / ⌘K: the clip under the playhead, in front of the nearest word boundary
    if (!v) return;
    if (canEdit) return flash(`Cannot split now: ${canEdit.toLowerCase()}.`);
    const r = splitAt(blocks, playhead());
    if ("error" in r) return flash(r.error);
    if (live0) return void edit("split", { span: r.span, word: r.word });
    splitFrom.current = v.n;
    flash(`Splitting clip ${r.span} in front of “${r.label}” (Version ${nextN(state!)}, $0, a re-render)…`);
    act("split", { span: r.span, word: r.word, from: v.n, text: r.label });
  };
  const selectStep = (dir: -1 | 1) => { // [ and ]: the clip left / right of the selection, else of the playhead
    const spans = blocks.filter((b) => b.kind === "span");
    if (!spans.length) return;
    const at = spans.findIndex((b) => b.key === selKey);
    const now = playhead();
    const here = at >= 0 ? at : spans.findIndex((b) => now >= b.t0 && now < b.t1);
    const to = here < 0 ? (dir > 0 ? 0 : spans.length - 1) : Math.max(0, Math.min(spans.length - 1, here + (at >= 0 ? dir : 0)));
    select(spans[to].key);
    player.current?.pause();
    seek(spans[to].t0 + 0.01);
  };
  const askTrim = useCallback((a: TrimAsk) => {
    if (live0) return void edit("trim", { span: a.span, edge: a.edge, word: a.word });
    setDialog({ kind: "trim", ask: a });
  }, [live0, edit]);
  const askMove = useCallback((a: MoveAsk) => {
    if (live0) return void edit("reorder", { span: a.span, before: a.before });
    setDialog({ kind: "move", ask: a });
  }, [live0, edit]);
  /** Keep where the editor is for a reload (ui/current.json): the newest version needs no record. */
  const remember = useCallback((n: number) => {
    if (replay || newest == null) return;
    const c: Current = n === newest ? { version: null, newest: null } : { version: n, newest };
    setCurrent(c);
    try { localStorage.setItem(currentKey(id), JSON.stringify(c)); } catch { /* this tab only */ }
    api(`/api/runs/${id}/current`, c).catch(() => { /* the browser's copy still holds */ });
  }, [id, replay, newest]);
  const pick = useCallback((n: number) => { setSel(n); remember(n); }, [remember]);
  /** A2: a caption line (`cap:<id>`) or callout (`callout:<span>`) picked on the timeline: selected, and its panel
   *  opens on it (Captions, Text). `edit` opens the line's text box there. */
  const pickItem = (key: string, edit = false) => {
    select(key);
    setPanel(key.startsWith("cap:") ? "captions" : "text");
    setFocus({ key, edit, nonce: Date.now() });
  };
  const onPunch = (a: PunchAsk) => { setSelKey(null); made(edit("punch", { ...a, from: v?.n })); };
  const step = (dir: -1 | 1) => {
    if (draftSum && (dir < 0 ? draftSum.can_undo : draftSum.can_redo)) return void draftStep(dir);
    const to = dir < 0 ? undoTo : redoTo;
    if (!to) return flash(dir < 0 ? "Nothing to undo: this is the first version." : "Nothing to redo.");
    setSel(to.n);
    remember(to.n);
    flash(`${dir < 0 ? "Undo" : "Redo"}: Version ${to.n}${to.n !== newest ? ` of ${newest}. A reload opens here; an edit now branches from Version ${to.n}` : ""}`);
  };

  const draftStep = async (dir: -1 | 1) => { // the draft's own undo / redo; undoing its last edit closes it
    try {
      const r = await api<{ draft: DraftSum | null }>(`/api/runs/${id}/draft/${dir < 0 ? "undo" : "redo"}`, {});
      if (r.draft && !r.draft.ops.length && !r.draft.can_redo) return void discardDraft();
      setDraftSum(r.draft);
      await loadDraft(false);
    } catch (e) { flash(String((e as Error).message), "error"); }
  };

  // keyboard, through a ref so the listener sees the current state without re-subscribing
  const keys = useRef<(e: KeyboardEvent) => void>(() => {});
  keys.current = (e: KeyboardEvent) => {
    if (typing(e.target) || dialog) return;
    const p = player.current;
    const mod = e.metaKey || e.ctrlKey;
    if (mod && (e.key === "z" || e.key === "Z")) { e.preventDefault(); step(e.shiftKey ? 1 : -1); return; }
    if (mod && (e.key === "=" || e.key === "+")) { e.preventDefault(); setZoom((z) => Math.min(ZOOM_MAX, (z ?? ppsRef.current) * 1.4)); return; }
    if (mod && e.key === "-") { e.preventDefault(); setZoom((z) => Math.max(ZOOM_MIN, (z ?? ppsRef.current) / 1.4)); return; }
    if (mod && (e.key === "k" || e.key === "K")) { e.preventDefault(); doSplit(); return; }
    if (mod && (e.key === "p" || e.key === "P")) { e.preventDefault(); setDialog({ kind: "palette" }); return; } // ⌘K is split
    if (mod && (e.key === "a" || e.key === "A") && tlMode === "timeline" && v) {
      e.preventDefault();
      const all = blocks.filter((b) => b.kind === "span").map((b) => b.key);
      setSelKeys(all); setSelKey(all[0] ?? null); setWordSel(null);
      flash(`${all.length} clips selected`);
      return;
    }
    if (mod) return;
    switch (e.key) {
      case " ": {
        const c = pressable(e.target);
        if (c) { // native buttons, links and summaries press on their own; role="button" rows only listen for Enter
          if (!c.matches("button, a[href], summary")) { e.preventDefault(); c.click(); }
          break;
        }
        e.preventDefault(); p?.toggle(); break;
      }
      case "j": case "J": p?.shuttle(-1); break;
      case "k": case "K": p?.shuttle(0); break;
      case "l": case "L": p?.shuttle(1); break;
      case "ArrowLeft": e.preventDefault(); p?.step(e.shiftKey ? -1 : -1 / FPS); break;
      case "ArrowRight": e.preventDefault(); p?.step(e.shiftKey ? 1 : 1 / FPS); break;
      case "ArrowUp": e.preventDefault(); p?.skip(-1); break;
      case "ArrowDown": e.preventDefault(); p?.skip(1); break;
      case "s": case "S": e.preventDefault(); doSplit(); break;
      case "[": selectStep(-1); break;
      case "]": selectStep(1); break;
      case "Delete": case "Backspace":
        if (wordSel) {
          e.preventDefault();
          askCut();
        } else if (selKeys.length > 1) {
          e.preventDefault();
          askDeleteClips(selKeys);
        } else if (block?.kind === "title") {
          e.preventDefault();
          if (canEdit) flash(`Cannot change it now: ${canEdit.toLowerCase()}.`);
          else setDialog({ kind: "title" });
        } else if (selKey?.startsWith("callout:") && v) { // A2: a selected callout comes off its clip
          e.preventDefault();
          if (canEdit) flash(`Cannot change it now: ${canEdit.toLowerCase()}.`);
          else { made(edit("text", { callouts: { [selKey.slice(8)]: "" }, from: v.n })); setSelKey(null); }
        } else if (selKey?.startsWith("cap:")) {
          e.preventDefault();
          flash("A caption line follows the speech: press Enter to retext it, or delete the words it says.");
        } else if (punch) {
          e.preventDefault();
          if (canEdit) flash(`Cannot remove it now: ${canEdit.toLowerCase()}.`);
          else onPunch({ skip: punch.key });
        } else if (block?.kind === "span") {
          e.preventDefault();
          if (canEdit) flash(`Cannot delete now: ${canEdit.toLowerCase()}.`);
          else if (v && v.spans.length < 2) flash("That is the only clip.");
          else askDelete(block.span!.id);
        }
        break;
      case "Enter":
        if (selKey?.startsWith("cap:") || selKey?.startsWith("callout:")) { e.preventDefault(); pickItem(selKey, true); }
        break;
      case "Escape": if (ctx) setCtx(null); else if (menu) setMenu(false); else if (wordSel) setWordSel(null); else { setSelKey(null); setSelKeys([]); } break;
      case "g": case "G": nextGuide(); break;
      case "v": case "V": if (!replay) setLive(!liveOn); break;
      case "m": case "M": if (e.shiftKey) p?.toggleMute(); break; // M alone stays free for markers (Premiere, Resolve)
      case "f": case "F": p?.fullscreen(); break;
      case "n": case "N": setSnap(!snap); flash(`Snapping ${snap ? "off" : "on"}${snap ? "" : ": the playhead, clip edges and review issues"} (hold ⇧ while dragging for free)`); break;
      case ".": case ",": { // the next (,: previous) reviewer issue of this version
        const back = e.key === ",";
        const iss = [...(v?.review?.issues ?? [])].sort((a, b) => a.t_s - b.t_s);
        if (!iss.length) { flash(v?.review ? "The reviewer raised no issue on this version." : "This version has not been reviewed."); break; }
        const now = playhead();
        const to = back ? [...iss].reverse().find((i) => i.t_s < now - 0.25) : iss.find((i) => i.t_s > now + 0.05);
        if (!to) { flash(back ? "No issue before the playhead." : "No issue after the playhead."); break; }
        p?.pause(); p?.seek(to.t_s); setSelKey(null);
        flash(`Issue ${iss.indexOf(to) + 1} of ${iss.length} at ${tc(to.t_s)} · ${to.severity}: ${to.what}`);
        break;
      }
      case "?": setDialog({ kind: "keys" }); break;
      case "/": e.preventDefault(); setDialog({ kind: "palette" }); break;
      case "1": case "2": case "3": case "4": case "5": case "6": case "7": case "8": // A5: the rail's panels in order
        setPanel(PANELS[Number(e.key) - 1].id); break;
    }
  };
  useEffect(() => {
    const on = (e: KeyboardEvent) => keys.current(e);
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);
  useEffect(() => { // close the context menu and the top menu on any click elsewhere
    if (!ctx && !menu) return;
    const off = () => { setCtx(null); setMenu(false); };
    window.addEventListener("pointerdown", off);
    return () => window.removeEventListener("pointerdown", off);
  }, [ctx, menu]);

  if (error) return <div className="empty">Could not open project {id}: {error} <a href="#/">All projects</a></div>;
  if (!state) return <div className="empty">Opening project {id}…</div>;

  const reviewed = visible.filter((x) => x.review);
  const top = reviewed.length ? reviewed.reduce((a, b) => (b.review!.overall > a.review!.overall ? b : a)) : null;
  const spent = replay ? replaySpent(lines) : null;
  const next = nextN(state);
  const base = `#/run/${state.id}`;
  const name = projectName(v, state.run.brief);
  const scoreTip = top ? `Best blind review: Version ${top.n}, ${top.review!.overall}/100${top.review!.pass ? ", passes" : ""}. The same cut reviewed twice can differ by about 10 points.` : "Not reviewed yet";

  return (
    <div className="editor">
      <header className="top">
        <button className="icon-btn" onClick={() => go("#/")} title="All projects"><Icon name="back" /></button>
        <a className="mark" href="#/" title="All projects"><img src="/montivo-lockup-dark.svg" alt="Montivo" /></a>
        <span className="divider" />
        <div className="title-block">
          <span className="proj" title={state.run.brief}>{name}</span>
          <span className="saved">{progress?.active && (busy || replay) ? <ProgressInline p={progress} />
            : busy ? <><MarkLoader /> {job!.label}…</> : draftV ? <>{edits(draftSum?.ops.length ?? 0)} on Version {draftSum?.base} · not a version yet</>
            : v ? <>Saved · Version {v.n} of {newest}</> : "No version yet"}</span>
        </div>
        {draftV && !busy && (
          <span className="draft-bar">
            <button className="btn quiet" onClick={discardDraft} title="Drop the draft's edits: the viewer goes back to the version they started from">Discard</button>
            <button className="btn primary" onClick={commitDraft} disabled={!draftSum?.ops.length}
              title="One render of every edit in the draft ($0); the version list gains one version, not one per edit">
              Make Version {draftData?.draft?.n}
            </button>
          </span>
        )}
        <span className="undo-redo">
          <button className="icon-btn" disabled={!undoTo} onClick={() => step(-1)} title={undoTo ? `Undo: back to Version ${undoTo.n} (⌘Z)` : "Nothing to undo"}><Icon name="undo" /></button>
          <button className="icon-btn" disabled={!redoTo} onClick={() => step(1)} title={redoTo ? `Redo: Version ${redoTo.n} (⇧⌘Z)` : "Nothing to redo"}><Icon name="redo" /></button>
        </span>
        <span className="grow" />
        <span className="chip-aspect" title="Vertical 1080 × 1920">9:16</span>
        <button className={`score-chip ${top?.review!.pass ? "pass" : ""} ${!top ? "none" : ""}`} title={scoreTip}
          onClick={() => { if (top) { setSel(top.n); setSelKey(null); } }}>
          {top ? <>{top.review!.pass && <Icon name="check" size={13} />}<b>{top.review!.overall}</b><span>review</span></> : <span>not reviewed</span>}
        </button>
        <button className="btn quiet loop-btn" onClick={() => go(`${base}/loop${replay ? `?replay=${replay}` : ""}`)}
          title="Every version side by side: the blind scores, the reviewer's notes and the fixes between them">
          <Icon name="loop" /> Loop
        </button>
        <div className="menu-wrap" onPointerDown={(e) => e.stopPropagation()}>
          <button className={`icon-btn ${menu ? "on" : ""}`} onClick={() => setMenu(!menu)} title="More"><Icon name="more" /></button>
          {menu && (
            <div className="menu">
              <div className="menu-label">Replay the agent run</div>
              <div className="menu-segs">
                {[0, 1, 4].map((s) => (
                  <button key={s} className={`seg ${replay === s ? "on" : ""}`} onClick={() => { setMenu(false); go(s ? `${base}?replay=${s}` : base); }}>{s ? `${s}×` : "Off"}</button>
                ))}
              </div>
              <div className="menu-sep" />
              <button className="menu-item" onClick={() => { setMenu(false); setDialog({ kind: "ledger" }); }}>
                <span>Cost and time</span>
                <span className="mono mute">{spent ? `${usd(spent.usd)} · ${dur(spent.s)}` : `${usd(state.ledger.total_cost_usd)} · ${dur(state.ledger.wall_seconds)}`}</span>
              </button>
              <button className="menu-item" onClick={() => { setMenu(false); setDialog({ kind: "keys" }); }}><span>Keyboard shortcuts</span><kbd>?</kbd></button>
              <div className="menu-sep" />
              <div className="menu-models">Muse Spark 1.3 · Muse Voice · SAM 3.1 · Muse Image</div>
            </div>
          )}
        </div>
        <ShareMenu id={id} version={v?.cut ? v.n : null} disabled={!!replay} onShare={shareLink} flash={flash} />
        <button className="btn export" disabled={!v?.cut} onClick={() => setDialog({ kind: "export" })} aria-label="Export"><Icon name="export" /> <span className="btn-label">Export</span></button>
      </header>

      <div className="mobile-note">Review mode: watch the cut, read the review, ask Montivo, export. The timeline and the editing panels open on a desktop.</div>

      <nav className="rail">
        {PANELS.map((x, i) => (
          <button key={x.id} className={`rail-btn ${panel === x.id ? "on" : ""}`} onClick={() => setPanel(x.id)} title={`${x.label} (${i + 1})`}>
            <Icon name={x.icon} size={19} />
            <span>{x.label}</span>
            {x.id === "assistant" && busy && <span className="rail-dot" />}
          </button>
        ))}
      </nav>

      <aside className="panel">
        {panel === "assistant" && (
          <Assistant state={state} versions={visible} lines={lines} job={job} seen={seen} progress={progress} replay={replay} v={v}
            selection={selection} getTime={getTime} focusNonce={askNonce}
            onSend={(text, chips, review, planner, scope, latest) => edit("ask", { message: text, chips, scope, from: latest ? undefined : v?.n, review, planner, preview: true })}
            onApply={(pending, review) => act("ask/apply", { pending, review })} onRetry={(pending) => act("ask/retry", { pending })}
            onRetryJob={() => act("retry", {})}
            onStop={() => act("stop", {})} onPick={pick} onSeek={(x) => { player.current?.pause(); seek(x); }}
            onCompare={(a, b) => go(`${base}/compare?a=${a}&b=${b}${replay ? `&replay=${replay}` : ""}`)}
            onRestore={canEdit ? null : (n) => edit("restore", { from: n })} />
        )}
        {panel === "media" && <MediaPanel state={state} />}
        {panel === "captions" && (
          <CaptionsPanel v={v} t={t} next={next} canEdit={canEdit} onSeek={seek} focus={focus} rev={rev} live={live0}
            onAsk={(c) => { setCapSel({ t_in: c.t_in, t_out: c.t_out, text: c.text }); setPanel("assistant"); setAskNonce((n) => n + 1); }}
            onApply={(edits) => made(edit("caption", { edits, from: v?.n }))}
            onStyle={() => setPanel("style")}
            onGlossary={(add, drop) => made(edit("glossary", { add, drop: drop ?? [], from: v?.n }))}
            onBreak={(at, join) => made(edit("linebreak", { at, join, from: v?.n }))} />
        )}
        {panel === "style" && (
          <StylePanel id={id} v={v} t={t} next={next} canEdit={canEdit} live={live0} draft={styleDraft} setDraft={setStyleDraft} onSeek={(a) => { player.current?.pause(); seek(a); }}
            hasKey={state.has_key} keyNote={state.key_note ?? null} cost={state.costs?.look ?? 0.004}
            onPrompt={(text) => made(stylePrompt(text))}
            onKit={(k) => made(edit("kit", { kit: { look: k.look, ...(k.music ? { music: k.music } : {}), ...(k.title_card != null ? { title_card: k.title_card } : {}) }, name: k.name, from: v?.n }))}
            clip={block?.kind === "span" ? block.span!.id : blocks.find((b) => b.kind === "span" && t >= b.t0 && t < b.t1)?.span?.id ?? null}
            onApply={() => { const d = styleDraft; made(edit("style", { look: d.look, callouts: d.callouts, clips: d.clips, from: v?.n })); }} />
        )}
        {panel === "text" && (
          <TextPanel state={state} v={v} t={t} next={next} canEdit={canEdit} onSeek={seek} focus={focus} rev={rev} live={live0}
            onApply={(e) => made(edit("text", { ...e, from: v?.n }))} />
        )}
        {panel === "music" && (
          <MusicPanel v={v} next={next} canEdit={canEdit} rev={rev} live={live0}
            onApply={(e) => made(edit("music", { ...e, from: v?.n }))} />
        )}
        {panel === "speakers" && (
          <SpeakersPanel id={id} v={v} next={next} canEdit={canEdit} rev={rev} live={live0}
            onApply={(speaker, head) => made(edit("pin", { speaker, head, from: v?.n }))} />
        )}
        {panel === "versions" && <VersionsPanel id={id} state={state} versions={visible} sel={sel} onPick={pick} go={go} replay={replay} opening={opening}
          canEdit={canEdit} onRestore={(n) => made(edit("restore", { from: n }))} />}
        {panel !== "assistant" && busy && !replay && ( // A1: the job a panel started, in view while the panel stays open
          <div className="panel-job">
            {progress?.active ? <ProgressInline p={progress} /> : <span><MarkLoader /> {job!.label}…</span>}
            <button className="link small" onClick={() => setPanel("assistant")}>Open chat</button>
          </div>
        )}
      </aside>

      <main className="viewer">
        {!replay && v && (
          <div className="view-mode" role="group" aria-label="What the viewer plays">
            <button className={liveOn ? "on" : ""} aria-pressed={liveOn} onClick={() => setLive(true)}
              title="Live: every cut, move or delete plays at once, straight from the footage; Make Version renders them into a file (V)">
              <span className="vm-dot" />Live
            </button>
            <button className={!liveOn ? "on" : ""} aria-pressed={!liveOn} onClick={() => setLive(false)}
              title="Rendered: the exported file of this version, exactly as it downloads (V)">
              Rendered
            </button>
          </div>
        )}
        {v?.cut && guide !== "off" && (
          <button className="guide-btn on" onClick={nextGuide}
            title="Safe-area guides: the margins Reels, TikTok and Shorts cover with their own UI (G cycles)">
            Safe area: {GUIDE_NAME[guide]}
          </button>
        )}
        {v && (v.cut || live0) ? (
          <Player ref={player} key={draftV ? `draft-${draftData!.draft!.id}` : `${v.n}${live0 ? "-live" : ""}`}
            src={v.cut ? fileUrl(id, v.cut) : ""} onTime={onTime} onPlaying={setPlaying}
            live={draftV ? draftData! : live0 ? `/api/runs/${id}/preview?v=${v.n}` : undefined}
            liveMix={live0 && mixStaged ? mix : null} liveSwap={styleKey ? styled : null}
            marks={blocks.filter((b) => b.kind !== "ghost").map((b) => b.t0)}
            label={draftV ? `Draft · ${edits(draftSum?.ops.length ?? 0)}` : `v${v.n}${live0 ? " · live" : ""}`} labelTone={draftV ? "draft" : undefined}
            safe={{ label: GUIDE_NAME[guide], on: guide !== "off", next: nextGuide }} overlay={<>
              {v && <StyleCanvas v={v} t={t} draft={styleDraft} setDraft={setStyleDraft} preview={stylePreview} busy={previewBusy}
                show={styling && !playing} />}
              <SafeArea kind={guide} />
            </>} />
        ) : (
          busy || replay ? <Waiting progress={progress} live={!replay} />
            : <div className="player-wait">No cut rendered yet.</div>
        )}
      </main>

      <aside className="insp">
        <Inspector id={id} state={state} versions={visible} v={v} block={block} punch={punch} next={next} words={state.words} busy={busy}
          item={selKey && (selKey.startsWith("cap:") || selKey.startsWith("callout:") || selKey === "music") ? selKey : null} edits={{
            caption: (c, text) => made(edit("caption", { edits: [{ at: (c.t_in + c.t_out) / 2, text }], from: v?.n })),
            callout: (sp, text) => { if (!text) setSelKey(null); made(edit("text", { callouts: { [sp]: text }, from: v?.n })); },
            calloutAuto: (sp) => made(edit("style", { callouts: { [sp]: null }, from: v?.n })),
            look: (patch) => { if (v?.look) made(edit("style", { look: { ...v.look, ...patch }, from: v.n })); },
            music: (m) => made(edit("music", { ...m, from: v?.n })),
            panel: (x) => setPanel(x),
            ask: (c) => { setCapSel({ t_in: c.t_in, t_out: c.t_out, text: c.text }); setPanel("assistant"); setAskNonce((n) => n + 1); },
          }}
          canEdit={canEdit} onSeek={seek} onPick={pick} onDelete={askDelete} onAsk={ask} onPunch={onPunch} onClear={() => { setSelKey(null); setSelKeys([]); }}
          onReview={() => v && edit("review", { from: v.n, passes: 1 })}
          onHook={() => { if (v) { setPanel("assistant"); edit("hook", { from: v.n }); } }} />
      </aside>

      <footer className="tl-wrap">
        <Timeline id={id} v={v} blocks={blocks} words={state.words} t={t} playing={playing} selected={selKey} selection={selKeys}
          zoom={zoom} setZoom={setZoom} mode={tlMode} setMode={setTlMode} canTrim={!canEdit} ppsRef={ppsRef} snap={snap} setSnap={setSnap}
          onDeleteClips={askDeleteClips} onSplit={doSplit}
          onSelect={select} onSeek={seek} onScrub={scrub} onMenu={setCtx} onTrim={askTrim} onMove={askMove}
          wordSel={wordSel} onWordSel={pickWords} onCutWords={askCut} onDelete={askDelete} onAsk={ask}
          mix={mix} base={mixBase} live={live} next={next} liveEdit={live0} canEdit={canEdit} onMix={setMixDraft} onResetMix={() => setMixDraft(null)}
          onMusicPanel={() => setPanel("music")}
          onPick={(k) => pickItem(k)}
          onCaptionText={(c, text) => {
            if (canEdit) return flash(`Cannot edit now: ${canEdit.toLowerCase()}.`);
            made(edit("caption", { edits: [{ at: (c.t_in + c.t_out) / 2, text }], from: v?.n }));
          }}
          onEdit={(path, body, note) => { // add a passage back, remove pauses, B-roll stills (Batch D)
            if (!v) return;
            if (canEdit) return flash(`Cannot edit now: ${canEdit.toLowerCase()}.`);
            if (live0 && DRAFT_OPS[path]) return void edit(path, body);
            flash(note);
            edit(path, { ...body, from: v.n });
          }}
          onApplyMix={() => {
            if (!v?.music || !mixDraft) return;
            const m = mixDraft;
            setMixDraft(null);
            made(edit("music", { track: v.music.track, level_db: m.level_db, duck_db: v.music.duck_db, voice_db: m.voice_db, mix: true, from: v.n }));
          }} />
      </footer>

      {ctx && (
        <div className="ctx-menu" style={{ left: Math.min(ctx.x, window.innerWidth - 240), top: Math.min(ctx.y, window.innerHeight - 160) }} onPointerDown={(e) => e.stopPropagation()}>
          <div className="menu-label">Clip {ctx.span}</div>
          <button className="menu-item" onClick={() => { const b = blocks.find((x) => x.key === ctx.span); if (b) seek(b.t0, b.t1); setCtx(null); }}><span>Play this clip</span></button>
          <button className="menu-item" onClick={() => ask(ctx.span)}><span>Ask Montivo about this clip</span></button>
          <div className="menu-sep" />
          <button className="menu-item danger" disabled={!!canEdit || (v?.spans.length ?? 0) < 2} onClick={() => askDelete(ctx.span)}><span>Delete clip</span><kbd>⌫</kbd></button>
        </div>
      )}
      {dialog?.kind === "delete" && v && (
        <EditDialog kind="delete" state={state} v={v} span={dialog.span} next={next} newest={newest} onCancel={() => setDialog(null)}
          onConfirm={(review) => { setDialog(null); made(act("drop", { span: dialog.span, from: v.n, review: review ? 1 : 0 })); }} />
      )}
      {dialog?.kind === "words" && v && (
        <EditDialog kind="words" state={state} v={v} span={dialog.ask.ranges[0].span} cut={dialog.ask} next={next} newest={newest} onCancel={() => setDialog(null)}
          onConfirm={(review) => { setDialog(null); setWordSel(null); setSelKeys([]); made(act(dialog.ask.path, { ...dialog.ask.body, from: v.n, review: review ? 1 : 0 })); }} />
      )}
      {dialog?.kind === "title" && v && (
        <EditDialog kind="title" state={state} v={v} span="title" next={next} newest={newest} onCancel={() => setDialog(null)}
          onConfirm={(review) => { setDialog(null); setSelKey(null); made(edit("text", { title_card: false, from: v.n, review: review ? 1 : 0 })); }} />
      )}
      {dialog?.kind === "trim" && v && (
        <EditDialog kind="trim" state={state} v={v} span={dialog.ask.span} trim={dialog.ask} next={next} newest={newest} onCancel={() => setDialog(null)}
          onConfirm={(review) => { setDialog(null); made(act("trim", { span: dialog.ask.span, edge: dialog.ask.edge, word: dialog.ask.word, from: v.n, review: review ? 1 : 0 })); }} />
      )}
      {dialog?.kind === "move" && v && (
        <EditDialog kind="move" state={state} v={v} span={dialog.ask.span} move={dialog.ask} next={next} newest={newest} onCancel={() => setDialog(null)}
          onConfirm={(review) => { setDialog(null); made(act("reorder", { span: dialog.ask.span, before: dialog.ask.before, from: v.n, review: review ? 1 : 0 })); }} />
      )}
      {dialog?.kind === "export" && <ExportPage id={id} versions={visible} initial={sel} onClose={() => setDialog(null)}
        onStyle={() => { setDialog(null); setPanel("style"); }}
        hasKey={state.has_key} keyNote={state.key_note ?? null} costs={state.costs} />}
      {dialog?.kind === "ledger" && <LedgerDialog state={state} onClose={() => setDialog(null)} />}
      {dialog?.kind === "keys" && <ShortcutsDialog onClose={() => setDialog(null)} />}
      {dialog?.kind === "palette" && <Palette onClose={() => setDialog(null)} commands={[
        ...PANELS.map((x, i): Command => ({ id: `panel:${x.id}`, label: `${x.label} panel`, group: "Panels", key: String(i + 1), icon: x.icon, run: () => setPanel(x.id) })),
        { id: "play", label: playing ? "Pause" : "Play", group: "Viewer", key: "Space", icon: playing ? "pause" : "play", run: () => player.current?.toggle() },
        { id: "split", label: "Split the clip at the playhead", group: "Edit", key: "S", icon: "scissors", off: canEdit, run: doSplit },
        { id: "undo", label: draftSum?.can_undo ? "Undo the last draft edit" : "Undo: the previous version", group: "Edit", key: "⌘Z", icon: "undo", run: () => step(-1) },
        { id: "redo", label: draftSum?.can_redo ? "Redo the draft edit" : "Redo: the next version", group: "Edit", key: "⇧⌘Z", icon: "redo", run: () => step(1) },
        ...(draftV ? [{ id: "commit", label: `Make Version ${draftData?.draft?.n ?? ""} from the draft`, group: "Draft", icon: "check", off: canEdit, run: commitDraft },
          { id: "discard", label: "Discard the draft", group: "Draft", icon: "close", run: discardDraft }] : []),
        { id: "live", label: liveOn ? "Viewer: Rendered (the exported file)" : "Viewer: Live (edits play at once)", group: "Viewer", key: "V", off: replay ? "Replay is on" : null, run: () => setLive(!liveOn) },
        { id: "guide", label: `Safe-area guides: ${GUIDE_NAME[GUIDES[(GUIDES.indexOf(guide) + 1) % GUIDES.length]]}`, group: "Viewer", key: "G", run: nextGuide },
        { id: "snap", label: `Snapping ${snap ? "off" : "on"}`, group: "Timeline", key: "N", icon: "magnet", run: () => setSnap(!snap) },
        { id: "transcript", label: tlMode === "timeline" ? "Show the transcript" : "Show the timeline", group: "Timeline", icon: tlMode === "timeline" ? "transcript" : "film", run: () => setTlMode(tlMode === "timeline" ? "transcript" : "timeline") },
        { id: "review", label: "Review this version (Muse Spark, blind)", group: "Agent", icon: "spark", off: canEdit ?? (!v?.cut ? "Make the version first" : null), run: () => { if (v) made(edit("review", { from: v.n, passes: 1 })); } },
        { id: "ask", label: "Ask Montivo", group: "Agent", icon: "assistant", run: () => { setPanel("assistant"); setAskNonce((n) => n + 1); } },
        { id: "loop", label: "Watch the agent loop", group: "Agent", icon: "loop", run: () => go(`${base}/loop${replay ? `?replay=${replay}` : ""}`) },
        { id: "export", label: "Export", group: "Project", icon: "export", off: !v?.cut ? "No rendered version" : null, run: () => setDialog({ kind: "export" }) },
        { id: "cost", label: "Cost and time", group: "Project", run: () => setDialog({ kind: "ledger" }) },
        { id: "keys", label: "Keyboard shortcuts", group: "Help", key: "?", run: () => setDialog({ kind: "keys" }) },
        { id: "home", label: "All projects", group: "Project", icon: "back", run: () => go("#/") },
      ]} />}
      {toast && (
        <div className={`toast ${toast.tone ?? ""}`} onClick={() => setToast(null)}>
          {toast.text}
          {toast.action && <button className="link toast-act" onClick={() => toast.action!.run()}>{toast.action.label}</button>}
        </div>
      )}
    </div>
  );
}

/** The version a running job is making: the newest EDL while it has no cut yet, else the one after it. */
function nextN(state: RunState): number {
  const last = state.versions.at(-1);
  return last ? (last.cut ? last.n + 1 : last.n) : 1;
}

/** In replay the cost counts up with the log: the dollars each model line reports, and the run's own clock. */
function replaySpent(lines: LogLine[]): { usd: number; s: number } {
  let usd = 0, s = 0, sessionEnd = 0, session = -1;
  for (const l of lines) {
    if (l.session !== session) { s += sessionEnd; sessionEnd = 0; session = l.session; }
    sessionEnd = l.t;
    if (/^(done:|review loop:|review: review_v\d+ overall|watch suggests|plan: speech)/.test(l.msg)) continue; // each call is counted once, on its spark: line
    const m = l.msg.match(/\$(\d+\.\d+)/);
    if (m) usd += Number(m[1]);
  }
  return { usd, s: s + sessionEnd };
}

/** A caption line, callout or the music bed (A6) still on version v: its selection survives the edit that made v. */
const itemIn = (v: Version, key: string) => key.startsWith("cap:") ? v.caption_lines.some((c) => c.id === key.slice(4))
  : key.startsWith("callout:") ? v.callouts.some((c) => c.span === key.slice(8)) : key === "music" && !!v.music;

/** A timeline item a panel should show: `cap:<id>` (Captions) or `callout:<span>` (Text); `edit` opens its text box. */
export type PanelFocus = { key: string; edit: boolean; nonce: number };

type Toast = { text: string; tone?: string; action?: { label: string; run: () => void } };

const edits = (n: number) => `${n} edit${n === 1 ? "" : "s"}`;

/** GET/POST /draft's summary of the open draft (server/draft.py). */
type DraftSum = { id: string; base: number; latest: number; stale: boolean; ops: { op: string; args: Record<string, unknown>; say?: string }[];
  can_undo: boolean; can_redo: boolean; runtime_s: number | null; committing?: unknown; commit_failed?: string | null };

/** The editor's edit routes that a draft takes in live mode, and their bodies as the draft's op arguments. */
const DRAFT_OPS: Record<string, { op: string; args: (b: Record<string, unknown>) => Record<string, unknown> }> = {
  split: { op: "split", args: (b) => ({ span: b.span, word: b.word }) },
  drop: { op: "drop", args: (b) => ({ span: b.span }) },
  trim: { op: "trim", args: (b) => ({ span: b.span, edge: b.edge, word: b.word }) },
  reorder: { op: "reorder", args: (b) => ({ span: b.span, before: b.before ?? null }) },
  cut: { op: "cut", args: (b) => ({ ranges: (b.words as { span: string; from: number; to: number }[]).map((r) => [r.span, r.from, r.to]) }) },
  insert: { op: "insert", args: (b) => ({ turn: b.turn, a: b.word_from, b: b.word_to, before: b.before ?? null }) },
  pauses: { op: "pauses", args: (b) => ({ over: b.over, keep: b.keep }) },
  // the panels' edits (A3): the same draft, one render at Make Version N
  caption: { op: "caption", args: (b) => ({ edits: b.edits }) },
  glossary: { op: "glossary", args: (b) => ({ add: b.add ?? {}, drop: b.drop ?? [] }) },
  linebreak: { op: "caption_break", args: (b) => ({ at: b.at, join: !!b.join }) },
  text: { op: "text", args: (b) => pick(b, ["callouts", "title", "title_card"]) },
  style: { op: "style", args: (b) => pick(b, ["look", "callouts", "prompt", "why", "clips"]) },
  music: { op: "music", args: (b) => ({ track: b.track ?? null, ...pick(b, ["level_db", "duck_db", "voice_db"]) }) },
  pin: { op: "pin", args: (b) => ({ speaker: b.speaker, head: b.head ?? null }) },
  punch: { op: "punch", args: (b) => pick(b, ["on", "skip"]) },
  kit: { op: "kit", args: (b) => pick(b, ["kit", "name"]) },
};

/** The edits with no route of their own: in Rendered mode they go through POST /op (the same pure op, one version). */
const OP_ROUTE: Record<string, { op: string; args: (b: Record<string, unknown>) => Record<string, unknown> }> = {
  kit: DRAFT_OPS.kit,
};

/** The keys of `b` that are set (a draft op's arguments leave the rest to the op's defaults). */
const pick = (b: Record<string, unknown>, keys: string[]) =>
  Object.fromEntries(keys.filter((k) => b[k] !== undefined && b[k] !== null).map((k) => [k, b[k]]));
