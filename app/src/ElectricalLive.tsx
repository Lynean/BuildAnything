import DepartmentChat from "./DepartmentChat";
import Tabbed from "./Tabbed";
import ToolTab, { useToolTabs } from "./ToolTab";
import DepartmentCapabilities from "./DepartmentCapabilities";
import { useEffect, useState } from "react";
import Circuits from "./Circuits";
import FactTrail, { factName } from "./FactTrail";

interface Rec { id: string; type: string; status: string; fields: Record<string, any>; }
interface Check { rule: string; status: "pass" | "fail" | "warn" | "unknown"; message: string; evidence: { parameter: string; page: number | null; label: string }[]; }
interface Session { id: string; title: string; where: string; domain: string; repeat: number; rung: number; ladder: string[]; status: string; next: string; hypotheses: Record<string, string>[]; measurements: string[]; errors: string[]; history: string[]; }
interface Note { id: string; part: string; page: number; finding: string; why: string; datasheet: string; current: boolean; usedBy: string[]; source?: string; confidence?: string; }
interface DocStatus { category: string; have: string[]; waived: Record<string, string>; needs: string[][]; missing: string[][]; assumed: string[][]; }
interface FactReview { verdict: string; checks?: { name: string; ok: boolean; skip?: boolean }[]; }
type FactRec = Rec & { review?: FactReview };
interface Assumption { id: string; text: string; checked?: string | null; }
interface Need { why?: string; id: string; item: string; status: "met" | "chosen" | "waived" | "open"; parts: string[]; reason: string | null; }
interface View { disputes: { message: string; why: string }[]; needs: Need[]; pageNotes: Note[]; documents?: Record<string, DocStatus>; docKinds?: Record<string, string>; assumptions?: Assumption[]; deviceFacts?: FactRec[]; components: Rec[]; datasheets: Rec[]; parameters: Rec[]; nets: Rec[]; checks: Check[]; powerTree: { net: string; volts: number | null; sources: string[]; loads: string[] }[]; debug: Session[]; spent: number; limit: number; }
interface Summary { id: string; name: string; }

async function call<T>(path: string, method = "GET", body?: unknown, raw?: Blob): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: raw ? { "Content-Type": "application/pdf" } : body === undefined ? undefined : { "Content-Type": "application/json" },
    body: raw ?? (body === undefined ? undefined : JSON.stringify(body)),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? `The request failed (${res.status}).`);
  }
  return res.json();
}

const remembered = (): string | null => { try { return localStorage.getItem("ba.project"); } catch { return null; } };
const remember = (id: string) => { try { localStorage.setItem("ba.project", id); } catch { /* optional */ } };

const STATUS_TEXT = { fail: "Fail", warn: "Warning", unknown: "Unknown", pass: "Pass" };
const fmt = (p: Rec) => {
  const f = p.fields;
  if (f.addresses?.length) return f.addresses.join(", ");
  return [f.min, f.typ, f.max].map((v: number | null) => (v === null || v === undefined ? "–" : v)).join(" / ");
};

