// A small stroke icon set (24x24 grid), so the UI needs no icon library
const P: Record<string, string> = {
  play: "M7 4.5v15l12-7.5z",
  pause: "M7 5h3.5v14H7zM13.5 5H17v14h-3.5z",
  skipBack: "M6 5v14M18 5.5v13L9 12z",
  skipFwd: "M18 5v14M6 5.5v13l9-6.5z",
  frameBack: "M15 6l-6 6 6 6",
  frameFwd: "M9 6l6 6-6 6",
  back: "M15 5l-7 7 7 7",
  undo: "M9 14L4 9l5-5M4 9h10.5a5.5 5.5 0 010 11H11",
  redo: "M15 14l5-5-5-5M20 9H9.5a5.5 5.5 0 000 11H13",
  assistant: "M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8zM18.5 15.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z",
  media: "M4 5h16v14H4zM4 9h16M4 15h16M8 5v4M12 5v4M16 5v4M8 15v4M12 15v4M16 15v4",
  captions: "M3.5 6h17v12h-17zM10.5 10.2a2.2 2.2 0 100 3.6M17 10.2a2.2 2.2 0 100 3.6",
  text: "M5 6V4.5h14V6M12 4.5v15M9 19.5h6",
  versions: "M12 3l9 5-9 5-9-5zM3 12.5l9 5 9-5M3 16.5l9 5 9-5",
  export: "M12 4v11M7.5 10.5L12 15l4.5-4.5M5 19.5h14",
  more: "M5 10.5a1.5 1.5 0 110 3 1.5 1.5 0 010-3zM12 10.5a1.5 1.5 0 110 3 1.5 1.5 0 010-3zM19 10.5a1.5 1.5 0 110 3 1.5 1.5 0 010-3z",
  check: "M5 12.5l4.5 4.5L19 7.5",
  trash: "M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13M10 11v6M14 11v6",
  close: "M6 6l12 12M18 6L6 18",
  pencil: "M15.5 4.5l4 4L8 20H4v-4zM13 7l4 4",
  chevron: "M9 6l6 6-6 6",
  chevronDown: "M6 9l6 6 6-6",
  zoomIn: "M10.5 4.5a6 6 0 110 12 6 6 0 010-12zM15 15l5 5M8 10.5h5M10.5 8v5",
  zoomOut: "M10.5 4.5a6 6 0 110 12 6 6 0 010-12zM15 15l5 5M8 10.5h5",
  fit: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
  compare: "M4 5h7v14H4zM13 5h7v14h-7z",
  scissors: "M6 9a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM6 20a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM8 8l12 10M8 16L20 6",
  spark: "M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z",
  dot: "M12 9a3 3 0 110 6 3 3 0 010-6z",
  loop: "M4.5 12a7.5 7.5 0 0113-5.1M19.5 12a7.5 7.5 0 01-13 5.1M17.5 3.5v3.4h-3.4M6.5 20.5v-3.4h3.4",
  archive: "M3.5 4.5h17v4h-17zM5 8.5v11h14v-11M10 12.5h4",
  speakers: "M9 11a3.5 3.5 0 100-7 3.5 3.5 0 000 7zM2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6M16 4.3a3.5 3.5 0 010 6.4M18.5 14.4c1.8.8 3 2.8 3 5.6",
  search: "M10.5 4a6.5 6.5 0 110 13 6.5 6.5 0 010-13zM15.5 15.5L20 20",
};
const FILLED = new Set(["play", "pause", "skipBack", "skipFwd", "dot", "more"]);

export function Icon({ name, size = 16, className }: { name: keyof typeof P | string; size?: number; className?: string }) {
  const filled = FILLED.has(name);
  return (
    <svg className={`ico ${className ?? ""}`} width={size} height={size} viewBox="0 0 24 24" aria-hidden
      fill={filled ? "currentColor" : "none"} stroke="currentColor" strokeWidth={filled ? 0 : 1.7} strokeLinecap="round" strokeLinejoin="round">
      <path d={P[name] ?? P.dot} />
      {(name === "skipBack" || name === "skipFwd") && <path d={name === "skipBack" ? "M6 5v14" : "M18 5v14"} stroke="currentColor" strokeWidth={2.2} />}
    </svg>
  );
}
