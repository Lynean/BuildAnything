import { useEffect, useState } from "react";
import { call, useBusy, useProject } from "./api";
import LinkIssues, { type LinkIssue } from "./LinkIssues";
import { AgentComposer } from "./AgentChat";

export type Section = "Data packets" | "Electrical" | "Mechanical" | "Link issues";
const SECTIONS: Section[] = ["Data packets", "Electrical", "Mechanical", "Link issues"];
const FAULTS: [string, string][] = [["none", "No fault"], ["drop", "Lose half the packets"], ["corrupt", "Damage some packets"], ["old_version", "Sender uses another version"], ["truncate", "Cut packets short"], ["wrong_port", "Wrong port"]];
const TYPES = ["uint8", "uint16", "uint32", "uint64", "int8", "int16", "int32", "int64", "float32", "float64", "enum8", "bytes[4]", "var_bytes"];

interface Wire { name: string; type: string; unit?: string; note?: string; const?: number; offset: number; auto?: boolean; max?: number; }
interface History { version: string; breaking: boolean; changes: string[]; reason: string; by: string; }
export interface Packet {
  name: string; id: number; version: string; transport: { type: string; port?: number }; rate_hz?: number; producer: string; consumers: string[]; waiting: string[]; acks: Record<string, string>;
  wire: Wire[]; fixed_size: number | null; max_size?: number; variable: boolean; endianness: string; checksum: boolean; chunked?: boolean; history: History[]; standard?: string; simulated: { ok: boolean; version: string } | null; fields?: unknown[];
}
interface Physical { id: string; status: string; fields: { name: string; agreed: string; between: string; kind: string }; }
export interface View { packets: Packet[]; physical: Record<string, Physical[]>; links: LinkIssue[]; drift: string[]; frozen: boolean; spent: number; limit: number; }
interface Check { ran: boolean; ok: boolean; detail: string; }
interface Report { fault: string; ok: boolean; checks: { name: string; state: string; evidence: string }[]; }
interface Row { name: string; type: string; unit: string; }

const blankRows = (): Row[] => [{ name: "", type: "uint8", unit: "" }];

function PacketForm({ onSave, onDraft, busy, nextId }: { onSave: (packet: unknown) => void; onDraft: (request: string) => void; busy: boolean; nextId: number }) {
  const [name, setName] = useState("");
  const [id, setId] = useState(String(nextId));
  const [transport, setTransport] = useState("udp");
  const [port, setPort] = useState("5005");
  const [producer, setProducer] = useState("");
  const [consumers, setConsumers] = useState("");
  const [rows, setRows] = useState<Row[]>(blankRows);
  useEffect(() => setId(String(nextId)), [nextId]);
  const update = (i: number, patch: Partial<Row>) => setRows((r) => r.map((row, n) => (n === i ? { ...row, ...patch } : row)));
  const save = () => onSave({
    name: name.trim().toUpperCase(), id: Number(id), producer: producer.trim().toLowerCase(), consumers: consumers.split(",").map((c) => c.trim().toLowerCase()).filter(Boolean),
    transport: transport === "udp" ? { type: "udp", port: Number(port) } : { type: transport },
    fields: rows.filter((r) => r.name.trim()).map((r) => ({ name: r.name.trim(), type: r.type, ...(r.unit.trim() ? { unit: r.unit.trim() } : {}), ...(r.type === "var_bytes" ? { max: 200 } : {}) })),
  });
  return (
    <section className="card wide">
      <h3>Add a packet</h3>
      <AgentComposer agent="Interface Designer" disabled={busy} onSend={onDraft} placeholder="Describe the packet, such as: the hub sends each node a target position at 5 Hz over UDP" />
      <details className="pkt-manual">
        <summary>Or define it yourself</summary>
        <div className="pkt-grid">
          <label className="field">Name<input value={name} onChange={(e) => setName(e.target.value)} placeholder="TELEMETRY" /></label>
          <label className="field">ID<input value={id} onChange={(e) => setId(e.target.value)} inputMode="numeric" /></label>
          <label className="field">Transport<select value={transport} onChange={(e) => setTransport(e.target.value)}>{["udp", "tcp", "serial", "espnow", "ble", "can", "i2c", "spi", "other"].map((t) => <option key={t}>{t}</option>)}</select></label>
          {transport === "udp" && <label className="field">Port<input value={port} onChange={(e) => setPort(e.target.value)} inputMode="numeric" /></label>}
          <label className="field">Sent by<input value={producer} onChange={(e) => setProducer(e.target.value)} placeholder="node-a" /></label>
          <label className="field">Received by<input value={consumers} onChange={(e) => setConsumers(e.target.value)} placeholder="hub, logger" /></label>
        </div>
        {rows.map((r, i) => (
          <div className="pkt-row" key={i}>
            <input aria-label="Field name" value={r.name} onChange={(e) => update(i, { name: e.target.value })} placeholder="Field name" />
            <select aria-label="Field type" value={r.type} onChange={(e) => update(i, { type: e.target.value })}>{TYPES.map((t) => <option key={t}>{t}</option>)}</select>
            <input aria-label="Unit" value={r.unit} onChange={(e) => update(i, { unit: e.target.value })} placeholder="Unit" />
          </div>
        ))}
        <div className="row">
          <button className="small" onClick={() => setRows((r) => [...r, { name: "", type: "uint8", unit: "" }])}>Add field</button>
          <button className="primary" disabled={busy || !name.trim()} onClick={save}>Save packet</button>
        </div>
      </details>
    </section>
  );
}