function Troubleshooting({ base, sessions, onView, run }: { base: string; sessions: Session[]; onView: (v: View) => void; run: (label: string, work: () => Promise<void>) => void }) {
  const open = sessions.filter((s) => s.domain !== "code" && s.status === "open");
  const [picked, setPicked] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [symptom, setSymptom] = useState("");
  const [measure, setMeasure] = useState("");
  const active = open.find((s) => s.id === picked) ?? open[0];
  const hypothesis = active?.hypotheses[active.hypotheses.length - 1];
  const post = (label: string, path: string, body?: unknown) => run(label, async () => onView(await call<View>(`${base}/debug${path}`, "POST", body)));
  return (
    <section className="card" data-tab="Checks">
      <h3>Troubleshooting</h3>
      {open.length > 0 && <div className="issue-tabs">{open.map((s) => (<button key={s.id} className={s.id === active?.id ? "on" : ""} onClick={() => setPicked(s.id)}>{s.title}<small>{s.domain === "device" ? "Possible device issue" : "Wiring"}{s.where ? ` · ${s.where}` : ""}</small></button>))}</div>}
      {active ? (
        <>
          <div className="ladder">{active.ladder.map((r, i) => (<div key={r} className={i + 1 === active.rung ? "on" : i + 1 < active.rung ? "past" : ""}><span className="mono">{i + 1}</span>{r}</div>))}</div>
          <p className="ask">{active.next}</p>
          {hypothesis ? (
            <dl>
              <dt>Hypothesis</dt><dd>{hypothesis.hypothesis}</dd>
              <dt>Primary source</dt><dd>{hypothesis.primary}</dd>
              <dt>Backup source</dt><dd>{hypothesis.backup}</dd>
              <dt>If right</dt><dd>{hypothesis.expect}</dd>
              <dt>Ruled out if</dt><dd>{hypothesis.refute}</dd>
            </dl>
          ) : null}
          {active.measurements.length > 0 && <><div className="eyebrow">Measured</div><ul className="tried">{active.measurements.map((m, i) => <li key={i}>{m}</li>)}</ul></>}
          <div className="row">
            <input value={measure} onChange={(e) => setMeasure(e.target.value)} placeholder="Enter what you measured" aria-label="Enter your measurement" />
            <button disabled={!measure.trim()} onClick={() => { post("Saving", `/${active.id}/measurement`, { text: measure }); setMeasure(""); }}>Save</button>
          </div>
          <div className="row">
            <button onClick={() => post("Finding a likely cause", `/${active.id}/hypothesis`)}>Suggest a cause</button>
            {active.domain === "device" && <button onClick={() => post("Saving", `/${active.id}/move`, { to: "code", reason: "The device checked out." })}>Device checks out: return to code</button>}
            <button className="ghost" onClick={() => post("Saving", `/${active.id}/close`, { text: "Resolved" })}>Mark resolved</button>
          </div>
        </>
      ) : <p className="hint">No open wiring or device problems.</p>}
      <div className="eyebrow connects">Report a problem</div>
      <input className="full" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Short title" aria-label="Problem title" />
      <div className="row">
        <input value={symptom} onChange={(e) => setSymptom(e.target.value)} placeholder="What's happening?" aria-label="Problem symptom" />
        <button disabled={!symptom.trim()} onClick={() => { post("Saving", "", { title, error: symptom, domain: "wiring" }); setTitle(""); setSymptom(""); }}>Report</button>
      </div>
      <p className="hint">Code problems start in the Software workspace, and move here if code fixes don't work.</p>
    </section>
  );
}

