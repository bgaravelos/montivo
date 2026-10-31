import { Fragment, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type MouseEvent as RMouseEvent, type PointerEvent as RPointerEvent, type ReactNode } from "react";
import { api, usd } from "./api";
import { Icon, MarkLoader } from "./icons";
import type { Budget } from "./types";

/** Monti, the help gecko in the corner: answers "where is X?" and "how do I do Y?" from montivo/monti_guide.md
 *  (POST /api/monti, one Muse Spark 1.3 call, or the guide's keywords for $0) and shows the place on screen. Show me
 *  runs the climb drawing along the window's edges to the element carrying `data-monti=<target>` (a closed list in
 *  montivo/monti.py TARGETS), then the pointing pose appears beside it while the brand's in / out brackets frame it and
 *  the rest dims. An edit request is not Monti's: it is handed to Ask Montivo's composer, never sent. Motion is CSS
 *  transforms and the Web Animations API only; with reduced motion only the pose changes.
 *  Design: docs/notes/monti-gecko-v2_2026-10-30.html (corner, Show me) and monti-options_2026-10-30.html option A. */

export type MontiPage = "editor" | "home" | "export";
export type MontiCtx = { page: MontiPage; version?: number | null; panel?: string | null; live?: boolean | null; draft?: boolean;
  selection?: string | null };
type Answer = {
  reply: string; steps: string[]; target: string | null; target_label: string | null; target_key: string | null; needs: string | null;
  open_panel: string | null; handoff: string | null; suggestions: string[]; section: string | null;
  source: "spark" | "guide"; model: string | null; cost_usd: number; seconds: number; why?: string | null; budget?: Budget | null;
};
type Turn = { q: string; a: Answer | null; err?: string };
type Pose = "idle" | "hello" | "chin" | "think" | "point" | "yay" | "shrug";

const PANEL_NAME: Record<string, string> = { assistant: "Assistant", media: "Media", captions: "Captions", style: "Style", text: "Text",
  music: "Music", speakers: "Speakers", versions: "Versions", export: "Export" };
const SUGGEST: Record<MontiPage, string[]> = {
  editor: ["Where do I add music?", "How do I fix a caption?", "What does the gate mean?"],
  home: ["How do I start a new project?", "What does the score mean?", "Which stage runs on Spark Open?"],
  export: ["How do I make a 4:5 version?", "How do I make a cover?", "What goes in the bundle?"],
};
const HI_KEY = "montivo.monti.hi";
const TURNS_KEY = "montivo.monti.turns";

// Where Monti sits (docs/notes/monti-placement-research_2026-10-31.md): a named spot or wherever he was dropped, per
// page; tucked into a window edge he is a tab there. Under 900 px he keeps the corner (the panel is a bottom sheet).
type SpotKind = "rail" | "br" | "bl";
type Spot = { kind: SpotKind } | { kind: "free"; x: number; y: number }; // free: the badge centre, fractions of the window
type Stash = { side: "left" | "right"; y: number };
type Geo = { cx: number; cy: number; h: number; kind: Spot["kind"] };
const SPOT_KEY = (place: string) => `montivo.monti.spot.${place}`;
const STASH_KEY = "montivo.monti.stash";
const NARROW = "(max-width: 900px)";
const RATIO = 256 / 133; // the peek drawing
const BADGE_H = 46, RAIL_H = 32, EDGE = 22, SNAP = 90;
const SPOT_NAME: Record<SpotKind, string> = { rail: "Under the panel icons", br: "Bottom right", bl: "Bottom left" };
const load = <T,>(key: string): T | null => { try { return JSON.parse(localStorage.getItem(key) ?? "null"); } catch { return null; } };
const save = (key: string, v: unknown) => { try { if (v == null) localStorage.removeItem(key); else localStorage.setItem(key, JSON.stringify(v)); } catch { /* this tab only */ } };
const clamp = (x: number, a: number, b: number) => Math.min(b, Math.max(a, x));
// Under 900 px in the editor he stays in the corner, but never on a control: he climbs above whatever button, link or
// field he would cover there (the chat's Send, the player's play), and steps aside when there is no free room
const CONTROLS = "button, a[href], input, textarea, select, [role=button], [role=slider], [tabindex='0']";
const NARROW_PAD = 12, NARROW_TOP = 56; // the corner's margin; never higher than under the top bar
/** The lowest bottom offset (px) at which a box w x h at the right edge covers no control, or null when none fits. */
function freeLift(w: number, h: number): number | null {
  const W = window.innerWidth, H = window.innerHeight, left = W - NARROW_PAD - w, right = W - NARROW_PAD;
  const hits = [...document.querySelectorAll<HTMLElement>(CONTROLS)].filter((e) => !e.closest(".monti, .monti-tuck"))
    .map((e) => e.getBoundingClientRect())
    .filter((r) => r.width > 0 && r.height > 0 && r.right > left && r.left < right && r.bottom > 0 && r.top < H);
  const tops = [NARROW_PAD, ...hits.map((r) => H - r.top + 6)].filter((b) => b >= NARROW_PAD).sort((a, b) => a - b);
  for (const b of tops) {
    const top = H - b - h, bot = H - b;
    if (top < NARROW_TOP) return null;
    if (!hits.some((r) => r.top < bot && r.bottom > top)) return b;
  }
  return null;
}

