import { useEffect, useState } from "react";
import { call, useBusy, useProject } from "./api";
import LaptopBench from "./LaptopBench";

interface Result { value: number | null; outcome: string; evidence: string; by: string; }
interface Proposal { id: string; value: number | null; outcome: string; evidence: string; files: string[]; by: string; at: string; }
interface Test { id: string; key: string; title: string; owner: string; covers: string[]; procedure: string; pass_if: string; metric: string | null; unit: string | null; min: number | null; max: number | null; uses: string[]; status: string; state: string; result: Result | null; proposals?: Proposal[]; staleBecause: string[]; relies: string[]; stage: string; automatic: string; }
interface DepOption { id: string; label: string; kind: string; }
interface Row { id: string; key: string; text: string; personSaid: string; addedBecause: string; status: string; tests: string[]; deviation: string | null; }
interface Deviation { id: string; key: string; what: string; why: string; restores: string; status: string; by: string; closedBecause?: string; }
interface Dropped { key: string; text: string; why: string; }
interface View { afterBuild?: { department: string; items: string[]; at: string }[]; rows: Row[]; tests: Test[]; counts: Record<string, number>; total: number; untestable: string[]; dependencyOptions: DepOption[]; deviations: Deviation[]; dropped: Dropped[]; }

const STATUS: Record<string, [string, string]> = {
  proven: ["Proven", "good"], deviated: ["Accepted deviation", "warn"], failed: ["Failed", "bad"], retest: ["Test again", "warn"], not_tested: ["Not tested yet", ""], needs_approval: ["Test needs approval", "warn"], no_test: ["No test", "bad"],
  passed: ["Passed", "good"],
};

const STAGE: Record<string, string> = { design: "Design stage", before_buying: "Before buying", built: "Once built" };

function DeviationRow({ deviation: d, base, onView }: { deviation: Deviation; base: string; onView: (v: View) => void }) {
  const { busy, error, run } = useBusy();
  const [reason, setReason] = useState("");
  const label = d.status === "active" ? ["Accepted", "warn"] : d.status === "closed" ? ["Closed", "good"] : ["Requirement changed", ""];
  return (
    <div className="deviation">
      <div className="hq-head"><b>{d.key}: {d.what}</b><span className={`pill ${label[1]}`}>{label[0]}</span></div>
      <p className="hint">Why: {d.why} Put right by: {d.restores} Accepted by {d.by}.{d.closedBecause ? ` Closed: ${d.closedBecause}` : ""}</p>
      {error && <p className="error" role="alert">{error}</p>}
      {d.status === "active" && <div className="test-record"><input className="grow" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="What changed?" aria-label={`Why ${d.key} deviation is closing`} /><button className="small" disabled={!!busy || !reason.trim()} onClick={() => run("Saving", async () => onView(await call<View>(`${base}/${d.id}/close`, "POST", { reason })))}>Close deviation</button></div>}
    </div>
  );
}

function Clarify({ requirement, base, onView }: { requirement: string; base: string; onView: (v: View) => void }) {
  const { busy, error, run } = useBusy();
  const [answer, setAnswer] = useState("");
  const send = () => run("Saving", async () => onView(await call<View>(`${base}/clarify`, "POST", { key: requirement, answer })));
  return <div className="test-record"><input className="grow" value={answer} onChange={(e) => setAnswer(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && answer.trim()) send(); }} placeholder="What would you accept? For example, at least 15 cm" aria-label={`What would you accept for ${requirement}?`} /><button className="small" disabled={!!busy || !answer.trim()} onClick={send}>Save</button>{error && <p className="error" role="alert">{error}</p>}</div>;
}

function limits(t: Test) {
  if (t.min !== null && t.max !== null) return `${t.min} to ${t.max} ${t.unit ?? ""}`;
  return t.min !== null ? `At least ${t.min} ${t.unit ?? ""}` : `At most ${t.max} ${t.unit ?? ""}`;
}

