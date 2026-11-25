import { useEffect, useState } from "react";
import { call, useBusy, useProject } from "./api";
import ToolTab from "./ToolTab";

// Reception's Tool tabs: tabs the Service agents wrote, waiting for the person; approved ones with the projects that
// show them; and the one switch that lets tab data scripts run in the sandbox. Only a person uses any of this.
interface Tab { id: string; title: string; approved_title?: string; tool: string; department: string; approved_department?: string; summary: string; why: string; exe: string; approved_exe?: string; adapter: boolean; status: string; intact: boolean; projects: string[]; approvedCopy: boolean; by?: string; rejected_because?: string; }
interface View { tabs: Tab[]; scripts: boolean; }

function Files({ id, which }: { id: string; which: "proposed" | "approved" }) {
  const [files, setFiles] = useState<Record<string, string> | null>(null);
  useEffect(() => { call<Record<string, string>>(`/tabs/${id}/files?which=${which}`).then(setFiles).catch(() => setFiles({})); }, [id, which]);
  if (!files) return <p className="hint">Loading…</p>;
  return <>{Object.entries(files).map(([name, text]) => <details key={name}><summary className="mono">{name}</summary><pre className="file-text wrap">{text}</pre></details>)}</>;
}

export default function ToolTabsAdmin() {
  const { projectId, projects } = useProject();
  const { busy, error, run } = useBusy();
  const [view, setView] = useState<View | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const load = () => call<View>("/tabs").then(setView).catch(() => undefined);
  useEffect(() => { load(); }, []);
  const changed = (v: View) => { setView(v); window.dispatchEvent(new Event("ba:tabs")); };
  const projectName = projects?.find((p) => p.id === projectId)?.name ?? projectId ?? "";
  if (!view) return null;
  const waiting = view.tabs.filter((t) => t.status === "waiting");
  const approved = view.tabs.filter((t) => t.approvedCopy);
  const decide = (t: Tab, approve: boolean) => {
    const reason = approve ? "" : window.prompt("Why not? The Service agent reads this.", "") ?? "";
    if (!approve && !reason) return;
    run(approve ? "Approving" : "Rejecting", async () => changed(await call<View>(`/tabs/${t.id}/decide`, "POST", { approve, reason, project: projectId })));
  };
  return (
    <section className="card wide tool-tabs-admin">
      <h3>Tool tabs</h3>
      <p className="hint">Tabs the Service agents made for a tool, shown in a department's screen. Each runs sealed off from the app: it can only read this project's files and its own data, never press buttons or reach the internet.</p>
      <label className="row auto-skills" title="Data scripts run in a sandbox: no network, no other programs except the one you approved for the tab, nothing written into the project.">
        <input type="checkbox" checked={view.scripts} disabled={!!busy} onChange={(e) => run("Saving", async () => changed(await call<View>("/tabs/scripts", "POST", { allowed: e.target.checked })))} />
        Let tool tabs run their data scripts on this computer
        <span className="hint">{view.scripts ? "On: a tab's Collect button runs its script in the sandbox." : "Off: tabs show only project files; data scripts don't run."}</span>
      </label>
      {error && <p className="error">{error}</p>}
      {waiting.map((t) => (
        <div key={t.id} className="note service-request">
          <div><b>{t.by?.replace("_", " ") ?? "A Service agent"} made a tab for {t.department}: {t.title}</b> <span className="hint">(for {t.tool}). {t.summary}</span></div>
          <p className="hint">{t.why} {t.adapter ? (t.exe ? `Its data script may run one program: ${t.exe}.` : "Its data script runs Python only, no other program.") : "No data script: it reads project files only."}</p>
          <details><summary>Read its files</summary><Files id={t.id} which="proposed" /></details>
          <div className="row">
            <button className="primary" disabled={!!busy} onClick={() => decide(t, true)}>Approve{projectId ? ` and show in ${projectName}` : ""}</button>
            <button disabled={!!busy} onClick={() => decide(t, false)}>Reject</button>
            {projectId && <button disabled={!!busy} onClick={() => setPreview(preview === t.id ? null : t.id)}>{preview === t.id ? "Hide preview" : "Preview on this project"}</button>}
          </div>
          {preview === t.id && projectId && <ToolTab projectId={projectId} projectName={projectName} department={t.department} tab={{ id: t.id, title: t.title, tool: t.tool, adapter: t.adapter, scripts: view.scripts }} which="proposed" />}
        </div>
      ))}
      {approved.length === 0 && waiting.length === 0 ? <p className="hint">None yet. Ask a Service agent below, such as “make a MuJoCo tab for Mechanical”.</p> : (
        <ul className="status-list">{approved.map((t) => {
          const shown = !!projectId && t.projects.includes(projectId);
          return (
            <li key={t.id}><i className={`dot ${t.intact ? "pass" : "warn"}`} />
              <span><b>{t.approved_title ?? t.title}</b> <small className="dim-text">{t.approved_department} · {t.tool}{t.approved_exe ? ` · runs ${t.approved_exe.split(/[\\/]/).pop()}` : ""}</small><br />
                <small className="dim-text">{t.intact ? `Shown on ${t.projects.length} ${t.projects.length === 1 ? "project" : "projects"}.` : "Its approved files were changed on disk, so it isn't shown."}{t.status === "waiting" ? " A newer version is waiting above." : ""}</small></span>
              {projectId && t.intact && <button className="small" disabled={!!busy} onClick={() => run("Saving", async () => changed(await call<View>(`/projects/${projectId}/tabs/${t.id}/show`, "POST", { on: !shown })))}>{shown ? `Hide from ${projectName}` : `Show in ${projectName}`}</button>}
            </li>
          );
        })}</ul>
      )}
      {view.tabs.filter((t) => t.status === "rejected").slice(0, 3).map((t) => <p key={t.id} className="hint">Rejected: {t.title}. {t.rejected_because}</p>)}
    </section>
  );
}
