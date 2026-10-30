import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { Icon } from "./icons";
import type { MusicInfo, MusicSetting, Version } from "./types";

// The Music panel: a bundled CC0 bed (montivo/music, from OpenGameArt) under the speech, ducked while anyone talks (engine/music.py).
// The choice is staged here and "Make Version N" renders it ($0, ffmpeg only); the preview plays the loop alone at
// roughly the bed's level against speech at -14 LUFS.

export type MusicEdit = { track: string | null; level_db?: number; duck_db?: number };

let infoCache: Promise<MusicInfo> | null = null;
const getInfo = () => (infoCache ??= api<MusicInfo>("/api/music"));

const same = (a: MusicSetting | null, b: MusicSetting | null) =>
  (!a && !b) || (!!a && !!b && a.track === b.track && a.level_db === b.level_db && a.duck_db === b.duck_db);

export function MusicPanel({ v, next, canEdit, onApply }: {
  v: Version | null; next: number; canEdit: string | null; onApply: (e: MusicEdit) => void;
}) {
  const [info, setInfo] = useState<MusicInfo | null>(null);
  const [draft, setDraft] = useState<MusicSetting | null | undefined>(undefined); // undefined = nothing staged
  const [playing, setPlaying] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  useEffect(() => { getInfo().then(setInfo).catch(() => {}); }, []);
  useEffect(() => { setDraft(undefined); }, [v?.n]);
  useEffect(() => () => audio.current?.pause(), []);
  const cur: MusicSetting | null = v?.music ? { track: v.music.track, level_db: v.music.level_db, duck_db: v.music.duck_db } : null;
  const want = draft === undefined ? cur : draft;
  const staged = draft !== undefined && !same(draft, cur);
  const set = (m: MusicSetting | null) => setDraft(same(m, cur) ? undefined : m);
  const level = want?.level_db ?? info?.default.level_db ?? -18;
  const duck = want?.duck_db ?? info?.default.duck_db ?? -6;
  const vol = (db: number) => Math.max(0.02, Math.min(1, 10 ** ((db + 14) / 20))); // the bed against speech at -14 LUFS
  useEffect(() => { if (audio.current) audio.current.volume = vol(level); }, [level]);
  const preview = (id: string, url: string) => {
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
  return (
    <section className="side-panel music-panel">
      <div className="panel-head"><span className="ph-title">Music</span><span className="ph-sub">a bed under the speech</span></div>
      <div className="panel-body">
        <p className="mute small">
          {v.music ? <>v{v.n}: <b>{name(v.music.track)}</b> at {v.music.level_db} LUFS, {-v.music.duck_db} dB lower while anyone speaks
            {v.music.margin_db != null && <> · measured: speech {v.music.margin_db} dB over it</>}.</>
            : `No music in v${v.n}.`}
        </p>
        <div className="pn-k">Track</div>
        <div className="list">
          <div role="button" tabIndex={0} className={`list-row mu-row ${!want ? "on" : ""}`} onClick={() => set(null)}
            onKeyDown={(e) => { if (e.key === "Enter") set(null); }}>
            <span className="mu-play" />
            <span className="lr-txt"><b>No music</b></span>
            <span className="lr-end" />
          </div>
          {info.tracks.map((t) => (
            <div key={t.id} role="button" tabIndex={0} className={`list-row mu-row ${want?.track === t.id ? "on" : ""}`}
              onClick={() => set({ track: t.id, level_db: level, duck_db: duck })}
              onKeyDown={(e) => { if (e.key === "Enter") set({ track: t.id, level_db: level, duck_db: duck }); }}>
              <button className="icon-btn mu-play" title={playing === t.id ? "Stop the preview" : "Preview the loop at this level"}
                onClick={(e) => { e.stopPropagation(); preview(t.id, t.url); }}>
                <Icon name={playing === t.id ? "pause" : "play"} size={13} /></button>
              <span className="lr-txt"><b>{t.name}</b><span className="mu-mood">{t.mood} · by {t.author}</span></span>
              <span className="lr-end"><a className="lr-flag" href={t.source} target="_blank" rel="noreferrer"
                onClick={(e) => e.stopPropagation()} title="The track's page on OpenGameArt">{t.licence}</a></span>
            </div>
          ))}
        </div>
        <label className="field">Level where nobody speaks: {level} LUFS
          <input type="range" min={info.level_range[0]} max={info.level_range[1]} step={1} value={level} disabled={!want}
            onChange={(e) => want && set({ ...want, level_db: Number(e.target.value) })} />
        </label>
        <label className="field">While anyone speaks: {duck} dB ({level + duck} LUFS, the voice sits at -14)
          <input type="range" min={info.duck_range[0]} max={info.duck_range[1]} step={1} value={duck} disabled={!want}
            onChange={(e) => want && set({ ...want, duck_db: Number(e.target.value) })} />
        </label>
        <p className="mute small">CC0 tracks from OpenGameArt (the licence tag opens each one's page). The bed fades in
          and out, ramps down 0.15 s before each word and back up after pauses of 0.8 s or more; the mix stays at -14 LUFS.</p>
      </div>
      {staged && (
        <div className="cap-apply">
          <span>{want ? `${name(want.track)}, ${want.level_db} LUFS` : "Music off"}</span>
          <span className="grow" />
          <button className="btn quiet" onClick={() => setDraft(undefined)}>Discard</button>
          <button className="btn" disabled={!!canEdit} title={canEdit ?? `Makes Version ${next} ($0, a re-render)`}
            onClick={() => { audio.current?.pause(); setPlaying(null); onApply(want ? { ...want } : { track: null }); }}>Make Version {next}</button>
        </div>
      )}
    </section>
  );
}
