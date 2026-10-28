import { useEffect, useMemo, useRef, useState } from "react";
import { api, tc } from "./api";
import { Icon } from "./icons";
import type { Look, LooksInfo, SavedLook, Version } from "./types";

// The Style panel and its canvas handles. A person's look (font, colours, words per line, the caption position) and
// callout places are staged here; while the viewer is paused it shows the server's preview still (engine/look.py: the
// same Pillow code the render burns in, so what you see is the new version's frame), and "Make Version N" renders it
// (POST /style -> montivo.human style, $0).

export type Place = [number, number] | null; // a callout's top-left in the 1080 x 1920 frame; null = back to auto
export type StyleDraft = { look: Look | null; callouts: Record<string, Place> };
export const EMPTY_DRAFT: StyleDraft = { look: null, callouts: {} };
export type Preview = { image: string; captions: PBox[]; callouts: PBox[]; title?: boolean };
type PBox = { text: string; box: number[]; placement: string; head_box: number[] | null; span?: string; t_in: number; t_out: number };

const W = 1080, H = 1920;
const SAFE_TOP = 220, SAFE_BOTTOM = 1620;
const pct = (x: number, of: number) => `${(x / of) * 100}%`;
const sameLook = (a: Look | null, b: Look | null) => JSON.stringify(a) === JSON.stringify(b);

export const draftCount = (d: StyleDraft, v: Version | null) =>
  (d.look && v && !sameLook(d.look, v.look) ? 1 : 0) + Object.keys(d.callouts).length;

let looksCache: Promise<LooksInfo> | null = null;
export const getLooks = () => (looksCache ??= api<LooksInfo>("/api/looks"));

/** The server's still of version v at cut second t with the draft's look and places, fetched while the viewer is
 *  paused (debounced; the server caches each one). Null when nothing is staged or the viewer plays. */
export function usePreview(id: string, v: Version | null, t: number, draft: StyleDraft, on: boolean): { p: Preview | null; busy: boolean } {
  const [p, setP] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const tq = Math.round(t * 10) / 10;
  const key = on && v ? JSON.stringify([v.n, tq, draft.look, draft.callouts]) : null;
  useEffect(() => {
    if (!key || !v) { setP(null); return; }
    let live = true;
    const h = window.setTimeout(() => {
      setBusy(true);
      api<Preview>(`/api/runs/${id}/preview`, { v: v.n, t: tq, look: draft.look, callouts: draft.callouts })
        .then((r) => { if (live) setP(r); }).catch(() => { if (live) setP(null); }).finally(() => { if (live) setBusy(false); });
    }, 160);
    return () => { live = false; window.clearTimeout(h); };
  }, [key]); // eslint-disable-line
  return { p, busy };
}

/** A caption time to show presets on: the line on screen at t, else the first line. */
function sampleT(v: Version, t: number) {
  const on = v.caption_lines.find((c) => t >= c.t_in && t < c.t_out);
  const c = on ?? v.caption_lines[0];
  return c ? Math.round(((c.t_in + c.t_out) / 2) * 10) / 10 : t;
}

function PresetCard({ id, v, t, look, name, on, onPick, saved, onDelete }: {
  id: string; v: Version; t: number; look: Look; name: string; on: boolean; onPick: () => void; saved?: boolean; onDelete?: () => void;
}) {
  const [src, setSrc] = useState<string | null>(null);
  const send = saved ? look : { preset: look.preset };
  const key = JSON.stringify(send);
  useEffect(() => {
    let live = true;
    api<Preview>(`/api/runs/${id}/preview`, { v: v.n, t, look: send }).then((r) => live && setSrc(r.image)).catch(() => {});
    return () => { live = false; };
  }, [id, v.n, t, key]); // eslint-disable-line
  return (
    <div className={`preset-card ${on ? "on" : ""}`} role="button" tabIndex={0} onClick={onPick}
      onKeyDown={(e) => { if (e.key === "Enter") onPick(); }} title={saved ? `Your look “${name}”` : `The ${name} preset`}>
      <span className="pc-img">{src ? <img src={src} alt="" /> : <span className="pulse" />}</span>
      <span className="pc-name">{name}</span>
      {onDelete && <button className="pc-del icon-btn" title="Delete this saved look" onClick={(e) => { e.stopPropagation(); onDelete(); }}><Icon name="close" size={11} /></button>}
    </div>
  );
}

