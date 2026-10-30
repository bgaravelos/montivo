import { useEffect, useState } from "react";
import { api } from "./api";
import { Icon, MarkLoader } from "./icons";

// The Share button and this run's links (montivo/server/share.py): Share makes or copies the version's read-only link,
// the caret lists every link of the run with Copy and Unshare. An unshared link answers 404 at once; sharing that version
// again makes a new link, so the old one never comes back.

type Link = { token: string; path: string; version: number; created: string };

export function ShareMenu({ id, version, disabled, onShare, flash }: {
  id: string; version: number | null; disabled: boolean; onShare: (n: number) => Promise<void>; flash: (text: string, tone?: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [links, setLinks] = useState<Link[] | null>(null);
  const load = () => api<{ shares: Link[] }>(`/api/runs/${id}/shares`).then((r) => setLinks(r.shares)).catch(() => setLinks([]));
  useEffect(() => { if (open) load(); }, [open, id]); // eslint-disable-line
  useEffect(() => {
    if (!open) return;
    const off = () => setOpen(false);
    window.addEventListener("pointerdown", off);
    return () => window.removeEventListener("pointerdown", off);
  }, [open]);
  const copy = async (l: Link) => {
    const url = `${window.location.origin}${l.path}`;
    try { await navigator.clipboard.writeText(url); flash(`Link copied: ${url}`); } catch { flash(`Share link: ${url}`); }
  };
  const unshare = async (l: Link) => {
    try {
      const r = await api<{ shares: Link[] }>(`/api/runs/${id}/share/${l.token}`, undefined, "DELETE");
      setLinks(r.shares);
      flash(`Link to Version ${l.version} removed: it no longer opens.`);
    } catch (e) { flash(String((e as Error).message), "error"); }
  };
  return (
    <div className="share-menu" data-monti="top.share" onPointerDown={(e) => e.stopPropagation()}>
      <button className="btn quiet share-btn" disabled={disabled || version == null} onClick={() => version != null && onShare(version).then(() => { if (open) load(); })}
        title="A read-only page with this version, its blind review and the score history; anyone with the link can watch it" aria-label="Share">
        <Icon name="link" /> <span className="btn-label">Share</span>
      </button>
      <button className="btn quiet share-caret" onClick={() => setOpen(!open)} title="This project's share links" aria-label="Share links">
        <Icon name="chevronDown" size={12} />
      </button>
      {open && (
        <div className="share-list">
          <div className="menu-label">Share links</div>
          {links == null ? <div className="mute small sl-empty"><MarkLoader /> Loading…</div>
            : !links.length ? <div className="mute small sl-empty">No links yet. Share makes one for the version on screen.</div>
            : links.map((l) => (
              <div key={l.token} className="sl-row">
                <span className="sl-v"><b>Version {l.version}</b><span className="mute small">{l.created.replace("T", " ").slice(0, 16)}</span></span>
                <a className="mono small sl-path" href={l.path} target="_blank" rel="noreferrer" title="Open the page">{l.path}</a>
                <button className="ib" onClick={() => copy(l)} title="Copy the link"><Icon name="link" size={12} /></button>
                <button className="btn danger-quiet small" onClick={() => unshare(l)} title="The link stops working at once; the version stays">Unshare</button>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}
