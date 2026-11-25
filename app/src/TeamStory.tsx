import { useEffect, useMemo, useRef, useState } from "react";
import { call, useProject } from "./api";
import { Linked, ScreenChip } from "./Linked";
import MemoryGraph from "./MemoryGraph";

interface Step { i: number; at: string; kind: "say" | "hand" | "status" | "spawn" | "despawn"; from: string; to: string; text: string; label: string; detail: string; clone: number; }

// The stage in three rows, in percent of its width and height, kept clear of the edges:
// you, Headquarters and Reception on top; the project's own departments in the middle; the record and the checks below.
const DEPARTMENTS = ["Mechanical", "Electrical", "Software", "Procurement"];
const SERVICES = ["External Service", "Internal Service"];
// Each member's colour, the same as on the canvas: used for their outline on the stage and their messages in the chat.
const COLOR: Record<string, string> = { You: "#e8a659", Headquarters: "#54d6c2", Mechanical: "#f0894b", Electrical: "#f2c94c", Software: "#5aa9ff", Procurement: "#6fd08c",
  "External Service": "#8fa8c8", "Internal Service": "#9aa7b8", Ledger: "#a493f2", Checks: "#ff6f91" };
const colorOf = (name: string) => COLOR[name] ?? COLOR[name.replace(/ clone \d+$/, "")] ?? "#8c93a1";
const time = (at: string) => { const d = new Date(at); return d.toDateString() === new Date().toDateString() ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : d.toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); };
const ROLE: Record<string, string> = { You: "human", Ledger: "data", Checks: "tool", "External Service": "service", "Internal Service": "service" };
const INITIALS: Record<string, string> = { You: "You", Headquarters: "HQ", Mechanical: "ME", Electrical: "EE", Software: "SW", Procurement: "PR", Ledger: "", Checks: "✓", "External Service": "EX", "Internal Service": "IN" };

function layout(departments: string[]): Record<string, [number, number]> {
  const seats: Record<string, [number, number]> = { You: [11, 17], Headquarters: [38, 17], "External Service": [66, 17], "Internal Service": [89, 17], Ledger: [38, 84], Checks: [66, 84] };
  departments.forEach((d, i) => { seats[d] = [departments.length === 1 ? 50 : 14 + (72 * i) / (departments.length - 1), 51]; });
  return seats;
}

function seatIn(seats: Record<string, [number, number]>, name: string): [number, number] {
  if (seats[name]) return seats[name];
  const m = name.match(/^(\w+) clone (\d+)$/);
  if (m && seats[m[1]]) {
    const [x, y] = seats[m[1]];
    const angle = (Number(m[2]) * 70 + 200) * (Math.PI / 180);
    return [Math.min(90, Math.max(10, x + Math.cos(angle) * 7)), y + Math.sin(angle) * 10];
  }
  return [50, 51];
}

interface Job { id: string; department: string; title: string; activity?: string; clone?: number; }
interface Work { departments: { name: string; state: string; jobs: Job[] }[]; agents?: { id: string; title: string; activity: string }[]; }

/** What's running right now: the Control room's live view, as a strip above the story. */
function WorkingNow({ projectId }: { projectId: string }) {
  const [work, setWork] = useState<Work | null>(null);
  useEffect(() => {
    const load = () => call<Work>(`/projects/${projectId}/work`).then(setWork).catch(() => undefined);
    load();
    const t = window.setInterval(load, 3000);
    return () => window.clearInterval(t);
  }, [projectId]);
  const jobs = (work?.departments ?? []).flatMap((d) => d.jobs.map((j) => ({ ...j, department: d.name })));
  const waiting = (work?.departments ?? []).filter((d) => d.state === "waiting_for_you").map((d) => d.name);
  if (!jobs.length && !waiting.length && !(work?.agents ?? []).length) return <div className="working-now idle"><span className="hint">Nobody is working right now.</span></div>;
  return (
    <div className="working-now">
      {jobs.map((j) => <span key={j.id} className="working-job"><i className="working-dot" /><ScreenChip screen={j.department} /><span>{j.title.slice(0, 70)}</span>{j.activity && <em>{j.activity}</em>}</span>)}
      {(work?.agents ?? []).map((a) => <span key={a.id} className="working-job"><i className="working-dot" /><b>{a.title}</b><em>{a.activity}</em></span>)}
      {waiting.map((d) => <span key={d} className="working-job wait"><ScreenChip screen={d} /> is waiting for you</span>)}
    </div>
  );
}

