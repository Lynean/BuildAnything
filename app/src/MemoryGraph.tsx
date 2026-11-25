import { useEffect, useMemo, useState } from "react";
import { Background, Controls, ReactFlow, type Edge, type Node } from "@xyflow/react";
import { call } from "./api";

// The project's memory as a graph: requirements, the tests that prove them, what the design needs and the parts chosen,
// connections, code, messages, assemblies, decisions and sourced facts. Every link points from what depends to what it
// depends on, so a change to one item can be followed to everything it affects.
interface MemNode { id: string; kind: string; label: string; detail: string; }
interface MemEdge { from: string; to: string; kind: string; }
interface Memory { nodes: MemNode[]; edges: MemEdge[]; counts: Record<string, number>; truncated: boolean; }

const KINDS: { kind: string; name: string; color: string }[] = [
  { kind: "requirement", name: "Requirements", color: "#e8a659" }, { kind: "test", name: "Tests", color: "#ff6f91" },
  { kind: "need", name: "Design needs", color: "#f2c94c" }, { kind: "decision", name: "Decisions", color: "#54d6c2" },
  { kind: "fact", name: "Sourced facts", color: "#7f8cff" }, { kind: "component", name: "Components", color: "#f2c94c" },
  { kind: "part", name: "Parts", color: "#f0894b" }, { kind: "assembly", name: "Assemblies", color: "#f0894b" },
  { kind: "net", name: "Connections", color: "#c08bff" }, { kind: "code", name: "Code", color: "#5aa9ff" },
  { kind: "packet", name: "Messages", color: "#6fd08c" }, { kind: "device", name: "Devices", color: "#8fa8c8" },
];
const COLOR = Object.fromEntries(KINDS.map((k) => [k.kind, k.color]));
const NAME = Object.fromEntries(KINDS.map((k) => [k.kind, k.name]));

export default function MemoryGraph({ projectId }: { projectId: string }) {
  const [memory, setMemory] = useState<Memory | null>(null);
  const [error, setError] = useState("");
  const [hidden, setHidden] = useState<Set<string>>(new Set(["code"]));
  const [search, setSearch] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  useEffect(() => { setMemory(null); setPicked(null); call<Memory>(`/projects/${projectId}/memory`).then(setMemory).catch((e) => setError((e as Error).message)); }, [projectId]);

  const byId = useMemo(() => new Map((memory?.nodes ?? []).map((n) => [n.id, n])), [memory]);
  // Everything a change to the picked item would reach: whatever depends on it, and whatever depends on those.
  const affected = useMemo(() => {
    if (!picked || !memory) return new Set<string>();
    const out = new Set<string>(), queue = [picked];
    while (queue.length) {
      const id = queue.shift()!;
      for (const e of memory.edges) if (e.to === id && !out.has(e.from)) { out.add(e.from); queue.push(e.from); }
    }
    return out;
  }, [picked, memory]);
  const dependsOn = useMemo(() => new Set((memory?.edges ?? []).filter((e) => e.from === picked).map((e) => e.to)), [picked, memory]);

  const { nodes, edges } = useMemo(() => {
    if (!memory) return { nodes: [] as Node[], edges: [] as Edge[] };
    const q = search.trim().toLowerCase();
    const columns = KINDS.filter((k) => !hidden.has(k.kind) && memory.counts[k.kind]);
    const nodes: Node[] = [];
    columns.forEach((col, x) => {
      memory.nodes.filter((n) => n.kind === col.kind).forEach((n, y) => {
        const lit = picked === n.id || affected.has(n.id) || dependsOn.has(n.id);
        const match = q && (n.label.toLowerCase().includes(q) || n.detail.toLowerCase().includes(q));
        const dim = (picked && !lit) || (q && !match);
        nodes.push({ id: n.id, position: { x: x * 250, y: y * 52 }, data: { label: n.label }, draggable: false,
          className: `mem-node ${picked === n.id ? "picked" : ""} ${affected.has(n.id) ? "affected" : ""} ${dependsOn.has(n.id) ? "depends" : ""} ${dim ? "dim" : ""}`,
          style: { ["--k" as string]: col.color } });
      });
    });
    const shown = new Set(nodes.map((n) => n.id));
    const edges: Edge[] = memory.edges.filter((e) => shown.has(e.from) && shown.has(e.to)).map((e, i) => {
      const lit = picked && (e.from === picked || e.to === picked || (affected.has(e.from) && (affected.has(e.to) || e.to === picked)));
      return { id: `e${i}`, source: e.from, target: e.to, className: `mem-edge ${lit ? "lit" : picked ? "dim" : ""}`, label: lit ? e.kind : undefined };
    });
    return { nodes, edges };
  }, [memory, hidden, search, picked, affected, dependsOn]);

  if (error) return <p className="error">{error}</p>;
  if (!memory) return <p className="hint">Reading the project's memory…</p>;
  const node = picked ? byId.get(picked) : undefined;
  const list = (ids: Iterable<string>) => [...ids].map((id) => byId.get(id)).filter(Boolean) as MemNode[];

  return (
    <div className="memory">
      <div className="memory-tools">
        <input placeholder="Search the memory, such as R5 or L12" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search the memory" />
        <div className="memory-kinds">{KINDS.filter((k) => memory.counts[k.kind]).map((k) => (
          <button key={k.kind} className={hidden.has(k.kind) ? "off" : "on"} style={{ ["--k" as string]: k.color }} onClick={() => setHidden((h) => { const n = new Set(h); n.has(k.kind) ? n.delete(k.kind) : n.add(k.kind); return n; })}>
            {k.name} <span>{memory.counts[k.kind]}</span>
          </button>
        ))}</div>
      </div>
      <div className="memory-body">
        <div className="memory-canvas">
          <ReactFlow nodes={nodes} edges={edges} defaultViewport={{ x: 24, y: 24, zoom: 0.9 }} minZoom={0.1} edgesFocusable={false} edgesReconnectable={false} nodesConnectable={false} onNodeClick={(_, n) => setPicked((p) => (p === n.id ? null : n.id))} onPaneClick={() => setPicked(null)} proOptions={{ hideAttribution: true }}>
            <Background gap={20} color="#20252e" />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
        <aside className="memory-side">
          {!node ? (
            <>
              <h3>Memory</h3>
              <p className="hint">Everything the project knows, linked by what depends on what. Worked out from the record each time; nothing is stored separately. Click an item to see what it relies on and what a change to it would affect.</p>
              <p className="hint">{memory.nodes.length} items, {memory.edges.length} links.{memory.truncated ? " Only the first 120 code blocks are shown." : ""} Code is hidden to start with; switch it on above.</p>
            </>
          ) : (
            <>
              <div className="eyebrow" style={{ color: COLOR[node.kind] }}>{NAME[node.kind]}</div>
              <h3>{node.label}</h3>
              {node.detail && <p>{node.detail}</p>}
              <h4>It depends on ({dependsOn.size})</h4>
              <ul className="memory-list">{list(dependsOn).map((n) => <li key={n.id}><button className="link" onClick={() => setPicked(n.id)}>{n.label}</button> <small className="dim-text">{NAME[n.kind]}</small></li>)}</ul>
              <h4>A change here affects ({affected.size})</h4>
              <ul className="memory-list">{list(affected).map((n) => <li key={n.id}><button className="link" onClick={() => setPicked(n.id)}>{n.label}</button> <small className="dim-text">{NAME[n.kind]}</small></li>)}</ul>
              {affected.size === 0 && <p className="hint">Nothing else relies on it.</p>}
            </>
          )}
        </aside>
      </div>
    </div>
  );
}
