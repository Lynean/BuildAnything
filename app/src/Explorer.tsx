import { useEffect, useState } from "react";
import { call } from "./api";
import { JsonViewer, MarkdownViewer, ModelViewer, PdfViewer, TableViewer } from "./Viewers";
import { ConverterPanel } from "./Converted";

// The project's folder, like a code workspace: CAD, firmware, simulations, reports, .git and the rest. Read-only.
interface Entry { name: string; path: string; dir: boolean; size: number | null; }
interface Listing { path: string; entries: Entry[]; truncated: boolean; }
export interface FileContent { path: string; name: string; size: number; viewer?: string; kind: "text" | "image" | "binary" | "too_big" | "pdf" | "model" | "table"; text?: string; data?: string; pretty?: string; note?: string;
  partial?: boolean; start?: number; end?: number; sheets?: { name: string; rows: string[][]; more: boolean }[]; }
const PAGE = 1_000_000;  // the backend's page size for big text files

const size = (n: number | null) => n === null ? "" : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;

function Folder({ projectId, path, depth, open, onOpenFile, selected }: { projectId: string; path: string; depth: number; open: boolean; onOpenFile: (path: string) => void; selected: string }) {
  const [listing, setListing] = useState<Listing | null>(null);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  useEffect(() => {
    if (!open) return;
    call<Listing>(`/projects/${projectId}/files?path=${encodeURIComponent(path)}`).then(setListing).catch((e) => setError((e as Error).message));
  }, [projectId, path, open]);
  if (!open) return null;
  if (error) return <div className="tree-note" style={{ paddingLeft: 12 + depth * 14 }}>{error}</div>;
  if (!listing) return <div className="tree-note" style={{ paddingLeft: 12 + depth * 14 }}>Loading…</div>;
  return (
    <ul className="tree">
      {listing.entries.map((e) => (
        <li key={e.path}>
          <button className={`tree-row ${e.dir ? "dir" : "file"} ${selected === e.path ? "on" : ""}`} style={{ paddingLeft: 10 + depth * 14 }} title={e.path}
            onClick={() => (e.dir ? setExpanded((x) => ({ ...x, [e.path]: !x[e.path] })) : onOpenFile(e.path))}>
            <span className="tree-caret">{e.dir ? (expanded[e.path] ? "▾" : "▸") : ""}</span>
            <span className="tree-name">{e.name}</span>
            {!e.dir && <span className="tree-size">{size(e.size)}</span>}
          </button>
          {e.dir && <Folder projectId={projectId} path={e.path} depth={depth + 1} open={!!expanded[e.path]} onOpenFile={onOpenFile} selected={selected} />}
        </li>
      ))}
      {listing.truncated && <li className="tree-note" style={{ paddingLeft: 12 + depth * 14 }}>Only the first 2000 shown.</li>}
    </ul>
  );
}

export default function Explorer({ projectId, onOpenFile, selected, onClose }: { projectId: string; onOpenFile: (path: string) => void; selected: string; onClose: () => void }) {
  const [version, setVersion] = useState(0);
  // Drag the panel's left edge (or use the arrow keys on it) to make it wider or narrower. Remembered on this computer.
  const [width, setWidth] = useState(() => { try { return Number(localStorage.getItem("ba.filesPanelW")) || 340; } catch { return 340; } });
  const fit = (w: number) => {
    const next = Math.round(Math.max(220, Math.min(w, window.innerWidth - 420)));
    setWidth(next);
    try { localStorage.setItem("ba.filesPanelW", String(next)); } catch { /* optional */ }
  };
  const startDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const x0 = e.clientX, w0 = width;
    const move = (ev: PointerEvent) => fit(w0 - (ev.clientX - x0));
    const stop = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", stop); document.body.classList.remove("resizing"); };
    document.body.classList.add("resizing");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  };
  return (
    <aside className="files-panel" aria-label="Project files" style={{ width }}>
      <div className="pane-resize col left" role="separator" aria-orientation="vertical" aria-label="Drag to resize the file explorer" title="Drag to resize" tabIndex={0} onPointerDown={startDrag}
        onKeyDown={(e) => { if (e.key === "ArrowLeft") fit(width + 30); if (e.key === "ArrowRight") fit(width - 30); }} />
      <div className="files-panel-head">
        <b>Files</b>
        <button className="small" onClick={() => setVersion((v) => v + 1)} title="Read the folder again">Refresh</button>
        <button className="small" onClick={onClose} aria-label="Close the file explorer">×</button>
      </div>
      <div className="files-panel-body">
        {projectId ? <Folder key={`${projectId}-${version}`} projectId={projectId} path="" depth={0} open onOpenFile={onOpenFile} selected={selected} /> : <p className="tree-note">Choose a project first.</p>}
      </div>
    </aside>
  );
}

