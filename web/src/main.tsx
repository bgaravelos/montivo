import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Compare } from "./Compare";
import { Home } from "./Home";
import { Editor } from "./Editor";
import { LoopFilm } from "./Loop";
import "./styles.css";

/** After a deploy an open tab keeps running the build it loaded (the app never reloads the page by itself), so a new
 *  project started in it showed the old UI. Every minute, and whenever the tab comes back into view, ask for index.html
 *  (never cached) and compare its bundle with ours. A new one reloads the page at the next route change, where nothing
 *  staged is lost, and offers a Reload button until then. The Vite dev server has no hashed bundle: no check. */
const BUNDLE = /\/assets\/index-[\w-]+\.js/;
const OURS = document.querySelector<HTMLScriptElement>('script[type="module"][src*="/assets/index-"]')?.src.match(BUNDLE)?.[0] ?? null;
const CHECK_MS = 60_000;

function useNewBuild(): boolean {
  const [fresh, setFresh] = useState(false);
  useEffect(() => {
    if (!OURS) return;
    let live = true;
    const check = () => fetch("/", { cache: "no-store" }).then((r) => (r.ok ? r.text() : ""))
      .then((html) => { const m = html.match(BUNDLE)?.[0]; if (live && m && m !== OURS) setFresh(true); }) // the sign-in page has no bundle
      .catch(() => { /* offline or a redeploy in progress: the next check */ });
    const h = window.setInterval(check, CHECK_MS);
    const back = () => { if (!document.hidden) check(); };
    document.addEventListener("visibilitychange", back);
    return () => { live = false; window.clearInterval(h); document.removeEventListener("visibilitychange", back); };
  }, []);
  return fresh;
}

function NewBuild() {
  return (
    <div role="status" style={{ position: "fixed", left: "50%", bottom: 18, transform: "translateX(-50%)", zIndex: 1000,
      display: "flex", alignItems: "center", gap: 12, padding: "8px 8px 8px 14px", borderRadius: 99, fontSize: 13,
      background: "var(--panel-2)", border: "1px solid var(--line-2)", boxShadow: "0 12px 40px -12px #000" }}>
      <span><span className="pulse" /> A new version of Montivo is live</span>
      <button className="btn" style={{ borderRadius: 99 }} onClick={() => window.location.reload()}>Reload</button>
    </div>
  );
}

// hash routes: #/  ·  #/run/012[?replay=4]  ·  #/run/012/compare?a=1&b=2  ·  #/run/012/loop
function useHash() {
  const [hash, setHash] = useState(window.location.hash || "#/");
  useEffect(() => {
    const on = () => setHash(window.location.hash || "#/");
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash;
}

function App() {
  const hash = useHash();
  const fresh = useNewBuild();
  useEffect(() => { if (fresh) window.location.reload(); }, [hash]); // eslint-disable-line -- only on a route change
  return <>{route(hash)}{fresh && <NewBuild />}</>;
}

function route(hash: string) {
  const go = (h: string) => { window.location.hash = h; };
  const [path, query] = hash.slice(1).split("?");
  const q = new URLSearchParams(query ?? "");
  const replay = Number(q.get("replay") || 0);
  const m = path.match(/^\/run\/([^/]+)(\/compare|\/loop)?$/);
  if (m && m[2] === "/loop") return <LoopFilm id={m[1]} replay={replay} go={go} />;
  if (m && m[2]) return <Compare id={m[1]} a={Number(q.get("a") || 1)} b={Number(q.get("b") || 2)} replay={replay} go={go} />;
  if (m) return <Editor key={`${m[1]}-${replay}`} id={m[1]} replay={replay} go={go} />;
  return <Home go={go} />;
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
