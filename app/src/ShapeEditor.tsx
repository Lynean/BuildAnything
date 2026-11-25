import { useEffect, useState } from "react";

// A project's shape: the departments on its team and the gates it stops at.
// Headquarters suggests one with the concept. Only a person saves it.
export interface Gate { id: string; name: string; purpose: string; phase: string; checks: string[]; criteria: string[]; title?: string; }
export interface DeptRequest { department: string; why: string; by: string; at: string; }
export interface ShapeView {
  chosen: boolean; team: string[]; gates: Gate[]; approved: string[];
  available: { name: string; blurb: string }[];
  checks: { key: string; label: string; stage: string }[];
  templates: { id: string; name: string; purpose: string; checks: string[] }[];
  requests: DeptRequest[];
  proposal: { team: string[]; gates: Gate[]; why?: Record<string, string> } | null;
}
export interface Shape { team: string[]; gates: Gate[]; }

const STAGES = ["G0", "G1", "G2", "G3", "G4", "G5", "G6"];
const sorted = (gates: Gate[]) => [gates[0], ...gates.slice(1).map((g, i) => ({ g, i })).sort((a, b) => STAGES.indexOf(a.g.phase) - STAGES.indexOf(b.g.phase) || a.i - b.i).map((x) => x.g)];

export default function ShapeEditor({ view, start, onSave, saveLabel, busy }: { view: ShapeView; start: Shape; onSave: (shape: Shape) => void; saveLabel: string; busy: boolean }) {
  const [team, setTeam] = useState<string[]>(start.team);
  const [gates, setGates] = useState<Gate[]>(start.gates);
  const [adding, setAdding] = useState("");
  const [customName, setCustomName] = useState("");
  const [customStage, setCustomStage] = useState("G5");
  useEffect(() => { setTeam(start.team); setGates(start.gates); }, [JSON.stringify(start)]);

  const passed = (id: string) => view.approved.includes(id);
  const label = (key: string) => view.checks.find((c) => c.key === key)?.label ?? key;
  const stageName = (id: string) => view.templates.find((t) => t.id === id)?.name ?? id;
  const why = view.proposal?.why ?? {};
  const change = (id: string, patch: Partial<Gate>) => setGates((gs) => gs.map((g) => (g.id === id ? { ...g, ...patch } : g)));
  const missingStages = view.templates.filter((t) => !gates.some((g) => g.id === t.id));

  const addGate = () => {
    if (adding === "custom") {
      if (!customName.trim()) return;
      const n = Math.max(0, ...gates.filter((g) => g.id.startsWith("C")).map((g) => Number(g.id.slice(1)) || 0)) + 1;
      setGates((gs) => sorted([...gs, { id: `C${n}`, name: customName.trim(), purpose: "", phase: customStage, checks: [], criteria: [] }]));
      setCustomName("");
    } else {
      const t = view.templates.find((x) => x.id === adding);
      if (t) setGates((gs) => sorted([...gs, { id: t.id, name: t.name, purpose: t.purpose, phase: t.id, checks: [...t.checks], criteria: [] }]));
    }
    setAdding("");
  };

  return (
    <div className="shape-editor">
      <h3>Team</h3>
      <p className="hint">Only the departments this project needs now. Any of them can ask you to add another later.</p>
      <ul className="shape-team">
        {view.available.map((d) => (
          <li key={d.name}>
            <label>
              <input type="checkbox" checked={team.includes(d.name)} disabled={busy} onChange={(e) => setTeam((t) => (e.target.checked ? [...t, d.name] : t.filter((x) => x !== d.name)))} />
              <b>{d.name}</b> <span className="hint">{why[d.name] || d.blurb}</span>
            </label>
          </li>
        ))}
      </ul>

      <h3>Stages</h3>
      <p className="hint">Where the project stops for your decision. Built-in checks are worked out from the project's data. Requirements you add here, you tick off yourself.</p>
      <ol className="shape-gates">
        {gates.map((g) => (
          <li key={g.id} className={passed(g.id) ? "passed" : ""}>
            <div className="row">
              <input aria-label={`Name of ${g.id}`} value={g.name} disabled={busy || passed(g.id)} onChange={(e) => change(g.id, { name: e.target.value })} />
              <span className="hint">{g.id.startsWith("C") ? `Your own stage, during ${stageName(g.phase)}` : g.purpose}</span>
              {passed(g.id) ? <em className="pill good">Passed</em> : g.id !== "G0" && <button disabled={busy} onClick={() => setGates((gs) => gs.filter((x) => x.id !== g.id))}>Remove</button>}
            </div>
            <details>
              <summary>{g.checks.length} built-in {g.checks.length === 1 ? "check" : "checks"}, {g.criteria.length} of your own</summary>
              <ul className="shape-checks">
                {view.checks.map((c) => (
                  <li key={c.key}><label><input type="checkbox" checked={g.checks.includes(c.key)} disabled={busy || passed(g.id)} onChange={(e) => change(g.id, { checks: e.target.checked ? [...g.checks, c.key] : g.checks.filter((k) => k !== c.key) })} /> {label(c.key)}</label></li>
                ))}
              </ul>
              <label className="field">Your own requirements, one per line
                <textarea rows={3} value={g.criteria.join("\n")} disabled={busy || passed(g.id)} placeholder="The app starts from the desktop shortcut" onChange={(e) => change(g.id, { criteria: e.target.value.split("\n") })} />
              </label>
            </details>
          </li>
        ))}
      </ol>
      <div className="row">
        <select aria-label="Add a stage" value={adding} disabled={busy} onChange={(e) => setAdding(e.target.value)}>
          <option value="">Add a stage…</option>
          {missingStages.map((t) => <option key={t.id} value={t.id}>{t.name}: {t.purpose}</option>)}
          <option value="custom">Your own stage…</option>
        </select>
        {adding === "custom" && <>
          <input aria-label="Stage name" placeholder="Run" value={customName} onChange={(e) => setCustomName(e.target.value)} />
          <select aria-label="During" value={customStage} onChange={(e) => setCustomStage(e.target.value)}>{view.templates.slice(1).map((t) => <option key={t.id} value={t.id}>during {t.name}</option>)}</select>
        </>}
        {adding && <button disabled={busy} onClick={addGate}>Add</button>}
      </div>
      <div className="row">
        <button className="primary" disabled={busy} onClick={() => onSave({ team, gates: gates.map((g) => ({ ...g, criteria: g.criteria.map((c) => c.trim()).filter(Boolean) })) })}>{saveLabel}</button>
      </div>
    </div>
  );
}
