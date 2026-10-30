import { useEffect, useRef, useState } from "react";
import { api, tc } from "./api";
import { Icon } from "./icons";
import { LIVE_TIP } from "./Panels";
import type { MusicInfo, MusicSetting, Version } from "./types";

// The Music panel: a bundled CC0 bed (montivo/music, from OpenGameArt) under the speech, ducked while anyone talks (engine/music.py).
// The choice is staged here (the editor holds it: in Live it plays under the cut at once, M1) and "Make Version N"
// renders it ($0, ffmpeg only). The level has one home, the timeline's Music fader (M2): here three steps, the dB in
// Details. In Rendered mode the play button plays the loop alone at roughly the bed's level against speech at -14 LUFS.

export type MusicEdit = { track: string | null; level_db?: number; duck_db?: number; start_s?: number; fade_out_s?: number };
/** undefined = nothing staged, null = no music */
export type MusicDraft = MusicSetting | null | undefined;

let infoCache: Promise<MusicInfo> | null = null;
const getInfo = () => (infoCache ??= api<MusicInfo>("/api/music"));

const FADE_OUT = 1.5; // engine/music.py FADE_OUT_S
const same = (a: MusicSetting | null, b: MusicSetting | null) =>
  (!a && !b) || (!!a && !!b && a.track === b.track && a.level_db === b.level_db && a.duck_db === b.duck_db
    && (a.start_s ?? 0) === (b.start_s ?? 0) && (a.fade_out_s ?? FADE_OUT) === (b.fade_out_s ?? FADE_OUT));

/** M2: the bed in three steps (level where nobody speaks, the duck under speech); Balanced = the defaults, about 9 dB
 *  under the voice on a talking head (run 015). */
const STEPS: [string, string, number, number][] = [
  ["quiet", "Quiet", -22, -8], ["balanced", "Balanced", -18, -6], ["present", "Present", -15, -5]];

/** M3: the moods the tracks' descriptions fall into (montivo/music/tracks.json `mood`). */
const MOODS: [string, RegExp][] = [["Lo-fi", /lo-?fi|mellow/i], ["Funky", /funk|swing|jam/i], ["Upbeat", /upbeat|happy|drive|pop/i],
  ["Acoustic", /acoustic|ukulele|cozy/i]];

