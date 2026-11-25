"""How Headquarters and the departments talk: fixed commands one way, fixed reports the other.

A report is data, so what happens next is decided by code. An agent can't declare work done:
the evidence on a report comes from the automatic checks.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from .ledger import Ledger

COMMANDS = ("BRIEF", "TASK", "QUESTION", "CHANGE_NOTICE", "RECHECK", "HOLD", "RESUME")
ReportType = Literal["DONE", "PARTIAL", "BLOCKED", "ANSWER", "REQUEST", "CHANGE_REQUEST", "RISK", "BUG"]
Owner = Literal["Electrical", "Mechanical", "Software", "Procurement", "Person", "Tool"]
MAX_WORDS = 60


class Need(BaseModel):
    owner: Owner = Field(description="Who has to act. Person only for facts only they have, never to approve or confirm something, because the app already shows those. Tool for broken software")
    what: str = Field(description="Exactly what is needed, in one sentence")


class Report(BaseModel):
    type: ReportType = Field(description="DONE: all of it finished. PARTIAL: some finished. BLOCKED: can't continue, and needs says on whom. ANSWER: a reply to a QUESTION. REQUEST: you need another department's work. CHANGE_REQUEST: you raised one. RISK: a safety or design concern. BUG: a tool or generated code is broken")
    done: list[str] = Field(description="What you actually finished, each as a short fact. Empty if nothing")
    not_checked: list[str] = Field(description="What you could not confirm")
    provisional: list[str] = Field(description="Values you chose without proof, so they can be revisited")
    needs: list[Need] = Field(description="What must happen next, and who owns each. Empty if nothing")
    can_give_now: list[str] = Field(default_factory=list, description="For BLOCKED or PARTIAL: what you can deliver today anyway, with cautious stated assumptions in place of what's unknown, such as 'collision shapes for the six links in the CAD, plus a 6 cm cylinder standing in for the missing pen holder'. One unknown never blocks the rest. Empty only if truly nothing can be given")
    risk: str = Field(description="For RISK or BUG: what's wrong. Otherwise empty")
    severity: Literal["none", "low", "medium", "high"] = Field(description="How serious the RISK is. none for every other report")
    safety: bool = Field(description="True if the RISK could hurt someone")
    for_person: str = Field(description="What the person reads: one or two plain sentences, at most 50 words. What changed and the one thing they need to do, if any. No tables, lists or links")
    details: str = Field(default="", description="Tables, sources and reasoning, in Markdown. Shown only if the person opens it. Empty if there's nothing to add")


def command(kind: str, items: list[str] | None = None, done_when: str = "", reason: str = "") -> dict[str, Any]:
    return {"type": kind, "items": items or [], "done_when": done_when, "reason": reason}


def from_reply(reply: Any) -> Report:
    """A plain string only comes from stand-in models in tests."""
    if isinstance(reply, Report):
        return reply
    return Report(type="ANSWER", done=[], not_checked=[], provisional=[], needs=[], can_give_now=[], risk="", severity="none", safety=False, for_person=str(reply))


def shorten(report: Report) -> Report:
    """An agent's word limit is only a request, so code keeps the chat short. Nothing is lost: the rest moves to details."""
    words = report.for_person.split()
    if len(words) <= MAX_WORDS:
        return report
    first = " ".join(re.split(r"(?<=[.!?])\s+", report.for_person.strip())[:2])
    if len(first.split()) > MAX_WORDS:
        first = " ".join(words[:MAX_WORDS]) + "…"
    return report.model_copy(update={"for_person": first, "details": f"{report.for_person}\n\n{report.details}".strip()})


def file_report(ledger: Ledger, actor: Any, department: str, sent: dict[str, Any], report: Report, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return ledger.append(actor, "report.filed", department=department, command=sent["type"], items=sent["items"], evidence=evidence, **report.model_dump())


def reports(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**e["data"], "id": e["id"], "at": e["at"]} for e in events if e["kind"] == "report.filed"]


def latest(events: list[dict[str, Any]], department: str) -> dict[str, Any] | None:
    return next((r for r in reversed(reports(events)) if r["department"] == department), None)


def bug_open(events: list[dict[str, Any]], department: str) -> bool:
    """A reported fault stops the department until it reports something else, or a person says to carry on."""
    for event in reversed(events):
        d = event["data"]
        if event["kind"] == "command.person" and d["department"] == department and d["type"] == "RESUME":
            return False
        if event["kind"] == "report.filed" and d["department"] == department:
            return d["type"] == "BUG"
    return False


def is_held(events: list[dict[str, Any]], department: str) -> bool:
    state = [e["data"]["type"] for e in events if e["kind"] == "command.person" and e["data"]["department"] == department and e["data"]["type"] in ("HOLD", "RESUME")]
    return bool(state) and state[-1] == "HOLD"
