"""Change Requests: see everything a change affects before it happens, and let only a person decide.

Impact comes from code following the project's links. An independent agent then looks for what code can't see.
"""
from __future__ import annotations

from . import shape

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from . import memo, depgraph, mechanical as mech, traceability
from .gateway import Gateway
from .ledger import Actor, Ledger, LedgerError, ProjectState

KINDS = ("part", "component", "requirement", "add_requirement", "drop_requirement", "deviation", "packet", "middleware", "other")
NEEDS_A_REASON = ("drop_requirement", "deviation")
PART_FIELDS = ("material", "process", "qty", "mass_g", "unit_cost", "remove")  # remove: true takes the part off the list
DEPARTMENTS = ("Electrical", "Mechanical", "Software", "Procurement", "Simulation")
SYSTEM = Actor.system("headquarters")


class ChangeError(LedgerError):
    pass


def _fold(ledger: Ledger) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for event in ledger.events():
        d = event["data"]
        if event["kind"] == "change.proposed":
            out[d["id"]] = {**d, "kind": d["type"], "status": "open", "review": None, "decision": None, "by": event["actor"]["name"], "byKind": event["actor"]["kind"], "at": event["at"], "triaged": False, "escalation": []}
        elif event["kind"] == "change.reviewed" and d["id"] in out:
            out[d["id"]]["review"] = d["review"]
        elif event["kind"] in ("change.approved", "change.rejected", "change.auto_approved", "change.auto_rejected") and d["id"] in out:
            out[d["id"]].update(status="approved" if event["kind"].endswith("approved") else "rejected", triaged=True, decision={"reason": d["reason"], "by": "Headquarters" if "auto" in event["kind"] else event["actor"]["name"], "auto": "auto" in event["kind"], "impact": d.get("impact"), "followUps": d.get("followUps", [])})
        elif event["kind"] == "change.escalated" and d["id"] in out:
            out[d["id"]].update(triaged=True, escalation=d["reasons"])
        elif event["kind"] == "change.reopened" and d["id"] in out:
            out[d["id"]].update(status="open", decision=None, triaged=True, escalation=["You reopened this after Headquarters declined it."])
    return out


def _target_label(state: ProjectState, kind: str, target: str) -> str:
    record = state.records.get(target)
    if kind == "component" and record:
        return record["fields"]["part_number"]
    if kind in ("requirement", "drop_requirement", "deviation") and record:
        return f"{record['fields'].get('key', '')}: {record['fields']['text']}"
    return target or "The whole project"


def _item(department: str, item: str, why: str, action: str) -> dict[str, str]:
    return {"department": department, "item": item, "why": why, "action": action}


def _part_impact(ledger: Ledger, state: ProjectState, target: str, details: dict[str, Any]) -> list[dict[str, str]]:
    revisions = mech.parts_by_number(state).get(target)
    if not revisions:
        return []
    latest = revisions[-1]["fields"]
    if details.get("remove") in (True, "true", "yes"):
        return [_item("Mechanical", f"Part {target}", "Taken off the parts list", "Remove it from assemblies and CAD plans"),
                _item("Procurement", f"Buying {target}", "No longer needed", "Take it off the buying list")]
    out, seen = [], set()
    for field, value in details.items():
        if field not in mech.FIELD_CHECKS or latest.get(field) == value:
            continue
        for check in mech.FIELD_CHECKS[field]:
            if check not in seen:
                seen.add(check)
                out.append(_item("Mechanical", f"{check} check on {target}", f"{field} changes from {latest.get(field)} to {value}", f"Redo the {check.lower()} check"))
    for release in mech.releases(ledger):
        if any(p["id"] == revisions[-1]["id"] for p in release["parts"]):
            out.append(_item("Mechanical", f"Release {release['id']}", f"Already sent to {release.get('recipient', 'someone')} with the old revision", "Tell the recipient, and issue a new release"))
    roll = mech.rollup(ledger, state)
    if details.get("mass_g") is not None:
        delta = (float(details["mass_g"]) - float(latest.get("mass_g") or 0)) * int(details.get("qty") or latest.get("qty") or 1)
        limit = roll["budget"].get("mass_g")
        over = f" That would take the total to {roll['massG'] + delta:g} g, over the {limit:g} g budget." if limit and roll["massG"] + delta > limit else ""
        out.append(_item("Mechanical", "Mass budget", f"Total mass changes by {delta:+g} g.{over}", "Update the mass budget"))
    if "unit_cost" in details or "material" in details or "process" in details:
        out.append(_item("Procurement", f"Cost and supply of {target}", "The part's cost or how it's made is changing", "Update the buying list"))
    return out


