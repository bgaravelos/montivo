import { useEffect, useState } from "react";
import { api, tc } from "./api";
import { Icon } from "./icons";
import type { Costs, Version } from "./types";

// The cover maker, the Export page's left column: a clean still of the version with a headline laid out around the
// speaker's head (engine/thumb.py). Frame is the still alone; Headline, Sticker and Cutout are the three designs; the
// cutout lays the speaker back over a giant headline and needs SAM 3.1's person mask of that still (about $0.01, made
// once per still). Every change re-renders the cover on the server ($0, POST /cover/render) with the measured checks
// (text in the grid window, off the face, readable on a grid tile) and the tile at the size a phone shows it.
// "Design it with Muse Spark" is the agent: it picks the exact still from a contact sheet around its cover moments,
// writes three headlines, picks a template, renders, checks the cover at tile size and applies one fix.

export type Template = "frame" | "headline" | "sticker" | "cutout";
export type CoverState = { t: number; template: Template; text: string; highlight: string };
type Check = { id: string; ok: boolean; detail: string; value?: number };
type Rendered = { image?: string; tile?: string; checks?: Check[]; need_mask?: boolean; lines?: string[] };
type Headline = { text: string; highlight: string; why: string };
type Attempt = CoverState & { t_s: number; pass: boolean; score: number; check: { note: string; fix: string } };
type Design = { final: { t_s: number; template: Template; text: string; highlight: string }; pass: boolean; headlines: Headline[];
  still_why?: string; template_why?: string; attempts: Attempt[]; cost_usd: number };
export type CoverRecord = { moments: { t_s: number; why: string }[]; design?: Design };

const TEMPLATES: { k: Template; label: string; note: string }[] = [
  { k: "frame", label: "Frame", note: "the still as it is, no captions" },
  { k: "headline", label: "Headline", note: "headline over the top, the face punched in" },
  { k: "sticker", label: "Sticker", note: "headline on a tilted marker sticker" },
  { k: "cutout", label: "Cutout", note: "giant headline behind the head (SAM 3.1 cut-out)" },
];

