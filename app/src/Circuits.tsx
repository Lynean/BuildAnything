import { useEffect, useState } from "react";
import { call } from "./api";

interface Pin { name: string; net: string; flag: string | null; side: "left" | "right"; y: number; draft: boolean; }
interface Part { id: string; ref: string; label: string; part: string; role: string; status: string; datasheet: string; x: number; y: number; w: number; h: number; pins: Pin[]; flagged: boolean; problems: string[]; }
interface Wire { net: string; kind: string; draft: boolean; volts: number | null; trunk: number; path: string; labelY: number; }
interface Port { id: string; net: string; label: string; to: string; toName: string; x: number; y: number; h: number; }
interface SubCircuit { id: string; name: string; parts: Part[]; wires: Wire[]; ports: Port[]; connects: { id: string; name: string }[]; width: number; height: number; }
interface View { subCircuits: SubCircuit[]; unconnected: { id: string; part: string }[]; }

const STUB = 16;

function PartShape({ part, selected, onSelect }: { part: Part; selected: boolean; onSelect: () => void }) {
  return (
    <g className={`sch-part ${selected ? "sel" : ""} ${part.flagged ? "flagged" : ""}`} role="button" tabIndex={0} aria-label={`${part.ref}, ${part.part}`}
      onClick={onSelect} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(); } }}>
      <rect x={part.x} y={part.y} width={part.w} height={part.h} rx={5} />
      <text x={part.x} y={part.y - 7} className="sch-ref">{part.ref} · {part.part.slice(0, 20)}</text>
      <text x={part.x + part.w / 2} y={part.y + 22} textAnchor="middle" className="sch-label">{part.label}</text>
      {part.pins.map((p) => {
        const left = p.side === "left";
        const px = left ? part.x : part.x + part.w;
        const end = left ? px - STUB : px + STUB;
        return (
          <g key={p.name + p.net}>
            <line x1={px} x2={end} y1={p.y} y2={p.y} className="sch-stub" />
            <text x={left ? px + 7 : px - 7} y={p.y + 4} textAnchor={left ? "start" : "end"} className="sch-pin">{p.name}</text>
            {p.flag && <text x={left ? end - 5 : end + 5} y={p.y + 4} textAnchor={left ? "end" : "start"} className="sch-flag">{p.flag}</text>}
          </g>
        );
      })}
    </g>
  );
}

function PortShape({ port, onJump }: { port: Port; onJump: (id: string) => void }) {
  const { x, y, h } = port;
  const mid = y + h / 2;
  const points = `${x + 14},${y} ${x + 130},${y} ${x + 130},${y + h} ${x + 14},${y + h} ${x},${mid}`;
  return (
    <g className="sch-port" role="button" tabIndex={0} aria-label={`${port.label}, go to ${port.toName}`} onClick={() => onJump(port.to)}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onJump(port.to); } }}>
      <polygon points={points} />
      <text x={x + 72} y={mid - 3} textAnchor="middle">{port.label}</text>
      <text x={x + 72} y={mid + 12} textAnchor="middle" className="sch-to">{port.toName}</text>
    </g>
  );
}

