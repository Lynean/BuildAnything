import { useEffect, useState } from "react";
import { call, useBusy, useProject } from "./api";
import AgentChat, { type ChatMessage } from "./AgentChat";
import ConnectorsLive from "./ConnectorsLive";
import { ScreenChip } from "./Linked";
import ToolTabsAdmin from "./ToolTabsAdmin";
import ConnectionsAdmin from "./ConnectionsAdmin";

// Reception: External Service (Cloud, App) and Internal Service (Platform, Project).
// Nothing is installed until a person presses Install or approves an agent's proposal. API keys go straight to the keyring.
interface How { kind: "winget" | "pip" | "key" | "steps" | "command"; label: string; id?: string; package?: string; where?: string; steps?: string[]; command?: string[]; }
interface Tool { id: string; group: string; name: string; what: string; capabilities: string[]; status: { ready: boolean; detail: string; version?: string }; how: How; custom?: boolean; }
interface ToolProposal { entry: { id: string; group: string; name: string; what: string; check: Record<string, unknown>; get: Record<string, unknown> }; why: string; by: string; status: string; found?: { ready: boolean; detail: string }; }
interface Request { id: string; service: string; title: string; why: string; status: string; by: string; at: string; commands: string[]; result?: string; reason?: string; log: string; project?: string; }
interface Ask { id: string; project: string; department: string; capability: string; why: string; at: string; }
interface ProjectEnv { python: { environment: string | null; packages: { module: string; package: string; ready: boolean }[]; missing: string[] }; firmware: { device: string; board: string; platform: string; libraries: string[] }[]; node: boolean; }
interface View { groups: Record<string, { service: string; name: string; blurb: string }>; tools: Tool[]; project: ProjectEnv | null; requests: Request[]; asks: Ask[]; used: Record<string, string>; toolProposals: ToolProposal[]; }
interface Message { side: string; role: "you" | "agent"; text: string; at: string; }
interface Audit { ok: boolean; problems: string[]; sources: { url: string; kind: string; opened: boolean }[]; }
interface Review { verdict: string; supported: boolean; on_topic: boolean; problems: string[]; }
interface Skill { id: string; tool: string; departments: string[]; title: string; summary: string; status: string; sources: string[]; content: string; by: string; audit?: Audit; review?: Review; automatic?: boolean; }

function Verification({ s }: { s: Skill }) {
  const passed = !!s.audit?.ok && s.review?.verdict === "pass" && s.review.supported && s.review.on_topic;
  const official = s.audit?.sources.filter((x) => x.kind === "official").length ?? 0;
  return (
    <div className={passed ? "tool-why" : "warn-text"}>
      {passed ? `Verified: ${official} official ${official === 1 ? "source" : "sources"}, every page opened, the reviewer found every instruction supported and on topic, and no warning signs.`
        : !s.review ? "Not reviewed yet." : `Not verified: ${[...(s.audit?.problems ?? []), ...s.review.problems].join(" · ")}`}
    </div>
  );
}

const TAB_NAMES: Record<string, string> = { cloud: "Cloud", app: "App", platform: "Platform", project: "Project" };
const SIDES = { external: { title: "External Service", groups: ["cloud", "app"], intro: "Connects the app to outside services (online APIs and MCP servers) so the agents can use them, guiding you through the setup. For programs you operate yourself, it writes Skills: guides the departments' agents follow to help you use them." },
  internal: { title: "Internal Service", groups: ["platform", "project"], intro: "Looks after what the project's code needs on this computer: its Python environment, and the programs and toolchains it builds and runs with (compilers, SDKs, build tools). It also makes tool tabs. Every install waits for you." } };