let STASH: Stash | null = load<Stash>(STASH_KEY);
let LAST: Geo | null = null; // where the badge was last drawn: More > Hide Monti tucks him into the nearer edge
const stashers = new Set<() => void>();
const setStash = (s: Stash | null) => { STASH = s; save(STASH_KEY, s); stashers.forEach((f) => f()); };
/** Tucked away or not; the editor's More menu and the palette show or hide him with `tuckMonti`. */
export function useMontiTucked() {
  const [, tick] = useState(0);
  useEffect(() => { const f = () => tick((n) => n + 1); stashers.add(f); return () => { stashers.delete(f); }; }, []);
  return !!STASH;
}
export const tuckMonti = (on: boolean) => setStash(on
  ? { side: LAST && LAST.cx < window.innerWidth / 2 ? "left" : "right", y: LAST ? LAST.cy / window.innerHeight : 0.7 } : null);

/** The empty foot of the editor's rail, if there is room for him under the panel icons. */
function railFoot(): { cx: number; cy: number } | null {
  const rail = document.querySelector<HTMLElement>(".rail");
  const last = rail?.lastElementChild?.getBoundingClientRect();
  if (!rail || !last) return null;
  const r = rail.getBoundingClientRect();
  if (!r.width || r.bottom - last.bottom < RAIL_H + 18) return null;
  return { cx: r.left + r.width / 2, cy: r.bottom - 10 - RAIL_H / 2 };
}
function where(spot: Spot, place: string): Geo {
  const W = window.innerWidth, H = window.innerHeight, pad = place === "home" ? [18, 16] : [14, 10];
  const bw = BADGE_H * RATIO;
  if (spot.kind === "rail") { const f = railFoot(); if (f) return { ...f, h: RAIL_H, kind: "rail" }; }
  if (spot.kind === "free") return { cx: clamp(spot.x * W, bw / 2 + 6, W - bw / 2 - 6), cy: clamp(spot.y * H, BADGE_H / 2 + 6, H - BADGE_H / 2 - 6), h: BADGE_H, kind: "free" };
  const left = spot.kind === "bl";
  return { cx: left ? pad[0] + bw / 2 : W - pad[0] - bw / 2, cy: H - pad[1] - BADGE_H / 2, h: BADGE_H, kind: left ? "bl" : "br" };
}
/** The container's corner and how the panel opens from the badge: beside it at the rail, else above or below. */
function anchor(g: Geo): { style: CSSProperties; cls: string } {
  const W = window.innerWidth, H = window.innerHeight, bw = g.h * RATIO;
  const east = g.cx > W / 2 && g.kind !== "rail", lower = g.cy > H / 2 || g.kind === "rail";
  const x = east ? { right: W - (g.cx + bw / 2), left: "auto" } : { left: g.cx - bw / 2, right: "auto" };
  const y = lower ? { bottom: H - (g.cy + g.h / 2), top: "auto" } : { top: g.cy - g.h / 2, bottom: "auto" };
  const room = g.kind === "rail" ? g.cy + g.h / 2 - 64 : lower ? g.cy - g.h / 2 - 72 : H - (g.cy + g.h / 2) - 24;
  return { style: { ...x, ...y, ["--mh" as string]: `${Math.max(260, room)}px` },
    cls: `${east ? "east" : "west"} ${lower ? "lower" : "upper"} ${g.kind === "rail" ? "at-rail" : ""}` };
}
let TURNS: Turn[] = (() => { try { return JSON.parse(sessionStorage.getItem(TURNS_KEY) ?? "[]"); } catch { return []; } })(); // this tab's conversation, across pages and reloads

