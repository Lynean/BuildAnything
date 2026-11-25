import { useEffect, useState, type ReactNode } from "react";
import { call, useBusy, useProject } from "./api";

function Page({ title, intro, children, projects, projectId, setProjectId }: { title: string; intro: string; children: ReactNode; projects: { id: string; name: string }[]; projectId: string; setProjectId: (id: string) => void }) {
  return (
    <div className="page">
      <div className="hq-head"><h1>{title}</h1><select aria-label="Project" value={projectId} onChange={(e) => setProjectId(e.target.value)}>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div>
      <p className="intro">{intro}</p>
      {children}
    </div>
  );
}

function Messages({ busy, error, note }: { busy: string; error: string; note?: string }) {
  return <>{busy && <p className="busy" role="status">{busy}…</p>}{error && <p className="error" role="alert">{error}</p>}{note && <p className="note" role="status">{note}</p>}</>;
}

function NoProject({ title, message }: { title: string; message: string }) {
  return <div className="page"><h1>{title}</h1><p className="intro">{message}</p></div>;
}

interface Task { id: string; key: string; title: string; department: string; start: number; weeks: number; state: string; done: boolean; }
const DEPARTMENTS = ["Mechanical", "Electrical", "Software", "Procurement"];

export function Tasks() {
  const { projects, projectId, setProjectId, loadError } = useProject();
  const { busy, error, run } = useBusy();
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const load = () => { if (projectId) call<Task[]>(`/projects/${projectId}/tasks`).then(setTasks); };
  useEffect(load, [projectId]);
  if (!projects) return <NoProject title="Tasks and timeline" message={loadError || "Loading…"} />;
  if (!projectId || !tasks) return <NoProject title="Tasks and timeline" message="Create a project in Headquarters first." />;
  const total = Math.max(8, ...tasks.map((t) => t.start + t.weeks));
  const weeks = Array.from({ length: total }, (_, i) => i + 1);
  const mark = (t: Task) => run("Saving", async () => { await call(`/projects/${projectId}/tasks/${t.id}/done`, "POST", { done: !t.done }); load(); });
  return (
    <Page title="Tasks and timeline" intro="Departments run in parallel where they can. Every waiting task says what it's waiting for." projects={projects} projectId={projectId} setProjectId={setProjectId}>
      <Messages busy={busy} error={error} />
      {tasks.length === 0 ? <p className="hint">The Project Controller writes the plan once Research is done.</p> : (
        <section className="card wide timeline">
          <div className="tl-head" style={{ gridTemplateColumns: `minmax(200px, 1.2fr) repeat(${total}, minmax(0, 1fr))` }}><div />{weeks.map((w) => <div key={w} className="mono tl-week">Wk {w}</div>)}</div>
          {DEPARTMENTS.filter((d) => tasks.some((t) => t.department === d)).map((d) => (
            <div key={d} className="tl-dept">
              <div className="eyebrow tl-title">{d}</div>
              {tasks.filter((t) => t.department === d).map((t) => (
                <div className="tl-row" key={t.id}>
                  <div className="tl-name"><span className="mono">{t.key}</span> {t.title}{!t.state.startsWith("Needs") && <button className="small tl-done" disabled={!!busy} onClick={() => mark(t)}>{t.done ? "Reopen" : "Done"}</button>}</div>
                  <div className="tl-track">
                    <div className={`tl-bar ${t.done ? "s-done" : t.state.startsWith("Waiting") ? "s-blocked" : t.state === "Ready" ? "s-inprogress" : "s-notstarted"}`} style={{ left: `${(t.start / total) * 100}%`, width: `${(t.weeks / total) * 100}%` }}>{t.state}</div>
                  </div>
                </div>
              ))}
            </div>
          ))}
        </section>
      )}
    </Page>
  );
}

/** Shows [label](https://...) links inside a claim as short links instead of raw text. */
function withLinks(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(/\(?\[([^\]]+)\]\((https?:[^)\s]+)\)\)?/g)) {
    out.push(text.slice(last, m.index));
    out.push(<a key={m.index} href={m[2]} target="_blank" rel="noreferrer">{m[1]}</a>);
    last = (m.index ?? 0) + m[0].length;
  }
  out.push(text.slice(last));
  return out;
}

interface Source { id: string; title: string; tier: string; origin: string; url: string | null; hasText: boolean; pages?: number; claims: { statement: string; status: string }[]; }
interface SourceView { sources: Source[]; decisions: { id: string; text: string }[]; tiers: string[]; }

