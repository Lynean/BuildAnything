import { useCallback, useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { call, selectProject, selectedProject, type Summary } from "./api";
import ElectricalLive from "./ElectricalLive";
import { Review, Sources } from "./Screens";
import { ImportLive, MechanicalLive, ProcurementLive } from "./BuildScreens";
import Workspace from "./Workspace";
import Interfaces, { type Section } from "./Interfaces";
import Headquarters from "./Headquarters";
import AgentsScreen from "./AgentsScreen";
import ChangesScreen from "./ChangesScreen";
import TestsScreen from "./TestsScreen";
import Journey from "./Journey";
import ServicesScreen from "./ServicesScreen";
import PlatformScreen from "./PlatformScreen";
import TeamStory from "./TeamStory";
import World, { placeOf } from "./World";
import Explorer, { FileView } from "./Explorer";
import { watchExpandable } from "./expandable";

const SCREENS = ["Headquarters", "Journey", "Team", "Tests", "Sources", "Mechanical", "Electrical", "Software", "Interface", "Procurement", "External Service", "Internal Service", "Platform", "Agents", "Review queue", "Import project"] as const;
// Screens that were merged into others, so old links still land somewhere sensible.
const MOVED: Record<string, string> = { Changes: "Review queue", Gates: "Journey", Tasks: "Headquarters", "Project Network": "Team", Connectors: "External Service" };
type Screen = (typeof SCREENS)[number];
const JOURNEY_LOOK = { label: "Journey", color: "#ffd166", icon: "M3 17h3l3-9 4 12 3-7h5v2h-4l-4 9-4-12-2 5H3z" };
const IMPORT_LOOK = { label: "Import a project", color: "#6ab0c8", icon: "M11 3h2v9.2l3.3-3.3 1.4 1.4L12 16l-5.7-5.7 1.4-1.4 3.3 3.3zM4 18h16v3H4z" };

function screenFromHash(): Screen | null {
  const raw = decodeURIComponent(window.location.hash.slice(1));
  const name = (MOVED[raw] ?? raw) as Screen;
  return SCREENS.includes(name) ? name : null;
}

const currentProject = selectedProject;

/** The one place to choose which project the app shows. */
function ProjectSwitcher({ name, onNew }: { name: string; onNew: () => void }) {
  const [open, setOpen] = useState(false);
  const [list, setList] = useState<Summary[]>([]);
  const [current, setCurrent] = useState(selectedProject);
  const load = () => call<Summary[]>("/projects").then(setList).catch(() => setList([]));
  useEffect(() => { load(); }, [open]);
  useEffect(() => {
    const follow = () => { setCurrent(selectedProject()); load(); };
    window.addEventListener("ba:project", follow);
    return () => window.removeEventListener("ba:project", follow);
  }, []);
  name = list.find((p) => p.id === current)?.name || name;
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!(e.target as HTMLElement).closest(".switcher")) setOpen(false); };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);
  return (
    <div className="switcher">
      <button className="switcher-btn" aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen((o) => !o)}>{name || "Choose a project"} <span aria-hidden="true">▾</span></button>
      {open && (
        <ul className="switcher-menu" role="listbox" aria-label="Projects">
          {list.map((p) => <li key={p.id}><button role="option" aria-selected={p.id === current} className={p.id === current ? "on" : ""} onClick={() => { selectProject(p.id); setOpen(false); }}>{p.name}</button></li>)}
          <li className="sep"><button onClick={() => { setOpen(false); onNew(); }}>+ New project</button></li>
        </ul>
      )}
    </div>
  );
}

