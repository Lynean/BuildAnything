import { useEffect, useState } from "react";
import { call, useBusy, useProject } from "./api";
import { Rendered } from "./AgentChat";

interface Impact { department: string; item: string; why: string; action: string; }
interface Review { recommendation: "approve" | "caution"; summary: string; risks: string[]; missed: Impact[]; }
interface Change { id: string; number: number; title: string; why: string; kind: string; targetLabel: string; details: Record<string, unknown>; status: string; by: string; byKind: string; review: Review | null; impact: Impact[]; decision: { reason: string; by: string; auto?: boolean } | null; escalation: string[]; triaged: boolean; }
interface Option { id: string; label: string; }
interface View { changes: Change[]; options: Record<string, Option[]>; running: boolean; }

const KINDS: [string, string][] = [["part", "A part"], ["component", "An electrical component"], ["requirement", "A requirement's wording"], ["deviation", "Accept that a requirement won't be met"], ["drop_requirement", "Remove a requirement"], ["packet", "A data packet"], ["other", "Something else"]];
const PART_FIELDS: [string, string][] = [["material", "New material"], ["process", "New process"], ["qty", "New quantity"], ["mass_g", "New mass (g)"], ["unit_cost", "New unit cost"]];
const STATUS: Record<string, [string, string]> = { open: ["Waiting for your decision", "warn"], approved: ["Approved", "good"], rejected: ["Rejected", ""] };

function ChangeCard({ change, base, onView }: { change: Change; base: string; onView: (v: View) => void }) {
  const { busy, error, run } = useBusy();
  const [reason, setReason] = useState("");
  const areas = [...new Set(change.impact.map((i) => i.department))];
  const act = (verb: string) => run("Saving", async () => onView(await call<View>(`${base}/${change.id}/${verb}`, "POST", { reason })));
  const open = change.status === "open";
  const [label, tone] = STATUS[change.status];
  return (
    <section className="card change-card">
      <div className="hq-head"><div className="eyebrow">Change {change.number} · {change.byKind === "agent" ? `Raised by the ${change.by} agent` : `Raised by ${change.by}`}</div><span className={`pill ${tone}`}>{label}</span></div>
      <h3>{change.title}</h3>
      {open && change.escalation.length > 0 && <p className="warn-text">Passed to you because: {change.escalation.join(" ")}</p>}
      <dl className="stack">
        <dt>Changes</dt><dd>{change.targetLabel}{Object.keys(change.details).length > 0 && `: ${Object.entries(change.details).map(([k, v]) => `${k.replace("_", " ")} → ${v}`).join(", ")}`}</dd>
        <dt>Why</dt><dd>{change.why}</dd>
      </dl>
      <h4>What it affects</h4>
      {change.impact.length === 0 ? <p className="hint">Nothing else was found to depend on this.</p> : areas.map((area) => (
        <div key={area} className="impact-area">
          <div className="eyebrow">{area}</div>
          <ul className="tried">{change.impact.filter((i) => i.department === area).map((i) => <li key={i.item}><b>{i.item}.</b> {i.why}. <em>{i.action}.</em></li>)}</ul>
        </div>
      ))}
      <h4>Independent review</h4>
      {change.review ? (
        <div className={`review-box ${change.review.recommendation}`}>
          <b>{change.review.recommendation === "approve" ? "No concerns" : "Advises caution"}</b>
          <Rendered text={change.review.summary} />
          {change.review.risks.length > 0 && <ul className="tried">{change.review.risks.map((r) => <li key={r}>{r}</li>)}</ul>}
          {open && change.review.missed.length > 0 && <><div className="eyebrow">Also found by the reviewer</div><ul className="tried">{change.review.missed.map((m) => <li key={m.item}><b>{m.department}: {m.item}.</b> {m.why}. <em>{m.action}.</em></li>)}</ul></>}
        </div>
      ) : <div className="agent-thinking" role="status"><i /><i /><i /><span>The reviewer is checking this change</span></div>}
      {error && <p className="error" role="alert">{error}</p>}
      {open ? (
        <div className="change-decide">
          <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder={change.review?.recommendation === "caution" || ["deviation", "drop_requirement"].includes(change.kind) ? "Reason, needed to go ahead or reject" : "Reason, needed to reject"} aria-label="Reason" />
          <button className="primary" disabled={!!busy || !change.review || (change.byKind === "agent" && !change.triaged)} onClick={() => act("approve")}>Approve change</button>
          <button disabled={!!busy || !reason.trim()} onClick={() => act("reject")}>Reject</button>
        </div>
      ) : change.decision?.reason || change.decision?.auto ? (
        <div className="hq-decision"><p className="hint">{change.status === "approved" ? "Approved" : "Declined"} by {change.decision.by}: {change.decision.reason}</p>{change.decision.auto && change.status === "rejected" && <button className="small" disabled={!!busy} onClick={() => run("Saving", async () => onView(await call<View>(`${base}/${change.id}/reopen`, "POST")))}>Reopen and decide myself</button>}</div>
      ) : null}
    </section>
  );
}

