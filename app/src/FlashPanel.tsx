import { useEffect, useState } from "react";
import { call } from "./api";

interface Board { id: string; name: string; mcu: string; platform: string; framework: string; }
interface Config { board: string; platform: string; framework: string; }
interface Port { port: string; description: string; }
interface Plan { id: string; port: string; board: string; chip: string | null; mac: string | null; size: number; can_back_up: boolean; undescribed: number; }
interface Result { outcome: string; backup?: string; lines: string[]; attempts?: number; help?: string; }
interface Backup { file: string; chip: string; mac: string; size: number; }
interface Status { config: Config | null; boardInfo: Board | null; backups: Backup[]; }

const OUTCOME: Record<string, [string, string]> = {
  running: ["Running. The board is printing output.", "note"],
  no_output: ["Flashed. The board printed nothing, which is normal if the code doesn't use serial.", "note"],
  crashing: ["Flashed, but the board is crashing. A problem was opened in the Debug tab.", "error"],
  flash_failed: ["The flash failed.", "error"],
};

function BoardPicker({ onPick, busy }: { onPick: (id: string) => void; busy: boolean }) {
  const [query, setQuery] = useState("");
  const [boards, setBoards] = useState<Board[] | null>(null);
  useEffect(() => {
    const timer = window.setTimeout(() => call<Board[]>(`/boards?q=${encodeURIComponent(query)}`).then(setBoards).catch(() => setBoards([])), 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  return (
    <div>
      <div className="eyebrow">Choose this device's board</div>
      <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search, such as esp32-c6, uno or nucleo" aria-label="Search boards" />
      {boards === null ? <p className="hint">Loading boards…</p> : boards.length === 0 ? <p className="hint">No boards found. Silicon Labs EFR32 isn't supported yet.</p> : (
        <div className="board-list">{boards.map((b) => <button key={b.id} className="board-row" disabled={busy} onClick={() => onPick(b.id)}><b>{b.name}</b><small>{b.mcu} · {b.framework}</small></button>)}</div>
      )}
    </div>
  );
}

export default function FlashPanel({ base, device, onChanged }: { base: string; device: string; onChanged: () => void }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [ports, setPorts] = useState<Port[]>([]);
  const [port, setPort] = useState("");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [safe, setSafe] = useState(false);
  const [noBackup, setNoBackup] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [changing, setChanging] = useState(false);
  const [restoring, setRestoring] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const path = `${base}/firmware/${device}/flash`;

  const refreshPorts = () => call<Port[]>("/ports").then((list) => { setPorts(list); setPort((p) => (list.some((x) => x.port === p) ? p : list[0]?.port ?? "")); }).catch(() => undefined);
  const load = () => call<Status>(path).then(setStatus).catch((e) => setError(e.message));
  useEffect(() => { setStatus(null); setPlan(null); setResult(null); setChanging(false); setError(""); load(); refreshPorts(); }, [path]);

  const run = async (label: string, work: () => Promise<void>) => {
    setBusy(label); setError("");
    try { await work(); } catch (e) { setError((e as Error).message); } finally { setBusy(""); }
  };

  const message = <>{busy && <p className="busy" role="status">{busy}…</p>}{error && <p className="error" role="alert">{error}</p>}</>;
  if (!status) return <p className="hint">Loading…</p>;
  if (!status.config || changing) {
    return <div>{message}<BoardPicker busy={!!busy} onPick={(board) => run("Saving", async () => { await call(`${base}/firmware/${device}/board`, "POST", { board }); setChanging(false); setPlan(null); await load(); onChanged(); })} /></div>;
  }
  const prepare = () => run("Checking the chip and building", async () => {
    setResult(null); setPlan(null); setSafe(false); setNoBackup(false);
    const r = await call<{ ready: boolean; plan?: Plan; build: { detail: string; errors?: string[] } }>(`${path}/prepare`, "POST", { port });
    if (r.ready && r.plan) setPlan(r.plan); else { setError(`${r.build.detail} ${r.build.errors?.[0] ?? ""}`); onChanged(); }
  });
  const flash = () => run("Backing up, flashing and listening. This can take a few minutes", async () => {
    const r = await call<Result>(`${path}/${plan?.id}/approve`, "POST", { confirmed: safe, accept_no_backup: noBackup });
    setResult(r); setPlan(null); setSafe(false); await load(); onChanged();
  });
  const restore = (file: string) => run("Restoring the backup", async () => {
    const r = await call<Result>(`${base}/backups/restore`, "POST", { file, port, confirmed: true });
    setResult(r); setRestoring(""); await load();
  });

  return (
    <div className="flash-panel">
      {message}
      <div className="eyebrow">Board</div>
      <p className="flash-board"><b>{status.boardInfo?.name ?? status.config.board}</b> <button className="small" disabled={!!busy} onClick={() => setChanging(true)}>Change</button></p>
      <div className="eyebrow">Connected to</div>
      <div className="row">
        <select aria-label="Serial port" value={port} onChange={(e) => { setPort(e.target.value); setPlan(null); }}>{ports.length === 0 && <option value="">No USB boards found</option>}{ports.map((p) => <option key={p.port} value={p.port}>{p.port}: {p.description}</option>)}</select>
        <button className="small" disabled={!!busy} onClick={refreshPorts}>Refresh</button>
      </div>
      {!plan && <button className="primary wide" disabled={!!busy || !port} onClick={prepare}>Prepare to flash</button>}
      {plan && (
        <div className="flash-plan">
          <div className="eyebrow">Flash plan</div>
          <dl className="stack">
            <dt>Target</dt><dd>{plan.chip ?? "Unknown chip"} on {plan.port}{plan.mac ? ` (${plan.mac})` : ""}</dd>
            <dt>Firmware</dt><dd>{Math.round(plan.size / 1024)} KB, built just now for {plan.board}</dd>
            <dt>Backup</dt><dd>{plan.can_back_up ? "The whole flash is saved first, unless the app put the current program there." : "This board can't be backed up."}</dd>
          </dl>
          {plan.undescribed > 0 && <p className="warn-text">{plan.undescribed} code blocks or files have no up-to-date description.</p>}
          <label className="consent"><input type="checkbox" checked={safe} onChange={(e) => setSafe(e.target.checked)} /> The board will restart and run this code. Nothing wired to it can move or be damaged.</label>
          {!plan.can_back_up && <label className="consent"><input type="checkbox" checked={noBackup} onChange={(e) => setNoBackup(e.target.checked)} /> Overwrite this board without a backup.</label>}
          <div className="row">
            <button className="primary" disabled={!!busy || !safe || (!plan.can_back_up && !noBackup)} onClick={flash}>Back up and flash</button>
            <button disabled={!!busy} onClick={() => setPlan(null)}>Cancel</button>
          </div>
        </div>
      )}
      {result && (
        <div className="flash-result">
          <p className={OUTCOME[result.outcome]?.[1] ?? "note"}>{OUTCOME[result.outcome]?.[0] ?? result.outcome}</p>
          {result.help && <p className="warn-text">{result.help}</p>}
          {(result.attempts ?? 1) > 1 && result.outcome !== "flash_failed" && <p className="warn-text">It took {result.attempts} tries to connect, so this USB connection may be unreliable.</p>}
          {result.backup && <p className="hint">Backup: {result.backup}</p>}
          {result.lines.length > 0 && <pre className="serial-output" tabIndex={0}>{result.lines.join("\n")}</pre>}
        </div>
      )}
      {status.backups.length > 0 && (
        <details className="flash-backups">
          <summary>Saved backups ({status.backups.length})</summary>
          {status.backups.map((b) => (
            <div key={b.file} className="backup-row">
              <span className="mono">{b.file}</span>
              {restoring === b.file
                ? <span className="decide"><button className="small primary" disabled={!!busy || !port} onClick={() => restore(b.file)}>Replace the board's program</button><button className="small" onClick={() => setRestoring("")}>Cancel</button></span>
                : <button className="small" disabled={!!busy} onClick={() => setRestoring(b.file)}>Restore</button>}
            </div>
          ))}
          <p className="hint">Backups can contain saved passwords. They stay on this computer and are never synced.</p>
        </details>
      )}
    </div>
  );
}
