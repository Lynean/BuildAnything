import { useEffect, useState } from "react";
import { call, useBusy } from "./api";

interface Entry { who: string; text: string; t_ms: number; count: number; ok: boolean; }
interface Scenario { index: number; title: string; covers: string; passed: boolean; error: string; sim_ms: number; checks: Entry[]; problems: Entry[]; notes: Entry[]; measures: { name: string; value: number; unit: string }[]; screens: string[]; }
interface Fake { file: string; part_number: string; instance: string; observe: string[]; rules: string[]; gaps: string[]; }
interface Device {
  device: string; ready: boolean; stale: boolean; accepted: boolean; screen: string;
  parts: { part_number: string; built_in: boolean }[];
  fakes: Fake[];
  coverage: { covered: { requirement: string; scenario: string; how: string }[]; not_covered: { requirement: string; reason: string }[] } | null;
  results: { ok: boolean; ran: boolean; stage: string; errors?: { where: string; text: string }[]; scenarios: Scenario[] } | null;
}
interface View { compiler: boolean; devices: Device[]; }

function FakeChip({ fake }: { fake: Fake }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`bench-fake ${open ? "open" : ""}`}>
      <button className="bench-fake-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="bench-fake-dot" />
        <b>{fake.part_number}</b>
        <span className="hint">{fake.rules.length} rules{fake.gaps.length ? `, ${fake.gaps.length} guessed` : ""}</span>
      </button>
      {open && (
        <div className="bench-fake-body">
          <h4>Rules it checks the firmware against</h4>
          <ul>{fake.rules.map((r) => <li key={r}>{r}</li>)}</ul>
          {fake.gaps.length > 0 && <><h4>Not in the documents, so the fake guessed</h4><ul className="bench-gaps">{fake.gaps.map((g) => <li key={g}>{g}</li>)}</ul></>}
          <h4>What scenarios can see and do</h4>
          <ul className="mono-list">{fake.observe.map((o) => <li key={o}>{o}</li>)}</ul>
        </div>
      )}
    </div>
  );
}

function ScenarioCard({ s, base, common }: { s: Scenario; base: string; common: Set<string> }) {
  const failed = s.checks.filter((c) => !c.ok);
  return (
    <article className={`bench-scenario ${s.passed ? "pass" : "fail"}`}>
      <header><span className="bench-mark">{s.passed ? "✓" : "✗"}</span><div><b>{s.title}</b><div className="hint">{s.covers} · {(s.sim_ms / 1000).toFixed(1)} s of device time</div></div></header>
      {s.measures.length > 0 && <div className="bench-measures">{s.measures.map((m) => <span key={m.name} className="bench-measure"><small>{m.name}</small><b>{Number.isInteger(m.value) ? m.value : m.value.toFixed(1)} {m.unit}</b></span>)}</div>}
      {s.error && <p className="bench-why">{s.error}</p>}
      {failed.map((c) => <p key={c.text} className="bench-why">{c.text}</p>)}
      {s.problems.filter((p) => !common.has(p.text)).map((p) => <p key={p.text} className="bench-problem"><b>{p.who}:</b> {p.text}{p.count > 1 ? ` (${p.count} times)` : ""}</p>)}
      {s.passed && s.checks.map((c) => <p key={c.text} className="bench-ok">{c.text}</p>)}
      {s.screens.length > 0 && <div className="bench-screens">{s.screens.map((name) => <figure key={name}><img src={`/api${base}/screens/${s.index}/${name}`} alt={`What the fake screen showed: ${name}`} /><figcaption>{name.replace(/\.png$/, "").replace(/_/g, " ")}</figcaption></figure>)}</div>}
    </article>
  );
}

