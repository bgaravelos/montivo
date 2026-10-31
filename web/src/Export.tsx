import { useEffect, useMemo, useRef, useState } from "react";
import { api, tc } from "./api";
import { CoverMaker, type CoverRecord, type CoverState } from "./CoverMaker";
import { useModal } from "./Dialogs";
import { Icon, MarkLoader } from "./icons";
import type { Costs, Version } from "./types";

// The Export page: a cover and a file name on the left, the destination, what goes in the bundle and the pre-flight on
// the right (as CapCut and Descript split it). Nothing here re-encodes for its own sake: the platforms re-encode, so the
// only presets are the cut as rendered and a small 720p copy. Feed 4:5 and Square 1:1 render the version again in that
// frame (the crop narrows and follows the head, the captions are laid out again); the cover maker lays its still out in
// that frame too, and their pre-flight says the app safe areas are a 9:16 matter. The files come from
// POST /export/bundle (python -m montivo.export, $0); more than one file downloads as a zip. The post text (UX B6) is one
// Muse Spark call per version (POST /export/post, about $0.01): a title, description and hashtags per platform, copied
// from here or put in the bundle as a .txt.

type Check = { id: string; label: string; status: "ok" | "warn" | "fail" | "info"; detail: string };
type Kind = "video" | "clean" | "srt" | "vtt" | "cover" | "audio" | "transcript" | "post";
type PostOne = { title: string; description: string; hashtags: string[]; text: string };
type PostRecord = { version: number; platforms: Record<string, PostOne> | null; cost_usd?: number; created?: string };
const PLATFORMS: { id: string; name: string; limit: number }[] = [
  { id: "reels", name: "Reels", limit: 2200 }, { id: "tiktok", name: "TikTok", limit: 4000 },
  { id: "shorts", name: "Shorts", limit: 5000 }, { id: "linkedin", name: "LinkedIn", limit: 3000 },
];
type Aspect = "9:16" | "4:5" | "1:1";
type Saved = { include: Kind[]; preset: "full" | "720"; grid: boolean; aspect?: Aspect };

const SIZE: Record<Aspect, [number, number]> = { "9:16": [1080, 1920], "4:5": [1080, 1350], "1:1": [1080, 1080] };
// the Instagram profile grid's 3:4 window in the middle of a cover: bands top and bottom on 9:16, at the sides on a feed frame
function gridBands(a: Aspect): { side: boolean; pct: number } {
  const [w, h] = SIZE[a];
  return w * 4 <= h * 3 ? { side: false, pct: (1 - (w * 4) / 3 / h) / 2 * 100 } : { side: true, pct: (1 - (h * 3) / 4 / w) / 2 * 100 };
}

const KINDS: { k: Kind; label: string; note: string }[] = [
  { k: "video", label: "Video", note: "MP4 with the captions burned in" },
  { k: "clean", label: "Video without captions", note: "MP4 for the app's own captions · renders again, about 30 s" },
  { k: "srt", label: "Captions file", note: "SRT, the lines as burned in" },
  { k: "vtt", label: "Web captions", note: "WebVTT" },
  { k: "cover", label: "Cover", note: "JPG of the cover and the 3:4 profile-grid crop" },
  { k: "audio", label: "Audio", note: "WAV, loudness as in the cut" },
  { k: "transcript", label: "Transcript", note: "TXT with times" },
  { k: "post", label: "Post text", note: "TXT with the title, description and hashtags per platform" },
];