def _component_impact(ledger: Ledger, state: ProjectState, target: str, details: dict[str, Any]) -> list[dict[str, str]]:
    record = state.records.get(target)
    if not record:
        return []
    number = record["fields"]["part_number"]
    out = []
    for net in state.of_type("net"):
        if net["status"] not in ("rejected", "superseded") and any(m["component"] == target for m in net["fields"]["members"]):
            out.append(_item("Electrical", f"Connection {net['fields']['name']}", f"Uses {number}", "Redraft and recheck this connection"))
    new = details.get("part_number", "the replacement")
    out.append(_item("Electrical", f"Datasheet for {new}", "Limits and compatibility come from the datasheet", "Upload the datasheet and confirm its limits"))
    for label in depgraph.build(ledger).dependents(number)["code"]:
        out.append(_item("Software", label, f"This code relies on {number}", "Review this code against the new part"))
    out.append(_item("Mechanical", f"Fit of {new}", "A different part can have a different size, mass or mounting", "Check the fit and mounting"))
    for order in state.of_type("order"):
        if number.lower() in str(order["fields"].get("items", "")).lower():
            out.append(_item("Procurement", f"Order from {order['fields'].get('supplier') or 'unknown supplier'}", f"Includes {number}", "Check whether it can be cancelled or is still needed"))
    out.append(_item("Procurement", f"Buy {new}", "It replaces an existing part", "Add it to the buying list"))
    return out


def _requirement_impact(state: ProjectState, target: str) -> list[dict[str, str]]:
    record = state.records.get(target)
    if not record:
        return []
    key = record["fields"].get("key", "")
    out = [_item("Headquarters", f"Test for {key}", f"Currently tested by: {record['fields'].get('verification', 'not set')}", "Confirm the test still proves the new wording")]
    for finding in state.of_type("finding"):
        if key and key in finding["fields"].get("related", []) and finding["status"] != "proposed":
            out.append(_item("Headquarters", f"Decision on: {finding['fields']['text'][:90]}", "That decision was about the old requirement", "Check it still holds"))
    for department in [d for d in shape.team(state) if d != "Procurement"]:
        if any(t["fields"]["department"] == department and t["status"] == "confirmed" for t in state.of_type("task")):
            out.append(_item(department, "Tasks based on this requirement", "The department planned against the old wording", "Check its tasks still fit"))
    return out


def _packet_impact(root: Any, state: ProjectState, target: str) -> list[dict[str, str]]:
    entry = state.packets.get(target)
    if not entry:
        return []
    packet = entry["packet"]
    out = [_item("Software", f"{device} using {target}", "Uses this packet's generated code", "Update and retest") for device in [packet["producer"], *packet["consumers"]]]
    out.append(_item("Electrical", f"Definition of {target}", "A breaking change must be acknowledged again by every device", "Update the definition on the Interface screen"))
    return out


def compute_impact(ledger: Ledger, change: dict[str, Any]) -> list[dict[str, str]]:
    state = ledger.state()
    kind, target, details = change["kind"], change.get("target", ""), change.get("details") or {}
    record = state.records.get(target) or {"fields": {}}
    key = record["fields"].get("key", "")
    name = {"part": target, "component": record["fields"].get("part_number", ""), "requirement": key, "drop_requirement": key, "deviation": key}.get(kind, "")
    retests = [_item(t["owner"], f"Test {t['key']}: {t['title']}", "It passed against the current design", "Run it again after this change") for t in traceability.affected_tests(ledger, name)]
    if kind == "part":
        return _part_impact(ledger, state, target, details) + retests
    if kind == "component":
        return _component_impact(ledger, state, target, details) + retests
    if kind == "requirement":
        return _requirement_impact(state, target) + retests
    if kind == "add_requirement":
        from . import shape

        return [_item(d, "New requirement", str(details.get("text", ""))[:200], "Plan for it, and a test will be written to prove it") for d in shape.team(state)]
    if kind in NEEDS_A_REASON:
        tests = [t for t in traceability.view(ledger)["tests"] if key in t["covers"]]
        verb = "No longer needed for this requirement" if kind == "drop_requirement" else "Its result no longer blocks the Prove stage"
        return _requirement_impact(state, target) + [_item(t["owner"], f"Test {t['key']}: {t['title']}", f"It proves {key}", verb) for t in tests]
    if kind == "packet":
        return _packet_impact(ledger.root, state, target)
    if kind == "middleware":
        return [_item("Software", target.partition(":")[2], "The app is built on this header", "Update the app with write_code once Electrical changes it")]
    return [_item(d, "Department plan", "A change that doesn't map to one record", "Check what this means for your work") for d in DEPARTMENTS]


