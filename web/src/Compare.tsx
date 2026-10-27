import { useEffect, useRef, useState } from "react";
import { crit, fileUrl, tc, useRun } from "./api";
import { chain, sevRank } from "./edit";
import { Player, type PlayerHandle } from "./Player";

/** Two versions side by side, one transport, parked on a moment the reviewer flagged in the first. */
export function Compare({ id, a, b, replay, go }: { id: string; a: number; b: number; replay: number; go: (h: string) => void }) {
  const { state } = useRun(id, 0);
  const pa = useRef<PlayerHandle>(null);
  const pb = useRef<PlayerHandle>(null);
  const [k, setK] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const va = state?.versions.find((v) => v.n === a);
  const vb = state?.versions.find((v) => v.n === b);
  const issues = [...(va?.review?.issues ?? [])].sort((x, y) => sevRank(x.severity) - sevRank(y.severity) || x.t_s - y.t_s);
  const issue = issues.find((i) => i.k === k) ?? null;
  useEffect(() => { if (issues.length && k == null) setK(issues[0].k); }, [issues.length]); // eslint-disable-line
  useEffect(() => { // park both players at the flagged moment
    if (!issue) return;
    const at = Math.max(0, issue.t_s - 0.5);
    const t = window.setTimeout(() => { pa.current?.seek(at); pb.current?.seek(at); }, 300);
    return () => window.clearTimeout(t);
  }, [issue?.k, va?.cut, vb?.cut]); // eslint-disable-line

  if (!state) return <div className="empty">Opening run {id}…</div>;
  if (!va?.cut || !vb?.cut) return <div className="empty">v{a} or v{b} has no cut. <a href={`#/run/${id}`}>back</a></div>;

  const both = () => {
    const ea = pa.current?.el(), eb = pb.current?.el();
    if (!ea || !eb) return;
    if (!ea.paused || !eb.paused) { ea.pause(); eb.pause(); setPlaying(false); return; }
    eb.currentTime = ea.currentTime;
    ea.play().catch(() => {}); eb.play().catch(() => {});
    setPlaying(true);
  };
  const c = issue && va ? chain(issue, va, state.versions) : null;
  const opts = state.versions.filter((v) => v.cut);
  const pick = (na: number, nb: number) => go(`#/run/${id}/compare?a=${na}&b=${nb}${replay ? `&replay=${replay}` : ""}`);

  return (
    <div className="compare">
      <header className="top">
        <a className="mark" href="#/">montivo</a>
        <span className="run-id">run {id} · compare</span>
        <span className="brief" title={state.run.brief}>{state.run.brief}</span>
        <span className="grow" />
        <select value={a} onChange={(e) => pick(Number(e.target.value), b)}>{opts.map((v) => <option key={v.n} value={v.n}>v{v.n}</option>)}</select>
        <span className="mute">|</span>
        <select value={b} onChange={(e) => pick(a, Number(e.target.value))}>{opts.map((v) => <option key={v.n} value={v.n}>v{v.n}</option>)}</select>
        <button className="btn quiet" onClick={() => go(`#/run/${id}${replay ? `?replay=${replay}` : ""}`)}>Review room</button>
      </header>
      <div className="cmp-body">
        <div className="cmp-side">
          <div className="note-card">
            <div className="nc-v">v{a} <b>{va.review?.overall ?? "·"}</b></div>
            {issue ? <>
              <div className="nc-line"><span className={`sev ${issue.severity}`}>{issue.severity}</span> {tc(issue.t_s)} · {crit(issue.criterion)}</div>
              <div className="nc-what">“{issue.what}”</div>
            </> : <div className="nc-what mute">no issue flagged in v{a}</div>}
          </div>
          <Player ref={pa} src={fileUrl(id, va.cut)} compact label={`v${a}`} />
        </div>
        <div className="cmp-mid">
          <button className="btn big" onClick={both}>{playing ? "❚❚ pause both" : "▶ play both"}</button>
          <div className="cmp-issues">
            {issues.map((i) => (
              <button key={i.k} className={`cmp-issue ${i.k === k ? "on" : ""}`} onClick={() => setK(i.k)}>
                <span className={`sev ${i.severity}`}>{i.severity}</span> {tc(i.t_s)} {crit(i.criterion)}
              </button>
            ))}
          </div>
        </div>
        <div className="cmp-side">
          <div className="note-card after">
            <div className="nc-v">v{b} <b>{vb.review?.overall ?? "·"}</b>{vb.review?.pass && <span className="tick"> ✓ pass</span>}</div>
            {c?.applied ? <>
              <div className="nc-line"><code>{c.applied.op} {c.applied.target}</code></div>
              <div className="nc-what">{c.applied.note}</div>
              {c.after != null && <div className="nc-line">{crit(issue!.criterion)} {c.before} → <b>{c.after}</b></div>}
            </> : vb.origin.source === "human" ? <div className="nc-what">human: {vb.origin.action === "drop" ? `drop ${vb.origin.fixes?.[0]?.target}` : vb.origin.instruction}</div>
              : <div className="nc-what mute">{c?.skipped ? `skipped: ${c.skipped.reason}` : "same moment, the other version"}</div>}
          </div>
          <Player ref={pb} src={fileUrl(id, vb.cut)} compact label={`v${b}`} />
        </div>
      </div>
    </div>
  );
}
