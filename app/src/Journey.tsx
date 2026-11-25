import { useEffect, useState } from "react";
import { call, useBusy, useProject } from "./api";
import { Rendered } from "./AgentChat";
import ShapeEditor, { type ShapeView } from "./ShapeEditor";

interface Waiting { who: string; text: string; }
interface Criterion { index: number; text: string; met: boolean; note?: string; by?: string; }
interface Stop { id: string; name: string; stage: string; purpose: string; phase: string; state: string; by?: string; at?: string; missing?: string[]; waiting?: Waiting[]; criteria: Criterion[]; checks: { key: string; label: string }[]; }
interface Checks { stages: { stage: string; owner: string; state: string; problems: string[] }[]; departments: { name: string; held: boolean; bug: boolean; lastReport: string | null }[]; }

// Each stop on the journey, drawn as a small picture so the stage reads at a glance.
const ICONS: Record<string, string> = {
  G0: "M12 3a6 6 0 0 0-3.5 10.9V16h7v-2.1A6 6 0 0 0 12 3Zm-2.5 15h5v2h-5z",
  G1: "M10.5 3a7.5 7.5 0 1 0 4.6 13.4l4.2 4.2 1.4-1.4-4.2-4.2A7.5 7.5 0 0 0 10.5 3Zm0 2a5.5 5.5 0 1 1 0 11 5.5 5.5 0 0 1 0-11Z",
  G2: "M5 4h14v2H5zm0 5h14v2H5zm0 5h9v2H5zm0 5h6v2H5z",
  G3: "M7 7h4v4H7zm6 6h4v4h-4zM11 9h4v2h-2v2h-2zM3 3h18v18H3zm2 2v14h14V5z",
  G4: "M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z",
  G5: "M13 2 4 14h7l-1 8 9-12h-7z",
  G6: "M12 2 4 5v6c0 5 3.4 9.7 8 11 4.6-1.3 8-6 8-11V5zm-1.2 14L7 12.2l1.4-1.4 2.4 2.4 4.8-4.8L17 9.8z",
};

