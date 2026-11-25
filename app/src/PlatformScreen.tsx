import { useEffect, useState } from "react";
import { call, useProject } from "./api";
import { ConverterApproval, type Converter } from "./Converted";

// What the app itself can do with this project's files: every file type it contains, and which ones it can open.
interface Format { type: string; count: number; viewer: string; example: string; }
interface Formats { types: Format[]; unsupported: Format[]; viewers: string[]; converters: Converter[]; }

const VIEWER_NAMES: Record<string, string> = {
  text: "Text and code", markdown: "Markdown (formatted)", json: "JSON tree", xml: "XML (indented)", table: "Table (CSV, TSV, Excel)",
  pdf: "PDF", model: "3D mesh (STL)", image: "Image", binary: "Binary, not shown", unknown: "No viewer yet", converter: "Platform converter",
};

export default function PlatformScreen() {
  const { projectId } = useProject();
  const [formats, setFormats] = useState<Formats | null>(null);
  const [error, setError] = useState("");
  const load = () => { if (projectId) call<Formats>(`/projects/${projectId}/files/formats`).then(setFormats).catch((e) => setError((e as Error).message)); };
  useEffect(load, [projectId]);

  return (
    <div className="page">
      <h1>Platform</h1>
      <p className="intro">What the app itself can open and show. Every file the departments write appears in the file explorer. For a kind of file with no viewer, open it there and ask Platform: its agent writes a converter, tests it on that file, and you approve it once for every project.</p>
      {error && <p className="error">{error}</p>}
      {!formats ? <p className="hint">Loading…</p> : (
        <div className="grid">
          <section className="card">
            <h3>File types in this project</h3>
            <table className="task-table">
              <thead><tr><th>Type</th><th>Files</th><th>Opens as</th><th>Example</th></tr></thead>
              <tbody>{formats.types.map((t) => (
                <tr key={t.type} className={t.viewer === "unknown" ? "warn-row" : ""}>
                  <td className="mono nowrap">{t.type}</td><td className="nowrap">{t.count}</td>
                  <td>{t.viewer === "unknown" ? <span className="pill warn">{VIEWER_NAMES.unknown}</span> : VIEWER_NAMES[t.viewer] ?? t.viewer}</td>
                  <td className="mono dim-text">{t.example}</td>
                </tr>
              ))}</tbody>
            </table>
          </section>
          <section className="card">
            <h3>Viewers the app has</h3>
            <ul className="status-list">{formats.viewers.filter((v) => v !== "binary").map((v) => <li key={v}><i className="dot pass" /><span>{VIEWER_NAMES[v] ?? v}</span></li>)}</ul>
            <h3 style={{ marginTop: 18 }}>Converters Platform wrote</h3>
            {formats.converters.length === 0 ? <p className="hint">None yet.</p> : formats.converters.map((c) => c.status === "proposed"
              ? <ConverterApproval key={c.type} converter={c} onDecided={load} />
              : <div key={c.type} className="status-list"><li><i className={`dot ${c.status === "approved" ? "pass" : "fail"}`} /><span><b className="mono">{c.type}</b> {c.status === "approved" ? `approved by ${c.approved_by}` : `rejected: ${c.reason}`}. {c.summary}</span></li></div>)}
            <h3 style={{ marginTop: 18 }}>Without a viewer</h3>
            {formats.unsupported.length === 0 ? <p className="hint">Every file type in this project can be opened.</p> : (
              <p className="hint">{formats.unsupported.map((t) => `${t.type} (${t.count})`).join(", ")}. These open as "no viewer yet" in the file explorer. Compiled build files (.o, .elf, .bin) aren't meant to be read.</p>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
