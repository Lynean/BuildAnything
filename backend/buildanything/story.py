"""The project as a story people can watch: who said what to whom, and what moved in and out of the record.

Every step comes from a real ledger event. Nothing here is made up for the animation.
"""
from __future__ import annotations

import re
from typing import Any

from . import gates
from .ledger import Ledger

TEAM = ["You", "Headquarters", "Mechanical", "Electrical", "Software", "Procurement", "External Service", "Internal Service", "Ledger", "Checks"]
AGENT_HOME = {"electrical": "Electrical", "device_facts": "Electrical", "datasheet": "Electrical", "mechanical": "Mechanical", "software": "Software",
              "procurement": "Procurement", "controller": "Headquarters", "research": "Headquarters", "concept": "Headquarters", "verifier": "Headquarters",
              "importer": "Headquarters", "firmware": "Software", "test_planner": "Headquarters"}
LABELS = {"page_note": "finding", "device_fact": "fact", "parameter": "limit", "component": "part", "net": "connection", "requirement": "requirement",
          "task": "task", "test": "test", "claim": "claim", "source": "source", "concept_draft": "concept", "finding": "review note", "need": "need",
          "part_proposal": "part", "assembly_plan": "assemblies", "design_proposal": "screen design", "order": "order", "price": "price", "datasheet": "PDF"}
SKIP = {"spend.recorded", "hq.finished", "memo", "relay.pending", "budget.set"}


def short(text: Any, words: int = 16) -> str:
    """One line for a chat bubble. The full text stays in detail."""
    flat = re.sub(r"\s+", " ", str(text or "")).strip()
    first = re.split(r"(?<=[.!?])\s", flat)[0]
    parts = first.split(" ")
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def _label(kind: str, fields: dict[str, Any]) -> str:
    """One or two words for a chip, such as 'I2C address' or 'datasheet p.24'."""
    if kind == "device_fact":
        return str(fields.get("key", "fact")).replace("_", " ")
    if kind == "page_note":
        return f"finding p.{fields.get('page')}"
    if kind == "component":
        return str(fields.get("part_number", "part"))[:18]
    if kind == "net":
        return str(fields.get("name", "connection"))[:18]
    if kind == "requirement":
        return str(fields.get("key", "requirement"))
    return LABELS.get(kind, kind.replace("_", " "))