export default function Journey() {
  const { projects, projectId, setProjectId, loadError } = useProject();
  const { busy, error, run } = useBusy();
  const [stops, setStops] = useState<Stop[] | null>(null);
  const [open, setOpen] = useState("");
  const [report, setReport] = useState("");
  const [checks, setChecks] = useState<Checks | null>(null);
  const [simulation, setSimulation] = useState<string | null>(null);
  const [shape, setShape] = useState<ShapeView | null>(null);
  const [editing, setEditing] = useState(false);
  const base = `/projects/${projectId}/gates`;

  const load = async () => {
    call<ShapeView>(`/projects/${projectId}/shape`).then(setShape).catch(() => undefined);
    const list = await call<Stop[]>(base);
    setStops(list);
    setOpen((o) => o || (list.find((g) => g.state === "current") ?? list[list.length - 1]).id);
  };
  useEffect(() => { if (projectId) { setOpen(""); setReport(""); load().catch(() => undefined); } }, [projectId]);
  useEffect(() => { if (projectId) call<Checks>(`/projects/${projectId}/ci`).then(setChecks).catch(() => undefined); }, [projectId, stops]);
  useEffect(() => { if (projectId) call<{ simulation_first: string | null }>(`/projects/${projectId}/scope`).then((r) => setSimulation(r.simulation_first)).catch(() => undefined); }, [projectId]);
  const toggleSimulation = () => {
    const on = !simulation;
    const reason = on ? window.prompt("Why should hardware wait? Electrical and Procurement won't start, and nothing is bought, wired or flashed until you switch hardware on.", "No hardware yet") : "";
    if (on && !reason) return;
    run(on ? "Switching to simulation first" : "Switching hardware on", async () => {
      const r = await call<{ simulation_first: string | null }>(`/projects/${projectId}/scope`, "POST", { simulation_first: on, reason });
      setSimulation(r.simulation_first);
      await load();
    });
  };
  useEffect(() => { if (projectId && open) call<{ text: string }>(`${base}/${open}/report`).then((r) => setReport(r.text)).catch(() => setReport("")); }, [projectId, open, stops]);
  useEffect(() => { if (!projectId) return; const t = window.setInterval(() => load().catch(() => undefined), 8000); return () => window.clearInterval(t); }, [projectId]);

  if (!projects) return <div className="page"><h1>Journey</h1><p>{loadError || "Loading…"}</p></div>;
  if (!projectId || !stops) return <div className="page"><h1>Journey</h1><p className="intro">Create a project in Headquarters first.</p></div>;

  const index = stops.findIndex((s) => s.id === open);
  const stop = stops[index];
  const done = stops.filter((s) => s.state === "approved" || s.state === "imported").length;
  const next = stops[index + 1];
  const yours = (stop?.waiting ?? []).filter((w) => w.who === "You");
  const team = (stop?.waiting ?? []).filter((w) => w.who !== "You");
  const ready = stop?.state === "current" && (stop.missing?.length ?? 1) === 0;
  const failing = checks?.stages.filter((s) => s.state === "fail").length ?? 0;

  return (
    <div className="page">
      <div className="hq-head"><h1>Journey</h1><select aria-label="Project" value={projectId} onChange={(e) => setProjectId(e.target.value)}>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div>
      <p className="intro">From idea to a device that's proven to work. The team works through each stage on its own. You're only stopped where a decision is truly yours.</p>
      {error && <p className="error" role="alert">{error}</p>}
      <p><label title="Saves money while there's no hardware: no parts, wiring, firmware or prices"><input type="checkbox" checked={!!simulation} disabled={!!busy} onChange={toggleSimulation} /> Simulation first{simulation ? `: ${simulation}. Electrical and Procurement wait until you switch hardware on.` : " (hardware work waits until you switch it on)"}</label></p>

      <ol className="journey-track" style={{ ["--progress-ratio" as string]: String(Math.min(1, done / (stops.length - 1))) }}>
        {stops.map((s, i) => (
          <li key={s.id} className={`stop ${s.state} ${s.id === open ? "open" : ""}`}>
            <button onClick={() => setOpen(s.id)} aria-pressed={s.id === open} aria-label={`${s.stage}: ${s.purpose}`}>
              <span className="stop-dot" style={{ animationDelay: `${i * 90}ms` }}>
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d={ICONS[s.id] ?? ICONS[s.phase]} /></svg>
              </span>
              <span className="stop-name">{s.stage}</span>
              <span className="stop-purpose">{s.purpose}</span>
            </button>
          </li>
        ))}
      </ol>

      {stop && (
        <section key={stop.id} className="card journey-stage gate-report">
          <div className="stage-head">
            <div><div className="eyebrow">{`Stage ${index + 1} of ${stops.length}`}</div><h2>{stop.stage}</h2><p>{stop.purpose}.</p></div>
            <em className={`pill ${stop.state === "approved" ? "good" : stop.state === "current" ? "warn" : ""}`}>{{ approved: "Done", imported: "Imported", current: "Happening now", later: "Coming up" }[stop.state]}</em>
          </div>

          {stop.state === "current" && (
            <div className="stage-columns">
              <div className="stage-col yours">
                <h3>Waiting for you</h3>
                {yours.length === 0
                  ? <p className="hint">{team.length ? "Nothing yet. The team will bring things to you as they're ready." : "Nothing. Everything this stage needs is in place."}</p>
                  : <ul className="todo">{yours.map((w, i) => <li key={i} style={{ animationDelay: `${i * 70}ms` }}>{w.text}</li>)}</ul>}
              </div>
              <div className="stage-col team">
                <h3>The team is working on</h3>
                {team.length === 0
                  ? <p className="hint">Nothing outstanding.</p>
                  : <ul className="todo working">{team.map((w, i) => <li key={i} style={{ animationDelay: `${i * 70}ms` }}><b>{w.who}</b> {w.text}</li>)}</ul>}
              </div>
            </div>
          )}
          {stop.criteria.length > 0 && (
            <div className="stage-criteria">
              <h3>Your own requirements for this stage</h3>
              <ul className="todo">{stop.criteria.map((c) => (
                <li key={c.index}>
                  <label><input type="checkbox" checked={c.met} disabled={!!busy || stop.state !== "current"} onChange={() => {
                    const note = c.met ? "" : window.prompt(`How do you know this is met?\n\n${c.text}`, "");
                    if (!c.met && !note) return;
                    run("Saving", async () => { await call(`/projects/${projectId}/gates/${stop.id}/criteria/${c.index}`, "POST", { met: !c.met, note }); await load(); });
                  }} /> {c.text}</label>
                  {c.met && <span className="hint"> {c.by}: {c.note}</span>}
                </li>
              ))}</ul>
            </div>
          )}
          {stop.state === "approved" && <p className="hint">{stop.by} moved the project on from here on {stop.at}. The report below is kept as it stood then.</p>}
          {stop.state === "later" && <p className="hint">This comes after the current stage. The report below shows how it would look today.</p>}

          {stop.state === "current" && (
            <div className="row">
              <button className="primary" aria-label={`Approve Gate ${stop.id.slice(1)}`} disabled={!!busy || !ready} onClick={() => run("Moving on", async () => { await call(`/projects/${projectId}/gates/${stop.id}/approve`, "POST"); setOpen(""); await load(); })}>
                {next ? `Continue to ${next.stage}` : "Finish the project"}
              </button>
              {!ready && <span className="hint">Available once nothing is waiting.</span>}
            </div>
          )}

          <details className="stage-report">
            <summary>Read the full report</summary>
            {report ? <Rendered text={report} /> : <p className="hint">Loading…</p>}
          </details>
        </section>
      )}

      {shape && (
        <section className="card wide">
          <div className="hq-head"><h2>Team and stages</h2>{!editing && <button disabled={!!busy} onClick={() => setEditing(true)}>Change</button>}</div>
          <p className="hint">{shape.chosen ? "Chosen for this project." : "This project uses the full team and all seven stages."} Team: {shape.team.join(", ") || "nobody yet"}.</p>
          {shape.requests.map((r) => (
            <div key={r.department} className="note row">
              <span><b>{r.by}</b> asks to add <b>{r.department}</b>: {r.why}</span>
              <button className="primary" disabled={!!busy} onClick={() => run(`Adding ${r.department}`, async () => { setShape(await call<ShapeView>(`/projects/${projectId}/shape/departments/${r.department}`, "POST", { action: "add", reason: r.why })); await load(); })}>Add {r.department}</button>
              <button disabled={!!busy} onClick={() => { const reason = window.prompt(`Why doesn't this project need ${r.department}?`, ""); if (reason) run("Declining", async () => setShape(await call<ShapeView>(`/projects/${projectId}/shape/departments/${r.department}`, "POST", { action: "decline", reason }))); }}>Not needed</button>
            </div>
          ))}
          {editing && <ShapeEditor view={shape} busy={!!busy} start={{ team: shape.team, gates: shape.gates }} saveLabel="Save team and stages"
            onSave={(s) => run("Saving", async () => { setShape(await call<ShapeView>(`/projects/${projectId}/shape`, "POST", { ...s, reason: "Changed on the Journey screen." })); setEditing(false); setOpen(""); await load(); })} />}
        </section>
      )}

      {checks && (
        <details className="card wide stage-report">
          <summary>Automatic checks: {checks.stages.length - failing} passing{failing ? `, ${failing} failing` : ""}</summary>
          <p className="hint">Free, and they run only when something they depend on changes. Agents can't mark these as passed.</p>
          <ul className="status-list">{checks.stages.map((s) => <li key={s.stage}><i className={`dot ${s.state === "pass" ? "pass" : s.state === "fail" ? "fail" : "warn"}`} /><span><b>{s.stage}</b>{s.problems[0] ? `: ${s.problems[0]}` : ""}</span><em>{s.owner}</em></li>)}</ul>
        </details>
      )}
    </div>
  );
}
