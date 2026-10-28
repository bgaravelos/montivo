import type { Progress } from "./types";

type Step = { title: string; what: string };

/** What each step of the first run does, in a line a viewer can read while it runs (titles from runs.progress). */
const STEPS: Record<string, Step> = {
  ingest: { title: "Getting your footage ready",
    what: "Montivo makes a light copy of the clip and its audio, and uploads it once for every model that follows." },
  voice: { title: "Listening and watching",
    what: "Muse Voice writes down every word with its timing, SAM 3.1 follows the speaker for the vertical crop, and Muse Spark watches the whole clip." },
  track: { title: "Matching voices to faces",
    what: "Montivo works out which face is talking, so the crop follows whoever speaks." },
  plan: { title: "Planning the edit",
    what: "Muse Spark reads the transcript against your brief and picks the lines to keep: a hook first, the strongest line last." },
  edges: { title: "Tightening the cuts",
    what: "Every cut moves to where the speech starts and stops, so no pause or breath is left at a join." },
  title: { title: "Making the title card",
    what: "Muse Image draws the opening card in the Montivo colours." },
  render: { title: "Rendering the first cut",
    what: "Montivo cuts the clips from the source, crops them to 9:16 around the speaker and burns in the captions." },
};
const START: Step = { title: "Starting the agent", what: "Montivo is getting the run ready. The first cut usually takes two to four minutes." };

function stepOf(p: Progress | null): Step {
  if (!p) return START;
  if (p.title === "Title card") return STEPS.title;
  return STEPS[p.stage ?? ""] ?? START;
}

/** The viewer while a new project has no cut yet: the logo sting on a loop (the in and out points close in, the V fills
 *  down, holds, and it opens again; assets/brand/src/sting.mjs), the step that runs and one line on what it does.
 *  Only while no version has a cut; edits on a project with a cut keep the player. */
export function Waiting({ progress }: { progress: Progress | null }) {
  const s = stepOf(progress);
  return (
    <div className="wait" role="status" aria-live="polite">
      <svg className="wait-mark" viewBox="0 0 64 64" width="56" height="56" aria-hidden>
        <defs><clipPath id="wait-v"><path d="M14 14H24L32 28L40 14H50L32 44Z" /></clipPath></defs>
        <path className="wm-l" fill="#F3F0E8" d="M6 6H24V14H14V50H24V58H6Z" />
        <path className="wm-r" fill="#F3F0E8" d="M58 6H40V14H50V50H40V58H58Z" />
        <g clipPath="url(#wait-v)"><rect className="wm-v" x="14" y="14" width="36" height="30" fill="#FFC629" /></g>
      </svg>
      <h2 key={s.title} className="shimmer">{s.title}</h2>
      <p key={s.what}>{s.what}</p>
    </div>
  );
}