const greek = (s: string) => /[α-ωά-ώ]/i.test(s);
const typing = (el: EventTarget | null) => {
  const e = el as HTMLElement | null;
  return !!e && (e.tagName === "INPUT" || e.tagName === "TEXTAREA" || e.tagName === "SELECT" || e.isContentEditable);
};
const reduced = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
const pose = (p: Pose) => `/monti/${p}@2x.webp`;

/** Keys in an answer as keycaps: "key 6", ⌘Z, ⇧⌘Z, ⇧M. */
function rich(text: string): ReactNode {
  const parts = text.split(/(\bkey [^\s,.;)]+|⇧?⌘[^\s,.;)/]+|⇧[^\s,.;)/⌘]+)/g);
  return parts.map((p, i) => {
    if (i % 2 === 0) return <Fragment key={i}>{p}</Fragment>;
    if (p.startsWith("key ")) return <Fragment key={i}>key <kbd>{p.slice(4)}</kbd></Fragment>;
    return <kbd key={i}>{p}</kbd>;
  });
}

export function Monti({ ctx, budget, hasKey, place, nonce, prepare, onOpen, onHandoff, onShortcuts }: {
  ctx: MontiCtx; budget?: Budget | null; hasKey: boolean; place: "editor" | "home"; nonce?: number; // a new nonce opens it (the palette)
  /** Before Show me: open the panel or the Export page the target lives in (or close Export for an editor target). */
  prepare?: (target: string, needs: string | null) => void;
  onOpen?: (panel: string) => void; onHandoff?: (text: string) => void; onShortcuts?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<Turn[]>(TURNS);
  const [text, setText] = useState("");
  const [focus, setFocus] = useState(false);
  const [busy, setBusy] = useState(false);
  const [hi, setHi] = useState(false);
  const [showing, setShowing] = useState<Showing | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const badge = useRef<HTMLButtonElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => { TURNS = turns; try { sessionStorage.setItem(TURNS_KEY, JSON.stringify(turns.filter((t) => t.a).slice(-12))); } catch { /* this page only */ } }, [turns]);
  useEffect(() => { // the first visit: "Hi, I'm Monti" over the corner for a few seconds
    let seen = true;
    try { seen = !!localStorage.getItem(HI_KEY); } catch { /* private window */ }
    if (seen) return;
    const a = window.setTimeout(() => setHi(true), 1200), b = window.setTimeout(() => setHi(false), 11000);
    return () => { window.clearTimeout(a); window.clearTimeout(b); };
  }, []);
  const greeted = () => { setHi(false); try { localStorage.setItem(HI_KEY, "1"); } catch { /* this tab only */ } };
  const toggle = (to = !open) => { greeted(); if (to && STASH) setStash(null); setOpen(to); setNote(null); setMenu(false); };

  // ---- where he sits: a spot per page, dragged with snap, tucked into an edge, aside while you work under him
  const tucked = useMontiTucked();
  const [narrow, setNarrow] = useState(() => window.matchMedia?.(NARROW).matches ?? false);
  const [spot, setSpot] = useState<Spot>(() => load<Spot>(SPOT_KEY(place)) ?? { kind: place === "editor" ? "rail" : "br" });
  const [dragAt, setDragAt] = useState<{ cx: number; cy: number } | null>(null);
  const [menu, setMenu] = useState(false);
  const [aside, setAside] = useState(false);
  const [, relayout] = useState(0);
  const drag = useRef<{ x0: number; y0: number; dx: number; dy: number; moved: boolean; at?: { cx: number; cy: number } } | null>(null);
  const dragged = useRef(false);
  useLayoutEffect(() => { // the rail is laid out in the same commit: measure again once it is there, and on every resize
    const mq = window.matchMedia?.(NARROW);
    const on = () => { setNarrow(mq?.matches ?? false); relayout((n) => n + 1); };
    on();
    window.addEventListener("resize", on); mq?.addEventListener?.("change", on);
    const rail = document.querySelector(".rail"), ro = rail && "ResizeObserver" in window ? new ResizeObserver(on) : null;
    if (rail) ro?.observe(rail);
    return () => { window.removeEventListener("resize", on); mq?.removeEventListener?.("change", on); ro?.disconnect(); };
  }, []);
  const root = useRef<HTMLDivElement>(null);
  const [lift, setLift] = useState<number | null>(NARROW_PAD);
  useEffect(() => { // phones: climb above the control he would cover, again on every scroll, resize and layout change
    if (!narrow || place !== "editor" || tucked || open) { setLift(NARROW_PAD); return; }
    let raf = 0;
    const fit = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => {
      const el = root.current;
      if (el) setLift(freeLift(el.offsetWidth, el.offsetHeight));
    }); };
    fit();
    const iv = window.setInterval(fit, 700); // a panel or a chat line that lands moves the controls without a scroll
    window.addEventListener("scroll", fit, true); window.addEventListener("resize", fit);
    return () => { cancelAnimationFrame(raf); window.clearInterval(iv); window.removeEventListener("scroll", fit, true); window.removeEventListener("resize", fit); };
  }, [narrow, place, tucked, open, hi]);
  const geo: Geo = dragAt ? { ...dragAt, h: BADGE_H, kind: "free" } : where(spot, place);
  if (!narrow && !tucked) LAST = geo;
  const at = anchor(geo);
  const spots = (place === "editor" ? ["rail", "br"] as SpotKind[] : ["br", "bl"] as SpotKind[]) // the editor's bottom left is the rail foot
    .map((k) => ({ k, g: where({ kind: k }, place) })).filter((c) => c.g.kind === c.k);
  const putAt = (s: Spot) => { setSpot(s); save(SPOT_KEY(place), s); setMenu(false); };
  const nearEdge = (cx: number) => cx < EDGE || cx > window.innerWidth - EDGE;
  const drop = (p: { cx: number; cy: number }) => {
    if (nearEdge(p.cx)) { setOpen(false); setStash({ side: p.cx < window.innerWidth / 2 ? "left" : "right", y: p.cy / window.innerHeight }); return; }
    const near = spots.map((c) => ({ ...c, d: Math.hypot(c.g.cx - p.cx, c.g.cy - p.cy) })).sort((a, b) => a.d - b.d)[0];
    putAt(near && near.d < SNAP ? { kind: near.k } : { kind: "free", x: p.cx / window.innerWidth, y: p.cy / window.innerHeight });
  };
  const grab = {
    onPointerDown: (e: RPointerEvent<HTMLButtonElement>) => {
      if (narrow || e.button !== 0) return;
      const r = e.currentTarget.getBoundingClientRect();
      drag.current = { x0: e.clientX, y0: e.clientY, dx: e.clientX - (r.left + r.width / 2), dy: e.clientY - (r.top + r.height / 2), moved: false };
      e.currentTarget.setPointerCapture(e.pointerId);
    },
    onPointerMove: (e: RPointerEvent<HTMLButtonElement>) => {
      const d = drag.current;
      if (!d || (!d.moved && Math.hypot(e.clientX - d.x0, e.clientY - d.y0) < 5)) return;
      if (!d.moved) { d.moved = true; setHi(false); setMenu(false); }
      d.at = { cx: clamp(e.clientX - d.dx, 0, window.innerWidth), cy: clamp(e.clientY - d.dy, BADGE_H / 2, window.innerHeight - BADGE_H / 2) };
      setDragAt(d.at);
    },
    onPointerUp: () => {
      const d = drag.current;
      drag.current = null;
      if (!d?.moved || !d.at) return;
      dragged.current = true; // the click that follows a drag does not open him
      setDragAt(null);
      drop(d.at);
    },
    onPointerCancel: () => { drag.current = null; setDragAt(null); },
    onContextMenu: (e: RMouseEvent) => { if (narrow) return; e.preventDefault(); setMenu(true); },
  };
  useEffect(() => { // WCAG 2.4.11: a drag that passes under him, or the focus landing under him, moves him out of the way
    if (narrow || tucked) return;
    let pressed = false;
    const under = (l: number, t: number, r: number, b: number, pad = 0) => {
      const g = badge.current?.getBoundingClientRect();
      return !!g && r > g.left - pad && l < g.right + pad && b > g.top - pad && t < g.bottom + pad;
    };
    const down = (e: PointerEvent) => { pressed = !(e.target as Element | null)?.closest?.(".monti"); };
    const move = (e: PointerEvent) => { if (pressed && under(e.clientX, e.clientY, e.clientX, e.clientY, 60)) setAside(true); };
    const up = () => { pressed = false; setAside(false); };
    const focus = (e: FocusEvent) => {
      const el = e.target instanceof HTMLElement ? e.target : null;
      if (!el || el.closest(".monti")) { setAside(false); return; }
      const f = el.getBoundingClientRect();
      setAside(f.width > 0 && under(f.left, f.top, f.right, f.bottom));
    };
    window.addEventListener("pointerdown", down, true); window.addEventListener("pointermove", move, true);
    window.addEventListener("pointerup", up, true); window.addEventListener("focusin", focus);
    return () => {
      window.removeEventListener("pointerdown", down, true); window.removeEventListener("pointermove", move, true);
      window.removeEventListener("pointerup", up, true); window.removeEventListener("focusin", focus);
    };
  }, [narrow, tucked]);
  useEffect(() => { // the menu closes on a click elsewhere
    if (!menu) return;
    const off = (e: PointerEvent) => { if (!(e.target as Element | null)?.closest?.(".monti-dock")) setMenu(false); };
    window.addEventListener("pointerdown", off, true);
    return () => window.removeEventListener("pointerdown", off, true);
  }, [menu]);
  useEffect(() => { if (open) window.setTimeout(() => input.current?.focus(), 40); }, [open]);
  useEffect(() => { if (nonce) toggle(true); }, [nonce]); // eslint-disable-line
  useLayoutEffect(() => { const el = body.current; if (el) el.scrollTop = el.scrollHeight; }, [turns, busy, open]);

  // ? opens Monti anywhere (not while typing); Esc closes it
  const keys = useRef<(e: KeyboardEvent) => void>(() => {});
  keys.current = (e: KeyboardEvent) => {
    if (e.key === "Escape" && menu) { setMenu(false); return; }
    if (e.key === "Escape" && (open || showing)) { if (showing) endShow(); else toggle(false); return; }
    if (e.key === "?" && !typing(e.target) && !e.metaKey && !e.ctrlKey) { e.preventDefault(); toggle(); }
  };
  useEffect(() => {
    const on = (e: KeyboardEvent) => keys.current(e);
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);

  const send = async (q: string) => {
    q = q.trim();
    if (!q || busy) return;
    setText(""); setBusy(true); setNote(null);
    const history = turns.filter((t) => t.a).slice(-2).map((t) => ({ q: t.q, a: [t.a!.reply, ...t.a!.steps].join(" ") }));
    setTurns((ts) => [...ts, { q, a: null }]);
    try {
      const a = await api<Answer>("/api/monti", { message: q, context: ctx, history });
      setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? { ...t, a } : t)));
    } catch (e) {
      setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? { ...t, err: String((e as Error).message) } : t)));
    } finally { setBusy(false); }
  };

  const show = async (a: Answer) => {
    if (!a.target) return;
    prepare?.(a.target, a.needs);
    await new Promise((ok) => window.setTimeout(ok, 160)); // a panel or the Export page mounting (a timer: frames stall in a hidden tab)
    const el = document.querySelector<HTMLElement>(`[data-monti="${a.target}"]`);
    let r = el?.getBoundingClientRect();
    if (!el || !r || r.width === 0 || r.height === 0) {
      setNote(greek(a.reply) ? `Το ${a.target_label ?? "σημείο"} δεν φαίνεται εδώ τώρα${window.innerWidth < 900 ? " (ανοίγει σε desktop)" : ""}.`
        : `${a.target_label ?? "That place"} is not on screen here${window.innerWidth < 900 ? " (it opens on a desktop)" : ""}.`);
      return;
    }
    el.scrollIntoView({ block: "nearest", inline: "nearest" });
    r = el.getBoundingClientRect();
    const b = badge.current?.getBoundingClientRect();
    setOpen(false);
    setShowing({ rect: r, from: b ? { x: b.left + b.width / 2, y: b.top + b.height / 2 } : { x: window.innerWidth - 40, y: window.innerHeight - 30 },
      label: a.target_label ?? "", key: a.target_key, say: a.reply.split(/(?<=[.!;])\s/)[0] });
  };
  const endShow = () => { setShowing(null); setOpen(true); };

  const last = [...turns].reverse().find((t) => t.a)?.a ?? null;
  const p: Pose = busy ? "think" : focus && text ? "chin" : !turns.length ? "hello"
    : last?.target || last?.handoff ? "point" : last && last.source === "guide" && !last.section ? "shrug" : "idle";
  const asked = new Set(turns.map((t) => t.q.toLowerCase()));
  const tips = (last?.suggestions?.length ? last.suggestions : SUGGEST[ctx.page]).filter((s) => !asked.has(s.toLowerCase()));
  const left = last?.budget ?? budget;
  const chips = [ctx.page === "home" ? "Projects" : ctx.page === "export" ? "Export" : "Editor",
    ...(ctx.version ? [`v${ctx.version}`] : []), ...(ctx.panel && ctx.page === "editor" ? [`${PANEL_NAME[ctx.panel] ?? ctx.panel} panel`] : [])];

  const W = window.innerWidth, H = window.innerHeight;
  return (<>
    {dragAt && <div className="monti-guides" aria-hidden>
      {spots.map((c) => <i key={c.k} className="monti-snap" style={{ left: c.g.cx - (c.g.h * RATIO) / 2, top: c.g.cy - c.g.h / 2, width: c.g.h * RATIO, height: c.g.h }} />)}
      <i className={`monti-edge left ${dragAt.cx < EDGE ? "on" : ""}`} /><i className={`monti-edge right ${dragAt.cx > W - EDGE ? "on" : ""}`} />
      {nearEdge(dragAt.cx) && <span className="monti-tuck-say" style={dragAt.cx < W / 2 ? { left: 34, top: dragAt.cy - 14 } : { right: 34, top: dragAt.cy - 14 }}>Let go to tuck Monti away</span>}
    </div>}
    {tucked && STASH && (
      <button className={`monti-tuck ${STASH.side}`} style={{ top: clamp(STASH.y * H - 33, 56, H - 76) }} onClick={() => tuckMonti(false)}
        aria-label="Bring Monti back (? opens him)" title="Bring Monti back (? opens him)"><img src="/monti/peek.webp" alt="" /></button>
    )}
    <div ref={root} className={`monti monti-${place} ${narrow ? "narrow" : at.cls} ${dragAt ? "dragging" : ""} ${narrow && lift == null && !open ? "no-room" : ""}`}
      style={narrow ? (place === "editor" && lift != null ? { bottom: lift } : undefined) : at.style}>
      {showing && <ShowMe s={showing} onDone={endShow} />}
      {open && (
        <div className="monti-panel" role="dialog" aria-label="Monti, help">
          <div className="mp-head">
            <img className={`monti-pose ${p}`} key={p} src={pose(p)} alt="" />
            <div><div className="nm">Monti</div><div className="sub">knows every corner of Montivo</div></div>
            <button className="icon-btn mp-x" onClick={() => toggle(false)} aria-label="Close Monti (Esc)"><Icon name="close" /></button>
          </div>
          <div className="mp-ctx">
            {chips.map((c) => <span key={c} className="chip">{c}</span>)}
            {ctx.page !== "home" && ctx.live != null && <span className={`chip ${ctx.live ? "y" : ""}`}>{ctx.live ? "Live" : "Rendered"}</span>}
            {ctx.draft && <span className="chip y">Draft</span>}
          </div>
          <div className="mp-body" ref={body}>
            {!turns.length && (
              <div className="ans hello"><div className="lead">Hi, I'm <b>Monti</b>. Ask me where anything is in Montivo, or how to do it, in English or Greek.
                I'll show you the place on screen.</div>
                <div className="src">Edits to your cut go to Ask Montivo, the chat in the Assistant panel.</div></div>
            )}
            {turns.map((t, i) => (
              <Fragment key={i}>
                <div className="q">{t.q}</div>
                {t.a ? <Reply a={t.a} q={t.q} here={ctx} onShow={() => show(t.a!)} onOpen={onOpen} onHandoff={onHandoff ? (x) => { onHandoff(x); toggle(false); } : undefined} />
                  : t.err ? <div className="ans err">{t.err}</div>
                  : <div className="ans thinking"><MarkLoader /> Monti is looking…</div>}
              </Fragment>
            ))}
            {note && <div className="ans note">{note}</div>}
          </div>
          <div className="mp-sugg">
            {tips.map((s) => <button key={s} className="chip" onClick={() => send(s)} disabled={busy}>{s}</button>)}
            {onShortcuts && !turns.length && <button className="chip" onClick={() => { toggle(false); onShortcuts(); }}>Keyboard shortcuts</button>}
          </div>
          <form className="mp-in" onSubmit={(e) => { e.preventDefault(); send(text); }}>
            <input ref={input} value={text} onChange={(e) => setText(e.target.value)} onFocus={() => setFocus(true)} onBlur={() => setFocus(false)}
              placeholder="Ask Monti how…" aria-label="Ask Monti" maxLength={600} />
            <button className="send" disabled={!text.trim() || busy} aria-label="Ask"><Icon name="send" size={14} /></button>
          </form>
          <div className="mp-foot">
            <span>{last ? (last.source === "spark" ? `Muse Spark 1.3 · $${last.cost_usd.toFixed(3)} this question` : "From the guide · $0")
              : hasKey ? "Muse Spark 1.3 · about $0.013 a question" : "From the guide · $0 (no model key)"}
              {left ? ` · ${usd(left.left)} left` : ""}</span>
            <span><kbd>?</kbd> opens · <kbd>Esc</kbd> closes</span>
          </div>
        </div>
      )}
      {hi && !open && !showing && !tucked && (
        <button className="monti-hi" onClick={() => toggle(true)}>Hi, I'm <b>Monti</b>. <span className="mute">Ask me where anything is, or how to do it.</span></button>
      )}
      {!tucked && (
        <div className={`monti-dock ${aside && !open ? "aside" : ""}`}>
          <button ref={badge} className={`monti-badge ${open ? "on" : ""} ${showing ? "away" : ""}`} {...grab}
            onClick={() => { if (dragged.current) { dragged.current = false; return; } toggle(); }}
            aria-label="Monti: where is it, how do I (?)" aria-expanded={open}
            title={narrow ? "Monti: where is it, how do I…" : "Monti: where is it, how do I… (?) · drag to move him"}>
            <img src="/monti/peek.webp" alt="" draggable={false} />
            <span className="monti-tag">Monti <kbd>?</kbd></span>
          </button>
          {!narrow && !open && (
            <button className={`monti-grip ${menu ? "on" : ""}`} onClick={() => setMenu(!menu)} aria-label="Move or hide Monti" aria-expanded={menu}
              title="Move or hide Monti"><Icon name="more" size={12} /></button>
          )}
          {menu && (
            <div className="monti-menu menu" role="menu">
              <div className="menu-label">Drag Monti anywhere, or put him</div>
              {spots.map((c) => (
                <button key={c.k} role="menuitem" className="menu-item" onClick={() => putAt({ kind: c.k })}>
                  <span>{SPOT_NAME[c.k]}</span>{geo.kind === c.k && <Icon name="check" size={13} />}
                </button>
              ))}
              <div className="menu-sep" />
              <button role="menuitem" className="menu-item" onClick={() => { setMenu(false); setOpen(false); tuckMonti(true); }}><span>Tuck him away</span></button>
              <div className="monti-menu-note">A tab stays on the edge; it or <kbd>?</kbd> brings him back.</div>
            </div>
          )}
        </div>
      )}
    </div>
  </>);
}

