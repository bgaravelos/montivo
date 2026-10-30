import { Fragment, useEffect, useRef, useState } from "react";
import { dur, usd } from "./api";
import { Icon } from "./icons";
import type { CutAsk } from "./edit";
import type { MoveAsk, TrimAsk } from "./Timeline";
import type { RunState, Version } from "./types";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

/** A modal's keyboard contract (WAI-ARIA dialog): focus moves into it when it opens, Tab and Shift+Tab stay inside it,
 *  Esc closes it, and focus goes back to the control that opened it. The Export page uses it too. */
export function useModal(box: React.RefObject<HTMLElement>, onClose: () => void) {
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const el = box.current;
    if (el && !el.contains(document.activeElement)) el.focus({ preventScroll: true });
    const k = (e: KeyboardEvent) => {
      const d = box.current;
      if (!d) return;
      if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); close.current(); return; }
      if (e.key !== "Tab") return;
      const items = [...d.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((x) => x.offsetParent !== null || x === document.activeElement);
      if (!items.length) { e.preventDefault(); d.focus(); return; }
      const first = items[0], last = items[items.length - 1], at = document.activeElement;
      if (e.shiftKey && (at === first || at === d || !d.contains(at))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (at === last || !d.contains(at))) { e.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", k, true);
    return () => {
      window.removeEventListener("keydown", k, true);
      if (opener && opener.isConnected && opener !== document.body) opener.focus({ preventScroll: true });
    };
  }, [box]);
}

function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: React.ReactNode; wide?: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  useModal(box, onClose);
  return (
    <div className="modal-bg" onPointerDown={onClose}>
      <div ref={box} tabIndex={-1} className={`modal ${wide ? "wide" : ""}`} onPointerDown={(e) => e.stopPropagation()}
        role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head"><span className="mh-title">{title}</span><button className="icon-btn" onClick={onClose} aria-label="close"><Icon name="close" size={14} /></button></div>
        {children}
      </div>
    </div>
  );
}

/** Delete clip, trim, move and delete words share one confirm: what changes, the version it makes, and what it costs
 *  before you commit. */
export function EditDialog({ kind, state, v, span, trim, move, cut, next, newest, onCancel, onConfirm }: {
  kind: "delete" | "trim" | "move" | "words" | "title"; state: RunState; v: Version; span: string; trim?: TrimAsk; move?: MoveAsk; cut?: CutAsk; next: number;
  newest?: number | null; // the newest version: an edit on an older one (after undo) forks from it
  onCancel: () => void; onConfirm: (review: boolean) => void;
}) {
  const [review, setReview] = useState(false);
  const sp = v.spans.find((s) => s.id === span);
  const canReview = state.has_key;
  const clips = cut?.clips;
  const title = kind === "title" ? "Turn the title card off?" : clips ? `Delete ${clips.length} clips?`
    : kind === "words" ? `Delete ${cut!.n} word${cut!.n === 1 ? "" : "s"}?` : kind === "delete" ? `Delete clip ${span}?` : kind === "move" ? `Move clip ${span} ${move!.label}?`
    : `${trim!.words > 0 ? "Extend" : "Trim"} clip ${span}?`;
  const roleNote = kind === "move" && move ? (() => { // the clip that ends up first is the hook, the last the payoff
    const ids = v.spans.map((s) => s.id).filter((x) => x !== span);
    const at = move.before ? ids.indexOf(move.before) : ids.length;
    const order = [...ids.slice(0, at), span, ...ids.slice(at)];
    const was = v.spans.map((s) => s.id);
    const notes = [order[0] !== was[0] ? `${order[0]} becomes the hook` : "", order.at(-1) !== was.at(-1) ? `${order.at(-1)} becomes the ending` : ""].filter(Boolean);
    return notes.length ? ` Roles follow the position: ${notes.join(", ")}.` : "";
  })() : "";
  return (
    <Modal title={title} onClose={onCancel}>
      <div className="modal-body">
        {kind === "title" ? (
          <p>Removes the {v.title_s.toFixed(1)} s title card “{v.title_card_text}” from the start; the cut opens on its first clip. The Text panel turns it back on.</p>
        ) : clips ? (<>
          <p>Removes clips <b>{clips.join(", ")}</b> (−{cut!.seconds.toFixed(1)} s) in one version.</p>
          <ul className="cut-lines">{cut!.lines.map((l) => <li key={l}>{l}</li>)}</ul>
          {cut!.error && <p className="err">{cut!.error}</p>}
        </>) : kind === "words" ? (<>
          <p>Removes <b>{cut!.n} word{cut!.n === 1 ? "" : "s"}</b> (−{cut!.seconds.toFixed(2)} s): <s className="w cut">“{cut!.text.slice(0, 160)}{cut!.text.length > 160 ? "…" : ""}”</s></p>
          <ul className="cut-lines">{cut!.lines.map((l) => <li key={l}>{l}</li>)}</ul>
          {cut!.error && <p className="err">{cut!.error}</p>}
        </>) : kind === "move" ? (
          <p>Plays “{sp?.words.slice(0, 120)}{(sp?.words.length ?? 0) > 120 ? "…" : ""}” {move!.label === "to the end" ? "at the end" : move!.label}. The cuts that now meet are tightened to the speech again.{roleNote}</p>
        ) : kind === "delete" ? (
          <p>Removes <b>{sp?.duration.toFixed(1)} s</b>: “{sp?.words.slice(0, 140)}{(sp?.words.length ?? 0) > 140 ? "…" : ""}”. The cuts on either side are tightened to the speech again.</p>
        ) : (
          <p>{trim!.words > 0 ? "Adds" : "Removes"} <b>{Math.abs(trim!.words)} word{Math.abs(trim!.words) === 1 ? "" : "s"}</b> ({trim!.dt >= 0 ? "+" : "−"}{Math.abs(trim!.dt).toFixed(2)} s) at the {trim!.edge === "in" ? "start" : "end"}: the clip {trim!.label}.
            Like the reviewer's own trims, the edge may settle on a sentence end one or two words away and on the quietest frame nearby.</p>
        )}
        <p className="mute">Version {v.n} stays as it is. This makes <b>Version {next}</b> and renders it with ffmpeg.</p>
        {newest != null && newest > v.n && <ForkNote from={v.n} newest={newest} next={next} />}
        <label className={`check ${canReview ? "" : "off"}`} title={canReview ? "" : "needs MODEL_API_KEY"}>
          <input type="checkbox" checked={review && canReview} disabled={!canReview} onChange={(e) => setReview(e.target.checked)} />
          then review it blind <span className="mute">~{usd(state.costs.review)}</span>
        </label>
      </div>
      <div className="modal-foot">
        <span className="cost">{review && canReview ? `~${usd(state.costs.review)}` : "render only · $0"}</span>
        <span className="grow" />
        <button className="btn quiet" onClick={onCancel}>Cancel</button>
        <button className={`btn ${kind === "delete" || kind === "words" || kind === "title" ? "danger" : ""}`} autoFocus disabled={!!cut?.error} onClick={() => onConfirm(review && canReview)}>
          {kind === "title" ? "Turn it off" : clips ? `Delete ${clips.length} clips` : kind === "words" ? "Delete words" : kind === "delete" ? "Delete clip" : kind === "move" ? "Move clip" : trim!.words > 0 ? "Extend clip" : "Trim clip"}
        </button>
      </div>
    </Modal>
  );
}

