import { useEffect, useState, type CSSProperties } from "react";
import { call } from "./api";
import { ScreenChip } from "./Linked";

interface Mod { path: string; owner: string; blocks: number; stale: number; generated: boolean; }
interface Check { stage: string; state: string; problems: string[]; }
interface Overview {
  device: string; computer: boolean; board: string | null;
  layers: { app: Mod[]; mid: Mod[]; driver: Mod[]; other: Mod[] };
  checks: Check[];
  flash: { at: string; outcome: string; board: string | null; port: string | null; size: number | null; current: boolean } | null;
  bench: { passed: number; total: number; picture: string | null } | null;
}

const OWNER: Record<string, string> = { Software: "#5aa9ff", Electrical: "#f2c94c", Generated: "#c08bff" };
const STAGE: Record<string, [string, string]> = { Firmware: ["Build", "Software"], App: ["Code check", "Software"], Bench: ["Laptop bench", "Tests"], Interface: ["Messages", "Interface"] };
const LAYERS: [keyof Overview["layers"], string, string][] = [["app", "App", "What the device does. Software writes it."], ["mid", "Middleware", "Turns raw data into something useful. Electrical writes it."], ["driver", "Driver", "Talks to the pins and buses. Electrical writes it."], ["other", "Entry and shared", "Start-up code and generated headers."]];

function Badge({ c }: { c: Check }) {
  const [label, screen] = STAGE[c.stage] ?? [c.stage, "Software"];
  const icon = c.state === "pass" ? "✓" : c.state === "fail" ? "✗" : c.state === "waiting" ? "…" : "–";
  return (
    <div className={`sw-badge ${c.state}`} title={c.problems.join("\n") || label}>
      <span className="sw-badge-icon">{icon}</span>
      <span><b>{label}</b><small>{c.state === "pass" ? "passed" : c.state === "fail" ? "failed" : c.state === "waiting" ? "waiting to run" : c.state === "not run" ? "couldn't run" : "not set up"}</small></span>
      {screen !== "Software" && <ScreenChip screen={screen}>open</ScreenChip>}
    </div>
  );
}

function Board({ o }: { o: Overview }) {
  const f = o.flash;
  const state = !f ? "never" : f.outcome === "flash_failed" ? "failed" : f.current ? "current" : "old";
  const text = { never: "Never flashed", failed: "Last flash failed", current: "Has the current code", old: "Code changed since the last flash" }[state];
  return (
    <div className={`sw-board ${state}`}>
      <svg viewBox="0 0 220 120" aria-hidden="true">
        <rect x="6" y="10" width="208" height="100" rx="8" className="pcb" />
        {Array.from({ length: 14 }, (_, i) => <rect key={`t${i}`} x={22 + i * 13} y="16" width="6" height="6" className="pin" />)}
        {Array.from({ length: 14 }, (_, i) => <rect key={`b${i}`} x={22 + i * 13} y="98" width="6" height="6" className="pin" />)}
        <rect x="86" y="42" width="48" height="36" rx="3" className="chip" />
        <text x="110" y="64" textAnchor="middle" className="chip-text">MCU</text>
        <rect x="16" y="48" width="22" height="24" rx="2" className="usb" />
        <circle cx="180" cy="60" r="7" className={`led ${state}`} />
      </svg>
      <div><b>{o.board ?? o.device}</b><span>{text}</span>{f && <small>{new Date(f.at).toLocaleString()}{f.port ? ` · ${f.port}` : ""}{f.size ? ` · ${(f.size / 1024).toFixed(1)} KB` : ""}</small>}</div>
    </div>
  );
}

export default function SoftwareOverview({ projectId, device, version, onOpenFile }: { projectId: string; device: string; version: number; onOpenFile: (path: string) => void }) {
  const [o, setO] = useState<Overview | null>(null);
  useEffect(() => { setO(null); call<Overview>(`/projects/${projectId}/software/${device}/overview`).then(setO).catch(() => undefined); }, [projectId, device, version]);
  if (!o) return <div className="empty">Loading…</div>;
  const layered = o.layers.app.length + o.layers.mid.length + o.layers.driver.length > 0;
  return (
    <div className="sw-overview">
      <div className="sw-badges">
        {o.checks.map((c) => <Badge key={c.stage} c={c} />)}
      </div>
      <div className="sw-top">
        {!o.computer && <Board o={o} />}
        {o.bench && (
          <button className="sw-bench" onClick={() => { window.location.hash = "Tests"; }} title="Open the laptop bench on the Tests screen">
            {o.bench.picture && <img src={`/api${o.bench.picture}`} alt="What the fake screen showed on the laptop bench" />}
            <span className={o.bench.passed === o.bench.total ? "ok" : "bad"}>{o.bench.passed} of {o.bench.total} bench scenarios passed</span>
          </button>
        )}
      </div>
      <div className="sw-layers">
        {LAYERS.filter(([key]) => o.layers[key].length > 0 && (layered || key === "other")).map(([key, name, why]) => (
          <div key={key} className={`sw-layer ${key}`}>
            <div className="sw-layer-name" title={why}>{key === "other" && !layered ? "Files" : name}</div>
            <div className="sw-mods">
              {o.layers[key].map((m) => (
                <button key={m.path} className="sw-mod" style={{ ["--c" as string]: OWNER[m.owner] ?? "#8fa8c8" } as CSSProperties} onClick={() => onOpenFile(m.path)} title={`${m.path}${m.owner ? ` · ${m.owner}` : ""} · ${m.blocks} described blocks`}>
                  <span>{m.path.split("/").pop()}</span>
                  {m.stale > 0 && <i title={`${m.stale} descriptions out of date`} />}
                </button>
              ))}
            </div>
          </div>
        ))}
        {layered && <div className="sw-legend"><span><i style={{ background: OWNER.Software }} />Software</span><span><i style={{ background: OWNER.Electrical }} />Electrical</span><span><i style={{ background: OWNER.Generated }} />Generated</span><span><i className="dot" />description out of date</span></div>}
      </div>
    </div>
  );
}
