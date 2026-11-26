import { useEffect, useState } from "react";
import { call } from "./api";

// Every standing permission in one place: what this project may do on this computer, and app-wide switches.
// One-off confirmations (flashing a board, importing files) stay with the action they confirm.

interface Program { device: string; run_allowed: boolean; kind: string; }
interface View {
  project: { programs: Program[]; simulation_first: string | null; limit: number; spent: number };
  app: { tab_scripts: boolean; auto_skills: boolean };
}

export default function Settings({ projectId }: { projectId: string | null }) {
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [limit, setLimit] = useState("");
  const load = async () => { if (!projectId) return; const v = await call<View>(`/projects/${projectId}/settings`); setView(v); setLimit(v.project.limit.toFixed(2)); };
  useEffect(() => { load().catch((e) => setError((e as Error).message)); }, [projectId]);
  const run = (label: string, work: () => Promise<unknown>) => { setBusy(label); setError(""); work().then(load).catch((e) => setError((e as Error).message)).finally(() => setBusy("")); };

  if (!projectId) return <div className="page"><h1>Settings</h1><p className="intro">Choose a project with the switcher at the top left.</p></div>;
  if (!view) return <div className="page"><h1>Settings</h1><p className="hint">{error || "Loading…"}</p></div>;

  const allowRun = (p: Program, allowed: boolean) => {
    if (allowed && !window.confirm(`Let the app run the ${p.device} program's code, builds and tests on this computer? The code is agent-written and runs without asking each time, from its own folder and never through a shell. Python packages go into this project's environment.`)) return;
    run("Saving", () => call(`/projects/${projectId}/electrical/firmware/${p.device}/autotest`, "POST", { allowed }));
  };
  const simulationFirst = (on: boolean) => {
    const reason = on ? window.prompt("Why should hardware wait? Electrical and Procurement won't start, and nothing is bought, wired or flashed until you switch hardware on.", "No hardware yet") : "";
    if (on && !reason) return;
    run("Saving", () => call(`/projects/${projectId}/scope`, "POST", { simulation_first: on, reason }));
  };

  return (
    <div className="page settings">
      <h1>Settings</h1>
      <p className="intro">Everything you've allowed, in one place. Agents propose; these switches decide what may happen without asking you each time.</p>
      {error && <p className="error" role="alert">{error}</p>}
      {busy && <p className="hint">{busy}…</p>}

      <section className="card">
        <h3>This project</h3>
        <div className="setting">
          <div><b>Run code and tests</b><p className="hint">Lets the department build, test and run its program on this computer after each change, and fix what fails. Off: code only runs when you start it.</p></div>
          {view.project.programs.length === 0 ? <p className="hint">No programs yet. One appears here once Software or Simulation writes it.</p> : (
            <ul className="status-list">{view.project.programs.map((p) => (
              <li key={p.device}><label className="row"><input type="checkbox" checked={p.run_allowed} disabled={!!busy} onChange={(e) => allowRun(p, e.target.checked)} /> <span className="mono">{p.device}</span> <span className="hint">{p.kind === "simulation" ? "the Simulation department's program" : "a program on this computer"}</span></label></li>
            ))}</ul>
          )}
        </div>
        <div className="setting">
          <label className="row"><input type="checkbox" checked={!!view.project.simulation_first} disabled={!!busy} onChange={(e) => simulationFirst(e.target.checked)} /> <b>Simulation first</b></label>
          <p className="hint">{view.project.simulation_first ? `On: hardware work waits (${view.project.simulation_first}).` : "Off: departments may plan parts, wiring, firmware and buying."}</p>
        </div>
        <div className="setting">
          <b>AI spending limit</b>
          <div className="row"><span>US$</span><input type="number" min="0.5" step="0.5" value={limit} onChange={(e) => setLimit(e.target.value)} aria-label="AI spending limit in US dollars" style={{ width: 110 }} />
            <button className="small" disabled={!!busy || !(Number(limit) > 0)} onClick={() => run("Saving", () => call(`/projects/${projectId}/limit`, "POST", { usd: Number(limit) }))}>Save</button>
            <span className="hint">Spent so far: ${view.project.spent.toFixed(2)}.</span></div>
        </div>
      </section>

      <section className="card">
        <h3>The whole app</h3>
        <div className="setting">
          <label className="row"><input type="checkbox" checked={view.app.tab_scripts} disabled={!!busy} onChange={(e) => run("Saving", () => call("/tabs/scripts", "POST", { allowed: e.target.checked }))} /> <b>Let tool tabs run their data scripts</b></label>
          <p className="hint">In a sandbox: no network, nothing written into the project, and only the one program you approved for that tab. Off: tabs show only project files.</p>
        </div>
        <div className="setting">
          <label className="row"><input type="checkbox" checked={view.app.auto_skills} disabled={!!busy} onChange={(e) => run("Saving", () => call("/skills/auto", "POST", { on: e.target.checked }))} /> <b>Approve Skills automatically when they pass verification</b></label>
          <p className="hint">External Service still checks every source and an independent reviewer reads every Skill; this only skips your final click when everything passed.</p>
        </div>
      </section>
    </div>
  );
}
