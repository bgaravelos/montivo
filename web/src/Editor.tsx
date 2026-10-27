import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, dur, fileUrl, tc, usd, useRun } from "./api";
import { Assistant } from "./Assistant";
import { EditDialog, ExportDialog, LedgerDialog, ShortcutsDialog } from "./Dialogs";
import { childOf, cutAsk, flatWords, layout, parentOf, projectName, type CutAsk, type WordSel } from "./edit";
import { Icon } from "./icons";
import { Inspector, type PunchAsk } from "./Inspector";
import { ProgressInline } from "./Progress";
import { GUIDE_NAME, GUIDES, SafeArea, type Guide } from "./SafeArea";
import { CaptionsPanel, MediaPanel, SpeakersPanel, TextPanel, VersionsPanel } from "./Panels";
import { FPS, Player, type PlayerHandle } from "./Player";
import { Timeline, ZOOM_MAX, ZOOM_MIN, type ClipMenu, type MoveAsk, type TrimAsk } from "./Timeline";
import type { LogLine, RunState } from "./types";

type Props = { id: string; replay: number; go: (hash: string) => void };
type PanelId = "assistant" | "media" | "captions" | "text" | "speakers" | "versions";
type Dialog = { kind: "delete"; span: string } | { kind: "words"; ask: CutAsk } | { kind: "trim"; ask: TrimAsk } | { kind: "move"; ask: MoveAsk } | { kind: "export" } | { kind: "ledger" } | { kind: "keys" };

const PANELS: { id: PanelId; label: string; icon: string }[] = [
  { id: "assistant", label: "Assistant", icon: "assistant" },
  { id: "media", label: "Media", icon: "media" },
  { id: "captions", label: "Captions", icon: "captions" },
  { id: "text", label: "Text", icon: "text" },
  { id: "speakers", label: "Speakers", icon: "speakers" },
  { id: "versions", label: "Versions", icon: "versions" },
];

const typing = (el: EventTarget | null) => {
  const e = el as HTMLElement | null;
  return !!e && (e.tagName === "INPUT" || e.tagName === "TEXTAREA" || e.tagName === "SELECT" || e.isContentEditable);
};

/** The editor: top bar, icon rail + one panel, the 9:16 viewer, the inspector and the timeline. The agent loop stays in
 *  view through the Assistant (the pipeline checklist and every version's story), the score chip and the review
 *  markers on the ruler; every edit makes a new version, so undo and redo walk the version tree. */