def steps(ledger: Ledger, after: int = -1) -> list[dict[str, Any]]:
    events = ledger.events()
    records: dict[str, dict[str, Any]] = {}
    asked: dict[str, str] = {}
    routed: dict[str, str] = {}
    out: list[dict[str, Any]] = []

    def add(i: int, kind: str, frm: str, to: str, text: str = "", label: str = "", detail: str = "", clone: int = 0) -> None:
        repeat = any(s["kind"] == kind and s["from"] == frm and s["to"] == to and s["text"][:60] == text[:60] for s in out[-4:])
        if i > after and not repeat:
            out.append({"i": i, "at": events[i]["at"], "kind": kind, "from": frm, "to": to, "text": text, "label": label, "detail": detail or text, "clone": clone})

    confirmed_run = 0
    for i, e in enumerate(events):
        k, d, actor = e["kind"], e["data"], e["actor"]
        if k == "record.proposed":
            records[d["id"]] = d
        if k != "record.confirmed" and confirmed_run:
            add(i - 1, "hand", "You", "Ledger", f"Confirmed {confirmed_run} {'item' if confirmed_run == 1 else 'items'}", "confirmed")
            confirmed_run = 0
        if k in SKIP:
            continue
        if k == "dept.message":
            role, dept = d.get("role"), d.get("department", "Headquarters")
            if role == "request":
                add(i, "say", d.get("from", "Headquarters"), dept, short(d.get("text")), detail=d.get("text", ""))
            elif role == "agent":
                add(i, "say", dept, "Headquarters" if str(d.get("internal")) == "True" else "You", short(d.get("text")), detail=f"{d.get('text', '')}\n\n{d.get('details', '')}".strip())
            elif role in ("person", "user", "you"):
                add(i, "say", "You", dept, short(d.get("text")), detail=d.get("text", ""))
        elif k == "command.sent":
            items = d.get("items") or []
            first = items[0] if isinstance(items, list) and items else ""
            text = {"BRIEF": "Here's your brief. Start on your part.", "RESUME": "Carry on.", "HOLD": "Hold for now."}.get(d.get("type"), short(first) or d.get("type", "").title())
            add(i, "say", "Headquarters", d.get("department", ""), text, detail=f"{d.get('type')}: {first}\nDone when: {d.get('done_when', '')}")
        elif k == "request.raised":
            asked[d["id"]] = d.get("sender", "Headquarters")
            add(i, "say", d.get("sender", "Headquarters"), "Headquarters" if d.get("sender") != "Headquarters" else d.get("to", ""), short(d.get("question")), detail=f"{d.get('question', '')}\nWhy: {d.get('why', '')}")
        elif k == "request.routed":
            routed[d["id"]] = d.get("to", "")
            if asked.get(d["id"]) != "Headquarters":
                add(i, "say", "Headquarters", d.get("to", ""), f"Over to you: {short(d.get('reason'), 12)}", detail=d.get("reason", ""))
        elif k == "request.answered":
            add(i, "say", routed.get(d["id"], "Headquarters"), asked.get(d["id"], "Headquarters"), short(d.get("answer")), detail=d.get("answer", ""))
        elif k == "report.filed":
            done = d.get("done") or []
            add(i, "say", d.get("department", ""), "Headquarters", f"{str(d.get('type', 'DONE')).title()}: {short(done[0] if done else '', 12)}", detail="\n".join(f"- {x}" for x in done))
        elif k == "job.started":
            if d.get("clone"):
                add(i, "spawn", d["department"], f"{d['department']} clone {d['clone']}", short(d.get("title"), 10), clone=int(d["clone"]))
            else:
                add(i, "status", d.get("department", ""), "", f"Working on: {short(d.get('title'), 10)}")
        elif k == "job.finished":
            if d.get("clone") or any(s.get("kind") == "spawn" and s["text"] == short(d.get("title"), 10) for s in out[-12:]):
                add(i, "despawn", d["department"], "", f"Finished in {d.get('seconds')} s")
        elif k == "record.proposed":
            fields = d.get("fields", {})
            label = _label(d["type"], fields)
            what = short(fields.get("summary") or fields.get("title") or fields.get("text") or fields.get("statement") or fields.get("item") or fields.get("name")
                         or fields.get("finding") or fields.get("value") or fields.get("part_number") or "", 12)
            full = str(fields.get("summary") or fields.get("text") or fields.get("statement") or fields.get("finding") or fields.get("quote") or fields.get("value") or what)
            if actor["kind"] == "human":
                add(i, "hand", "You", "Ledger", f"Added a {LABELS.get(d['type'], d['type'])}: {what}" if what else f"Added a {LABELS.get(d['type'], d['type'])}", label, detail=full)
                continue
            home = AGENT_HOME.get(actor["name"], "Headquarters")
            if d["type"] in ("page_note", "device_fact", "parameter"):
                add(i, "hand", "Ledger", home, f"Read {fields.get('part', '') or 'a document'} p.{fields.get('page', '?')}", f"p.{fields.get('page', '?')}")
            kind_name = LABELS.get(d["type"], d["type"].replace("_", " "))
            said = what if label == kind_name else f"{label}" + (f" ({what})" if what and what != label else "")
            add(i, "hand", home, "Ledger", f"Saved a {kind_name}: {said}" if said else f"Saved a {kind_name}", label, detail=full)
        elif k == "record.confirmed" and actor["kind"] == "human":
            confirmed_run += 1
        elif k == "firmware.generated":
            add(i, "hand", "Ledger", "Software" if d.get("scope") != "lower" else "Electrical", "Took the pins, facts and messages", "pins + facts")
            add(i, "hand", "Software" if d.get("scope") != "lower" else "Electrical", "Ledger", f"Wrote code for {d.get('device')}", "code", detail=", ".join(d.get("files") or []))
        elif k in ("datasheet.read", "facts.read"):
            add(i, "hand", "Ledger", "Electrical", "Read the datasheet" if k == "datasheet.read" else "Read the device facts", "datasheet")
        elif k == "ci.result":
            add(i, "hand", "Checks", "Ledger", f"{d.get('stage', '').replace(':', ' ')} {'passed' if d.get('ok') else 'failed'}", "pass" if d.get("ok") else "fail", detail="; ".join(d.get("problems") or []))
        elif k == "bench.fake_written":
            add(i, "hand", "Ledger", "Checks", "Took the part's documents", "datasheet")
            add(i, "hand", "Checks", "Ledger", f"Built a fake part with {d.get('rules', 0)} rules", "fake part", detail="; ".join(d.get("gaps") or []))
        elif k == "bench.scenarios_written":
            add(i, "hand", "Ledger", "Checks", "Took the requirements", "requirements")
            add(i, "hand", "Checks", "Ledger", f"Wrote {d.get('scenarios', 0)} laptop scenarios", "scenarios")
        elif k == "gate.approved":
            gate = d.get("gate", "")
            add(i, "say", "You", "Headquarters", f"{gates.stage(gate)} is done. Carry on." if gate else "Carry on.")
        elif k == "hq.started":
            add(i, "status", "Headquarters", "", short(d.get("label"), 10))
        elif k == "project.created":
            add(i, "say", "You", "Headquarters", f"I want to build: {short(d.get('aim') or d.get('name'), 12)}", detail=str(d.get("scope", "")))
        elif k == "service.chat":
            service = "External Service" if d.get("side") == "external" else "Internal Service"
            add(i, "say", "You", service, short(d.get("message"), 14), detail=str(d.get("message", "")))
            if d.get("reply"):
                add(i, "say", service, "You", short(d.get("reply"), 14), detail=str(d.get("reply", "")))
        elif k == "tool.requested":
            add(i, "say", d.get("department", "Headquarters"), d.get("service", "Internal Service"), f"I need a tool: {short(d.get('capability'), 8)}", detail=str(d.get("why", "")))
        elif k == "dept.message" and d.get("from") == "External Service":
            add(i, "hand", "External Service", d.get("department", "Headquarters"), short(d.get("text"), 14), "Skill", str(d.get("text", "")))
        elif k == "handoff.created":
            add(i, "hand", d.get("department", "Headquarters"), "You", f"Please run this in {d.get('tool')}: {short(d.get('title'), 8)}", "script", str(d.get("changes", "")))
        elif k == "handoff.reported":
            add(i, "say", "You", "Headquarters", "I ran the script." if d.get("ran") else "I couldn't run the script.", detail=str(d.get("output", "")))
        elif k == "flash.finished":
            add(i, "hand", "Software", "You", f"Board flashed: {short(d.get('outcome'), 8)}", "firmware")
    if confirmed_run:
        add(len(events) - 1, "hand", "You", "Ledger", f"Confirmed {confirmed_run} {'item' if confirmed_run == 1 else 'items'}", "confirmed")
    return out
