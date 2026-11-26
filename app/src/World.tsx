import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { placeFor, StatusBar, TrailBubble, useRunning } from "./LiveWork";
import { call } from "./api";

/** One node on the project canvas. x and y are its centre on a 1600 x 900 canvas. */
export interface Place { screen: string; label: string; blurb: string; x: number; y: number; color: string; icon: string; size?: number; dept?: string; area?: boolean; }

export const W = 1600, H = 900;

// Laid out like a workflow, left to right: what feeds Headquarters, Headquarters, the departments, then what they hand on to.
export const PLACES: Place[] = [
  { screen: "Sources", label: "Library", blurb: "Research and sources", x: 170, y: 520, color: "#7f8cff", icon: "M4 3h4v18H4zm6 0h4v18h-4zm6.3.6 3.8-1 4.6 17.4-3.8 1z" },
  { screen: "Agents", label: "HR", blurb: "Every agent and its job", x: 170, y: 250, color: "#3fc7a4", icon: "M8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm8 0a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM8 13c-3 0-6 1.5-6 4v2h12v-2c0-2.5-3-4-6-4Zm8 0c-.6 0-1.3 0-1.9.2A4.6 4.6 0 0 1 16 17v2h6v-2c0-2.5-3-4-6-4Z" },
  { screen: "External Service", label: "External Service", blurb: "Online services, MCP servers and Skills", x: 1440, y: 315, color: "#8fa8c8", icon: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm6.9 6h-3a15.6 15.6 0 0 0-1.4-3.6A8 8 0 0 1 18.9 8ZM12 4c.8 1.2 1.5 2.5 1.9 4h-3.8c.4-1.5 1.1-2.8 1.9-4ZM4.3 14a8.2 8.2 0 0 1 0-4h3.4a16.5 16.5 0 0 0 0 4Zm.8 2h3c.3 1.3.8 2.5 1.4 3.6A8 8 0 0 1 5.1 16Zm3-8h-3a8 8 0 0 1 4.4-3.6C8 5.5 7.5 6.7 8.1 8ZM12 20c-.8-1.2-1.5-2.5-1.9-4h3.8c-.4 1.5-1.1 2.8-1.9 4Zm2.3-6H9.7a14.7 14.7 0 0 1 0-4h4.6a14.7 14.7 0 0 1 0 4Zm.3 5.6c.6-1.1 1.1-2.3 1.4-3.6h3a8 8 0 0 1-4.4 3.6Zm1.7-5.6a16.5 16.5 0 0 0 0-4h3.4a8.2 8.2 0 0 1 0 4Z" },
  { screen: "Internal Service", label: "Internal Service", blurb: "Project environments and tool tabs", x: 1440, y: 455, color: "#9aa7b8", icon: "M16 3v4h2V3h2v4a3 3 0 0 1-3 3h-1v3a5 5 0 0 1-4 4.9V21h-2v-3.1A5 5 0 0 1 6 13v-3H5a3 3 0 0 1-3-3V3h2v4h2V3h2v4h8V3z" },
  { screen: "Platform", label: "Platform", blurb: "File viewers and converters", x: 1440, y: 590, size: 0.85, color: "#7fb3a8", icon: "M4 4h10l6 6v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Zm9 1.5V11h5.5ZM6 14h3v2H6zm5 0h7v2h-7zm-5 4h3v2H6zm5 0h7v2h-7z" },
  { screen: "Review queue", label: "You", blurb: "Approvals and change requests", x: 560, y: 220, color: "#e8a659", icon: "M3 13h5l2 3h4l2-3h5v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zm2-9h14l2 7h-6l-2 3h-2l-2-3H3z" },
  { screen: "Headquarters", label: "Headquarters", blurb: "Talk to the team", x: 560, y: 450, color: "#54d6c2", size: 1.2, icon: "M12 2 3 7v2h18V7zM5 11v7h3v-7zm5 0v7h4v-7zm6 0v7h3v-7zM3 20v2h18v-2z" },
  { screen: "Team", label: "The Ledger", blurb: "Everything recorded, live", x: 170, y: 720, color: "#a493f2", icon: "M6 3h11a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Zm1 4v2h9V7Zm0 4v2h9v-2Zm0 4v2h6v-2Z" },
  { screen: "Mechanical", label: "Mechanical", blurb: "Parts and CAD", x: 880, y: 250, color: "#f0894b", dept: "Mechanical", icon: "M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Zm8.4 4.5.1-1-.1-1 2.1-1.6-2-3.5-2.5 1a7.6 7.6 0 0 0-1.7-1L16 3.2h-4l-.4 2.7a7.6 7.6 0 0 0-1.7 1l-2.5-1-2 3.5L7.5 11l-.1 1 .1 1-2.1 1.6 2 3.5 2.5-1c.5.4 1.1.7 1.7 1l.4 2.7h4l.4-2.7c.6-.3 1.2-.6 1.7-1l2.5 1 2-3.5z" },
  { screen: "Electrical", label: "Electrical", blurb: "Components and wiring", x: 1000, y: 420, color: "#f2c94c", dept: "Electrical", icon: "M13 2 4 14h7l-1 8 9-12h-7z" },
  { screen: "Software", label: "Software", blurb: "Code for every device", x: 880, y: 620, color: "#5aa9ff", dept: "Software", icon: "M8.6 16.6 4 12l4.6-4.6L10 8.8 6.8 12l3.2 3.2zm6.8 0L20 12l-4.6-4.6L14 8.8l3.2 3.2-3.2 3.2z" },
  { screen: "Procurement", label: "Procurement", blurb: "Prices and orders", x: 1220, y: 250, color: "#6fd08c", dept: "Procurement", icon: "M7 18a2 2 0 1 0 0 4 2 2 0 0 0 0-4Zm10 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4ZM5.2 4l.9 2H21l-3.6 7H8.1l-.9 1.6V16h12v2H5v-3l1.4-2.6L3 4H1V2h3.4z" },
  { screen: "Simulation", label: "Simulation", blurb: "Runs the design and code, and proves they work", x: 1180, y: 640, color: "#3fb6d9", dept: "Simulation", icon: "M3 3h18v14H3zm2 2v10h14V5zm2 8 3-4 2 2.5L15 7l2 6zM8 19h8v2H8z" },
  { screen: "Interface", label: "Interface", blurb: "What the departments agree on", x: 0, y: 0, area: true, color: "#c08bff", icon: "M4 15h16v2H4zm1-2c0-3.9 3.1-7 7-7s7 3.1 7 7h-2a5 5 0 0 0-10 0zM2 17h2v4H2zm18 0h2v4h-2z" },
  { screen: "Tests", label: "Tester", blurb: "Prove each requirement", x: 1440, y: 770, color: "#ff6f91", icon: "M9 2h6v2h-1v5.2l5.4 9.4A2 2 0 0 1 17.7 22H6.3a2 2 0 0 1-1.7-3.4L10 9.2V4H9zm-.9 13h7.8l-2.3-4H10.4z" },
];

// Each link is a real hand-off: what flows from the first place to the second. Links to a hidden department aren't drawn.
const EDGES: { from: string; to: string; label: string }[] = [
  { from: "Sources", to: "Headquarters", label: "Sourced facts" },
  { from: "Agents", to: "Headquarters", label: "Staffs the team" },
  { from: "Review queue", to: "Headquarters", label: "Your decisions" },
  { from: "Headquarters", to: "Team", label: "Records everything" },
  { from: "Headquarters", to: "Mechanical", label: "Assigns work" },
  { from: "Headquarters", to: "Electrical", label: "Assigns work" },
  { from: "Headquarters", to: "Software", label: "Assigns work" },
  { from: "Headquarters", to: "Procurement", label: "Assigns work" },
  { from: "Headquarters", to: "Simulation", label: "Assigns work" },
  { from: "Headquarters", to: "Tests", label: "Test plan" },
  { from: "External Service", to: "Mechanical", label: "Skills and connections" },
  { from: "External Service", to: "Electrical", label: "Skills and connections" },
  { from: "Internal Service", to: "Electrical", label: "Tool tabs" },
  { from: "Internal Service", to: "Software", label: "Python environment" },
  { from: "Mechanical", to: "Procurement", label: "Parts to buy" },
  { from: "Electrical", to: "Procurement", label: "Components to buy" },
  { from: "Electrical", to: "Software", label: "Drivers" },
  { from: "Mechanical", to: "Software", label: "CAD model" },
  { from: "Software", to: "Tests", label: "Automatic checks" },
  { from: "Software", to: "Simulation", label: "Code to run" },
  { from: "Mechanical", to: "Simulation", label: "Model to simulate" },
  { from: "Simulation", to: "Tests", label: "Results" },
  { from: "Internal Service", to: "Simulation", label: "Python environment" },
];
const JOURNEY_Y = 70, JOURNEY_X0 = 80, JOURNEY_W = 1440;
const R = 30;  // node radius on the canvas
const NAME = 30;  // room for a node's name under it
// The Database frame holds the project's records: the Ledger and the Library.
const DATABASE = { x: 60, y: 430, w: 220, h: 360 };
// Reception holds the two services that bring tools into the app.
const RECEPTION = { x: 1330, y: 250, w: 220, h: 410 };

interface Stop { id: string; stage: string; state: string; }
interface Dept { name: string; state: string; jobs: { id: string }[]; }

export function placeOf(screen: string): Place | undefined { return PLACES.find((p) => p.screen === screen); }

/** The canvas camera: everything, or zoomed onto one node with room for the open card. */
export function camera(view: { w: number; h: number }, focus?: Place): CSSProperties {
  const fit = Math.min(view.w / W, view.h / H);
  if (!focus) return { transform: `translate(${(view.w - W * fit) / 2}px, ${(view.h - H * fit) / 2}px) scale(${fit})` };
  const zoom = fit * 2.6;
  const tx = view.w * 0.13 - focus.x * zoom, ty = view.h * 0.5 - focus.y * zoom;
  return { transform: `translate(${tx}px, ${ty}px) scale(${zoom})` };
}

/** A workflow-style curve between two nodes, leaving and entering on the facing sides. */
function curve(a: Place, b: Place): { d: string; mx: number; my: number } {
  const ra = R * (a.size ?? 1), rb = R * (b.size ?? 1);
  const horizontal = Math.abs(b.x - a.x) >= Math.abs(b.y - a.y);
  if (horizontal) {
    const dir = Math.sign(b.x - a.x) || 1;
    const x1 = a.x + dir * ra, x2 = b.x - dir * rb, k = Math.max(40, Math.abs(x2 - x1) * 0.45);
    return { d: `M ${x1} ${a.y} C ${x1 + dir * k} ${a.y}, ${x2 - dir * k} ${b.y}, ${x2} ${b.y}`, mx: (x1 + x2) / 2, my: (a.y + b.y) / 2 };
  }
  // A node's name sits under it, so a line leaves or enters from below beneath the name.
  const dir = Math.sign(b.y - a.y) || 1;
  const y1 = a.y + (dir > 0 ? ra + NAME : -ra), y2 = b.y + (dir > 0 ? -rb : rb + NAME), k = Math.max(30, Math.abs(y2 - y1) * 0.45);
  return { d: `M ${a.x} ${y1} C ${a.x} ${y1 + dir * k}, ${b.x} ${y2 - dir * k}, ${b.x} ${y2}`, mx: (a.x + b.x) / 2, my: (y1 + y2) / 2 };
}

export default function World({ projectId, review, onOpen, focus, children }: { projectId: string; review: number; onOpen: (screen: string) => void; focus?: string; children?: ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ w: 1400, h: 900 });
  const [stops, setStops] = useState<Stop[]>([]);
  const [depts, setDepts] = useState<Dept[]>([]);
  const [team, setTeam] = useState<string[] | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const running = useRunning(projectId);
  const [picked, setPicked] = useState<string | null>(null);
  useEffect(() => setPicked(null), [projectId]);
  useLayoutEffect(() => {
    // The canvas resizes when the file explorer opens beside it, not only with the window.
    const measure = () => box.current && setView({ w: box.current.clientWidth, h: box.current.clientHeight });
    measure();
    const watch = new ResizeObserver(measure);
    if (box.current) watch.observe(box.current);
    return () => watch.disconnect();
  }, []);
  useEffect(() => {
    setTeam(null);
    if (!projectId) return;
    const load = () => {
      call<Stop[]>(`/projects/${projectId}/gates`).then(setStops).catch(() => undefined);
      call<{ departments: Dept[] }>(`/projects/${projectId}/work`).then((w) => setDepts(w.departments)).catch(() => undefined);
      call<{ team: string[] }>(`/projects/${projectId}/shape`).then((s) => setTeam(s.team)).catch(() => undefined);
    };
    load();
    const t = window.setInterval(load, 5000);
    return () => window.clearInterval(t);
  }, [projectId]);

  // A department that isn't on this project's team isn't shown at all.
  const visible = PLACES.filter((p) => !p.area && (!p.dept || (team ?? []).includes(p.dept)));
  const departments = visible.filter((p) => p.dept);
  const area = departments.length ? {
    x: Math.min(...departments.map((p) => p.x)) - 110, y: Math.min(...departments.map((p) => p.y)) - 80,
    w: Math.max(...departments.map((p) => p.x)) - Math.min(...departments.map((p) => p.x)) + 220,
    h: Math.max(...departments.map((p) => p.y)) - Math.min(...departments.map((p) => p.y)) + 170,
  } : null;
  const shown = new Set(visible.map((p) => p.screen));
  const edges = EDGES.filter((e) => shown.has(e.from) && shown.has(e.to));
  const stateOf = (dept?: string) => depts.find((d) => d.name === dept)?.state ?? "idle";
  // Who's running where: every live agent belongs to one node.
  const busyAt: Record<string, typeof running> = {};
  running.forEach((r) => { (busyAt[placeFor(r, shown)] ??= []).push(r); });
  const colorOf = (r: (typeof running)[number]) => placeOf(placeFor(r, shown))?.color ?? "#a493f2";
  const working = (screen: string) => { const p = placeOf(screen)!; return !!busyAt[screen] || (!!p.dept && stateOf(p.dept) === "working") || (screen === "Review queue" && review > 0); };
  const focused = focus ? placeOf(focus) : undefined;
  const lit = hover ?? focus ?? null;

  const current = stops.findIndex((s) => s.state === "current");
  const allDone = stops.length > 0 && stops.every((s) => s.state === "approved" || s.state === "imported");
  const done = stops.length ? (current >= 0 ? current : allDone ? stops.length - 1 : 0) / Math.max(1, stops.length - 1) : 0;

  return (
    <div className={`world ${focused ? "zoomed" : ""}`} ref={box}>
      <div className="world-map" style={camera(view)}>
        <div className="frame database" style={{ left: DATABASE.x, top: DATABASE.y, width: DATABASE.w, height: DATABASE.h }}><span className="frame-label">Database</span></div>
        <div className="frame reception" style={{ left: RECEPTION.x, top: RECEPTION.y, width: RECEPTION.w, height: RECEPTION.h }}><span className="frame-label">Reception</span></div>
        {area && (
          <div className={`frame interface ${focus === "Interface" ? "focused" : ""}`} style={{ left: area.x, top: area.y, width: area.w, height: area.h, ["--c" as string]: "#c08bff" }}
            onClick={() => onOpen("Interface")} title="Open Interface: what the departments agree on">
            <button className="frame-label" onClick={(e) => { e.stopPropagation(); onOpen("Interface"); }} aria-label="Interface: what the departments agree on">Interface</button>
          </div>
        )}
        <svg className="world-roads" viewBox={`0 0 ${W} ${H}`} width={W} height={H} aria-hidden="true">
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" className="arrow-head" /></marker>
            <marker id="arrow-lit" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" className="arrow-head lit" /></marker>
          </defs>
          <line x1={JOURNEY_X0} y1={JOURNEY_Y} x2={JOURNEY_X0 + JOURNEY_W} y2={JOURNEY_Y} className="journey-road" />
          {done > 0 && <line x1={JOURNEY_X0} y1={JOURNEY_Y} x2={JOURNEY_X0 + JOURNEY_W * done} y2={JOURNEY_Y} className="journey-done" />}
          {edges.map((e) => {
            const { d } = curve(placeOf(e.from)!, placeOf(e.to)!);
            const on = lit === e.from || lit === e.to;
            const busy = working(e.from) || working(e.to);
            return <path key={`${e.from}-${e.to}`} d={d} className={`link ${on ? "lit" : ""} ${busy ? "busy" : ""}`} markerEnd={`url(#${on ? "arrow-lit" : "arrow"})`} />;
          })}
        </svg>
        {edges.map((e) => {
          if (focused || (hover !== e.from && hover !== e.to)) return null;  // labels on hover, not behind an open card
          const { mx, my } = curve(placeOf(e.from)!, placeOf(e.to)!);
          return <span key={`l-${e.from}-${e.to}`} className="link-label" style={{ left: mx, top: my }}>{e.label}</span>;
        })}

        <button className="journey-strip" style={{ left: JOURNEY_X0, top: JOURNEY_Y - 34, width: JOURNEY_W }} aria-label="Journey: from idea to a proven device" onClick={() => onOpen("Journey")}>
          {stops.map((s, i) => (
            <span key={s.id} className={`milestone ${s.state}`} style={{ left: `${(i / Math.max(1, stops.length - 1)) * 100}%` }}>
              <i /><b>{s.stage}</b>
            </span>
          ))}
          {stops.length === 0 && <span className="milestone current" style={{ left: "0%" }}><i /><b>Journey</b></span>}
        </button>

        {visible.map((p) => {
          const agentsHere = busyAt[p.screen] ?? [];
          const state = agentsHere.length ? "working" : p.dept ? stateOf(p.dept) : "";
          const badge = p.screen === "Review queue" && review > 0 ? review : null;
          const note = agentsHere.length ? agentsHere[0].activity : state === "working" ? "Working…" : state === "waiting_for_you" ? "Waiting for you" : p.blurb;
          return (
            <div key={p.screen} className="node-spot" style={{ left: p.x, top: p.y }}>
              <button className={`node ${state} ${focus === p.screen ? "focused" : ""} ${state === "working" || state === "waiting_for_you" ? "show-note" : ""}`} aria-label={`${p.label}: ${p.blurb}`} onClick={() => onOpen(p.screen)}
                onMouseEnter={() => setHover(p.screen)} onMouseLeave={() => setHover(null)} onFocus={() => setHover(p.screen)} onBlur={() => setHover(null)}
                style={{ ["--s" as string]: p.size ?? 1, ["--c" as string]: p.color }}>
                <span className="orb">
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d={p.icon} /></svg>
                  {agentsHere.length > 0 && <span className="busy-ring" aria-hidden="true" />}
                  {badge !== null && <span className="badge-dot">{badge}</span>}
                </span>
                <span className="node-name">{p.label}</span>
                <span className="node-note">{note}</span>
              </button>
              {agentsHere.length > 0 && (
                <button className="node-talk" style={{ ["--c" as string]: p.color }} onClick={() => setPicked(agentsHere[0].id)} title={`See what ${agentsHere.map((a) => a.title).join(", ")} ${agentsHere.length === 1 ? "is" : "are"} doing`} aria-label={`See what ${agentsHere[0].title} is doing`}>
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h16a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9l-5 4v-4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z" /></svg>
                  {agentsHere.length > 1 && <b>{agentsHere.length}</b>}
                </button>
              )}
            </div>
          );
        })}
      </div>
      {!focused && <StatusBar running={running} colorOf={colorOf} onPick={(id) => setPicked((x) => (x === id ? null : id))} picked={picked} />}
      {!focused && picked && running.some((r) => r.id === picked) && (
        <TrailBubble projectId={projectId} agentId={picked} color={colorOf(running.find((r) => r.id === picked)!)} onClose={() => setPicked(null)} onOpen={(screen) => { setPicked(null); onOpen(screen); }} />
      )}
      {children}
    </div>
  );
}
