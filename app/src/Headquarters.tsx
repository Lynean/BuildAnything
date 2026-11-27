import { useEffect, useRef, useState, type ReactNode } from "react";
import { ScreenChip, Linked, go } from "./Linked";
import { selectProject, selectedProject, useBusy } from "./api";
import HqGlance from "./HqGlance";
import TeamStory from "./TeamStory";
import { STAGES } from "./data";
import AgentChat, { Rendered, type ChatMessage } from "./AgentChat";
import ShapeEditor, { type ShapeView } from "./ShapeEditor";

interface Rec { id: string; type: string; status: string; fields: Record<string, any>; decision?: { decision: string; reason: string }; }
interface Project {
  id: string;
  meta: { name: string; aim: string; scope: string };
  gates: { id: string; name: string; approved: boolean; imported: boolean }[];
  currentGate: string | null;
  missing: string[];
  limit: number;
  spent: number;
  records: Rec[];
  git: { available: boolean; unsaved: number; last: string | null; remote?: string | null };
}
interface Summary { id: string; name: string; currentGate: string | null; }

async function call<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? `The request failed (${res.status}).`);
  }
  return res.json();
}

const CLOSED: Record<string, string> = { accept: "Risk accepted", dismiss: "Not a problem", fixed: "Fixed", later: "Later stage" };

