import { useMemo, useRef, useState } from "react";
import { useModal } from "./Dialogs";
import { Icon } from "./icons";

/** One command of the palette: what it says, its key (shown, not bound here), and what it does; `off` says why it is
 *  greyed out now. */
export type Command = { id: string; label: string; group: string; key?: string; icon?: string; off?: string | null; run: () => void };

/** The command palette (⌘P or /): every panel and the editor's actions by name, typed to filter (each word must
 *  appear), ↑ ↓ to move, Enter to run. Descript's Action bar; ⌘K stays split at the playhead. */
export function Palette({ commands, onClose }: { commands: Command[]; onClose: () => void }) {
  const box = useRef<HTMLDivElement>(null);
  useModal(box, onClose);
  const [q, setQ] = useState("");
  const [at, setAt] = useState(0);
  const shown = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    return commands.filter((c) => words.every((w) => `${c.label} ${c.group}`.toLowerCase().includes(w)));
  }, [q, commands]);
  const run = (c: Command | undefined) => {
    if (!c || c.off) return;
    onClose();
    c.run();
  };
  const k = Math.min(at, Math.max(0, shown.length - 1));
  return (
    <div className="modal-bg palette-bg" onPointerDown={onClose}>
      <div ref={box} tabIndex={-1} className="modal palette" role="dialog" aria-modal="true" aria-label="Commands"
        onPointerDown={(e) => e.stopPropagation()}>
        <input autoFocus className="palette-q" value={q} placeholder="Type a command or a panel…" aria-label="Command"
          onChange={(e) => { setQ(e.target.value); setAt(0); }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setAt((x) => Math.min(shown.length - 1, x + 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setAt((x) => Math.max(0, x - 1)); }
            else if (e.key === "Enter") { e.preventDefault(); run(shown[k]); }
          }} />
        <div className="palette-list" role="listbox">
          {shown.map((c, i) => (
            <button key={c.id} role="option" aria-selected={i === k} className={`palette-row ${i === k ? "on" : ""}`}
              disabled={!!c.off} title={c.off ?? undefined} onPointerEnter={() => setAt(i)} onClick={() => run(c)}>
              {c.icon ? <Icon name={c.icon} size={14} /> : <span className="palette-dot" />}
              <span className="palette-label">{c.label}</span>
              <span className="palette-group">{c.off ?? c.group}</span>
              {c.key && <kbd>{c.key}</kbd>}
            </button>
          ))}
          {!shown.length && <p className="mute small pad">No command matches “{q}”.</p>}
        </div>
      </div>
    </div>
  );
}