function PhysicalSection({ kind, rows, onAdd, onDecide, busy }: { kind: "Electrical" | "Mechanical"; rows: Physical[]; onAdd: (b: Record<string, string>) => void; onDecide: (id: string, action: "confirm" | "reject") => void; busy: boolean }) {
  const [name, setName] = useState("");
  const [agreed, setAgreed] = useState("");
  const [between, setBetween] = useState("");
  return (
    <section className="card wide">
      <h3>{kind} interfaces</h3>
      {rows.length === 0 ? <p className="hint">None yet.</p> : (
        <table>
          <thead><tr><th>Interface</th><th>Agreed values</th><th>Between</th><th>State</th></tr></thead>
          <tbody>{rows.map((i) => <tr key={i.id}><td>{i.fields.name}</td><td>{i.fields.agreed}</td><td>{i.fields.between}</td><td>{i.status === "confirmed" ? <span className="pill good">Current</span> : <span className="decide"><button className="small" onClick={() => onDecide(i.id, "confirm")}>Confirm</button><button className="small" onClick={() => onDecide(i.id, "reject")}>Reject</button></span>}</td></tr>)}</tbody>
        </table>
      )}
      <div className="pkt-grid">
        <label className="field">Name<input value={name} onChange={(e) => setName(e.target.value)} placeholder={kind === "Electrical" ? "Battery connector" : "Motor mount"} /></label>
        <label className="field">What's agreed<input value={agreed} onChange={(e) => setAgreed(e.target.value)} placeholder={kind === "Electrical" ? "XT60, 11.1 V nominal, 20 A" : "4 × M3 on a 25 mm circle"} /></label>
        <label className="field">Between<input value={between} onChange={(e) => setBetween(e.target.value)} placeholder="Frame and motor" /></label>
      </div>
      <button className="small" disabled={busy || !name.trim() || !agreed.trim() || !between.trim()} onClick={() => { onAdd({ kind, name, agreed, between }); setName(""); setAgreed(""); setBetween(""); }}>Add</button>
    </section>
  );
}

