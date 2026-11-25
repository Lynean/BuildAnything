import { useEffect, useState } from "react";
import { call } from "./api";
import { Rendered } from "./AgentChat";

// A department's harness: the Skills it was given, the tools, MCP servers and APIs it can use (with a live check),
// its own agent tools, and the scripts it handed to the person to run.
interface Skill { id: string; tool: string; title: string; summary: string; status: string; sources: string[]; content: string; intact: boolean; reason?: string; automatic?: boolean; review?: { verdict: string }; audit?: { ok: boolean }; }
interface Tool { id: string; group: string; name: string; what: string; status: { ready: boolean; detail: string; version?: string }; mcp?: boolean; }
interface Handoff { id: string; tool: string; title: string; changes: string; file: string; steps: string[]; report_back: string; status: string; output?: string; at: string; }
interface Caps { department: string; skills: Skill[]; tools: Tool[]; agentTools: { name: string; what: string }[]; handoffs: Handoff[]; }

const GROUP: Record<string, string> = { cloud: "Cloud", app: "App", platform: "Platform", project: "Project" };

function HandoffCard({ projectId, h, onDone }: { projectId: string; h: Handoff; onDone: () => void }) {
  const [script, setScript] = useState("");
  const [output, setOutput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { call<{ text?: string }>(`/projects/${projectId}/files/content?path=${encodeURIComponent(h.file)}`).then((f) => setScript(f.text ?? "")).catch(() => undefined); }, [h.file]);
  const send = async (ran: boolean) => {
    setBusy(true); setError("");
    try { await call(`/projects/${projectId}/handoffs/${h.id}/report`, "POST", { ran, output }); onDone(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const download = () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([script], { type: "text/plain" })); a.download = h.file.split("/").pop()!.replace(/^ho_[a-f0-9]+_/, ""); a.click(); };
  return (
    <div className={`handoff ${h.status}`}>
      <div className="tool-head"><b>Run in {h.tool}: {h.title}</b><em className={`pill ${h.status === "done" ? "good" : h.status === "waiting" ? "warn" : "bad"}`}>{{ waiting: "Waiting for you", done: "Done", couldnt: "Couldn't run" }[h.status]}</em></div>
      <p><b>What it changes:</b> {h.changes}</p>
      <ol className="tool-steps">{h.steps.map((s) => <li key={s}>{s}</li>)}</ol>
      <div className="row"><button className="small" onClick={() => navigator.clipboard?.writeText(script)}>Copy script</button><button className="small" onClick={download}>Download {h.file.split("/").pop()!.replace(/^ho_[a-f0-9]+_/, "")}</button><span className="hint">Open it from {h.tool}; don't double-click it. Also in the file explorer at {h.file}.</span></div>
      <details><summary>The script</summary><pre className="file-text">{script}</pre></details>
      {h.status === "waiting" ? (
        <>
          <label className="field">Then send back: {h.report_back}<textarea rows={4} value={output} onChange={(e) => setOutput(e.target.value)} placeholder={`Paste what ${h.tool} showed`} /></label>
          {error && <p className="error">{error}</p>}
          <div className="row"><button className="primary" disabled={busy} onClick={() => send(true)}>{busy ? "Sending…" : "I ran it"}</button><button disabled={busy} onClick={() => send(false)}>I couldn't run it</button></div>
        </>
      ) : h.output ? <p className="hint">You sent: {h.output.slice(0, 400)}</p> : null}
    </div>
  );
}

export default function DepartmentCapabilities({ projectId, department }: { projectId: string; department: string }) {
  const [caps, setCaps] = useState<Caps | null>(null);
  const [checking, setChecking] = useState("");
  const load = () => call<Caps>(`/projects/${projectId}/departments/${department}/capabilities`).then(setCaps).catch(() => undefined);
  useEffect(() => { setCaps(null); load(); }, [projectId, department]);
  const check = async (tool: Tool) => {
    setChecking(tool.id);
    try { const status = await call<Tool["status"]>(`/services/check/${tool.id}`, "POST"); setCaps((c) => c && { ...c, tools: c.tools.map((t) => (t.id === tool.id ? { ...t, status } : t)) }); } catch { /* shown as is */ } finally { setChecking(""); }
  };
  if (!caps) return <section className="card wide" data-tab="Capability"><p className="hint">Checking what {department} can use…</p></section>;
  const waiting = caps.handoffs.filter((h) => h.status === "waiting");
  return (
    <>
      {waiting.length > 0 && <section className="card wide" data-tab="Capability"><h3>Waiting for you to run</h3>{waiting.map((h) => <HandoffCard key={h.id} projectId={projectId} h={h} onDone={load} />)}</section>}
      <section className="card wide" data-tab="Capability">
        <h3>Tools, MCP servers and APIs</h3>
        <p className="hint">What {department}'s agent can reach, checked live. Add or set up tools at Reception (External and Internal Service).</p>
        <ul className="status-list">{caps.tools.map((t) => (
          <li key={t.id}><i className={`dot ${t.status.ready ? "pass" : "warn"}`} /><span><b>{t.name}</b> <small className="dim-text">{t.mcp ? "MCP" : GROUP[t.group]}</small> {t.what}<br /><small className="dim-text">{t.status.version ? `${t.status.version}. ` : ""}{t.status.detail}</small></span>
            <button className="small" disabled={checking === t.id} onClick={() => check(t)}>{checking === t.id ? "Checking…" : "Check"}</button></li>
        ))}</ul>
      </section>
      <section className="card wide" data-tab="Capability">
        <h3>Skills</h3>
        <p className="hint">Guides for tools the person operates, written by External Service from official sources. Only approved ones reach the agent.</p>
        {caps.skills.length === 0 ? <p className="hint">None yet. When {department} needs a tool you operate yourself, such as Altium, ask External Service to write a Skill for it.</p> : caps.skills.map((s) => (
          <details key={s.id} className="skill">
            <summary><b>{s.title}</b> <small className="dim-text">for {s.tool}</small> <em className={`pill ${s.status === "approved" && s.intact ? "good" : s.status === "waiting" ? "warn" : "bad"}`}>{s.status === "approved" && !s.intact ? "edited, not used" : s.status === "waiting" ? "waiting for approval" : s.status}</em></summary>
            <p>{s.summary}</p>
            <p className="hint">{s.audit?.ok && s.review?.verdict === "pass" ? "Verified by External Service's checks and the independent reviewer. " : ""}{s.automatic ? "Approved automatically, as you chose." : s.status === "approved" ? "Approved by you." : ""}</p>
            <p className="hint">Sources: {s.sources.map((u) => <a key={u} href={u} target="_blank" rel="noreferrer">{new URL(u).hostname} </a>)}</p>
            <div className="skill-body"><Rendered text={s.content} /></div>
          </details>
        ))}
      </section>
      <section className="card wide" data-tab="Capability">
        <h3>{department}'s own tools</h3>
        <ul className="status-list">{caps.agentTools.map((t) => <li key={t.name}><span><span className="mono">{t.name}</span> <small className="dim-text">{t.what}</small></span></li>)}</ul>
      </section>
      {caps.handoffs.length > waiting.length && <section className="card wide" data-tab="Capability"><h3>Earlier hand-offs</h3>{caps.handoffs.filter((h) => h.status !== "waiting").map((h) => <HandoffCard key={h.id} projectId={projectId} h={h} onDone={load} />)}</section>}
    </>
  );
}
