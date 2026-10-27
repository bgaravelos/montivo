// Safe-area guides on the 9:16 viewer: the margins each app covers with its own UI (header, caption and buttons).
// Approximate published guidance for a 1080 x 1920 frame, as fractions of the frame:
// Reels: keep text out of the top 14 %, the bottom 35 % and 6 % at each side (Meta's Reels ad guidance);
// TikTok: 150 px top, 440 px bottom, 60 px left, 140 px right (the icon column);
// Shorts: 160 px top, 480 px bottom, 60 px left, 140 px right.
export type Guide = "off" | "reels" | "tiktok" | "shorts";
export const GUIDES: Guide[] = ["off", "reels", "tiktok", "shorts"];
export const GUIDE_NAME: Record<Guide, string> = { off: "Off", reels: "Reels", tiktok: "TikTok", shorts: "Shorts" };

const ZONES: Record<Exclude<Guide, "off">, { top: number; bottom: number; left: number; right: number }> = {
  reels: { top: 0.14, bottom: 0.35, left: 0.06, right: 0.06 },
  tiktok: { top: 150 / 1920, bottom: 440 / 1920, left: 60 / 1080, right: 140 / 1080 },
  shorts: { top: 160 / 1920, bottom: 480 / 1920, left: 60 / 1080, right: 140 / 1080 },
};

const pct = (x: number) => `${(x * 100).toFixed(2)}%`;

/** The shaded margins and the dashed safe box, drawn over the video (no pointer events). */
export function SafeArea({ kind }: { kind: Guide }) {
  if (kind === "off") return null;
  const z = ZONES[kind];
  return (
    <div className="safe-area" aria-hidden>
      <i style={{ left: 0, right: 0, top: 0, height: pct(z.top) }} />
      <i style={{ left: 0, right: 0, bottom: 0, height: pct(z.bottom) }} />
      <i style={{ left: 0, width: pct(z.left), top: pct(z.top), bottom: pct(z.bottom) }} />
      <i style={{ right: 0, width: pct(z.right), top: pct(z.top), bottom: pct(z.bottom) }} />
      <b style={{ left: pct(z.left), right: pct(z.right), top: pct(z.top), bottom: pct(z.bottom) }}>
        <span>{GUIDE_NAME[kind]} safe area · approx.</span>
      </b>
    </div>
  );
}
