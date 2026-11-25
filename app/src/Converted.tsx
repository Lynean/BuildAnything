import { useEffect, useState } from "react";
import { call } from "./api";
import { MarkdownViewer, TableViewer } from "./Viewers";

// Files the app had no viewer for, shown through a converter the Platform agent wrote and the person approved.
export interface Result { kind: "markdown" | "html" | "table" | "image" | "text"; text?: string; html?: string; png_base64?: string; sheets?: { name: string; rows: string[][]; more: boolean }[]; }
export interface Converter { type: string; status: "none" | "proposed" | "approved" | "rejected"; summary?: string; tested_on?: string; tests?: number; preview?: Result; code?: string; reason?: string; approved_by?: string; }
interface Converted { converter: Converter; ok?: boolean; result?: Result; error?: string; }

export function ResultView({ result }: { result: Result }) {
  if (result.kind === "markdown") return <MarkdownViewer text={result.text ?? ""} />;
  if (result.kind === "table") return <TableViewer sheets={(result.sheets ?? []).map((s) => ({ ...s, more: !!s.more }))} />;
  if (result.kind === "image") return <div className="converted-image"><img src={`data:image/png;base64,${result.png_base64}`} alt="Converted view" /></div>;
  // HTML from a converter is shown in a frame with no scripts and no access to the app.
  if (result.kind === "html") return <iframe className="converted-html" sandbox="" srcDoc={result.html ?? ""} title="Converted view" />;
  return <pre className="file-text wrap">{result.text}</pre>;
}

export function ConverterApproval({ converter, onDecided }: { converter: Converter; onDecided: (c: Converter) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const decide = async (approve: boolean) => {
    const reason = approve ? "" : window.prompt(`Why reject the converter for ${converter.type}? Platform reads this when it tries again.`, "") ?? "";
    if (!approve && !reason.trim()) return;
    setBusy(true); setError("");
    try { onDecided(await call<Converter>("/platform/converters/decide", "POST", { type: converter.type, approve, reason })); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <div className="converter-card">
      <div className="converter-head">
        <b>Platform made a viewer for {converter.type} files</b>
        <span className="hint">Tested on {converter.tested_on}{converter.tests ? `, after ${converter.tests} ${converter.tests === 1 ? "try" : "tries"}` : ""}. Once approved, every {converter.type} file in every project opens this way, with no AI cost.</span>
      </div>
      {converter.summary && <p>{converter.summary}</p>}
      {error && <p className="error">{error}</p>}
      <div className="row">
        <button className="primary" disabled={busy} onClick={() => decide(true)}>Approve</button>
        <button disabled={busy} onClick={() => decide(false)}>Reject</button>
      </div>
      <details><summary>The converter's code</summary><pre className="file-text">{converter.code}</pre></details>
      {converter.preview && <div className="converter-preview"><div className="eyebrow">What this file looks like through it</div><ResultView result={converter.preview} /></div>}
    </div>
  );
}

/** Shown for a file the app has no viewer for: its converted view, a converter waiting for approval, or a way to ask Platform for one. */
export function ConverterPanel({ projectId, path, type, onShowing }: { projectId: string; path: string; type: string; onShowing: (converted: boolean) => void }) {
  const [state, setState] = useState<Converted | null>(null);
  const [making, setMaking] = useState(false);
  const [error, setError] = useState("");
  const load = () => call<Converted>(`/projects/${projectId}/files/converted?path=${encodeURIComponent(path)}`).then((s) => { setState(s); onShowing(!!s.result); }).catch((e) => setError((e as Error).message));
  useEffect(() => { setState(null); setError(""); load(); }, [projectId, path]);
  const ask = async () => {
    setMaking(true); setError("");
    try { await call(`/projects/${projectId}/files/converter`, "POST", { path }); await load(); } catch (e) { setError((e as Error).message); } finally { setMaking(false); }
  };
  if (!state) return error ? <div className="viewer-note error">{error}</div> : null;
  const c = state.converter;
  if (state.result) return <ResultView result={state.result} />;
  if (state.error && c.status === "approved") return <div className="viewer-note error">The approved converter for {c.type} hit an error on this file: {state.error.slice(-400)}</div>;
  if (c.status === "proposed") return <ConverterApproval converter={c} onDecided={() => load()} />;
  return (
    <div className="converter-card ask">
      <b>The app has no viewer for {type} files yet.</b>
      <span className="hint">Platform can write one: a small converter that it tests on this file in a sandbox, then brings to you to approve. It uses AI, charged to this project (usually a few cents to $0.30), and only once per file type.{c.status === "rejected" ? ` You rejected the last one: ${c.reason}` : ""}</span>
      {error && <p className="error">{error}</p>}
      <div className="row"><button className="primary" disabled={making} onClick={ask}>{making ? "Platform is writing and testing a converter…" : `Ask Platform to make a ${type} viewer`}</button></div>
    </div>
  );
}