export function MusicPanel({ v, next, canEdit, onApply, rev, live, draft, setDraft, onListen }: {
  v: Version | null; next: number; canEdit: string | null; onApply: (e: MusicEdit) => void; rev: string; live: boolean;
  draft: MusicDraft; setDraft: (d: MusicDraft) => void;
  onListen: () => void; // Live: play the cut from the playhead with the staged bed (M1)
}) {
  const [info, setInfo] = useState<MusicInfo | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);
  const [mood, setMood] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  useEffect(() => { getInfo().then(setInfo).catch(() => {}); }, []);
  useEffect(() => { setDraft(undefined); }, [rev]); // eslint-disable-line
  useEffect(() => () => { audio.current?.pause(); setDraft(undefined); }, []); // eslint-disable-line
  const cur: MusicSetting | null = v?.music ? { track: v.music.track, level_db: v.music.level_db, duck_db: v.music.duck_db,
    ...(v.music.start_s ? { start_s: v.music.start_s } : {}), ...(v.music.fade_out_s != null ? { fade_out_s: v.music.fade_out_s } : {}) } : null;
  const want = draft === undefined ? cur : draft;
  const staged = draft !== undefined && !same(draft, cur);
  const set = (m: MusicSetting | null) => setDraft(same(m, cur) ? undefined : m);
  const level = want?.level_db ?? info?.default.level_db ?? -18;
  const duck = want?.duck_db ?? info?.default.duck_db ?? -6;
  const vol = (db: number) => Math.max(0.02, Math.min(1, 10 ** ((db + 14) / 20))); // the bed against speech at -14 LUFS
  useEffect(() => { if (audio.current) audio.current.volume = vol(level); }, [level]);
  const loopAlone = (id: string, url: string) => {
    if (playing === id) { audio.current?.pause(); setPlaying(null); return; }
    audio.current?.pause();
    const a = new Audio(url);
    a.loop = true;
    a.volume = vol(level);
    a.play().then(() => setPlaying(id)).catch(() => setPlaying(null));
    audio.current = a;
  };
  if (!v || !info) return <section className="side-panel"><div className="panel-head"><span className="ph-title">Music</span></div></section>;
  const name = (id: string) => info.tracks.find((t) => t.id === id)?.name ?? id;
  const pickTrack = (id: string) => set({ track: id, level_db: level, duck_db: duck, // another track starts at its top
    ...(want?.track === id && want.start_s ? { start_s: want.start_s } : {}), ...(want?.fade_out_s != null ? { fade_out_s: want.fade_out_s } : {}) });
  const step = STEPS.find(([, , l, d]) => l === level && d === duck)?.[0] ?? null;
  const cutS = v.runtime_s || v.duration_s || 0;
  const loop = want ? info.tracks.find((t) => t.id === want.track)?.loop_s ?? 0 : 0;
  const shown = info.tracks.filter((t) => !mood || MOODS.find(([m]) => m === mood)![1].test(t.mood));
  return (
    <section className="side-panel music-panel">
      <div className="panel-head"><span className="ph-title">Music</span><span className="ph-sub">a bed under the speech</span></div>
      <div className="panel-body">
        <p className="mute small">
          {v.music ? <>v{v.n}: <b>{name(v.music.track)}</b>{v.music.margin_db != null && <>, about {Math.round(v.music.margin_db)} dB under the voice</>}.</>
            : `No music in v${v.n}.`} {live ? "The play button plays the cut with that track from the playhead." : "The play button plays the loop alone; Live plays it under the cut."}
        </p>
        <div className="pn-k">Mood</div>
        <div className="chips mood-chips">
          <button className={`chip ${!mood ? "on" : ""}`} onClick={() => setMood(null)}>All</button>
          {MOODS.filter(([, re]) => info.tracks.some((t) => re.test(t.mood))).map(([m]) =>
            <button key={m} className={`chip ${mood === m ? "on" : ""}`} onClick={() => setMood(mood === m ? null : m)}>{m}</button>)}
        </div>
        <div className="pn-k">Track</div>
        <div className="list">
          <div role="button" tabIndex={0} className={`list-row mu-row ${!want ? "on" : ""}`} onClick={() => set(null)}
            onKeyDown={(e) => { if (e.key === "Enter") set(null); }}>
            <span className="mu-play" />
            <span className="lr-txt"><b>No music</b></span>
            <span className="lr-end" />
          </div>
          {shown.map((t) => (
            <div key={t.id} role="button" tabIndex={0} className={`list-row mu-row ${want?.track === t.id ? "on" : ""}`}
              onClick={() => pickTrack(t.id)} onKeyDown={(e) => { if (e.key === "Enter") pickTrack(t.id); }}>
              <button className="icon-btn mu-play" title={live ? "Play the cut with this track (from the playhead)" : playing === t.id ? "Stop the preview" : "Preview the loop at this level"}
                onClick={(e) => { e.stopPropagation(); if (live) { pickTrack(t.id); onListen(); } else loopAlone(t.id, t.url); }}>
                <Icon name={!live && playing === t.id ? "pause" : "play"} size={13} /></button>
              <span className="lr-txt"><b>{t.name}</b><span className="mu-mood">{t.mood} · by {t.author}</span>
                <span className="mu-mood" title="The loop repeats when the cut is longer">loop {tc(t.loop_s)} · cut {tc(cutS)}{t.loop_s < cutS ? ` · repeats ${Math.ceil(cutS / t.loop_s) - 1}×` : ""}</span></span>
              <span className="lr-end"><a className="lr-flag" href={t.source} target="_blank" rel="noreferrer"
                onClick={(e) => e.stopPropagation()} title="The track's page on OpenGameArt">{t.licence}</a></span>
            </div>
          ))}
        </div>
        <div className="pn-k">Level</div>
        <div className="seg-group" role="group" aria-label="How loud the music sits under the voice">
          {STEPS.map(([k, label, l, d]) => <button key={k} className={`seg ${step === k ? "on" : ""}`} disabled={!want}
            onClick={() => want && set({ ...want, level_db: l, duck_db: d })}>{label}</button>)}
        </div>
        <p className="mute small">{step ? "" : `Custom: ${level} LUFS, ${duck} dB under speech. `}Fine-tune it with the Music fader on the timeline: it plays at once.</p>
        {want && (
          <>
            <label className="field">Starts in the loop at <span className="mono mute">{tc(want.start_s ?? 0)}</span>
              <input type="range" min={0} max={Math.max(1, Math.floor(loop))} step={0.5} value={want.start_s ?? 0}
                onChange={(e) => set({ ...want, start_s: Number(e.target.value) || undefined })} />
            </label>
            <label className="field">Fade out at the end <span className="mono mute">{(want.fade_out_s ?? FADE_OUT).toFixed(1)} s</span>
              <input type="range" min={0} max={8} step={0.5} value={want.fade_out_s ?? FADE_OUT}
                onChange={(e) => { const f = Number(e.target.value); set({ ...want, fade_out_s: f === FADE_OUT ? undefined : f }); }} />
            </label>
          </>
        )}
        <details className="mu-details">
          <summary>Details: the dB</summary>
          <label className="field">Level where nobody speaks: {level} LUFS
            <input type="range" min={info.level_range[0]} max={info.level_range[1]} step={1} value={level} disabled={!want}
              onChange={(e) => want && set({ ...want, level_db: Number(e.target.value) })} />
          </label>
          <label className="field">While anyone speaks: {duck} dB ({level + duck} LUFS, the voice sits at -14)
            <input type="range" min={info.duck_range[0]} max={info.duck_range[1]} step={1} value={duck} disabled={!want}
              onChange={(e) => want && set({ ...want, duck_db: Number(e.target.value) })} />
          </label>
        </details>
        <p className="mute small">CC0 tracks from OpenGameArt (the licence tag opens each one's page). The bed fades in
          and out, ramps down 0.15 s before each word and back up after pauses of 0.8 s or more; drag across the Music lane
          to hold it down or lift it by hand. The mix stays at -14 LUFS.</p>
      </div>
      {staged && (
        <div className="cap-apply">
          <span>{want ? `${name(want.track)}${step ? `, ${STEPS.find(([k]) => k === step)![1].toLowerCase()}` : `, ${want.level_db} LUFS`}` : "Music off"}</span>
          <span className="grow" />
          <button className="btn quiet" onClick={() => setDraft(undefined)}>Discard</button>
          <button className="btn" disabled={!!canEdit} title={canEdit ?? (live ? LIVE_TIP : `Makes Version ${next} ($0, a re-render)`)}
            onClick={() => { audio.current?.pause(); setPlaying(null); onApply(want ? { ...want, start_s: want.start_s ?? 0, fade_out_s: want.fade_out_s ?? FADE_OUT } : { track: null }); }}>{live ? "Apply" : `Make Version ${next}`}</button>
        </div>
      )}
    </section>
  );
}
