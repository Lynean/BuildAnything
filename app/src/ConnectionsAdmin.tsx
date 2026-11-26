import { useEffect, useState } from "react";
import { call, useBusy } from "./api";

// External Service's Connections: outside services (online APIs and MCP servers) the agents can use. A connection
// waits for the person's approval, then for its keys (typed here, straight into the keyring) or a browser sign-in;
// Test proves it works. Anything an agent wants to change in a service waits here too, one request at a time.
interface Op { name: string; description: string; writes: boolean; method: string; path: string; mcp_tool: string; params: { name: string; description: string; required: boolean }[] }
interface Manifest { id: string; name: string; summary: string; kind: string; base_url: string; mcp_target: string; fields: { id: string; label: string; where: string; secret: boolean }[]; auth: { type: string; oauth?: { grant: string; scopes: string[] } }; setup: string[]; departments: string[]; operations: Op[]; check: string; sources: string[] }
interface Conn { id: string; state: "waiting" | "needs_setup" | "connected"; approved: Manifest | null; proposed: Manifest | null; missing: string[]; signedIn: boolean; last: { at: string; ok: boolean; operation: string; status?: number | string } | null }
interface Req { request: string; id: string; operation: string; params: Record<string, unknown>; why: string; department: string; project: string; by: string; at: string; status: string; result?: { ok: boolean; status?: number; summary?: string } }
interface View { connections: Conn[]; requests: Req[]; redirect: string; test?: { ok: boolean; error?: string; status?: number | string; data?: unknown }; result?: { ok: boolean; error?: string } }

const STATE: Record<Conn["state"], [string, string]> = { waiting: ["Waiting for your approval", "warn"], needs_setup: ["Needs setting up", "warn"], connected: ["Connected", "good"] };
const where = (m: Manifest) => m.kind === "mcp" ? m.mcp_target : m.base_url;

function Operations({ m }: { m: Manifest }) {
  return (
    <ul className="status-list conn-ops">{m.operations.map((o) => (
      <li key={o.name}><i className={`dot ${o.writes ? "warn" : "pass"}`} />
        <span><b className="mono">{o.name}</b> <small className="dim-text">{o.writes ? "changes something: asks you every time" : "reads"} · {o.method === "MCP" ? `MCP tool ${o.mcp_tool}` : `${o.method} ${o.path}`}</small><br /><small>{o.description}</small></span></li>
    ))}</ul>
  );
}

