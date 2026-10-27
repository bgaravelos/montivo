import { fileUrl, tc } from "./api";
import { originKind, originLabel, parentOf } from "./edit";
import { Icon } from "./icons";
import type { RunState, Version } from "./types";

const mb = (b: number | null | undefined) => (b == null ? "–" : b > 1e9 ? `${(b / 1e9).toFixed(2)} GB` : `${(b / 1e6).toFixed(1)} MB`);

export function MediaPanel({ state }: { state: RunState }) {
  const m = state.media;
  const name = m.footage?.split("/").pop() ?? "source";
  return (
    <section className="side-panel">
      <div className="panel-head"><span className="ph-title">Media</span><span className="ph-sub">1 clip</span></div>
      <div className="panel-body">
        <div className="media-card">
          {m.proxy ? <video src={fileUrl(state.id, m.proxy)} controls muted preload="metadata" /> : <div className="media-ph">no preview</div>}
          <div className="mc-name" title={m.footage ?? ""}>{name}</div>
          <dl className="props">
            <dt>Duration</dt><dd className="mono">{tc(m.duration)}</dd>
            <dt>Frame</dt><dd>{m.width && m.height ? `${m.width} × ${m.height}` : "–"}{m.fps ? ` · ${Math.round(m.fps)} fps` : ""}</dd>
            <dt>Codec</dt><dd>{m.codec ?? "–"}</dd>
            <dt>Size</dt><dd>{mb(m.bytes)}</dd>
            {m.hflip && <><dt>Mirror</dt><dd>flipped horizontally</dd></>}
            <dt>Transcript</dt><dd>{m.words ? `${m.words} words · ${m.speakers} speaker${m.speakers === 1 ? "" : "s"}` : "–"}</dd>
          </dl>
        </div>
        <div className="panel-note">
          <div className="pn-k">Brief</div>
          <p>{state.run.brief}</p>
          <div className="pn-k">Target</div>
          <p>{state.run.target_s ?? 40} s, vertical 9:16</p>
        </div>
      </div>
    </section>
  );
}