export function CoverMaker({ id, v, total, aspect, W, H, grid, bands, cover, setCover, record, setRecord, hasKey, keyNote, costs, onErr }: {
  id: string; v: Version | null; total: number; aspect: string; W: number; H: number; grid: boolean;
  bands: { side: boolean; pct: number }; cover: CoverState; setCover: (c: CoverState) => void;
  record: CoverRecord | null; setRecord: (r: CoverRecord) => void;
  hasKey: boolean; keyNote: string | null; costs: Costs | undefined; onErr: (e: string | null) => void;
}) {
  const [shown, setShown] = useState<Rendered | null>(null);
  const [rendering, setRendering] = useState(false);
  const [designing, setDesigning] = useState(false);
  const [masking, setMasking] = useState(false);
  const designCost = costs?.cover_design ?? 0.05;
  const maskCost = costs?.cover_mask ?? 0.01;
  useEffect(() => {
    if (!v) return;
    let live = true;
    setRendering(true);
    const tm = setTimeout(() => {
      api<Rendered>(`/api/runs/${id}/cover/render`, { v: v.n, t: cover.t, template: cover.template, text: cover.text, highlight: cover.highlight, aspect })
        .then((r) => { if (live) setShown((prev) => (r.need_mask ? { ...prev, need_mask: true, checks: [] } : r)); }).catch((e) => { if (live) onErr(String((e as Error).message)); })
        .finally(() => { if (live) setRendering(false); });
    }, 220);
    return () => { live = false; clearTimeout(tm); };
  }, [id, v, cover.t, cover.template, cover.text, cover.highlight, aspect]); // eslint-disable-line react-hooks/exhaustive-deps

  const design = async () => {
    if (!v) return;
    setDesigning(true); onErr(null);
    try {
      const r = await api<CoverRecord>(`/api/runs/${id}/cover/design`, { v: v.n, aspect });
      setRecord(r);
      const f = r.design?.final;
      if (f) setCover({ t: f.t_s, template: f.template, text: f.text, highlight: f.highlight });
    } catch (e) { onErr(String((e as Error).message)); } finally { setDesigning(false); }
  };
  const cutOut = async () => {
    if (!v) return;
    setMasking(true); onErr(null);
    try {
      await api(`/api/runs/${id}/cover/mask`, { v: v.n, t: cover.t, aspect });
      setCover({ ...cover }); // render again, now with the mask
    } catch (e) { onErr(String((e as Error).message)); } finally { setMasking(false); }
  };
  const words = cover.text.trim() ? cover.text.trim().split(/\s+/) : [];
  const clean = (w: string) => w.replace(/[^\p{L}\p{N}]/gu, "").toLowerCase();
  const d = record?.design;
  const last = d?.attempts.at(-1);
  return (
    <>
      <div className="cm-top">
        <div className={`ex-cover ${grid ? "grid" : ""}`} style={{ aspectRatio: `${W} / ${H}` }}>
          {shown?.image && <img src={shown.image} alt={`Cover, ${cover.template}`} className={shown.need_mask ? "cm-dim" : ""} />}
          {shown?.need_mask && (
            <div className="cm-mask">
              <span>The cutout lays the speaker over the headline: SAM 3.1 cuts them out of this still first.</span>
              <button className="btn small" disabled={!hasKey || masking} onClick={cutOut}
                title={hasKey ? `One SAM 3.1 image request, about $${maskCost.toFixed(2)}, made once per still` : keyNote ?? "Set MODEL_API_KEY"}>
                {masking ? <><span className="pulse" /> Cutting out…</> : <>Cut out the speaker · ~${maskCost.toFixed(2)}</>}</button>
            </div>)}
          {(rendering || !shown) && !shown?.need_mask && <span className="cm-busy"><span className="pulse" /></span>}
          {grid && (bands.side
            ? <><i className="ex-grid-l" style={{ width: `${bands.pct}%` }} /><i className="ex-grid-r" style={{ width: `${bands.pct}%` }} /></>
            : <><i className="ex-grid-top" /><i className="ex-grid-bot" /></>)}
          {grid && <span className="ex-grid-tag" style={bands.side ? { top: 8, left: `calc(${bands.pct}% + 8px)` } : undefined}>profile grid 3:4</span>}
        </div>
        <div className="cm-tile">
          <span className="pn-k">On the grid</span>
          <div className="cm-tile-row" title="The profile-grid tile at the size a phone shows it">
            {shown?.tile && <img src={shown.tile} alt="Grid tile" />}
          </div>
          <div className="cm-checks">
            {(shown?.checks ?? []).map((c) => (
              <span key={c.id} className={`cm-check ${c.ok ? "ok" : "bad"}`} title={c.detail}>{c.ok ? "✓" : "!"} {c.detail}</span>
            ))}
          </div>
        </div>
      </div>

      <button className="btn cm-design" disabled={!v || designing || !hasKey} onClick={design}
        title={hasKey ? `Muse Spark picks the exact still, writes headlines, picks a template and checks the cover at grid size (about $${designCost.toFixed(2)}, 30-60 s)` : keyNote ?? "Set MODEL_API_KEY to let Muse Spark design it"}>
        {designing ? <><span className="pulse" /> Designing… still, headline, check</> : <><Icon name="spark" size={12} /> Design it with Muse Spark · ~${designCost.toFixed(2)}</>}
      </button>
      {d && last && (
        <div className={`cm-verdict ${d.pass ? "ok" : "warn"}`}>
          <b>{d.pass ? "Passed its own check" : "Best of " + d.attempts.length + ", no pass"}</b>
          <span className="mute"> · {d.attempts.length} render{d.attempts.length > 1 ? "s" : ""} · ${d.cost_usd.toFixed(3)}</span>
          <div className="mute small">{last.check.note}</div>
        </div>
      )}

      <div className="cm-templates" role="radiogroup" aria-label="Cover template">
        {TEMPLATES.map((x) => (
          <button key={x.k} role="radio" aria-checked={cover.template === x.k} className={`cm-tpl ${cover.template === x.k ? "on" : ""}`}
            onClick={() => setCover({ ...cover, template: x.k })} title={x.note}>{x.label}</button>
        ))}
      </div>

      {cover.template !== "frame" && (
        <>
          <label className="field">Headline <span className="mute small">2 to 5 words</span>
            <input className="st-input" value={cover.text} maxLength={80} placeholder="Type a headline, or let Muse Spark write it"
              onChange={(e) => setCover({ ...cover, text: e.target.value })} />
          </label>
          {words.length > 0 && (
            <div className="cm-words" aria-label="Word to highlight">
              <span className="mute small">Highlight</span>
              {words.map((w, k) => (
                <button key={k} className={`cm-word ${clean(w) === clean(cover.highlight) ? "on" : ""}`}
                  onClick={() => setCover({ ...cover, highlight: clean(w) === clean(cover.highlight) ? "" : w })}>{w}</button>
              ))}
            </div>
          )}
          {!!d?.headlines.length && (
            <div className="cm-heads">
              {d.headlines.map((h, k) => (
                <button key={k} className={`ex-moment ${h.text === cover.text ? "on" : ""}`} title={h.why}
                  onClick={() => setCover({ ...cover, text: h.text, highlight: h.highlight })}><span>{h.text}</span></button>
              ))}
            </div>
          )}
        </>
      )}

      <label className="field">Still <span className="mono mute">{tc(cover.t, 2)}</span>
        <input type="range" min={0} max={Math.max(0.1, total - 0.1)} step={0.04} value={cover.t}
          onChange={(e) => setCover({ ...cover, t: Number(e.target.value) })} />
      </label>
      {!!record?.moments.length && (
        <div className="ex-pick">
          {d && <button className={`ex-moment ${Math.abs(d.final.t_s - cover.t) < 0.02 ? "on" : ""}`} onClick={() => setCover({ ...cover, t: d.final.t_s })}
            title={d.still_why}><b>{tc(d.final.t_s, 2)}</b> <span>Spark's still: {d.still_why}</span></button>}
          {record.moments.map((m, k) => (
            <button key={k} className={`ex-moment ${Math.abs(m.t_s - cover.t) < 0.02 ? "on" : ""}`} onClick={() => setCover({ ...cover, t: m.t_s })} title={m.why}>
              <b>{tc(m.t_s)}</b> <span>{m.why}</span></button>
          ))}
        </div>
      )}
    </>
  );
}