def propose(ledger: Ledger, title: str, why: str, kind: str, target: str, details: dict[str, Any], actor: Actor) -> str:
    state = ledger.state()
    if not title.strip() or len(why.strip()) < 8:
        raise ChangeError("Give the change a title and say why it's needed.")
    if kind not in KINDS:
        raise ChangeError("Unknown kind of change.")
    details = {k: v for k, v in (details or {}).items() if v not in (None, "")}
    record = state.records.get(target)
    if kind == "part":
        details = {k: v for k, v in details.items() if k in PART_FIELDS}
        if target not in mech.parts_by_number(state) or not details:
            raise ChangeError("Choose a part and say what changes about it.")
    elif kind == "component":
        if not record or record["type"] != "component" or not str(details.get("part_number", "")).strip():
            raise ChangeError("Choose a component and name its replacement.")
    elif kind == "add_requirement":
        if not str(details.get("text", "")).strip():
            raise ChangeError("Write the new requirement.")
    elif kind == "requirement":
        if not record or record["type"] != "requirement" or not str(details.get("text", "")).strip():
            raise ChangeError("Choose a requirement and write its new wording.")
    elif kind == "drop_requirement":
        if not record or record["type"] != "requirement" or record["status"] != "confirmed":
            raise ChangeError("Choose the requirement to drop.")
    elif kind == "deviation":
        if not record or record["type"] != "requirement" or record["status"] != "confirmed":
            raise ChangeError("Choose the requirement this deviation is against.")
        if not str(details.get("what", "")).strip() or not str(details.get("restores", "")).strip():
            raise ChangeError("Say what isn't met, and what would put it right.")
    elif kind == "packet" and target not in state.packets:
        raise ChangeError("Choose a packet.")
    elif kind == "middleware":
        device, _, path = target.partition(":")
        if not path.startswith("src/mid/") or not path.endswith(".h") or not (ledger.root / "firmware" / device / path).is_file():
            raise ChangeError("Name the middleware header as device:src/mid/name.h.")
        if not str(details.get("change", "")).strip():
            raise ChangeError("Say what changes in the header, in details as change.")
    change_id = f"chg_{uuid.uuid4().hex[:10]}"
    number = sum(1 for e in ledger.events() if e["kind"] == "change.proposed") + 1
    ledger.append(actor, "change.proposed", id=change_id, number=number, title=title.strip(), why=why.strip(), type=kind, target=target, details=details)
    return change_id


class MissedImpact(BaseModel):
    department: Literal["Electrical", "Mechanical", "Software", "Procurement", "Simulation", "Headquarters"]
    item: str
    why: str
    action: str


class ChangeReview(BaseModel):
    recommendation: Literal["approve", "caution"]
    summary: str = Field(description="Two sentences at most, in plain language")
    risks: list[str]
    missed: list[MissedImpact] = Field(description="Things the listed impact doesn't include. Empty if it looks complete")


async def review(ledger: Ledger, gateway: Gateway, change_id: str) -> None:
    change = _fold(ledger)[change_id]
    state = ledger.state()
    output: ChangeReview = await gateway.run(
        agent="change_reviewer", output_type=ChangeReview,
        values={"project": state.meta, "change": {k: change[k] for k in ("title", "why", "kind", "details")} | {"target": _target_label(state, change["kind"], change.get("target", ""))}, "computed_impact": compute_impact(ledger, change),
                "requirements": [r["fields"] for r in state.of_type("requirement", "confirmed")], "decisions": [d["fields"]["text"] for d in state.of_type("decision", "confirmed")]},
    )
    ledger.append(SYSTEM, "change.reviewed", id=change_id, review=output.model_dump())