/** The publish pack: one Spark call writes a post per platform from the brief, the words the cut keeps and the review. */
function PostText({ id, n, post, setPost, hasKey, keyNote, onErr, onMoved }: {
  id: string; n: number; post: PostRecord | null; setPost: (p: PostRecord) => void; hasKey: boolean; keyNote: string | null;
  onErr: (e: string | null) => void; onMoved?: (run: string) => void;
}) {
  const [tab, setTab] = useState("reels");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const write = async () => {
    setBusy(true); onErr(null);
    try {
      const r = await api<PostRecord & { run?: string }>(`/api/runs/${id}/export/post`, { v: n });
      setPost(r);
      if (r.run && r.run !== id) onMoved?.(r.run); // on a sample it went into the judge's own copy
    } catch (e) { onErr(String((e as Error).message)); } finally { setBusy(false); }
  };
  const one = post?.platforms?.[tab];
  const copy = async () => {
    if (!one) return;
    try { await navigator.clipboard.writeText(one.text); setCopied(tab); window.setTimeout(() => setCopied(null), 2000); } catch { onErr("The browser did not allow copying: select the text and copy it."); }
  };
  const lim = PLATFORMS.find((x) => x.id === tab)!.limit;
  return (
    <div className="ex-post">
      {post?.platforms ? (
        <>
          <div className="seg-group ex-post-tabs">{PLATFORMS.map((x) => <button key={x.id} className={`seg ${tab === x.id ? "on" : ""}`} onClick={() => setTab(x.id)}>{x.name}</button>)}</div>
          {one && <pre className="ex-post-text">{one.text}</pre>}
          <div className="ex-post-row">
            <span className="mute small">{one ? `${one.text.length} / ${lim} characters` : ""}{one?.title ? ` · title ${one.title.length} / 100` : ""}</span>
            <span className="grow" />
            <button className="btn quiet small" disabled={!hasKey || busy} onClick={write} title={hasKey ? "Write all four again (one Muse Spark call, about $0.01)" : keyNote ?? "Needs MODEL_API_KEY"}>{busy ? <><MarkLoader size={14} /> Writing…</> : "Write again"}</button>
            <button className="btn small" disabled={!one} onClick={copy}><Icon name={copied === tab ? "check" : "link"} size={12} />{copied === tab ? "Copied" : "Copy"}</button>
          </div>
        </>
      ) : (
        <div className="ex-post-row">
          <span className="mute small">A title, a description and hashtags for Reels, TikTok, Shorts and LinkedIn, from the brief, the words the cut keeps and the blind review.</span>
          <button className="btn small" disabled={!hasKey || busy} onClick={write} title={hasKey ? "One Muse Spark call, about $0.01" : keyNote ?? "Needs MODEL_API_KEY"}>
            {busy ? <><MarkLoader size={14} /> Writing…</> : <><Icon name="spark" size={12} />Write with Muse Spark · ~$0.01</>}</button>
        </div>
      )}
    </div>
  );
}
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

