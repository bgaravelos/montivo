// A small stroke icon set (24x24 grid, one stroke width, round caps), so the UI needs no icon library
const P: Record<string, string> = {
  play: "M8 5.5v13a1 1 0 001.5.86l10.4-6.5a1 1 0 000-1.72L9.5 4.64A1 1 0 008 5.5z",
  pause: "M7.7 5h1.6a1.2 1.2 0 011.2 1.2v11.6a1.2 1.2 0 01-1.2 1.2H7.7a1.2 1.2 0 01-1.2-1.2V6.2A1.2 1.2 0 017.7 5zM14.7 5h1.6a1.2 1.2 0 011.2 1.2v11.6a1.2 1.2 0 01-1.2 1.2h-1.6a1.2 1.2 0 01-1.2-1.2V6.2A1.2 1.2 0 0114.7 5z",
  stop: "M7 5h10a2 2 0 012 2v10a2 2 0 01-2 2H7a2 2 0 01-2-2V7a2 2 0 012-2z",
  skipBack: "M6 5v14M19 6l-9 6 9 6z",
  skipFwd: "M18 5v14M5 6l9 6-9 6z",
  frameBack: "M14.5 7l-5 5 5 5",
  frameFwd: "M9.5 7l5 5-5 5",
  repeat: "M17 2.5l3.5 3.5L17 9.5M3.5 11v-1a4 4 0 014-4h13M7 21.5L3.5 18 7 14.5M20.5 13v1a4 4 0 01-4 4h-13",
  volume: "M11 5L6 9H3v6h3l5 4zM15.5 8.5a5 5 0 010 7M18.5 5.5a9 9 0 010 13",
  mute: "M11 5L6 9H3v6h3l5 4zM16 9.5l5 5M21 9.5l-5 5",
  safe: "M7 2.5h10a2 2 0 012 2v15a2 2 0 01-2 2H7a2 2 0 01-2-2v-15a2 2 0 012-2zM8.5 6.5h7M8.5 17.5h4",
  back: "M15 5l-7 7 7 7",
  undo: "M9 14L4 9l5-5M4 9h10.5a5.5 5.5 0 010 11H11",
  redo: "M15 14l5-5-5-5M20 9H9.5a5.5 5.5 0 000 11H13",
  assistant: "M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8zM18.5 15.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z",
  media: "M4 5h16v14H4zM4 9h16M4 15h16M8 5v4M12 5v4M16 5v4M8 15v4M12 15v4M16 15v4",
  captions: "M5.5 5.5h13a2.5 2.5 0 012.5 2.5v8a2.5 2.5 0 01-2.5 2.5h-13A2.5 2.5 0 013 16V8a2.5 2.5 0 012.5-2.5zM10.5 10.2a2.2 2.2 0 100 3.6M17 10.2a2.2 2.2 0 100 3.6",
  text: "M5 7V5h14v2M12 5v14M9 19h6",
  transcript: "M4 6h16M4 10h16M4 14h11M4 18h7",
  film: "M5 4h14a2 2 0 012 2v12a2 2 0 01-2 2H5a2 2 0 01-2-2V6a2 2 0 012-2zM7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4",
  wave: "M3 12h1.5M7 8.5v7M11 5v14M15 9v6M19 7v10",
  music: "M9 17.5V6l10.5-2v11.5M9 17.5a2.5 2.5 0 11-5 0 2.5 2.5 0 015 0zM19.5 15.5a2.5 2.5 0 11-5 0 2.5 2.5 0 015 0zM9 9.5l10.5-2",
  style: "M12 3.5a8.5 8.5 0 100 17c1.2 0 1.8-.9 1.4-1.9-.5-1.2.3-2.3 1.6-2.3H17a3.5 3.5 0 003.5-3.5c0-5-3.8-9.3-8.5-9.3zM7.5 12.5a1 1 0 110-2 1 1 0 010 2zM10 8.5a1 1 0 110-2 1 1 0 010 2zM14.5 8.5a1 1 0 110-2 1 1 0 010 2z",
  versions: "M12 3l9 5-9 5-9-5zM3 12.5l9 5 9-5M3 16.5l9 5 9-5",
  export: "M12 4v11M7.5 10.5L12 15l4.5-4.5M5 19.5h14",
  more: "M5 10.5a1.5 1.5 0 110 3 1.5 1.5 0 010-3zM12 10.5a1.5 1.5 0 110 3 1.5 1.5 0 010-3zM19 10.5a1.5 1.5 0 110 3 1.5 1.5 0 010-3z",
  check: "M5 12.5l4.5 4.5L19 7.5",
  trash: "M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13M10 11v6M14 11v6",
  close: "M6 6l12 12M18 6L6 18",
  pencil: "M15.5 4.5l4 4L8 20H4v-4zM13 7l4 4",
  chevron: "M9 6l6 6-6 6",
  chevronDown: "M6 9l6 6 6-6",
  chevronUp: "M6 15l6-6 6 6",
  zoomIn: "M10.5 4.5a6 6 0 110 12 6 6 0 010-12zM15 15l5 5M8 10.5h5M10.5 8v5",
  zoomOut: "M10.5 4.5a6 6 0 110 12 6 6 0 010-12zM15 15l5 5M8 10.5h5",
  fit: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
  compare: "M4.5 5h5a1.5 1.5 0 011.5 1.5v11A1.5 1.5 0 019.5 19h-5A1.5 1.5 0 013 17.5v-11A1.5 1.5 0 014.5 5zM14.5 5h5A1.5 1.5 0 0121 6.5v11a1.5 1.5 0 01-1.5 1.5h-5a1.5 1.5 0 01-1.5-1.5v-11A1.5 1.5 0 0114.5 5z",
  scissors: "M6 9a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM6 20a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM8 8l12 10M8 16L20 6",
  spark: "M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z",
  dot: "M12 9a3 3 0 110 6 3 3 0 010-6z",
  loop: "M4.5 12a7.5 7.5 0 0113-5.1M19.5 12a7.5 7.5 0 01-13 5.1M17.5 3.5v3.4h-3.4M6.5 20.5v-3.4h3.4",
  archive: "M3.5 4.5h17v4h-17zM5 8.5v11h14v-11M10 12.5h4",
  speakers: "M9 11a3.5 3.5 0 100-7 3.5 3.5 0 000 7zM2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6M16 4.3a3.5 3.5 0 010 6.4M18.5 14.4c1.8.8 3 2.8 3 5.6",
  search: "M10.5 4a6.5 6.5 0 110 13 6.5 6.5 0 010-13zM15.5 15.5L20 20",
  send: "M12 19V5M5.5 11.5L12 5l6.5 6.5",
  plus: "M12 5v14M5 12h14",
  clock: "M12 3.5a8.5 8.5 0 110 17 8.5 8.5 0 010-17zM12 7.5V12l3 2",
  eyeOff: "M3 3l18 18M10.6 5.6A9.9 9.9 0 0112 5.5c6 0 9.5 6.5 9.5 6.5a17 17 0 01-3 3.7M6.6 6.6A17 17 0 002.5 12S6 18.5 12 18.5c1.8 0 3.3-.5 4.6-1.3M9.9 9.9a3 3 0 004.2 4.2",
  brief: "M7 3.5h7l4 4v13H7zM14 3.5v4h4M9.5 12h6M9.5 15.5h6",
  anchor: "M12 2.5a2.5 2.5 0 110 5 2.5 2.5 0 010-5zM12 7.5V21M5 13a7 7 0 0014 0M8.5 11h7",
  flag: "M5 21V4M5 4h11l-2 4 2 4H5",
  zap: "M13 2.5L4.5 14H11l-1 7.5L18.5 10H12z",
  gauge: "M4 17a8 8 0 1116 0M12 13l4-4M12 12a1 1 0 110 2 1 1 0 010-2z",
  person: "M12 4.5a3.5 3.5 0 110 7 3.5 3.5 0 010-7zM5 20c0-3.6 3.1-6 7-6s7 2.4 7 6",
  hook: "M15 3v9a5 5 0 01-10 0v-2M5 10l-2 2M5 10l2 2",
  punch: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5M10 9h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1v-4a1 1 0 011-1z",
  target: "M12 3.5a8.5 8.5 0 110 17 8.5 8.5 0 010-17zM12 7.5a4.5 4.5 0 110 9 4.5 4.5 0 010-9zM12 11a1 1 0 110 2 1 1 0 010-2z",
  move: "M5 9l-3 3 3 3M19 9l3 3-3 3M2 12h20",
  callout: "M5 4.5h14a2 2 0 012 2v8a2 2 0 01-2 2h-7l-4.5 3.5v-3.5H5a2 2 0 01-2-2v-8a2 2 0 012-2z",
  info: "M12 3.5a8.5 8.5 0 110 17 8.5 8.5 0 010-17zM12 11v5.5M12 7.8v.2",
};
const FILLED = new Set(["play", "pause", "stop", "dot", "more"]);

export function Icon({ name, size = 16, className }: { name: keyof typeof P | string; size?: number; className?: string }) {
  const filled = FILLED.has(name);
  return (
    <svg className={`ico ${className ?? ""}`} width={size} height={size} viewBox="0 0 24 24" aria-hidden
      fill={filled ? "currentColor" : "none"} stroke="currentColor" strokeWidth={filled ? 0 : 1.75} strokeLinecap="round" strokeLinejoin="round">
      <path d={P[name] ?? P.dot} />
    </svg>
  );
}

/** One icon per reviewer criterion (api.ts CRITERIA), for the score bars. */
export const CRIT_ICON: Record<string, string> = {
  hook_in_1_5s: "zap", brief_covered: "brief", pacing: "gauge", no_cut_mid_word: "scissors", captions_match_speech: "transcript",
  captions_legible: "captions", subject_framed: "person", callouts_anchored: "anchor", ending: "flag",
};