export default function Circuits({ base, version }: { base: string; version: number }) {
  const [view, setView] = useState<View | null>(null);
  const [activeId, setActiveId] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const apply = (v: View) => { setView(v); setActiveId((id) => (v.subCircuits.some((c) => c.id === id) ? id : v.subCircuits[0]?.id ?? "")); };
  useEffect(() => { call<View>(`${base}/circuits`).then(apply).catch((e) => setError(e.message)); }, [base, version]);
  const post = (path: string, body: unknown, after?: () => void) => { setError(""); call<View>(`${base}/circuits${path}`, "POST", body).then((v) => { apply(v); after?.(); }).catch((e) => setError(e.message)); };

  if (!view) return <p className="hint">{error || "Loading…"}</p>;
  const addForm = adding ? (
    <span className="add-sub-form">
      <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Sub-circuit name" aria-label="Sub-circuit name" onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) post("/add", { name }, () => { setAdding(false); setName(""); }); }} />
      <button className="small" disabled={!name.trim()} onClick={() => post("/add", { name }, () => { setAdding(false); setName(""); })}>Add</button>
      <button className="small" onClick={() => setAdding(false)}>Cancel</button>
    </span>
  ) : <button className="add-sub" onClick={() => setAdding(true)}>+ Add sub-circuit</button>;

  if (view.subCircuits.length === 0) {
    return <div><p className="hint">Diagrams appear once there are connections. Ask the Electrical Lead to draft them from your confirmed components.</p>{view.unconnected.length > 0 && <p className="hint">Not connected yet: {view.unconnected.map((u) => u.part).join(", ")}</p>}</div>;
  }
  const circuit = view.subCircuits.find((c) => c.id === activeId) ?? view.subCircuits[0];
  const part = circuit.parts.find((p) => p.id === selected);
  const open = (id: string) => { setActiveId(id); setSelected(null); };

  return (
    <div>
      <div className="sub-tabs" role="tablist" aria-label="Sub-circuits">
        {view.subCircuits.map((c) => <button key={c.id} role="tab" aria-selected={c.id === circuit.id} className={c.id === circuit.id ? "on" : ""} onClick={() => open(c.id)}>{c.name}</button>)}
        {addForm}
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="circuit-body">
        <div className="schematic">
          {circuit.parts.length === 0 ? <p className="hint circuit-empty">Empty. Select a part in another sub-circuit and move it here.</p> : (
            <svg viewBox={`0 0 ${circuit.width} ${circuit.height}`} role="img" aria-label={`${circuit.name} schematic`}>
              {circuit.wires.map((w) => (
                <g key={w.net}>
                  <path d={w.path} className="sch-wire" strokeDasharray={w.draft ? "6 4" : undefined} style={{ stroke: "var(--signal-net)" }} />
                  <text x={w.trunk + 3} y={w.labelY} className="sch-net">{w.net}</text>
                </g>
              ))}
              {circuit.ports.map((p) => <PortShape key={p.id} port={p} onJump={open} />)}
              {circuit.parts.map((p) => <PartShape key={p.id} part={p} selected={p.id === selected} onSelect={() => setSelected(p.id)} />)}
            </svg>
          )}
        </div>
        <aside className="circuit-side">
          {part ? (
            <>
              <div className="eyebrow">{part.ref}</div>
              <h4>{part.role || part.part}</h4>
              <dl>
                <dt>Part</dt><dd>{part.part}</dd>
                <dt>Datasheet</dt><dd className={part.datasheet === "Missing" ? "warn-text" : ""}>{part.datasheet}</dd>
                <dt>Status</dt><dd>{part.status === "confirmed" ? "Confirmed" : "Needs your review"}</dd>
              </dl>
              {part.problems.length > 0 && <ul className="tried">{part.problems.map((m) => <li key={m} className="warn-text">{m}</li>)}</ul>}
              <label className="field">Move to<select value={circuit.name} onChange={(e) => post("/assign", { component: part.id, group: e.target.value }, () => setSelected(null))}>{view.subCircuits.map((c) => <option key={c.id}>{c.name}</option>)}</select></label>
            </>
          ) : (
            <>
              <div className="eyebrow">Sub-circuit</div>
              <h4>{circuit.name}</h4>
              <p>{circuit.parts.length} {circuit.parts.length === 1 ? "part" : "parts"}. Select one to see its details. Dashed wires aren't confirmed yet.</p>
            </>
          )}
          <div className="eyebrow connects">Connects to</div>
          {circuit.connects.length === 0 ? <p>Nothing. This sub-circuit stands alone.</p> : <div className="connect-list">{circuit.connects.map((c) => <button key={c.id} className="small" onClick={() => open(c.id)}>{c.name}</button>)}</div>}
          {view.unconnected.length > 0 && <><div className="eyebrow connects">Not connected yet</div><p className="hint">{view.unconnected.map((u) => u.part).join(", ")}</p></>}
        </aside>
      </div>
    </div>
  );
}