export default function ElectricalLive() {
  const [projects, setProjects] = useState<Summary[] | null>(null);
  const [projectId, setProjectId] = useState<string | null>(remembered());
  const toolTabs = useToolTabs(projectId, "Electrical");
  const [view, setView] = useState<View | null>(null);
  const [part, setPart] = useState("");
  const [role, setRole] = useState("");
  const [trail, setTrail] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");

  useEffect(() => {
    call<Summary[]>("/projects").then((list) => {
      setProjects(list);
      setProjectId((id) => (id && list.some((p) => p.id === id) ? id : list[0]?.id ?? null));
    }).catch(() => setError("The backend isn't running."));
  }, []);
  const base = projectId ? `/projects/${projectId}/electrical` : "";
  useEffect(() => { if (projectId) { remember(projectId); call<View>(base).then(setView).catch((e) => setError(e.message)); } }, [projectId]);

  const run = async (label: string, work: () => Promise<void>) => {
    setBusy(label); setError(""); setNote("");
    try { await work(); } catch (e) { setError((e as Error).message); } finally { setBusy(""); }
  };
  const decide = (id: string, action: "confirm" | "reject") => run("Saving", async () => { await call(`/projects/${projectId}/records/${id}/${action}`, "POST"); setView(await call<View>(base)); });

  if (!projects) return <div className="page"><h1>Electrical</h1>{error ? <p className="error" role="alert">{error}</p> : <p>Loading…</p>}</div>;
  if (projects.length === 0 || !projectId) return <div className="page"><h1>Electrical</h1><p className="intro">Create a project in Headquarters first.</p></div>;
  if (!view) return <div className="page"><h1>Electrical</h1>{error ? <p className="error" role="alert">{error}</p> : <p>Loading…</p>}</div>;

  const sheetFor = (id: string) => [...view.datasheets].reverse().find((d) => d.fields.component === id);
  const paramsFor = (id: string) => view.parameters.filter((p) => p.fields.component === id && p.status !== "rejected");
  const counts = view.checks.reduce<Record<string, number>>((n, c) => ({ ...n, [c.status]: (n[c.status] ?? 0) + 1 }), {});
  const upload = (cid: string, file: File, doc = "datasheet") => run(`Uploading ${file.name}`, async () => setView(await call<View>(`${base}/components/${cid}/datasheet?filename=${encodeURIComponent(file.name)}&doc=${doc}`, "POST", undefined, file)));
  const kindName = (k: string) => view.docKinds?.[k] ?? k;
  const readFacts = (cid: string) => run("Reading the device facts", async () => {
    const r = await call<{ result: { created: number; unverified: number; read: string[] }; view: View }>(`${base}/components/${cid}/facts`, "POST");
    setView(r.view);
    setNote(`Found ${r.result.created} facts on ${r.result.read.length} pages${r.result.unverified ? `. ${r.result.unverified} quotes couldn't be found on their page, so check those closely` : ""}.`);
  });
  const sayVariant = (cid: string, part: string, now?: string) => { const text = window.prompt(`Which ${part} do you have? For example the gear ratio, voltage and options, as on its label or order (such as 150:1, 12 V, with encoder). Facts are then read only for that variant.`, now ?? ""); if (text?.trim()) run("Saving", async () => setView(await call<View>(`${base}/components/${cid}/variant`, "POST", { text }))); };
  const decideFact = (id: string, verb: "confirm" | "reject") => run("Saving", async () => { await call(`/projects/${projectId}/records/${id}/${verb}`, "POST"); setView(await call<View>(base)); });
  const docMissing = (cid: string, doc: string, what: string) => { const reason = window.prompt(`Why is there no ${what}? Anything it would settle becomes an assumption you check on the device.`); if (reason) run("Saving", async () => setView(await call<View>(`${base}/components/${cid}/document-missing`, "POST", { doc, reason }))); };
  const extract = (cid: string, override = false) => run("Reading the datasheet's limits", async () => {
    const r = await call<{ result: { created: number; pages: number[]; unverified?: number; problem: string | null }; view: View }>(`${base}/components/${cid}/extract?override=${override}`, "POST");
    setView(r.view); setOpen(cid);
    setNote(r.result.problem ?? `Read ${r.result.created} limits from pages ${r.result.pages.join(", ")}. ${r.result.unverified ? `${r.result.unverified} couldn't be matched to the page and need a closer look.` : "Every number was found on its page."}`);
  });
  const confirmParams = (cid: string) => run("Confirming", async () => { for (const p of paramsFor(cid).filter((x) => x.status === "proposed" && x.fields.value_on_page)) await call(`/projects/${projectId}/records/${p.id}/confirm`, "POST"); setView(await call<View>(base)); });

  return (
    <div className="page">
      <div className="hq-head">
        <h1>Electrical</h1>
        <select aria-label="Project" value={projectId} onChange={(e) => setProjectId(e.target.value)}>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
      </div>
      <p className="intro">Every component needs its documents: a datasheet, and for a board its user manual or schematic. Limits come from the page they were printed on. AI spend ${view.spent.toFixed(3)} of ${view.limit.toFixed(2)}.</p>
      {busy && <p className="busy" role="status">{busy}…</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {note && <p className="note" role="status">{note}</p>}
      <Tabbed id="electrical" tabs={["Lead", "Circuit", ...toolTabs.map((t) => t.title), "Parts", "Documents", "Checks", "Capability"]}>
      {toolTabs.map((t) => <div key={t.id} className="wide tool-tab" data-tab={t.title}><ToolTab projectId={projectId} projectName={projects.find((p) => p.id === projectId)?.name ?? projectId} department="Electrical" tab={t} /></div>)}
      <div data-tab="Lead"><DepartmentChat projectId={projectId} department="Electrical" onChange={() => { call<View>(base).then(setView).catch(() => undefined); }} /></div>
        <section className="card wide circuit-first" data-tab="Circuit"><h3>Circuit</h3><Circuits base={base} version={view.nets.length + view.components.length + view.nets.filter((n) => n.status === "confirmed").length} /></section>
      {view.disputes.length > 0 && <section className="card wide" data-tab="Checks"><h3>Warnings Electrical says don't apply</h3><p className="hint">The app's automatic check raised these. You decide, and your reason goes in the gate report.</p><ul className="status-list">{view.disputes.map((d) => <li key={d.message}><span><b>{d.message}</b> {d.why}</span><button className="small" disabled={!!busy} onClick={() => { const reason = window.prompt("Why is this safe to accept?", "Electrical explained why it doesn't apply to this board."); if (reason) run("Saving", async () => setView(await call<View>(`${base}/warnings/accept`, "POST", { message: d.message, reason }))); }}>Accept warning</button></li>)}</ul></section>}
      {view.needs.length > 0 && <section className="card wide" data-tab="Parts"><h3>What the design needs</h3><p className="hint">The Check stage can't finish until each of these is chosen, or you've said why it isn't needed.</p><ul className="status-list">{view.needs.map((n) => <li key={n.id}><i className={`dot ${n.status === "met" || n.status === "waived" ? "pass" : "warn"}`} /><span><b>{n.item}</b>{n.parts.length > 0 ? `: ${n.parts.join(", ")}` : ""}{n.reason ? `. Not needed: ${n.reason}` : ""}{n.why ? <small className="dim-text"> ({n.why})</small> : null}</span><em>{{ met: "Chosen", chosen: "Waiting for you to confirm", waived: "Not needed", open: "Not chosen yet" }[n.status]}</em>{n.status === "open" && <button className="small" disabled={!!busy} onClick={() => { const reason = window.prompt("Why doesn't this design need it?"); if (reason) run("Saving", async () => setView(await call<View>(`${base}/needs/${n.id}/waive`, "POST", { reason }))); }}>Not needed</button>}</li>)}</ul></section>}
      {(view.assumptions?.length ?? 0) > 0 && <section className="card wide" data-tab="Checks"><h3>Assumptions to check on the real device</h3><p className="hint">No document backs these. The Prove stage can't finish until you've tried each one on the device and said what you saw.</p><ul className="status-list">{view.assumptions!.map((a) => <li key={a.id}><i className={`dot ${a.checked ? "pass" : "warn"}`} /><span>{a.text}{a.checked ? <small className="dim-text"> You saw: {a.checked}</small> : null}</span>{!a.checked && <button className="small" disabled={!!busy} onClick={() => { const note = window.prompt("What did you see on the device?"); if (note) run("Saving", async () => setView(await call<View>(`${base}/assumptions/${a.id}/checked`, "POST", { note }))); }}>Checked on the device</button>}</li>)}</ul></section>}


      <div className="grid">
        <section className="card wide" data-tab="Parts">
          <h3>Components</h3>
          {view.components.some((c) => c.status === "proposed") && <div className="row"><button disabled={!!busy} onClick={() => run("Confirming", async () => setView(await call<View>(`${base}/components/confirm-all`, "POST")))}>Confirm all parts</button><span className="hint">Fuses, switches, mains items and anything too vague stay for you to check one by one.</span></div>}
          <div className="row">
            <input value={part} onChange={(e) => setPart(e.target.value)} placeholder="Part number, such as TMP102" aria-label="Part number" />
            <input value={role} onChange={(e) => setRole(e.target.value)} placeholder="Role, such as temperature sensor" aria-label="Role" />
            <button disabled={!part.trim() || !!busy} onClick={() => run("Saving", async () => { setView(await call<View>(`${base}/components`, "POST", { part_number: part, role })); setPart(""); setRole(""); })}>Add component</button>
          </div>
          {view.components.length > 0 && (() => {
            const live = view.components.filter((c) => c.status !== "rejected" && !("replaces" in c.fields));
            return (
              <div className="part-cards">
                {live.map((c) => {
                  const docs = view.documents?.[c.id];
                  const missing = docs?.missing.length ?? 0;
                  const facts = (view.deviceFacts ?? []).filter((f) => f.fields.component === c.id).length;
                  const limitsRead = paramsFor(c.id).length;
                  return (
                    <div key={c.id} className={`part-card ${c.status} ${missing ? "needs" : ""}`}>
                      <b title={c.fields.role}>{c.fields.part_number}</b>
                      <div className="part-docs">
                        {(docs?.have ?? []).map((k) => <span key={k} className="doc have" title={kindName(k)}>✓ {kindName(k)}</span>)}
                        {(docs?.missing ?? []).map((g) => <span key={g.join()} className="doc need" title={`Needs ${g.map(kindName).join(" or ")}`}>! {g.map(kindName).join(" / ")}</span>)}
                        {!docs?.have.length && !missing && <span className="doc">no documents needed</span>}
                      </div>
                      <div className="part-nums"><span><b>{limitsRead}</b> limits</span><span><b>{facts}</b> facts</span><span className={`pill ${c.status === "confirmed" ? "good" : "warn"}`}>{c.status}</span></div>
                    </div>
                  );
                })}
              </div>
            );
          })()}
          {view.components.length > 0 && (
            <details className="parts-table" open={view.components.some((c) => c.status === "proposed" || (view.documents?.[c.id]?.missing.length ?? 0) > 0)}>
            <summary>Documents, limits and confirmation for each part</summary>
            <table className="task-table">
              <thead><tr><th>Component</th><th>Datasheet</th><th>Limits</th><th>Status</th></tr></thead>
              <tbody>
                {view.components.filter((c) => c.status !== "rejected").flatMap((c) => {
                  const sheet = sheetFor(c.id);
                  const params = paramsFor(c.id);
                  const pending = params.filter((p) => p.status === "proposed").length;
                  const rows = [
                    <tr key={c.id}>
                      <td>{c.fields.part_number}<div className="dim-text small-text">{c.fields.role}</div></td>
                      <td>
                        <div className="datasheet-cell">
                          {sheet ? (sheet.fields.part_match ? <span className="pill good">Matches part</span> : <span className="pill bad">Part number not found in PDF</span>) : <span className="pill bad">Missing</span>}
                          <label className="file-btn"><input type="file" accept="application/pdf,.pdf" aria-label={`Upload datasheet for ${c.fields.part_number}`} onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(c.id, f); e.target.value = ""; }} />{sheet ? "Replace" : "Upload"}</label>
                          {!sheet && c.fields.datasheet_url && <a className="page-link" href={c.fields.datasheet_url} target="_blank" rel="noreferrer">Get the PDF</a>}
                          {!sheet && c.status === "confirmed" && (c.fields.generic || c.fields.no_datasheet ? <span className="pill">No datasheet needed</span> : <button className="small" disabled={!!busy} onClick={() => { const reason = window.prompt("Why is there no datasheet for this part?"); if (reason) run("Saving", async () => setView(await call<View>(`${base}/components/${c.id}/no-datasheet`, "POST", { reason }))); }}>No datasheet exists</button>)}
                        </div>
                        {sheet && <div className="file-name mono">{sheet.fields.filename}, {sheet.fields.pages} pages</div>}
                        {(() => {
                          const docs = view.documents?.[c.id];
                          if (!docs || c.status !== "confirmed") return null;
                          const others = docs.have.filter((k) => k !== "datasheet");
                          const needed = docs.missing.filter((group) => !group.includes("datasheet"));
                          return <>
                            {others.length > 0 && <div className="small-text">Also uploaded: {others.map(kindName).join(", ")}</div>}
                            {needed.map((group) => {
                              const what = group.map(kindName).join(" or ");
                              return <div key={group.join()} className="datasheet-cell">
                                <span className="pill bad">Needs {what}</span>
                                {group.map((k) => <label key={k} className="file-btn"><input type="file" accept="application/pdf,.pdf" aria-label={`Upload ${kindName(k)} for ${c.fields.part_number}`} onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(c.id, f, k); e.target.value = ""; }} />Upload {kindName(k)}</label>)}
                                <button className="small" disabled={!!busy} aria-label={`No ${what} for ${c.fields.part_number}`} onClick={() => docMissing(c.id, group[0], what)}>Doesn't exist</button>
                              </div>;
                            })}
                          </>;
                        })()}
                      </td>
                      <td>
                        {sheet && <div className="decide">
                          <button className="small" disabled={!!busy} onClick={() => extract(c.id, !sheet.fields.part_match)}>{params.length ? "Read again" : "Read limits"}</button>
                          {params.some((p) => p.status === "proposed" && p.fields.value_on_page) && <button className="small" disabled={!!busy} aria-label={`Confirm matched limits for ${c.fields.part_number}`} onClick={() => run("Confirming", async () => { for (const p of params.filter((x) => x.status === "proposed" && x.fields.value_on_page)) await call(`${base.replace(/\/electrical$/, "")}/records/${p.id}/confirm`, "POST"); setView(await call<View>(base)); })}>Confirm all found on the page</button>}
                          {params.length > 0 && <button className="small" aria-label={`Show limits for ${c.fields.part_number}`} onClick={() => setOpen(open === c.id ? null : c.id)}>{params.length} limits{pending ? `, ${pending} to check` : ""}</button>}
                        </div>}
                      </td>
                      <td>{c.status === "proposed"
                        ? <div className="decide"><button className="small" onClick={() => decide(c.id, "confirm")}>Confirm</button><button className="small" onClick={() => decide(c.id, "reject")}>Reject</button></div>
                        : <div className="decide"><span className={`pill ${c.status === "confirmed" ? "good" : "bad"}`}>{c.status === "confirmed" ? "Confirmed" : "Rejected"}</span>{c.status === "confirmed" && (c.fields.owned ? <span className="pill">Already owned</span> : <button className="small" disabled={!!busy} onClick={() => run("Saving", async () => setView(await call<View>(`${base}/components/${c.id}/owned`, "POST")))}>I already have this</button>)}</div>}</td>
                    </tr>,
                  ];
                  if (open === c.id && params.length > 0) {
                    rows.push(
                      <tr key={`${c.id}-params`} className="param-row"><td colSpan={4}>
                        <table>
                          <thead><tr><th>Limit</th><th>Kind</th><th>Min / typ / max</th><th>Unit</th><th>Page</th><th>Status</th></tr></thead>
                          <tbody>
                            {params.map((p) => (
                              <tr key={p.id}>
                                <td>{p.fields.label}{p.fields.conditions && <div className="dim-text small-text">{p.fields.conditions}</div>}</td>
                                <td className="mono small-text">{p.fields.kind}</td>
                                <td className="mono">{fmt(p)}</td>
                                <td>{p.fields.unit}</td>
                                <td>{sheet && <a className="page-link" href={`/api${base}/datasheets/${p.fields.datasheet}/file#page=${p.fields.page}`} target="_blank" rel="noreferrer">p. {p.fields.page}</a>}</td>
                                <td>{p.status === "proposed"
                                  ? <div className="datasheet-cell">{!p.fields.value_on_page && <span className="pill warn">Not found on page</span>}<div className="decide"><button className="small" onClick={() => decide(p.id, "confirm")}>Confirm</button><button className="small" onClick={() => decide(p.id, "reject")}>Reject</button></div></div>
                                  : <span className="pill good">Confirmed</span>}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        {pending > 0 && <div className="row"><button onClick={() => confirmParams(c.id)}>Confirm all found on page</button><span className="hint">Check absolute maximums against the linked page first.</span></div>}
                      </td></tr>,
                    );
                  }
                  return rows;
                })}
              </tbody>
            </table>
            {view.components.some((c) => c.status === "rejected") && <p className="hint">Rejected and hidden: {view.components.filter((c) => c.status === "rejected").map((c) => c.fields.part_number).join(", ")}.</p>}
            </details>
          )}
        </section>

        <section className="card wide" data-tab="Circuit">
          <div className="hq-head">
            <h3>Connections</h3>
            <button className="primary" disabled={!!busy} onClick={() => run("Designing connections", async () => { const r = await call<{ result: { created: number; problems: string[]; notes: string[] }; view: View }>(`${base}/design`, "POST"); setView(r.view); if (r.result.problems.length) setError(r.result.problems.join(" ")); else setNote(`Proposed ${r.result.created} nets. ${r.result.notes.join(" ")}`); })}>Propose connections</button>
          </div>
          {view.nets.filter((n) => n.status !== "rejected").length === 0 ? <p className="hint">No connections yet.</p> : (
            <details className="parts-table" open={view.nets.some((n) => n.status === "proposed")}><summary>{view.nets.filter((n) => n.status === "confirmed").length} of {view.nets.filter((n) => n.status !== "rejected").length} connections confirmed. The circuit diagram above shows them; open for the list.</summary>
            <table><thead><tr><th>Net</th><th>Type</th><th>Voltage</th><th>Connected</th><th>Status</th></tr></thead><tbody>
              {view.nets.filter((n) => n.status !== "rejected").map((n) => (
                <tr key={n.id}>
                  <td className="mono">{n.fields.name}</td><td>{n.fields.kind}</td><td className="mono">{n.fields.nominal_v ?? "–"}</td>
                  <td className="small-text">{n.fields.members.map((m: any) => `${view.components.find((c) => c.id === m.component)?.fields.part_number ?? "?"} ${m.pin}`).join(", ")}</td>
                  <td>{n.status === "proposed" ? <div className="decide"><button className="small" onClick={() => decide(n.id, "confirm")}>Confirm</button><button className="small" onClick={() => decide(n.id, "reject")}>Reject</button></div> : <span className="pill good">Confirmed</span>}</td>
                </tr>
              ))}
            </tbody></table></details>
          )}
        </section>

        <section className="card" data-tab="Parts">
          <h3>Compatibility</h3>
          <div className="fact-meter" role="img" aria-label={`${counts.fail ?? 0} failed, ${counts.warn ?? 0} warnings, ${counts.unknown ?? 0} unknown, ${counts.pass ?? 0} passed`}>
            <span className="bad" style={{ flexGrow: counts.fail ?? 0 }} /><span className="warn" style={{ flexGrow: counts.warn ?? 0 }} /><span className="idle" style={{ flexGrow: counts.unknown ?? 0 }} /><span className="good" style={{ flexGrow: counts.pass ?? 0 }} />
          </div>
          <p className="hint check-counts">{counts.fail ?? 0} failed, {counts.warn ?? 0} warnings, {counts.unknown ?? 0} unknown, {counts.pass ?? 0} passed</p>
          {(() => {
            // The same reason on many connections is said once, with the connections as chips.
            const order = ["fail", "warn", "unknown", "pass"];
            const groups = new Map<string, { status: string; rule: string; reason: string; subjects: string[]; pages: number[] }>();
            view.checks.forEach((c) => {
              const cut = c.message.indexOf(": ");
              const subject = cut > 0 ? c.message.slice(0, cut) : "";
              const reason = cut > 0 ? c.message.slice(cut + 2) : c.message;
              const key = `${c.status}|${c.rule}|${reason}`;
              const g = groups.get(key) ?? { status: c.status, rule: c.rule, reason, subjects: [], pages: [] };
              if (subject) g.subjects.push(subject);
              c.evidence.forEach((e) => { if (e.page && !g.pages.includes(e.page)) g.pages.push(e.page); });
              groups.set(key, g);
            });
            const list = [...groups.values()].sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status));
            const row = (g: typeof list[number], i: number) => (
              <li key={i} className={`check-group ${g.status}`}>
                <i className={`dot ${g.status === "pass" ? "pass" : g.status === "fail" ? "fail" : g.status === "warn" ? "warn" : "unknown"}`} />
                <span><b>{g.rule}</b> {g.reason}{g.pages.length > 0 && <small> Datasheet p. {g.pages.join(", ")}</small>}
                  {g.subjects.length > 0 && <span className="check-subjects">{g.subjects.map((x) => <i key={x}>{x}</i>)}</span>}</span>
                <em>{STATUS_TEXT[g.status as keyof typeof STATUS_TEXT]}{g.subjects.length > 1 ? ` ×${g.subjects.length}` : ""}</em>
              </li>
            );
            const urgent = list.filter((g) => g.status === "fail" || g.status === "warn");
            const rest = list.filter((g) => g.status !== "fail" && g.status !== "warn");
            return <>
              <ul className="status-list check-list">{urgent.map(row)}</ul>
              {rest.length > 0 && <details open={!urgent.length && rest.length < 4}><summary className="hint">Unknown and passed ({rest.reduce((n, g) => n + Math.max(1, g.subjects.length), 0)})</summary><ul className="status-list check-list">{rest.map(row)}</ul></details>}
            </>;
          })()}
        </section>

        <div className="stack-cards">
          <section className="card" data-tab="Circuit">
            <h3>Power tree</h3>
            {view.powerTree.length === 0 ? <p className="hint">Appears once power nets exist.</p> : (
              <ul className="status-list">{view.powerTree.map((p) => <li key={p.net}><span><span className="mono">{p.net}</span> ({p.volts ?? "?"} V): {p.sources.join(", ") || "no source"} to {p.loads.join(", ") || "no loads"}</span></li>)}</ul>
            )}
          </section>
          <Troubleshooting base={base} sessions={view.debug} onView={setView} run={run} />
        </div>


        <section className="card wide" data-tab="Documents">
          <h3>Device facts</h3>
          <p className="hint">What code needs to talk to each part, read from its documents. The app checks each fact itself, and only asks you about the ones it couldn't verify that code relies on.</p>
          <div className="row">{view.components.filter((c, i, all) => c.status === "confirmed" && view.documents?.[c.id]?.have.length && all.findIndex((o) => o.status === "confirmed" && view.documents?.[o.id]?.have.length && o.fields.part_number === c.fields.part_number) === i).map((c) => <button key={c.id} className="small" disabled={!!busy} onClick={() => readFacts(c.id)}>Read facts: {c.fields.part_number}{c.fields.variant ? ` (${c.fields.variant})` : ""}</button>)}</div>
          <p className="hint small-text">One document for many variants, such as a gearmotor family? Say which one you have first: {view.components.filter((c, i, all) => c.status === "confirmed" && view.documents?.[c.id]?.have.length && all.findIndex((o) => o.fields.part_number === c.fields.part_number) === i).map((c) => <button key={c.id} className="link" disabled={!!busy} onClick={() => sayVariant(c.id, c.fields.part_number, c.fields.variant)}>{c.fields.part_number}</button>)}</p>
          {(() => {
            const facts = (view.deviceFacts ?? []).filter((f) => f.review?.verdict !== "replaced");
            if (!facts.length) return null;
            const group = (v: string[]) => facts.filter((f) => v.includes(f.review?.verdict ?? ""));
            const yours = group(["needs_you"]), fine = group(["verified", "confirmed"]), idle = group(["unused"]);
            const partOf = (f: FactRec) => view.components.find((c) => c.id === f.fields.component)?.fields.part_number;
            const row = (f: FactRec) => <li key={f.id}><span><b>{partOf(f)}</b> {factName(f.fields.key, f.fields.signal, f.fields.qualifier)} = <span className="mono">{f.fields.value}</span></span><button className="small" onClick={() => setTrail(f.id)}>Show evidence</button></li>;
            return <>
              <div className="fact-meter" role="img" aria-label={`${fine.length} checked by the app, ${yours.length} need you, ${idle.length} not used yet`}>
                <span className="good" style={{ flexGrow: fine.length }} /><span className="warn" style={{ flexGrow: yours.length }} /><span className="idle" style={{ flexGrow: idle.length }} />
              </div>
              <div className="fact-counts"><span><b>{fine.length}</b> checked by the app</span><span><b>{yours.length}</b> need you</span><span><b>{idle.length}</b> not used by any code yet</span></div>
              {yours.length > 0 && <div className="fact-cards">{yours.map((f, i) => (
                <article key={f.id} className="fact-card" style={{ animationDelay: `${i * 80}ms` }}>
                  <div className="eyebrow">{partOf(f)}</div>
                  <div className="fact-line">{factName(f.fields.key, f.fields.signal, f.fields.qualifier)} <b className="mono">{f.fields.value}</b></div>
                  <ul className="fact-why">{(f.review?.checks ?? []).filter((c) => !c.ok && !c.skip).map((c) => <li key={c.name}>✗ {c.name}</li>)}</ul>
                  <button className="primary small" onClick={() => setTrail(f.id)}>Show evidence</button>
                </article>
              ))}</div>}
              {fine.length > 0 && <details><summary>Checked by the app ({fine.length})</summary><ul className="status-list">{fine.map(row)}</ul></details>}
              {idle.length > 0 && <details><summary>Not used by any code yet ({idle.length})</summary><ul className="status-list">{idle.map(row)}</ul></details>}
            </>;
          })()}
          {trail && <FactTrail base={base} factId={trail} onClose={() => setTrail("")} onDecide={decideFact} />}
        </section>

        <section className="card wide" data-tab="Documents">
          <h3>Datasheet memory</h3>
          {view.pageNotes.length > 0 && (
            <div className="memory-strips">
              {[...new Set(view.pageNotes.map((n) => n.part))].map((part) => (
                <div key={part} className="memory-strip">
                  <b>{part}</b>
                  <div className="memory-pages">
                    {view.pageNotes.filter((n) => n.part === part).sort((a, b) => a.page - b.page).map((n) => (
                      <a key={n.id} className={`memory-page ${!n.current ? "old" : n.usedBy.length ? "used" : ""}`} href={`/api${base}/datasheets/${n.datasheet}/file#page=${n.page}`} target="_blank" rel="noreferrer" title={`${n.why}\n\n"${n.finding}"\n\n${n.current ? (n.usedBy.length ? "Used by " + n.usedBy.join("; ") : "Not used by code yet") : "Datasheet replaced"}`}>
                        <span>p.{n.page}</span><small>{n.why.length > 28 ? n.why.slice(0, 26) + "…" : n.why}</small>
                      </a>
                    ))}
                  </div>
                </div>
              ))}
              <div className="sw-legend"><span><i style={{ background: "#6fd08c" }} />used by code</span><span><i style={{ background: "#3a4357" }} />saved, not used yet</span><span><i style={{ background: "#ff6f61" }} />datasheet replaced</span></div>
            </div>
          )}
          {view.pageNotes.length === 0 ? <p className="hint">Agents save exact text from each page they rely on, so they don't have to search again.</p> : (
            <details className="memory-table"><summary>Every saved page as a table ({view.pageNotes.length})</summary><table><thead><tr><th>Part</th><th>Source</th><th>Page</th><th>Why</th><th>Exact text</th><th>Used by</th></tr></thead><tbody>
              {view.pageNotes.map((n) => (
                <tr key={n.id} className={n.current ? "" : "muted"}>
                  <td>{n.part}</td>
                  <td className="small-text">{n.source ?? "datasheet"}{n.confidence && n.confidence !== "high" ? <span className="pill warn">{n.confidence} confidence</span> : null}</td>
                  <td><a className="page-link" href={`/api${base}/datasheets/${n.datasheet}/file#page=${n.page}`} target="_blank" rel="noreferrer">p. {n.page}</a></td>
                  <td>{n.why}</td>
                  <td className="small-text">{n.finding}</td>
                  <td className="small-text">{n.current ? (n.usedBy.join("; ") || "Not used yet") : "Datasheet replaced"}</td>
                </tr>
              ))}
            </tbody></table></details>
          )}
        </section>

      </div>
      <DepartmentCapabilities projectId={projectId} department="Electrical" />
      </Tabbed>
    </div>
  );
}