export function ExportPage({ id, versions, initial, onClose, onStyle, hasKey, keyNote, costs, onMoved }: {
  id: string; versions: Version[]; initial: number | null; onClose: () => void; onStyle: () => void;
  hasKey: boolean; keyNote: string | null; costs: Costs | undefined;
  /** A paid call on a hosted sample went into the judge's own copy: open the copy's Export page. */
  onMoved?: (run: string) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  useModal(box, onClose); // Esc closes it from anywhere, focus moves in and stays in, and goes back to Export after
  const cuts = versions.filter((x) => x.cut);
  const [n, setN] = useState(initial ?? cuts.at(-1)?.n ?? 1);
  const v = cuts.find((x) => x.n === n) ?? null;
  const total = v?.duration_s ?? v?.runtime_s ?? 0;
  const [saved, setSaved] = useState<Saved>(load);
  const firstT = () => Math.round(Math.max(v?.title_s ?? 0, total / 3) * 10) / 10;
  const [cover, setCover] = useState<CoverState>(() => ({ t: firstT(), template: "headline", text: "", highlight: "" }));
  const [record, setRecord] = useState<CoverRecord | null>(null);
  const [name, setName] = useState(`montivo-${id}-v${n}`);
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<{ name: string; url: string; bytes: number }[] | null>(null);
  const [post, setPost] = useState<PostRecord | null>(null);
  useEffect(() => { setPost(null); api<PostRecord>(`/api/runs/${id}/export/post?v=${n}`).then(setPost).catch(() => {}); }, [id, n]);
  useEffect(() => {
    setRecord(null);
    api<CoverRecord>(`/api/runs/${id}/export/cover?v=${n}`).then((r) => {
      setRecord(r);
      const f = r.design?.final;
      setCover(f ? { t: f.t_s, template: f.template, text: f.text, highlight: f.highlight } : (c) => ({ ...c, t: r.moments[0]?.t_s ?? c.t }));
    }).catch(() => {});
  }, [id, n]);
  useEffect(() => { try { localStorage.setItem("montivo.export", JSON.stringify(saved)); } catch { /* this tab only */ } }, [saved]);
  useEffect(() => { setName((x) => x.replace(/-v\d+$/, `-v${n}`)); setChecks(null); setDone(null); setErr(null);
    api<{ checks: Check[] }>(`/api/runs/${id}/export/check?v=${n}&aspect=${encodeURIComponent(saved.aspect ?? "9:16")}`).then((r) => setChecks(r.checks))
      .catch((e) => setChecks([{ id: "err", label: "Pre-flight", status: "info", detail: `could not be read (${(e as Error).message})` }])); }, [id, n, saved.aspect]);
  useEffect(() => setDone(null), [saved, cover, name, post]);
  const aspect: Aspect = saved.aspect ?? "9:16";
  const feed = aspect !== "9:16";
  const [W, H] = SIZE[aspect];
  const bands = gridBands(aspect);
  const inc = saved.include.filter((k) => k !== "post" || post?.platforms); // the post text once it is written
  const toggle = (k: Kind) => setSaved({ ...saved, include: saved.include.includes(k) ? saved.include.filter((x) => x !== k) : [...saved.include, k] });
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
        `/api/runs/${id}/export/bundle`, { v: v.n, include: inc, preset: saved.preset, name, aspect,
          ...(inc.includes("cover") ? { cover_t: cover.t, cover_design: { template: cover.template, text: cover.text, highlight: cover.highlight } } : {}) });
      const one = r.zip ?? r.files[0];
      download(one.url, one.name);
      setDone(r.zip ? [r.zip, ...r.files] : r.files);
    } catch (e) { setErr(String((e as Error).message)); } finally { setBusy(false); }
  };
  return (
    <div className="modal-bg" onPointerDown={onClose}>
      <div ref={box} tabIndex={-1} className="modal export-page" onPointerDown={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Export">
        <div className="modal-head"><span className="mh-title">Export</span><button className="icon-btn" onClick={onClose} aria-label="close"><Icon name="close" size={14} /></button></div>
        <div className="ex-grid">
          <div className="ex-left" data-monti="export.cover">
            <CoverMaker id={id} onMoved={onMoved} v={v} total={total} aspect={aspect} W={W} H={H} grid={saved.grid} bands={bands}
              cover={cover} setCover={setCover} record={record} setRecord={setRecord}
              hasKey={hasKey} keyNote={keyNote} costs={costs} onErr={setErr} />
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
            <div className="ex-dest" data-monti="export.dest">
              <button className={`ex-card ${!p720 && !feed ? "on" : ""}`} onClick={() => setSaved({ ...saved, preset: "full", aspect: "9:16" })}>
                <b>Reels · TikTok · Shorts</b><span>1080 × 1920 · 9:16 · as rendered</span></button>
              <button className={`ex-card ${aspect === "4:5" ? "on" : ""}`} onClick={() => setSaved({ ...saved, preset: "full", aspect: "4:5" })}>
                <b>Feed 4:5</b><span>1080 × 1350 · Instagram, Facebook, LinkedIn · renders again</span></button>
              <button className={`ex-card ${aspect === "1:1" ? "on" : ""}`} onClick={() => setSaved({ ...saved, preset: "full", aspect: "1:1" })}>
                <b>Square 1:1</b><span>1080 × 1080 · any feed · renders again</span></button>
              <button className={`ex-card ${p720 && !feed ? "on" : ""}`} onClick={() => setSaved({ ...saved, preset: "720", aspect: "9:16" })}>
                <b>Small</b><span>720 × 1280 · for messages and review links</span></button>
            </div>
            <div className="pn-k">Include</div>
            <div className="ex-kinds" data-monti="export.include">
              {KINDS.map(({ k, label, note }) => (
                <label key={k} className={`ex-kind ${inc.includes(k) ? "on" : ""} ${k === "post" && !post?.platforms ? "off" : ""}`}
                  title={k === "post" && !post?.platforms ? "Write the post text below first" : undefined}>
                  <input type="checkbox" checked={inc.includes(k)} disabled={k === "post" && !post?.platforms} onChange={() => toggle(k)} />
                  <span><b>{label}</b><span className="mute small">{k === "video" && feed ? `MP4 ${W} × ${H} with the captions laid out again · renders, about 30 s`
                    : k === "cover" ? `JPG ${W} × ${H} and the 3:4 profile-grid crop` : note}</span></span>
                </label>
              ))}
            </div>
            <div className="pn-k" data-monti="export.post">Post text</div>
            <PostText id={id} n={n} post={post} setPost={setPost} hasKey={hasKey} keyNote={keyNote} onErr={setErr} onMoved={onMoved} />
            <div className="pn-k">Pre-flight {checks && <span className={`ex-sum s-${worst}`}>{worst === "ok" ? "ready" : worst === "fail" ? "fix first" : worst === "info" ? "–" : "check these"}</span>}</div>
            <div className="ex-checks" data-monti="export.checks">
              {!checks && <span className="mute small"><MarkLoader /> checking…</span>}
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
            {busy ? <><MarkLoader size={14} /> {feed && (inc.includes("video") || inc.includes("clean")) ? `Rendering the ${aspect} version…`
              : inc.includes("clean") ? "Rendering the copy without captions…" : "Preparing…"}</> : <><Icon name="export" /> Download</>}
          </button>
        </div>
      </div>
    </div>
  );
}
