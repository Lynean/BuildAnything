import { useEffect, useState } from "react";

interface Capability { id: string; department: string; label: string; risk: string; connector: string | null; route: string; health: string; autonomy: string; maxAutonomy: string; consequence: string | null; }
interface Connector { id: string; name: string; rung: string; health: string; detail: string; version?: string; capabilities: string[]; changes?: { added: string[]; removed: string[]; changed: string[] } | null; }
interface Provider { id: string; name: string; built: boolean; note: string; hasKey: boolean; source: string; }
interface Step { instruction: string; expect: string; primary: string; backup: string; check?: string; }
interface Option { id: string; name: string; support: string; summary: string; capabilities: string[]; steps: Step[]; }
interface View { providers: Provider[]; tiers: { tier: string; model: string }[]; capabilities: Capability[]; connectors: Connector[]; levels: string[]; catalog: Option[]; }
interface ToolRow { name: string; description: string; capability: string | null; risk: string; }

const AREA: Record<string, string> = { cad: "CAD", fea: "Structural analysis", eda: "Circuit design", spice: "Circuit simulation", fw: "Firmware", serial: "Serial", vcs: "Version control", pkg: "Packages", sim: "Simulation", cloud: "Cloud", lit: "Research papers" };

async function call<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, { method, headers: body === undefined ? undefined : { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? `The request failed (${res.status}).`);
  }
  return res.json();
}

const RISKS = ["read", "write", "deploy", "hardware", "spending"];

function ProviderCard({ provider, onView, setError }: { provider: Provider; onView: (v: View) => void; setError: (m: string) => void }) {
  const [key, setKey] = useState("");
  const [result, setResult] = useState("");
  const save = async () => {
    try {
      onView(await call<View>(`/providers/${provider.id}/key`, "POST", { key }));
      setKey(""); setResult("Saved to your system keychain.");
    } catch (e) { setError((e as Error).message); }
  };
  const check = async () => {
    setResult("Checking…");
    try { setResult((await call<{ detail: string }>(`/providers/${provider.id}/check`, "POST")).detail); } catch (e) { setResult(""); setError((e as Error).message); }
  };
  const sourceText = provider.source === "keychain" ? "Key in system keychain" : provider.source === "environment" ? "Key from environment variable" : "No key";
  return (
    <div className="provider">
      <div className="provider-head">
        <b>{provider.name}</b>
        <span className={`pill ${provider.hasKey ? "good" : ""}`}>{sourceText}</span>
      </div>
      {provider.built ? (
        <>
          <div className="row">
            <input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder={provider.hasKey ? "Replace key" : "Paste API key"} aria-label={`${provider.name} API key`} />
            <button disabled={!key} onClick={save}>Save key</button>
            <button disabled={!provider.hasKey} onClick={check}>Check</button>
            {provider.source === "keychain" && <button className="ghost" onClick={() => call<View>(`/providers/${provider.id}/key`, "DELETE").then((v) => { onView(v); setResult("Key removed."); })}>Remove</button>}
          </div>
          {result && <p className="hint" role="status">{result}</p>}
        </>
      ) : <p className="hint">{provider.note} Not available yet.</p>}
    </div>
  );
}

