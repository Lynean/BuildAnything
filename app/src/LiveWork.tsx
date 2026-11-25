import { useEffect, useRef, useState } from "react";
import { call } from "./api";

// Who's working right now, for the home canvas: a ring on each busy node, a status bar along the bottom, and a
// chat-style bubble with one agent's live trail (its reasoning summaries, the tools it uses and what it says).
export interface Running { id: string; agent: string; area: string; title: string; activity: string; job: string | null; started: number; steps: number; }
interface Step { at: string; kind: "thought" | "tool" | "said"; text: string; }
interface Trail { id: string; title: string; area: string; activity: string; started: number; task: string; steps: Step[]; }

const DEPARTMENTS = ["Mechanical", "Electrical", "Software", "Procurement"];

/** The canvas node an agent belongs to. */
export function placeFor(r: Running, visible: Set<string>): string {
  if (r.agent === "external_service" || r.agent === "skill_reviewer") return "External Service";
  if (r.agent === "internal_service" || r.agent === "platform") return "Internal Service";
  if (r.area === "Tests" && visible.has("Tests")) return "Tests";
  if (DEPARTMENTS.includes(r.area) && visible.has(r.area)) return r.area;
  return "Headquarters";
}

export function useRunning(projectId: string | null): Running[] {
  const [running, setRunning] = useState<Running[]>([]);
  useEffect(() => {
    setRunning([]);
    if (!projectId) return;
    let live = true;
    const load = () => call<{ running?: Running[] }>(`/projects/${projectId}/work`).then((w) => live && setRunning(w.running ?? [])).catch(() => undefined);
    load();
    const t = window.setInterval(load, 2000);
    return () => { live = false; window.clearInterval(t); };
  }, [projectId]);
  return running;
}

const since = (started: number) => {
  const s = Math.max(0, Math.round(Date.now() / 1000 - started));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
};

export function StatusBar({ running, colorOf, onPick, picked }: { running: Running[]; colorOf: (r: Running) => string; onPick: (id: string) => void; picked: string | null }) {
  const [, tick] = useState(0);
  useEffect(() => { const t = window.setInterval(() => tick((n) => n + 1), 1000); return () => window.clearInterval(t); }, []);
  return (
    <div className={`live-bar ${running.length ? "busy" : ""}`} role="status" aria-live="polite">
      <span className="live-count"><i className={running.length ? "on" : ""} />{running.length ? `${running.length} ${running.length === 1 ? "agent" : "agents"} working` : "Nobody is working right now"}</span>
      <div className="live-chips">
        {running.map((r) => (
          <button key={r.id} className={`live-chip ${picked === r.id ? "on" : ""}`} style={{ ["--c" as string]: colorOf(r) }} onClick={() => onPick(r.id)} title="See what it's doing">
            <b>{r.title}</b><span>{r.activity}</span><small>{since(r.started)}</small>
          </button>
        ))}
      </div>
    </div>
  );
}

export function TrailBubble({ projectId, agentId, color, onClose, onOpen }: { projectId: string; agentId: string; color: string; onClose: () => void; onOpen: (screen: string) => void }) {
  const [trail, setTrail] = useState<Trail | null>(null);
  const [gone, setGone] = useState(false);
  const list = useRef<HTMLDivElement>(null);
  const [, tick] = useState(0);
  useEffect(() => {
    setTrail(null); setGone(false);
    let live = true;
    const load = () => call<Trail>(`/projects/${projectId}/agents/${agentId}/trail`).then((t) => live && setTrail(t)).catch(() => live && setGone(true));
    load();
    const t = window.setInterval(() => { load(); tick((n) => n + 1); }, 1500);
    return () => { live = false; window.clearInterval(t); };
  }, [projectId, agentId]);
  useEffect(() => { const el = list.current; if (el) el.scrollTop = el.scrollHeight; }, [trail?.steps.length]);
  const area = trail?.area && DEPARTMENTS.includes(trail.area) ? trail.area : trail?.area === "Reception" ? "External Service" : "Headquarters";
  return (
    <aside className="trail-bubble" style={{ ["--c" as string]: color }} aria-label="What this agent is doing">
      <header>
        <div><b>{trail?.title ?? "Agent"}</b>{trail && <small>{trail.task ? `${trail.task} · ` : ""}{since(trail.started)}</small>}</div>
        <button className="small" onClick={onClose} aria-label="Close">×</button>
      </header>
      <div className="trail-steps" ref={list}>
        {gone && !trail ? <p className="hint">It has finished. Its result is in the department's chat and the Ledger.</p> : !trail ? <p className="hint">Loading…</p> : (
          <>
            {trail.steps.length === 0 && <p className="hint">Just started.</p>}
            {trail.steps.map((s, i) => (
              <div key={i} className={`trail-step ts-${s.kind}`}>
                <span className="trail-kind">{s.kind === "thought" ? "Thinking" : s.kind === "tool" ? "Using" : "Says"}</span>
                <p>{s.text}</p>
              </div>
            ))}
            {!gone && <div className="trail-step ts-now"><span className="trail-kind">Now</span><p>{trail.activity}<span className="dots" /></p></div>}
            {gone && <p className="hint">It has finished. Its result is in the department's chat and the Ledger.</p>}
          </>
        )}
      </div>
      <footer><span className="hint">Reasoning is the model's own short summary, when it gives one.</span><button className="small" onClick={() => onOpen(area)}>Open {area}</button></footer>
    </aside>
  );
}
