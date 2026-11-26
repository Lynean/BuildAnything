import { useEffect, useRef, useState } from "react";
import { call } from "./api";

// Terminals inside the app, for you only: a shell in the project folder with the project's Python first on PATH.
// Any screen can open one in a folder: window.dispatchEvent(new CustomEvent("ba:terminal", { detail: "firmware/laptop" })).

interface Term { id: string; title: string; folder: string; running: boolean; }

export default function TerminalPanel({ projectId, onClose, request }: { projectId: string | null; onClose: () => void; request: { folder: string; n: number } | null }) {
  const [terms, setTerms] = useState<Term[]>([]);
  const [active, setActive] = useState("");
  const [text, setText] = useState<Record<string, string>>({});
  const [line, setLine] = useState("");
  const [history, setHistory] = useState<string[]>([]);
  const [back, setBack] = useState(-1);
  const [error, setError] = useState("");
  const seq = useRef<Record<string, number>>({});
  const out = useRef<HTMLPreElement>(null);
  const input = useRef<HTMLInputElement>(null);

  const refresh = async () => { if (!projectId) return []; const list = await call<Term[]>(`/terminals?project=${encodeURIComponent(projectId)}`); setTerms(list); return list; };
  const open = async (folder = "") => {
    if (!projectId) return;
    setError("");
    try {
      const t = await call<Term>("/terminals", "POST", { project: projectId, folder });
      seq.current[t.id] = 0;
      setText((x) => ({ ...x, [t.id]: "" }));
      await refresh();
      setActive(t.id);
      window.setTimeout(() => input.current?.focus(), 50);
    } catch (e) { setError((e as Error).message); }
  };

  useEffect(() => { refresh().then((list) => { if (list.length) setActive((a) => a || list[0].id); else if (!request) open(); }).catch((e) => setError((e as Error).message)); }, [projectId]);
  useEffect(() => { if (request) open(request.folder); }, [request?.n]);

  // Poll the active terminal's output.
  useEffect(() => {
    if (!active) return;
    let alive = true;
    const tick = async () => {
      try {
        const r = await call<{ text: string; seq: number; running: boolean }>(`/terminals/${active}/output?after=${seq.current[active] ?? 0}`);
        if (!alive) return;
        seq.current[active] = r.seq;
        if (r.text) setText((x) => ({ ...x, [active]: ((x[active] ?? "") + r.text).slice(-200_000) }));
      } catch { /* closed */ }
    };
    tick();
    const timer = window.setInterval(tick, 400);
    return () => { alive = false; window.clearInterval(timer); };
  }, [active]);
  useEffect(() => { const el = out.current; if (el) el.scrollTop = el.scrollHeight; }, [text, active]);

  const send = async () => {
    if (!active) return;
    const l = line;
    setLine(""); setBack(-1);
    if (l.trim()) setHistory((h) => [...h.filter((x) => x !== l), l].slice(-50));
    try { await call(`/terminals/${active}/input`, "POST", { line: l }); } catch (e) { setError((e as Error).message); }
  };
  const stop = async () => { if (active) { try { await call(`/terminals/${active}/stop`, "POST"); } catch (e) { setError((e as Error).message); } } };
  const close = async (id: string) => {
    try { await call(`/terminals/${id}`, "DELETE"); } catch { /* already closed */ }
    const list = await refresh();
    if (id === active) setActive(list[0]?.id ?? "");
  };
  const keys = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") { e.preventDefault(); send(); }
    else if (e.key === "ArrowUp" && history.length) { e.preventDefault(); const i = back < 0 ? history.length - 1 : Math.max(0, back - 1); setBack(i); setLine(history[i]); }
    else if (e.key === "ArrowDown" && back >= 0) { e.preventDefault(); const i = back + 1; if (i >= history.length) { setBack(-1); setLine(""); } else { setBack(i); setLine(history[i]); } }
  };

  return (
    <section className="terminal-panel" aria-label="Terminals">
      <div className="terminal-tabs" role="tablist">
        {terms.map((t) => (
          <span key={t.id} className={`terminal-tab ${t.id === active ? "on" : ""}`}>
            <button role="tab" aria-selected={t.id === active} onClick={() => setActive(t.id)} title={t.folder || "project folder"}>{t.title}</button>
            <button className="terminal-x" aria-label={`Close ${t.title}`} onClick={() => close(t.id)}>×</button>
          </span>
        ))}
        <button className="small" onClick={() => open()} disabled={!projectId}>+ New</button>
        <span className="grow" />
        <button className="small" onClick={stop} disabled={!active} title="Ends whatever is running in this terminal, and everything it started, then opens a fresh shell">Stop</button>
        <button className="small" onClick={onClose}>Hide</button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {!projectId ? <p className="hint">Choose a project first; terminals open in its folder.</p> : !active ? <p className="hint">No terminal open. Press + New.</p> : (
        <>
          <pre ref={out} className="terminal-out" onClick={() => input.current?.focus()}>{text[active] ?? ""}</pre>
          <div className="terminal-in"><span className="mono">›</span><input ref={input} value={line} onChange={(e) => setLine(e.target.value)} onKeyDown={keys} placeholder="Type a command and press Enter, such as python app.py" aria-label="Command" spellCheck={false} autoComplete="off" /></div>
        </>
      )}
    </section>
  );
}
