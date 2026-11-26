import { useEffect, useState, type ReactNode } from "react";
import DepartmentChat from "./DepartmentChat";
import DepartmentCapabilities from "./DepartmentCapabilities";
import ToolTab, { useToolTabs } from "./ToolTab";
import SoftwareOverview from "./SoftwareOverview";
import FlashPanel from "./FlashPanel";

type Lang = "c" | "python" | "ts";
interface Block { title?: string; description?: string; takes?: string; gives?: string; based_on?: string[]; components?: string[]; owner?: string; whole_file?: boolean; code: string; state: "documented" | "stale" | "missing" | "source_changed"; }
interface CodeFile { path: string; blocks: Block[]; plain: string | null; outside: string; generated?: boolean; }
interface Step { instruction: string; expect: string; source: string; done: string | null; }
interface Setup { steps: Step[]; dependencies: { name: string; licence: string; url: string }[]; review: string[]; linkIssues: { id: string; title: string; between: string[] }[]; autotest?: boolean; }
interface Note { id: string; part: string; page: number; finding: string; why: string; current: boolean; }
interface Session { id: string; title: string; device: string | null; domain: string; rung: number; ladder: string[]; status: string; next: string; hypotheses: Record<string, string>[]; measurements: string[]; errors: string[]; }
interface Elec { pageNotes: Note[]; debug: Session[]; spent: number; limit: number; }
interface Summary { id: string; name: string; }

async function call<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, { method, headers: body === undefined ? undefined : { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? `The request failed (${res.status}).`);
  }
  return res.json();
}

const KEYWORDS: Record<Lang, Set<string>> = {
  c: new Set("static return if else for while typedef struct float int bool char void const uint8_t uint16_t uint32_t int16_t #include #define true false".split(" ")),
  python: new Set("def return try except if else for while import from None True False class with as".split(" ")),
  ts: new Set("export async function const let return await if else type string".split(" ")),
};

const langOf = (path: string): Lang => (path.endsWith(".py") ? "python" : path.endsWith(".ts") ? "ts" : "c");

function Line({ text, lang }: { text: string; lang: Lang }) {
  const pattern = lang === "python"
    ? /(#.*$|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\b\d+(?:\.\d+)?\b|\b[A-Za-z_]\w*\b)/g
    : /(\/\/.*$|\/\*.*?\*\/|"(?:[^"\\]|\\.)*"|\b0x[0-9a-fA-F]+\b|\b\d+(?:\.\d+)?f?\b|#?\b[A-Za-z_]\w*\b)/g;
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(pattern)) {
    const t = m[0];
    const index = m.index ?? 0;
    out.push(text.slice(last, index));
    const cls = /^(#\s|\/\/|\/\*)/.test(t) ? "t-com" : /^["']/.test(t) ? "t-str" : /^\d/.test(t) ? "t-num" : KEYWORDS[lang].has(t) ? "t-key" : "";
    out.push(cls ? <span key={index} className={cls}>{t}</span> : t);
    last = index + t.length;
  }
  out.push(text.slice(last));
  return <>{out}</>;
}

function Code({ code, lang }: { code: string; lang: Lang }) {
  return <pre className="code" tabIndex={0}>{code.split("\n").map((l, i) => <div className="code-line" key={i}><span className="ln">{i + 1}</span><span><Line text={l} lang={lang} /></span></div>)}</pre>;
}

const LABEL: Record<string, string> = { stale: "Description out of date", missing: "Needs description", source_changed: "Depends on something that changed" };