function Setup({ c, view, busy, run, setView }: { c: Conn; view: View; busy: string; run: (label: string, work: () => Promise<void>) => void; setView: (v: View) => void }) {
  const m = c.approved!;
  const [values, setValues] = useState<Record<string, string>>({});
  const [test, setTest] = useState<View["test"] | null>(null);
  const browser = m.auth.type === "oauth2" && m.auth.oauth?.grant === "authorization_code";
  const save = (field: string) => run("Saving", async () => { setView(await call<View>(`/connections/${m.id}/key`, "POST", { field, value: values[field] })); setValues((v) => ({ ...v, [field]: "" })); });
  return (
    <>
      <ol className="tool-steps">{m.setup.map((s) => <li key={s}>{s}</li>)}</ol>
      {browser && <p className="hint">Sign-in return address to add in the service's app settings: <code className="mono">{view.redirect}</code></p>}
      {m.fields.map((f) => (
        <div key={f.id} className="row conn-field">
          <label className="field">{f.label}{c.missing.includes(f.label) ? "" : " (saved)"}
            <input type={f.secret ? "password" : "text"} autoComplete="off" value={values[f.id] ?? ""} placeholder={c.missing.includes(f.label) ? "Paste it here" : "Saved. Paste a new one to replace it."} onChange={(e) => setValues((v) => ({ ...v, [f.id]: e.target.value }))} />
          </label>
          <button disabled={!!busy || !(values[f.id] ?? "").trim()} onClick={() => save(f.id)}>Save</button>
          {f.where && <span className="hint">From: {f.where}</span>}
        </div>
      ))}
      <div className="row">
        {browser && <button className={c.signedIn ? "" : "primary"} disabled={!!busy || c.missing.length > 0} onClick={() => run("Opening the sign-in", async () => { const { url } = await call<{ url: string }>(`/connections/${m.id}/oauth/start`); window.open(url, "_blank", "noopener"); })}>{c.signedIn ? "Sign in again" : "Sign in"}</button>}
        <button className={c.state === "connected" ? "" : "primary"} disabled={!!busy} onClick={() => run("Testing", async () => { const r = await call<View>(`/connections/${m.id}/test`, "POST"); setTest(r.test); setView(r); })}>Test</button>
        <button disabled={!!busy} onClick={() => { if (window.confirm(`Disconnect ${m.name}? Its saved keys are deleted from the keyring.`)) run("Removing", async () => setView(await call<View>(`/connections/${m.id}`, "DELETE"))); }}>Disconnect</button>
      </div>
      {test && (test.ok ? <p className="note">It works: {m.check} answered{test.status ? ` (${test.status})` : ""}.</p> : <p className="error">{test.error || `It answered ${test.status}.`}</p>)}
    </>
  );
}

export default function ConnectionsAdmin() {
  const { busy, error, run } = useBusy();
  const [view, setView] = useState<View | null>(null);
  const [openIds, setOpenIds] = useState<Record<string, boolean>>({});  // a panel stays open or shut as the person left it
  useEffect(() => {
    const load = () => call<View>("/connections").then(setView).catch(() => undefined);
    load();
    const t = window.setInterval(load, 5000);  // agents propose connections and changes while this screen is open
    window.addEventListener("ba:connections", load);
    return () => { window.clearInterval(t); window.removeEventListener("ba:connections", load); };
  }, []);
  if (!view) return null;
  const decide = (c: Conn, approve: boolean) => {
    const reason = approve ? "" : window.prompt("Why not? External Service reads this.", "") ?? "";
    if (!approve && !reason) return;
    run(approve ? "Approving" : "Rejecting", async () => setView(await call<View>(`/connections/${c.id}/decide`, "POST", { approve, reason })));
  };
  const decideRequest = (r: Req, approve: boolean) => {
    const reason = approve ? "" : window.prompt("Why not? The department reads this.", "") ?? "";
    if (!approve && !reason) return;
    run(approve ? "Sending" : "Rejecting", async () => setView(await call<View>(`/connections/requests/${r.request}/decide`, "POST", { approve, reason })));
  };
  const waitingRequests = view.requests.filter((r) => r.status === "waiting");
  return (
    <section className="card wide connections-admin">
      <h3>Connections</h3>
      <p className="hint">Outside services the agents can use: online APIs and MCP servers. Keys go straight into this computer's keyring; agents, scripts and tabs never see them. Reading runs automatically; anything that changes something in a service waits for you here.</p>
      {error && <p className="error">{error}</p>}
      {waitingRequests.map((r) => (
        <div key={r.request} className="note service-request">
          <div><b>{r.department} wants to change something in {view.connections.find((c) => c.id === r.id)?.approved?.name ?? r.id}: {r.operation}</b> <span className="hint">({r.project}). {r.why}</span></div>
          <pre className="file-text wrap">{JSON.stringify(r.params, null, 1)}</pre>
          <div className="row"><button className="primary" disabled={!!busy} onClick={() => decideRequest(r, true)}>Allow and send</button><button disabled={!!busy} onClick={() => decideRequest(r, false)}>Reject</button></div>
        </div>
      ))}
      {view.connections.length === 0 && <p className="hint">None yet. Ask External Service below, such as “connect Altium 365” or “connect GitHub so Software can read our issues”.</p>}
      {view.connections.map((c) => {
        const m = (c.proposed ?? c.approved)!;
        const [label, tone] = STATE[c.state];
        return (
          <details key={c.id} className="conn" open={openIds[c.id] ?? (c.state !== "connected" || !!c.proposed)} onToggle={(e) => { const isOpen = (e.currentTarget as HTMLDetailsElement).open; setOpenIds((o) => (o[c.id] === isOpen ? o : { ...o, [c.id]: isOpen })); }}>
            <summary><b>{m.name}</b> <em className={`pill ${tone}`}>{label}</em>{c.approved && c.proposed && <em className="pill warn">New version waiting for you</em>} <small className="dim-text">{where(m)}</small>{c.last && <small className="dim-text"> · last used {new Date(c.last.at).toLocaleString()} ({c.last.operation}, {c.last.ok ? "worked" : "failed"})</small>}</summary>
            <p>{m.summary}</p>
            <p className="hint">For {m.departments.join(", ")}. Signs in with {m.auth.type === "oauth2" ? `your account (${m.auth.oauth?.grant === "authorization_code" ? "browser sign-in" : "app ID and secret"}; access: ${m.auth.oauth?.scopes.join(" ")})` : m.auth.type === "none" ? "nothing" : "a key you paste"}.{m.sources.length > 0 && <> Sources: {m.sources.map((u) => <a key={u} href={u} target="_blank" rel="noreferrer">{new URL(u).hostname} </a>)}</>}</p>
            <Operations m={m} />
            {c.proposed && (
              <div className="row">
                <button className="primary" disabled={!!busy} onClick={() => decide(c, true)}>{c.approved ? "Approve the new version" : "Approve"}</button>
                <button disabled={!!busy} onClick={() => decide(c, false)}>Reject</button>
              </div>
            )}
            {c.approved && !c.proposed && <Setup c={c} view={view} busy={busy} run={run} setView={setView} />}
          </details>
        );
      })}
      {view.requests.some((r) => r.status !== "waiting") && (
        <details><summary>Earlier change requests</summary>
          <ul className="status-list">{view.requests.filter((r) => r.status !== "waiting").slice(0, 15).map((r) => <li key={r.request}><i className={`dot ${r.status === "approved" && r.result?.ok ? "pass" : "warn"}`} /><span>{r.department}: {r.operation} on {r.id} <small className="dim-text">{r.status}{r.result ? `, ${r.result.ok ? "done" : `failed (${r.result.status})`}` : ""}</small></span></li>)}</ul>
        </details>
      )}
    </section>
  );
}
