import { useEffect, useState, type ReactNode } from "react";
import { call, useBusy, useProject, type Summary } from "./api";
import DepartmentChat from "./DepartmentChat";
import Tabbed from "./Tabbed";
import DepartmentCapabilities from "./DepartmentCapabilities";
import ToolTab, { useToolTabs } from "./ToolTab";

interface Rec { id: string; status: string; fields: Record<string, any>; landed?: number | null; }
interface PartGroup { number: string; current: Rec; superseded: Rec[]; cad: "modelled" | "waived" | "loose" | "missing"; cadWaiver: string; }
interface Check { part: string; check: string; status: string; reason: string; evidence: string | null; }
interface Release { id: string; recipient: string; override: string; parts: { number: string; revision: string }[]; }
interface View {
  parts: PartGroup[]; checks: Check[]; releases: Release[]; imported: Rec[]; spent: number; limit: number;
  rollup: { massG: number; cost: number; missingMass: string[]; missingCost: string[]; budget: Record<string, number>; problems: string[] };
  orders: { orders: Rec[]; totals: Record<string, number> };
}

function Messages({ busy, error, note }: { busy: string; error: string; note: string }) {
  return <>{busy && <p className="busy" role="status">{busy}…</p>}{error && <p className="error" role="alert">{error}</p>}{note && <p className="note" role="status">{note}</p>}</>;
}

function Shell({ title, intro, projects, projectId, setProjectId, children }: { title: string; intro: string; projects: Summary[]; projectId: string; setProjectId: (id: string) => void; children: ReactNode }) {
  return (
    <div className="page">
      <div className="hq-head"><h1>{title}</h1><select aria-label="Project" value={projectId} onChange={(e) => setProjectId(e.target.value)}>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div>
      <p className="intro">{intro}</p>
      {children}
    </div>
  );
}

function useBuildView(projectId: string | null) {
  const [view, setView] = useState<View | null>(null);
  useEffect(() => { if (projectId) call<View>(`/projects/${projectId}/build`).then(setView); }, [projectId]);
  return { view, setView };
}

const num = (v: string) => (v.trim() === "" ? null : Number(v));