function BlockPanel({ block, lang, open, onToggle, notes }: { block: Block; lang: Lang; open: boolean; onToggle: () => void; notes: Note[] }) {
  const missing = block.state === "missing";
  const sources = (block.based_on ?? []).map((item) => {
    if (!item.startsWith("note:")) return { label: item, title: item };
    const note = notes.find((n) => n.id === item.slice(5));
    return note ? { label: `${note.part} p. ${note.page}`, title: note.finding } : { label: "Datasheet replaced", title: item };
  });
  return (
    <section className={`block ${block.state === "documented" ? "documented" : block.state === "missing" ? "missing" : "stale"}`}>
      <button className="block-head" aria-expanded={open} onClick={onToggle}>
        <span className={`chev ${open ? "open" : ""}`} aria-hidden>›</span>
        <span className="block-main">
          <span className="block-title">{missing ? "Undocumented code block" : block.title}</span>
          <span className="block-desc">{missing ? "The agent must describe this block before the change can be accepted." : block.description}</span>
        </span>
        {block.whole_file && <span className="pill">Whole file</span>}
        {block.state !== "documented" && <span className={`pill ${missing ? "bad" : "warn"}`}>{LABEL[block.state]}</span>}
      </button>
      {!missing && (block.takes || block.gives || sources.length > 0 || (block.components?.length ?? 0) > 0) && (
        <dl className="block-meta">
          {block.takes && (<><dt>Takes</dt><dd>{block.takes}</dd></>)}
          {block.gives && (<><dt>Gives</dt><dd>{block.gives}</dd></>)}
          {(block.components?.length ?? 0) > 0 && (<><dt>Depends on</dt><dd className="chips">{block.components?.map((c) => <span key={c} className="chip">{c}</span>)}</dd></>)}
          {sources.length > 0 && (<><dt>Based on</dt><dd className="chips">{sources.map((s) => <span key={s.label + s.title} className="chip" title={s.title}>{s.label}</span>)}</dd></>)}
        </dl>
      )}
      {open && <div className="block-code"><Code code={block.code} lang={lang} /></div>}
    </section>
  );
}

interface Space { open: string[]; active: string; expanded: string[]; raw: boolean; }
const EMPTY: Space = { open: [], active: "", expanded: [], raw: false };

// The Software screen, and the Simulation screen for the Simulation department's own program (firmware/simulation/).
const SIM_DEVICE = "simulation";

