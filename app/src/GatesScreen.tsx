import { useEffect, useState } from "react";
import { call, useBusy, useProject } from "./api";
import { Rendered } from "./AgentChat";

interface Checks { stages: { stage: string; owner: string; state: string; problems: string[] }[]; departments: { name: string; held: boolean; bug: boolean; lastReport: string | null }[]; }
interface Gate { id: string; name: string; state: string; by?: string; at?: string; missing?: string[]; }
const LABEL: Record<string, [string, string]> = { approved: ["Approved", "good"], imported: ["Imported, not reviewed", "warn"], current: ["Current", "warn"], later: ["Later", ""] };

export default function GatesScreen() {
  const { projects, projectId, setProjectId, loadError } = useProject();
  const { busy, error, run } = useBusy();
  const [items, setItems] = useState<Gate[] | null>(null);
  const [open, setOpen] = useState("");
  const [report, setReport] = useState("");
  const base = `/projects/${projectId}/gates`;
  const load = async () => {
    const list = await call<Gate[]>(base);
    setItems(list);
    const current = list.find((g) => g.state === "current") ?? list[list.length - 1];
    setOpen((o) => o || current.id);
    return list;
  };
  const [checks, setChecks] = useState<Checks | null>(null);
  const loadChecks = () => call<Checks>(`/projects/${projectId}/ci`).then(setChecks).catch(() => undefined);
  useEffect(() => { if (projectId) loadChecks(); }, [projectId, items]);
  const command = (department: string, type: string) => run("Sending", async () => { const reason = type === "RECHECK" ? window.prompt("What should it look at again?", "") ?? "" : ""; await call(`/projects/${projectId}/departments/${department}/command`, "POST", { type, reason }); await loadChecks(); });
  useEffect(() => { if (projectId) { setOpen(""); setReport(""); load().catch(() => undefined); } }, [projectId]);
  useEffect(() => { if (projectId && open) call<{ text: string }>(`${base}/${open}/report`).then((r) => setReport(r.text)).catch(() => setReport("")); }, [projectId, open, items]);
  if (!projects) return <div className="page"><h1>Gates</h1><p>{loadError || "Loading…"}</p></div>;
  if (!projectId || !items) return <div className="page"><h1>Gates</h1><p className="intro">Create a project in Headquarters first.</p></div>;
  const gate = items.find((g) => g.id === open);
  const ready = gate?.state === "current" && (gate.missing?.length ?? 1) === 0;
  return (
    <div className="page">
      <div className="hq-head"><h1>Gates</h1><select aria-label="Project" value={projectId} onChange={(e) => setProjectId(e.target.value)}>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div>
      <p className="intro">Checkpoints only you can pass. The app works out what each one needs from the project itself, and each report shows where things really stand.</p>
      {error && <p className="error" role="alert">{error}</p>}
      {checks && <section className="card wide"><h3>Automatic checks</h3><p className="hint">Free, and they run only when something they depend on changes. Agents can't mark these as passed.</p>
        <ul className="status-list">{checks.stages.map((s) => <li key={s.stage}><i className={`dot ${s.state === "pass" ? "pass" : s.state === "fail" ? "fail" : "warn"}`} /><span><b>{s.stage}</b>{s.problems[0] ? `: ${s.problems[0]}` : ""}</span><em>{s.owner}</em></li>)}{checks.stages.length === 0 && <li>Nothing to check yet.</li>}</ul>
        <div className="row">{checks.departments.map((d) => <span className="decide" key={d.name}><b>{d.name}</b>{d.lastReport && <em className="pill">{d.lastReport}</em>}<button className="small" disabled={!!busy} onClick={() => command(d.name, d.held || d.bug ? "RESUME" : "HOLD")}>{d.held ? "Resume" : d.bug ? "Carry on" : "Hold"}</button><button className="small" disabled={!!busy} onClick={() => command(d.name, "RECHECK")}>Recheck</button></span>)}</div>
      </section>}
      <div className="gate-layout">
        <div className="gate-list" role="tablist" aria-label="Gates">
          {items.map((g) => (
            <button key={g.id} role="tab" aria-selected={g.id === open} className={`nav ${g.id === open ? "active" : ""}`} onClick={() => setOpen(g.id)}>
              <span className="mono">{g.id}</span> {g.name}
              <em className={`pill ${LABEL[g.state][1]}`}>{LABEL[g.state][0]}</em>
            </button>
          ))}
        </div>
        <section className="card gate-report">
          {gate?.state === "later" && <p className="hint">Earlier gates come first. This is what the report would say today.</p>}
          {gate?.by && <p className="hint">Approved by {gate.by} on {gate.at}. This page is kept as it stood then.</p>}
          {report ? <Rendered text={report} /> : <p className="hint">Loading…</p>}
          {gate?.state === "current" && (
            <div className="row">
              <button className="primary" disabled={!!busy || !ready} onClick={() => run("Approving", async () => { await call(`/projects/${projectId}/gates/${gate.id}/approve`, "POST"); setOpen(""); await load(); })}>Approve Gate {gate.id.slice(1)}</button>
              {!ready && <span className="hint">Available once nothing is blocking it.</span>}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