export default function TeamStory({ compact = false }: { compact?: boolean }) {
  const { projects, projectId, setProjectId, loadError } = useProject();
  const [expanded, setExpanded] = useState(!compact);
  const [steps, setSteps] = useState<Step[]>([]);
  const [pos, setPos] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [watching, setWatching] = useState(false);  // false: showing the current state as it happens
  const [from, setFrom] = useState("");
  const [picked, setPicked] = useState<number | null>(null);
  const log = useRef<HTMLOListElement>(null);
  const [team, setTeam] = useState<string[] | null>(null);
  const [view, setView] = useState<"Story" | "Memory">("Story");
  const [open, setOpen] = useState<Set<number>>(new Set());
  useEffect(() => { if (projectId) call<{ team: string[] }>(`/projects/${projectId}/shape`).then((r) => setTeam(r.team)).catch(() => setTeam(DEPARTMENTS)); }, [projectId]);

  useEffect(() => {
    if (!projectId) return;
    setSteps([]); setPos(0); setPicked(null); setWatching(false); setPlaying(true);
    call<{ steps: Step[] }>(`/projects/${projectId}/story`).then((r) => { setSteps(r.steps); setPos(r.steps.length); }).catch(() => undefined);
  }, [projectId]);

  // Live: new events join the story as they happen.
  useEffect(() => {
    if (!projectId) return;
    const t = window.setInterval(() => {
      setSteps((now) => {
        const last = now.length ? now[now.length - 1].i : -1;
        call<{ steps: Step[] }>(`/projects/${projectId}/story?after=${last}`).then((r) => r.steps.length && setSteps((s) => [...s, ...r.steps.filter((x) => x.i > (s.length ? s[s.length - 1].i : -1))])).catch(() => undefined);
        return now;
      });
    }, 4000);
    return () => window.clearInterval(t);
  }, [projectId]);

  useEffect(() => {
    if (watching && pos >= steps.length) { setWatching(false); setPlaying(true); return; }  // caught up: back to the current state
    if (!playing || pos >= steps.length) return;
    const t = window.setTimeout(() => setPos((p) => p + 1), 1500 / speed);
    return () => window.clearTimeout(t);
  }, [playing, pos, steps.length, speed, watching]);
  const watchFrom = () => {
    const n = Math.min(Math.max(1, Math.round(Number(from) || 1)), Math.max(1, steps.length));
    setPicked(null); setPos(n - 1); setPlaying(true); setWatching(true);
  };
  const stopWatching = () => { setWatching(false); setPlaying(true); setPos(steps.length); };
  const controls = (small: boolean) => watching ? (
    <>
      <span className="hint">Watching: step {pos} of {steps.length}</span>
      <button className={small ? "small" : ""} onClick={() => setPlaying((x) => !x)}>{playing ? "Pause" : "Play"}</button>
      <label className="hint">Speed <select aria-label="Speed" value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>{[0.5, 1, 2, 4, 8].map((v) => <option key={v} value={v}>{v}×</option>)}</select></label>
      <button className={small ? "small primary" : "primary"} onClick={stopWatching}>Stop</button>
    </>
  ) : (
    <>
      <span className="hint">Now: step {steps.length}</span>
      <label className="hint watch-from">Watch from step <input type="number" min={1} max={steps.length || 1} value={from} placeholder="1" aria-label="Step to watch from" onChange={(e) => setFrom(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") watchFrom(); }} /></label>
      <button className={small ? "small" : ""} disabled={steps.length === 0} onClick={watchFrom}>Watch</button>
    </>
  );

  useEffect(() => { const el = log.current; if (el) requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; }); }, [pos, steps.length, expanded, view, !!projects, projectId]);

  // The stage as it stands after the steps shown so far.
  const scene = useMemo(() => {
    const bubbles: Record<string, { text: string; at: number }> = {};
    const doing: Record<string, string> = {};
    const clones = new Map<string, string>();
    steps.slice(0, pos).forEach((s, n) => {
      if (s.kind === "say") bubbles[s.from] = { text: s.text, at: n };
      if (s.kind === "status") doing[s.from] = s.text;
      if (s.kind === "spawn") clones.set(s.to, s.from);
      if (s.kind === "despawn") [...clones].filter(([, parent]) => parent === s.from).slice(0, 1).forEach(([c]) => clones.delete(c));
    });
    return { bubbles, doing, clones };
  }, [steps, pos]);

  if (!projects) return compact ? null : <div className="page"><h1>Team</h1><p>{loadError || "Loading…"}</p></div>;
  if (!projectId) return compact ? null : <div className="page"><h1>Team</h1><p className="intro">Create a project in Headquarters first.</p></div>;

  // Departments on this project's team, plus any that appear in its history.
  const named = new Set(steps.flatMap((x) => [x.from, x.to]));
  const departments = DEPARTMENTS.filter((d) => (team ?? DEPARTMENTS).includes(d) || named.has(d));
  const seats = layout(departments);
  const seat = (name: string) => seatIn(seats, name);
  const current = steps[pos - 1];
  const moving = current && (current.kind === "say" || current.kind === "hand") && current.to;
  const [x0, y0] = current ? seat(current.from) : [0, 0];
  const [x1, y1] = current && current.to ? seat(current.to) : [x0, y0];
  const nodes = [...Object.keys(seats), ...scene.clones.keys()];

  return (
    <div className={compact ? `story-embed ${expanded ? "open" : ""}` : "page"}>
      {!compact && <div className="hq-head"><h1>The Ledger</h1><select aria-label="Project" value={projectId} onChange={(e) => setProjectId(e.target.value)}>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div>}
      {!compact && <p className="intro">Watch the agents work: who asks whom, what they answer, and every document and fact they take from the project record or save to it. Each step is a real event from the record.</p>}
      {!compact && <div className="viewer-tabs section-tabs ledger-tabs">{(["Story", "Memory"] as const).map((v) => <button key={v} className={v === view ? "on" : ""} onClick={() => setView(v)}>{v === "Story" ? "Story: who did what" : "Memory: what depends on what"}</button>)}</div>}
      {!compact && view === "Memory" ? <MemoryGraph projectId={projectId} /> : <>
      <WorkingNow projectId={projectId} />
      {compact && <div className="story-embed-head"><b>Between departments</b>{!expanded && controls(true)}<button className="small" onClick={() => setExpanded((x) => !x)}>{expanded ? "Smaller" : "Expand"}</button><ScreenChip screen="Team">Open the Ledger</ScreenChip></div>}
      {(!compact || expanded) && <div className="story-controls row">{controls(false)}</div>}
      <div className={`story-layout ${compact && !expanded ? "story-compact" : ""}`}>
        {(!compact || expanded) && <div className="story-stage" aria-label="The team at work">
          <svg className="story-links" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            {[...departments, ...SERVICES].map((n) => <line key={n} x1={seats.Headquarters[0]} y1={seats.Headquarters[1]} x2={seats[n][0]} y2={seats[n][1]} className={SERVICES.includes(n) ? "to-service" : ""} />)}
            {[...departments, "Headquarters"].map((n) => <line key={`l-${n}`} className="to-ledger" x1={seats[n][0]} y1={seats[n][1]} x2={seats.Ledger[0]} y2={seats.Ledger[1]} />)}
            <line x1={seats.Checks[0]} y1={seats.Checks[1]} x2={seats.Ledger[0]} y2={seats.Ledger[1]} className="to-ledger" />
            <line x1={seats.You[0]} y1={seats.You[1]} x2={seats.Headquarters[0]} y2={seats.Headquarters[1]} />
            {[...scene.clones].map(([c, parent]) => { const [x, y] = seat(c); const [px, py] = seat(parent); return <line key={c} className="clone-link" x1={px} y1={py} x2={x} y2={y} />; })}
          </svg>
          {nodes.map((n) => {
            const [x, y] = seat(n);
            const clone = scene.clones.has(n);
            const active = current && (current.from === n || current.to === n);
            const bubble = scene.bubbles[n];
            return (
              <div key={n} className={`story-node ${ROLE[n] ?? "ai"} ${clone ? "clone" : ""} ${active ? "active" : ""}`} style={{ left: `${x}%`, top: `${y}%`, ["--role" as string]: colorOf(n) }}>
                {bubble && pos - 1 - bubble.at < 4 && <div className={`story-bubble ${y < 30 ? "below" : "above"} ${x < 22 ? "from-left" : x > 78 ? "from-right" : ""}`} key={bubble.at}>{bubble.text}</div>}
                <div className="story-avatar">{n === "Ledger" ? <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h11a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Zm1 4v2h9V7Zm0 4v2h9v-2Zm0 4v2h6v-2Z" /></svg> : clone ? "◦" : INITIALS[n] ?? n.slice(0, 2)}</div>
                <div className="story-name">{clone ? `clone ${n.split(" ").pop()}` : n}</div>
                {scene.doing[n] && !clone && <div className="story-doing">{scene.doing[n]}</div>}
              </div>
            );
          })}
          {moving && (
            <div key={`${current.i}-${pos}`} className={`story-token ${current.kind}`} style={{ ["--x0" as string]: `${x0}%`, ["--y0" as string]: `${y0}%`, ["--x1" as string]: `${x1}%`, ["--y1" as string]: `${y1}%`, animationDuration: `${1100 / speed}ms` }}>
              {current.kind === "hand" ? current.label || "item" : "✉"}
            </div>
          )}
        </div>}
        <aside className="story-log">
          {(!compact || expanded) && <h3>Conversation</h3>}
          <ol ref={log} className="chat-log">
            {steps.slice(Math.max(0, pos - 80), pos).map((s, n) => {
              const index = Math.max(0, pos - 80) + n;
              const more = s.detail && s.detail !== s.text;
              const isOpen = open.has(index);
              return (
                <li key={`${s.i}-${index}`} className={`chat-msg log-${s.kind} ${s.from === "You" ? "mine" : ""} ${isOpen ? "open" : ""}`} style={{ ["--who" as string]: colorOf(s.from) }}
                  onClick={() => { if (!more) return; setOpen((o) => { const x = new Set(o); x.has(index) ? x.delete(index) : x.add(index); return x; }); setPlaying(false); }}>
                  <div className="chat-meta"><b style={{ color: colorOf(s.from) }}>{s.from}</b>{s.to ? <> → <span style={{ color: colorOf(s.to) }}>{s.to}</span></> : null}<time dateTime={s.at} title={new Date(s.at).toLocaleString()}>{time(s.at)}</time></div>
                  <div className="chat-text"><Linked text={s.text} />{s.kind === "hand" && s.label ? <span className="story-chip">{s.label}</span> : null}</div>
                  {more && (isOpen ? <div className="chat-detail"><Linked text={s.detail} /></div> : <div className="chat-more">Show more</div>)}
                </li>
              );
            })}
          </ol>
        </aside>
      </div>
      </>}
    </div>
  );
}
