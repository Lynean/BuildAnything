import { useEffect, useState } from "react";
import { call, useBusy } from "./api";
import type { View } from "./Interfaces";

interface LinkCheck { name: string; state: "pass" | "fail" | "todo"; evidence: string; source: string; }
interface Advice { check: number; possible: string[]; ruled_out: string[]; instruction: string; look_for: string; why_person: string; }
interface Route { cause: string; to: string; label: string; detail: string; }
export interface LinkIssue { id: string; title: string; between: string[]; packet: string | null; trigger: string; status: string; checks: LinkCheck[]; automatic: string[]; advice: Advice | null; answers: { text: string; by: string }[]; routes: Route[]; spent: number; current: number | null; failed: number | null; resolution?: string; }

const LABEL = { pass: "Passed", fail: "Failed here", todo: "Not reached" } as const;
const CAUSES: Record<string, string> = { wrong_address: "Wrong address, port or radio channel", firewall: "Firewall or network settings", weak_signal: "Weak signal", physical_fault: "Physical fault (radio, antenna, connector, power)", antenna_placement: "Antenna placement", format_mismatch: "Format or version mismatch", rejected_by_device: "Rejected or not executed by the device" };
const NEEDS_DEVICE = ["wrong_address", "firewall", "physical_fault", "rejected_by_device"];

