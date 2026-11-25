// The SDK every tool tab is compiled against (imported as "@ba/tab"). A tab runs in a sealed frame with no network:
// it talks to the app only through these hooks, which ask the host over postMessage. The parts below follow the app's
// theme (flat grey cards, a thin outline in the department's colour, no glow) and keep everything inside the panel.
import { Component, useEffect, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";

type Pending = { res: (v: any) => void; rej: (e: Error) => void };
const waiting = new Map<number, Pending>();
let seq = 0;
if (typeof window !== "undefined") {
  window.addEventListener("message", (e) => {
    const m = e.data;
    if (!m || m.ba !== 1 || !m.reply) return;
    const p = waiting.get(m.id);
    if (!p) return;
    waiting.delete(m.id);
    if (m.ok) p.res(m.result); else p.rej(new Error(m.error || "The app couldn't answer."));
  });
}
function ask<T = any>(op: string, args: Record<string, unknown> = {}): Promise<T> {
  return new Promise((res, rej) => { const id = ++seq; waiting.set(id, { res, rej }); parent.postMessage({ ba: 1, id, op, args }, "*"); });
}

export interface TabInfo { project: { id: string; name: string }; department: string; accent: string; tab: { id: string; title: string; tool: string }; canRun: boolean; why: string; }
let info: TabInfo | null = null;
export function useTab(): TabInfo | null {
  const [v, setV] = useState(info);
  useEffect(() => { if (!v) ask<TabInfo>("hello").then((x) => { info = x; setV(x); document.documentElement.style.setProperty("--accent", x.accent); }).catch(() => undefined); }, []);
  return v;
}

export interface TabData { ok: boolean | null; output: any; images: Record<string, string>; ranAt: string | null; error?: string; warnings?: string[]; }
/** The data script's last output, shared by every part of the tab. run() runs it again in the sandbox (only if the person allowed tab scripts). */
type Shared = { data: TabData | null; running: boolean; error: string };
let shared: Shared = { data: null, running: false, error: "" };
let loaded = false;
const listeners = new Set<(s: Shared) => void>();
const publish = (next: Partial<Shared>) => { shared = { ...shared, ...next }; listeners.forEach((l) => l(shared)); };
function runData() {
  publish({ running: true, error: "" });
  ask<TabData>("run").then((data) => publish({ data, running: false })).catch((e) => publish({ error: e.message, running: false }));
}
export function useData() {
  const [s, setS] = useState(shared);
  useEffect(() => {
    listeners.add(setS);
    if (!loaded) { loaded = true; ask<TabData>("data").then((data) => publish({ data })).catch((e) => publish({ error: e.message })); }
    return () => { listeners.delete(setS); };
  }, []);
  return { data: s.data, running: s.running, error: s.error || s.data?.error || "", run: runData };
}

/** One project file, read-only. Text comes back as text; anything else as base64. */
export function useFile(path: string | null) {
  const [f, setF] = useState<{ text?: string; base64?: string; size?: number } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { setF(null); setError(""); if (path) ask("read", { path }).then(setF).catch((e) => setError(e.message)); }, [path]);
  return { file: f, error, loading: !!path && !f && !error };
}

/** Read from a connected outside service (an API or MCP server the person connected at External Service). Read
 *  operations only; the app adds the key itself. Pass params as an object; change params to fetch again. */
export function useService(connection: string, operation: string, params: Record<string, unknown> = {}) {
  const key = JSON.stringify(params);
  const [state, setState] = useState<{ data: any; error: string; loading: boolean; status?: number | string }>({ data: null, error: "", loading: true });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    setState({ data: null, error: "", loading: true });
    ask<{ ok: boolean; data: any; error?: string; status?: number | string }>("service", { connection, operation, params: JSON.parse(key) })
      .then((r) => setState({ data: r.data, error: r.ok ? "" : r.error || "The service said no.", loading: false, status: r.status }))
      .catch((e) => setState({ data: null, error: e.message, loading: false }));
  }, [connection, operation, key, tick]);
  return { ...state, refresh: () => setTick((t) => t + 1) };
}

/** Project files under a folder, such as useFiles("mechanics/exports", "*.stl"). */
export function useFiles(dir = "", pattern = "*") {
  const [list, setList] = useState<{ path: string; size: number; modified: number }[] | null>(null);
  useEffect(() => { ask("list", { dir, pattern }).then(setList).catch(() => setList([])); }, [dir, pattern]);
  return list;
}