def view(ledger: Ledger) -> list[dict[str, Any]]:
    return memo.remembered(ledger, "changes.view", lambda: _view(ledger))


def _view(ledger: Ledger) -> list[dict[str, Any]]:
    state = ledger.state()
    out = []
    for change in _fold(ledger).values():
        impact = change["decision"]["impact"] if change["status"] == "approved" else compute_impact(ledger, change)
        out.append({**change, "targetLabel": _target_label(state, change["kind"], change.get("target", "")), "impact": impact})
    return sorted(out, key=lambda c: -c["number"])


def options(state: ProjectState) -> dict[str, Any]:
    requirement_choices = [{"id": r["id"], "label": f"{r['fields'].get('key', '')}: {r['fields']['text'][:70]}"} for r in state.of_type("requirement", "confirmed")]
    return {
        "part": [{"id": n, "label": f"{n}: {r[-1]['fields']['name']} (rev {r[-1]['fields']['revision']})"} for n, r in mech.parts_by_number(state).items()],
        "component": [{"id": c["id"], "label": c["fields"]["part_number"]} for c in state.of_type("component", "confirmed")],
        "requirement": requirement_choices,
        "drop_requirement": requirement_choices,
        "deviation": requirement_choices,
        "packet": [{"id": n, "label": n} for n in state.packets],
    }


def waiting_review(ledger: Ledger) -> list[str]:
    return [c["id"] for c in _fold(ledger).values() if c["status"] == "open" and (not c["review"] or (c["byKind"] == "agent" and not c["triaged"]))]


class Route(BaseModel):
    action: Literal["forward", "answer_from_record", "decline", "ask_person", "reword"]
    handler: Literal["Electrical", "Mechanical", "Software", "Procurement", "Simulation", "None"] = Field(description="Who should handle it. None when declining, or when asking the person")
    reason: str = Field(description="One plain sentence the requester will see")
    earlier_id: str | None = Field(description="For answer_from_record: the ID of the earlier request that already answers this")


ROLES = {
    "Electrical": "Components, datasheets, pins, power, connections, circuits and low-level firmware.",
    "Mechanical": "Parts, structure, mounting, fit, materials, CAD and fabrication.",
    "Software": "High-level code, computer programs, user interfaces and open-source choices.",
    "Procurement": "Buying lists, suppliers, orders and costs.",
    "Simulation": "Simulations of the models and code, their runs and results: physics, circuits, structures and other analyses.",
}
ROUTINE_KINDS = ("part",)


def must_ask_person(ledger: Ledger, change: dict[str, Any], review: dict[str, Any], impact: list[dict[str, str]]) -> list[str]:
    """Written in code on purpose: no model can decide these don't need a person."""
    reasons = []
    if change["kind"] not in ROUTINE_KINDS:
        reasons.append({"drop_requirement": "It removes a requirement.", "deviation": "It accepts that a requirement won't be met.", "component": "It replaces a component, which undoes agreed connections.", "requirement": "It changes a requirement.", "packet": "It changes an interface between devices.", "middleware": "It changes a middleware header that other code is built on.", "other": "It doesn't map to one clear record."}.get(change["kind"], "It changes something agreed."))
    if review["recommendation"] != "approve" or review["risks"]:
        reasons.append("The independent reviewer raised a concern.")
    if review["missed"]:
        reasons.append("The reviewer found effects the app couldn't trace.")
    if any(i["item"].startswith("Release ") for i in impact):
        reasons.append("A version of this part has already been sent to someone.")
    if any("over the" in i["why"] and "budget" in i["why"] for i in impact):
        reasons.append("It would go over a budget.")
    return reasons


def _duplicate_of(ledger: Ledger, change: dict[str, Any]) -> dict[str, Any] | None:
    for other in _fold(ledger).values():
        if other["id"] != change["id"] and other["number"] < change["number"] and other["status"] != "approved" and (other["kind"], other.get("target"), other.get("details")) == (change["kind"], change.get("target"), change.get("details")):
            return other
    return None