function AddMcp({ view, onView, setError }: { view: View; onView: (v: View) => void; setError: (m: string) => void }) {
  const [target, setTarget] = useState("");
  const [name, setName] = useState("");
  const [trusted, setTrusted] = useState(false);
  const [tools, setTools] = useState<ToolRow[] | null>(null);
  const [busy, setBusy] = useState(false);
  const inspect = async () => {
    setBusy(true);
    try { setTools((await call<{ tools: ToolRow[] }>("/connectors/mcp/inspect", "POST", { target, confirmed: trusted })).tools); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const save = async () => {
    setBusy(true);
    try {
      const mapping = Object.fromEntries((tools ?? []).map((t) => [t.name, { capability: t.capability ?? "", risk: t.risk }]));
      onView(await call<View>("/connectors/mcp", "POST", { name, target, mapping }));
      setTools(null); setTarget(""); setName(""); setTrusted(false);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const update = (index: number, patch: Partial<ToolRow>) => setTools((list) => list!.map((t, i) => (i === index ? { ...t, ...patch } : t)));
  return (
    <section className="card wide">
      <h3>Add an MCP server</h3>
      <label className="field">Command or URL<input value={target} onChange={(e) => { setTarget(e.target.value); setTools(null); setTrusted(false); }} placeholder="python C:\tools\my_server.py   or   https://example.com/mcp" /></label>
      {target.trim() && !/^https?:/i.test(target.trim()) && (
        <label className="consent">
          <input type="checkbox" checked={trusted} onChange={(e) => setTrusted(e.target.checked)} />
          <span>I trust this program. The app will run <code>{target.trim()}</code> on this computer to list its tools. It won't receive your API keys.</span>
        </label>
      )}
      {!tools && <div className="row"><button className="primary" disabled={!target.trim() || busy || (!/^https?:/i.test(target.trim()) && !trusted)} onClick={() => { if (/^https?:/i.test(target.trim())) setTrusted(true); inspect(); }}>{busy ? "Reading tools…" : "List tools"}</button></div>}
      {tools && (
        <>
          <table className="task-table">
            <thead><tr><th>Tool</th><th>Provides</th><th>Risk</th></tr></thead>
            <tbody>
              {tools.map((t, i) => (
                <tr key={t.name}>
                  <td><span className="mono">{t.name}</span><div className="dim-text small-text">{t.description}</div></td>
                  <td><select value={t.capability ?? ""} aria-label={`Capability for ${t.name}`} onChange={(e) => update(i, { capability: e.target.value || null })}><option value="">Not used</option>{view.capabilities.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}</select></td>
                  <td><select value={t.risk} aria-label={`Risk for ${t.name}`} onChange={(e) => update(i, { risk: e.target.value })}>{RISKS.map((r) => <option key={r}>{r}</option>)}</select></td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="hint">Tools set to Not used are never shown to agents.</p>
          <div className="row"><input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name this connector" aria-label="Connector name" /><button className="primary" disabled={!name.trim() || busy} onClick={save}>Approve and save</button></div>
        </>
      )}
    </section>
  );
}

function SetupGuide({ view, onView }: { view: View; onView: (v: View) => void }) {
  const [optionId, setOptionId] = useState(view.catalog[0].id);
  const [index, setIndex] = useState(0);
  const [outcome, setOutcome] = useState("");
  const [problem, setProblem] = useState("");
  const [help, setHelp] = useState("");
  const [busy, setBusy] = useState(false);
  const option = view.catalog.find((o) => o.id === optionId)!;
  const step = option.steps[index];
  const reset = () => { setOutcome(""); setHelp(""); setProblem(""); };
  const check = async () => {
    setBusy(true);
    try {
      const r = await call<{ result: { found: boolean; detail: string; version?: string }; view: View }>(`/guide/check/${step.check}`, "POST");
      onView(r.view);
      setOutcome(r.result.found ? `Found: ${r.result.version}` : r.result.detail);
    } catch (e) { setOutcome((e as Error).message); } finally { setBusy(false); }
  };
  const ask = async () => {
    setBusy(true);
    try { setHelp((await call<{ answer: string }>("/guide/help", "POST", { option: option.id, step: index, problem })).answer); }
    catch (e) { setHelp((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <section className="card wide">
      <h3>Setup Guide</h3>
      <label className="field">What do you want to connect?
        <select value={optionId} onChange={(e) => { setOptionId(e.target.value); setIndex(0); reset(); }}>{view.catalog.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</select>
      </label>
      <p className="guide-summary">{option.summary} <span className="pill">{option.support}</span></p>
      <div className="stepper">{option.steps.map((s, i) => (<button key={i} className={i === index ? "on" : i < index ? "past" : ""} onClick={() => { setIndex(i); reset(); }}><span className="mono">{i + 1}</span>Step {i + 1}</button>))}</div>
      <div className="guide-body">
        <b>{step.instruction}</b>
        <dl>
          <dt>You should see</dt><dd>{step.expect}</dd>
          <dt>Instructions</dt><dd><a href={step.primary} target="_blank" rel="noreferrer">Official source</a></dd>
          <dt>If that fails</dt><dd><a href={step.backup} target="_blank" rel="noreferrer">Backup source</a></dd>
        </dl>
        <div className="row">
          {step.check && <button className="primary" disabled={busy} onClick={check}>Check</button>}
          {index < option.steps.length - 1 && <button onClick={() => { setIndex(index + 1); reset(); }}>Next step</button>}
        </div>
        {outcome && <p className="note" role="status">{outcome}</p>}
        <div className="row">
          <input value={problem} onChange={(e) => setProblem(e.target.value)} placeholder="Stuck? Describe what happened" aria-label="Describe the setup problem" />
          <button disabled={!problem.trim() || busy} onClick={ask}>Ask for help</button>
        </div>
        <p className="hint">Asking for help uses a small amount of your AI credits. Everything else here is free.</p>
        {help && <p className="agent-help" role="status">{help}</p>}
      </div>
    </section>
  );
}

export default function ConnectorsLive() {
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [scanning, setScanning] = useState(false);
  useEffect(() => { call<View>("/connectors").then(setView).catch((e) => setError(e.message)); }, []);

  if (!view) return <div className="page"><h1>Connectors</h1>{error ? <p className="error" role="alert">The backend isn't responding. {error}</p> : <p>Loading…</p>}</div>;

  const scan = async () => {
    setScanning(true); setError(""); setNote("");
    try {
      const r = await call<{ found: string[]; view: View }>("/connectors/scan", "POST");
      setView(r.view);
      setNote(r.found.length ? `Found and connected ${r.found.length}: ${r.found.join(", ")}.` : "No known tools were found on PATH.");
    } catch (e) { setError((e as Error).message); } finally { setScanning(false); }
  };
  const setAutonomy = async (capability: string, level: string) => {
    try { setView(await call<View>("/autonomy", "POST", { capability, level })); } catch (e) { setError((e as Error).message); }
  };
  const act = async (path: string, method: string) => {
    try { setView(await call<View>(path, method)); } catch (e) { setError((e as Error).message); }
  };

  return (
    <div className="page">
      <h1>Connectors</h1>
      <p className="intro">You choose the tools and AI providers. Each one is tested before agents can use it.</p>
      {error && <p className="error" role="alert">{error}</p>}
      {note && <p className="note" role="status">{note}</p>}
      <div className="grid">
        <section className="card">
          <h3>AI providers</h3>
          {view.providers.map((p) => <ProviderCard key={p.id} provider={p} onView={setView} setError={setError} />)}
        </section>
        <section className="card">
          <h3>Model tiers</h3>
          <table><tbody>{view.tiers.map((t) => <tr key={t.tier}><td>{t.tier}</td><td className="mono">{t.model}</td></tr>)}</tbody></table>
        </section>
        <section className="card wide">
          <div className="hq-head">
            <h3>Capabilities</h3>
            <button className="primary" disabled={scanning} onClick={scan}>{scanning ? "Scanning…" : "Scan this computer"}</button>
          </div>
          <div className="cap-areas">
            {Object.entries(view.capabilities.reduce<Record<string, Capability[]>>((acc, c) => { const area = c.id.split(".")[0]; (acc[area] ||= []).push(c); return acc; }, {})).map(([area, caps]) => {
              const live = caps.filter((c) => c.health === "Passed").length;
              const tools = [...new Set(caps.map((c) => c.connector).filter(Boolean))];
              return (
                <div key={area} className={`cap-area ${live === caps.length ? "all" : live ? "some" : "none"}`}>
                  <div className="cap-area-head"><b>{AREA[area] ?? area}</b><span className="cap-meter"><i style={{ width: `${(live / caps.length) * 100}%` }} /></span><small>{live}/{caps.length}</small></div>
                  <div className="cap-tool">{tools.length ? tools.join(", ") : "Manual: agents guide you, you confirm"}</div>
                  <div className="cap-chips">
                    {caps.map((c) => (
                      <details key={c.id} className={`cap-chip ${c.health === "Passed" ? "ok" : "off"}`}>
                        <summary title={c.id}>{c.health === "Passed" ? "●" : "○"} {c.label}</summary>
                        <div className="cap-detail">
                          <span className="hint">{c.route}{c.consequence && c.connector ? ` · ${c.consequence}` : ""}</span>
                          <label className="hint">Agents may <select value={c.autonomy} aria-label={`Autonomy for ${c.label}`} onChange={(e) => setAutonomy(c.id, e.target.value)}>{view.levels.slice(0, view.levels.indexOf(c.maxAutonomy) + 1).map((l) => <option key={l}>{l}</option>)}</select></label>
                        </div>
                      </details>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </section>
        {view.connectors.length > 0 && (
          <section className="card wide">
            <h3>Connected</h3>
            <table><tbody>
              {view.connectors.map((c) => (
                <tr key={c.id}>
                  <td>{c.name}<div className="dim-text small-text">{c.version ?? c.detail}</div></td>
                  <td><span className={`pill ${c.health === "passed" ? "good" : c.health === "changed" ? "warn" : "bad"}`}>{c.health === "passed" ? "Healthy" : c.health === "changed" ? "Changed, review needed" : c.health === "failed" ? "Failed" : "Untested"}</span>{c.health !== "passed" && <div className="dim-text small-text">{c.detail}</div>}{c.changes && <ul className="tried change-list">{c.changes.added.map((t) => <li key={t}>Added: {t}</li>)}{c.changes.removed.map((t) => <li key={t}>Removed: {t}</li>)}{c.changes.changed.map((t) => <li key={t}>{t}</li>)}</ul>}</td>
                  <td>
                    <div className="decide">
                      <button className="small" onClick={() => act(`/connectors/${c.id}/check`, "POST")}>Check</button>
                      {c.health === "changed" && <button className="small" onClick={() => act(`/connectors/${c.id}/approve`, "POST")}>Re-approve</button>}
                      <button className="small" onClick={() => act(`/connectors/${c.id}`, "DELETE")}>Remove</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody></table>
          </section>
        )}
        <AddMcp view={view} onView={setView} setError={setError} />
        <SetupGuide view={view} onView={setView} />
      </div>
    </div>
  );
}