const CSS = `
:root{--bg:#12161d;--card:#161a22;--line:#2a2f3a;--ink:#d7dbe3;--dim:#8c93a1;--accent:#a493f2;--good:#6fd08c;--warn:#f2c94c;--bad:#ff6f91}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,"Segoe UI",sans-serif;overflow-wrap:anywhere}
.ba-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(320px,100%),1fr));gap:14px;padding:2px}
.ba-card .ba-grid{grid-template-columns:repeat(auto-fit,minmax(min(130px,100%),1fr));gap:10px;padding:0}
.ba-card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;min-width:0;max-width:100%;overflow:hidden}
.ba-card:hover{border-color:var(--accent)}.ba-card.wide{grid-column:1/-1}.ba-card.resizable{resize:vertical;overflow:auto;min-height:120px}
.ba-card h3{margin:0 0 6px;font-size:15px}.ba-hint{color:var(--dim);font-size:12.5px;margin:4px 0}
.ba-stat b{font-size:22px}.ba-stat small{color:var(--dim);margin-left:4px}
.ba-pill{display:inline-block;font-size:12px;padding:1px 8px;border-radius:10px;border:1px solid currentColor;margin:2px 8px 6px 0}
.ba-pill.good{color:var(--good)}.ba-pill.warn{color:var(--warn)}.ba-pill.bad{color:var(--bad)}
.ba-table{width:100%;border-collapse:collapse;font-size:13px;display:block;overflow:auto;max-height:420px}.ba-table th,.ba-table td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}.ba-table th{white-space:nowrap;overflow-wrap:normal}
.ba-select{background:#0f1218;color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:6px 10px;font:inherit;max-width:100%}
.ba-btn{margin:2px 6px 2px 0;background:transparent;color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:6px 12px;font:inherit;cursor:pointer}.ba-btn:hover{border-color:var(--accent)}.ba-btn.primary{border-color:var(--accent);color:var(--accent)}.ba-btn:disabled{opacity:.5;cursor:default}
.ba-pic{max-width:100%;height:auto;border-radius:8px;border:1px solid var(--line);display:block}
.ba-err{color:var(--bad)}pre.ba-code{background:#0f1218;border:1px solid var(--line);border-radius:8px;padding:10px;overflow:auto;max-height:360px;white-space:pre-wrap;font:12px ui-monospace,Consolas,monospace}
`;

export function Grid({ children }: { children: ReactNode }) { return <div className="ba-grid">{children}</div>; }
export function Card({ title, hint, wide, resizable, children }: { title?: string; hint?: string; wide?: boolean; resizable?: boolean; children?: ReactNode }) {
  return <section className={`ba-card ${wide ? "wide" : ""} ${resizable ? "resizable" : ""}`}>{title && <h3>{title}</h3>}{hint && <p className="ba-hint">{hint}</p>}{children}</section>;
}
export function Stat({ label, value, unit }: { label: string; value: ReactNode; unit?: string }) { return <div className="ba-stat"><div className="ba-hint">{label}</div><b>{value}</b>{unit && <small>{unit}</small>}</div>; }
export function Status({ state, children }: { state: "good" | "warn" | "bad"; children: ReactNode }) { return <span className={`ba-pill ${state}`}>{children}</span>; }
export function Button({ onClick, primary, disabled, children }: { onClick: () => void; primary?: boolean; disabled?: boolean; children: ReactNode }) { return <button className={`ba-btn ${primary ? "primary" : ""}`} disabled={disabled} onClick={onClick}>{children}</button>; }
/** A dropdown, for choosing one of many items (files, runs, scenarios) without a wall of buttons. */
export function Choice({ options, value, onChange, label }: { options: { value: string; label: string }[]; value: string | null; onChange: (v: string) => void; label?: string }) {
  return <label className="ba-hint">{label} <select className="ba-select" value={value ?? ""} onChange={(e) => onChange(e.target.value)}>{options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select></label>;
}
export function Code({ children }: { children: string }) { return <pre className="ba-code">{children}</pre>; }
export function Table({ columns, rows }: { columns: string[]; rows: (string | number | null)[][] }) {
  return <table className="ba-table"><thead><tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr></thead><tbody>{rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j}>{v ?? ""}</td>)}</tr>)}</tbody></table>;
}
export function Picture({ src, caption }: { src?: string; caption?: string }) { return src ? <figure style={{ margin: 0 }}><img className="ba-pic" src={src} alt={caption ?? ""} />{caption && <figcaption className="ba-hint">{caption}</figcaption>}</figure> : <p className="ba-hint">No picture yet.</p>; }

