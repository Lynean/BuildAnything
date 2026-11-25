"""Project-wide views: what needs a person, the task timeline, sources, and the state of each team."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from . import changes, connectors as conn, traceability, datasheets, firmware, gates, headquarters as hq, interfaces as itf, links, mechanical as mech
from .citations import Paper, normalize
from .ledger import Actor, Ledger, LedgerError, ProjectState

MAX_PDF_BYTES = 40 * 1024 * 1024
TIERS = ("Peer-reviewed", "Preprint", "Standard", "Vendor document", "Textbook", "Web or other")
INLINE = {"claim": "Sourced claim", "requirement": "Requirement", "task": "Task", "component": "Component", "interface": "Interface"}
ELSEWHERE = {"parameter": ("datasheet limits to confirm", "Electrical"), "net": ("connections to confirm", "Electrical"), "imported_item": ("imported items to review", "Import project")}
MAX_EXCERPT = 8000


class OverviewError(LedgerError):
    pass


def _title(record: dict[str, Any]) -> tuple[str, str]:
    f = record["fields"]
    kind = record["type"]
    if kind == "claim":
        return f["statement"], f"Quote: {f['quote']}"
    if kind == "requirement":
        origin = f"You said: \"{f['person_said']}\". " if f.get("person_said") else f"Added by agents: {f['added_because']} " if f.get("added_because") else ""
        return f["text"], f"{origin}Tested by: {f.get('verification', 'not set')}"
    if kind == "task":
        return f["title"], f"{f['department']}, {f['weeks']} weeks"
    if kind == "component":
        return f["part_number"], f.get("role", "")
    return f.get("name", record["id"]), f"{f.get('agreed', '')} ({f.get('between', '')})"


def review_queue(ledger: Ledger) -> list[dict[str, Any]]:
    """Everything waiting on a person. Items open where they can be judged properly."""
    state = ledger.state()
    items: list[dict[str, Any]] = []
    for need in hq.needs(ledger):
        if need["kind"] == "gate":
            items.append({"id": need["gate"], "kind": "gate", "type": "Gate", "title": need["text"], "body": "", "gate": need["gate"]})
        elif need["kind"] in ("concept", "no_sources", "failed"):
            items.append({"id": need["kind"], "kind": "open", "type": "Headquarters", "title": need["text"], "body": "", "screen": "Headquarters"})
    tests = traceability.view(ledger)
    waiting_tests = sum(1 for t in tests["tests"] if t["status"] == "proposed")
    if waiting_tests:
        items.append({"id": "tests", "kind": "open", "type": "Tests", "title": f"{waiting_tests} tests to approve", "body": "Check they really prove the requirements.", "screen": "Tests"})
    for test in tests["tests"]:
        if test["state"] in ("failed", "retest"):
            items.append({"id": test["id"], "kind": "open", "type": "Test", "title": f"{test['key']} {'failed' if test['state'] == 'failed' else 'needs running again'}: {test['title']}", "body": "", "screen": "Tests"})
    for request in hq.requests_view(ledger.events()):
        if request["status"] == "waiting_for_you":
            items.append({"id": request["id"], "kind": "question", "type": f"Question from {request['from']}", "title": request["question"], "body": request.get("why", "")})
    for change in changes.view(ledger):
        if change["status"] == "open" and change["review"] and (change["byKind"] != "agent" or change["triaged"]):
            items.append({"id": change["id"], "kind": "open", "type": "Change Request", "title": change["title"], "body": " ".join(change["escalation"]) or f"{len(change['impact'])} things affected.", "screen": "Changes"})
    for finding in gates.open_high_findings(state):
        items.append({"id": finding["id"], "kind": "open", "type": "Safety finding" if finding["fields"].get("safety") else "Verifier finding", "title": finding["fields"]["text"], "body": "", "screen": "Headquarters"})
    for name, entry in state.packets.items():
        for consumer in entry["packet"]["consumers"]:
            if consumer not in entry["acks"]:
                items.append({"id": f"{name}:{consumer}", "kind": "ack", "type": "Interface change", "title": f"{consumer} must acknowledge {name} v{entry['major']}.{entry['minor']}", "body": "", "packet": name, "consumer": consumer})
    for record_type, label in INLINE.items():
        for record in state.of_type(record_type, "proposed"):
            title, body = _title(record)
            items.append({"id": record["id"], "kind": "record", "type": label, "title": title, "body": body})
    for record in state.of_type("part_proposal", "proposed"):
        f = record["fields"]
        items.append({"id": record["id"], "kind": "part", "type": "Suggested part", "title": f"{f['number']}: {f['name']}", "body": f"{f['material']}, {f['process']}, quantity {f['qty']}. {f.get('why', '')}"})
    for record_type, (label, screen) in ELSEWHERE.items():
        count = len(state.of_type(record_type, "proposed"))
        if count:
            items.append({"id": record_type, "kind": "open", "type": screen, "title": f"{count} {label}", "body": "", "screen": screen})
    return items


def task_view(ledger: Ledger) -> list[dict[str, Any]]:
    state = ledger.state()
    tasks = [t for t in state.of_type("task") if t["status"] in ("proposed", "confirmed")]
    by_key = {t["fields"]["key"]: t for t in tasks}
    out = []
    for t in sorted(tasks, key=lambda x: x["fields"]["start_week"]):
        waiting = [k for k in t["fields"].get("depends_on", []) if k in by_key and not by_key[k].get("done")]
        state_name = "Done" if t.get("done") else "Needs review" if t["status"] == "proposed" else f"Waiting: {', '.join(waiting)}" if waiting else "Ready"
        out.append({"id": t["id"], "key": t["fields"]["key"], "title": t["fields"]["title"], "department": t["fields"]["department"], "start": t["fields"]["start_week"], "weeks": t["fields"]["weeks"], "state": state_name, "done": bool(t.get("done"))})
    return out


def mark_task_done(ledger: Ledger, task_id: str, done: bool, actor: Actor) -> None:
    record = ledger.state().records.get(task_id)
    if not record or record["type"] != "task" or record["status"] != "confirmed":
        raise OverviewError("Only confirmed tasks can be marked done.")
    ledger.append(actor, "task.done", id=task_id, done=done)


def upload_source(ledger: Ledger, filename: str, tier: str, data: bytes, actor: Actor) -> str:
    if tier not in TIERS:
        raise OverviewError("Choose what kind of source this is.")
    if len(data) > MAX_PDF_BYTES:
        raise OverviewError("That PDF is too large.")
    if not data.startswith(b"%PDF-"):
        raise OverviewError("That file isn't a PDF.")
    digest = hashlib.sha256(data).hexdigest()[:12]
    folder = ledger.root / "sources"
    folder.mkdir(exist_ok=True)
    target = folder / f"{digest}.pdf"
    target.write_bytes(data)
    try:
        pages = datasheets.page_texts(target)
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise OverviewError("That PDF couldn't be read.") from exc
    record = ledger.propose(actor, "source", ref=digest, title=re.sub(r"\.pdf$", "", Path(filename).name, flags=re.I)[:200], url=None, tier=tier, origin="upload", file=target.name, pages=len(pages), has_text=any(p.strip() for p in pages))
    ledger.append(actor, "record.confirmed", id=record)
    return record


def source_view(ledger: Ledger, project_id: str) -> dict[str, Any]:
    state = ledger.state()
    claims = [c for c in state.of_type("claim") if c["status"] != "rejected"]
    sources = []
    for s in state.of_type("source"):
        if s["status"] == "rejected":
            continue
        f = s["fields"]
        url = f"/api/projects/{project_id}/sources/{s['id']}/file" if f.get("origin") == "upload" else f.get("url")
        sources.append({"id": s["id"], "title": f["title"], "tier": f.get("tier", ""), "origin": f.get("origin", ""), "url": url, "hasText": f.get("has_text", True), "pages": f.get("pages"), "claims": [{"statement": c["fields"]["statement"], "status": c["status"]} for c in claims if c["fields"].get("source") == s["id"]]})
    return {"sources": sources, "decisions": [{"id": d["id"], "text": d["fields"]["text"]} for d in state.of_type("decision", "confirmed")], "tiers": TIERS}


class ProjectIndex:
    """Searches sources the person uploaded as well as the outside index, including when that's unreachable."""

    def __init__(self, ledger: Ledger, outside: Any):
        self.ledger, self.outside = ledger, outside

    def _uploads(self) -> list[tuple[dict[str, Any], list[str]]]:
        out = []
        for source in self.ledger.state().of_type("source", "confirmed"):
            f = source["fields"]
            if f.get("origin") == "upload" and f.get("has_text"):
                out.append((source, datasheets.page_texts(self.ledger.root / "sources" / f["file"])))
        return out

    def search(self, query: str) -> list[Paper]:
        words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2]
        found = []
        for source, pages in self._uploads():
            ranked = sorted(((sum(normalize(p).count(w) for w in words), i) for i, p in enumerate(pages)), reverse=True)
            if ranked and ranked[0][0] > 0:
                text = "\n".join(pages[i] for score, i in ranked[:4] if score)[:MAX_EXCERPT]
                found.append(Paper(source["fields"]["ref"], source["fields"]["title"], text, ""))
        try:
            return found + self.outside.search(query)
        except (OSError, ValueError):
            if found:
                return found
            raise

    def get(self, paper_id: str) -> Paper | None:
        for source, pages in self._uploads():
            if source["fields"]["ref"] == paper_id:
                return Paper(paper_id, source["fields"]["title"], "\n".join(pages), "")
        return self.outside.get(paper_id)


