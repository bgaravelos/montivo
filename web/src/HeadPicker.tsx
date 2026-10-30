import { useEffect, useState } from "react";
import { api } from "./api";
import { speakerName } from "./edit";
import type { HeadsAt, Version } from "./types";

const W = 1080, H = 1920; // the editor's viewer is the 9:16 frame

/** SP4: the tracked heads on the paused viewer while the Speakers panel is open (GET /heads/at: frame pixels through
 *  the crop): a click on a face stages "follow this head when the speaker of this clip talks", the same pin the
 *  Speakers panel's head cards stage. */
export function HeadPicker({ id, v, t, show, staged, onPick }: {
  id: string; v: Version; t: number; show: boolean; staged: { speaker: string; head: string | null } | null;
  onPick: (speaker: string, head: string) => void;
}) {
  const [at, setAt] = useState<HeadsAt | null>(null);
  const tq = Math.round(t * 10) / 10;
  useEffect(() => {
    if (!show) return setAt(null);
    let live = true;
    const h = window.setTimeout(() => {
      api<HeadsAt>(`/api/runs/${id}/heads/at?t=${tq}&${v.draft ? "draft=1" : `v=${v.n}`}`)
        .then((r) => { if (live) setAt(r); }).catch(() => { if (live) setAt(null); });
    }, 150);
    return () => { live = false; window.clearTimeout(h); };
  }, [id, v.n, v.draft, tq, show]);
  if (!show || !at || !at.speaker) return null;
  const pinned = (staged?.speaker === at.speaker ? staged.head : null) ?? v.speaker_pins[at.speaker] ?? null;
  const solo = new Set(v.spans.map((s) => s.speaker)).size < 2; // one voice: the head is shown, there is nothing to pin (SP5)
  return (
    <div className="head-picker" onClick={(e) => e.stopPropagation()}>
      {at.heads.map((h) => {
        const [x0, y0, x1, y1] = h.box;
        return (
          <button key={h.id} className={`hp-box ${pinned === h.id ? "on" : ""} ${solo ? "solo" : ""}`}
            style={{ left: `${(Math.max(0, x0) / W) * 100}%`, top: `${(Math.max(0, y0) / H) * 100}%`,
              width: `${((Math.min(W, x1) - Math.max(0, x0)) / W) * 100}%`, height: `${((Math.min(H, y1) - Math.max(0, y0)) / H) * 100}%` }}
            title={solo ? `The crop follows head ${h.id}, ${speakerName(v, at.speaker!)}` : `Follow head ${h.id} when ${speakerName(v, at.speaker!)} talks (clip ${at.span})`}
            onClick={() => { if (!solo) onPick(at.speaker!, h.id); }}>
            <span>{solo ? `${speakerName(v, at.speaker!)} · head ${h.id}` : `head ${h.id}${pinned === h.id ? " · pinned" : ""}`}</span>
          </button>
        );
      })}
      {!at.heads.length && <span className="hp-none">No tracked head here</span>}
    </div>
  );
}
