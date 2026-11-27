"""Link issues: five ordered checks. Evidence decides where the problem goes next."""
from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from . import debugging, interfaces as itf
from .gateway import Gateway
from .ledger import Actor, Ledger, LedgerError

CHECKS = itf.CHECK_NAMES
INVESTIGATION_CAP_USD = 0.50
CAUSES = {
    "wrong_address": "Wrong address, port or radio channel",
    "firewall": "Firewall or network settings",
    "weak_signal": "Weak signal",
    "physical_fault": "Physical fault",
    "antenna_placement": "Antenna placement",
    "format_mismatch": "Format or version mismatch",
    "rejected_by_device": "Rejected or not executed by the device",
}
Cause = Literal["wrong_address", "firewall", "weak_signal", "physical_fault", "antenna_placement", "format_mismatch", "rejected_by_device"]


class LinkError(LedgerError):
    pass


def issues(ledger: Ledger) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for event in ledger.events():
        kind, d = event["kind"], event["data"]
        if not kind.startswith("link."):
            continue
        if kind == "link.opened":
            out[d["id"]] = {"id": d["id"], "title": d["title"], "between": d["between"], "packet": d.get("packet"), "trigger": d["trigger"], "status": "open", "checks": [{"name": n, "state": "todo", "evidence": "", "source": ""} for n in CHECKS], "automatic": [], "advice": None, "answers": [], "routes": [], "spent": 0.0, "opened": event["at"]}
            continue
        issue = out.get(d.get("id"))
        if issue is None:
            continue
        if kind == "link.check":
            issue["checks"][d["index"]].update(state=d["result"], evidence=d["evidence"], source=d["source"])
            for later in issue["checks"][d["index"] + 1:]:
                later.update(state="todo", evidence="", source="")
        elif kind == "link.automatic":
            issue["automatic"].append(d["text"])
        elif kind == "link.advice":
            issue["advice"] = d["advice"]
            issue["spent"] = round(issue["spent"] + d["usd"], 6)
        elif kind == "link.answer":
            issue["answers"].append({"text": d["text"], "by": event["actor"]["name"]})
        elif kind == "link.routed":
            issue["routes"].append({k: d.get(k) for k in ("cause", "to", "label", "detail", "debug")})
        elif kind == "link.closed":
            issue["status"] = "closed"
            issue["resolution"] = d["evidence"]
    for issue in out.values():
        issue["current"] = next((i for i, c in enumerate(issue["checks"]) if c["state"] != "pass"), None)
        issue["failed"] = next((i for i, c in enumerate(issue["checks"]) if c["state"] == "fail"), None)
    return out


def _get(ledger: Ledger, issue_id: str) -> dict[str, Any]:
    issue = issues(ledger).get(issue_id)
    if issue is None:
        raise LinkError("Link issue not found.")
    return issue


def record_check(ledger: Ledger, issue_id: str, index: int, result: str, evidence: str, actor: Actor, source: str = "person") -> dict[str, Any]:
    issue = _get(ledger, issue_id)
    if issue["status"] == "closed":
        raise LinkError("This issue is closed.")
    if result not in ("pass", "fail") or not 0 <= index < len(CHECKS):
        raise LinkError("Unknown check or result.")
    if not evidence.strip():
        raise LinkError("Every check needs evidence. Say what you saw.")
    if any(c["state"] != "pass" for c in issue["checks"][:index]):
        raise LinkError("Work through the checks in order. An earlier check hasn't passed yet.")
    ledger.append(actor, "link.check", id=issue_id, index=index, result=result, evidence=evidence.strip(), source=source)
    return _get(ledger, issue_id)


def free_checks(ledger: Ledger, issue_id: str) -> dict[str, Any]:
    """Costs nothing. The results are facts for the investigator, not checks on the real devices."""
    issue = _get(ledger, issue_id)
    name = issue.get("packet")
    state = ledger.state()
    system = Actor.system("link-checks")
    if not name or name not in state.packets:
        ledger.append(system, "link.automatic", id=issue_id, text="No defined packet is linked to this issue, so the format can't be checked.")
        return _get(ledger, issue_id)
    entry = itf.expand(name, state.packets[name])
    stale = itf.drift(ledger.root, state)
    itf.regenerate(ledger.root, state)
    if stale:
        ledger.append(system, "link.automatic", id=issue_id, text=f"These generated files didn't match the definition and were rebuilt, so a device may be running old code: {', '.join(stale)}.")
    else:
        ledger.append(system, "link.automatic", id=issue_id, text="All generated files match the definition.")
    if entry["waiting"]:
        ledger.append(system, "link.automatic", id=issue_id, text=f"{', '.join(entry['waiting'])} hasn't acknowledged version {entry['version']}, so it may be using an older format.")
    if entry.get("standard"):
        return _get(ledger, issue_id)
    report = itf.simulate(ledger, name, count=5)
    if report["ok"]:
        ledger.append(system, "link.automatic", id=issue_id, text=f"Simulator: version {entry['version']} passed all five checks over local UDP, so the definition and generated code work. This doesn't test the real devices or radio.")
    else:
        failed = next(c for c in report["checks"] if c["state"] == "fail")
        ledger.append(system, "link.automatic", id=issue_id, text=f"Simulator failed at \"{failed['name']}\": {failed['evidence']} The generated code or definition is at fault.")
    return _get(ledger, issue_id)


