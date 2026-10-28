import { useEffect, useMemo, useState } from "react";
import { api, frameUrl, tc } from "./api";
import { Icon } from "./icons";
import type { Version } from "./types";

// The Export page: a cover and a file name on the left, the destination, what goes in the bundle and the pre-flight on
// the right (as CapCut and Descript split it). Nothing here re-encodes for its own sake: the platforms re-encode, so the
// only presets are the cut as rendered and a small 720p copy. The files come from POST /export/bundle
// (python -m montivo.export, $0); more than one file downloads as a zip.

type Check = { id: string; label: string; status: "ok" | "warn" | "fail" | "info"; detail: string };
type Kind = "video" | "clean" | "srt" | "vtt" | "cover" | "audio" | "transcript";
type Saved = { include: Kind[]; preset: "full" | "720"; grid: boolean };

const KINDS: { k: Kind; label: string; note: string }[] = [
  { k: "video", label: "Video", note: "MP4 with the captions burned in" },
  { k: "clean", label: "Video without captions", note: "MP4 for the app's own captions · renders again, about 20 s" },
  { k: "srt", label: "Captions file", note: "SRT, the lines as burned in" },
  { k: "vtt", label: "Web captions", note: "WebVTT" },
  { k: "cover", label: "Cover", note: "JPG 1080 × 1920 and the 3:4 profile-grid crop" },
  { k: "audio", label: "Audio", note: "WAV, loudness as in the cut" },
  { k: "transcript", label: "Transcript", note: "TXT with times" },
];
const mb = (b: number | null | undefined) => (b == null ? "–" : `${(b / 1e6).toFixed(1)} MB`);

function load(): Saved {
  try {
    const s = JSON.parse(localStorage.getItem("montivo.export") ?? "null") as Saved | null;
    if (s && Array.isArray(s.include) && s.include.length) return s;
  } catch { /* first export */ }
  return { include: ["video", "srt", "cover"], preset: "full", grid: false };
}

function download(url: string, name: string) {
  const a = document.createElement("a");
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
}