export function Editor({ id, replay, go }: Props) {
  const { state, error, lines, job, seen, progress } = useRun(id, replay);
  const [sel, setSel] = useState<number | null>(null);
  const [selKey, setSelKey] = useState<string | null>(null); // a span id or "title"
  const [wordSel, setWordSel] = useState<WordSel | null>(null); // words selected in the transcript view
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
  const [toast, setToast] = useState<{ text: string; tone?: string } | null>(null);
  const [guide, setGuideState] = useState<Guide>(() => {
    try { const g = localStorage.getItem("montivo.viewer.guide") as Guide | null; return g && GUIDES.includes(g) ? g : "off"; } catch { return "off"; }
  });
  const setGuide = (g: Guide) => { setGuideState(g); try { localStorage.setItem("montivo.viewer.guide", g); } catch { /* this tab only */ } };
  const nextGuide = () => setGuide(GUIDES[(GUIDES.indexOf(guide) + 1) % GUIDES.length]);
  const player = useRef<PlayerHandle>(null);

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
  const opening = kept ?? (best && visible.some((v) => v.n === best) ? best : newest);
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
    const grew = seenNewest.current != null && newest != null && newest > seenNewest.current;
    seenNewest.current = newest;
    if (grew || replay) return setSel(newest);
    setSel((s) => (s != null && visible.some((v) => v.n === s) ? s : opening));
  }, [visible.length, newest, opening, replay]); // eslint-disable-line

  const v = visible.find((x) => x.n === sel) ?? null;
  const parent = v && state ? parentOf(v, state.versions) : undefined;
  const blocks = useMemo(() => (v && state ? layout(v, parent, state.words) : []), [v, parent, state]);
  const block = blocks.find((b) => b.key === selKey && b.kind !== "ghost") ?? null;
  const punch = v && selKey?.startsWith("punch:") ? v.punch_ins.find((x) => `punch:${x.key}` === selKey) ?? null : null;
  useEffect(() => { if (selKey && v && !blocks.some((b) => b.key === selKey && b.kind !== "ghost")) setSelKey(null); }, [v?.n]); // eslint-disable-line
  useEffect(() => setWordSel(null), [v?.n, tlMode]);

  const undoTo = v && state ? visible.find((x) => x.n === parent?.n) : undefined;
  const redoTo = v ? childOf(v, visible) : undefined;
  const busy = !!job?.running;
  const canEdit = replay ? "Replay is on" : busy ? `${job!.label} is running` : null;

  const flash = useCallback((text: string, tone?: string) => { setToast({ text, tone }); window.setTimeout(() => setToast(null), 4500); }, []);
  const act = useCallback(async (path: string, body: unknown) => {
    try { await api(`/api/runs/${id}/${path}`, body); } catch (e) { flash(String((e as Error).message), "error"); }
  }, [id, flash]);
  const seek = useCallback((a: number, b?: number) => player.current?.seek(a, b), []);
  const scrub = useCallback((x: number) => { player.current?.pause(); player.current?.seek(x); }, []);
  const onTime = useCallback((x: number) => { tRef.current = x; setT(x); }, []);
  const getTime = useCallback(() => tRef.current, []);
  const select = useCallback((k: string | null) => { setSelKey(k); setCtx(null); setWordSel(null); }, []);
  const pickWords = useCallback((w: WordSel | null) => { setWordSel(w); if (w) { setSelKey(null); setCtx(null); } }, []);
  const askCut = () => { // the transcript selection -> the confirm: drop, trim or cut, whichever the words need
    if (!v || !state || !wordSel) return;
    if (canEdit) return flash(`Cannot delete now: ${canEdit.toLowerCase()}.`);
    const a = cutAsk(v, flatWords(blocks), wordSel, state.words);
    if (a) setDialog({ kind: "words", ask: a });
  };
  const ask = useCallback((span: string) => { setSelKey(span); setPanel("assistant"); setAskNonce((n) => n + 1); setCtx(null); }, []);
  const askDelete = useCallback((span: string) => { setCtx(null); setDialog({ kind: "delete", span }); }, []);
  const askTrim = useCallback((a: TrimAsk) => setDialog({ kind: "trim", ask: a }), []);
  const askMove = useCallback((a: MoveAsk) => setDialog({ kind: "move", ask: a }), []);
  const pick = useCallback((n: number) => setSel(n), []);
  const onPunch = (a: PunchAsk) => { setSelKey(null); setPanel("assistant"); act("punch", { ...a, from: v?.n }); };
  const step = (dir: -1 | 1) => {
    const to = dir < 0 ? undoTo : redoTo;
    if (!to) return flash(dir < 0 ? "Nothing to undo: this is the first version." : "Nothing to redo.");
    setSel(to.n);
    flash(`${dir < 0 ? "Undo" : "Redo"}: Version ${to.n}`);
  };

  // keyboard, through a ref so the listener sees the current state without re-subscribing
  const keys = useRef<(e: KeyboardEvent) => void>(() => {});
  keys.current = (e: KeyboardEvent) => {
    if (typing(e.target) || dialog) return;
    const p = player.current;
    const mod = e.metaKey || e.ctrlKey;
    if (mod && (e.key === "z" || e.key === "Z")) { e.preventDefault(); step(e.shiftKey ? 1 : -1); return; }
    if (mod && (e.key === "=" || e.key === "+")) { e.preventDefault(); setZoom((z) => Math.min(ZOOM_MAX, (z ?? 30) * 1.4)); return; }
    if (mod && e.key === "-") { e.preventDefault(); setZoom((z) => Math.max(ZOOM_MIN, (z ?? 30) / 1.4)); return; }
    if (mod) return;
    switch (e.key) {
      case " ": e.preventDefault(); p?.toggle(); break;
      case "j": case "J": p?.shuttle(-1); break;
      case "k": case "K": p?.shuttle(0); break;
      case "l": case "L": p?.shuttle(1); break;
      case "ArrowLeft": e.preventDefault(); p?.step(e.shiftKey ? -1 : -1 / FPS); break;
      case "ArrowRight": e.preventDefault(); p?.step(e.shiftKey ? 1 : 1 / FPS); break;
      case "Delete": case "Backspace":
        if (wordSel) {
          e.preventDefault();
          askCut();
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
      case "Escape": if (ctx) setCtx(null); else if (menu) setMenu(false); else if (wordSel) setWordSel(null); else setSelKey(null); break;
      case "g": case "G": nextGuide(); break;
      case "n": case "N": { // the next (Shift: previous) reviewer issue of this version
        const iss = [...(v?.review?.issues ?? [])].sort((a, b) => a.t_s - b.t_s);
        if (!iss.length) { flash(v?.review ? "The reviewer raised no issue on this version." : "This version has not been reviewed."); break; }
        const now = tRef.current;
        const to = e.shiftKey ? [...iss].reverse().find((i) => i.t_s < now - 0.25) : iss.find((i) => i.t_s > now + 0.05);
        if (!to) { flash(e.shiftKey ? "No issue before the playhead." : "No issue after the playhead."); break; }
        p?.pause(); p?.seek(to.t_s); setSelKey(null);
        flash(`Issue ${iss.indexOf(to) + 1} of ${iss.length} at ${tc(to.t_s)} · ${to.severity}: ${to.what}`);
        break;
      }
      case "?": setDialog({ kind: "keys" }); break;
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
            : busy ? <><span className="pulse" /> {job!.label}…</> : v ? <>Saved · Version {v.n} of {newest}</> : "No version yet"}</span>
        </div>
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
        <button className="btn export" disabled={!v?.cut} onClick={() => setDialog({ kind: "export" })}><Icon name="export" /> Export</button>
      </header>

      <nav className="rail">
        {PANELS.map((x) => (
          <button key={x.id} className={`rail-btn ${panel === x.id ? "on" : ""}`} onClick={() => setPanel(x.id)} title={x.label}>
            <Icon name={x.icon} size={19} />
            <span>{x.label}</span>
            {x.id === "assistant" && busy && <span className="rail-dot" />}
          </button>
        ))}
      </nav>

      <aside className="panel">
        {panel === "assistant" && (
          <Assistant state={state} versions={visible} lines={lines} job={job} seen={seen} progress={progress} replay={replay} v={v}
            clip={block?.kind === "span" ? block.span!.id : null} getTime={getTime} focusNonce={askNonce}
            onSend={(text, chips, review, planner) => act("replan", { instruction: text, chips, from: v?.n, review, planner })}
            onStop={() => act("stop", {})} />
        )}
        {panel === "media" && <MediaPanel state={state} />}
        {panel === "captions" && (
          <CaptionsPanel v={v} t={t} next={next} canEdit={canEdit} onSeek={seek}
            onApply={(edits) => { setPanel("assistant"); act("caption", { edits, from: v?.n }); }} />
        )}
        {panel === "text" && (
          <TextPanel state={state} v={v} t={t} next={next} canEdit={canEdit} onSeek={seek}
            onApply={(edit) => { setPanel("assistant"); act("text", { ...edit, from: v?.n }); }} />
        )}
        {panel === "speakers" && (
          <SpeakersPanel id={id} v={v} next={next} canEdit={canEdit}
            onApply={(speaker, head) => { setPanel("assistant"); act("pin", { speaker, head, from: v?.n }); }} />
        )}
        {panel === "versions" && <VersionsPanel id={id} state={state} versions={visible} sel={sel} onPick={pick} go={go} replay={replay} opening={opening} />}
      </aside>

      <main className="viewer">
        {v?.cut && (
          <button className={`guide-btn ${guide !== "off" ? "on" : ""}`} onClick={nextGuide}
            title="Safe-area guides: the margins Reels, TikTok and Shorts cover with their own UI (G cycles)">
            Safe area: {GUIDE_NAME[guide]}
          </button>
        )}
        {v?.cut ? (
          <Player ref={player} key={`${v.n}`} src={fileUrl(id, v.cut)} onTime={onTime} onPlaying={setPlaying}
            marks={blocks.filter((b) => b.kind !== "ghost").map((b) => b.t0)}
            label={`v${v.n}`} overlay={<SafeArea kind={guide} />} />
        ) : (
          <div className="player-wait">{busy || replay ? <span><span className="pulse" /> Waiting for the first render…</span> : "No cut rendered yet."}</div>
        )}
      </main>

      <aside className="insp">
        <Inspector id={id} state={state} versions={visible} v={v} block={block} punch={punch} next={next} words={state.words} busy={busy}
          canEdit={canEdit} onSeek={seek} onPick={pick} onDelete={askDelete} onAsk={ask} onPunch={onPunch} onClear={() => setSelKey(null)}
          onReview={() => v && act("review", { from: v.n, passes: 1 })}
          onHook={() => { if (v) { setPanel("assistant"); act("hook", { from: v.n }); } }} />
      </aside>

      <footer className="tl-wrap">
        <Timeline id={id} v={v} blocks={blocks} words={state.words} t={t} playing={playing} selected={selKey}
          zoom={zoom} setZoom={setZoom} mode={tlMode} setMode={setTlMode} canTrim={!canEdit}
          onSelect={select} onSeek={seek} onScrub={scrub} onMenu={setCtx} onTrim={askTrim} onMove={askMove}
          wordSel={wordSel} onWordSel={pickWords} onCutWords={askCut} />
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
        <EditDialog kind="delete" state={state} v={v} span={dialog.span} next={next} onCancel={() => setDialog(null)}
          onConfirm={(review) => { setDialog(null); setPanel("assistant"); act("drop", { span: dialog.span, from: v.n, review: review ? 1 : 0 }); }} />
      )}
      {dialog?.kind === "words" && v && (
        <EditDialog kind="words" state={state} v={v} span={dialog.ask.ranges[0].span} cut={dialog.ask} next={next} onCancel={() => setDialog(null)}
          onConfirm={(review) => { setDialog(null); setWordSel(null); setPanel("assistant"); act(dialog.ask.path, { ...dialog.ask.body, from: v.n, review: review ? 1 : 0 }); }} />
      )}
      {dialog?.kind === "trim" && v && (
        <EditDialog kind="trim" state={state} v={v} span={dialog.ask.span} trim={dialog.ask} next={next} onCancel={() => setDialog(null)}
          onConfirm={(review) => { setDialog(null); setPanel("assistant"); act("trim", { span: dialog.ask.span, edge: dialog.ask.edge, word: dialog.ask.word, from: v.n, review: review ? 1 : 0 }); }} />
      )}
      {dialog?.kind === "move" && v && (
        <EditDialog kind="move" state={state} v={v} span={dialog.ask.span} move={dialog.ask} next={next} onCancel={() => setDialog(null)}
          onConfirm={(review) => { setDialog(null); setPanel("assistant"); act("reorder", { span: dialog.ask.span, before: dialog.ask.before, from: v.n, review: review ? 1 : 0 }); }} />
      )}
      {dialog?.kind === "export" && <ExportDialog id={id} versions={visible} initial={sel} onClose={() => setDialog(null)} />}
      {dialog?.kind === "ledger" && <LedgerDialog state={state} onClose={() => setDialog(null)} />}
      {dialog?.kind === "keys" && <ShortcutsDialog onClose={() => setDialog(null)} />}
      {toast && <div className={`toast ${toast.tone ?? ""}`} onClick={() => setToast(null)}>{toast.text}</div>}
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
