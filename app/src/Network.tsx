import { useEffect, useMemo, useState } from "react";
import { Background, Handle, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import { call, useProject } from "./api";
import { actors, links, type Actor } from "./data";

interface Overview { name: string; gate: string; spent: number; limit: number; massG: number; massLimit?: number; cost: number; costLimit?: number; blocked: number; linkIssues: number; status: Record<string, string>; }

interface Job { id: string; department: string; title: string; items: string[]; status: string; activity: string; clone: number; started: number; }
interface LiveAgent { id: string; agent: string; area: string; title: string; activity: string; }
interface Department { name: string; state: string; jobs: Job[]; lastReport: string | null; lastSummary: string; questions: string[]; }
interface Finished { id: string; department: string; title: string; outcome: string; seconds: number; at: string; }
interface Work { departments: Department[]; agents: LiveAgent[]; recent: Finished[]; }

const DEPARTMENT_NODE: Record<string, string> = { Mechanical: "mech", Electrical: "elec", Software: "soft", Procurement: "proc" };
const AGENT_NODE: Record<string, string> = { concept: "concept", research: "research", verifier: "verifier", gate_reviewer: "verifier", controller: "controller", test_planner: "controller", router: "controller", change_reviewer: "controller" };
const STATE_LABEL: Record<string, string> = { working: "Working", queued: "Queued", waiting_for_you: "Waiting for you", blocked: "Blocked", problem: "Tool problem", held: "On hold", idle: "Idle" };

type ActorData = { actor: Actor; state: string; note: string; selected: boolean };
type CloneData = { label: string; state: string; title: string };

function ActorNode({ data }: NodeProps<Node<ActorData>>) {
  const { actor, state, note, selected } = data;
  return (
    <div className={`actor ${actor.kind} ${state} ${selected ? "selected" : ""}`}>
      <Handle type="target" position={Position.Top} className="hidden-handle" />
      <div className="shape"><span>{actor.initials}</span></div>
      <div className="actor-label">{actor.label}</div>
      {note ? <div className="actor-state">{note}</div> : actor.tier && <div className="actor-tier">{actor.tier}</div>}
      <Handle type="source" position={Position.Bottom} className="hidden-handle" />
    </div>
  );
}

function CloneNode({ data }: NodeProps<Node<CloneData>>) {
  return (
    <div className={`actor clone ${data.state}`} title={data.title}>
      <Handle type="target" position={Position.Top} className="hidden-handle" />
      <div className="shape"><span>{data.label}</span></div>
    </div>
  );
}

const nodeTypes = { actor: ActorNode, clone: CloneNode };
const elapsed = (from: number) => { const s = Math.max(0, Math.round(Date.now() / 1000 - from)); return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`; };

export default function Network() {
  const { projects, projectId, setProjectId } = useProject();
  const [selected, setSelected] = useState<string>("controller");
  const [view, setView] = useState<Overview | null>(null);
  const [work, setWork] = useState<Work | null>(null);
  const [holding, setHolding] = useState(false);
  const loadWork = () => { if (projectId) call<Work>(`/projects/${projectId}/work`).then(setWork).catch(() => undefined); };
  useEffect(() => { loadWork(); const timer = window.setInterval(loadWork, 2000); return () => window.clearInterval(timer); }, [projectId]);
  useEffect(() => {
    if (!projectId) return;
    const load = () => call<Overview>(`/projects/${projectId}/overview`).then(setView).catch(() => undefined);
    load();
    const timer = window.setInterval(load, 5000);
    return () => window.clearInterval(timer);
  }, [projectId]);

  const departmentOf = (id: string) => work?.departments.find((d) => DEPARTMENT_NODE[d.name] === id);
  const agentsOn = (id: string) => (work?.agents ?? []).filter((a) => (AGENT_NODE[a.agent] ?? DEPARTMENT_NODE[a.area]) === id);
  const nodes: Node[] = useMemo(() => {
    const main: Node[] = actors.map((a) => {
      const department = departmentOf(a.id);
      const busy = agentsOn(a.id);
      const running = department?.jobs.filter((j) => j.status === "running") ?? [];
      const state = department ? (department.state === "idle" && busy.length ? "working" : department.state) : busy.length ? "working" : "idle";
      const note = running.length === 1 ? running[0].activity : running.length > 1 ? `${running.length} clones working` : busy[0]?.activity ?? (state !== "idle" && state !== "working" ? STATE_LABEL[state] : "");
      return { id: a.id, type: "actor", position: { x: a.x, y: a.y }, data: { actor: a, state, note: note.slice(0, 44), selected: a.id === selected }, draggable: false };
    });
    const clones: Node[] = (work?.departments ?? []).flatMap((d) => d.jobs.length < 2 ? [] : d.jobs.map((j, i) => {
      const parent = actors.find((a) => a.id === DEPARTMENT_NODE[d.name])!;
      return { id: j.id, type: "clone", position: { x: parent.x - 36 + (i - (d.jobs.length - 1) / 2) * 66 + 26, y: parent.y + 114 }, data: { label: j.status === "queued" ? "…" : String(j.clone || i + 1), state: j.status === "running" ? "working" : "queued", title: `${j.title}: ${j.activity}` }, draggable: false };
    }));
    return [...main, ...clones];
  }, [work, selected]);
  const edges: Edge[] = useMemo(() => {
    const active = (id: string) => departmentOf(id)?.state === "working" || agentsOn(id).length > 0;
    const main: Edge[] = links.map((l) => { const on = active(l.to) || active(l.from); return { id: `${l.from}-${l.to}`, source: l.from, target: l.to, animated: on, style: { stroke: on ? "var(--ai)" : "var(--line-strong)", strokeWidth: on ? 1.8 : 1.2, strokeDasharray: l.access === "r" && !on ? "2 5" : undefined } }; });
    const clones: Edge[] = (work?.departments ?? []).flatMap((d) => d.jobs.length < 2 ? [] : d.jobs.map((j) => ({ id: `clone-${j.id}`, source: DEPARTMENT_NODE[d.name], target: j.id, animated: j.status === "running", style: { stroke: "var(--ai)", strokeWidth: 1.2 } })));
    return [...main, ...clones];
  }, [work]);

  const actor = actors.find((a) => a.id === selected)!;
  const department = departmentOf(selected);
  const busy = agentsOn(selected);
  const recent = (work?.recent ?? []).filter((r) => !department || r.department === department.name).slice(0, 8);
  const working = (work?.departments ?? []).reduce((n, d) => n + d.jobs.filter((j) => j.status === "running").length, 0) + (work?.agents.length ?? 0);
  const hold = (type: string) => { if (!department) return; setHolding(true); call(`/projects/${projectId}/departments/${department.name}/command`, "POST", { type, reason: "" }).then(loadWork).finally(() => setHolding(false)); };
  if (!view) return <div className="page"><h1>Project Network</h1><p className="intro">{projects && projects.length === 0 ? "Create a project in Headquarters first." : "Loading…"}</p></div>;
  const pct = (view.spent / view.limit) * 100;

  return (
    <div className="network">
      <div className="stats">
        <Stat label="Gate" value={view.gate} />
        <Stat label="AI spend" value={`$${view.spent.toFixed(2)}`} sub={`of $${view.limit} limit, ${pct.toFixed(0)}%`} />
        <Stat label="Mass" value={`${view.massG} g`} sub={view.massLimit ? `of ${view.massLimit} g` : "no budget set"} />
        <Stat label="Parts cost" value={String(view.cost)} sub={view.costLimit ? `of ${view.costLimit}` : "no budget set"} />
        <Stat label="Waiting" value={String(view.blocked)} sub="tasks" warn={view.blocked > 0} />
        <Stat label="Link issues" value={String(view.linkIssues)} sub="open" warn={view.linkIssues > 0} />
        <Stat label="Agents" value={String(working)} sub="working now" />
        {projects && projectId && <select className="stats-project" aria-label="Project" value={projectId} onChange={(e) => setProjectId(e.target.value)}>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>}
      </div>
      <div className="network-body">
        <div className="canvas">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{ padding: 0.08 }}
            nodesConnectable={false}
            proOptions={{ hideAttribution: true }}
            onNodeClick={(_, n) => { if (n.type === "actor") setSelected(n.id); else { const owner = work?.departments.find((d) => d.jobs.some((j) => j.id === n.id)); if (owner) setSelected(DEPARTMENT_NODE[owner.name]); } }}
          >
            <Background gap={28} size={1} color="var(--grid)" />
          </ReactFlow>
          <div className="legend">
            <div className="eyebrow">Actors</div>
            <Legend cls="human" text="Person" />
            <Legend cls="ai" text="AI agent" />
            <Legend cls="data" text="Project data" />
            <Legend cls="tool" text="Connected tool" />
            <div className="eyebrow legend-gap">Access</div>
            <div className="line-row"><i className="line solid" />Read and write</div>
            <div className="line-row"><i className="line dotted" />Read only</div>
            <div className="eyebrow legend-gap">Right now</div>
            <Legend cls="working" text="Working" />
            <Legend cls="queued" text="Queued" />
            <Legend cls="waiting" text="Needs you or stuck" />
          </div>
        </div>
        <aside className="inspector">
          <div className="eyebrow">{actor.kind === "tool" ? "Connected tool" : actor.kind === "data" ? "Project data" : actor.kind === "human" ? "Person" : "AI agent"}</div>
          <h2>{actor.label}</h2>
          <p>{actor.role}</p>
          <dl>
            <dt>Status</dt><dd>{department ? STATE_LABEL[department.state] : busy.length ? "Working" : view.status[actor.id] ?? "Unknown"}</dd>
            {actor.tier && (<><dt>Model tier</dt><dd>{actor.tier}</dd></>)}
            {actor.reads.length > 0 && (<><dt>Can read</dt><dd>{actor.reads.join(", ")}</dd></>)}
            {actor.writes.length > 0 && (<><dt>Can write</dt><dd>{actor.writes.join(", ")}</dd></>)}
          </dl>
          {department && <div className="row"><a className="page-link" href={`#${department.name}`}>Open {department.name}</a><button className="small" disabled={holding} onClick={() => hold(department.state === "held" ? "RESUME" : "HOLD")}>{department.state === "held" ? "Resume" : "Hold"}</button></div>}
          {(department?.jobs.length || busy.length) ? <><div className="eyebrow legend-gap">Doing now</div><ul className="job-list">
            {department?.jobs.map((j) => <li key={j.id}><b>{j.title}</b><small>{j.status === "queued" ? "Queued behind work on the same thing" : `${j.activity} · ${elapsed(j.started)}`}</small></li>)}
            {busy.map((a) => <li key={a.id}><b>{a.title}</b><small>{a.activity}</small></li>)}
          </ul></> : null}
          {department && department.questions.length > 0 && <><div className="eyebrow legend-gap">Waiting for your answer</div><ul className="job-list">{department.questions.map((q) => <li key={q}>{q}</li>)}</ul></>}
          {department?.lastSummary && <><div className="eyebrow legend-gap">Last report{department.lastReport ? ` · ${department.lastReport}` : ""}</div><p className="hint">{department.lastSummary}</p></>}
          {recent.length > 0 && <><div className="eyebrow legend-gap">Recently finished</div><ul className="job-list">{recent.map((r) => <li key={r.id}>{r.title}<small>{r.department} · {r.outcome} · {r.seconds}s</small></li>)}</ul></>}
        </aside>
      </div>
    </div>
  );
}

function Stat({ label, value, sub, warn }: { label: string; value: string; sub?: string; warn?: boolean }) {
  return (
    <div className="stat">
      <div className={`stat-value ${warn ? "warn-text" : ""}`}>{value}</div>
      <div className="eyebrow">{label}{sub ? <span className="stat-sub"> · {sub}</span> : null}</div>
    </div>
  );
}

function Legend({ cls, text }: { cls: string; text: string }) {
  return <div className="legend-row"><i className={`mini ${cls}`} />{text}</div>;
}
