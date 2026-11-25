import { useEffect, useState, type ReactNode } from "react";
import { call } from "./api";

interface Check { name: string; ok: boolean; skip?: boolean; detail?: string; }
interface Use { device: string; path: string; block: string; }
interface Trace {
  fact: { id: string; part: string; key: string; signal?: string; value: string; qualifier?: string; quote: string; derived?: boolean; working?: string; status: string; confidence?: string; page: number };
  document: { kind: string; filename: string; page: number; id: string; snippet: string };
  constant: string;
  header_line: string;
  review: { verdict?: string; checks?: Check[]; used_by?: Use[] };
}

const VERDICT: Record<string, [string, string]> = {
  verified: ["The app checked this itself. You don't need to.", "good"],
  confirmed: ["You confirmed this.", "good"],
  needs_you: ["Please look. The app couldn't verify all of this, and code relies on it.", "warn"],
  unused: ["Not verified, but no code relies on it yet, so it can wait.", ""],
};

export const factName = (key: string, signal?: string, qualifier?: string) =>
  `${qualifier && !["as stated", "default", "required"].includes(qualifier) ? `${qualifier} ` : ""}${key.replace(/_/g, " ")}${signal ? ` (${signal})` : ""}`;

/** Wraps the quote inside the page text, so you can see it where it was printed. */
function highlight(snippet: string, quote: string): ReactNode {
  const flat = (s: string) => s.replace(/\s+/g, " ");
  const text = flat(snippet);
  const at = text.toLowerCase().indexOf(flat(quote).toLowerCase().slice(0, 40));
  if (at < 0) return <>{text}</>;
  const end = Math.min(text.length, at + flat(quote).length);
  return <>{text.slice(0, at)}<mark>{text.slice(at, end)}</mark>{text.slice(end)}</>;
}

export default function FactTrail({ base, factId, onClose, onDecide }: { base: string; factId: string; onClose: () => void; onDecide: (id: string, verb: "confirm" | "reject") => void }) {
  const [trace, setTrace] = useState<Trace | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { call<Trace>(`${base}/facts/${factId}/trace`).then(setTrace).catch((e) => setError(String(e))); }, [base, factId]);
  useEffect(() => { const key = (e: KeyboardEvent) => e.key === "Escape" && onClose(); window.addEventListener("keydown", key); return () => window.removeEventListener("keydown", key); }, [onClose]);

  const f = trace?.fact;
  const steps: { label: string; body: ReactNode }[] = trace && f ? [
    { label: "Where it came from", body: <><b>{trace.document.kind}</b> for {f.part}<div className="dim-text small-text">{trace.document.filename}, <a className="page-link" href={`/api${base}/datasheets/${trace.document.id}/file#page=${trace.document.page}`} target="_blank" rel="noreferrer">open page {trace.document.page}</a></div></> },
    { label: "What the page says", body: <>
      {!trace.review.checks?.find((c) => c.name.startsWith("Quote found"))?.ok && <p className="trail-warn">The reader quoted: "{f.quote}". Those exact words aren't on the page, so here's the page around where it looked.</p>}
      <blockquote className="trail-quote">{trace.document.snippet ? highlight(trace.document.snippet, f.quote) : f.quote}</blockquote>
    </> },
    ...(f.derived ? [{ label: "How it was worked out", body: <p className="trail-working">{f.working}</p> }] : []),
    { label: "The fact", body: <div className="trail-fact"><span>{factName(f.key, f.signal, f.qualifier)}</span><b className="mono">{f.value}</b></div> },
    { label: "Its name in the code", body: <code className="mono">{trace.header_line || trace.constant}</code> },
    { label: "Code that relies on it", body: trace.review.used_by?.length ? <ul className="trail-uses">{trace.review.used_by.map((u, i) => <li key={i}><span className="mono">{u.device}/{u.path}</span> {u.block}</li>)}</ul> : <span className="dim-text">No code uses it yet.</span> },
  ] : [];
  const verdict = VERDICT[trace?.review.verdict ?? ""] ?? ["", ""];

  return (
    <div className="trail-overlay" role="dialog" aria-modal="true" aria-label="Evidence trail" onClick={onClose}>
      <aside className="trail" onClick={(e) => e.stopPropagation()}>
        <div className="trail-head"><h3>How the app knows this</h3><button className="ghost" aria-label="Close" onClick={onClose}>×</button></div>
        {error && <p className="error">{error}</p>}
        {!trace && !error && <p className="hint">Loading…</p>}
        {trace && f && <>
          {verdict[0] && <p className={`trail-verdict ${verdict[1]}`}>{verdict[0]}</p>}
          <ol className="trail-steps">
            {steps.map((s, i) => <li key={s.label} className="trail-step" style={{ animationDelay: `${i * 130}ms` }}><div className="eyebrow">{s.label}</div>{s.body}</li>)}
          </ol>
          <h4>What the app checked</h4>
          <ul className="trail-checks">
            {(trace.review.checks ?? []).filter((c) => !c.skip).map((c) => <li key={c.name} className={c.ok ? "ok" : "no"}><span aria-hidden="true">{c.ok ? "✓" : "✗"}</span>{c.name}{c.detail ? `: ${c.detail}` : ""}</li>)}
          </ul>
          {f.status === "proposed" && <div className="row"><button className="primary" onClick={() => { onDecide(f.id, "confirm"); onClose(); }}>It's right</button><button onClick={() => { onDecide(f.id, "reject"); onClose(); }}>It's wrong</button></div>}
        </>}
      </aside>
    </div>
  );
}