export function MechanicalLive() {
  const { projects, projectId, setProjectId, loadError } = useProject();
  const toolTabs = useToolTabs(projectId, "Mechanical");
  const { view, setView } = useBuildView(projectId);
  const [suggested, setSuggested] = useState<any[]>([]);
  useEffect(() => { if (projectId) call<any>(`/projects/${projectId}`).then((x) => setSuggested(x.records.filter((r: any) => r.type === "part_proposal" && r.status === "proposed"))); }, [projectId]);
  const { busy, error, note, setNote, run } = useBusy();
  const [form, setForm] = useState({ number: "", name: "", material: "", process: "", qty: "1", mass: "", cost: "" });
  const [revising, setRevising] = useState<string | null>(null);
  const [rev, setRev] = useState({ reason: "", material: "" });
  const [evidence, setEvidence] = useState<Record<string, string>>({});
  const [recipient, setRecipient] = useState("");
  const [override, setOverride] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [clear, setClear] = useState<{ a: string; b: string; gapMm: number; status: string }[] | null>(null);
  const [budget, setBudget] = useState({ mass: "", cost: "", currency: "SGD" });

  if (!projects || !view) return <div className="page"><h1>Mechanical</h1><p>{loadError || "Loading…"}</p></div>;
  if (!projectId) return <div className="page"><h1>Mechanical</h1><p className="intro">Create a project in Headquarters first.</p></div>;
  const base = `/projects/${projectId}`;
  const open = view.checks.filter((c) => c.status === "open");
  const stepParts = view.parts.filter((p) => /\.(step|stp)$/i.test(p.current.fields.file ?? ""));
  const set = (k: keyof typeof form, v: string) => setForm((f) => ({ ...f, [k]: v }));
  const upload = (number: string, file: File) => run(`Uploading ${file.name}`, async () => {
    const r = await call<{ view: View; measured: any; impact: any }>(`${base}/parts/${number}/file?filename=${encodeURIComponent(file.name)}`, "POST", undefined, file);
    setView(r.view);
    if (r.measured) setNote(`${number}: ${r.measured.sizeMm.join(" x ")} mm, ${r.measured.volumeCm3} cm³${r.measured.massG ? `, about ${r.measured.massG} g` : ""}.${r.impact ? ` Reopened: ${r.impact.reopened.join(", ")}.` : ""}`);
  });

  return (
    <Shell title="Mechanical" intro="Each part has one current revision. Changes reopen the checks they affect." projects={projects} projectId={projectId} setProjectId={setProjectId}>
      <Messages busy={busy} error={error} note={note} />
      <Tabbed id="mechanical" tabs={["Lead", ...toolTabs.map((t) => t.title), "Parts", "Checks", "Release", "Capability"]}>
      {toolTabs.map((t) => <div key={t.id} className="wide tool-tab" data-tab={t.title}><ToolTab projectId={projectId} projectName={projects.find((p) => p.id === projectId)?.name ?? projectId} department="Mechanical" tab={t} /></div>)}
      <div data-tab="Lead"><DepartmentChat projectId={projectId} department="Mechanical" onChange={() => { call<View>(`${base}/build`).then(setView); call<any>(base).then((x) => setSuggested(x.records.filter((r: any) => r.type === "part_proposal" && r.status === "proposed"))); }} /></div>
      {suggested.length > 0 && <section className="card wide" data-tab="Parts"><h3>Parts the Lead suggests</h3><div className="row"><button disabled={!!busy} onClick={() => run("Adding", async () => { for (const s of suggested) await call(`${base}/part-proposals/${s.id}`, "POST", { accept: true }); setView(await call<View>(`${base}/build`)); setSuggested([]); })}>Add all {suggested.length}</button></div><ul className="status-list">{suggested.map((s) => <li key={s.id}><span><b>{s.fields.number}</b> {s.fields.name}: {s.fields.material}, {s.fields.process}, qty {s.fields.qty}</span><em className="decide"><button className="small" onClick={() => run("Saving", async () => { await call(`${base}/part-proposals/${s.id}`, "POST", { accept: true }); setView(await call<View>(`${base}/build`)); setSuggested((x) => x.filter((y) => y.id !== s.id)); })}>Add</button><button className="small" onClick={() => run("Saving", async () => { await call(`${base}/part-proposals/${s.id}`, "POST", { accept: false }); setSuggested((x) => x.filter((y) => y.id !== s.id)); })}>Dismiss</button></em></li>)}</ul></section>}
      <div className="grid">
        <section className="card wide" data-tab="Parts">
          <h3>Parts</h3>
          <div className="row form-row">
            <input placeholder="Part no." aria-label="Part number" value={form.number} onChange={(e) => set("number", e.target.value)} />
            <input placeholder="Name" aria-label="Part name" value={form.name} onChange={(e) => set("name", e.target.value)} />
            <input placeholder="Material" aria-label="Material" value={form.material} onChange={(e) => set("material", e.target.value)} />
            <input placeholder="Process" aria-label="Process" value={form.process} onChange={(e) => set("process", e.target.value)} />
            <input className="narrow" placeholder="Qty" aria-label="Quantity" value={form.qty} onChange={(e) => set("qty", e.target.value)} />
            <input className="narrow" placeholder="Mass g" aria-label="Mass in grams" value={form.mass} onChange={(e) => set("mass", e.target.value)} />
            <input className="narrow" placeholder="Unit cost" aria-label="Unit cost" value={form.cost} onChange={(e) => set("cost", e.target.value)} />
            <button disabled={!form.number.trim() || !form.name.trim() || !!busy} onClick={() => run("Saving", async () => { setView(await call<View>(`${base}/parts`, "POST", { number: form.number, name: form.name, material: form.material, process: form.process, qty: Number(form.qty) || 1, mass_g: num(form.mass), unit_cost: num(form.cost) })); setForm({ number: "", name: "", material: "", process: "", qty: "1", mass: "", cost: "" }); })}>Add part</button>
          </div>
          {view.parts.length > 0 && (
            <table className="task-table"><thead><tr><th>Part</th><th>Name</th><th>Rev</th><th>Material</th><th>Qty</th><th>Mass</th><th>Cost</th><th>File</th><th>In CAD model</th><th /></tr></thead><tbody>
              {view.parts.flatMap((p) => {
                const f = p.current.fields;
                const rows = [
                  <tr key={p.current.id}>
                    <td className="mono">{p.number}</td><td>{f.name}</td><td>{f.revision}</td><td>{f.material || "–"}</td><td>{f.qty}</td><td className="mono">{f.mass_g ?? "–"}</td><td className="mono">{f.unit_cost ?? "–"}</td>
                    <td><label className="file-btn"><input type="file" accept=".step,.stp,.dxf,.stl,.pdf" aria-label={`Upload file for ${p.number}`} onChange={(e) => { const x = e.target.files?.[0]; if (x) upload(p.number, x); e.target.value = ""; }} />{f.file ? "Replace" : "Upload"}</label></td>
                    <td>{p.cad === "modelled" ? <span className="pill">Yes</span> : p.cad === "loose" ? <span className="hint">Not needed (loose item)</span> : p.cad === "waived" ? <span className="hint" title={p.cadWaiver}>Not needed: {p.cadWaiver}</span> : (
                      <><span className="pill warn">Missing</span> <button className="small" disabled={!!busy} onClick={() => { const reason = window.prompt(`Why doesn't ${p.number} need to be in the CAD model?`, ""); if (reason) run("Saving", async () => setView(await call<View>(`${base}/parts/${encodeURIComponent(p.number)}/no-model`, "POST", { reason }))); }}>Not needed</button></>
                    )}</td>
                    <td><button className="small" onClick={() => { setRevising(revising === p.number ? null : p.number); setRev({ reason: "", material: f.material ?? "" }); }}>Revise</button></td>
                  </tr>,
                ];
                if (revising === p.number) rows.push(
                  <tr key={`${p.number}-r`} className="param-row"><td colSpan={10}><div className="row">
                    <input placeholder="New material" aria-label="New material" value={rev.material} onChange={(e) => setRev((r) => ({ ...r, material: e.target.value }))} />
                    <input placeholder="Why is this changing?" aria-label="Reason for change" value={rev.reason} onChange={(e) => setRev((r) => ({ ...r, reason: e.target.value }))} />
                    <button className="primary" disabled={!rev.reason.trim() || !!busy} onClick={() => run("Saving", async () => { const r = await call<{ impact: { revision: string; reopened: string[]; alreadySentIn: string[] }; view: View }>(`${base}/parts/${p.number}/revise`, "POST", { reason: rev.reason, material: rev.material }); setView(r.view); setRevising(null); setNote(`${p.number} is now rev ${r.impact.revision}. Reopened: ${r.impact.reopened.join(", ")}.${r.impact.alreadySentIn.length ? ` The previous revision was already sent in ${r.impact.alreadySentIn.join(", ")}.` : ""}`); })}>Save revision</button>
                  </div></td></tr>,
                );
                p.superseded.forEach((s) => rows.push(<tr key={s.id} className="muted"><td className="mono">{p.number}</td><td>{s.fields.name}</td><td>{s.fields.revision}</td><td>{s.fields.material || "–"}</td><td colSpan={6}>Superseded</td></tr>));
                return rows;
              })}
            </tbody></table>
          )}
        </section>
        <section className="card" data-tab="Checks">
          <h3>Checks reopened by changes</h3>
          {open.length === 0 ? <p className="hint">Nothing is waiting.</p> : (
            <ul className="status-list">{open.map((c) => {
              const key = `${c.part}/${c.check}`;
              return <li key={key} className="check-open"><span><b>{c.part}: {c.check}</b><small>{c.reason}</small></span>
                <div className="decide"><input placeholder="What proves it?" aria-label={`Evidence for ${key}`} value={evidence[key] ?? ""} onChange={(e) => setEvidence((v) => ({ ...v, [key]: e.target.value }))} /><button className="small" disabled={!evidence[key]?.trim()} onClick={() => run("Saving", async () => setView(await call<View>(`${base}/checks/close`, "POST", { part: c.part, check: c.check, evidence: evidence[key] })))}>Close</button></div></li>;
            })}</ul>
          )}
        </section>
        <section className="card" data-tab="Checks">
          <h3>Mass and cost</h3>
          <dl><dt>Mass</dt><dd className="mono">{view.rollup.massG} g{view.rollup.budget.mass_g ? ` of ${view.rollup.budget.mass_g} g` : ""}</dd><dt>Parts cost</dt><dd className="mono">{view.rollup.cost}{view.rollup.budget.cost ? ` of ${view.rollup.budget.cost}` : ""}</dd></dl>
          {view.rollup.problems.map((p) => <p key={p} className="error">{p}</p>)}
          {view.rollup.missingMass.length > 0 && <p className="hint">No mass yet for {view.rollup.missingMass.join(", ")}.</p>}
          <div className="row"><input className="narrow" placeholder="Mass g" aria-label="Mass budget" value={budget.mass} onChange={(e) => setBudget((b) => ({ ...b, mass: e.target.value }))} /><input className="narrow" placeholder="Cost" aria-label="Cost budget" value={budget.cost} onChange={(e) => setBudget((b) => ({ ...b, cost: e.target.value }))} /><select aria-label="Budget currency" value={budget.currency} onChange={(e) => setBudget((b) => ({ ...b, currency: e.target.value }))}>{["SGD", "USD", "EUR", "GBP", "MYR", "AUD"].map((c) => <option key={c}>{c}</option>)}</select>
            <button disabled={!!busy || (!budget.mass && !budget.cost)} onClick={() => run("Saving", async () => { let v: View | null = null; if (budget.mass) v = await call<View>(`${base}/budget`, "POST", { kind: "mass_g", limit: Number(budget.mass) }); if (budget.cost) v = await call<View>(`${base}/budget`, "POST", { kind: "cost", limit: Number(budget.cost), currency: budget.currency }); if (v) setView(v); setBudget((b) => ({ ...b, mass: "", cost: "" })); })}>Set budget</button></div>
        </section>
        <section className="card" data-tab="Checks">
          <h3>Clearance check</h3>
          {stepParts.length < 2 ? <p className="hint">Upload at least two STEP files, exported in assembly position.</p> : <>
            <div className="chips">{stepParts.map((p) => <label key={p.number} className="chip"><input type="checkbox" checked={chosen.includes(p.number)} onChange={(e) => setChosen((c) => (e.target.checked ? [...c, p.number] : c.filter((x) => x !== p.number)))} /> {p.number}</label>)}</div>
            <div className="row"><button disabled={chosen.length < 2 || !!busy} onClick={() => run("Measuring", async () => setClear((await call<{ results: typeof clear }>(`${base}/clearance`, "POST", { parts: chosen })).results))}>Check clearance</button></div>
            {clear && <ul className="status-list">{clear.map((r) => <li key={r.a + r.b}><i className={`dot ${r.status === "ok" ? "pass" : r.status === "tight" ? "warn" : "fail"}`} />{r.a} to {r.b}<em>{r.status === "clash" ? "Clash" : `${r.gapMm} mm`}</em></li>)}</ul>}
          </>}
        </section>
        <section className="card" data-tab="Release">
          <h3>Release</h3>
          <input className="full" placeholder="Who is this for?" aria-label="Recipient" value={recipient} onChange={(e) => setRecipient(e.target.value)} />
          {open.length > 0 && <input className="full gap" placeholder={`${open.length} checks open. Why release anyway?`} aria-label="Reason to release with open checks" value={override} onChange={(e) => setOverride(e.target.value)} />}
          <div className="row"><button className="primary" disabled={!recipient.trim() || !view.parts.length || !!busy || (open.length > 0 && !override.trim())} onClick={() => run("Creating pack", async () => { setView((await call<{ view: View }>(`${base}/releases`, "POST", { recipient, override })).view); setRecipient(""); setOverride(""); })}>Create fabrication pack</button></div>
          {view.releases.length > 0 && <ul className="status-list">{view.releases.map((r) => <li key={r.id}><a className="page-link" href={`/api${base}/releases/${r.id}/file`}>{r.id}</a> {r.recipient}<em>{r.parts.length} parts{r.override ? ", open checks" : ""}</em></li>)}</ul>}
        </section>
      </div>
      <DepartmentCapabilities projectId={projectId} department="Mechanical" />
      </Tabbed>
    </Shell>
  );
}