def open_issue(ledger: Ledger, title: str, between: list[str], packet: str | None, trigger: str, actor: Actor) -> dict[str, Any]:
    if not title.strip() or len([b for b in between if b.strip()]) != 2:
        raise LinkError("Describe the problem and name the two devices.")
    if packet and packet not in ledger.state().packets:
        raise LinkError("That packet isn't defined.")
    issue_id = f"lnk_{uuid.uuid4().hex[:10]}"
    ledger.append(actor, "link.opened", id=issue_id, title=title.strip(), between=[b.strip() for b in between], packet=packet or None, trigger=trigger)
    return free_checks(ledger, issue_id)


class Advice(BaseModel):
    check: int = Field(description="Which check to work on, from 1 to 5")
    possible: list[Cause] = Field(description="Most likely first")
    ruled_out: list[Cause]
    instruction: str = Field(description="One concrete test to run")
    look_for: str = Field(description="What will be seen, and what each result means")
    why_person: str = Field(description="Why a person is being asked, such as it being physical, or quicker to watch than to read logs")


async def advise(ledger: Ledger, gateway: Gateway, issue_id: str) -> dict[str, Any]:
    issue = _get(ledger, issue_id)
    if issue["status"] == "closed":
        raise LinkError("This issue is closed.")
    if issue["spent"] >= INVESTIGATION_CAP_USD:
        raise LinkError(f"This issue reached its ${INVESTIGATION_CAP_USD:.2f} investigation limit. Continue by hand, or close it.")
    state = ledger.state()
    packet = itf.expand(issue["packet"], state.packets[issue["packet"]]) if issue.get("packet") in state.packets else None
    summary = {k: packet[k] for k in ("name", "version", "transport", "producer", "consumers", "fixed_size")} if packet else "None linked."
    before = state.spent_usd
    output: Advice = await gateway.run(
        agent="link_debug", output_type=Advice,
        values={"issue": {"title": issue["title"], "between": issue["between"], "trigger": issue["trigger"]}, "packet": summary, "checks": issue["checks"], "automatic_findings": issue["automatic"][-12:], "answers": issue["answers"]},
    )
    ledger.append(Actor.system("link-checks"), "link.advice", id=issue_id, advice=output.model_dump(), usd=round(ledger.state().spent_usd - before, 6))
    return _get(ledger, issue_id)


def add_answer(ledger: Ledger, issue_id: str, text: str, actor: Actor) -> dict[str, Any]:
    if not text.strip():
        raise LinkError("Say what happened.")
    _get(ledger, issue_id)
    ledger.append(actor, "link.answer", id=issue_id, text=text.strip())
    return _get(ledger, issue_id)


def route(ledger: Ledger, issue_id: str, cause: str, device: str, detail: str, actor: Actor) -> dict[str, Any]:
    """The failed check and its cause decide who gets the fix. The issue itself stays open."""
    issue = _get(ledger, issue_id)
    if issue["failed"] is None:
        raise LinkError("Record which check failed, with evidence, before sending this anywhere.")
    if cause not in CAUSES:
        raise LinkError("Unknown cause.")
    if cause in ("wrong_address", "firewall", "rejected_by_device", "physical_fault") and device not in issue["between"]:
        raise LinkError(f"Choose one of: {', '.join(issue['between'])}.")
    label, to, debug_id = CAUSES[cause], "", None
    system = Actor.system("link-checks")
    error = f"Link issue: {issue['title']}. {detail}".strip()
    if cause in ("wrong_address", "rejected_by_device"):
        to = f"Software workspace: {device}"
        debug_id = debugging.open_or_repeat(ledger, f"Link: {issue['title']}", error, "code", system, device=device, where=device)["id"]
    elif cause == "firewall":
        to = f"Setup of {device}"
    elif cause == "weak_signal":
        to = "Stays here until measured"
    elif cause == "physical_fault":
        to = "Electrical Troubleshooting"
        debug_id = debugging.open_or_repeat(ledger, f"Link: {issue['title']}", error, "wiring", system, device=device, where=device)["id"]
    elif cause == "antenna_placement":
        to = "Mechanical"
        from .agents import add_task

        add_task(ledger, system, key=f"LINK-{issue_id[-4:].upper()}", department="Mechanical", title=f"Review antenna placement: {issue['title']}", depends_on=[], start_week=0)
    else:
        to = "Interface"
    ledger.append(actor, "link.routed", id=issue_id, cause=cause, to=to, label=label, detail=detail.strip(), debug=debug_id)
    return _get(ledger, issue_id)


def close(ledger: Ledger, issue_id: str, evidence: str, actor: Actor) -> dict[str, Any]:
    issue = _get(ledger, issue_id)
    if any(c["state"] != "pass" for c in issue["checks"]):
        raise LinkError("This stays open until a full end-to-end transfer succeeds. All five checks must pass.")
    if not evidence.strip():
        raise LinkError("Describe the successful transfer.")
    ledger.append(actor, "link.closed", id=issue_id, evidence=evidence.strip())
    return _get(ledger, issue_id)


def open_for_device(ledger: Ledger, device: str) -> list[dict[str, Any]]:
    return [i for i in issues(ledger).values() if i["status"] == "open" and device in i["between"]]