export default function Workspace({ onOpenTroubleshooting, department = "Software" }: { onOpenTroubleshooting: () => void; department?: "Software" | "Simulation" }) {
  const mine = (d: string) => (department === "Simulation" ? d === SIM_DEVICE : d !== SIM_DEVICE);
  const [projects, setProjects] = useState<Summary[] | null>(null);
  const [projectId, setProjectId] = useState<string | null>(() => { try { return localStorage.getItem("ba.project"); } catch { return null; } });
  const [devices, setDevices] = useState<string[]>([]);
  const [device, setDevice] = useState("");
  const [files, setFiles] = useState<Record<string, CodeFile[]>>({});
  const [spaces, setSpaces] = useState<Record<string, Space>>({});
  const [elec, setElec] = useState<Elec | null>(null);
  const [side, setSide] = useState<string>("Agent");
  const toolTabs = useToolTabs(projectId, department);
  // Software's sections are sized by dragging their edges (or the arrow keys on an edge). Sizes are remembered on this computer.
  const stored = (key: string, fallback: number) => { try { return Number(localStorage.getItem(key)) || fallback; } catch { return fallback; } };
  const [sideW, setSideW] = useState(() => stored("ba.sideW", 290));
  const [filesW, setFilesW] = useState(() => stored("ba.filesW", 216));
  const [chatH, setChatH] = useState(() => stored("ba.chatH", 340));
  const keep = (key: string, set: (n: number) => void, value: number) => { set(value); try { localStorage.setItem(key, String(value)); } catch { /* optional */ } };
  const widthOf = (el: Element) => (el.closest(".ide-body") as HTMLElement | null)?.clientWidth ?? 1200;
  const fitSide = (w: number, total: number) => keep("ba.sideW", setSideW, Math.round(Math.max(240, Math.min(w, total - filesW - 320))));
  const fitFiles = (w: number, total: number) => keep("ba.filesW", setFilesW, Math.round(Math.max(140, Math.min(w, total - sideW - 320))));
  const fitChat = (h: number) => keep("ba.chatH", setChatH, Math.round(Math.max(120, Math.min(h, window.innerHeight - 260))));
  const drag = (e: React.PointerEvent<HTMLDivElement>, cursor: string, onMove: (dx: number, dy: number) => void) => {
    e.preventDefault();
    const x0 = e.clientX, y0 = e.clientY;
    const move = (ev: PointerEvent) => onMove(ev.clientX - x0, ev.clientY - y0);
    const stop = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", stop); document.body.classList.remove("resizing", "resizing-row"); };
    document.body.classList.add(cursor === "row" ? "resizing-row" : "resizing");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  };
  const startSide = (e: React.PointerEvent<HTMLDivElement>) => { const total = widthOf(e.currentTarget), w0 = sideW; drag(e, "col", (dx) => fitSide(w0 - dx, total)); };
  const startFiles = (e: React.PointerEvent<HTMLDivElement>) => { const total = widthOf(e.currentTarget), w0 = filesW; drag(e, "col", (dx) => fitFiles(w0 + dx, total)); };
  const startChat = (e: React.PointerEvent<HTMLDivElement>) => { const h0 = chatH; drag(e, "row", (_dx, dy) => fitChat(h0 + dy)); };
  const [view, setView] = useState<"Overview" | "Code">("Overview");
  const [version, setVersion] = useState(0);
  const [designs, setDesigns] = useState<{ id: string; title: string; html: string }[]>([]);
  const [design, setDesign] = useState<string | null>(null);
  const [measure, setMeasure] = useState("");
  const [newDevice, setNewDevice] = useState("");
  const [newIsComputer, setNewIsComputer] = useState(false);
  const [computers, setComputers] = useState<string[]>([]);
  const [setup, setSetup] = useState<Setup | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const base = projectId ? `/projects/${projectId}/electrical` : "";

  const loadFiles = (d: string) => call<{ files: CodeFile[] }>(`${base}/firmware/${d}`).then((r) => {
    setFiles((f) => ({ ...f, [d]: r.files }));
    setSpaces((s) => (s[d]?.open.length || !r.files.length ? s : { ...s, [d]: { ...EMPTY, open: [r.files[0].path], active: r.files[0].path } }));
  });

  useEffect(() => {
    call<Summary[]>("/projects").then((list) => { setProjects(list); setProjectId((id) => (id && list.some((p) => p.id === id) ? id : list[0]?.id ?? null)); }).catch(() => setError("The backend isn't running."));
  }, []);
  useEffect(() => {
    if (!projectId) return;
    try { localStorage.setItem("ba.project", projectId); } catch { /* optional */ }
    setFiles({}); setSpaces({});
    call<Elec>(base).then(setElec).catch((e) => setError(e.message));
    call<{ devices: string[]; computers: string[] }>(`${base}/firmware`).then((r) => { setDevices(r.devices.filter(mine)); setComputers(r.computers); setDevice(r.devices.filter(mine)[0] ?? ""); r.devices.forEach(loadFiles); }).catch((e) => setError(e.message));
  }, [projectId]);
  useEffect(() => { if (projectId) call<{ id: string; title: string; html: string }[]>(`/projects/${projectId}/designs`).then(setDesigns).catch(() => undefined); }, [projectId]);
  useEffect(() => {
    setSetup(null);
    if (projectId && device) call<Setup>(`${base}/firmware/${device}/setup`).then(setSetup).catch(() => undefined);
  }, [projectId, device]);

  const run = async (label: string, work: () => Promise<void>) => {
    setBusy(label); setError(""); setNote("");
    try { await work(); } catch (e) { setError((e as Error).message); } finally { setBusy(""); }
  };

  if (!projects) return <div className="page"><h1>{department}</h1>{error ? <p className="error" role="alert">{error}</p> : <p>Loading…</p>}</div>;
  if (!projectId) return <div className="page"><h1>{department}</h1><p className="intro">Create a project in Headquarters first.</p></div>;

  const ws = spaces[device] ?? EMPTY;
  const list = files[device] ?? [];
  const file = list.find((f) => f.path === ws.active);
  const update = (patch: Partial<Space>) => setSpaces((s) => ({ ...s, [device]: { ...(s[device] ?? EMPTY), ...patch } }));
  const openFile = (path: string) => { update({ open: ws.open.includes(path) ? ws.open : [...ws.open, path], active: path }); setView("Code"); };
  const closeFile = (path: string) => { const open = ws.open.filter((p) => p !== path); update({ open, active: ws.active === path ? open[open.length - 1] ?? "" : ws.active }); };
  const blockKey = (i: number) => `${ws.active}#${i}`;
  const keys = file?.blocks.map((_, i) => blockKey(i)) ?? [];
  const allOpen = keys.length > 0 && keys.every((k) => ws.expanded.includes(k));
  // Files at the top of the device folder (laptop.py) have no folder of their own and are listed first.
  const folders = ["", ...new Set(list.filter((f) => f.path.includes("/")).map((f) => f.path.split("/")[0]))];
  const allBlocks = list.flatMap((f) => f.blocks);
  const okCount = allBlocks.filter((b) => b.state === "documented").length;
  const sessions = (elec?.debug ?? []).filter((s) => s.device === device && s.status === "open" && s.domain === "code");
  const moved = (elec?.debug ?? []).filter((s) => s.device === device && s.status === "open" && s.domain !== "code");
  const active = sessions[0];
  const hypothesis = active?.hypotheses[active.hypotheses.length - 1];
  const notes = elec?.pageNotes ?? [];
  const refreshElec = async () => setElec(await call<Elec>(base));
  const postDebug = (label: string, path: string, body?: unknown) => run(label, async () => setElec(await call<Elec>(`${base}/debug${path}`, "POST", body)));

  const reloadAll = async () => {
    const r = await call<{ devices: string[]; computers: string[] }>(`${base}/firmware`);
    setDevices((d) => [...new Set([...d, ...r.devices.filter(mine)])]); setComputers(r.computers); setDevice((x) => x || r.devices.filter(mine)[0] || "");
    r.devices.forEach(loadFiles);
    setVersion((v) => v + 1);
    setDesigns(await call(`/projects/${projectId}/designs`));
    await refreshElec();
    if (device) setSetup(await call<Setup>(`${base}/firmware/${device}/setup`));
  };
  const isComputer = computers.includes(device);
  const runTests = () => {
    if (!window.confirm("Run this program's tests on your computer? The code was written by an agent, so check it first. The first run installs its requirements.txt into this project's Python environment.")) return;
    run("Running the tests", async () => {
      const r = await call<{ result: { ok: boolean; summary: string; lines: string[] }; view: Elec }>(`${base}/firmware/${device}/tests`, "POST", { confirmed: true });
      setElec(r.view);
      if (r.result.ok) setNote(r.result.summary); else { setError(`Tests failed. ${r.result.summary} ${r.result.lines.slice(-2).join(" ")}`); setSide("Debug"); }
    });
  };
  const toggleAutotest = () => {
    const allowed = !setup?.autotest;
    if (allowed && !window.confirm(`Let the app run this program's code and tests? ${department === "Simulation" ? "Simulation then runs its simulations and saves the results" : "Software then runs its tests after each write"}. The code is agent-written and runs on your computer, without asking each time. The app first installs the packages in its requirements.txt into this project's Python environment. You can switch this off at any time.`)) return;
    run(allowed ? "Allowing automatic test runs" : "Stopping automatic test runs", async () => {
      await call(`${base}/firmware/${device}/autotest`, "POST", { allowed });
      setSetup(await call<Setup>(`${base}/firmware/${device}/setup`));
      setNote(allowed ? (department === "Simulation" ? "Simulation can now run its simulations and save the results." : "Software can now run its tests after each write.") : "Code now runs only when you start it.");
    });
  };
  const build = () => run(isComputer ? "Checking the code" : "Building", async () => {
    const r = await call<{ build: any; view: Elec }>(`${base}/firmware/${device}/build`, "POST");
    setElec(r.view);
    setVersion((v) => v + 1);
    if (r.build.ok) setNote(`${r.build.detail}${r.build.warnings?.length ? ` ${r.build.warnings[0]}` : ""}`); else { setError(`${r.build.detail} ${r.build.errors?.[0] ?? ""}`); setSide("Debug"); }
  });
  const describe = () => run("Writing a description", async () => {
    const r = await call<{ files: CodeFile[] }>(`${base}/firmware/${device}/describe`, "POST", { path: ws.active });
    setFiles((f) => ({ ...f, [device]: r.files }));
  });
  const addDevice = () => {
    const name = newDevice.trim().toLowerCase().replace(/[^a-z0-9-]+/g, "-");
    if (!name) return;
    setDevices((d) => (d.includes(name) ? d : [...d, name])); setDevice(name); setNewDevice("");
    if (newIsComputer) setComputers((c) => (c.includes(name) ? c : [...c, name])); else setSide("Device");
  };

  return (
    <div className="ide">
      {design !== null && <div className="design-overlay" role="dialog" aria-label="Screen design"><button className="small" onClick={() => setDesign(null)}>Close</button><iframe title="Screen design" sandbox="" srcDoc={`<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">${design}`} /></div>}
      <div className="device-tabs" role="tablist" aria-label="Devices">
        {devices.map((d) => <button key={d} role="tab" aria-selected={d === device} className={d === device ? "on" : ""} onClick={() => setDevice(d)}><span><b>{d}</b></span></button>)}
        <div className="add-device-form">
          <input value={newDevice} onChange={(e) => setNewDevice(e.target.value)} placeholder="New device" aria-label="New device name" onKeyDown={(e) => e.key === "Enter" && addDevice()} />
          <select aria-label="Device type" value={newIsComputer ? "computer" : "board"} onChange={(e) => setNewIsComputer(e.target.value === "computer")}><option value="board">Board</option><option value="computer">Computer</option></select>
          <button className="small" disabled={!newDevice.trim()} onClick={addDevice}>Add</button>
        </div>
        <select className="project-pick" aria-label="Project" value={projectId} onChange={(e) => setProjectId(e.target.value)}>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
      </div>
      {busy && <p className="busy ide-msg" role="status">{busy}…</p>}
      {error && <p className="error ide-msg" role="alert">{error}</p>}
      {note && <p className="note ide-msg" role="status">{note}</p>}
      <div className="ide-body" style={{ ["--side-w" as string]: `${sideW}px`, ["--files-w" as string]: `${filesW}px`, ["--chat-log-h" as string]: `${chatH}px` }}>
        <div className="pane-resize col files" role="separator" aria-orientation="vertical" aria-label="Drag to resize the file list" title="Drag to resize" tabIndex={0} onPointerDown={startFiles}
          onKeyDown={(e) => { if (e.key === "ArrowRight") fitFiles(filesW + 30, widthOf(e.currentTarget)); if (e.key === "ArrowLeft") fitFiles(filesW - 30, widthOf(e.currentTarget)); }} />
        <aside className="explorer">
          <div className="eyebrow">Files</div>
          {folders.map((folder) => (
            <div key={folder}>
              {folder && <div className="folder">{folder}</div>}
              {list.filter((f) => folder ? f.path.startsWith(folder + "/") : !f.path.includes("/")).map((f) => (
                <button key={f.path} className={`file ${ws.active === f.path ? "active" : ""}`} onClick={() => openFile(f.path)}>
                  <span>{folder ? f.path.split("/").slice(1).join("/") : f.path}</span>
                  {f.blocks.some((b) => b.state !== "documented") && <i className="file-dot" title="Has documentation issues" />}
                </button>
              ))}
            </div>
          ))}
          {list.length === 0 && <p className="hint">{device ? "No code yet. Ask the agent to write some." : "Add a device to start."}</p>}
        </aside>
        <div className="editor">
          <div className="file-tabs" role="tablist" aria-label="Open files">
            <div className={`file-tab view-tab ${view === "Overview" ? "on" : ""}`}><button role="tab" aria-selected={view === "Overview"} onClick={() => setView("Overview")}>Overview</button></div>
            {ws.open.map((p) => (
              <div key={p} className={`file-tab ${view === "Code" && p === ws.active ? "on" : ""}`}>
                <button role="tab" aria-selected={view === "Code" && p === ws.active} onClick={() => { update({ active: p }); setView("Code"); }}>{p.split("/").pop()}</button>
                <button className="x" aria-label={`Close ${p}`} onClick={() => closeFile(p)}>×</button>
              </div>
            ))}
          </div>
          {view === "Overview" && device ? (
            <div className="editor-scroll"><SoftwareOverview projectId={projectId} device={device} version={version} onOpenFile={openFile} /></div>
          ) : file ? (
            <div className="editor-scroll">
              <div className="file-head">
                <div>
                  <div className="mono file-path">{file.path}</div>
                  <div className="file-info">{file.generated ? "Generated from the Interface screen. Don't edit it here." : file.blocks[0]?.owner ? `Owned by ${file.blocks[0].owner} · Edited by agent · ${file.blocks.length} ${file.blocks.length === 1 ? "description" : "descriptions"}` : "Not described"}</div>
                </div>
                <div className="row">
                  {file.generated ? null : file.blocks.length === 0
                    ? <button className="small" disabled={!!busy} onClick={describe}>Describe this file</button>
                    : <>
                        {!ws.raw && <button className="small" onClick={() => update({ expanded: allOpen ? ws.expanded.filter((x) => !keys.includes(x)) : [...new Set([...ws.expanded, ...keys])] })}>{allOpen ? "Collapse all" : "Expand all"}</button>}
                        <button className="small" onClick={() => update({ raw: !ws.raw })}>{ws.raw ? "Description view" : "Full file"}</button>
                      </>}
                  <button className="small" disabled={!!busy} onClick={build}>{isComputer ? "Check code" : "Build"}</button>
                  {isComputer && <button className="small" disabled={!!busy} onClick={runTests}>Run tests</button>}
                  {isComputer && <label className="small" title="The agent's code and tests run on your computer without asking each time, in this project's Python environment with its requirements.txt installed"><input type="checkbox" checked={!!setup?.autotest} disabled={!!busy} onChange={toggleAutotest} /> Run code and tests (installs its requirements)</label>}
                </div>
              </div>
              {file.blocks.length === 0
                ? <Code code={file.plain ?? ""} lang={langOf(file.path)} />
                : ws.raw
                  ? <Code code={file.blocks.map((b) => b.code).join("\n\n")} lang={langOf(file.path)} />
                  : file.blocks.map((b, i) => <BlockPanel key={i} block={b} lang={langOf(file.path)} notes={notes} open={ws.expanded.includes(blockKey(i))} onToggle={() => update({ expanded: ws.expanded.includes(blockKey(i)) ? ws.expanded.filter((x) => x !== blockKey(i)) : [...ws.expanded, blockKey(i)] })} />)}
            </div>
          ) : <div className="empty">{list.length ? "Choose a file from the list." : "No file open."}</div>}
          <div className="status-bar">
            <span>{device || "No device"}</span>
            <span className={okCount === allBlocks.length ? "" : "warn-text"}>Descriptions {okCount} of {allBlocks.length} up to date</span>
          </div>
        </div>
        <aside className="ide-side">
          <div className="pane-resize col left" role="separator" aria-orientation="vertical" aria-label="Drag to resize the agent panel" title="Drag to resize" tabIndex={0} onPointerDown={startSide}
            onKeyDown={(e) => { if (e.key === "ArrowLeft") fitSide(sideW + 30, widthOf(e.currentTarget)); if (e.key === "ArrowRight") fitSide(sideW - 30, widthOf(e.currentTarget)); }} />
          <div className="side-tabs" role="tablist">
            {[...(device && !computers.includes(device) ? ["Agent", "Device", "Debug"] : ["Agent", "Debug"]), ...toolTabs.map((t) => t.title), "Capability"].map((t) => <button key={t} role="tab" aria-selected={side === t} className={side === t ? "on" : ""} onClick={() => setSide(t)}>{t}{t === "Debug" && (sessions.length > 0 || moved.length > 0 || (setup?.linkIssues.length ?? 0) > 0) ? <i className="file-dot tab-dot" /> : null}</button>)}
          </div>
          {side === "Capability" && <div className="side-body side-capability"><DepartmentCapabilities projectId={projectId} department={department} /></div>}
          {toolTabs.filter((t) => t.title === side).map((t) => <div key={t.id} className="side-body side-tool"><ToolTab projectId={projectId} projectName={projectId} department={department} tab={t} /></div>)}
          {side === "Agent" && (
            <div className="side-body">
              <DepartmentChat projectId={projectId} department={department} compact onChange={() => { reloadAll().catch(() => undefined); }} />
              <div className="pane-resize row" role="separator" aria-orientation="horizontal" aria-label="Drag to resize the agent chat" title="Drag to resize" tabIndex={0} onPointerDown={startChat}
                onKeyDown={(e) => { if (e.key === "ArrowDown") fitChat(chatH + 40); if (e.key === "ArrowUp") fitChat(chatH - 40); }} />
              {designs.length > 0 && <div className="design-list"><div className="eyebrow">Screen designs</div>{designs.map((d) => <button key={d.id} className="small" onClick={() => setDesign(d.html)}>{d.title}</button>)}</div>}
              <p className="hint">{computers.includes(device) ? "The agent uses your packet definitions and checks licences for anything it reuses." : "The agent searches the datasheets and remembers what it finds."} AI spend ${elec?.spent.toFixed(3)} of ${elec?.limit.toFixed(2)}.</p>
              {setup && setup.review.length > 0 && <div className="link-notice"><div className="eyebrow">Check these licences</div><p>{setup.review.join(", ")}</p></div>}
              {setup && setup.steps.length > 0 && (
                <div className="setup-steps">
                  <div className="eyebrow">Setup</div>
                  {setup.steps.map((s, i) => (
                    <div key={i} className="setup-step">
                      <p>{i + 1}. {s.instruction}</p>
                      <p className="hint">You should see: {s.expect}</p>
                      {s.done ? <span className={`pill ${s.done === "worked" ? "good" : "bad"}`}>{s.done === "worked" ? "Worked" : "Didn't work"}</span> : <div className="decide"><button className="small" onClick={() => run("Saving", async () => setSetup(await call<Setup>(`${base}/firmware/${device}/setup/${i}`, "POST", { worked: true })))}>Worked</button><button className="small" onClick={() => run("Saving", async () => setSetup(await call<Setup>(`${base}/firmware/${device}/setup/${i}`, "POST", { worked: false })))}>Didn't work</button></div>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          {side === "Device" && device && !computers.includes(device) && (
            <div className="side-body"><FlashPanel key={device} base={base} device={device} onChanged={() => { loadFiles(device); refreshElec(); }} /></div>
          )}
          {side === "Debug" && (
            <div className="side-body">
              {(setup?.linkIssues.length ?? 0) > 0 && <div className="link-notice"><div className="eyebrow">Link issue</div>{setup?.linkIssues.map((l) => <p key={l.id}>{l.title} ({l.between.join(" and ")}). See the Interface screen.</p>)}</div>}
              {moved.length > 0 && <div className="link-notice"><div className="eyebrow">Moved to Troubleshooting</div><p>{moved[0].title}</p><button className="small" onClick={onOpenTroubleshooting}>Open Troubleshooting</button></div>}
              {active ? (
                <>
                  <div className="eyebrow">Code problem</div>
                  <h4 className="debug-title">{active.title}</h4>
                  <div className="ladder side-ladder">{active.ladder.map((r, i) => <div key={r} className={i + 1 === active.rung ? "on" : i + 1 < active.rung ? "past" : ""}><span className="mono">{i + 1}</span>{r}</div>)}</div>
                  <p className="ask">{active.next}</p>
                  <div className="mono dim-text debug-file">{active.errors[active.errors.length - 1].slice(0, 300)}</div>
                  {hypothesis && <dl className="stack"><dt>Hypothesis</dt><dd>{hypothesis.hypothesis}</dd><dt>Primary source</dt><dd>{hypothesis.primary}</dd><dt>Backup source</dt><dd>{hypothesis.backup}</dd><dt>If right</dt><dd>{hypothesis.expect}</dd><dt>Ruled out if</dt><dd>{hypothesis.refute}</dd></dl>}
                  <label className="field debug-field">Paste the log or result<textarea rows={3} value={measure} onChange={(e) => setMeasure(e.target.value)} /></label>
                  <div className="row">
                    <button disabled={!measure.trim()} onClick={() => { postDebug("Saving", `/${active.id}/measurement`, { text: measure }); setMeasure(""); }}>Save result</button>
                    <button onClick={() => postDebug("Finding a likely cause", `/${active.id}/hypothesis`)}>Suggest a cause</button>
                  </div>
                  <button className="wide escalate" onClick={() => postDebug("Moving", `/${active.id}/move`, { to: "device", reason: "Code fixes aren't working." })}>Code fixes aren't working: check the device</button>
                  <button className="ghost wide" onClick={() => postDebug("Saving", `/${active.id}/close`, { text: "Resolved" })}>Mark resolved</button>
                </>
              ) : moved.length === 0 && <p className="hint">No open code problems on this device.</p>}
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