export default function App() {
  const [screen, setScreenState] = useState<Screen | null>(screenFromHash);
  const open = (s: string | null) => {
    if (s) window.location.hash = encodeURIComponent(s);
    else history.pushState("", document.title, window.location.pathname + window.location.search);
    setScreenState(s as Screen | null);
  };
  useEffect(() => {
    const sync = () => setScreenState(screenFromHash());
    window.addEventListener("hashchange", sync);
    window.addEventListener("popstate", sync);
    return () => { window.removeEventListener("hashchange", sync); window.removeEventListener("popstate", sync); };
  }, []);
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === "Escape" && screen && !document.querySelector(".trail-overlay")) open(null); };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [screen]);

  useEffect(() => watchExpandable(), []);
  const [interfaceSection] = useState<Section>("Data packets");
  const [explorer, setExplorer] = useState(false);
  const [file, setFile] = useState("");
  const [waiting, setWaiting] = useState(0);
  const [projectName, setProjectName] = useState("");
  const [projectId, setProjectId] = useState(currentProject);
  const refresh = useCallback(() => {
    const id = currentProject();
    setProjectId(id);
    if (id) call<{ name: string; review: number }>(`/projects/${id}/overview`).then((o) => { setProjectName(o.name); setWaiting(o.review); }).catch(() => undefined);
  }, []);
  useEffect(() => { refresh(); const timer = window.setInterval(refresh, 5000); return () => window.clearInterval(timer); }, [refresh]);
  useEffect(() => { window.addEventListener("ba:project", refresh); return () => window.removeEventListener("ba:project", refresh); }, [refresh]);
  // Switching project closes a department screen the new project doesn't have, so it never shows another project's leftovers.
  useEffect(() => {
    const check = (e: Event) => {
      const id = (e as CustomEvent<string>).detail;
      const dept = screen && ["Mechanical", "Electrical", "Software", "Procurement"].includes(screen) ? screen : null;
      setFile("");
      if (!dept) return;
      call<{ team: string[] }>(`/projects/${id}/shape`).then((r) => { if (!r.team.includes(dept)) open(null); }).catch(() => open(null));
    };
    window.addEventListener("ba:project", check);
    return () => window.removeEventListener("ba:project", check);
  }, [screen]);

  const views: Record<Screen, ReactNode> = {
    Headquarters: <Headquarters />,
    Journey: <Journey />,
    Team: <TeamStory />,
    Tests: <TestsScreen />,
    Sources: <Sources />,
    Mechanical: <MechanicalLive />,
    Electrical: <ElectricalLive />,
    Software: <Workspace onOpenTroubleshooting={() => open("Electrical")} />,
    Interface: <Interfaces initial={interfaceSection} />,
    Procurement: <ProcurementLive />,
    "External Service": <ServicesScreen side="external" />,
    "Internal Service": <ServicesScreen side="internal" />,
    Platform: <PlatformScreen />,
    Agents: <AgentsScreen />,
    "Review queue": <><Review onOpen={(name) => open(name)} onChange={refresh} /><ChangesScreen /></>,
    "Import project": <ImportLive />,
  };
  const look = screen === "Journey" ? JOURNEY_LOOK : screen === "Import project" ? IMPORT_LOOK : (() => { const p = screen ? placeOf(screen) : undefined; return p ? { label: p.label, color: p.color, icon: p.icon } : { label: screen ?? "", color: "#54d6c2", icon: "" }; })();

  return (
    <div className="app">
      <header className="world-top">
        <button className="brand-btn" onClick={() => open(null)} aria-label="Back to the project map"><span className="brand">Build<span>Anything</span></span></button>
        <ProjectSwitcher name={projectName} onNew={() => { open("Headquarters"); window.setTimeout(() => window.dispatchEvent(new Event("ba:new-project")), 0); }} />
        <button className="import-btn" onClick={() => open("Import project")} aria-label="Import a project"><span>+</span> Import</button>
        <span className="world-hint">{screen ? "Esc or the map button takes you back" : "Click any circle to go inside"}</span>
        <button className={`files-btn ${explorer ? "on" : ""}`} onClick={() => setExplorer((x) => !x)} aria-pressed={explorer} aria-label="Project files">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 5a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zm2 4v10h14V9z" /></svg> Files
        </button>
      </header>
      <div className="app-main">
      <World projectId={projectId} review={waiting} onOpen={(s) => { setFile(""); open(s); }} focus={screen ?? undefined}>
        {file && (
          <div className="panel-wrap" onClick={() => setFile("")}>
            <section className="panel" role="dialog" aria-label={file} onClick={(e) => e.stopPropagation()} style={{ ["--accent" as string]: "#8fa8c8" } as CSSProperties}>
              <div className="panel-head">
                <span className="panel-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 2h8l6 6v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Zm7 1.5V9h5.5z" /></svg></span>
                <span className="panel-title">{file.split("/").pop()}</span>
                <button className="panel-back" onClick={() => setFile("")}>Close</button>
              </div>
              <div className="panel-body"><FileView projectId={projectId} path={file} /></div>
            </section>
          </div>
        )}
        {screen && !file && (
          <div className="panel-wrap" onClick={() => open(null)}>
            <section key={screen} className="panel" role="dialog" aria-label={look.label} onClick={(e) => e.stopPropagation()} style={{ ["--accent" as string]: look.color, ["--ai" as string]: look.color } as CSSProperties}>
              <div className="panel-head">
                <span className="panel-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d={look.icon} /></svg></span>
                <span className="panel-title">{look.label}</span>
                <button className="panel-back" onClick={() => open(null)}>Back to the map</button>
              </div>
              <div className="panel-body">{views[screen]}</div>
            </section>
          </div>
        )}
      </World>
      <div className="section-backdrop" aria-hidden="true" />
      {explorer && <Explorer projectId={projectId} selected={file} onOpenFile={setFile} onClose={() => setExplorer(false)} />}
      </div>
    </div>
  );
}