function Dependencies({ test, base, options, onView }: { test: Test; base: string; options: DepOption[]; onView: (v: View) => void }) {
  const { busy, error, run } = useBusy();
  const update = (uses: string[]) => run("Saving", async () => onView(await call<View>(`${base}/${test.id}/dependencies`, "POST", { uses })));
  const nameOf = (u: string) => options.find((o) => o.id === u || o.id.endsWith(`:${u}`))?.label ?? u;
  const free = options.filter((o) => !test.uses.some((u) => o.id === u || o.id.endsWith(`:${u}`)));
  return (
    <div className="deps">
      <div className="eyebrow">Depends on</div>
      <div className="chips">
        {test.uses.length === 0 && <span className="hint">Nothing listed, so any {test.owner} change will ask for this test again.</span>}
        {test.uses.map((u) => <span className="chip" key={u}>{nameOf(u)}<button className="chip-x" disabled={!!busy} aria-label={`Remove ${nameOf(u)}`} onClick={() => update(test.uses.filter((x) => x !== u))}>×</button></span>)}
        {free.length > 0 && <select aria-label={`Add a dependency to ${test.key}`} value="" disabled={!!busy} onChange={(e) => e.target.value && update([...test.uses, e.target.value])}><option value="">Add…</option>{free.map((o) => <option key={o.id} value={o.id}>{o.kind}: {o.label}</option>)}</select>}
      </div>
      {test.relies.length > test.uses.length && <details className="relies"><summary>Everything this result relies on ({test.relies.length})</summary><ul className="tried">{test.relies.map((r) => <li key={r}>{r}</li>)}</ul></details>}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}

function TestCard({ test, base, options, onView }: { test: Test; base: string; options: DepOption[]; onView: (v: View) => void }) {
  const { busy, error, run } = useBusy();
  const [value, setValue] = useState("");
  const [evidence, setEvidence] = useState("");
  const [label, tone] = STATUS[test.state] ?? ["", ""];
  const save = (outcome: string) => run("Saving", async () => {
    onView(await call<View>(`${base}/${test.id}/result`, "POST", { value: value.trim() ? Number(value) : null, outcome, evidence }));
    setValue(""); setEvidence("");
  });
  const canRecord = test.status === "confirmed" && test.automatic === "none";
  return (
    <section className={`card test-card compact-test ${test.state}`}>
      <div className="test-line">
        <span className="test-key mono">{test.key}</span>
        <b className="test-title">{test.title}</b>
        {test.metric && <span className="test-limit" title={test.metric}>{limits(test)}</span>}
        <span className="test-meta">{STAGE[test.stage]} · {test.owner}{test.automatic !== "none" ? " · automatic" : ""}</span>
        <span className={`pill ${tone}`}>{label}</span>
      </div>
      <details className="test-how">
        <summary>How to test it, and what it depends on</summary>
        <dl className="stack">
          <dt>How</dt><dd>{test.procedure}</dd>
          <dt>Passes if</dt><dd>{test.pass_if}{test.metric ? ` (${test.metric}: ${limits(test)})` : ""}</dd>
        </dl>
        <Dependencies test={test} base={base} options={options} onView={onView} />
      </details>
      {test.result && <p className="hint">Last result by {test.result.by}: {test.result.value !== null ? `${test.result.value} ${test.unit ?? ""}, ` : ""}{test.result.outcome}. {test.result.evidence}</p>}
      {test.state === "retest" && <p className="warn-text">Changed since this was tested: {test.staleBecause.join(", ")}.</p>}
      {(test.proposals ?? []).map((p) => (
        <div key={p.id} className="test-proposal">
          <p><b>{p.by.replace("_", " ")} proposes:</b> {p.value !== null ? `${p.value} ${test.unit ?? ""}` : p.outcome}. {p.evidence}</p>
          {p.files.length > 0 && <div className="row">{p.files.map((f) => <button key={f} type="button" className="small mono" onClick={() => window.dispatchEvent(new CustomEvent("ba:open-file", { detail: f }))}>{f.split("/").pop()}</button>)}</div>}
          <div className="row">
            <button className="primary small" disabled={!!busy} onClick={() => run("Accepting", async () => onView(await call<View>(`${base}/proposals/${p.id}/accept`, "POST")))}>Accept as the result</button>
            <button className="small" disabled={!!busy} onClick={() => run("Dismissing", async () => onView(await call<View>(`${base}/proposals/${p.id}/dismiss`, "POST", { reason: "Not accepted." })))}>Dismiss</button>
            <span className="hint">{p.value !== null ? "The app judges the number against the limits." : ""}</span>
          </div>
        </div>
      ))}
      {error && <p className="error" role="alert">{error}</p>}
      {canRecord && (
        <div className="test-record">
          {test.metric && <input value={value} onChange={(e) => setValue(e.target.value)} inputMode="decimal" placeholder={`Measured ${test.metric}${test.unit ? ` (${test.unit})` : ""}`} aria-label={`Measured ${test.metric}`} />}
          <input className="grow" value={evidence} onChange={(e) => setEvidence(e.target.value)} placeholder="How did you measure it?" aria-label="How it was measured" />
          {test.metric
            ? <button className="primary" disabled={!!busy || !value.trim() || !evidence.trim()} onClick={() => save("")}>Record result</button>
            : <><button className="primary" disabled={!!busy || !evidence.trim()} onClick={() => save("passed")}>Passed</button><button disabled={!!busy || !evidence.trim()} onClick={() => save("failed")}>Failed</button></>}
        </div>
      )}
    </section>
  );
}

export default function TestsScreen() {
  const { projects, projectId, setProjectId, loadError } = useProject();
  const { busy, error, run } = useBusy();
  const [view, setView] = useState<View | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const base = `/projects/${projectId}/tests`;
  useEffect(() => { if (projectId) call<View>(base).then(setView).catch(() => undefined); }, [projectId]);
  if (!projects) return <div className="page"><h1>Tests</h1><p>{loadError || "Loading…"}</p></div>;
  if (!projectId || !view) return <div className="page"><h1>Tests</h1><p className="intro">Create a project in Headquarters first.</p></div>;
  const waiting = view.tests.filter((t) => t.status === "proposed").length;
  const c = view.counts;
  return (
    <div className="page">
      <div className="hq-head"><h1>Tests</h1><select aria-label="Project" value={projectId} onChange={(e) => setProjectId(e.target.value)}>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div>
      <p className="intro">Does the design do what we said it would? Each requirement needs a test, and each test needs a measured result.</p>
      {(view.afterBuild ?? []).length > 0 && (
        <section className="card wide">
          <h3>To measure after Build</h3>
          <p className="hint">These can only be proven on the real hardware. Until then the departments use the provisional values below, and at Build each one becomes their work.</p>
          {view.afterBuild!.map((a) => <div key={a.department}><b>{a.department}</b> <span className="hint">({a.at})</span><ul>{a.items.map((i) => <li key={i}>{i}</li>)}</ul></div>)}
        </section>
      )}
      {view.total === 0 ? <p className="hint">Requirements appear here once they're confirmed.</p> : (
        <>
          <section className="card wide">
            <div className="test-summary"><a className="page-link csv-link" href={`/api${base}.csv`}>Export to CSV</a><b>{c.proven} of {view.total} proven</b>{c.failed > 0 && <span className="pill bad">{c.failed} failed</span>}{c.retest > 0 && <span className="pill warn">{c.retest} to test again</span>}{c.no_test > 0 && <span className="pill bad">{c.no_test} with no test</span>}</div>
            <div className="meter"><div style={{ width: `${(c.proven / view.total) * 100}%` }} /></div>
            <div className="req-tiles">
              {view.rows.map((r) => (
                <button key={r.id} className={`req-tile ${r.status} ${focus === r.key ? "on" : ""}`} onClick={() => setFocus(focus === r.key ? null : r.key)} title={`${r.text}${r.deviation ? ` · Not met: ${r.deviation}` : ""}`} aria-pressed={focus === r.key}>
                  <span className="req-tile-head"><b className="mono">{r.key}</b><span className={`pill ${STATUS[r.status][1]}`}>{STATUS[r.status][0]}</span></span>
                  <span className="req-tile-text">{r.text}</span>
                  <span className="req-tile-tests">{r.tests.length ? r.tests.map((t) => <i key={t}>{t}</i>) : <em>no test</em>}</span>
                </button>
              ))}
            </div>
            {focus && <p className="hint">Showing the tests that prove {focus}. <button className="link" onClick={() => setFocus(null)}>Show all</button></p>}
          </section>
          <LaptopBench projectId={projectId} />
          {view.deviations.length > 0 && <section className="card wide"><h3>Requirements knowingly not met</h3>{view.deviations.map((d) => <DeviationRow key={d.id} deviation={d} base={base.replace("/tests", "/deviations")} onView={setView} />)}</section>}
          {view.dropped.length > 0 && <section className="card wide"><h3>Requirements dropped</h3><ul className="tried">{view.dropped.map((d) => <li key={d.key}><b>{d.key}:</b> {d.text} <em>{d.why}</em></li>)}</ul></section>}
          <p className="hint">If a requirement can't be met or is no longer wanted, propose that on the Changes screen.</p>
          {view.untestable.length > 0 && <section className="card wide"><h3>Too vague to test</h3><ul className="tried">{view.untestable.map((u) => <li key={u}>{u}{view.rows.some((r) => r.key === u.split(/[\s:,]/)[0] && r.status === "no_test") && <Clarify requirement={u.split(/[\s:,]/)[0]} base={base} onView={setView} />}</li>)}</ul><p className="hint">Raise a change to make these measurable.</p></section>}
          {error && <p className="error" role="alert">{error}</p>}
          {waiting > 0 && <div className="row test-approve"><button className="primary" disabled={!!busy} onClick={() => run("Saving", async () => setView(await call<View>(`${base}/approve-all`, "POST", {})))}>Approve all {waiting} tests</button><span className="hint">Check that these really prove the requirements before you start testing.</span></div>}
          {view.tests.filter((t) => !focus || t.covers.includes(focus)).map((t) => <TestCard key={t.id} test={t} base={base} options={view.dependencyOptions} onView={setView} />)}
        </>
      )}
    </div>
  );
}