export function ProcurementLive() {
  const { projects, projectId, setProjectId, loadError } = useProject();
  const toolTabs = useToolTabs(projectId, "Procurement");
  const { view, setView } = useBuildView(projectId);
  const { busy, error, note, setNote, run } = useBusy();
  const empty = { supplier: "", items: "", buyer: "", funding: "", order_date: "", currency: "SGD", total: "", duties: "" };
  const [form, setForm] = useState(empty);
  const [people, setPeople] = useState("");
  const [preview, setPreview] = useState<{ text: string; clean: boolean; hasText: boolean } | null>(null);
  const [cost, setCost] = useState({ limit: "", currency: "SGD" });
  if (!projects || !view) return <div className="page"><h1>Procurement</h1><p>{loadError || "Loading…"}</p></div>;
  if (!projectId) return <div className="page"><h1>Procurement</h1><p className="intro">Create a project in Headquarters first.</p></div>;
  const base = `/projects/${projectId}`;
  const names = people.split(",").map((p) => p.trim()).filter(Boolean);
  const set = (k: keyof typeof empty, v: string) => setForm((f) => ({ ...f, [k]: v }));
  const onInvoice = (file: File) => run("Removing personal details", async () => setPreview(await call(`${base}/invoices/preview?people=${encodeURIComponent(people)}`, "POST", undefined, file)));
  return (
    <Shell title="Procurement" intro="Log orders when you place them. Missing details are flagged straight away." projects={projects} projectId={projectId} setProjectId={setProjectId}>
      <Messages busy={busy} error={error} note={note} />
      <Tabbed id="procurement" tabs={["Lead", ...toolTabs.map((t) => t.title), "Orders", "Budget", "Capability"]}>
      {toolTabs.map((t) => <div key={t.id} className="wide tool-tab" data-tab={t.title}><ToolTab projectId={projectId} projectName={projects.find((p) => p.id === projectId)?.name ?? projectId} department="Procurement" tab={t} /></div>)}
      <div data-tab="Lead"><DepartmentChat projectId={projectId} department="Procurement" /></div>
      <div className="grid">
        <section className="card wide" data-tab="Budget">
          <h3>Parts budget</h3>
          <p className="hint">{view.rollup.budget.cost ? `Set at ${view.rollup.budget.cost}. Parts so far: ${view.rollup.cost}.` : "Not set yet. The Check stage needs one before anything is bought."}</p>
          <div className="row"><input className="narrow" placeholder="Cost" aria-label="Parts budget" value={cost.limit} onChange={(e) => setCost((c) => ({ ...c, limit: e.target.value }))} />
            <select aria-label="Budget currency" value={cost.currency} onChange={(e) => setCost((c) => ({ ...c, currency: e.target.value }))}>{["SGD", "USD", "EUR", "GBP", "MYR", "AUD"].map((c) => <option key={c}>{c}</option>)}</select>
            <button disabled={!!busy || !cost.limit} onClick={() => run("Saving", async () => { setView(await call<View>(`${base}/budget`, "POST", { kind: "cost", limit: Number(cost.limit), currency: cost.currency })); setCost((c) => ({ ...c, limit: "" })); })}>Set budget</button></div>
        </section>
        <section className="card wide" data-tab="Orders">
          <h3>Orders</h3>
          <div className="row form-row">
            <input placeholder="Supplier" aria-label="Supplier" value={form.supplier} onChange={(e) => set("supplier", e.target.value)} />
            <input placeholder="What was bought" aria-label="Items" value={form.items} onChange={(e) => set("items", e.target.value)} />
            <input placeholder="Buyer" aria-label="Buyer" value={form.buyer} onChange={(e) => set("buyer", e.target.value)} />
            <input placeholder="Funding source" aria-label="Funding source" value={form.funding} onChange={(e) => set("funding", e.target.value)} />
            <input className="narrow" type="date" aria-label="Order date" value={form.order_date} onChange={(e) => set("order_date", e.target.value)} />
            <input className="narrow" placeholder="Currency" aria-label="Currency" value={form.currency} onChange={(e) => set("currency", e.target.value)} />
            <input className="narrow" placeholder="Total" aria-label="Total" value={form.total} onChange={(e) => set("total", e.target.value)} />
            <input className="narrow" placeholder="Duties" aria-label="Duties" value={form.duties} onChange={(e) => set("duties", e.target.value)} />
            <button disabled={!form.supplier.trim() || !!busy} onClick={() => run("Saving", async () => { setView(await call<View>(`${base}/orders`, "POST", { ...form, total: num(form.total), duties: num(form.duties) })); setForm(empty); })}>Add order</button>
          </div>
          {view.orders.orders.length > 0 && (
            <table className="task-table"><thead><tr><th>Supplier</th><th>Items</th><th>Buyer</th><th>Landed cost</th><th>Status</th><th>Flags</th></tr></thead><tbody>
              {view.orders.orders.map((o) => <tr key={o.id}><td>{o.fields.supplier || "Unknown"}</td><td>{o.fields.items}</td><td>{o.fields.buyer || "–"}</td><td className="mono">{o.landed ?? "–"} {o.fields.currency}</td><td>{o.status === "proposed" ? <div className="decide"><button className="small" onClick={() => run("Saving", async () => { await call(`${base}/records/${o.id}/confirm`, "POST"); setView(await call<View>(`${base}/build`)); })}>Confirm</button></div> : <span className="decide"><span className={`pill ${o.fields.status === "Arrived" ? "good" : "warn"}`}>{o.fields.status}</span>{o.fields.status !== "Arrived" && <button className="small" onClick={() => run("Saving", async () => { await call(`${base}/orders/${o.id}/arrived`, "POST"); setView(await call<View>(`${base}/build`)); })}>Arrived</button>}</span>}</td><td className="flag">{o.fields.flags.join("; ")}</td></tr>)}
            </tbody></table>
          )}
          <p className="hint">Confirmed total: {Object.entries(view.orders.totals).map(([c, v]) => `${v} ${c}`).join(", ") || "none yet"}.</p>
        </section>
        <section className="card wide" data-tab="Orders">
          <h3>Read an invoice</h3>
          <label className="field">Names to remove, separated by commas<input value={people} onChange={(e) => setPeople(e.target.value)} placeholder="Everyone who may appear on your invoices" /></label>
          <div className="row"><label className="file-btn"><input type="file" accept="application/pdf,.pdf" aria-label="Upload invoice" onChange={(e) => { const f = e.target.files?.[0]; if (f) onInvoice(f); e.target.value = ""; }} />Choose invoice PDF</label><span className="hint">Nothing leaves this computer until you approve the text below.</span></div>
          {preview && <>
            <div className="eyebrow connects">This is exactly what would be sent</div>
            <pre className="preview">{preview.text || "No text was found. This may be a scan."}</pre>
            {!preview.clean && <p className="error">Personal details may remain, so this can't be sent.</p>}
            <div className="row"><button className="primary" disabled={!preview.clean || !preview.text || !!busy} onClick={() => run("Reading invoice", async () => { setView(await call<View>(`${base}/invoices/read`, "POST", { text: preview.text, people: names })); setPreview(null); setNote("Added as an unconfirmed order. Check it, then confirm."); })}>Send this text</button><button onClick={() => setPreview(null)}>Don't send</button></div>
          </>}
        </section>
      </div>
      <DepartmentCapabilities projectId={projectId} department="Procurement" />
      </Tabbed>
    </Shell>
  );
}

const GATE_CHOICES = [["G1", "Concepts"], ["G2", "Baseline"], ["G3", "Interface freeze"], ["G4", "Design review"], ["G5", "Build"], ["G6", "Test"]];

export function ImportLive() {
  const { projects, projectId, setProjectId, refresh } = useProject();
  const { busy, error, note, setNote, run } = useBusy();
  const [step, setStep] = useState(0);
  const [folder, setFolder] = useState("");
  const [scan, setScan] = useState<any>(null);
  const [gaps, setGaps] = useState<{ level: string; text: string; effect: string }[]>([]);
  const [files, setFiles] = useState<string[]>([]);
  const [people, setPeople] = useState("");
  const [preview, setPreview] = useState<{ items: { file: string; text: string; clean: boolean }[]; estimatedUsd: number } | null>(null);
  const [name, setName] = useState("");
  const [items, setItems] = useState<Rec[]>([]);
  const [gate, setGate] = useState("G5");
  const names = people.split(",").map((p) => p.trim()).filter(Boolean);
  const STEPS = ["Choose project", "Local scan", "Cost and privacy", "Draft record", "Gap report", "Resume"];
  const base = `/projects/${projectId}`;
  const reload = async () => setItems((await call<View>(`${base}/build`)).imported);

  return (
    <div className="page">
      <h1>Import project</h1>
      <p className="intro">Start from existing work. Your original files are only ever read.</p>
      <Messages busy={busy} error={error} note={note} />
      <div className="stepper import-steps">{STEPS.map((s, i) => <button key={s} className={i === step ? "on" : i < step ? "past" : ""} disabled={i > step} onClick={() => setStep(i)}><span className="mono">{i + 1}</span>{s}</button>)}</div>
      <section className="card import-card">
        {step === 0 && <>
          <h3>Where is the project?</h3>
          <label className="field">Folder on this computer<input value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="C:\Projects\my-robot" /></label>
          <div className="row"><button className="primary" disabled={!folder.trim() || !!busy} onClick={() => run("Scanning", async () => { const r = await call<any>("/import/scan", "POST", { folder }); setScan(r.scan); setGaps(r.gaps); setFiles(r.scan.readFirst.slice(0, 5)); setStep(1); })}>Scan project</button></div>
        </>}
        {step === 1 && scan && <>
          <h3>Found on this computer</h3>
          <table><tbody>{Object.entries(scan.counts).map(([k, v]) => <tr key={k}><td>{k}</td><td className="mono">{String(v)}</td></tr>)}{scan.commits !== null && <tr><td>Git commits</td><td className="mono">{scan.commits}</td></tr>}</tbody></table>
          <p className="hint">No AI was used for this scan. It cost nothing and nothing left your computer.</p>
          <div className="eyebrow connects">Notes the AI may read</div>
          {scan.readFirst.length === 0 ? <p className="hint">No READMEs or notes were found.</p> : <ul className="status-list">{scan.readFirst.map((f: string) => <li key={f}><label className="consent no-gap"><input type="checkbox" checked={files.includes(f)} onChange={(e) => setFiles((c) => (e.target.checked ? [...c, f] : c.filter((x) => x !== f)))} /><span className="mono">{f}</span></label></li>)}</ul>}
          <label className="field">Names to remove, separated by commas<input value={people} onChange={(e) => setPeople(e.target.value)} /></label>
          <div className="row"><button className="primary" disabled={!!busy} onClick={() => run("Preparing preview", async () => { setPreview(await call("/import/preview", "POST", { folder, files, people: names })); setStep(2); })}>Continue</button></div>
        </>}
        {step === 2 && preview && <>
          <h3>Approve what gets read</h3>
          {preview.items.map((i) => <div key={i.file}><div className="mono">{i.file} {!i.clean && <span className="pill bad">Won't be sent</span>}</div><pre className="preview short">{i.text.slice(0, 700)}</pre></div>)}
          <p className="hint">Estimated cost: ${preview.estimatedUsd.toFixed(4)}.</p>
          <label className="field">Name for the new project<input value={name} onChange={(e) => setName(e.target.value)} /></label>
          <div className="row">
            <button className="primary" disabled={!name.trim() || !!busy} onClick={() => run("Reading approved notes", async () => { const p = await call<{ id: string }>("/projects", "POST", { name, aim: "Imported project", scope: `Imported from ${folder}` }); setProjectId(p.id); await refresh(); const r = await call<{ result: { created: number; rejected: number }; view: View }>(`/projects/${p.id}/import/draft`, "POST", { folder, files, people: names }); setItems(r.view.imported); setNote(`${r.result.created} items found. ${r.result.rejected} were dropped because their quote wasn't in the file.`); setStep(3); })}>Approve and read</button>
            <button onClick={() => setStep(4)}>Skip the AI step</button>
          </div>
        </>}
        {step === 3 && <>
          <h3>Check what the app understood</h3>
          {items.length === 0 ? <p className="hint">Nothing was found.</p> : <table><thead><tr><th>Type</th><th>Found</th><th>From</th><th>Status</th></tr></thead><tbody>{items.map((d) => <tr key={d.id}><td>{d.fields.kind}</td><td>{d.fields.text}<div className="dim-text small-text">“{d.fields.quote}”</div></td><td className="mono dim-text">{d.fields.source}</td><td>{d.status === "proposed" ? <div className="datasheet-cell"><span className="pill warn">Imported, unverified</span><div className="decide"><button className="small" onClick={() => run("Saving", async () => { await call(`${base}/import/${d.id}/confirm`, "POST"); await reload(); })}>Confirm</button><button className="small" onClick={() => run("Saving", async () => { await call(`${base}/records/${d.id}/reject`, "POST"); await reload(); })}>Reject</button></div></div> : <span className={`pill ${d.status === "confirmed" ? "good" : "bad"}`}>{d.status === "confirmed" ? "Confirmed" : "Rejected"}</span>}</td></tr>)}</tbody></table>}
          <div className="row"><button className="primary" onClick={() => setStep(4)}>See gap report</button></div>
        </>}
        {step === 4 && <>
          <h3>What's missing or wrong</h3>
          {gaps.length === 0 ? <p className="hint">No problems were found by the automatic checks.</p> : <table><thead><tr><th>Level</th><th>Finding</th><th>Effect</th></tr></thead><tbody>{gaps.map((g) => <tr key={g.text}><td><span className={`pill ${g.level === "Blocks that work" ? "bad" : g.level === "Review" ? "warn" : ""}`}>{g.level}</span></td><td>{g.text}</td><td>{g.effect}</td></tr>)}</tbody></table>}
          <div className="row"><button className="primary" onClick={() => setStep(5)}>Choose where to resume</button></div>
        </>}
        {step === 5 && <>
          <h3>Where is this project now?</h3>
          <div className="resume">{GATE_CHOICES.map(([g, label]) => <button key={g} className={g === gate ? "on" : ""} aria-pressed={g === gate} onClick={() => setGate(g)}>{label}</button>)}</div>
          <p className="hint">Earlier gates will be marked Imported, not reviewed.</p>
          <div className="row">
            {projects && projectId && <select aria-label="Project" value={projectId} onChange={(e) => setProjectId(e.target.value)}>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>}
            <button className="primary" disabled={!projectId || !!busy} onClick={() => run("Saving", async () => { await call(`${base}/import/resume`, "POST", { gate }); setNote("Resume point saved. Open the project in Headquarters."); })}>Set resume point</button>
          </div>
        </>}
      </section>
    </div>
  );
}