function ToolCard({ tool, onInstall, onKey, busy, why, onUse }: { tool: Tool; onInstall: (t: Tool) => void; onKey: (t: Tool, key: string) => void; busy: boolean; why?: string; onUse?: (t: Tool, on: boolean) => void }) {
  const [key, setKey] = useState("");
  const ready = tool.status.ready;
  return (
    <div className={`tool-card ${ready ? "ready" : "missing"}`}>
      <div className="tool-head"><b>{tool.name}</b><em className={`pill ${ready ? "good" : "warn"}`}>{ready ? "Ready" : "Not set up"}</em></div>
      <p>{tool.what}</p>
      {why && <p className="tool-why">{why}</p>}
      {tool.custom && <p className="hint">Added by you, from an agent's proposal.</p>}
      <p className="hint">{tool.status.version ? `${tool.status.version}. ` : ""}{tool.status.detail}</p>
      {!ready && tool.how.kind === "key" && (
        <div className="row">
          <input type="password" autoComplete="off" placeholder="Paste the key here" aria-label={`${tool.name} key`} value={key} onChange={(e) => setKey(e.target.value)} />
          <button disabled={busy || key.trim().length < 8} onClick={() => { onKey(tool, key); setKey(""); }}>Save key</button>
          {tool.how.where && <a href={tool.how.where} target="_blank" rel="noreferrer">Get a key</a>}
        </div>
      )}
      {!ready && (tool.how.kind === "winget" || tool.how.kind === "pip") && <div className="row"><button className="primary" disabled={busy} onClick={() => onInstall(tool)}>Install</button><span className="hint">{tool.how.label}</span></div>}
      {!ready && tool.how.kind === "steps" && <ol className="tool-steps">{tool.how.steps?.map((s) => <li key={s}>{s}</li>)}</ol>}
      {onUse && <div className="row"><button className="small" disabled={busy} onClick={() => onUse(tool, !why)}>{why ? "Remove from this project" : "Use in this project"}</button></div>}
    </div>
  );
}

export default function ServicesScreen({ side }: { side: "external" | "internal" }) {
  const { projectId } = useProject();
  const { busy, error, run } = useBusy();
  const [view, setView] = useState<View | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [sending, setSending] = useState("");
  const [skills, setSkills] = useState<Skill[]>([]);
  const [autoSkills, setAutoSkills] = useState(false);
  const q = `project=${encodeURIComponent(projectId ?? "")}`;
  const load = (refresh = false) => call<View>(`/services?${q}${refresh ? "&refresh=true" : ""}`).then(setView);
  useEffect(() => { load().catch(() => undefined); call<Message[]>(`/services/${side}/messages`).then(setMessages).catch(() => undefined); call<Skill[]>("/skills").then(setSkills).catch(() => undefined); call<{ on: boolean }>("/skills/auto").then((r) => setAutoSkills(r.on)).catch(() => undefined); }, [side, projectId]);
  useEffect(() => { const t = window.setInterval(() => { if (view?.requests.some((r) => r.status === "installing" || r.status === "approved")) load().catch(() => undefined); }, 3000); return () => window.clearInterval(t); }, [view]);

  const installPackages = (packages: string[]) => {
    if (!window.confirm(`Install into this project's own Python environment:\n\n${packages.join("\n")}\n\nIt will be created first if it doesn't exist.`)) return;
    run("Installing", async () => setView(await call<View>("/services/install", "POST", { service: "project-python", project: projectId, packages, why: "Pressed Install for the packages this project's code imports." })));
  };
  const decide = (r: Request, approve: boolean) => {
    const reason = approve ? "" : window.prompt("Why not?", "") ?? "";
    if (!approve && !reason) return;
    run(approve ? "Approving" : "Rejecting", async () => setView(await call<View>(`/services/requests/${r.id}/${approve ? "approve" : "reject"}`, "POST", { reason, project: projectId })));
  };
  const send = (text: string) => { setSending(text); run("", async () => { try { setMessages(await call<Message[]>(`/services/${side}/chat`, "POST", { message: text, project: projectId })); await load(); setSkills(await call<Skill[]>("/skills")); window.dispatchEvent(new Event("ba:connections")); window.dispatchEvent(new Event("ba:tabs")); } finally { setSending(""); } }); };
  const decideSkill = (s: Skill, approve: boolean) => {
    const reason = approve ? "" : window.prompt("Why not? External Service reads this.", "") ?? "";
    if (!approve && !reason) return;
    run(approve ? "Approving" : "Rejecting", async () => setSkills(await call<Skill[]>(`/skills/${s.id}/decide`, "POST", { approve, reason, project: projectId })));
  };

  const closeAsk = (a: Ask) => {
    const reason = window.prompt(`Close this request from ${a.department}? Optionally say why (for example, it's solved).`, "");
    if (reason === null) return;
    run("Closing", async () => { await call(`/services/asks/${a.id}/close`, "POST", { reason }); await load(); });
  };

  const chat: ChatMessage[] = messages.map((m) => ({ from: m.role === "you" ? "you" : "agent", text: m.text }));
  if (sending) chat.push({ from: "you", text: sending });
  const requests = side === "internal" ? (view?.requests ?? []) : [];
  const env = view?.project;

  return (
    <div className="page">
      <div className="hq-head"><h1>{SIDES[side].title}</h1></div>
      <p className="intro">{SIDES[side].intro}</p>
      {error && <p className="error" role="alert">{error}</p>}
      {busy && <p className="hint">{busy}…</p>}

      {requests.filter((r) => r.status === "waiting").map((r) => (
        <div key={r.id} className="note service-request">
          <div><b>{r.by.replace("_", " ")} proposes:</b> {r.title}. <span className="hint">{r.why}</span></div>
          <code className="mono small-text">{r.commands.join("  &&  ")}</code>
          <div className="row"><button className="primary" disabled={!!busy} onClick={() => decide(r, true)}>Approve and install</button><button disabled={!!busy} onClick={() => decide(r, false)}>Reject</button></div>
        </div>
      ))}
      {side === "internal" && (view?.toolProposals ?? []).filter((t) => t.status === "waiting").map((t) => (
        <div key={t.entry.id} className="note service-request">
          <div><b>{t.by.replace("_", " ")} proposes a program for this computer: {t.entry.name}</b> <span className="hint">{t.entry.what} {t.why}</span></div>
          <div className="hint">How it's checked: <code className="mono small-text">{JSON.stringify(t.entry.check)}</code>. How it's installed: <code className="mono small-text">{JSON.stringify(t.entry.get)}</code>.{t.found ? (t.found.ready ? " It's already installed." : " Not installed yet.") : ""}</div>
          <div className="row">
            <button className="primary" disabled={!!busy} onClick={() => run("Approving", async () => setView(await call<View>(`/services/tools/${t.entry.id}/decide`, "POST", { approve: true, project: projectId })))}>Approve</button>
            <button disabled={!!busy} onClick={() => run("Rejecting", async () => setView(await call<View>(`/services/tools/${t.entry.id}/decide`, "POST", { approve: false, reason: "Not now.", project: projectId })))}>Reject</button>
            <span className="hint">Approving adds it to the list below. Installing it is a separate step you approve.</span>
          </div>
        </div>
      ))}
      {side === "internal" && (view?.tools ?? []).some((t) => t.custom) && (
        <details className="card stage-report" open>
          <summary>Programs and toolchains on this computer</summary>
          <ul className="status-list">{(view?.tools ?? []).filter((t) => t.custom).map((t) => (
            <li key={t.id}>
              <i className={`dot ${t.status.ready ? "pass" : "warn"}`} />
              <span><b>{t.name}</b> <span className="hint">{t.status.ready ? `Installed${t.status.version ? `: ${t.status.version}` : ""}.` : t.status.detail}</span></span>
              {!t.status.ready && t.how.kind !== "steps" && t.how.kind !== "key" && <button className="small" disabled={!!busy} onClick={() => run("Installing", async () => setView(await call<View>("/services/install", "POST", { service: t.id, project: projectId, why: "Pressed Install." })))}>Install</button>}
              {!t.status.ready && t.how.kind === "steps" && <span className="hint">{(t.how.steps ?? []).join(" ")}</span>}
            </li>
          ))}</ul>
          <p className="hint">Internal Service proposes programs the project's code needs, such as a compiler, an SDK or a build tool. Each one, and each install, waits for you.</p>
        </details>
      )}
      {side === "external" && skills.filter((s) => s.status === "waiting").map((s) => (
        <div key={s.id} className="note service-request">
          <div><b>External Service wrote a Skill for {s.departments.join(", ")}: {s.title}</b> <span className="hint">(for {s.tool}). {s.summary}</span></div>
          <Verification s={s} />
          <span className="hint">Sources: {s.sources.map((u) => <a key={u} href={u} target="_blank" rel="noreferrer">{new URL(u).hostname} </a>)}. Once approved, {s.departments.join(" and ")} use it with every request.</span>
          <details><summary>Read the Skill</summary><pre className="file-text wrap">{s.content}</pre></details>
          <div className="row"><button className="primary" disabled={!!busy} onClick={() => decideSkill(s, true)}>Approve and give to {s.departments.join(", ")}</button><button disabled={!!busy} onClick={() => decideSkill(s, false)}>Reject</button></div>
        </div>
      ))}
      {(view?.asks ?? []).length > 0 && (() => {
        const all = view!.asks;
        const here = all.filter((a) => a.project === projectId);
        const elsewhere = all.filter((a) => a.project !== projectId);
        const item = (a: Ask) => (
          <li key={a.id}><b>{a.department}</b>{a.project !== projectId ? ` (${a.project})` : ""}: {a.capability}. <span className="hint">{a.why}</span>{" "}
            <button className="small" disabled={!!busy} onClick={() => closeAsk(a)}>Close</button></li>
        );
        return (
          <div className="note"><b>Requests from departments to {SIDES[side].title}</b>
            <p className="hint">These aren't waiting for your approval. A department asked for a tool it lacked; each request closes when {SIDES[side].title} answers it, its department later finishes the job, or you close it. Ask below which tool would provide one.</p>
            {here.length > 0 ? <ul>{here.map(item)}</ul> : <p className="hint">None from this project.</p>}
            {elsewhere.length > 0 && <details><summary>{elsewhere.length} from other projects</summary><ul>{elsewhere.map(item)}</ul></details>}
          </div>
        );
      })()}

      {side === "external" && (
        <p className="hint">Approving verified Skills automatically: {autoSkills ? "on" : "off"}. Change it in <ScreenChip screen="Settings">Settings</ScreenChip>.</p>
      )}
      {side === "external" && skills.filter((s) => s.status === "approved" && s.automatic).slice(0, 3).map((s) => (
        <p key={s.id} className="hint">Approved automatically: <b>{s.title}</b> for {s.departments.join(", ")} ({s.tool}).</p>
      ))}
      {side === "internal" && (!env ? <p className="hint">Choose a project with the switcher at the top left to see its Python environment and firmware.</p> : (
          <div className="grid">
            <section className="card">
              <h3>Python environment</h3>
              <p className="hint">{env.python.environment ? `This project has its own environment in ${env.python.environment}.` : "This project has no environment of its own yet; one is made when you install its packages."} Packages come from what the project's code imports.</p>
              {env.python.packages.length === 0 ? <p className="hint">The project's code doesn't import any outside Python packages.</p> : (
                <ul className="status-list">{env.python.packages.map((p) => <li key={p.module}><i className={`dot ${p.ready ? "pass" : "warn"}`} /><span className="mono">{p.package}</span><em>{p.ready ? "installed" : "missing"}</em></li>)}</ul>
              )}
              {env.python.missing.length > 0 && <button className="primary" disabled={!!busy} onClick={() => installPackages(env.python.missing)}>Install the {env.python.missing.length} missing</button>}
            </section>
            <section className="card">
              <h3>Firmware toolchains</h3>
              {env.firmware.length === 0 ? <p className="hint">No PlatformIO firmware in this project.</p> : env.firmware.map((f) => (
                <div key={f.device} className="tool-card"><b>{f.device}</b><p className="hint">{f.platform} · {f.board}</p>{f.libraries.length > 0 && <p className="mono small-text">{f.libraries.join(", ")}</p>}</div>
              ))}
              <p className="hint">PlatformIO downloads each board's toolchain and libraries itself the first time it builds. PlatformIO itself is under Platform.</p>
            </section>
          </div>
      ))}

      {requests.filter((r) => r.status !== "waiting").length > 0 && (
        <details className="card stage-report" open={requests.some((r) => r.status === "installing" || r.status === "approved")}>
          <summary>Installs</summary>
          {requests.filter((r) => r.status !== "waiting").map((r) => (
            <div key={r.id} className="install-row">
              <div><b>{r.title}</b> <em className={`pill ${r.status === "done" ? "good" : r.status === "failed" || r.status === "rejected" ? "bad" : "warn"}`}>{r.status}</em> <span className="hint">{r.result || r.reason}</span></div>
              {r.log && <pre className="file-text wrap install-log">{r.log}</pre>}
            </div>
          ))}
        </details>
      )}

      {side === "external" && <ConnectionsAdmin />}
      <ToolTabsAdmin />

      <div className="service-chat">
        <AgentChat agent={SIDES[side].title} role={side === "external" ? "Connects outside services, writes Skills" : "Looks after the project's Python environment"} messages={chat}
          thinking={sending ? "Thinking" : undefined} onSend={send} empty={side === "external" ? "Ask External Service to connect a service, such as “connect Altium 365” or “connect GitHub for Software”, or to write a Skill for a program you operate. Uses a little AI, charged to the open project." : "Ask Internal Service about the open project's Python packages. It proposes installs for you to approve. Uses a little AI, charged to the open project."} placeholder={side === "external" ? "Such as “connect Altium 365 for Electrical”" : "Such as “install what the laptop code needs”"} />
      </div>

      {side === "external" && <details className="card stage-report"><summary>AI providers and model tiers</summary><ConnectorsLive /></details>}
      {side === "internal" && <p className="hint">File viewers and converters have their own place now: <ScreenChip screen="Platform">Platform</ScreenChip>, in Reception.</p>}
    </div>
  );
}