/** The one data button: says when it last ran, runs again, and explains a failure in plain words. */
export function RunButton({ label = "Collect again" }: { label?: string }) {
  const tab = useTab();
  const { data, running, error, run } = useData();
  return <div><Button primary disabled={running || !tab?.canRun} onClick={run}>{running ? "Collecting…" : label}</Button>
    <p className="ba-hint">{!tab?.canRun ? tab?.why : data?.ranAt ? `Last collected ${new Date(data.ranAt).toLocaleString()}.` : "Not collected yet."}</p>{error && <p className="ba-err">{error}</p>}</div>;
}

const PALETTE = ["#5aa9ff", "#f0894b", "#6fd08c", "#f2c94c", "#c08bff", "#ff6f91"];
/** A line plot that fits its card: series of x and y values, with axis labels and a wrapping legend. */
export function Plot({ series, xLabel = "", yLabel = "", height = 260 }: { series: { name: string; x: number[]; y: number[] }[]; xLabel?: string; yLabel?: string; height?: number }) {
  const xs = series.flatMap((s) => s.x), ys = series.flatMap((s) => s.y);
  if (!xs.length) return <p className="ba-hint">No data to plot.</p>;
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const W = 600, H = height, L = 52, B = 34, sx = (v: number) => L + ((v - x0) / (x1 - x0 || 1)) * (W - L - 10), sy = (v: number) => H - B - ((v - y0) / (y1 - y0 || 1)) * (H - B - 10);
  const ticks = (a: number, b: number) => [0, 0.25, 0.5, 0.75, 1].map((t) => a + (b - a) * t);
  const fmt = (v: number) => (Math.abs(v) >= 1000 || (Math.abs(v) < 0.01 && v !== 0) ? v.toExponential(1) : +v.toFixed(2)).toString();
  return <div><svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={`${yLabel} against ${xLabel}`}>
    {ticks(y0, y1).map((v) => <g key={`y${v}`}><line x1={L} x2={W - 10} y1={sy(v)} y2={sy(v)} stroke="#2a2f3a" /><text x={L - 6} y={sy(v) + 4} fill="#8c93a1" fontSize="11" textAnchor="end">{fmt(v)}</text></g>)}
    {ticks(x0, x1).map((v) => <text key={`x${v}`} x={sx(v)} y={H - B + 16} fill="#8c93a1" fontSize="11" textAnchor="middle">{fmt(v)}</text>)}
    {series.map((s, i) => <polyline key={s.name} fill="none" stroke={PALETTE[i % PALETTE.length]} strokeWidth="2" points={s.x.map((x, k) => `${sx(x)},${sy(s.y[k])}`).join(" ")} />)}
    <text x={(L + W) / 2} y={H - 4} fill="#8c93a1" fontSize="12" textAnchor="middle">{xLabel}</text>
    <text x={12} y={(H - B) / 2} fill="#8c93a1" fontSize="12" textAnchor="middle" transform={`rotate(-90 12 ${(H - B) / 2})`}>{yLabel}</text>
  </svg><div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>{series.map((s, i) => <span key={s.name} className="ba-hint"><span style={{ color: PALETTE[i % PALETTE.length] }}>■</span> {s.name}</span>)}</div></div>;
}

class Boundary extends Component<{ children: ReactNode }, { error: string }> {
  state = { error: "" };
  static getDerivedStateFromError(e: Error) { return { error: e.message }; }
  render() { return this.state.error ? <p className="ba-err">This tab hit an error and stopped: {this.state.error}</p> : this.props.children; }
}

/** Called by the app's wrapper, never by the tab itself. */
export function mount(Tab: () => ReactNode) {
  const style = document.createElement("style");
  style.textContent = CSS;
  document.head.appendChild(style);
  const el = document.getElementById("root")!;
  createRoot(el).render(<Boundary><Tab /></Boundary>);
  new ResizeObserver(() => parent.postMessage({ ba: 1, op: "height", args: { px: Math.ceil(document.documentElement.scrollHeight) } }, "*")).observe(document.body);
}