export default function Interfaces({ initial = "Data packets" }: { initial?: Section }) {
  const { projects, projectId, setProjectId, loadError } = useProject();
  const { busy, error, note, setNote, run } = useBusy();
  const [section, setSection] = useState<Section>(initial);
  const [view, setView] = useState<View | null>(null);
  const [name, setName] = useState("");
  const [fault, setFault] = useState("none");
  const [report, setReport] = useState<Report | null>(null);
  const [checks, setChecks] = useState<Record<string, Check> | null>(null);
  const [code, setCode] = useState<{ name: string; text: string } | null>(null);
  const base = projectId ? `/projects/${projectId}/interfaces` : "";

  useEffect(() => {
    if (!base) return;
    setView(null); setReport(null); setChecks(null); setCode(null);
    call<View>(base).then((v) => { setView(v); setName(v.packets[0]?.name ?? ""); });
  }, [base]);

  if (!projects) return <div className="page"><h1>Interface</h1>{loadError ? <p className="error" role="alert">{loadError}</p> : <p>Loading…</p>}</div>;
  if (!projectId) return <div className="page"><h1>Interface</h1><p className="intro">Create a project in Headquarters first.</p></div>;
  if (!view) return <div className="page"><h1>Interface</h1><p>Loading…</p></div>;

  const packet = view.packets.find((p) => p.name === name) ?? view.packets[0];
  const openLinks = view.links.filter((l) => l.status === "open").length;
  const post = (label: string, path: string, body?: unknown, after?: () => void) => run(label, async () => { setView(await call<View>(`${base}${path}`, "POST", body ?? {})); after?.(); });
  const decide = (id: string, action: "confirm" | "reject") => run("Saving", async () => { await call(`/projects/${projectId}/records/${id}/${action}`, "POST"); setView(await call<View>(base)); });
  const nextId = Math.max(0, ...view.packets.map((p) => p.id)) + 1;

  return (
    <div className="page">
      <div className="hq-head">
        <h1>Interface</h1>
        <select aria-label="Project" value={projectId} onChange={(e) => setProjectId(e.target.value)}>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
      </div>
      <p className="intro">Everything two departments or devices must agree on. Each entry is defined once, and code is generated from it.</p>
      <div className="sub-tabs" role="tablist" aria-label="Interface types">
        {SECTIONS.map((s) => <button key={s} role="tab" aria-selected={s === section} className={s === section ? "on" : ""} onClick={() => setSection(s)}>{s}{s === "Link issues" && openLinks > 0 ? ` (${openLinks})` : ""}</button>)}
      </div>
      {busy && <p className="busy" role="status">{busy}…</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {note && <p className="note" role="status">{note}</p>}
      {view.frozen && section === "Data packets" && <p className="hint">Interfaces are frozen. Breaking changes need a reason, and every device must acknowledge them again.</p>}
      {view.drift.length > 0 && section === "Data packets" && <p className="warn-text">These generated files don't match the definitions, and will be rebuilt on the next change: {view.drift.join(", ")}</p>}

      {section === "Link issues" ? <LinkIssues view={view} base={base} setView={setView} /> : section === "Data packets" ? (
        <div className="grid">
          <section className="card wide">
            <h3>Packets</h3>
            {view.packets.length === 0 ? <p className="hint">No packets yet.</p> : (
              <table>
                <thead><tr><th>ID</th><th>Packet</th><th>Direction</th><th>Rate</th><th>Bytes</th><th>State</th></tr></thead>
                <tbody>
                  {view.packets.map((p) => (
                    <tr key={p.name} className={`pick ${p.name === packet?.name ? "picked" : ""}`} onClick={() => { setName(p.name); setReport(null); }}>
                      <td className="mono">{p.id}</td>
                      <td><button className="link mono" onClick={() => setName(p.name)}>{p.name}</button></td>
                      <td>{p.producer} → {p.consumers.join(", ")}</td>
                      <td>{p.rate_hz ? `${p.rate_hz} Hz` : "On demand"}</td>
                      <td className="mono">{p.standard ? p.standard : p.variable ? `${p.fixed_size}–${p.max_size}` : p.fixed_size}</td>
                      <td><span className={`pill ${p.waiting.length ? "bad" : "good"}`}>{p.waiting.length ? "Acknowledgement needed" : "Acknowledged"}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
          {packet && (
            <>
              <section className="card">
                <h3><span className="mono">{packet.name}</span> layout, v{packet.version}</h3>
                {packet.standard ? <p>Follows {packet.standard}. No code is generated for it.</p> : (
                  <>
                    <table>
                      <thead><tr><th>Offset</th><th>Field</th><th>Type</th><th>Unit</th></tr></thead>
                      <tbody>{packet.wire.map((f) => <tr key={f.name} className={f.auto ? "dim-row" : ""}><td className="mono">{f.offset}</td><td className="mono">{f.name}</td><td className="mono">{f.type}{f.type === "var_bytes" ? ` (up to ${f.max})` : ""}</td><td>{f.auto ? "Added by the app" : f.unit ?? ""}</td></tr>)}</tbody>
                    </table>
                    <p className="hint">{packet.variable ? `${packet.fixed_size} to ${packet.max_size}` : packet.fixed_size} bytes, {packet.endianness}-endian{packet.checksum ? ", with a checksum" : ""}.</p>
                  </>
                )}
              </section>
              <section className="card">
                <h3>Who uses it</h3>
                <dl><dt>Sends</dt><dd>{packet.producer}</dd></dl>
                <ul className="status-list">
                  {packet.consumers.map((c) => {
                    const by = packet.acks[c];
                    return <li key={c}><i className={`dot ${by ? "pass" : "warn"}`} />{c}<em>{by ? `Acknowledged by ${by}` : <button className="small" disabled={!!busy} onClick={() => post("Saving", `/packets/${packet.name}/ack`, { consumer: c })}>Acknowledge</button>}</em></li>;
                  })}
                </ul>
                <p className="hint">A breaking change must be acknowledged by every device that uses it.</p>
                {packet.history.length > 1 && <details><summary>Version history</summary><ul className="tried">{[...packet.history].reverse().map((h) => <li key={h.version}><b>v{h.version}</b> by {h.by}: {h.changes.join(" ")}{h.reason ? ` Reason: ${h.reason}` : ""}</li>)}</ul></details>}
              </section>
              {!packet.standard && (
                <section className="card wide">
                  <h3>Test it</h3>
                  <p className="hint">The simulator sends real packets between simulated nodes and a hub on this computer, using the generated code. It doesn't test your devices or radio.</p>
                  <div className="row">
                    <select aria-label="Fault to simulate" value={fault} onChange={(e) => setFault(e.target.value)}>{FAULTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
                    <button className="primary" disabled={!!busy} onClick={() => run("Simulating", async () => { const r = await call<{ report: Report; view: View }>(`${base}/packets/${packet.name}/simulate`, "POST", { fault }); setReport(r.report); setView(r.view); })}>Run simulator</button>
                    <button disabled={!!busy} onClick={() => run("Checking generated code", async () => { const r = await call<{ results: Record<string, Check>; view: View }>(`${base}/check`, "POST"); setChecks(r.results); setView(r.view); })}>Check generated code</button>
                    {["ba_packets.h", "packets.py", "packets.ts"].map((f) => <button key={f} className="small" disabled={!!busy} onClick={() => run("Opening", async () => setCode(await call(`${base}/generated/${f}`)))}>{f}</button>)}
                  </div>
                  {report && <ol className="link-checks">{report.checks.map((c, i) => <li key={c.name} className={c.state}><span className="link-num mono">{i + 1}</span><div><b>{c.name}</b><p>{c.evidence}</p></div><span className={`pill ${c.state === "pass" ? "good" : c.state === "fail" ? "bad" : ""}`}>{c.state === "pass" ? "Passed" : c.state === "fail" ? "Failed here" : "Not reached"}</span></li>)}</ol>}
                  {checks && <ul className="status-list">{Object.entries(checks).map(([lang, c]) => <li key={lang}><i className={`dot ${c.ok ? "pass" : c.ran ? "fail" : "warn"}`} />{lang}<em>{c.detail.slice(0, 200)}</em></li>)}</ul>}
                  {code && <details open><summary className="mono">{code.name}</summary><pre className="generated-code" tabIndex={0}>{code.text}</pre></details>}
                </section>
              )}
            </>
          )}
          <PacketForm busy={!!busy} nextId={nextId}
            onSave={(p) => post("Saving", "/packets", { packet: p }, () => setNote("Saved. C, Python and TypeScript were generated."))}
            onDraft={(request) => run("The agent is drafting a packet", async () => { const r = await call<{ made: string; view: View }>(`${base}/packets/draft`, "POST", { request }); setView(r.view); setName(r.made); setNote(`${r.made} drafted. Check it, then acknowledge it for each device.`); })} />
        </div>
      ) : <PhysicalSection kind={section} rows={view.physical[section] ?? []} busy={!!busy} onDecide={decide} onAdd={(b) => post("Saving", "/physical", b)} />}
    </div>
  );
}