def _status(state: ProjectState, ledger: Ledger, registry: conn.Registry | None) -> dict[str, str]:
    drafts, approved = state.of_type("concept_draft"), hq.concept_approved(ledger)
    claims = len(state.of_type("claim", "confirmed"))
    plan = [r for r in state.of_type("requirement") + state.of_type("task") if r["status"] in ("proposed", "confirmed")]
    high = len(gates.open_high_findings(state))
    devices = firmware.devices(ledger.root)
    git = ledger.root / ".git"
    connector = False  # no CAD program is connected to the app
    return {
        "you": f"{len(review_queue(ledger))} items waiting",
        "concept": "Concept approved" if approved else "Drafting" if drafts else "Not started",
        "research": f"{claims} confirmed claims",
        "verifier": f"{high} serious problems open" if high else "No serious problems open" if state.of_type("finding") else "Not run",
        "controller": f"{len(plan)} requirements and tasks",
        "ledger": f"{state.event_count} events recorded",
        "mech": f"{len(state.of_type('part', 'confirmed'))} parts",
        "elec": f"{len(state.of_type('component', 'confirmed'))} confirmed components",
        "soft": f"{len(devices)} device workspaces",
        "proc": f"{len(state.of_type('order'))} orders",
        "cad": "Connected" if connector else "Not connected",
        "fw": "PlatformIO available" if conn.detect_cli("pio")["found"] else "Not connected",
        "git": "Version history on" if git.exists() else "Not set up",
        "supply": "Not connected",
    }


def summary(ledger: Ledger, registry: conn.Registry | None = None) -> dict[str, Any]:
    state = ledger.state()
    roll = mech.rollup(ledger, state)
    current = gates.current_gate(state)
    return {
        "name": state.meta.get("name"), "gate": f"{current} {gates.stage(current, state)}" if current else "All gates complete", "spent": state.spent_usd, "limit": state.limit_usd,
        "massG": roll["massG"], "massLimit": roll["budget"].get("mass_g"), "cost": roll["cost"], "costLimit": roll["budget"].get("cost"),
        "blocked": sum(1 for t in task_view(ledger) if t["state"].startswith("Waiting")), "linkIssues": sum(1 for i in links.issues(ledger).values() if i["status"] == "open"),
        "review": len(review_queue(ledger)), "status": _status(state, ledger, registry), "packets": len(itf.packets(state)),
    }