async def triage(ledger: Ledger, gateway: Gateway, change_id: str) -> None:
    """Headquarters settles routine requests from agents, and passes on the ones that need a person."""
    change = _fold(ledger)[change_id]
    if change["byKind"] != "agent" or change["status"] != "open" or change["triaged"]:
        return
    copy = _duplicate_of(ledger, change)
    if copy:
        ledger.append(SYSTEM, "change.auto_rejected", id=change_id, reason=f"This repeats change {copy['number']}, which is {copy['status'] if copy['status'] == 'rejected' else 'still open'}.")
        return
    impact = compute_impact(ledger, change)
    reasons = must_ask_person(ledger, change, change["review"], impact)
    if reasons:
        ledger.append(SYSTEM, "change.escalated", id=change_id, reasons=reasons)
        return
    state = ledger.state()
    route: Route = await gateway.run(agent="router", output_type=Route, values={"project": {"aim": state.meta.get("aim"), "scope": state.meta.get("scope")}, "kind": "change_request", "item": {k: change[k] for k in ("title", "why", "kind", "details")} | {"impact": impact}, "suggested": "", "departments": ROLES, "recent_requests": [], "decisions": [d["fields"]["text"] for d in state.of_type("decision", "confirmed")]})
    if route.action == "forward":
        impact = compute_impact(ledger, change)
        ledger.append(SYSTEM, "change.auto_approved", id=change_id, reason=route.reason, impact=impact)
        actor = Actor.delegated()
        _apply(ledger, change, actor)
        ledger.append(SYSTEM, "change.followups", id=change_id, tasks=_follow_ups(ledger, change, impact, actor))
    elif route.action == "decline":
        ledger.append(SYSTEM, "change.auto_rejected", id=change_id, reason=route.reason)
    else:
        ledger.append(SYSTEM, "change.escalated", id=change_id, reasons=[route.reason])


def reopen(ledger: Ledger, change_id: str, actor: Actor) -> None:
    change = _fold(ledger).get(change_id)
    if not change or change["status"] != "rejected" or not change["decision"].get("auto"):
        raise ChangeError("Only a change Headquarters declined can be reopened.")
    ledger.append(actor, "change.reopened", id=change_id)


def _apply(ledger: Ledger, change: dict[str, Any], actor: Actor) -> None:
    kind, target, details = change["kind"], change.get("target", ""), change.get("details") or {}
    state = ledger.state()
    if kind == "part" and details.get("remove") in (True, "true", "yes"):
        for revision in mech.parts_by_number(state).get(target, []):  # nothing current is left, so no gate asks for its model or price
            ledger.append(actor, "record.rejected", id=revision["id"])
    elif kind == "part":
        mech.revise(ledger, target, details, f"Change {change['number']}: {change['why']}", actor)
    elif kind == "component":
        old = state.records[target]
        new = ledger.propose(actor, "component", part_number=str(details["part_number"]).strip(), role=old["fields"].get("role", ""), replaces=target)
        ledger.append(actor, "record.confirmed", id=new)
        ledger.append(actor, "record.rejected", id=target)
        for net in state.of_type("net"):
            if net["status"] not in ("rejected", "superseded") and any(m["component"] == target for m in net["fields"]["members"]):
                ledger.append(SYSTEM, "record.superseded", id=net["id"])
    elif kind == "requirement":
        old = state.records[target]
        new = ledger.propose(actor, "requirement", key=old["fields"].get("key", ""), text=str(details["text"]).strip(), verification=str(details.get("verification") or old["fields"].get("verification", "")), replaces=target)
        ledger.append(actor, "record.confirmed", id=new)
        ledger.append(SYSTEM, "record.superseded", id=target)
    elif kind == "add_requirement":
        keys = [r["fields"].get("key", "") for r in state.of_type("requirement")]
        numbers = [int(k[1:]) for k in keys if k[:1] == "R" and k[1:].isdigit()]
        new = ledger.propose(actor, "requirement", key=f"R{max(numbers, default=0) + 1}", text=str(details["text"]).strip(),
                             verification=str(details.get("verification", "")).strip(), person_said=str(details.get("person_said") or details["text"]).strip())
        ledger.append(actor, "record.confirmed", id=new)
    elif kind == "drop_requirement":
        old = state.records[target]
        ledger.append(SYSTEM, "requirement.dropped", id=target, key=old["fields"].get("key", ""), text=old["fields"]["text"], why=change["why"], change=change["number"])
        ledger.append(SYSTEM, "record.superseded", id=target)
        for test in state.of_type("test"):
            if test["status"] != "rejected" and test["fields"]["covers"] == [old["fields"].get("key", "")]:
                ledger.append(actor, "record.rejected", id=test["id"])
    elif kind == "deviation":
        old = state.records[target]
        ledger.append(SYSTEM, "deviation.accepted", id=f"dev_{change['number']}", key=old["fields"].get("key", ""), text=old["fields"]["text"], what=str(details["what"]).strip(), why=change["why"], restores=str(details["restores"]).strip(), change=change["number"], by=actor.name)