export function ExportPage({ id, versions, initial, onClose, onStyle, hasKey, keyNote, coverCost }: {
  id: string; versions: Version[]; initial: number | null; onClose: () => void; onStyle: () => void;
  hasKey: boolean; keyNote: string | null; coverCost: number;
}) {
  const cuts = versions.filter((x) => x.cut);
  const [n, setN] = useState(initial ?? cuts.at(-1)?.n ?? 1);
  const v = cuts.find((x) => x.n === n) ?? null;
  const total = v?.duration_s ?? v?.runtime_s ?? 0;
  const [saved, setSaved] = useState<Saved>(load);
  const [coverT, setCoverT] = useState(() => Math.round(Math.max(v?.title_s ?? 0, total / 3) * 10) / 10);
  const [name, setName] = useState(`montivo-${id}-v${n}`);
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<{ name: string; url: string; bytes: number }[] | null>(null);
  const [moments, setMoments] = useState<{ t_s: number; why: string }[]>([]);
  const [picking, setPicking] = useState(false);
  useEffect(() => { setMoments([]); api<{ moments: { t_s: number; why: string }[] }>(`/api/runs/${id}/export/cover?v=${n}`).then((r) => setMoments(r.moments)).catch(() => {}); }, [id, n]);
  const pickCover = async () => {
    setPicking(true); setErr(null);
    try {
      const r = await api<{ moments: { t_s: number; why: string }[] }>(`/api/runs/${id}/export/cover`, { v: n });
      setMoments(r.moments);
      if (r.moments[0]) setCoverT(r.moments[0].t_s);
    } catch (e) { setErr(String((e as Error).message)); } finally { setPicking(false); }
  };
  useEffect(() => { try { localStorage.setItem("montivo.export", JSON.stringify(saved)); } catch { /* this tab only */ } }, [saved]);
  useEffect(() => { setName((x) => x.replace(/-v\d+$/, `-v${n}`)); setChecks(null); setDone(null); setErr(null);
    api<{ checks: Check[] }>(`/api/runs/${id}/export/check?v=${n}`).then((r) => setChecks(r.checks))
      .catch((e) => setChecks([{ id: "err", label: "Pre-flight", status: "info", detail: `could not be read (${(e as Error).message})` }])); }, [id, n]);
  useEffect(() => setDone(null), [saved, coverT, name]);
  const inc = saved.include;
  const toggle = (k: Kind) => setSaved({ ...saved, include: inc.includes(k) ? inc.filter((x) => x !== k) : [...inc, k] });
  const p720 = saved.preset === "720";
  const est = useMemo(() => {
    const video = (v?.bytes ?? 0) * (p720 ? 0.11 : 1);
    return (inc.includes("video") ? video : 0) + (inc.includes("clean") ? video : 0) + (inc.includes("audio") ? total * 192000 : 0)
      + (inc.includes("cover") ? 300000 : 0);
  }, [inc, v, p720, total]);
  const files = inc.reduce((a, k) => a + (k === "cover" ? 2 : 1), 0);
  const worst = !checks?.length || checks.some((c) => c.id === "err") ? "info" : checks.some((c) => c.status === "fail") ? "fail" : checks?.some((c) => c.status === "warn") ? "warn" : "ok";
  const go = async () => {
    if (!v || !inc.length) return;
    setBusy(true); setErr(null);
    try {
      const r = await api<{ files: { name: string; url: string; bytes: number }[]; zip: { name: string; url: string; bytes: number } | null }>(
        `/api/runs/${id}/export/bundle`, { v: v.n, include: inc, preset: saved.preset, name, cover_t: inc.includes("cover") ? coverT : null });
      const one = r.zip ?? r.files[0];
      download(one.url, one.name);
      setDone(r.zip ? [r.zip, ...r.files] : r.files);
    } catch (e) { setErr(String((e as Error).message)); } finally { setBusy(false); }
  };
  return (
    <div className="modal-bg" onPointerDown={onClose}>
      <div className="modal export-page" onPointerDown={(e) => e.stopPropagation()} role="dialog" aria-label="Export"
        onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}>
        <div className="modal-head"><span className="mh-title">Export</span><button className="icon-btn" onClick={onClose} aria-label="close"><Icon name="close" size={14} /></button></div>
        <div className="ex-grid">
          <div className="ex-left">
            <div className={`ex-cover ${saved.grid ? "grid" : ""}`}>
              {v && <img src={frameUrl(id, v.n, coverT, true)} alt="Cover frame" />}
              {saved.grid && <><i className="ex-grid-top" /><i className="ex-grid-bot" /><span className="ex-grid-tag">profile grid 3:4</span></>}
            </div>
            <label className="field">Cover frame <span className="mono mute">{tc(coverT)}</span>
              <input type="range" min={0} max={Math.max(0.1, total - 0.1)} step={0.1} value={coverT} onChange={(e) => setCoverT(Number(e.target.value))} />
            </label>
            <div className="ex-pick">
              <button className="btn quiet small" disabled={!v || picking || !hasKey} onClick={pickCover}
                title={hasKey ? `Muse Spark watches Version ${n} and names its best cover moments (about $${coverCost.toFixed(3)})` : keyNote ?? "Set MODEL_API_KEY to let Muse Spark pick"}>
                {picking ? <><span className="pulse" /> Watching…</> : <><Icon name="spark" size={12} /> Let Muse Spark pick · ~${coverCost.toFixed(3)}</>}</button>
              {moments.map((m, k) => (
                <button key={k} className={`ex-moment ${Math.abs(m.t_s - coverT) < 0.05 ? "on" : ""}`} onClick={() => setCoverT(m.t_s)} title={m.why}>
                  <b>{tc(m.t_s)}</b> <span>{m.why}</span></button>
              ))}
            </div>
            <label className="st-check"><input type="checkbox" checked={saved.grid} onChange={(e) => setSaved({ ...saved, grid: e.target.checked })} /> Show the Instagram grid crop</label>
            <label className="field">File name
              <input className="st-input" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
            </label>
          </div>
          <div className="ex-right">
            <label className="field">Version
              <select value={n} onChange={(e) => setN(Number(e.target.value))}>
                {[...cuts].reverse().map((x) => (
                  <option key={x.n} value={x.n}>Version {x.n}{x.review ? ` · ${x.review.overall}${x.review.pass ? " ✓" : ""}` : ""} · {tc(x.duration_s ?? x.runtime_s)}</option>
                ))}
              </select>
            </label>
            <div className="pn-k">Destination</div>
            <div className="ex-dest">
              <button className={`ex-card ${!p720 ? "on" : ""}`} onClick={() => setSaved({ ...saved, preset: "full" })}>
                <b>Reels · TikTok · Shorts</b><span>1080 × 1920 · H.264 · as rendered</span></button>
              <button className={`ex-card ${p720 ? "on" : ""}`} onClick={() => setSaved({ ...saved, preset: "720" })}>
                <b>Small</b><span>720 × 1280 · for messages and review links</span></button>
            </div>
            <div className="pn-k">Include</div>
            <div className="ex-kinds">
              {KINDS.map(({ k, label, note }) => (
                <label key={k} className={`ex-kind ${inc.includes(k) ? "on" : ""}`}>
                  <input type="checkbox" checked={inc.includes(k)} onChange={() => toggle(k)} />
                  <span><b>{label}</b><span className="mute small">{note}</span></span>
                </label>
              ))}
            </div>
            <div className="pn-k">Pre-flight {checks && <span className={`ex-sum s-${worst}`}>{worst === "ok" ? "ready" : worst === "fail" ? "fix first" : worst === "info" ? "–" : "check these"}</span>}</div>
            <div className="ex-checks">
              {!checks && <span className="mute small"><span className="pulse" /> checking…</span>}
              {checks?.map((c) => (
                <div key={c.id} className={`ex-check s-${c.status}`}>
                  <span className="ex-dot" />
                  <span><b>{c.label}</b> <span className="mute">{c.detail}</span>
                    {c.id.startsWith("safe_") && c.status === "warn" && <button className="btn quiet small" onClick={onStyle}>Open Style</button>}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="modal-foot">
          {err ? <span className="err small">{err}</span>
            : done ? <span className="mute small">Downloaded {done[0].name} ({mb(done[0].bytes)}){done.length > 1 ? ` · ${done.length - 1} files inside` : ""}</span>
            : <span className="mute small">{files} file{files === 1 ? "" : "s"}{files > 1 ? " in a zip" : ""} · about {mb(est)}</span>}
          <span className="grow" />
          <button className="btn quiet" onClick={onClose}>Close</button>
          <button className="btn" disabled={!v || !inc.length || busy} onClick={go}>
            {busy ? <><span className="pulse" /> {inc.includes("clean") ? "Rendering the copy without captions…" : "Preparing…"}</> : <><Icon name="export" /> Download</>}
          </button>
        </div>
      </div>
    </div>
  );
}
