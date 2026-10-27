import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Compare } from "./Compare";
import { Home } from "./Home";
import { Room } from "./Room";
import "./styles.css";

// hash routes: #/  ·  #/run/012[?replay=4]  ·  #/run/012/compare?a=1&b=2
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
  const go = (h: string) => { window.location.hash = h; };
  const [path, query] = hash.slice(1).split("?");
  const q = new URLSearchParams(query ?? "");
  const replay = Number(q.get("replay") || 0);
  const m = path.match(/^\/run\/([^/]+)(\/compare)?$/);
  if (m && m[2]) return <Compare id={m[1]} a={Number(q.get("a") || 1)} b={Number(q.get("b") || 2)} replay={replay} go={go} />;
  if (m) return <Room key={`${m[1]}-${replay}`} id={m[1]} replay={replay} go={go} />;
  return <Home go={go} />;
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