export function FileView({ projectId, path }: { projectId: string; path: string }) {
  const [file, setFile] = useState<FileContent | null>(null);
  const [error, setError] = useState("");
  const [wrap, setWrap] = useState(false);
  const [rawText, setRawText] = useState(false);  // markdown, JSON and XML have a readable view and the raw text
  const [converted, setConverted] = useState(false);  // a file shown through a Platform converter hides its raw text unless asked
  const load = (start?: number) => {
    setError("");
    const at = start === undefined ? "" : `&start=${start}`;
    call<FileContent>(`/projects/${projectId}/files/content?path=${encodeURIComponent(path)}${at}`).then(setFile).catch((e) => setError((e as Error).message));
  };
  useEffect(() => { setFile(null); setRawText(false); setConverted(false); setWrap(path.endsWith(".jsonl") || path.endsWith(".json")); load(); }, [projectId, path]);
  if (error) return <div className="page"><p className="error">{error}</p></div>;
  if (!file) return <div className="page"><p className="hint">Loading…</p></div>;
  const start = file.start ?? 0, end = file.end ?? file.size;
  const raw = `/api/projects/${projectId}/files/raw?path=${encodeURIComponent(file.path)}`;
  const readable = file.kind === "text" && ["markdown", "json", "xml"].includes(file.viewer ?? "") && !(file.viewer === "json" && file.partial && !file.name.endsWith(".jsonl"));
  const noViewer = file.viewer === "unknown" || file.viewer === "binary";
  const showRaw = (!readable || rawText) && !(noViewer && converted && !rawText);
  const text = file.viewer === "xml" && !rawText && file.pretty ? file.pretty : (file.text ?? "");
  return (
    <div className="file-view">
      <div className="file-meta">
        <span className="mono">{file.path}</span>
        <span className="file-tools">
          {noViewer && converted && file.kind === "text" && <span className="seg">{["Converted", "Raw"].map((m) => <button key={m} className={(m === "Raw") === rawText ? "on" : ""} onClick={() => setRawText(m === "Raw")}>{m}</button>)}</span>}
          {readable && <span className="seg">{["Readable", "Raw"].map((m) => <button key={m} className={(m === "Raw") === rawText ? "on" : ""} onClick={() => setRawText(m === "Raw")}>{m}</button>)}</span>}
          {file.kind === "text" && (showRaw || file.viewer === "xml") && <label><input type="checkbox" checked={wrap} onChange={(e) => setWrap(e.target.checked)} /> Wrap lines</label>}
          {(file.kind === "pdf" || file.kind === "model" || file.kind === "image") && <a href={raw} target="_blank" rel="noreferrer">Open on its own</a>}
          <span>{size(file.size)}</span>
        </span>
      </div>
      {file.partial && (
        <div className="file-pager">
          <span>Showing {size(start)} to {size(end)} of {size(file.size)}{end >= file.size ? ", the end of the file" : ""}. Line numbers count from the top of this page.</span>
          <button className="small" disabled={start === 0} onClick={() => load(0)}>Start</button>
          <button className="small" disabled={start === 0} onClick={() => load(Math.max(0, start - PAGE))}>Earlier</button>
          <button className="small" disabled={end >= file.size} onClick={() => load(end)}>Later</button>
          <button className="small" disabled={end >= file.size} onClick={() => load()}>End</button>
        </div>
      )}
      {noViewer && !(converted && rawText) && <ConverterPanel projectId={projectId} path={file.path} type={file.name.includes(".") ? "." + file.name.split(".").pop()!.toLowerCase() : file.name} onShowing={setConverted} />}
      {file.kind === "text" && readable && !rawText && file.viewer === "markdown" && <MarkdownViewer text={text} />}
      {file.kind === "text" && readable && !rawText && file.viewer === "json" && <JsonViewer text={text} lines={file.name.endsWith(".jsonl")} />}
      {file.kind === "text" && (showRaw || file.viewer === "xml") && <pre className={`file-text ${wrap ? "wrap" : ""}`}>{text.split("\n").map((line, i) => <div key={i}><span className="ln">{i + 1}</span>{line || " "}</div>)}</pre>}
      {file.kind === "pdf" && <PdfViewer url={raw} />}
      {file.kind === "model" && <ModelViewer url={raw} />}
      {file.kind === "table" && <TableViewer sheets={file.sheets ?? []} />}
      {file.kind === "image" && <img className="file-image" src={file.data} alt={file.name} />}
      {file.kind === "binary" && !noViewer && <p className="hint viewer-note">{file.note ?? "This is a binary file the app doesn't show."}</p>}
      {file.kind === "too_big" && <p className="hint">This image is too big to show here.</p>}
    </div>
  );
}
