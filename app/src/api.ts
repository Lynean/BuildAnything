import { useEffect, useState } from "react";

export async function call<T>(path: string, method = "GET", body?: unknown, raw?: Blob): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: raw ? { "Content-Type": "application/octet-stream" } : body === undefined ? undefined : { "Content-Type": "application/json" },
    body: raw ?? (body === undefined ? undefined : JSON.stringify(body)),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? `The request failed (${res.status}).`);
  }
  return res.json();
}

export interface Summary { id: string; name: string; }

/** The one project the whole app shows. Only the switcher at the top left changes it; every screen follows. */
export function selectedProject(): string {
  try { return localStorage.getItem("ba.project") ?? ""; } catch { return ""; }
}

export function selectProject(id: string) {
  try { localStorage.setItem("ba.project", id); } catch { /* optional */ }
  window.dispatchEvent(new CustomEvent("ba:project", { detail: id }));
}

/** The selected project is shared across screens, and is only a convenience. */
export function useProject() {
  const [projects, setProjects] = useState<Summary[] | null>(null);
  const [projectId, setProjectId] = useState<string | null>(() => { try { return localStorage.getItem("ba.project"); } catch { return null; } });
  const [loadError, setLoadError] = useState("");
  const refresh = () => call<Summary[]>("/projects").then((list) => {
    setProjects(list);
    setProjectId((id) => (id && list.some((p) => p.id === id) ? id : list[0]?.id ?? null));
  }).catch(() => setLoadError("The backend isn't running."));
  useEffect(() => { refresh(); }, []);
  useEffect(() => {
    const follow = (e: Event) => { setProjectId((e as CustomEvent<string>).detail); refresh(); };
    window.addEventListener("ba:project", follow);
    return () => window.removeEventListener("ba:project", follow);
  }, []);
  const choose = (id: string | null) => { if (id) selectProject(id); else setProjectId(null); };
  return { projects, projectId, setProjectId: choose, loadError, refresh };
}

export function useBusy() {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const run = async (label: string, work: () => Promise<void>) => {
    setBusy(label); setError(""); setNote("");
    try { await work(); } catch (e) { setError((e as Error).message); } finally { setBusy(""); }
  };
  return { busy, error, note, setNote, setError, run };
}