def _follow_ups(ledger: Ledger, change: dict[str, Any], impact: list[dict[str, str]], actor: Actor) -> list[dict[str, Any]]:
    state = ledger.state()
    tasks = [t for t in state.of_type("task") if t["status"] == "confirmed"]
    start = max((t["fields"].get("start_week", 0) + t["fields"].get("weeks", 1) for t in tasks), default=0)
    out = []
    for department in shape.team(state):  # a department not on the team gets no tasks; adding it later brings its work back
        items = [i for i in impact if i["department"] == department]
        if items:
            key = f"CR{change['number']}-{department[:4].upper()}"
            title = f"Change {change['number']}, {change['title']}: {'; '.join(i['action'] for i in items[:3])}" + (f", and {len(items) - 3} more" if len(items) > 3 else "")
            record = ledger.propose(actor, "task", key=key, department=department, title=title[:200], weeks=1, depends_on=[], start_week=start, change=change["id"])
            ledger.append(actor, "record.confirmed", id=record)
            out.append({"department": department, "key": key})
    return out


def approve(ledger: Ledger, change_id: str, reason: str, actor: Actor) -> dict[str, Any]:
    change = _fold(ledger).get(change_id)
    if not change or change["status"] != "open":
        raise ChangeError("That change is no longer open.")
    if not change["review"]:
        raise ChangeError("The independent review hasn't finished yet.")
    if change["review"]["recommendation"] == "caution" and len(reason.strip()) < 8:
        raise ChangeError("The reviewer advised caution. Say why you're going ahead.")
    if change["kind"] in NEEDS_A_REASON and len(reason.strip()) < 8:
        raise ChangeError("Say why you're accepting this, so the record explains it later.")
    impact = compute_impact(ledger, change) + change["review"]["missed"]
    ledger.append(actor, "change.approved", id=change_id, reason=reason.strip(), impact=impact)
    _apply(ledger, change, actor)
    followups = _follow_ups(ledger, change, impact, actor)
    ledger.append(SYSTEM, "change.followups", id=change_id, tasks=followups)
    return {"followUps": followups}


def reject(ledger: Ledger, change_id: str, reason: str, actor: Actor) -> None:
    change = _fold(ledger).get(change_id)
    if not change or change["status"] != "open":
        raise ChangeError("That change is no longer open.")
    if len(reason.strip()) < 8:
        raise ChangeError("Say why this change isn't going ahead.")
    ledger.append(actor, "change.rejected", id=change_id, reason=reason.strip())


def briefings_due(ledger: Ledger, done: set[str]) -> list[tuple[str, str]]:
    """Each affected department hears about an approved change once."""
    out = []
    team = shape.team(ledger.state())
    for change in _fold(ledger).values():
        if change["status"] != "approved":
            continue
        for department in sorted({i["department"] for i in change["decision"]["impact"] if i["department"] in team}):
            if f"brief_change:{change['id']}:{department}" not in done:
                out.append((change["id"], department))
    return out


def briefing_text(ledger: Ledger, change_id: str, department: str) -> str:
    change = _fold(ledger)[change_id]
    items = "\n".join(f"- {i['item']}: {i['action']}" for i in change["decision"]["impact"] if i["department"] == department)
    return f"Change {change['number']} was approved: {change['title']}. Why: {change['why']}\n\nYour department needs to deal with:\n{items}\n\nSay what you'll do, and do what you can now."