function JoinProject({ onJoined }: { onJoined: (p: Project) => void }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const join = async () => {
    setBusy(true); setError("");
    try { onJoined(await call<Project>("/projects/join", "POST", { url })); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <details className="team-panel">
      <summary>Join a teammate's project</summary>
      <label className="field">Their Git address<input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://github.com/you/project.git" /></label>
      <button className="small" disabled={busy || !url.trim()} onClick={join}>{busy ? "Copying…" : "Join"}</button>
      {error && <p className="error" role="alert">{error}</p>}
    </details>
  );
}

interface Need { goto?: string; kind: string; text: string; gate?: string; key?: string; }
interface Outbox { wait: number | null; hold: boolean; pending: boolean; quietSeconds: number; }
interface Hq { eventCount: number; running: string | null; outbox?: Outbox; needs: Need[]; conceptApproved: boolean; requests: { id: string; from: string; to: string; question: string; answer: string | null; status: string; reason?: string; route?: string }[]; }

function TeamPanel({ p, project, setProject, busy, run, setNote }: { p: string; project: Project; setProject: (x: Project) => void; busy: string; run: (label: string, work: () => Promise<void>) => void; setNote: (t: string) => void }) {
  const [url, setUrl] = useState(project.git.remote ?? "");
  const [person, setPerson] = useState("");
  useEffect(() => { call<{ name: string }>("/person").then((r) => setPerson(r.name === "owner" ? "" : r.name)); }, []);
  const sync = () => run("Syncing with your team", async () => {
    const r = await call<{ result: { ok: boolean; detail: string; review?: string[] }; project: Project }>(`${p}/sync`, "POST");
    setProject(r.project);
    setNote([r.result.detail, ...(r.result.review ?? [])].join(" "));
  });
  return (
    <details className="team-panel">
      <summary>Work with a teammate</summary>
      <label className="field">Your name<input value={person} onChange={(e) => setPerson(e.target.value)} onBlur={() => person.trim() && call("/person", "POST", { name: person })} placeholder="Shown on what you approve" /></label>
      <label className="field">Team Git address<input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://github.com/you/project.git" /></label>
      <div className="row">
        <button className="small" disabled={!!busy || !url.trim() || url === project.git.remote} onClick={() => run("Saving", async () => setProject(await call<Project>(`${p}/remote`, "POST", { url })))}>Save address</button>
        <button className="primary" disabled={!!busy || !project.git.remote} onClick={sync}>Sync</button>
      </div>
      <p className="hint">Sync saves your work, brings in your teammate's, and publishes the result to that address. It uses your own Git login.</p>
    </details>
  );
}

function FindingDecision({ rec, onDecide }: { rec: Rec; onDecide: (id: string, decision: string, reason: string) => void }) {
  const [asking, setAsking] = useState("");
  const [reason, setReason] = useState("");
  const d = rec.decision;
  if (rec.status !== "proposed") {
    return <div><span className={`pill ${d?.decision === "dismiss" ? "" : "good"}`}>{d ? CLOSED[d.decision] : rec.status === "confirmed" ? "Accepted" : "Rejected"}</span>{d?.reason && <p className="hint">{d.reason}</p>}</div>;
  }
  if (asking) {
    return (
      <div className="decide-reason">
        <input autoFocus value={reason} placeholder={rec.fields.safety && asking === "dismiss" ? "Why can't this cause harm?" : asking === "later" ? "Which stage, such as before testing on the real stair?" : "Why?"} onChange={(e) => setReason(e.target.value)} aria-label="Reason" />
        <button className="small" disabled={!reason.trim()} onClick={() => onDecide(rec.id, asking, reason)}>Save</button>
        <button className="small" onClick={() => { setAsking(""); setReason(""); }}>Cancel</button>
      </div>
    );
  }
  if (d?.decision === "fix") {
    return <div className="decide"><span className="pill warn">{rec.fields.kind === "beyond_scope" ? "Being removed" : "Being fixed"}</span><button className="small" onClick={() => onDecide(rec.id, "fixed", "")}>Mark fixed</button><button className="small" onClick={() => setAsking("accept")}>Accept risk</button></div>;
  }
  if (rec.fields.kind === "beyond_scope") {
    return <div className="decide"><button className="small" onClick={() => onDecide(rec.id, "fix", "")} title="Take it out of the plan, or narrow it to what you asked">Remove it</button><button className="small" onClick={() => setAsking("dismiss")} title="You do want it">Keep it</button></div>;
  }
  return <div className="decide"><button className="small" onClick={() => onDecide(rec.id, "fix", "")}>Fix it</button><button className="small" onClick={() => setAsking("later")} title="A real concern, kept for a later stage, without adding work now">Later stage</button><button className="small" onClick={() => setAsking("accept")}>Accept risk</button><button className="small" onClick={() => setAsking("dismiss")}>Not a problem</button></div>;
}

function Decide({ rec, onDecide, acceptLabel = "Confirm" }: { rec: Rec; onDecide: (id: string, action: "confirm" | "reject") => void; acceptLabel?: string }) {
  if (rec.status !== "proposed") return <span className={`pill ${rec.status === "confirmed" ? "good" : "bad"}`}>{rec.status === "confirmed" ? (acceptLabel === "Accept risk" ? "Accepted" : "Confirmed") : "Rejected"}</span>;
  return (
    <div className="decide">
      <button className="small" onClick={() => onDecide(rec.id, "confirm")}>{acceptLabel}</button>
      <button className="small" onClick={() => onDecide(rec.id, "reject")}>Reject</button>
    </div>
  );
}

function Section({ title, step, action, children }: { title: string; step: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="card hq-section">
      <div className="hq-head">
        <div><span className="eyebrow">{step}</span><h3>{title}</h3></div>
        {action}
      </div>
      {children}
    </section>
  );
}

function NewProject({ onCreated }: { onCreated: (p: Project) => void }) {
  const [name, setName] = useState("");
  const [aim, setAim] = useState("");
  const [scope, setScope] = useState("");
  const [error, setError] = useState("");
  const create = async () => {
    try {
      onCreated(await call<Project>("/projects", "POST", { name, aim, scope }));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <section className="card new-project">
      <h3>New project</h3>
      <label className="field">Name<input value={name} onChange={(e) => setName(e.target.value)} placeholder="Butterfly Drone" /></label>
      <label className="field">Aim<textarea rows={2} value={aim} onChange={(e) => setAim(e.target.value)} placeholder="What should it do, and why?" /></label>
      <label className="field">Scope<textarea rows={2} value={scope} onChange={(e) => setScope(e.target.value)} placeholder="What's included, and what isn't?" /></label>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="row"><button className="primary" disabled={!name.trim()} onClick={create}>Create project</button></div>
    </section>
  );
}

export default function Headquarters() {
  const [list, setList] = useState<Summary[] | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [hq, setHq] = useState<Hq | null>(null);
  const [waive, setWaive] = useState("");
  const [sending, setSending] = useState("");
  const [shapeView, setShapeView] = useState<ShapeView | null>(null);
  useEffect(() => { if (project) call<ShapeView>(`/projects/${project.id}/shape`).then(setShapeView).catch(() => setShapeView(null)); }, [project?.id, project?.records.length]);
  const seen = useRef(0);
  const [offline, setOffline] = useState(false);

  // Headquarters shows the project chosen with the switcher at the top left, and follows it when it changes.
  useEffect(() => {
    const show = (id: string) => { if (id) call<Project>(`/projects/${id}`).then((x) => { setProject(x); setCreating(false); }).catch(() => setProject(null)); };
    const follow = (e: Event) => show((e as CustomEvent<string>).detail);
    const flag = window as unknown as { baNewProject?: boolean };
    const start = () => { flag.baNewProject = false; setProject(null); setCreating(true); };
    if (flag.baNewProject) start();  // "New project" was chosen before Headquarters opened, so the event may have come too early
    else show(selectedProject());
    window.addEventListener("ba:project", follow);
    window.addEventListener("ba:new-project", start);
    return () => { window.removeEventListener("ba:project", follow); window.removeEventListener("ba:new-project", start); };
  }, []);
  const refreshList = () => call<Summary[]>("/projects").then((l) => { setList(l); setOffline(false); return l; });
  useEffect(() => { refreshList().catch(() => setOffline(true)); }, []);

  useEffect(() => {
    if (!project) return;
    let alive = true;
    const id = project.id;
    seen.current = 0;
    const load = () => call<Hq>(`/projects/${id}/hq`).then((h) => {
      if (!alive) return;
      setHq(h);
      if (seen.current && h.eventCount !== seen.current) call<Project>(`/projects/${id}`).then((x) => { if (alive) setProject(x); });
      seen.current = h.eventCount;
    }).catch(() => undefined);
    load();
    const timer = window.setInterval(load, 2500);
    return () => { alive = false; window.clearInterval(timer); };
  }, [project?.id]);

  const run = async (label: string, work: () => Promise<void>) => {
    setBusy(label); setError(""); setNote("");
    try { await work(); } catch (e) { setError((e as Error).message); } finally { setBusy(""); }
  };

  if (offline) {
    return <div className="page"><h1>Headquarters</h1><p className="error" role="alert">The backend isn't running. Start it from the backend folder with: uv run uvicorn buildanything.api:app --port 8787</p></div>;
  }
  if (!project) {
    return (
      <div className="page">
        <h1>Headquarters</h1>
        <p className="intro">{list && list.length ? "Choose a project with the switcher at the top left, or start a new one." : "Start your first project."}</p>
        {error && <p className="error" role="alert">{error}</p>}
        <div className="grid">
          {creating ? <NewProject onCreated={(p) => { setCreating(false); refreshList(); selectProject(p.id); }} /> : <section className="card"><h3>Start something new</h3><button className="primary" onClick={() => setCreating(true)}>New project</button><JoinProject onJoined={(p) => { refreshList(); selectProject(p.id); }} /></section>}
          <DisconnectedProjects onReconnected={(id) => { refreshList(); selectProject(id); }} />
        </div>
      </div>
    );
  }

  const p = `/projects/${project.id}`;
  const of = (type: string) => project.records.filter((r) => r.type === type);
  const live = (r: Rec) => r.status !== "superseded";
  const claims = of("claim");
  const requirements = of("requirement").filter(live);
  const tasks = [...of("task").filter(live)].sort((a, b) => a.fields.start_week - b.fields.start_week);
  const drafts = of("concept_draft");
  const latest = [...drafts].reverse().find((d) => d.fields.summary);
  const conceptMessages: ChatMessage[] = drafts.flatMap((d): ChatMessage[] => [...(d.fields.message ? [{ from: "you" as const, text: d.fields.message }] : []), { from: "agent" as const, text: d.fields.reply, questions: d.fields.questions }]);
  if (sending) conceptMessages.push({ from: "you", text: sending });
  const sendConcept = (text: string) => { setSending(text); run("", async () => { try { setProject(await call<Project>(`${p}/concept`, "POST", { message: text })); } finally { setSending(""); } }); };
  const findings = of("finding");
  const latestRun = Math.max(0, ...findings.map((f) => f.fields.run));
  const openFindings = findings.filter((f) => f.fields.run === latestRun || (f.fields.severity === "high" && f.status === "proposed"));
  const pendingPlan = [...requirements, ...tasks].filter((r) => r.status === "proposed");
  const pct = Math.min(100, (project.spent / project.limit) * 100);

  const decideFinding = (id: string, decision: string, reason: string) => run("Saving", async () => setProject(await call<Project>(`${p}/findings/${id}/decide`, "POST", { decision, reason })));
  const decide = (id: string, action: "confirm" | "reject") => run("Saving", async () => setProject(await call<Project>(`${p}/records/${id}/${action}`, "POST")));

  const spendRing = 2 * Math.PI * 22;
  const pendingClaims = claims.filter((c) => c.status === "proposed");
  const highFindings = openFindings.filter((f) => f.fields.severity === "high");

  return (
    <div className="page hq">
      <div className="hq-top">
        <div className="hq-title">
          <h1>{project.meta.name}</h1>
          <details className="hq-brief"><summary>{(project.meta.aim || "No aim set").slice(0, 110)}{(project.meta.aim || "").length > 110 ? "…" : ""}</summary><p><b>Aim:</b> {project.meta.aim || "Not set"}</p><p><b>Scope:</b> {project.meta.scope || "Not set"}</p></details>
        </div>
        <div className="hq-spend-ring" role="button" tabIndex={0} title={`AI spend: $${project.spent.toFixed(3)} of $${project.limit.toFixed(2)}. Click to change the limit.`}
          onClick={() => { const v = window.prompt("AI spending limit for this project, in US dollars:", project.limit.toFixed(2)); const usd = v ? Number(v) : NaN; if (usd > 0) run("Saving", async () => { await call(`${p}/limit`, "POST", { usd }); setProject(await call<Project>(p)); }); }}>
          <svg viewBox="0 0 56 56" aria-hidden="true"><circle cx="28" cy="28" r="22" className="ring-track" /><circle cx="28" cy="28" r="22" className={`ring-part ${pct > 85 ? "hot" : ""}`} strokeDasharray={`${(pct / 100) * spendRing} ${spendRing}`} /></svg>
          <span><b>${project.spent.toFixed(2)}</b><small>of ${project.limit.toFixed(0)}</small></span>
        </div>
        <button className={`small ${hq?.needs.some((n) => n.kind === "paused") ? "primary" : ""}`} disabled={!!busy} title="Paused: running jobs finish and nothing new starts"
          onClick={() => { const pause = !hq?.needs.some((n) => n.kind === "paused"); run(pause ? "Pausing" : "Resuming", async () => { setProject(await call<Project>(`${p}/hq/pause`, "POST", { paused: pause })); setHq(await call<Hq>(`${p}/hq`)); }); }}>
          {hq?.needs.some((n) => n.kind === "paused") ? "Resume" : "Pause"}</button>
        <details className="hq-more">
          <summary aria-label="Saving, handoff and team">⋯</summary>
          <div className="hq-more-body">
            <div className="decide"><button className="small" disabled={!!busy} title="Takes the project out of the app. Its folder stays on disk, untouched, and you can connect it again from the Headquarters start screen."
              onClick={() => { if (window.confirm(`Disconnect ${project.meta.name} from the app? Nothing is deleted: its folder stays at ${project.id}, it's paused, and you can connect it again from the Headquarters start screen.`)) run("Disconnecting", async () => { await call(`${p}/disconnect`, "POST", { off: true }); try { localStorage.removeItem("ba.project"); } catch { /* optional */ } window.location.reload(); }); }}>Disconnect this project</button></div>
            <span className="dim-text">{hq?.outbox?.hold ? "Holding new work until you press Send" : `New work starts ${hq?.outbox?.quietSeconds ?? 60} s after your last action`}</span>
            <div className="decide"><button className="small" disabled={!!busy} title="For a long review: nothing new starts until you press Send." onClick={() => run("Saving", async () => { setProject(await call<Project>(`${p}/hq/hold`, "POST", { hold: !hq?.outbox?.hold })); setHq(await call<Hq>(`${p}/hq`)); })}>{hq?.outbox?.hold ? "Stop holding" : "Hold until I press Send"}</button></div>
            <span className={project.git.unsaved ? "warn-text" : "dim-text"}>{!project.git.available ? "Git isn't available" : project.git.unsaved ? `${project.git.unsaved} unsaved changes` : "All changes saved"}</span>
            <div className="decide">
              {project.git.available && project.git.unsaved > 0 && <button className="small" disabled={!!busy} onClick={() => run("Saving", async () => setProject(await call<Project>(`${p}/save`, "POST")))}>Save</button>}
              <button className="small" disabled={!!busy} onClick={() => run("Writing handoff", async () => { const r = await call<{ file: string; content: string; project: Project }>(`${p}/handoff`, "POST"); setProject(r.project); setNote(`Handoff saved to ${r.file}`); })}>Create handoff</button>
            </div>
            <TeamPanel p={p} project={project} setProject={setProject} busy={busy} run={run} setNote={setNote} />
          </div>
        </details>
      </div>

      <div className="gates">
        {project.gates.map((g) => (<button key={g.id} className={`gate ${g.approved ? "done" : g.id === project.currentGate ? "active" : "todo"}`} onClick={() => go("Journey")}><span className="mono">{STAGES[g.id] ?? g.name}</span>{g.imported && <small className="imported-tag">Imported</small>}</button>))}
      </div>

      {busy && <p className="busy" role="status">{busy}…</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {note && <p className="note" role="status">{note}</p>}

      {hq?.outbox && (hq.outbox.pending || hq.outbox.hold) && (
        <div className="note outbox-bar" role="status">
          <span>{hq.outbox.wait === null
            ? "Holding new work for the departments until you press Send. Chats still answer at once."
            : `Departments get your answers and approvals together, in ${Math.max(0, hq.outbox.wait)} s, once you've stopped for ${hq.outbox.quietSeconds} s. Chats still answer at once.`}</span>{" "}
          <button className="small primary" disabled={!!busy} onClick={() => run("Sending", async () => { setProject(await call<Project>(`${p}/hq/send-now`, "POST")); setHq(await call<Hq>(`${p}/hq`)); })}>Send now</button>{" "}
          <button className="small" disabled={!!busy} title="Hold: nothing new starts until you press Send, however long you take. Pause, Stop and spending changes still go through at once."
            onClick={() => run("Saving", async () => { setProject(await call<Project>(`${p}/hq/hold`, "POST", { hold: !hq.outbox!.hold })); setHq(await call<Hq>(`${p}/hq`)); })}>{hq.outbox.hold ? "Stop holding" : "Hold"}</button>
        </div>
      )}

      {(hq?.running || (hq?.needs.length ?? 0) > 0) && (
        <section className="hq-needs" aria-label="What needs you">
          {hq?.running && <div className="agent-thinking" role="status"><i /><i /><i /><span>{hq.running}</span></div>}
          {hq?.needs.map((n) => (
            <div className={`need-card ${n.kind}`} key={n.kind + n.text}>
              <b>{n.kind === "failed" ? "Stopped" : n.kind === "paused" ? "Paused" : n.kind === "gate" ? "Ready to continue" : "Needs you"}</b>
              <span><Linked text={n.text} /></span>
              <span className="decide">
                {n.kind === "failed" && <button className="primary small" disabled={!!busy} onClick={() => run("Restarting", async () => setProject(await call<Project>(`${p}/hq/retry`, "POST")))}>Try again</button>}
                {n.kind === "no_sources" && <>
                  <button className="small" disabled={!!busy} onClick={() => run("Restarting", async () => setProject(await call<Project>(`${p}/hq/retry`, "POST")))}>Search again</button>
                  <input value={waive} onChange={(e) => setWaive(e.target.value)} placeholder="Why go ahead without sources?" aria-label="Reason to continue without sources" />
                  <button className="small" disabled={!!busy || !waive.trim()} onClick={() => run("Saving", async () => setProject(await call<Project>(`${p}/hq/waive-research`, "POST", { reason: waive })))}>Continue without</button>
                </>}
                {n.goto && <ScreenChip screen={n.goto as string}>Open</ScreenChip>}
                {n.kind === "tool" && n.key && <button className="small" disabled={!!busy} title="Close this request: for example it's solved, or a tool you approved covers it." onClick={() => { const reason = window.prompt("Close this tool request? Optionally say why (for example, the tool you approved covers it).", ""); if (reason !== null) run("Closing", async () => { await call(`/services/asks/${n.key}/close`, "POST", { reason }); setHq(await call<Hq>(`${p}/hq`)); }); }}>Close</button>}
                {n.kind === "tab_idea" && <button className="small" disabled={!!busy} onClick={() => run("Saving", async () => { await call(`${p}/tab-ideas/dismiss`, "POST", { key: n.key }); setHq(await call<Hq>(`${p}/hq`)); })}>Not needed</button>}
                {n.kind === "gate" && <button className="primary small" disabled={!!busy} onClick={() => run("Approving", async () => setProject(await call<Project>(`${p}/gates/${n.gate}/approve`, "POST")))}>Approve</button>}
              </span>
            </div>
          ))}
        </section>
      )}

      <HqGlance projectId={project.id} version={hq?.eventCount ?? 0} />

      <TeamStory compact />

      <div className="concept-layout">
        <AgentChat agent="Concept Partner" role="Your contact at Headquarters" messages={conceptMessages} thinking={sending ? "Thinking" : hq?.running?.startsWith("Concept") ? hq.running : undefined} onSend={sendConcept} />
        <section className="card concept-panel">
          <div className="eyebrow">{hq?.conceptApproved ? "Approved concept" : "Draft concept"}</div>
          {latest ? (
            <>
              {latest.fields.decisions.length > 0 && <div className="chip-cloud">{latest.fields.decisions.map((d: string) => <span key={d} className="idea-chip done" title={d}>✓ {d.length > 70 ? d.slice(0, 68) + "…" : d}</span>)}</div>}
              {latest.fields.open_questions.length > 0 && <div className="chip-cloud">{latest.fields.open_questions.map((d: string) => <span key={d} className="idea-chip open" title={d}>? {d.length > 70 ? d.slice(0, 68) + "…" : d}</span>)}</div>}
              <details className="concept-summary" open={!hq?.conceptApproved}><summary>The concept in words</summary><Rendered text={latest.fields.summary} /></details>
              {!hq?.conceptApproved && shapeView && (
                <details className="concept-shape" open>
                  <summary>Team and stages Headquarters suggests</summary>
                  <ShapeEditor view={shapeView} busy={!!busy || !!hq?.running}
                    start={shapeView.proposal ? { team: shapeView.proposal.team, gates: shapeView.proposal.gates } : { team: shapeView.team, gates: shapeView.gates }}
                    saveLabel={latest.fields.open_questions.length ? "Approve anyway" : "Approve concept"}
                    onSave={(s) => run("Approving the concept", async () => setProject(await call<Project>(`${p}/concept/approve`, "POST", s)))} />
                </details>
              )}
            </>
          ) : <p className="hint">The Concept Partner is preparing a first proposal.</p>}
        </section>
      </div>

      <div className="hq-folds">
        <details className="card hq-fold" open={pendingPlan.length > 0}>
          <summary><b>Requirements</b><span className="fold-count">{requirements.length}</span>{pendingPlan.length > 0 && <span className="pill warn">{pendingPlan.length} to confirm</span>}<ScreenChip screen="Tests">Tests</ScreenChip></summary>
          {pendingPlan.length > 0 && <button disabled={!!busy} onClick={() => run("Confirming", async () => { let last: Project | null = null; for (const r of pendingPlan) last = await call<Project>(`${p}/records/${r.id}/confirm`, "POST"); if (last) setProject(last); })}>Confirm all ({pendingPlan.length})</button>}
          <div className="req-grid">{requirements.map((r) => <div key={r.id} className={`req-card ${r.status}`} title={`Tested by: ${r.fields.verification}`}><span className="mono">{r.fields.key}</span><span>{r.fields.text}</span>{r.status === "proposed" && <Decide rec={r} onDecide={decide} />}</div>)}</div>
          {requirements.length === 0 && <p className="hint">Written automatically once Research is done.</p>}
        </details>
        <details className="card hq-fold" open={pendingClaims.length > 0}>
          <summary><b>Sourced claims</b><span className="fold-count">{claims.length}</span>{pendingClaims.length > 0 && <span className="pill warn">{pendingClaims.length} to confirm</span>}<ScreenChip screen="Sources">Library</ScreenChip></summary>
          {claims.length === 0 ? <p className="hint">Starts automatically when you approve the concept.</p> : (
            <div className="claim-list">{claims.map((c) => <div key={c.id} className="claim-row"><span>{c.fields.statement}</span><q className="quote">{c.fields.quote}</q><span className="dim-text small-text">{c.fields.url ? <a href={c.fields.url} target="_blank" rel="noreferrer">{c.fields.title}</a> : c.fields.title}</span>{c.status === "proposed" ? <Decide rec={c} onDecide={decide} /> : <span className="pill good">✓</span>}</div>)}</div>
          )}
        </details>
        <details className="card hq-fold" open={openFindings.some((f) => !f.decision || f.decision.decision === "fix")}>
          <summary><b>Independent Verifier</b><span className="fold-count">{openFindings.length}</span>{highFindings.length > 0 && <span className="pill bad">{highFindings.length} high</span>}</summary>
          {openFindings.length === 0 ? <p className="hint">Reviews the plan automatically as soon as it's written.</p> : (
            <div className="claim-list">{openFindings.map((f) => <div key={f.id} className="claim-row finding-row"><span className={`pill ${f.fields.severity === "high" ? "bad" : f.fields.severity === "medium" ? "warn" : ""}`}>{f.fields.severity}</span><span>{f.fields.safety && <span className="pill bad">Safety</span>}{f.fields.kind === "beyond_scope" && <span className="pill warn">Beyond what you asked</span>} <Linked text={f.fields.text} /></span><FindingDecision rec={f} onDecide={decideFinding} /></div>)}</div>
          )}
        </details>
      </div>
    </div>
  );
}


// Projects taken out of the app: their folders are still on disk, and each can be connected again.
function DisconnectedProjects({ onReconnected }: { onReconnected: (id: string) => void }) {
  const [off, setOff] = useState<(Summary & { disconnected?: boolean })[]>([]);
  const { busy, run } = useBusy();
  useEffect(() => { call<(Summary & { disconnected?: boolean })[]>("/projects?include_disconnected=true").then((l) => setOff(l.filter((x) => x.disconnected))).catch(() => undefined); }, []);
  if (off.length === 0) return null;
  return (
    <section className="card"><h3>Disconnected projects</h3>
      <p className="hint">Taken out of the app; their folders are still on disk. Connecting one brings it back paused.</p>
      <ul className="status-list">{off.map((x) => <li key={x.id}><span>{x.name ?? x.id} <span className="hint mono">{x.id}</span></span>
        <button className="small" disabled={!!busy} onClick={() => run("Connecting", async () => { await call(`/projects/${x.id}/disconnect`, "POST", { off: false }); setOff((l) => l.filter((y) => y.id !== x.id)); onReconnected(x.id); })}>Connect again</button></li>)}</ul>
    </section>
  );
}