export function CaptionsPanel({ v, t, onSeek }: { v: Version | null; t: number; onSeek: (a: number) => void }) {
  const lines = v?.caption_lines ?? [];
  const st = v?.caption_style;
  const font = st?.font_path?.split("/").pop()?.replace(/\.(ttf|otf)$/, "").replace(/-/g, " ");
  return (
    <section className="side-panel">
      <div className="panel-head"><span className="ph-title">Captions</span><span className="ph-sub">{lines.length} lines</span></div>
      <div className="panel-body">
        <div className="style-card">
          <div className="pn-k">Style</div>
          {st ? (
            <>
              <div className="cap-sample" style={{ fontSize: Math.min(26, (st.font ?? 84) / 3.4) }}>Captions look like this</div>
              <dl className="props">
                <dt>Font</dt><dd>{font ?? "–"} · {st.font ?? "–"} px</dd>
                <dt>Line</dt><dd>up to {st.max_words ?? 4} words</dd>
                <dt>Position</dt><dd>lower third (y {st.default_top}), moves off the face</dd>
              </dl>
              {v?.caption_style_note && <p className="mute small">Planner's note: {v.caption_style_note}</p>}
              <p className="mute small">Style presets are not editable yet: the renderer has one caption style.</p>
            </>
          ) : <p className="mute small">This version was rendered without captions.</p>}
        </div>
        <div className="list">
          {lines.map((c) => (
            <button key={c.id} className={`list-row ${t >= c.t_in && t < c.t_out ? "on" : ""}`} onClick={() => onSeek(c.t_in)}>
              <span className="lr-t mono">{tc(c.t_in)}</span>
              <span className="lr-txt">{c.text}</span>
              {c.over_face && <span className="lr-flag" title="moved off the face">moved</span>}
            </button>
          ))}
          {!lines.length && <p className="mute small pad">No caption lines in this version.</p>}
        </div>
      </div>
    </section>
  );
}

export function TextPanel({ state, v, t, onSeek }: { state: RunState; v: Version | null; t: number; onSeek: (a: number) => void }) {
  const calls = v?.callouts ?? [];
  return (
    <section className="side-panel">
      <div className="panel-head"><span className="ph-title">Text</span><span className="ph-sub">title and callouts</span></div>
      <div className="panel-body">
        <div className="pn-k">Title card</div>
        {v?.title_card_text ? (
          <button className={`title-card ${v.title_s > 0 ? "" : "off"}`} onClick={() => v.title_s > 0 && onSeek(0)}>
            {state.media.title_png && <img src={fileUrl(state.id, state.media.title_png)} alt="" />}
            <span className="tc-text">{v.title_card_text}</span>
            <span className="tc-meta">{v.title_s > 0 ? `0:00 – ${tc(v.title_s)} · Muse Image background` : "off in this version (the reviewer dropped it)"}</span>
          </button>
        ) : <p className="mute small">No title card.</p>}
        <div className="pn-k">Callouts</div>
        <div className="list">
          {calls.map((c, k) => (
            <button key={k} className={`list-row ${t >= c.t_in && t < c.t_out ? "on" : ""}`} onClick={() => onSeek(c.t_in)}>
              <span className="lr-t mono">{tc(c.t_in)}</span>
              <span className="lr-txt"><b>{c.text}</b></span>
              <span className="lr-flag">clip {c.span}</span>
            </button>
          ))}
          {!calls.length && <p className="mute small">No callouts in this version.</p>}
        </div>
      </div>
    </section>
  );
}

export function VersionsPanel({ id, state, versions, sel, onPick, go, replay }: {
  id: string; state: RunState; versions: Version[]; sel: number | null; onPick: (n: number) => void; go: (h: string) => void; replay: number;
}) {
  const v = versions.find((x) => x.n === sel);
  const parent = v ? parentOf(v, state.versions) : undefined;
  const pair = v && versions.length > 1 ? (parent && versions.some((x) => x.n === parent.n) ? [parent.n, v.n] : [v.n, versions.find((x) => x.n !== v.n)!.n]) : null;
  const best = state.loop?.best?.version;
  return (
    <section className="side-panel">
      <div className="panel-head"><span className="ph-title">Versions</span><span className="ph-sub">{versions.length}</span></div>
      <div className="panel-body">
        <div className="versions">
          {[...versions].reverse().map((x) => {
            const p = parentOf(x, state.versions);
            return (
              <button key={x.n} className={`ver ${x.n === sel ? "on" : ""} ${originKind(x)}`} onClick={() => onPick(x.n)}>
                <span className="ver-top">
                  <span className="ver-name">Version {x.n}</span>
                  {x.n === best && <span className="ver-best">best</span>}
                  <span className="grow" />
                  {x.review ? <span className={`ver-score ${x.review.pass ? "pass" : ""}`}>{x.review.pass && <Icon name="check" size={12} />}{x.review.overall}</span>
                    : <span className="ver-score none" title="not reviewed">–</span>}
                </span>
                <span className="ver-meta">
                  <span className={`origin ${originKind(x)}`}>{originLabel(x)}</span>
                  {p && <span className="mute">from Version {p.n}</span>}
                </span>
                <span className="ver-meta mute">{x.spans.length} clips · {tc(x.duration_s ?? x.runtime_s)}</span>
              </button>
            );
          })}
        </div>
        {pair && (
          <button className="btn quiet wide" onClick={() => go(`#/run/${id}/compare?a=${pair[0]}&b=${pair[1]}${replay ? `&replay=${replay}` : ""}`)}>
            <Icon name="compare" /> Compare Version {pair[0]} and {pair[1]}
          </button>
        )}
      </div>
    </section>
  );
}
