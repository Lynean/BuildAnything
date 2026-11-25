import { useEffect, useState } from "react";

interface AgentDef {
  id: string;
  title: string;
  tier: string;
  model: string;
  area: string;
  systemPrompt: string;
  inputs: Record<string, string>;
  tools: Record<string, string>;
  output: string;
}

export default function AgentsScreen() {
  const [agents, setAgents] = useState<AgentDef[]>([]);
  const [open, setOpen] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/agents")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("The app's server isn't responding."))))
      .then((list: AgentDef[]) => { setAgents(list); setOpen(list[0]?.id ?? ""); })
      .catch((e: Error) => setError(e.message));
  }, []);

  const agent = agents.find((a) => a.id === open);

  return (
    <div className="page agents-page">
      <div className="hq-head">
        <h1>Agents</h1>
        <a className="page-link" href="https://platform.openai.com/traces" target="_blank" rel="noreferrer">Open run traces on OpenAI</a>
      </div>
      <p className="hint">Exactly what each agent is told, what it receives and what it can do. Each run appears in OpenAI's traces under the project and agent name.</p>
      {error && <p className="warn-text">{error}</p>}
      <div className="agents-layout">
        <div className="agents-list">
          {agents.map((a) => (
            <button key={a.id} className={`nav ${a.id === open ? "active" : ""}`} onClick={() => setOpen(a.id)}>
              {a.title}
              <em className="agents-area">{a.area}</em>
            </button>
          ))}
        </div>
        {agent && (
          <div className="agents-detail">
            <h2>{agent.title}</h2>
            <p className="dim-text">{agent.area} · {agent.tier} · {agent.model}</p>
            <h3>System prompt</h3>
            <pre className="agents-prompt">{agent.systemPrompt}</pre>
            <h3>What it receives</h3>
            <table><thead><tr><th>Section</th><th>Meaning</th></tr></thead><tbody>
              {Object.entries(agent.inputs).map(([name, meaning]) => <tr key={name}><td><code>{name}</code></td><td>{meaning}</td></tr>)}
            </tbody></table>
            <h3>Tools</h3>
            {Object.keys(agent.tools).length === 0 ? <p className="hint">None. It only answers.</p> : (
              <table><thead><tr><th>Tool</th><th>What it does</th></tr></thead><tbody>
                {Object.entries(agent.tools).map(([name, meaning]) => <tr key={name}><td><code>{name}</code></td><td>{meaning}</td></tr>)}
              </tbody></table>
            )}
            <h3>What it returns</h3>
            <p>{agent.output}</p>
          </div>
        )}
      </div>
    </div>
  );
}