/** An edit on a version older than the newest (after an undo): it branches from there, nothing after it is lost. */
export function ForkNote({ from, newest, next }: { from: number; newest: number; next: number }) {
  return (
    <p className="fork-note">You stepped back to Version {from}: Version {next} branches from it. Versions {from + 1}–{newest} stay in Versions,
      and Undo from Version {next} leads back to {from}.</p>
  );
}

export function LedgerDialog({ state, onClose }: { state: RunState; onClose: () => void }) {
  const rows = Object.entries(state.ledger.stages);
  return (
    <Modal title={`Cost and time · run ${state.id}`} onClose={onClose} wide>
      <div className="modal-body">
        <p className="mute">Every model call and ffmpeg pass on this run folder, from ledger.json, including every review and re-render made while building the engine on this footage.</p>
        <table className="ledger">
          <thead><tr><th>stage</th><th>calls</th><th>tokens in / out</th><th>seconds</th><th>cost</th></tr></thead>
          <tbody>
            {rows.map(([k, s]) => (
              <tr key={k}><td>{k}</td><td>{s.calls}</td><td>{s.tokens_in || s.tokens_out ? `${(s.tokens_in / 1000).toFixed(1)}K / ${(s.tokens_out / 1000).toFixed(1)}K` : "–"}</td>
                <td>{s.seconds.toFixed(1)}</td><td>{usd(s.cost_usd, 4)}</td></tr>
            ))}
          </tbody>
          <tfoot><tr><td>total</td><td /><td /><td>{dur(state.ledger.wall_seconds)}</td><td>{usd(state.ledger.total_cost_usd, 4)}</td></tr></tfoot>
        </table>
      </div>
    </Modal>
  );
}

export function ShortcutsDialog({ onClose }: { onClose: () => void }) {
  const keys: [string, string][] = [
    ["Space", "Play / pause"], ["J · K · L", "Shuttle back · stop · forward (press again for 2×, 4×)"],
    ["← →", "One frame back / forward"], ["⇧ ← →", "One second back / forward"], ["↑ ↓", "Previous / next cut"],
    ["S · ⌘K", "Split the clip at the playhead"], ["[ · ]", "Select the clip to the left / right"],
    ["⇧ click · ⌘ click", "Select a run of clips · add or remove one"], ["⌘A", "Select every clip"],
    ["⌫ / Delete", "Delete the selected clips, or the selected transcript words"],
    ["N", "Snapping on / off (hold ⇧ while dragging: free)"],
    ["1 – 8", "Open a panel: Assistant, Media, Captions, Style, Text, Music, Speakers, Versions"], ["⌘P · /", "Command palette"], ["?", "Monti: where is it, how do I…"],
    ["Enter", "Retext the caption or callout picked on the timeline"],
    ["⌘Z · ⇧⌘Z", "Previous version · next version (in Live: the draft's last edit)"], ["⌘+ · ⌘−", "Zoom the timeline"], ["Esc", "Clear the selection"],
    [". · ,", "Next · previous reviewer issue"], ["V", "Viewer: Live (edits play at once) or Rendered (the exported file)"], ["G", "Safe-area guides: Reels, TikTok, Shorts, off"], ["⇧M", "Mute the viewer"], ["F", "Full screen"],
  ];
  return (
    <Modal title="Keyboard shortcuts" onClose={onClose}>
      <div className="modal-body"><dl className="props keys">{keys.map(([k, d]) => <Fragment key={k}><dt><kbd>{k}</kbd></dt><dd>{d}</dd></Fragment>)}</dl></div>
    </Modal>
  );
}