export default function LaptopBench({ projectId }: { projectId: string }) {
  const [view, setView] = useState<View | null>(null);
  const { busy, error, run } = useBusy();
  const base = `/projects/${projectId}/bench`;
  useEffect(() => { call<View>(base).then(setView).catch(() => undefined); }, [projectId]);
  if (!view || view.devices.length === 0) return null;
  return (
    <>
      {view.devices.map((d) => {
        const results = d.results?.scenarios ?? [];
        const passed = results.filter((s) => s.passed).length;
        // Rules a fake reported in every scenario are said once, above the cards.
        const common = new Set(results.length > 1 ? results[0].problems.map((p) => p.text).filter((t) => results.every((s) => s.problems.some((p) => p.text === t))) : []);
        const commonRows = results[0]?.problems.filter((p) => common.has(p.text)) ?? [];
        const dbase = `${base}/${d.device}`;
        return (
          <section key={d.device} className="card wide bench-card">
            <div className="bench-head">
              <div>
                <h3>Laptop bench: {d.device}</h3>
                <p className="hint">The real firmware runs on this computer against fake parts written from their documents, by an agent that never saw the firmware. It catches code mistakes before flashing, not wiring or hardware faults.</p>
              </div>
              <div className="row">
                {d.ready && <button disabled={!!busy || !view.compiler} onClick={() => run("Running the bench", async () => setView(await call<View>(`${dbase}/run`, "POST", {})))}>Run again</button>}
                {!d.ready && <button className="primary" disabled={!!busy || !view.compiler || d.parts.length === 0} onClick={() => run("Writing fake parts and scenarios", async () => setView(await call<View>(`${dbase}/build`, "POST", {})))}>Build the bench</button>}
              </div>
            </div>
            {!view.compiler && <p className="error">No laptop C++ compiler (g++) was found, so the bench can't run here.</p>}
            {error && <p className="error" role="alert">{error}</p>}
            {d.results && d.ready && (
              <div className={`bench-trust ${d.accepted ? "on" : ""}`}>
                {d.accepted
                  ? <span><b>In use.</b> Failures from this bench are sent to the department that owns the code.</span>
                  : <span><b>Not directing the team yet.</b> A fake can be wrong. Check the pictures look like your real screen and read what each fake guessed, then use the results.</span>}
                {results.some((s) => s.screens.length > 0) && (
                  <label className="hint">Fake screen compared with the real one <select aria-label="Turn the fake screen" value={d.screen} disabled={!!busy} onChange={(e) => run("Turning the screen and running again", async () => setView(await call<View>(`${dbase}/screen`, "POST", { screen: e.target.value })))}>
                    <option value="none">Same way up</option><option value="rotate_180">Upside down: turn it</option><option value="mirror_x">Mirrored left to right</option><option value="mirror_y">Mirrored top to bottom</option>
                  </select></label>
                )}
                {!d.accepted && <button className="primary" disabled={!!busy} onClick={() => run("Saving", async () => setView(await call<View>(`${dbase}/accept`, "POST", {})))}>Use these results</button>}
              </div>
            )}
            {d.fakes.length > 0 && <div className="bench-fakes">{d.fakes.map((f) => <FakeChip key={f.file} fake={f} />)}</div>}
            {d.results && (
              <>
                <div className="bench-summary">
                  {results.length > 0 && <><b>{passed} of {results.length} scenarios passed</b><div className="meter"><div style={{ width: `${(passed / results.length) * 100}%` }} /></div></>}
                  {d.stale && <span className="pill warn">Out of date: run again</span>}
                </div>
                {d.results.errors && d.results.errors.length > 0 && <ul className="bench-errors">{d.results.errors.map((e) => <li key={e.text}><span className="pill">{e.where}</span> {e.text}</li>)}</ul>}
                {commonRows.length > 0 && <div className="bench-common"><div className="eyebrow">Broken in every scenario</div>{commonRows.map((p) => <p key={p.text} className="bench-problem"><b>{p.who}:</b> {p.text}</p>)}</div>}
                <div className="bench-grid">{results.map((s) => <ScenarioCard key={s.index} s={s} base={dbase} common={common} />)}</div>
              </>
            )}
            {d.coverage && d.coverage.not_covered.length > 0 && (
              <details className="bench-not-covered"><summary>{d.coverage.not_covered.length} requirements the bench can't check</summary>
                <ul>{d.coverage.not_covered.map((n) => <li key={n.requirement}><b>{n.requirement}:</b> {n.reason}</li>)}</ul>
              </details>
            )}
          </section>
        );
      })}
    </>
  );
}
