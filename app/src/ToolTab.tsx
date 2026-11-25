import { useEffect, useRef, useState } from "react";
import { call } from "./api";

// Hosts one tool tab in a sealed frame: sandbox without same-origin and a content policy with no network, so the
// tab's code can't reach the app's routes or buttons. It asks for data over postMessage, and only these questions are
// answered: who am I, the data script's output, run it again, read a project file, list project files.
const CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:";
const ACCENT: Record<string, string> = { Mechanical: "#f0894b", Electrical: "#f2c94c", Software: "#5aa9ff", Procurement: "#6fd08c" };

export interface ToolTabInfo { id: string; title: string; tool: string; adapter: boolean; scripts: boolean; }

export default function ToolTab({ projectId, projectName, department, tab, which = "approved" }: { projectId: string; projectName: string; department: string; tab: ToolTabInfo; which?: "approved" | "proposed" }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [doc, setDoc] = useState("");
  const [height, setHeight] = useState(240);
  const [error, setError] = useState("");
  useEffect(() => {
    setDoc(""); setError(""); setHeight(240);
    call<{ js: string }>(`/tabs/${tab.id}/bundle?which=${which}`).then(({ js }) => setDoc(
      `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${CSP}"></head><body><div id="root"></div><script>${js.replace(/<\/script/gi, "<\\/script")}</script></body></html>`,
    )).catch((e) => setError((e as Error).message));
  }, [tab.id, which, projectId]);

  useEffect(() => {
    const base = `/projects/${projectId}/tabs/${tab.id}`;
    const onMessage = async (e: MessageEvent) => {
      const m = e.data;
      if (!m || m.ba !== 1 || m.reply || e.source !== frame.current?.contentWindow) return;
      if (m.op === "height") { setHeight(Math.min(6000, Math.max(160, Number(m.args?.px) || 0))); return; }
      const answer = (ok: boolean, result?: unknown, err?: string) => frame.current?.contentWindow?.postMessage({ ba: 1, reply: true, id: m.id, ok, result, error: err }, "*");
      try {
        if (m.op === "hello") {
          const canRun = tab.adapter && tab.scripts;
          answer(true, { project: { id: projectId, name: projectName }, department, accent: ACCENT[department] ?? "#a493f2", tab: { id: tab.id, title: tab.title, tool: tab.tool }, canRun,
            why: !tab.adapter ? "This tab reads the project's files; it has no data script." : !tab.scripts ? "Tab data scripts are switched off at Reception." : "" });
        } else if (m.op === "data") answer(true, await call(`${base}/data?which=${which}`));
        else if (m.op === "run") answer(true, await call(`${base}/run?which=${which}`, "POST"));
        else if (m.op === "read") answer(true, await call(`${base}/file?path=${encodeURIComponent(String(m.args?.path ?? ""))}`));
        else if (m.op === "service") answer(true, await call(`${base}/service`, "POST", { connection: m.args?.connection, operation: m.args?.operation, params: m.args?.params ?? {} }));
        else if (m.op === "list") answer(true, await call(`${base}/list?dir=${encodeURIComponent(String(m.args?.dir ?? ""))}&pattern=${encodeURIComponent(String(m.args?.pattern ?? "*"))}`));
        else answer(false, undefined, "Tabs can't ask for that.");
      } catch (err) { answer(false, undefined, (err as Error).message); }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [projectId, projectName, department, tab, which]);

  if (error) return <p className="error">{error}</p>;
  if (!doc) return <p className="hint">Loading the {tab.title} tab…</p>;
  // key: a new frame for each project, so nothing from the last project is ever shown
  return <iframe key={`${projectId}-${tab.id}-${which}`} ref={frame} className="tool-tab-frame" title={`${tab.title} tab`} sandbox="allow-scripts" srcDoc={doc} style={{ height }} />;
}

/** The approved tool tabs the person chose to show on this project, in one department. */
export function useToolTabs(projectId: string | null, department: string): ToolTabInfo[] {
  const [tabs, setTabs] = useState<ToolTabInfo[]>([]);
  useEffect(() => {
    setTabs([]);
    if (!projectId) return;
    const load = () => call<ToolTabInfo[]>(`/projects/${projectId}/departments/${department}/tabs`).then(setTabs).catch(() => undefined);
    load();
    window.addEventListener("ba:tabs", load);
    return () => window.removeEventListener("ba:tabs", load);
  }, [projectId, department]);
  return tabs;
}