export function Sources() {
  const { projects, projectId, setProjectId, loadError } = useProject();
  const { busy, error, note, setNote, run } = useBusy();
  const [view, setView] = useState<SourceView | null>(null);
  const [tier, setTier] = useState("");
  const base = `/projects/${projectId}/sources`;
  useEffect(() => { if (projectId) call<SourceView>(base).then(setView); }, [projectId]);
  if (!projects) return <NoProject title="Sources and decisions" message={loadError || "Loading…"} />;
  if (!projectId || !view) return <NoProject title="Sources and decisions" message="Create a project in Headquarters first." />;
  const upload = (file: File) => run("Reading the PDF", async () => {
    const r = await call<SourceView>(`${base}?filename=${encodeURIComponent(file.name)}&tier=${encodeURIComponent(tier)}`, "POST", undefined, file);
    setView(r); setNote(`${file.name} added. The Research Engineer can now search it.`);
  });
  return (
    <Page title="Sources and decisions" intro="Project truth comes from sources with verified quotes, and from decisions you made." projects={projects} projectId={projectId} setProjectId={setProjectId}>
      <Messages busy={busy} error={error} note={note} />
      <div className="grid">
        <section className="card wide">
          <h3>Sources</h3>
          <div className="card-action">
            <select aria-label="Source type" value={tier} onChange={(e) => setTier(e.target.value)}><option value="">Choose type</option>{view.tiers.map((t) => <option key={t}>{t}</option>)}</select>
            <label className={`file-btn primary ${tier && !busy ? "" : "disabled"}`}><input type="file" accept="application/pdf,.pdf" aria-label="Upload source PDF" disabled={!tier || !!busy} onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />Upload source</label>
            <span className="hint">Add papers, standards or books, including ones behind a paywall.</span>
          </div>
          {view.sources.length === 0 ? <p className="hint">No sources yet. Research adds them after you approve the concept.</p> : (
            <table><thead><tr><th>Source</th><th>Claims from it</th><th>Type</th></tr></thead><tbody>
              {view.sources.map((s) => (
                <tr key={s.id}>
                  <td>{s.url ? <a className="page-link" href={s.url} target="_blank" rel="noreferrer">{s.title}</a> : s.title}<div className="dim-text small-text">{s.origin === "upload" ? `Uploaded by a person${s.pages ? `, ${s.pages} pages` : ""}` : "Found by the Research Engineer"}{!s.hasText && ". This PDF has no searchable text, so it may be a scan."}</div></td>
                  <td>{s.claims.length === 0 ? <span className="dim-text">None yet</span> : <ul className="tried">{s.claims.map((c) => <li key={c.statement}>{withLinks(c.statement)} <span className={`pill ${c.status === "confirmed" ? "good" : "warn"}`}>{c.status === "confirmed" ? "Confirmed" : "Needs review"}</span></li>)}</ul>}</td>
                  <td>{s.tier}</td>
                </tr>
              ))}
            </tbody></table>
          )}
        </section>
        <section className="card wide">
          <h3>Decisions</h3>
          {view.decisions.length === 0 ? <p className="hint">Recorded when you approve the concept.</p> : <ul className="status-list">{view.decisions.map((d) => <li key={d.id}><i className="dot pass" />{d.text}</li>)}</ul>}
        </section>
      </div>
    </Page>
  );
}

interface Item { id: string; kind: string; type: string; title: string; body: string; screen?: string; gate?: string; packet?: string; consumer?: string; }

function Answer({ onSend, busy }: { onSend: (text: string) => void; busy: boolean }) {
  const [text, setText] = useState("");
  return <><input className="grow" value={text} onChange={(e) => setText(e.target.value)} placeholder="Your answer" aria-label="Your answer" onKeyDown={(e) => { if (e.key === "Enter" && text.trim()) onSend(text); }} /><button className="primary" disabled={busy || !text.trim()} onClick={() => onSend(text)}>Send</button></>;
}

export function Review({ onOpen, onChange }: { onOpen: (screen: string) => void; onChange?: () => void }) {
  const { projects, projectId, setProjectId, loadError } = useProject();
  const { busy, error, run } = useBusy();
  const [items, setItems] = useState<Item[] | null>(null);
  const base = `/projects/${projectId}`;
  const load = async () => { setItems(await call<Item[]>(`${base}/review`)); onChange?.(); };
  useEffect(() => { if (projectId) { load().catch(() => setItems([])); const timer = window.setInterval(() => load().catch(() => undefined), 4000); return () => window.clearInterval(timer); } }, [projectId]);
  if (!projects) return <NoProject title="Review queue" message={loadError || "Loading…"} />;
  if (!projectId || !items) return <NoProject title="Review queue" message="Create a project in Headquarters first." />;
  const act = (path: string, body?: unknown) => run("Saving", async () => { await call(`${base}${path}`, "POST", body ?? {}); await load(); });
  return (
    <Page title="Review queue" intro="Everything waiting on you, in one place. Only a person can approve these." projects={projects} projectId={projectId} setProjectId={setProjectId}>
      <Messages busy={busy} error={error} />
      {items.length === 0 ? <p className="hint">Nothing is waiting on you.</p> : (
        <div className="reviews">
          {items.map((r) => (
            <section key={r.id} className="card review">
              <div className="eyebrow">{r.type}</div>
              <h3>{r.title}</h3>
              {r.body && <p>{r.body}</p>}
              <div className="row">
                {r.kind === "record" && <><button className="primary" disabled={!!busy} onClick={() => act(`/records/${r.id}/confirm`)}>Approve</button><button disabled={!!busy} onClick={() => act(`/records/${r.id}/reject`)}>Reject</button></>}
                {r.kind === "part" && <><button className="primary" disabled={!!busy} onClick={() => act(`/part-proposals/${r.id}`, { accept: true })}>Add part</button><button disabled={!!busy} onClick={() => act(`/part-proposals/${r.id}`, { accept: false })}>Dismiss</button></>}
                {r.kind === "ack" && <button className="primary" disabled={!!busy} onClick={() => act(`/interfaces/packets/${r.packet}/ack`, { consumer: r.consumer })}>Acknowledge</button>}
                {r.kind === "gate" && <button className="primary" onClick={() => onOpen("Gates")}>Read the report</button>}
                {r.kind === "question" && <Answer busy={!!busy} onSend={(text) => act(`/requests/${r.id}/answer`, { text })} />}
                {r.kind === "open" && r.screen && <button onClick={() => onOpen(r.screen as string)}>Open {r.screen}</button>}
              </div>
            </section>
          ))}
        </div>
      )}
    </Page>
  );
}
