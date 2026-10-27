import { Fragment, useEffect, useState } from "react";
import { dur, tc, usd } from "./api";
import { Icon } from "./icons";
import type { TrimAsk } from "./Timeline";
import type { RunState, Version } from "./types";

function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: React.ReactNode; wide?: boolean }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } };
    window.addEventListener("keydown", k, true);
    return () => window.removeEventListener("keydown", k, true);
  }, [onClose]);
  return (
    <div className="modal-bg" onPointerDown={onClose}>
      <div className={`modal ${wide ? "wide" : ""}`} onPointerDown={(e) => e.stopPropagation()} role="dialog" aria-label={title}>
        <div className="modal-head"><span className="mh-title">{title}</span><button className="icon-btn" onClick={onClose} aria-label="close"><Icon name="close" size={14} /></button></div>
        {children}
      </div>
    </div>
  );
}

/** Delete clip and trim share one confirm: what changes, the version it makes, and what it costs before you commit. */
export function EditDialog({ kind, state, v, span, trim, next, onCancel, onConfirm }: {
  kind: "delete" | "trim"; state: RunState; v: Version; span: string; trim?: TrimAsk; next: number;
  onCancel: () => void; onConfirm: (review: boolean) => void;
}) {
  const [review, setReview] = useState(false);
  const sp = v.spans.find((s) => s.id === span);
  const canReview = state.has_key;
  const title = kind === "delete" ? `Delete clip ${span}?` : `${trim!.words > 0 ? "Extend" : "Trim"} clip ${span}?`;
  return (
    <Modal title={title} onClose={onCancel}>
      <div className="modal-body">
        {kind === "delete" ? (
          <p>Removes <b>{sp?.duration.toFixed(1)} s</b>: “{sp?.words.slice(0, 140)}{(sp?.words.length ?? 0) > 140 ? "…" : ""}”. The cuts on either side are tightened to the speech again.</p>
        ) : (
          <p>{trim!.words > 0 ? "Adds" : "Removes"} <b>{Math.abs(trim!.words)} word{Math.abs(trim!.words) === 1 ? "" : "s"}</b> ({trim!.dt >= 0 ? "+" : "−"}{Math.abs(trim!.dt).toFixed(2)} s) at the {trim!.edge === "in" ? "start" : "end"}: the clip {trim!.label}.
            Like the reviewer's own trims, the edge may settle on a sentence end one or two words away and on the quietest frame nearby.</p>
        )}
        <p className="mute">Version {v.n} stays as it is. This makes <b>Version {next}</b> and renders it with ffmpeg.</p>
        <label className={`check ${canReview ? "" : "off"}`} title={canReview ? "" : "needs MODEL_API_KEY"}>
          <input type="checkbox" checked={review && canReview} disabled={!canReview} onChange={(e) => setReview(e.target.checked)} />
          then review it blind <span className="mute">~{usd(state.costs.review)}</span>
        </label>
      </div>
      <div className="modal-foot">
        <span className="cost">{review && canReview ? `~${usd(state.costs.review)}` : "render only · $0"}</span>
        <span className="grow" />
        <button className="btn quiet" onClick={onCancel}>Cancel</button>
        <button className={`btn ${kind === "delete" ? "danger" : ""}`} autoFocus onClick={() => onConfirm(review && canReview)}>
          {kind === "delete" ? "Delete clip" : trim!.words > 0 ? "Extend clip" : "Trim clip"}
        </button>
      </div>
    </Modal>
  );
}

const mb = (b: number | null | undefined) => (b == null ? "–" : `${(b / 1e6).toFixed(1)} MB`);

export function ExportDialog({ id, versions, initial, onClose }: { id: string; versions: Version[]; initial: number | null; onClose: () => void }) {
  const cuts = versions.filter((x) => x.cut);
  const [n, setN] = useState(initial ?? cuts.at(-1)?.n ?? 1);
  const v = cuts.find((x) => x.n === n);
  return (
    <Modal title="Export" onClose={onClose}>
      <div className="modal-body">
        <label className="field">Version
          <select value={n} onChange={(e) => setN(Number(e.target.value))}>
            {[...cuts].reverse().map((x) => (
              <option key={x.n} value={x.n}>Version {x.n}{x.review ? ` · ${x.review.overall}${x.review.pass ? " ✓" : ""}` : ""}</option>
            ))}
          </select>
        </label>
        {v && (
          <dl className="props export-props">
            <dt>Format</dt><dd>MP4 · H.264 + AAC · 1080 × 1920 (9:16)</dd>
            <dt>Duration</dt><dd className="mono">{tc(v.duration_s ?? v.runtime_s, 2)}</dd>
            <dt>Loudness</dt><dd>{v.loudness ? <>{v.loudness.integrated_lufs} LUFS · peak {v.loudness.true_peak_dbtp} dBTP</> : "–"}</dd>
            <dt>Size</dt><dd>{mb(v.bytes)}</dd>
            <dt>Review</dt><dd>{v.review ? `${v.review.overall}/100${v.review.pass ? ", passes" : ""}` : "not reviewed"}</dd>
          </dl>
        )}
      </div>
      <div className="modal-foot">
        <span className="grow" />
        <button className="btn quiet" onClick={onClose}>Cancel</button>
        {v && <a className="btn" href={`/api/runs/${id}/export?v=${v.n}`} download onClick={() => window.setTimeout(onClose, 300)}><Icon name="export" /> Download</a>}
      </div>
    </Modal>
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
    ["← →", "One frame back / forward"], ["⇧ ← →", "One second back / forward"], ["⌫ / Delete", "Delete the selected clip"],
    ["⌘Z · ⇧⌘Z", "Previous version · next version"], ["⌘+ · ⌘−", "Zoom the timeline"], ["Esc", "Clear the selection"],
  ];
  return (
    <Modal title="Keyboard shortcuts" onClose={onClose}>
      <div className="modal-body"><dl className="props keys">{keys.map(([k, d]) => <Fragment key={k}><dt><kbd>{k}</kbd></dt><dd>{d}</dd></Fragment>)}</dl></div>
    </Modal>
  );
}
