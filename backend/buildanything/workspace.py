"""Each project is a Git repository, and handoffs are generated from its ledger."""
from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable

from . import gates
from .ledger import Ledger, LedgerError, ProjectState, current_person

GENERATED = re.compile(r"^(AGENTS\.md|CLAUDE\.md|handoffs/|interfaces/generated/|firmware/[^/]+/(include/ba_packets\.h|generated/packets\.(py|ts)$))")
URL_OK = re.compile(r"^(https://|ssh://|git@[\w.-]+:|file://|[A-Za-z]:[\\/]|/)")
GIT_ATTRIBUTES = "ledger/events.jsonl merge=union\n"
GIT_IGNORE = ".cache/\n"


class SyncError(LedgerError):
    pass


def _identity(person: str) -> list[str]:
    name = re.sub(r"[<>\n]", "", person)[:60] or "BuildAnything"
    return ["-c", f"user.name={name}", "-c", "user.email=app@buildanything.invalid", "-c", "commit.gpgsign=false"]


def _git(path: Path, *args: str, person: str | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
    if person is None:
        person = "BuildAnything" if current_person() == "owner" else current_person()
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # A login prompt would hang the app forever.
    return subprocess.run(["git", *_identity(person), *args], cwd=path, capture_output=True, text=True, timeout=timeout, env=env)


def write_git_files(path: Path) -> None:
    (path / ".gitattributes").write_text(GIT_ATTRIBUTES, encoding="utf-8", newline="\n")
    (path / ".gitignore").write_text(GIT_IGNORE, encoding="utf-8", newline="\n")


def git_available() -> bool:
    return shutil.which("git") is not None


def init(path: Path) -> None:
    if git_available():
        _git(path, "init", "--quiet")


def commit(path: Path, message: str) -> str | None:
    """Save the project's current state. Returns the commit ID, or None if nothing changed."""
    if not git_available():
        return None
    if not (path / ".git").exists():
        init(path)  # Projects created before Git support catch up on their first save.
    _git(path, "add", "-A")
    if _git(path, "diff", "--cached", "--quiet").returncode == 0:
        return None
    if _git(path, "commit", "--quiet", "-m", message).returncode != 0:
        return None
    return _git(path, "rev-parse", "--short", "HEAD").stdout.strip()


def status(path: Path) -> dict:
    if not git_available():
        return {"available": False, "unsaved": 0, "last": None}
    if not (path / ".git").exists():
        return {"available": True, "unsaved": 1, "last": None}
    lines = _git(path, "status", "--porcelain").stdout.splitlines()
    last = _git(path, "log", "-1", "--format=%h %s").stdout.strip() or None
    remote = _git(path, "remote", "get-url", "origin").stdout.strip() or None
    return {"available": True, "unsaved": len(lines), "last": last, "remote": remote}


def _bullets(items: list[str], empty: str) -> str:
    return "\n".join(f"- {i}" for i in items) if items else f"- {empty}"


def _delivery_sections(state: ProjectState, ledger: Ledger | None) -> str:
    """Interfaces, problems and recent work. Needs the event log, and is left out without it."""
    if ledger is None:
        return ""
    from . import debugging, firmware, interfaces, links, software, traceability

    trace = traceability.view(ledger)
    c = trace["counts"]
    packets = interfaces.packets(state)
    packet_rows = [f"{p['name']} v{p['version']}: {p['producer']} to {', '.join(p['consumers'])}" + (f"; waiting for {', '.join(p['waiting'])} to acknowledge" if p["waiting"] else "") for p in packets]
    all_links = links.issues(ledger).values()
    link_rows = [f"{i['title']} ({' and '.join(i['between'])}), stopped at: {i['checks'][i['current']]['name'] if i['current'] is not None else 'ready to close'}" for i in all_links if i["status"] == "open"]
    debug_rows = [f"{s['title']} [{s['domain']}]: {s['next']}" for s in debugging.sessions(ledger).values() if s["status"] == "open"]
    device_rows = []
    events = ledger.events()
    for device in firmware.devices(ledger.root):
        files = [f["path"] for f in firmware.scan_workspace(ledger.root, device) if not f.get("generated")]
        kind = "computer" if firmware.is_computer(ledger.root, device) else "board"
        plan = software.setup_view(events, device)
        unfinished = sum(1 for s in plan["steps"] if s["done"] != "worked")
        device_rows.append(f"{device} ({kind}): {', '.join(files) or 'no code yet'}" + (f"; {unfinished} setup steps unfinished" if unfinished else ""))
    recent = [f"{e['at'][:10]} {e['actor']['name']}: {e['kind']}" + (f" {e['data'].get('gate') or e['data'].get('name') or ''}".rstrip()) for e in events if e["actor"]["kind"] == "human"][-10:]
    return f"""
## Interfaces

Generated code is in `interfaces/generated/`, and copied into each device that uses it. Never edit it by hand.

{_bullets(packet_rows, 'None defined yet.')}

## Devices and code

{_bullets(device_rows, 'None yet.')}

## Proof that it works

Of {trace['total']} requirements. Proven: {c['proven']}. Accepted deviations: {c['deviated']}. Failed: {c['failed']}. To test again: {c['retest']}. Not tested yet: {c['not_tested']}. Test awaiting approval: {c['needs_approval']}. No test: {c['no_test']}.

## Problems still open

### Link issues
{_bullets(link_rows, 'None.')}

### Debug sessions
{_bullets(debug_rows, 'None.')}

## Latest actions by people

{_bullets(recent, 'None yet.')}
"""


def handoff_markdown(state: ProjectState, ledger: Ledger | None = None) -> str:
    current = gates.current_gate(state)
    gate_name = gates.stage(current, state) if current else "All gates complete"
    missing = gates.missing(current, state, ledger) if current else []
    decisions = [d["fields"]["text"] for d in state.of_type("decision", "confirmed")]
    requirements = [f"{r['fields'].get('key', r['id'])}: {r['fields']['text']} (tested by: {r['fields'].get('verification', 'not set')})" for r in state.of_type("requirement", "confirmed")]
    tasks = [f"{t['fields'].get('key', t['id'])} [{t['fields'].get('department', '')}]: {t['fields']['title']}" for t in state.of_type("task", "confirmed")]
    claims = [f"{c['fields']['statement']} ({c['fields'].get('ref', 'no reference')})" for c in state.of_type("claim", "confirmed")]
    pending = [f"{r['type']}: {r['fields'].get('text') or r['fields'].get('title') or r['fields'].get('statement', '')}" for r in state.records.values()
               if r["status"] == "proposed" and r["type"] in ("requirement", "task", "claim")]
    high = [f["fields"]["text"] for f in gates.open_high_findings(state)]
    return f"""# {state.meta.get('name', 'Project')}: handoff

*Generated from the project ledger on {dt.date.today().isoformat()}. This is a snapshot: if it disagrees with the repository, trust the repository.*

## Summary

- **Aim:** {state.meta.get('aim') or 'Not set'}
- **Scope:** {state.meta.get('scope') or 'Not set'}
- **Current gate:** {current or 'None'}, {gate_name}
- **AI spend:** ${state.spent_usd:.2f} of ${state.limit_usd:.2f}

## What already exists

### Decisions
{_bullets(decisions, 'None yet.')}

### Requirements
{_bullets(requirements, 'None confirmed yet.')}

### Tasks
{_bullets(tasks, 'None confirmed yet.')}

### Sourced claims
{_bullets(claims, 'None confirmed yet.')}

## Next steps, in order

{_bullets(missing, 'Nothing is blocking the current gate. It is waiting for approval.')}

## Open items

### Waiting for confirmation
{_bullets(pending, 'None.')}

### High-severity Verifier findings
{_bullets(high, 'None.')}
{_delivery_sections(state, ledger)}"""


AGENT_FILE = """# Project instructions

{note}

The project's truth is in `ledger/events.jsonl`. Never edit that file by hand.

- Only a person can approve gates, confirm records or change spending limits.
- Read the latest file in `handoffs/` before making changes.
- Cite a source for engineering claims. If a value isn't known, say so.
- Describe any code you write or edit.
"""


def write_handoff(path: Path, state: ProjectState, ledger: Ledger | None = None) -> Path:
    folder = path / "handoffs"
    folder.mkdir(exist_ok=True)
    target = folder / f"HANDOFF-{dt.datetime.now().strftime('%Y-%m-%d-%H%M%S')}.md"
    target.write_text(handoff_markdown(state, ledger), encoding="utf-8", newline="\n")
    note = "This file is generated by BuildAnything."
    for name in ("AGENTS.md", "CLAUDE.md"):
        (path / name).write_text(AGENT_FILE.format(note=note), encoding="utf-8", newline="\n")
    return target


# ---- Working with other people ----


def _check_url(url: str) -> str:
    url = url.strip()
    if not URL_OK.match(url) or url.startswith("-") or re.search(r"://[^/@]+:[^/@]+@", url):
        raise SyncError("Use an https, ssh or local Git address, without a password in it.")
    return url


def set_remote(path: Path, url: str) -> None:
    if not git_available():
        raise SyncError("Git isn't available on this computer.")
    url = _check_url(url)
    if not (path / ".git").exists():
        init(path)
    action = "set-url" if _git(path, "remote", "get-url", "origin").returncode == 0 else "add"
    done = _git(path, "remote", action, "origin", url)
    if done.returncode != 0:
        raise SyncError(done.stderr.strip()[:300] or "Git couldn't save that address.")


def _decision_conflicts(events: list[dict[str, Any]], local: set[str], names: dict[str, str]) -> list[str]:
    """Where a teammate and you decided the same thing differently. The later decision stands until you review it."""
    found = []
    decided: dict[str, list[dict[str, Any]]] = {}
    versions: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    for event in events:
        if event["kind"] in ("record.confirmed", "record.rejected"):
            decided.setdefault(event["data"]["id"], []).append(event)
        elif event["kind"] == "packet.saved":
            d = event["data"]
            versions.setdefault((d["packet"]["name"], d["major"], d["minor"]), []).append(event)
    for record, items in decided.items():
        if len({e["kind"] for e in items}) > 1 and any(e["id"] in local for e in items) and any(e["id"] not in local for e in items):
            found.append(f"{names.get(record, record)} was both confirmed and rejected. The later decision stands, so please review it.")
    for (name, major, minor), items in versions.items():
        if len({str(e["data"]["packet"]) for e in items}) > 1 and any(e["id"] in local for e in items) and any(e["id"] not in local for e in items):
            found.append(f"{name} v{major}.{minor} was changed by two people. The later change stands, so please review it.")
    return found


def sync(path: Path, person: str, regenerate: Callable[[], Any]) -> dict[str, Any]:
    """Save, bring in teammates' work, then publish. Only a person pressing Sync starts this."""
    if not git_available():
        raise SyncError("Git isn't available on this computer.")
    if _git(path, "remote", "get-url", "origin").returncode != 0:
        raise SyncError("Add the team's Git address first.")
    write_git_files_if_missing(path)
    ledger = Ledger(path)
    local_ids = {e["id"] for e in ledger.events()}
    commit_as(path, "Save project state", person)
    branch = _git(path, "branch", "--show-current").stdout.strip()
    fetched = _git(path, "fetch", "origin", timeout=180)
    if fetched.returncode != 0:
        raise SyncError(f"Couldn't reach the team's repository: {fetched.stderr.strip()[:300]}")
    brought, conflicts = 0, []
    if _git(path, "rev-parse", "--verify", "--quiet", f"origin/{branch}").returncode == 0:
        merged = _git(path, "merge", "--no-commit", "--no-ff", f"origin/{branch}", person=person, timeout=180)
        if merged.returncode != 0:
            unmerged = [f for f in _git(path, "diff", "--name-only", "--diff-filter=U").stdout.splitlines() if f]
            real = [f for f in unmerged if not GENERATED.match(f)]
            if real or not unmerged:
                _git(path, "merge", "--abort")
                detail = f"These files were changed by both of you: {', '.join(real)}." if real else f"Git refused to merge: {(merged.stderr or merged.stdout).strip()[:300]}"
                return {"ok": False, "detail": f"{detail} Nothing was changed or published.", "conflicts": real, "brought": 0, "pushed": False}
            for file in unmerged:  # Generated files are rebuilt just below.
                _git(path, "checkout", "--theirs", "--", file)
                _git(path, "add", "--", file)
        try:
            events = Ledger(path).events()
        except LedgerError as exc:
            _git(path, "merge", "--abort")
            raise SyncError(f"The team's project history failed its integrity check, so nothing was merged. {exc}") from exc
        brought = len([e for e in events if e["id"] not in local_ids])
        names = {e["data"]["id"]: str((e["data"].get("fields") or {}).get("text") or (e["data"].get("fields") or {}).get("title") or e["data"]["id"]) for e in events if e["kind"] == "record.proposed"}
        conflicts = _decision_conflicts(events, local_ids, names)
        if (path / ".git" / "MERGE_HEAD").exists():
            _git(path, "commit", "--quiet", "--no-edit", person=person)
    regenerate()
    commit_as(path, "Rebuild generated files", person)
    pushed = _git(path, "push", "-u", "origin", "HEAD", person=person, timeout=180)
    if pushed.returncode != 0:
        return {"ok": False, "detail": f"Your work is saved here, but couldn't be published: {pushed.stderr.strip()[:300]}", "conflicts": [], "brought": brought, "pushed": False, "review": conflicts}
    return {"ok": True, "detail": f"Up to date. {brought} new events came from teammates.", "conflicts": [], "brought": brought, "pushed": True, "review": conflicts}


def write_git_files_if_missing(path: Path) -> None:
    if not (path / ".gitattributes").exists():
        (path / ".gitattributes").write_text(GIT_ATTRIBUTES, encoding="utf-8", newline="\n")
    if not (path / ".gitignore").exists():
        (path / ".gitignore").write_text(GIT_IGNORE, encoding="utf-8", newline="\n")


def commit_as(path: Path, message: str, person: str) -> None:
    _git(path, "add", "-A")
    if _git(path, "diff", "--cached", "--quiet").returncode != 0:
        _git(path, "commit", "--quiet", "-m", message, person=person)


def clone_project(root: Path, url: str) -> str:
    if not git_available():
        raise SyncError("Git isn't available on this computer.")
    url = _check_url(url)
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / f"_join-{uuid.uuid4().hex[:8]}"
    done = _git(root, "clone", "--quiet", "--", url, temporary.name, timeout=300)
    if done.returncode != 0:
        shutil.rmtree(temporary, ignore_errors=True)
        raise SyncError(f"Couldn't copy that project: {done.stderr.strip()[:300]}")
    if not (temporary / "ledger" / "events.jsonl").exists():
        shutil.rmtree(temporary, ignore_errors=True)
        raise SyncError("That repository isn't a BuildAnything project.")
    try:
        name = str(Ledger(temporary).state().meta.get("name") or "project")
    except LedgerError as exc:
        shutil.rmtree(temporary, ignore_errors=True)
        raise SyncError(f"That project failed its integrity check. {exc}") from exc
    slug = (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "project") + f"-{uuid.uuid4().hex[:6]}"
    temporary.rename(root / slug)
    return slug