function NewChange({ view, base, onView, onClose }: { view: View; base: string; onView: (v: View) => void; onClose: () => void }) {
  const { busy, error, run } = useBusy();
  const [title, setTitle] = useState("");
  const [why, setWhy] = useState("");
  const [kind, setKind] = useState("part");
  const [target, setTarget] = useState("");
  const [details, setDetails] = useState<Record<string, string>>({});
  const choices = view.options[kind] ?? [];
  const set = (k: string, v: string) => setDetails((d) => ({ ...d, [k]: v }));
  const numeric = (d: Record<string, string>) => Object.fromEntries(Object.entries(d).filter(([, v]) => v.trim()).map(([k, v]) => [k, ["qty", "mass_g", "unit_cost"].includes(k) ? Number(v) : v]));
  const submit = () => run("Sending for review", async () => { onView(await call<View>(base, "POST", { title, why, kind, target, details: numeric(details) })); onClose(); });
  return (
    <section className="card">
      <h3>Propose a change</h3>
      <label className="field">What's the change?<input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Use metal-gear servos" /></label>
      <label className="field">Why is it needed?<textarea rows={2} value={why} onChange={(e) => setWhy(e.target.value)} /></label>
      <label className="field">What does it change?<select value={kind} onChange={(e) => { setKind(e.target.value); setTarget(""); setDetails({}); }}>{KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      {kind !== "other" && <label className="field">Which one?<select value={target} onChange={(e) => setTarget(e.target.value)}><option value="">{choices.length ? "Choose" : "None available yet"}</option>{choices.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}</select></label>}
      {kind === "part" && <div className="pkt-grid">{PART_FIELDS.map(([k, l]) => <label className="field" key={k}>{l}<input value={details[k] ?? ""} onChange={(e) => set(k, e.target.value)} /></label>)}</div>}
      {kind === "component" && <label className="field">Replace it with<input value={details.part_number ?? ""} onChange={(e) => set("part_number", e.target.value)} placeholder="Part number" /></label>}
      {kind === "deviation" && <><label className="field">What isn't met?<input value={details.what ?? ""} onChange={(e) => set("what", e.target.value)} placeholder="Tracking only reaches 1.5 m, not 2 m" /></label><label className="field">What would put it right?<input value={details.restores ?? ""} onChange={(e) => set("restores", e.target.value)} placeholder="A wider-angle camera in the next build" /></label></>}
      {kind === "requirement" && <><label className="field">New wording<input value={details.text ?? ""} onChange={(e) => set("text", e.target.value)} /></label><label className="field">How it will be tested (optional)<input value={details.verification ?? ""} onChange={(e) => set("verification", e.target.value)} /></label></>}
      {error && <p className="error" role="alert">{error}</p>}
      <div className="row"><button className="primary" disabled={!!busy || !title.trim() || !why.trim()} onClick={submit}>See the impact</button><button onClick={onClose}>Cancel</button></div>
    </section>
  );
}

export default function ChangesScreen() {
  const { projects, projectId, setProjectId, loadError } = useProject();
  const [view, setView] = useState<View | null>(null);
  const [adding, setAdding] = useState(false);
  const base = `/projects/${projectId}/changes`;
  useEffect(() => {
    if (!projectId) return;
    const load = () => call<View>(base).then(setView).catch(() => undefined);
    load();
    const timer = window.setInterval(load, 3000);
    return () => window.clearInterval(timer);
  }, [projectId]);
  if (!projects) return <div className="page"><h1>Changes</h1><p>{loadError || "Loading…"}</p></div>;
  if (!projectId || !view) return <div className="page"><h1>Changes</h1><p className="intro">Create a project in Headquarters first.</p></div>;
  return (
    <div className="page">
      <div className="hq-head"><h1>Changes</h1><select aria-label="Project" value={projectId} onChange={(e) => setProjectId(e.target.value)}>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div>
      <p className="intro">See everything a change affects before it happens. Headquarters settles routine requests from departments, and passes the rest to you.</p>
      {adding ? <NewChange view={view} base={base} onView={setView} onClose={() => setAdding(false)} /> : <div className="row"><button className="primary" onClick={() => setAdding(true)}>Propose a change</button></div>}
      {view.changes.length === 0 && !adding && <p className="hint">No changes yet.</p>}
      {view.changes.map((c) => <ChangeCard key={c.id} change={c} base={base} onView={setView} />)}
    </div>
  );
}
