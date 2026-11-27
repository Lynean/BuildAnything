import { useEffect, useState } from "react";
import { call } from "./api";

interface Run { name: string; modified: number; recordings: string[]; images: string[]; tables: string[]; summary: unknown; assumptions: unknown; other: string[]; }

const raw = (projectId: string, path: string, version: number) => `/api/projects/${projectId}/files/raw?path=${encodeURIComponent(path)}&v=${version}`;
const fileName = (path: string) => path.split("/").pop() ?? path;
const nice = (key: string) => key.replace(/_/g, " ");

// Every number and short value in a run's results, as rows: "straight › distance after cutoff m" = 0.41.
function rows(value: unknown, path: string[] = [], out: [string, string][] = []): [string, string][] {
  if (out.length >= 80) return out;
  if (value === null || ["string", "number", "boolean"].includes(typeof value)) {
    const text = typeof value === "number" ? String(Math.abs(value) >= 1000 || Number.isInteger(value) ? value : Number(value.toPrecision(4))) : String(value);
    if (text.length <= 140 && !/^[A-Z]:\\|^\//.test(text)) out.push([path.map(nice).join(" › "), text]);
  } else if (Array.isArray(value)) {
    if (value.length <= 3 && value.every((v) => typeof v === "number")) out.push([path.map(nice).join(" › "), value.map((v) => Number((v as number).toPrecision(4))).join(", ")]);
    else if (value.every((v) => typeof v === "string") && value.length <= 8) value.forEach((v, i) => rows(v, [...path, String(i + 1)], out));
  } else if (typeof value === "object") {
    Object.entries(value as Record<string, unknown>).forEach(([k, v]) => rows(v, [...path, k], out));
  }
  return out;
}

function assumptionList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((v) => (typeof v === "string" ? v : JSON.stringify(v)));
  if (value && typeof value === "object") return Object.entries(value as Record<string, unknown>).map(([k, v]) => `${nice(k)}: ${typeof v === "string" ? v : JSON.stringify(v)}`);
  return [];
}

// The Simulation screen leads with what the simulation did: each saved run's recording, pictures, results and assumptions.
export default function SimulationRuns({ projectId, version }: { projectId: string; version: number }) {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [picked, setPicked] = useState("");
  useEffect(() => { call<Run[]>(`/projects/${projectId}/simulation/runs`).then((r) => { setRuns(r); setPicked((p) => (r.some((x) => x.name === p) ? p : r[0]?.name ?? "")); }).catch(() => setRuns([])); }, [projectId, version]);
  if (runs === null) return <p className="hint">Loading runs…</p>;
  if (runs.length === 0) return <p className="hint">No simulation runs yet. Each run saves its results in simulation/results/&lt;run name&gt;/, with a recording, plots and its numbers, and shows here.</p>;
  const run = runs.find((r) => r.name === picked) ?? runs[0];
  const results = rows(run.summary);
  const assumed = assumptionList(run.assumptions);
  return (
    <div className="sim-runs">
      <div className="row">
        <label className="hint">Run <select aria-label="Simulation run" value={run.name} onChange={(e) => setPicked(e.target.value)}>{runs.map((r) => <option key={r.name} value={r.name}>{r.name} · {new Date(r.modified * 1000).toLocaleString()}</option>)}</select></label>
      </div>
      {run.recordings.length > 0 ? (
        <section className="card wide"><h3>Recording</h3>
          {run.recordings.map((p) => /\.gif$/i.test(p)
            ? <figure key={p}><img src={raw(projectId, p, version)} alt={`Recording of ${run.name}`} style={{ maxWidth: "100%" }} /><figcaption className="hint">{fileName(p)}</figcaption></figure>
            : <figure key={p}><video src={raw(projectId, p, version)} controls loop style={{ maxWidth: "100%" }} /><figcaption className="hint">{fileName(p)}</figcaption></figure>)}
        </section>
      ) : <p className="hint">This run saved no recording, so there's nothing to watch. Ask Simulation to save a GIF or MP4 of every run.</p>}
      {run.images.length > 0 && (
        <section className="card wide"><h3>Plots and pictures</h3>
          <div className="grid">{run.images.map((p) => <figure key={p}><img src={raw(projectId, p, version)} alt={fileName(p)} style={{ maxWidth: "100%" }} /><figcaption className="hint">{fileName(p)}</figcaption></figure>)}</div>
        </section>
      )}
      {results.length > 0 && (
        <section className="card wide"><h3>Results</h3>
          <table className="task-table"><tbody>{results.map(([k, v]) => <tr key={k}><td>{k}</td><td className="mono">{v}</td></tr>)}</tbody></table>
        </section>
      )}
      {assumed.length > 0 && (
        <section className="card wide"><h3>What this run assumed</h3>
          <ul>{assumed.map((a) => <li key={a}>{a}</li>)}</ul>
        </section>
      )}
      {(run.tables.length > 0 || run.other.length > 0) && <p className="hint">Data files: {[...run.tables, ...run.other].map(fileName).join(", ")}. Open them in Files.</p>}
    </div>
  );
}