function Color({ label, value, onChange, alpha }: { label: string; value: string; onChange: (x: string) => void; alpha?: boolean }) {
  const rgb = value.slice(0, 7);
  const a = alpha && value.length === 9 ? parseInt(value.slice(7), 16) : 255;
  const hexA = (n: number) => Math.round(n).toString(16).padStart(2, "0").toUpperCase();
  return (
    <label className="st-color">
      <input type="color" value={rgb} onChange={(e) => onChange(e.target.value.toUpperCase() + (alpha ? hexA(a) : ""))} />
      <span>{label}</span>
      {alpha && <input type="range" min={40} max={255} value={a} title="Opacity" onChange={(e) => onChange(rgb + hexA(Number(e.target.value)))} />}
    </label>
  );
}

function Seg<T extends string | number>({ value, options, onChange }: { value: T; options: [T, string][]; onChange: (x: T) => void }) {
  return (
    <div className="seg-group">
      {options.map(([k, label]) => <button key={String(k)} className={`seg ${value === k ? "on" : ""}`} onClick={() => onChange(k)}>{label}</button>)}
    </div>
  );
}

export function StylePanel({ id, v, t, next, canEdit, draft, setDraft, onSeek, onApply, hasKey, keyNote, cost, onPrompt }: {
  id: string; v: Version | null; t: number; next: number; canEdit: string | null;
  draft: StyleDraft; setDraft: (d: StyleDraft) => void; onSeek: (a: number) => void; onApply: () => void;
  hasKey: boolean; keyNote: string | null; cost: number; onPrompt: (text: string) => void;
}) {
  const [info, setInfo] = useState<LooksInfo | null>(null);
  const [words, setWords] = useState("");
  const [ask, setAsk] = useState("");
  const [saveName, setSaveName] = useState("");
  const setSaved = (saved: SavedLook[]) => setInfo((i) => (i ? { ...i, saved } : i));
  useEffect(() => { getLooks().then(setInfo).catch(() => {}); }, []);
  const base = v?.look ?? info?.default ?? null;
  const look = draft.look ?? base;
  useEffect(() => setWords((look?.highlight_add ?? []).join(", ")), [v?.n, look?.preset]); // eslint-disable-line
  const presetT = useMemo(() => (v ? sampleT(v, t) : 0), [v?.n]); // eslint-disable-line
  if (!v || !look || !info) return <section className="side-panel"><div className="panel-head"><span className="ph-title">Style</span></div></section>;
  const set = (patch: Partial<Look>) => {
    const nl = { ...look, ...patch };
    setDraft({ ...draft, look: sameLook(nl, v.look) ? null : nl });
  };
  const saveLook = () => {
    const name = saveName.trim();
    if (!name) return;
    api<{ saved: SavedLook[] }>("/api/looks/saved", { name, look }).then((r) => { setSaved(r.saved); setSaveName(""); looksCache = null; }).catch(() => {});
  };
  const pick = (p: Look) => set({ ...p, position: look.position, top: look.position === "fixed" ? look.top : p.top });
  const n = draftCount(draft, v);
  const covered = v.caption_lines.filter((c) => c.placement === "fixed" && c.over_face);
  const calls = v.callouts;
  const placeOf = (sid: string) => (sid in draft.callouts ? draft.callouts[sid] : v.spans.find((s) => s.id === sid)?.callout_xy ?? null);
  return (
    <section className="side-panel">
      <div className="panel-head"><span className="ph-title">Style</span><span className="ph-sub">captions and callouts</span></div>
      <div className="panel-body style-panel">
        <div className="pn-k">Describe it</div>
        <div className="st-ask">
          <textarea rows={2} value={ask} maxLength={300} placeholder="Bold yellow, one word at a time, higher up"
            onChange={(e) => setAsk(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && ask.trim() && hasKey && !canEdit) { e.preventDefault(); onPrompt(ask.trim()); setAsk(""); } }} />
          <button className="btn small" disabled={!ask.trim() || !hasKey || !!canEdit}
            title={canEdit ?? (hasKey ? `Muse Spark picks the look and Montivo renders Version ${next} (about $${cost.toFixed(3)})` : keyNote ?? "Set MODEL_API_KEY to style from a sentence")}
            onClick={() => { onPrompt(ask.trim()); setAsk(""); }}><Icon name="spark" size={12} /> Make Version {next} · ~${cost.toFixed(3)}</button>
        </div>
        <p className="mute small">Muse Spark reads the current look and how light the picture is behind the captions, then picks fonts and colours from the options below. Tweak the result here after.</p>
        <div className="pn-k">Presets</div>
        <div className="preset-grid">
          {info.presets.map((p) => <PresetCard key={p.id} id={id} v={v} t={presetT} look={p.look} name={p.name}
            on={look.preset === p.id && !info.saved.some((x) => sameLook(x.look, look))} onPick={() => pick(p.look)} />)}
        </div>
        <div className="pn-k">Your looks <span className="mute small">kept for every project</span></div>
        {info.saved.length > 0 && (
          <div className="preset-grid">
            {info.saved.map((x) => <PresetCard key={x.id} id={id} v={v} t={presetT} look={x.look} name={x.name} saved
              on={sameLook(x.look, look)} onPick={() => set(x.look)}
              onDelete={() => api<{ saved: SavedLook[] }>(`/api/looks/saved/${x.id}`, undefined, "DELETE").then((r) => setSaved(r.saved)).catch(() => {})} />)}
          </div>
        )}
        <div className="st-row">
          <input className="st-input grow" value={saveName} maxLength={40} placeholder="Name this look, e.g. Nocta brand"
            onChange={(e) => setSaveName(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") saveLook(); }} />
          <button className="btn quiet small" disabled={!saveName.trim()} onClick={saveLook} title="Keep the look as it is now (with your staged changes) for other projects">Save look</button>
        </div>

        <div className="pn-k">Text</div>
        <div className="st-row">
          <label className="field grow">Font
            <select value={look.font} onChange={(e) => set({ font: e.target.value })}>
              {info.fonts.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
            </select>
          </label>
          <label className="field">Case
            <Seg value={look.case} options={[["as_said", "Aa"], ["upper", "AA"]]} onChange={(c) => set({ case: c })} />
          </label>
        </div>
        <label className="field">Size <span className="mono mute">{look.size} px</span>
          <input type="range" min={48} max={140} step={2} value={look.size} onChange={(e) => set({ size: Number(e.target.value) })} />
        </label>
        <label className="field">Words per line
          <Seg value={look.max_words} options={[[1, "1"], [2, "2"], [3, "3"], [4, "4"], [5, "5"], [6, "6"]]} onChange={(x) => set({ max_words: x })} />
        </label>
        <div className="st-colors">
          <Color label="Text" value={look.text} onChange={(x) => set({ text: x })} />
          <Color label="Outline" value={look.stroke} onChange={(x) => set({ stroke: x })} />
        </div>
        <label className="field">Outline <span className="mono mute">{look.stroke_w ? `${look.stroke_w} px` : "none"}</span>
          <input type="range" min={0} max={16} value={look.stroke_w} onChange={(e) => set({ stroke_w: Number(e.target.value) })} />
        </label>
        <div className="st-row">
          <label className="st-check"><input type="checkbox" checked={look.shadow} onChange={(e) => set({ shadow: e.target.checked })} /> Soft shadow</label>
          <label className="st-check"><input type="checkbox" checked={!!look.box} onChange={(e) => set({ box: e.target.checked ? "#000000C8" : "" })} /> Box behind the line</label>
        </div>
        {look.box && <Color label="Box" value={look.box} alpha onChange={(x) => set({ box: x })} />}

        <div className="pn-k">The word being said</div>
        <Seg value={look.active} options={[["pill", "Pill"], ["color", "Colour"], ["karaoke", "Karaoke"], ["none", "None"]]} onChange={(a) => set({ active: a })} />
        {look.active !== "none" && (
          <div className="st-colors">
            <Color label={look.active === "pill" ? "Pill" : "Colour"} value={look.active_bg} onChange={(x) => set({ active_bg: x })} />
            {look.active === "pill" && <Color label="Pill text" value={look.active_fg} onChange={(x) => set({ active_fg: x })} />}
          </div>
        )}
        <div className="pn-k">Highlighted words</div>
        <div className="st-row">
          <label className="st-check"><input type="checkbox" checked={!!look.highlight} onChange={(e) => set({ highlight: e.target.checked ? "#FFC629" : "" })} /> Colour names</label>
          {look.highlight && <Color label="" value={look.highlight} onChange={(x) => set({ highlight: x })} />}
        </div>
        {look.highlight && (
          <label className="field">Also highlight <span className="mute small">(Montivo, Muse, Spark, Voice, SAM, Meta always)</span>
            <input className="st-input" value={words} placeholder="agent, reviewed" onChange={(e) => setWords(e.target.value)}
              onBlur={() => set({ highlight_add: words.split(",").map((w) => w.trim().toLowerCase()).filter(Boolean) })} />
          </label>
        )}

        <div className="pn-k">Position</div>
        <Seg value={look.position} options={[["auto", "Auto: keep off the face"], ["fixed", "Fixed"]]}
          onChange={(p) => set({ position: p, top: p === "fixed" ? look.top : look.top })} />
        <label className="field">{look.position === "fixed" ? "Every line at" : "Lines start at"} <span className="mono mute">y {look.top}</span>
          <input type="range" min={160} max={1700} step={10} value={look.top} onChange={(e) => set({ top: Number(e.target.value) })} />
        </label>
        <p className="mute small">Or drag the caption on the video (pause first). Dragging fixes the position; Auto still moves a line that would touch the face.</p>
        {covered.length > 0 && (
          <div className="st-warn">
            <b>{covered.length} line{covered.length > 1 ? "s" : ""} cover the face</b> at your fixed position. The review loop keeps your choice.
            <div className="st-warn-list">
              {covered.slice(0, 6).map((c) => <button key={c.id} className="btn quiet small" onClick={() => onSeek(c.t_in)}>{tc(c.t_in)} {c.text}</button>)}
            </div>
          </div>
        )}

        <div className="pn-k">Callouts</div>
        <div className="st-colors">
          <Color label="Fill" value={look.callout_bg} onChange={(x) => set({ callout_bg: x })} />
          <Color label="Text" value={look.callout_fg} onChange={(x) => set({ callout_fg: x })} />
        </div>
        <p className="mute small">Drag a callout on the video to pin it there. The red area shows where the face goes while it is up.</p>
        <div className="list">
          {calls.length === 0 && <p className="mute small pad">No callouts in this version.</p>}
          {calls.map((c) => {
            const place = placeOf(c.span);
            return (
              <div key={c.span} className={`list-row cap-row ${t >= c.t_in && t < c.t_out ? "on" : ""} ${c.span in draft.callouts ? "staged" : ""}`}
                role="button" tabIndex={0} onClick={() => onSeek(c.t_in + 0.05)}>
                <span className="lr-t mono">{tc(c.t_in)}</span>
                <span className="lr-txt"><b>{c.text}</b>
                  <span className="lr-was">{place ? `pinned at ${place[0]}, ${place[1]}` : "auto: anchored to the speaker"}
                    {c.over_face && !(c.span in draft.callouts) ? " · covers the face" : ""}</span></span>
                <span className="lr-end">
                  {place && <button className="btn quiet small" disabled={!!canEdit} onClick={(e) => { e.stopPropagation(); setDraft({ ...draft, callouts: { ...draft.callouts, [c.span]: null } }); }}>Auto</button>}
                </span>
              </div>
            );
          })}
        </div>
      </div>
      {n > 0 && (
        <div className="cap-apply">
          <span>{n} change{n > 1 ? "s" : ""}</span>
          <span className="grow" />
          <button className="btn quiet" onClick={() => setDraft(EMPTY_DRAFT)}>Discard</button>
          <button className="btn" disabled={!!canEdit} title={canEdit ?? `Makes Version ${next} ($0, a re-render)`} onClick={onApply}>Make Version {next}</button>
        </div>
      )}
    </section>
  );
}

/** The preview still over the paused video, and the drag handles for the caption on screen and every callout. Boxes
 *  are 1080 x 1920 frame pixels (the EDL's), drawn as percentages of the 9:16 viewer. */
export function StyleCanvas({ v, t, draft, setDraft, preview, busy, show }: {
  v: Version; t: number; draft: StyleDraft; setDraft: (d: StyleDraft) => void; preview: Preview | null; busy: boolean; show: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<{ kind: "caption" | "callout"; span?: string; box: number[]; dx: number; dy: number; head: number[] | null } | null>(null);
  const fresh = preview && !preview.title;
  const caps: PBox[] = fresh ? preview!.captions : v.caption_lines.filter((c) => c.box && t >= c.t_in && t < c.t_out).map((c) => ({ ...c, box: c.box!, placement: c.placement ?? "", head_box: c.head_box ?? null }));
  const calls: PBox[] = fresh ? preview!.callouts : v.callouts.filter((c) => c.box && t >= c.t_in && t < c.t_out).map((c) => ({ ...c, box: c.box!, placement: c.placement ?? "", head_box: c.head_box ?? null }));
  if (!show) return null;
  const look = draft.look ?? v.look;
  const toFrame = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect();
    return [((e.clientX - r.left) / r.width) * W, ((e.clientY - r.top) / r.height) * H];
  };
  const start = (e: React.PointerEvent, kind: "caption" | "callout", b: PBox) => {
    e.stopPropagation(); e.preventDefault();
    try { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); } catch { /* a synthetic pointer */ }
    const [x, y] = toFrame(e);
    setDrag({ kind, span: b.span, box: [...b.box], dx: x - b.box[0], dy: y - b.box[1], head: b.head_box });
  };
  const move = (e: React.PointerEvent) => {
    if (!drag) return;
    const [x, y] = toFrame(e);
    const [, , w, h] = drag.box;
    let nx = drag.kind === "caption" ? drag.box[0] : Math.round(x - drag.dx);
    let ny = Math.round(y - drag.dy);
    // snaps: the centre line, the safe area's edges, and for a caption just clear of the face (below or above it)
    if (drag.kind === "callout" && Math.abs(nx + w / 2 - W / 2) < 18) nx = W / 2 - w / 2;
    const hd = drag.head;
    const snaps = [SAFE_TOP, SAFE_BOTTOM - h, ...(drag.kind === "caption" && hd ? [hd[3] + 36, hd[1] - 36 - h] : [])];
    for (const s of snaps) if (Math.abs(ny - s) < 16) ny = Math.round(s);
    nx = Math.max(0, Math.min(W - w, nx)); ny = Math.max(drag.kind === "caption" ? 160 : 0, Math.min((drag.kind === "caption" ? 1700 : H - h), ny));
    setDrag({ ...drag, box: [nx, ny, w, h] });
  };
  const end = () => {
    if (!drag) return;
    const [x, y] = drag.box;
    if (drag.kind === "caption" && look) setDraft({ ...draft, look: { ...look, position: "fixed", top: Math.round(y / 10) * 10 } });
    if (drag.kind === "callout" && drag.span) setDraft({ ...draft, callouts: { ...draft.callouts, [drag.span]: [x, y] } });
    setDrag(null);
  };
  const box = (b: number[], cls: string, extra?: React.ReactNode, props?: React.HTMLAttributes<HTMLDivElement>) => (
    <div className={cls} style={{ left: pct(b[0], W), top: pct(b[1], H), width: pct(b[2], W), height: pct(b[3], H) }} {...props}>{extra}</div>
  );
  const hb = drag?.head ?? null;
  const overHead = drag && hb && drag.box[0] < hb[2] && drag.box[0] + drag.box[2] > hb[0] && drag.box[1] < hb[3] && drag.box[1] + drag.box[3] > hb[1];
  return (
    <div className="style-canvas" ref={ref} onPointerMove={move} onPointerUp={end} onClick={(e) => e.stopPropagation()}>
      {fresh && <img className="style-still" src={preview!.image} alt="Preview of the new version" />}
      {(fresh || busy) && <span className="style-tag">{busy ? "Preview…" : "Preview · not made yet"}</span>}
      {hb && box([hb[0], hb[1], hb[2] - hb[0], hb[3] - hb[1]], "sc-head", <span>face while it is up</span>)}
      {drag && <span className="sc-guide-v" style={{ left: "50%" }} />}
      {caps.length > 0 && box(caps[0].box, `sc-handle sc-caption ${drag?.kind === "caption" ? "sc-orig" : ""}`, <span className="sc-label">caption · drag up or down</span>,
        { onPointerDown: (e) => start(e, "caption", caps[0]) })}
      {calls.map((c) => (
        <div key={c.span}>{box(c.box, `sc-handle sc-callout ${c.placement === "overlap" ? "sc-warn" : ""} ${drag?.kind === "callout" && drag.span === c.span ? "sc-orig" : ""}`, <span className="sc-label">{c.placement === "pinned" ? "pinned" : "callout"} · drag</span>,
          { onPointerDown: (e) => start(e, "callout", c) })}</div>
      ))}
      {drag && box(drag.box, `sc-handle dragging ${overHead ? "sc-warn" : ""}`, <span className="sc-label">{overHead ? "over the face" : `x ${drag.box[0]} · y ${drag.box[1]}`}</span>)}
    </div>
  );
}