function Reply({ a, q, here, onShow, onOpen, onHandoff }: {
  a: Answer; q: string; here: MontiCtx; onShow: () => void; onOpen?: (panel: string) => void; onHandoff?: (text: string) => void;
}) {
  const gr = greek(q);
  const openable = a.open_panel && onOpen && !(here.page === "editor" && here.panel === a.open_panel) ? a.open_panel : null;
  return (
    <div className="ans">
      {!(a.handoff && onHandoff) && <div className="lead">{rich(a.reply)}</div>}
      {a.steps.length > 0 && !a.handoff && <ol className="steps">{a.steps.map((s, i) => <li key={i}><span>{rich(s)}</span></li>)}</ol>}
      {a.handoff && onHandoff && (
        <button className="hand" onClick={() => onHandoff(a.handoff!)}>
          {gr ? <>Αυτό είναι edit. Το <b>Ask Montivo</b> κάνει τα edits: <span>Στείλ' το εκεί →</span></>
            : <>That's an edit. <b>Ask Montivo</b> does edits: <span>Send it there →</span></>}
        </button>
      )}
      {((a.target && !a.handoff && !hidden(a.target)) || openable) && (
        <div className="acts">
          {a.target && !a.handoff && !hidden(a.target) && <button className="b" onClick={onShow}><Icon name="target" size={13} />Show me</button>}
          {openable && <button className="b q2" onClick={() => onOpen!(openable)}>Open {PANEL_NAME[openable] ?? openable}</button>}
        </div>
      )}
      {a.section && !a.handoff && <div className="src">From the Montivo guide · {a.section}{a.source === "guide" ? " · keywords, $0" : ""}</div>}
      {a.source === "guide" && a.why && !a.handoff && <div className="src">No model: {a.why}</div>}
    </div>
  );
}

/** A target that is in the page but has no size: hidden in this layout (the rail and timeline under 900 px). */
const hidden = (target: string) => {
  const el = document.querySelector(`[data-monti="${target}"]`);
  return !!el && el.getClientRects().length === 0;
};

// ------------------------------------------------------------------ Show me

type Showing = { rect: DOMRect; from: { x: number; y: number }; label: string; key: string | null; say: string };
const RUN_PX_S = 1100;
const POINT_H = 84;

/** The climb drawing runs from the corner along the bottom edge, then up to the target; the pointing pose then stands
 *  beside it, the target between an in and an out bracket and the rest dimmed. A click anywhere (or Esc) ends it. */
function ShowMe({ s, onDone }: { s: Showing; onDone: () => void }) {
  const [phase, setPhase] = useState<"run" | "point">(reduced() ? "point" : "run");
  const runner = useRef<HTMLImageElement>(null);
  const r = s.rect;
  const tx = r.left + r.width / 2, ty = r.top + r.height / 2;
  useEffect(() => {
    if (phase !== "run" || !runner.current) return;
    // two legs: along the bottom edge to the target's x, then up (or down) to it; the head leads (the drawing faces up)
    const a = s.from, c = { x: tx, y: a.y }, d = { x: tx, y: ty + (ty < a.y ? r.height / 2 + 18 : -r.height / 2 - 18) };
    const l1 = Math.abs(c.x - a.x), l2 = Math.abs(d.y - c.y), all = Math.max(1, l1 + l2);
    const turn1 = c.x < a.x ? -90 : 90, turn2 = d.y < c.y ? 0 : 180;
    const at = (x: number, y: number, deg: number) => `translate(${x - 22}px, ${y - 30}px) rotate(${deg}deg)`;
    const k = Math.min(0.98, Math.max(0.02, l1 / all));
    const anim = runner.current.animate([
      { transform: at(a.x, a.y, turn1), offset: 0 },
      { transform: at(c.x, c.y, turn1), offset: Math.max(0.01, k - 0.02) },
      { transform: at(c.x, c.y, turn2), offset: Math.min(0.99, k + 0.02) },
      { transform: at(d.x, d.y, turn2), offset: 1 },
    ], { duration: Math.min(1600, Math.max(550, (all / RUN_PX_S) * 1000)), easing: "ease-in-out", fill: "forwards" });
    anim.onfinish = () => setPhase("point");
    return () => anim.cancel();
  }, [phase]); // eslint-disable-line
  useEffect(() => {
    if (phase !== "point") return;
    const t = window.setTimeout(onDone, 5200);
    return () => window.clearTimeout(t);
  }, [phase, onDone]);

  // the pose points left with its finger near the top third: stand to the right of the target, else mirrored on its left
  const w = POINT_H * 1.11, pad = 18; // clear of the out bracket
  const right = r.right + pad + w < window.innerWidth - 8;
  const px = right ? r.right + pad : r.left - pad - w;
  const py = Math.min(window.innerHeight - POINT_H - 70, Math.max(8, ty - POINT_H * 0.36));
  // the frame around the target, kept inside the window so both brackets show (New project sits at the top-right edge)
  const hx = Math.max(10, r.left - 6), hy = Math.max(10, r.top - 6);
  const hole = { left: hx, top: hy, width: Math.min(window.innerWidth - 10, r.right + 6) - hx, height: Math.min(window.innerHeight - 10, r.bottom + 6) - hy };
  const sayLeft = Math.min(window.innerWidth - 268, Math.max(8, right ? px : px + w - 260));
  return (
    <div className="showme" onClick={onDone} role="status" aria-live="polite">
      <div className="sm-hole" style={hole}>
        <i className="bl" /><i className="br" />
      </div>
      {phase === "run" && <img ref={runner} className="sm-runner" src="/monti/climb.webp" alt="" />}
      {phase === "point" && <>
        <img className={`sm-point ${right ? "" : "flip"}`} src="/monti/point@2x.webp" alt="" style={{ left: px, top: py, height: POINT_H }} />
        <div className="sm-say" style={{ left: sayLeft, top: py + POINT_H + 6 }}>
          <b>{s.label}</b>{s.key ? <> · key <kbd>{s.key}</kbd></> : null}
          <div className="mute">{rich(s.say)}</div>
        </div>
      </>}
    </div>
  );
}
