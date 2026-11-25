import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { call } from "./api";
import Circuits from "./Circuits";
import { go } from "./Linked";
import { placeOf } from "./World";

interface Glance {
  requirements: { total: number; proven: number; failed: number; retest: number; not_tested: number; deviated: number; no_test: number; needs_approval: number };
  claims: { total: number; confirmed: number };
  decisions: number;
  findings: { high: number; medium: number; low: number };
  electrical: { components: number; nets: number; facts: number };
  mechanical: { parts: number; summary: string | null; ok: boolean | null; kind: string | null; picture: string | null };
  benches: { device: string; passed: number; total: number; accepted: boolean; picture: string | null }[];
  code: { devices: string[] };
  changes: number;
}

function Tile({ screen, title, children, wide }: { screen: string; title: string; children: ReactNode; wide?: boolean }) {
  const color = placeOf(screen)?.color ?? "#54d6c2";
  return (
    <button className={`glance-tile ${wide ? "wide" : ""}`} style={{ ["--c" as string]: color } as CSSProperties} onClick={() => go(screen)} aria-label={`${title}: open ${placeOf(screen)?.label ?? screen}`}>
      <span className="glance-title">{title}<i>→</i></span>
      {children}
    </button>
  );
}

/** A ring split into coloured parts, for counts out of a total. */
function Ring({ parts, total, label }: { parts: { value: number; color: string }[]; total: number; label: string }) {
  const r = 30, c = 2 * Math.PI * r;
  let offset = 0;
  return (
    <svg className="glance-ring" viewBox="0 0 80 80" aria-hidden="true">
      <circle cx="40" cy="40" r={r} className="ring-track" />
      {total > 0 && parts.map((p, i) => {
        const len = (p.value / total) * c;
        const el = <circle key={i} cx="40" cy="40" r={r} stroke={p.color} strokeDasharray={`${len} ${c - len}`} strokeDashoffset={-offset} className="ring-part" />;
        offset += len;
        return el;
      })}
      <text x="40" y="45" textAnchor="middle">{label}</text>
    </svg>
  );
}

export default function HqGlance({ projectId, version }: { projectId: string; version: number }) {
  const [g, setG] = useState<Glance | null>(null);
  useEffect(() => { call<Glance>(`/projects/${projectId}/glance`).then(setG).catch(() => undefined); }, [projectId, version]);
  if (!g) return null;
  const req = g.requirements;
  return (
    <div className="glance">
      <Tile screen="Tests" title="Requirements proven">
        <div className="glance-row">
          <Ring total={req.total} label={`${req.proven}/${req.total}`} parts={[{ value: req.proven, color: "#6fd08c" }, { value: req.failed + req.retest, color: "#ff6f61" }, { value: req.not_tested + req.needs_approval + req.no_test, color: "#3a4357" }]} />
          <span className="glance-legend"><i style={{ background: "#6fd08c" }} />proven {req.proven}<br /><i style={{ background: "#ff6f61" }} />failed {req.failed + req.retest}<br /><i style={{ background: "#3a4357" }} />to test {req.not_tested + req.needs_approval + req.no_test}</span>
        </div>
      </Tile>
      <Tile screen="Electrical" title="Circuit" wide>
        <div className="glance-circuit"><Circuits base={`/projects/${projectId}/electrical`} version={g.electrical.nets} /></div>
        <span className="glance-foot">{g.electrical.components} parts · {g.electrical.nets} connections · {g.electrical.facts} device facts</span>
      </Tile>
      {g.mechanical.picture || g.mechanical.summary ? (
        <Tile screen="Mechanical" title={g.mechanical.kind === "mobile" ? "Can it get there?" : "Will the servos hold it?"}>
          {g.mechanical.picture && <img src={`/api${g.mechanical.picture}`} alt="The MuJoCo simulation" />}
          <span className={`glance-foot ${g.mechanical.ok ? "ok" : "bad"}`}>{g.mechanical.summary}</span>
        </Tile>
      ) : (
        <Tile screen="Mechanical" title="Mechanical"><span className="glance-big">{g.mechanical.parts}</span><span className="glance-foot">parts in the list</span></Tile>
      )}
      {g.benches.map((b) => (
        <Tile key={b.device} screen="Tests" title={`Laptop bench: ${b.device}`}>
          {b.picture && <img className="pixel" src={`/api${b.picture}`} alt={`What ${b.device}'s fake screen showed`} />}
          <span className={`glance-foot ${b.passed === b.total ? "ok" : "bad"}`}>{b.passed} of {b.total} scenarios passed{b.accepted ? "" : " · not checked by you yet"}</span>
        </Tile>
      ))}
      <Tile screen="Software" title="Code">
        <span className="glance-chips">{g.code.devices.length ? g.code.devices.map((d) => <i key={d}>{d}</i>) : <em>No code yet</em>}</span>
      </Tile>
      <Tile screen="Sources" title="Research">
        <span className="glance-big">{g.claims.total}</span><span className="glance-foot">sourced claims, {g.claims.confirmed} confirmed · {g.decisions} decisions</span>
      </Tile>
      <Tile screen="Review queue" title="Verifier and changes">
        <span className="glance-dots">
          <span className="sev high"><b>{g.findings.high}</b>high</span><span className="sev medium"><b>{g.findings.medium}</b>medium</span><span className="sev low"><b>{g.findings.low}</b>low</span>
        </span>
        <span className="glance-foot">{g.changes} change requests</span>
      </Tile>
    </div>
  );
}