function Issue({ issue, base, setView }: { issue: LinkIssue; base: string; setView: (v: View) => void }) {
  const { busy, error, run } = useBusy();
  const [evidence, setEvidence] = useState("");
  const [answer, setAnswer] = useState("");
  const [cause, setCause] = useState("wrong_address");
  const [device, setDevice] = useState(issue.between[0]);
  const [detail, setDetail] = useState("");
  const [final, setFinal] = useState("");
  const [ports, setPorts] = useState<{ port: string; description: string }[]>([]);
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [captured, setCaptured] = useState("");
  useEffect(() => {
    call<{ port: string; description: string }[]>("/ports").then((list) => {
      const options = [...list, { port: "computer", description: "This computer (the program's log)" }];
      setPorts(options);
      setChosen(Object.fromEntries(issue.between.map((d, i) => [d, options[i]?.port ?? ""])));
    }).catch(() => undefined);
  }, [issue.id]);
  const capture = () => run("Listening to both devices for 8 seconds", async () => {
    setCaptured("");
    const r = await call<{ result: { failed: number | null; unknown: string | null }; view: View }>(`${path}/capture`, "POST", { ports: chosen });
    setView(r.view);
    setCaptured(r.result.unknown ?? (r.result.failed === null ? "All five checks passed on the real devices." : `Stopped at check ${r.result.failed + 1}. The agent's next test is below.`));
  });
  const path = `${base}/links/${issue.id}`;
  const post = (label: string, sub: string, body?: unknown, done?: () => void) => run(label, async () => { setView(await call<View>(`${path}/${sub}`, "POST", body ?? {})); done?.(); });
  const current = issue.current;
  const open = issue.status === "open";
  const record = (result: "pass" | "fail") => post("Saving", "check", { index: current, result, evidence }, () => setEvidence(""));
  return (
    <div className="grid">
      <section className="card wide">
        <div className="eyebrow">{issue.between.join(" and ")}{issue.packet ? ` · ${issue.packet}` : ""}</div>
        <h3 className="link-title">{issue.title}</h3>
        <p className="link-meta">{issue.trigger} · Investigation spend ${issue.spent.toFixed(2)} of $0.50{open ? "" : " · Closed"}</p>
        {error && <p className="error" role="alert">{error}</p>}
        <ol className="link-checks">
          {issue.checks.map((c, i) => (
            <li key={c.name} className={c.state}>
              <span className="link-num mono">{i + 1}</span>
              <div><b>{c.name}</b>{c.source === "capture" && <small className="measured">Measured from the devices</small>}<p>{c.evidence || (i === current && open ? "Next to check." : "")}</p></div>
              <span className={`pill ${c.state === "pass" ? "good" : c.state === "fail" ? "bad" : ""}`}>{LABEL[c.state]}</span>
            </li>
          ))}
        </ol>
        {open && issue.packet && (
          <div className="capture-box">
            <div className="eyebrow">Measure it from the devices</div>
            <div className="pkt-grid">{issue.between.map((d) => <label className="field" key={d}>{d}<select value={chosen[d] ?? ""} onChange={(e) => setChosen((c) => ({ ...c, [d]: e.target.value }))}>{ports.length === 0 && <option value="">No USB boards found</option>}{ports.map((p) => <option key={p.port} value={p.port}>{p.port === "computer" ? p.description : `${p.port}: ${p.description}`}</option>)}</select></label>)}</div>
            <button className="primary" disabled={!!busy || ports.length < 2} onClick={capture}>Capture evidence</button>
            {busy.startsWith("Listening") && <p className="busy" role="status">{busy}…</p>}
            {captured && <p className="note" role="status">{captured}</p>}
            <p className="hint">Only listens. Close any serial monitor using these ports first. A computer program must write its evidence log for the app to read it.</p>
          </div>
        )}
        {open && current !== null && (
          <>
            <label className="field">Or say what you saw for "{issue.checks[current].name}"?<input value={evidence} onChange={(e) => setEvidence(e.target.value)} placeholder="For example: the hub log shows 10 packets" /></label>
            <div className="row">
              <button className="primary" disabled={!!busy || !evidence.trim()} onClick={() => record("pass")}>Passed</button>
              <button disabled={!!busy || !evidence.trim()} onClick={() => record("fail")}>Failed here</button>
            </div>
          </>
        )}
        {open && current === null && (
          <>
            <label className="field">Describe the full transfer that worked<input value={final} onChange={(e) => setFinal(e.target.value)} /></label>
            <button className="primary" disabled={!!busy || !final.trim()} onClick={() => post("Closing", "close", { evidence: final })}>Close this issue</button>
          </>
        )}
      </section>
      <section className="card">
        <h3>What the app already knows</h3>
        <ul className="tried">{issue.automatic.map((t, i) => <li key={i}>{t}</li>)}</ul>
        {issue.routes.length > 0 && <><div className="eyebrow">Sent to</div><ul className="tried">{issue.routes.map((r, i) => <li key={i}><b>{r.to}</b>: {r.label}{r.detail ? `. ${r.detail}` : ""}</li>)}</ul></>}
        {open && issue.failed !== null && (
          <div className="route">
            <div className="eyebrow">What caused it?</div>
            <select aria-label="Cause" value={cause} onChange={(e) => setCause(e.target.value)}>{Object.entries(CAUSES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
            {NEEDS_DEVICE.includes(cause) && <select aria-label="Device" value={device} onChange={(e) => setDevice(e.target.value)}>{issue.between.map((d) => <option key={d}>{d}</option>)}</select>}
            <input aria-label="Detail" value={detail} onChange={(e) => setDetail(e.target.value)} placeholder="Detail, such as: code uses port 5006" />
            <button className="small" disabled={!!busy} onClick={() => post("Sending", "route", { cause, device: NEEDS_DEVICE.includes(cause) ? device : "", detail }, () => setDetail(""))}>Send it there</button>
          </div>
        )}
      </section>
      <section className="card">
        <h3>Next test</h3>
        {issue.advice ? (
          <>
            <p className="ask">{issue.advice.instruction}</p>
            <dl>
              <dt>Look for</dt><dd>{issue.advice.look_for}</dd>
              <dt>Why you</dt><dd>{issue.advice.why_person}</dd>
              {issue.advice.possible.length > 0 && <><dt>Still possible</dt><dd>{issue.advice.possible.map((c) => CAUSES[c] ?? c).join(", ")}</dd></>}
              {issue.advice.ruled_out.length > 0 && <><dt>Ruled out</dt><dd>{issue.advice.ruled_out.map((c) => CAUSES[c] ?? c).join(", ")}</dd></>}
            </dl>
          </>
        ) : <p className="hint">Ask the agent to suggest the next test. It can suggest, but only you or the simulator can record a result.</p>}
        {issue.answers.length > 0 && <ul className="tried">{issue.answers.map((a, i) => <li key={i}>{a.by}: {a.text}</li>)}</ul>}
        {open && (
          <>
            <label className="field">What happened?<input value={answer} onChange={(e) => setAnswer(e.target.value)} /></label>
            <div className="row">
              <button disabled={!!busy || !answer.trim()} onClick={() => post("Saving", "answer", { text: answer }, () => setAnswer(""))}>Save answer</button>
              <button className="primary" disabled={!!busy} onClick={() => post("The agent is thinking", "advice")}>{issue.advice ? "Suggest another test" : "Suggest a test"}</button>
            </div>
            {busy && <p className="busy" role="status">{busy}…</p>}
          </>
        )}
      </section>
    </div>
  );
}

export default function LinkIssues({ view, base, setView }: { view: View; base: string; setView: (v: View) => void }) {
  const { busy, error, run } = useBusy();
  const [title, setTitle] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [packet, setPacket] = useState("");
  const [pick, setPick] = useState("");
  const list = [...view.links].reverse();
  const issue = list.find((i) => i.id === pick) ?? list.find((i) => i.status === "open") ?? list[0];
  const devices = [...new Set(view.packets.flatMap((p) => [p.producer, ...p.consumers]))];
  const report = () => run("Opening and running the free checks", async () => {
    const v = await call<View>(`${base}/links`, "POST", { title, from: from.trim().toLowerCase(), to: to.trim().toLowerCase(), packet });
    setView(v); setPick(v.links[v.links.length - 1].id); setTitle("");
  });
  return (
    <div className="link-page">
      {list.length > 1 && <div className="sub-tabs" role="tablist" aria-label="Link issues">{list.map((i) => <button key={i.id} role="tab" aria-selected={i.id === issue?.id} className={i.id === issue?.id ? "on" : ""} onClick={() => setPick(i.id)}>{i.title}{i.status === "closed" ? " (closed)" : ""}</button>)}</div>}
      {issue ? <Issue key={issue.id} issue={issue} base={base} setView={setView} /> : <p className="hint">No link issues. Data isn't failing between devices, or nobody has reported it yet.</p>}
      <section className="card wide link-report">
        <h3>Report a link problem</h3>
        {error && <p className="error" role="alert">{error}</p>}
        <label className="field">What's going wrong?<input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="The hub isn't receiving telemetry" /></label>
        <div className="pkt-grid">
          <label className="field">From<input list="link-devices" value={from} onChange={(e) => setFrom(e.target.value)} placeholder="node-a" /></label>
          <label className="field">To<input list="link-devices" value={to} onChange={(e) => setTo(e.target.value)} placeholder="hub" /></label>
          <label className="field">Packet<select value={packet} onChange={(e) => setPacket(e.target.value)}><option value="">Not sure</option>{view.packets.map((p) => <option key={p.name}>{p.name}</option>)}</select></label>
        </div>
        <datalist id="link-devices">{devices.map((d) => <option key={d} value={d} />)}</datalist>
        <button className="primary" disabled={!!busy || !title.trim() || !from.trim() || !to.trim()} onClick={report}>Open link issue</button>
        {busy && <p className="busy" role="status">{busy}…</p>}
      </section>
    </div>
  );
}
