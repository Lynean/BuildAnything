"""Headquarters runs the project. People talk to one agent per department, and this does the routing.

What happens next is decided here in code from the project's state, not by a model, so a
run can't skip a review. Agents do the work, and only a person passes a review.
"""
from __future__ import annotations

from pathlib import Path

import asyncio
import hashlib

import json
import math
import re
import threading
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from agents import WebSearchTool

from . import shape, jobs, ci, devicefacts, documents, datasheets, designneeds, protocol, agents as hq_agents, changes, compat, depgraph, traceability, connectors as conn, firmware, gates, interfaces, mechanical, page_memory, procurement, software
from .citations import PaperIndex
from .gateway import Gateway
from .ledger import Actor, Ledger, ProjectState

DEPARTMENTS = tuple(shape.DEPARTMENT_TYPES)  # every kind of department the app can run; a project's own team is shape.team(state)
LEADS = {name: kind["lead"] for name, kind in shape.DEPARTMENT_TYPES.items()}
MAX_REQUESTS_PER_RUN = 3
MAX_RESEARCH_ATTEMPTS = 2
MAX_PLAN_ATTEMPTS = 2
MAX_DESIGN_CHARS = 100_000
SYSTEM = Actor.system("headquarters")
LABELS = {"concept": "Concept Partner is preparing the concept", "research": "Research Engineer is finding and checking sources", "plan": "Project Controller is writing the plan", "revise": "Project Controller is revising the plan", "verify": "Verifier is reviewing the plan"}


class HeadquartersError(ValueError):
    pass


@dataclass
class Services:
    papers: PaperIndex
    registry: conn.Registry | None = None


def concept_approved(ledger: Ledger) -> bool:
    return any(e["kind"] == "concept.approved" for e in ledger.events())


def _past_concept(ledger: Ledger, state: ProjectState) -> bool:
    return concept_approved(ledger) or gates.passed(state, "G1")


def _active(state: ProjectState, record_type: str) -> list[dict[str, Any]]:
    return [r for r in state.of_type(record_type) if r["status"] not in ("rejected", "superseded")]


def _count(events: list[dict[str, Any]], step: str) -> int:
    return sum(1 for e in events if e["kind"] == "hq.finished" and e["data"]["step"] == step)


def failure(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A failed step waits for a person, so a broken step can't keep spending."""
    for event in reversed(events):
        if event["kind"] == "hq.retry":
            return None
        if event["kind"] == "hq.failed":
            return event["data"]
    return None


def plan_version(events: list[dict[str, Any]]) -> int:
    return sum(1 for e in events if e["kind"] == "plan.written")


def _addressed(events: list[dict[str, Any]]) -> set[str]:
    return {f for e in events if e["kind"] == "plan.written" for f in e["data"].get("addresses", [])}


def fixes_to_apply(state: ProjectState, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Waits until every open high finding has a decision, so the plan is revised once, not per click."""
    opened = gates.open_high_findings(state)
    if not opened or any(not f.get("decision") for f in opened):
        return []
    done = _addressed(events)
    return [f for f in opened if f["decision"]["decision"] == "fix" and f["id"] not in done]


AUTO_CI = True
GATE_REVIEW = True
_proposing = threading.Lock()  # An agent can call tools in parallel, which could otherwise add one part twice.
CLONE_STAGGER_S = 3  # The first clone goes slightly early, so the others find its context already saved at the cheaper rate.
PARALLEL = "Other jobs in your department are running at the same time. Stay within your items. look_up_project shows the current state and what the others are doing. If you need anything outside your items, report BLOCKED."
MAX_DRIVES = 3  # Per department and gate, so an agent that can't finish can never spend without limit.
ROUTINE = {"QUESTION": ("Fast", 8), "CHANGE_NOTICE": ("Fast", 8)}  # Answering a question or acknowledging a change is routine: cheaper tier, fewer turns.
LEAD_TURNS = 12


def _owned_work(ledger: Ledger, state: ProjectState, gate: str) -> dict[str, list[str]]:
    from . import gatereport

    out: dict[str, list[str]] = {}
    team = shape.team(state)
    for owner, text in gatereport.blockers(gate, state, ledger):
        if owner in team:
            out.setdefault(owner, []).append(text)
    return out


def _drive(ledger: Ledger, state: ProjectState, done: set[str]) -> str | None:
    """Gives each department what the gate needs from it. It runs again only when the facts have changed."""
    gate = gates.current_gate(state)
    if gate is None or gates.phase(state, gate) in ("G0", "G1", "G2"):
        return None
    events = ledger.events()
    waiting_on_person = {r["from"] for r in requests_view(events) if r["status"] == "waiting_for_you"}
    for department, items in _owned_work(ledger, state, gate).items():
        if designneeds.deferred(ledger, department):
            continue
        if protocol.is_held(events, department) or protocol.bug_open(events, department) or department in waiting_on_person:
            continue
        # The same list twice means the agent already tried, so a person hears about it instead of paying again.
        token = hashlib.sha256(json.dumps(items).encode()).hexdigest()[:10]
        action = f"drive:{gate}:{department}:{token}"
        if action not in done and sum(1 for step in done if step.startswith(f"drive:{gate}:{department}:")) < MAX_DRIVES:
            return action
    return None


def _answers_to_deliver(events: list[dict[str, Any]], done: set[str]) -> dict[str, list[str]]:
    """Answers wait until a department's questions are all answered, so it's told everything in one message."""
    requests = requests_view(events)
    waiting = {r["from"] for r in requests if r["status"] == "waiting_for_you"}
    out: dict[str, list[str]] = {}
    for r in requests:
        if r.get("route") == "ask_person" and r["status"] == "answered" and f"deliver:{r['id']}" not in done and r["from"] not in waiting:
            out.setdefault(r["from"], []).append(r["id"])
    return out


def next_action(ledger: Ledger) -> str | None:
    state, events = ledger.state(), ledger.events()
    if failure(events):
        return None
    waiting = changes.waiting_review(ledger)
    if waiting:
        return f"review_change:{waiting[0]}"
    done = {e["data"]["step"] for e in events if e["kind"] == "hq.finished"}
    for ids in _answers_to_deliver(events, done).values():
        return f"deliver:{','.join(ids)}"
    due = changes.briefings_due(ledger, done)
    if due:
        department = due[0][1]  # One message per department, however many changes were approved together.
        return f"brief_change:{','.join(c for c, d in due if d == department)}:{department}"
    for relay in [e for e in events if e["kind"] == "relay.pending"]:
        if f"relay:{relay['id']}" not in done:
            return f"relay:{relay['id']}"
    for asked in [e for e in events if e["kind"] == "command.person" and e["data"]["type"] == "RECHECK"]:
        if f"recheck:{asked['id']}" not in done:
            return f"recheck:{asked['id']}"
    if not _past_concept(ledger, state):
        drafts = state.of_type("concept_draft")
        return "concept" if not drafts or "summary" not in drafts[-1]["fields"] else None
    if not gates.passed(state, "G1"):
        waived = any(e["kind"] == "research.waived" for e in events)
        if not _active(state, "claim") and not waived and _count(events, "research") < MAX_RESEARCH_ATTEMPTS:
            return "research"
        return None
    plan_exists = bool(_active(state, "requirement") or _active(state, "task"))
    if not plan_exists and "G2" not in gates.order(state):
        # No Plan gate to stop at, but the departments still work from requirements and tasks.
        return "plan" if _count(events, "plan") < MAX_PLAN_ATTEMPTS else None
    if not gates.passed(state, "G2"):
        if not plan_exists:
            return "plan" if _count(events, "plan") < MAX_PLAN_ATTEMPTS else None
        if fixes_to_apply(state, events):
            return "revise"
        version = plan_version(events)
        findings = state.of_type("finding")
        reviewed = any(f["fields"].get("plan", 0) == version for f in findings) if version else bool(findings)
        return None if reviewed else "verify"
    # Answering another department's request doesn't count as having made a first proposal.
    opened = {e["data"]["step"] for e in events if e["kind"] == "hq.finished"}
    for department in shape.team(state):
        if f"open:{department}" not in opened and not designneeds.declared_none(ledger, department) and not designneeds.deferred(ledger, department):  # No paying for a proposal nobody needs yet.
            return f"open:{department}"
    if traceability.uncovered(state) and "plan_tests" not in opened and not changes.waiting_review(ledger):
        return "plan_tests"
    answers = traceability.clarifications(events)
    waiting_tests = {traceability._key(r): answers[traceability._key(r)] for r in traceability.uncovered(state) if traceability._key(r) in answers}
    if waiting_tests:
        step = "plan_tests:" + hashlib.sha256(json.dumps(waiting_tests, sort_keys=True).encode()).hexdigest()[:10]
        if step not in opened:
            return step
    if AUTO_CI and gates.current_gate(state) and ci.pending(ledger):
        return "ci"
    work = _drive(ledger, state, opened)
    if work:
        return work
    gate = gates.current_gate(state)
    if GATE_REVIEW and gates.phase(state, gate) in ("G4", "G5", "G6") and f"gate_review:{gate}" not in opened:
        from . import gatereport

        if not gatereport.blockers(gate, state, ledger):
            return f"gate_review:{gate}"
    return None


GOTO = {"claims": "Review queue", "plan": "Review queue", "question": "Review queue", "gate_waiting": "Journey"}
PLACES = (("Verifier findings|mark them fixed", "Headquarters"), ("Tests screen", "Tests"), ("Set the parts budget on the Procurement", "Procurement"), ("Set the parts budget", "Mechanical"), ("over your .* budget", "Procurement"), ("warning doesn't apply|Upload datasheets|Check the datasheet|Review \\d+ (components|datasheet|connections)", "Electrical"), ("suggested part|Fusion change|part checks|Release", "Mechanical"), ("acknowledge|interfaces", "Interface"), ("Approve \\d+ test|requirement|you'd accept", "Tests"), ("change request", "Changes"), ("Order these|arrived", "Procurement"), ("Flash", "Software"))


def _place(text: str) -> str:
    return next((screen for pattern, screen in PLACES if re.search(pattern, text)), "Journey")


def needs(ledger: Ledger) -> list[dict[str, Any]]:
    """What only the person can do next, and where to go to do it."""
    return [{**n, **({"goto": n.get("goto") or GOTO[n["kind"]]} if n.get("goto") or n["kind"] in GOTO else {})} for n in _needs(ledger)]


def _needs(ledger: Ledger) -> list[dict[str, Any]]:
    state, events = ledger.state(), ledger.events()
    out: list[dict[str, Any]] = []
    broken = failure(events)
    if broken:
        out.append({"kind": "no_sources" if broken["step"] == "research" else "failed", "text": f"Research stopped: {broken['message']}" if broken["step"] == "research" else f"{broken['label']} stopped: {broken['message']}"})
        return out
    if not _past_concept(ledger, state):
        drafts = [d for d in state.of_type("concept_draft") if "summary" in d["fields"]]
        if drafts:
            count = len(drafts[-1]["fields"].get("open_questions", []))
            out.append({"kind": "concept", "text": f"Approve the concept when it looks right. {count} questions are still open." if count else "The concept looks complete. Check the team and stages Headquarters suggests, then approve it."})
        return out
    from . import services as reception

    app_folder = ledger.root.parent / "_app"
    for asked in reception.asks(Ledger(app_folder)) if app_folder.is_dir() else []:
        if asked["project"] == ledger.root.name:
            found = [reception.BY_ID[i]["group"] for i in reception.providers_of(asked["capability"])]
            side = "External Service" if found and found[0] in ("cloud", "app") else "Internal Service"
            out.append({"kind": "tool", "text": f"{asked['department']} needs a tool: {asked['capability']}. {asked['why'][:160]}", "goto": side})
    for asked in shape.requests(ledger):
        out.append({"kind": "team", "text": f"{asked['by'].title()} asks to add {asked['department']} to the team: {asked['why'][:180]}", "goto": "Journey"})
    if not gates.passed(state, "G1"):
        waiting = state.of_type("claim", "proposed")
        if waiting:
            out.append({"kind": "claims", "text": f"Review {len(waiting)} sourced claims."})
        elif not _active(state, "claim") and next_action(ledger) is None and not any(e["kind"] == "research.waived" for e in events):
            out.append({"kind": "no_sources", "text": "Research couldn't find sources that passed the quote check."})
        elif gates.missing("G1", state):
            out.append({"kind": "gate_waiting", "text": f"To finish {gates.stage('G1', state)}: {gates.missing('G1', state)[0]}", "goto": "Journey"})
        else:
            out.append({"kind": "gate", "gate": "G1", "text": f"Sources are reviewed. {gates.continue_text('G1', state)}{' to start the plan' if gates.next_stage('G1', state) == gates.stage('G2', state) and 'G2' in gates.order(state) else ''}."})
        return out
    if not gates.passed(state, "G2"):
        if next_action(ledger):
            return out
        plan = state.of_type("requirement", "proposed") + state.of_type("task", "proposed")
        high = gates.open_high_findings(state)
        undecided = [f for f in high if not f.get("decision")]
        if undecided:
            out.append({"kind": "findings", "text": f"Decide {len(undecided)} serious problems the Verifier found."})
        fixing = [f for f in high if f.get("decision", {}).get("decision") == "fix"]
        if fixing:
            out.append({"kind": "findings", "text": f"Check that {len(fixing)} fixes worked, then mark them fixed."})
        if plan:
            out.append({"kind": "plan", "text": f"Review {len(plan)} requirements and tasks."})
        if not out and gates.missing("G2", state):  # such as a requirement the person wrote for this stage
            out.append({"kind": "gate_waiting", "text": f"To finish {gates.stage('G2', state)}: {gates.missing('G2', state)[0]}", "goto": "Journey"})
        if not out and not gates.missing("G2", state):
            out.append({"kind": "gate", "gate": "G2", "text": f"The plan is reviewed. {gates.continue_text('G2', state)} to brief the departments."})
        return out
    for department in shape.team(state):
        report = protocol.latest(events, department)
        if protocol.bug_open(events, department) and not designneeds.declared_none(ledger, department):
            out.append({"kind": "bug", "text": f"{department} found a tool problem: {(report['risk'] or report['for_person'])[:200]}", "goto": department})
    questions = [r for r in requests_view(events) if r["status"] == "waiting_for_you"]
    if questions:
        more = f" And {len(questions) - 1} more in the Review queue." if len(questions) > 1 else ""
        out.append({"kind": "question", "text": f"{questions[0]['from']} needs an answer from you: {questions[0]['question'][:180]}{more}"})
    current = gates.current_gate(state)
    if current and next_action(ledger) is None:
        from . import gatereport

        waiting = gatereport.blockers(current, state, ledger)
        stage = gates.stage(current, state)
        yours = [text for owner, text in waiting if owner == gatereport.YOU]
        stuck = [(owner, text) for owner, text in waiting if owner != gatereport.YOU]
        if yours:
            out.append({"kind": "gate_waiting", "text": f"To finish {stage}: {yours[0]}" + (f" And {len(waiting) - 1} more on the Journey screen." if len(waiting) > 1 else ""), "goto": _place(yours[0])})
        elif stuck:
            out.append({"kind": "stuck", "text": f"{stuck[0][0]} couldn't finish this for the {stage} stage: {stuck[0][1]} Open it and tell it what you know.", "goto": stuck[0][0]})
        elif not waiting:
            out.append({"kind": "gate", "gate": current, "text": f"{stage} is done. {gates.continue_text(current, state)}."})
    return out


# ---- Conversations with a department ----


def thread(events: list[dict[str, Any]], department: str) -> list[dict[str, Any]]:
    approved = next((i for i, e in enumerate(events) if e["kind"] == "concept.approved"), -1)
    return [{"role": e["data"]["role"], "text": e["data"]["text"], "from": e["data"].get("from"), "at": e["at"], "early": i < approved, "details": e["data"].get("details", ""), "internal": bool(e["data"].get("internal")) or (e["data"]["role"] == "request" and e["data"].get("from") != "You")} for i, e in enumerate(events) if e["kind"] == "dept.message" and e["data"]["department"] == department]


def requests_view(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for event in events:
        d = event["data"]
        if event["kind"] == "request.raised":
            out[d["id"]] = {"id": d["id"], "from": d["sender"], "to": d["to"], "question": d["question"], "why": d["why"], "answer": None, "status": "open", "at": event["at"]}
        elif event["kind"] == "request.answered" and d["id"] in out:
            out[d["id"]].update(answer=d["answer"], status="answered", outcome=d.get("outcome"))
        elif event["kind"] == "request.failed" and d["id"] in out:
            out[d["id"]].update(answer=d["message"], status="failed")
        elif event["kind"] == "request.routed" and d["id"] in out:
            out[d["id"]].update(to=d["to"], route=d["action"], reason=d["reason"], status={"decline": "declined", "reword": "declined", "assign": "declined", "ask_person": "waiting_for_you"}.get(d["action"], out[d["id"]]["status"]))
        elif event["kind"] == "request.person_answered" and d["id"] in out:
            out[d["id"]].update(answer=d["answer"], status="answered")
    return list(out.values())


def _say(ledger: Ledger, department: str, role: str, text: str, sender: str | None = None, details: str = "", internal: bool = False) -> None:
    actor = Actor.human() if role == "you" else SYSTEM
    ledger.append(actor, "dept.message", department=department, role=role, text=text, **({"from": sender} if sender else {}), **({"details": details} if details else {}), **({"internal": True} if internal else {}))


SCREEN_ACTION = re.compile(r"^(please\s+)?(approve|decline|reject|confirm|review|accept)\b.*\b(cad|fusion|proposals?|proposed|components?|connections?|limits?|parts?|tests?|wiring list)\b", re.I)
APPROVAL = re.compile(r"\b(approve|decline|approval)\b.*\b(cad|fusion|proposals?|components?|connections?|limits?|parts?|tests?)\b|\b(cad|fusion|proposals?)\b.*\b(approve|decline)\b", re.I)
ASKS_A_FACT = re.compile(r"\b(which|whether|what|how many)\b", re.I)
SECOND_ACTION = re.compile(r"\s+(?:and|then)\s+(?=(?:please\s+)?(?:approve|decline|reject|confirm|review|accept)\b)", re.I)
COMMON_WORDS = {"a", "an", "the", "of", "or", "and", "to", "your", "you", "that", "this", "is", "it", "in", "on", "for", "please", "already", "existing", "requested", "previously", "clear", "exact"}


def _meaningful_words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9-]+", text.lower())) - COMMON_WORDS


def _repeats(question: str, asked: list[str]) -> bool:
    words = _meaningful_words(question)
    if len(words) < 3:
        return any(_meaningful_words(q) == words for q in asked)
    return any(len(words & other) / min(len(words), len(other)) >= 0.7 for other in map(_meaningful_words, asked) if other)


def question_for_person(what: str, asked: list[str]) -> str | None:
    """Keeps only what the person alone can answer. Anything with a button on a screen already shows up there."""
    clauses = [c.strip().rstrip(".") for c in SECOND_ACTION.split(what)]
    kept = " and ".join(c for c in clauses if len(c.split()) > 1 and not APPROVAL.search(c) and (ASKS_A_FACT.search(c) or not SCREEN_ACTION.search(c)))
    return None if not kept or _repeats(kept, asked) else kept[0].upper() + kept[1:] + ("" if kept[-1] in ".?!" else ".")


def _asked_person(events: list[dict[str, Any]]) -> list[str]:
    return [r["question"] for r in requests_view(events) if r.get("route") == "ask_person"]


def _similar(a: str, b: str) -> bool:
    return _repeats(a, [b])


def _plan_owner(ledger: Ledger, text: str) -> str | None:
    """The department the plan gave this work to, if a task matches it closely."""
    words = _meaningful_words(text)
    best, score = None, 0.0
    for task in _active(ledger.state(), "task"):
        other = _meaningful_words(task["fields"].get("title", ""))
        if words and other:
            overlap = len(words & other) / min(len(words), len(other))
            if overlap > score:
                best, score = task["fields"].get("department"), overlap
    return best if score >= 0.5 and best in shape.team(ledger.state()) else None


def _loop(events: list[dict[str, Any]], sender: str, target: str, question: str) -> str | None:
    """Two departments asking each other for the same thing, or one asking again after being told it's blocked."""
    for r in requests_view(events):
        if r["status"] not in ("answered", "open") or not _similar(question, r["question"]):
            continue
        if r["from"] == target and r["to"] == sender:
            return f"{target} already asked {sender} for this"
        if r["from"] == sender and r["to"] == target and r.get("outcome") in ("BLOCKED", "PARTIAL"):
            return f"{target} already answered this as {r['outcome'].lower()}"
    return None


def department_tool(ledger: Ledger, gateway: Gateway, sender: str, services: Services) -> Callable[..., Awaitable[str]]:
    """Requests between departments always go through Headquarters, and answers can't start new requests."""
    used = {"count": 0}

    async def ask_department(department: str, deliverable: str, used_for: str, why: str = "") -> str:
        """Ask another department through Headquarters for one specific thing it can hand over, such as "collision shapes for each arm link from the CAD" or "the ESP32-C6 pin for servo 3". deliverable is that thing; used_for is what you'll do with it. Ask for something that can be produced now, not "everything needed for X". Check list_deliverables first: it may already exist. department is one on this project's team."""
        question = deliverable.strip()
        why = (used_for.strip() + (f" ({why.strip()})" if why.strip() else "")).strip()
        team = shape.team(ledger.state())
        if department in DEPARTMENTS and department not in team:
            return f"{department} isn't on this project's team. If the project really needs it, ask the person to add it with request_department."
        if department not in team or department == sender:
            return f"Choose one of: {', '.join(d for d in team if d != sender) or 'nobody else is on the team'}."
        if len(question.split()) < 3 or not used_for.strip():
            return "Say exactly what you want handed over and what you'll use it for."
        if used["count"] >= MAX_REQUESTS_PER_RUN:
            return "You've reached the limit of department requests for this reply. Work with what you have."
        used["count"] += 1
        request_id = f"req_{uuid.uuid4().hex[:10]}"
        looping = _loop(ledger.events(), sender, department, question) if sender in DEPARTMENTS else None
        owner = _plan_owner(ledger, question) if looping else None
        if looping and not owner:  # nobody's job by the plan: the person decides, in one plain question
            question = f"{sender} and {department} each need the other for this: {deliverable.strip()}. Which department should do it?"
        ledger.append(SYSTEM, "request.raised", id=request_id, sender=sender, to=department, question=question, why=why)
        events = ledger.events()
        if looping:
            if owner == sender:
                ledger.append(SYSTEM, "request.routed", id=request_id, to=sender, action="assign", reason=f"{looping}. The plan gives this work to {sender}.")
                return f"Headquarters: {looping}, and the plan gives this work to you. Do it yourself now with cautious assumptions for anything unknown, and list them as provisional."
            if owner:
                department = owner
                question = f"{question}\n\nHeadquarters assigned this to you because {looping} and the plan gives this work to {owner}. Hand over what you can now; use cautious assumptions for anything unknown and list them."
            else:
                ledger.append(SYSTEM, "request.routed", id=request_id, to="You", action="ask_person", reason=f"{looping}, and the plan doesn't say whose job it is.")
                return f"Headquarters: {looping}. The person has been asked whose job it is. Meanwhile do every part you can with cautious assumptions."
        earlier = {r["id"]: r for r in requests_view(events) if r["status"] == "answered" and r["id"] != request_id}
        route: changes.Route = await gateway.run(
            agent="router", output_type=changes.Route,
            values={"project": {"aim": ledger.state().meta.get("aim"), "scope": ledger.state().meta.get("scope")}, "kind": "request_between_departments", "item": {"from": sender, "question": question, "why": why}, "suggested": department, "departments": changes.ROLES, "recent_requests": [{k: r[k] for k in ("id", "from", "to", "question", "answer")} for r in list(earlier.values())[-10:]], "decisions": [d["fields"]["text"] for d in ledger.state().of_type("decision", "confirmed")]},
        )
        if route.action == "reword" and not looping:
            ledger.append(SYSTEM, "request.routed", id=request_id, to="None", action="reword", reason=route.reason)
            return f"Headquarters sent this back: it asks for more than anyone can hand over. {route.reason} Ask again for one specific thing."
        if route.action == "answer_from_record" and route.earlier_id in earlier:
            ledger.append(SYSTEM, "request.routed", id=request_id, to=earlier[route.earlier_id]["to"], action=route.action, reason=route.reason)
            ledger.append(SYSTEM, "request.answered", id=request_id, answer=earlier[route.earlier_id]["answer"], outcome=earlier[route.earlier_id].get("outcome"))
            return f"Already answered: {earlier[route.earlier_id]['answer']}"
        if route.action == "decline":
            ledger.append(SYSTEM, "request.routed", id=request_id, to="None", action=route.action, reason=route.reason)
            return f"Headquarters declined this: {route.reason}"
        if route.action == "ask_person" and question_for_person(question, _asked_person(events)) is None:
            ledger.append(SYSTEM, "request.routed", id=request_id, to="None", action="decline", reason="Already asked, or already waiting on a screen.")
            return "The person has already been asked that, or it's waiting on their screen. Carry on with what you can."
        if route.action == "ask_person":
            ledger.append(SYSTEM, "request.routed", id=request_id, to="You", action=route.action, reason=route.reason)
            return "Headquarters has passed this to the person. Carry on with what you can, and say what's waiting on them."
        if route.handler == sender and not looping:
            ledger.append(SYSTEM, "request.routed", id=request_id, to=sender, action="decline", reason=f"This is {sender}'s own job.")
            return f"This is your own department's job, so handle it yourself. {route.reason}"
        target = route.handler if route.handler in team and route.handler != sender and not looping else department
        ledger.append(SYSTEM, "request.routed", id=request_id, to=target, action="forward", reason=route.reason)
        _say(ledger, target, "request", f"{question}\n\nFor: {why}" if why else question, sender)
        try:
            answer = await run_lead(ledger, gateway, target, f"{sender} asks you to hand over: {question}\nThey'll use it for: {why}", services, nested=True, internal=True)
        except Exception as exc:
            ledger.append(SYSTEM, "request.failed", id=request_id, message=type(exc).__name__)
            raise
        report = protocol.latest(ledger.events(), target) or {}
        outcome = report.get("type")
        if report.get("can_give_now"):
            answer += " What they can give now: " + "; ".join(report["can_give_now"])
        ledger.append(SYSTEM, "request.answered", id=request_id, answer=answer, outcome=outcome)
        return answer

    return ask_department


def change_tool(ledger: Ledger, department: str) -> Callable[..., dict[str, str]]:
    def raise_change_request(title: str, why: str, kind: str, target: str, details_json: str) -> dict[str, str]:
        """Ask for a change to something already agreed. A person decides. kind is part, component, requirement, drop_requirement, deviation, packet, middleware or other. target is the part number, record ID or packet name, or device:src/mid/header.h for middleware. details_json is a JSON object, such as {"material": "PETG"} for a part, {"part_number": "MG90S"} for a component, {"text": "new wording"} for a requirement, or {"what": "what isn't met", "restores": "what would put it right"} for a deviation."""
        try:
            details = json.loads(details_json or "{}")
        except ValueError:
            return {"error": "details_json must be a JSON object."}
        try:
            return {"id": changes.propose(ledger, title, why, kind, target, details if isinstance(details, dict) else {}, Actor.agent(department.lower()))}
        except changes.ChangeError as exc:
            return {"error": str(exc)}

    return raise_change_request


def _cad_files(ledger: Ledger) -> dict[str, Any] | None:
    """The CAD export in the project folder, if any, so Software's programs can read the real model's files."""
    meta_file = ledger.root / "mechanics" / "cad" / "meta.json"
    if not meta_file.exists():
        return None
    info = json.loads(meta_file.read_text(encoding="utf-8"))
    model = Path(info["model"])
    if model.name == "ba_prepared.urdf":
        original = [p for p in (ledger.root / "mechanics" / "cad" / model.parent).glob("*.urdf") if p.name != model.name]
        if original:
            model = model.parent / original[0].name
    return {"model": f"mechanics/cad/{model.as_posix()}", "folder": f"mechanics/cad/{model.parent.as_posix()}", "format": info.get("format"),
            "from_a_device_folder": f"../../mechanics/cad/{model.as_posix()}",
            "note": "The machine's CAD export, with meshes next to it. Programs in firmware/<device>/ reach it with the relative path given."}


def _department_state(ledger: Ledger, department: str, services: Services) -> dict[str, Any]:
    events = ledger.events()
    from . import deliverables

    others: dict[str, Any] = {}
    for d in shape.team(ledger.state()):
        if d == department:
            continue
        report = protocol.latest(events, d)
        if report:
            others[d] = {**deliverables.report_summary(report), "details": (report.get("details") or "")[:1500]}
        else:
            text = next((m["text"][:1200] for m in reversed(thread(events, d)) if m["role"] == "agent" and not m["early"]), None)
            if text:
                others[d] = text
    simulation = designneeds.simulation_first(ledger)
    scope = {"simulation_first": f"The person wants the software and simulation first ({simulation}). No parts, wiring, firmware or buying until they switch hardware on: don't propose or ask for any."} if simulation else {}
    return {**_own_state(ledger, department, services), **scope, "other_departments_latest": others,
            "deliverables": "Every department's reports and files can be read with list_deliverables, read_deliverable and read_department_reports. Look there before asking for something."}


def _own_state(ledger: Ledger, department: str, services: Services) -> dict[str, Any]:
    state = ledger.state()
    if department == "Electrical":
        sheets = page_memory.latest_sheets(state)
        return {"components": [{"id": c["id"], "part": c["fields"]["part_number"], "role": c["fields"].get("role"), "status": c["status"], "datasheet": c["id"] in sheets} for c in state.of_type("component") if c["status"] != "rejected"], "nets": len(_active(state, "net")), "failed_checks": [c for c in compat.run_checks(state) if c.get("status") == "fail"][:10], "packets": interfaces.summary_for_agents(state), "needed_items": designneeds.view(ledger), "agreed_pins": firmware.pin_defines(state)}
    if department == "Mechanical":
        return {"no_mechanical_work": designneeds.declared_none(ledger, "Mechanical"), "parts": [p["fields"] for p in _active(state, "part")], "suggested_parts": [p["fields"] for p in state.of_type("part_proposal", "proposed")]}
    if department == "Software":
        return {"cad_model_files": _cad_files(ledger), "devices": {d: {"kind": "computer" if firmware.is_computer(ledger.root, d) else "board", "files": [f["path"] for f in firmware.scan_workspace(ledger.root, d) if not f.get("generated")]} for d in firmware.devices(ledger.root)}, "packets": interfaces.summary_for_agents(state), "designs": [d["fields"]["title"] for d in state.of_type("design_proposal")]}
    return {"budget_currency": designneeds.currency(ledger), "buying_list": designneeds.to_buy(state, mechanical.current_parts(state)), "priced": list(designneeds.prices(state)), "orders": procurement.orders_view(state)["orders"][:30], "components": [{"part": c["fields"]["part_number"], "role": c["fields"].get("role")} for c in state.of_type("component") if c["status"] != "rejected"], "parts": [p["fields"] for p in _active(state, "part")]}


def _services_for(ledger: Ledger, department: str) -> Any:
    from . import cloud

    store = cloud.store_for(ledger.root.parent / "_app")
    listed = store.for_department(department) if store else []
    return listed or "None connected. External Service can connect an outside service (an API or MCP server) if you need one."


def _service_tool(ledger: Ledger, department: str) -> Any:
    from . import cloud

    async def use_service(connection: str, operation: str, params_json: str = "{}", why: str = "") -> dict[str, Any]:
        """Use a connected outside service listed in services: run one of its operations by name with params as a JSON object. Reading runs straight away; an operation that changes something waits for the person's approval, so say why."""
        store = cloud.store_for(ledger.root.parent / "_app")
        if store is None:
            return {"error": "No services are connected."}
        try:
            params = json.loads(params_json or "{}")
            m = store.approved(connection)
            if not m or department not in m.departments:
                return {"error": f"{connection} isn't connected for {department}. Ask External Service, through the person, to connect it."}
            op = next((o for o in m.operations if o.name == operation), None)
            if op and op.writes:
                if not why.strip():
                    return {"error": "This changes something in the service, so give the person a reason in why."}
                return store.request_write(connection, operation, params, why, ledger.root.name, department, Actor.agent(department.lower()))
            return await store.call(connection, operation, params, Actor.agent(department.lower()), ledger.root.name)
        except (ValueError, cloud.CloudError) as exc:
            return {"error": str(exc)}

    return use_service


def _lead_tools(ledger: Ledger, gateway: Gateway, department: str, services: Services, nested: bool = False) -> list[Any]:
    root = ledger.root
    if nested and not concept_approved(ledger):
        return [WebSearchTool()]  # Before the concept is agreed a department can advise, but it can't add anything to the design.
    tools: list[Any] = [_service_tool(ledger, department)]
    if department == "Electrical":
        def set_needed_items(items_json: str) -> dict[str, Any]:
            """State everything this design needs to be built and safe, as a JSON object of short item names, each with the requirement key it serves or the engineering or safety reason it's needed, such as {"Six joint servos": "R1", "Servo power fuse": "Stops a stalled servo overheating the wiring"}. Include only what's really needed. A gate checks that each item gets chosen. Call it again with the full list to update it."""
            try:
                items = json.loads(items_json or "{}")
            except ValueError:
                return {"error": "items_json must be a JSON object."}
            if not isinstance(items, dict) or not items:
                return {"error": "Give a JSON object of item names, each with its reason."}
            keys = {r["fields"].get("key") for r in ledger.state().of_type("requirement", "confirmed")}
            unexplained = [name for name, reason in items.items() if str(reason).strip() not in keys and len(str(reason).strip()) < 8]
            if unexplained:
                return {"error": f"Give a requirement key or a real reason for: {', '.join(unexplained)}."}
            return designneeds.set_needed(ledger, list(items), "", Actor.agent("electrical"), {str(k): str(v).strip() for k, v in items.items()})

        async def read_limits(part_number: str) -> dict[str, Any]:
            """Read the electrical limits from a confirmed component's uploaded datasheet. A person checks them afterwards."""
            state = ledger.state()
            component = next((c for c in state.of_type("component", "confirmed") if c["fields"]["part_number"].lower() == part_number.strip().lower()), None)
            sheet = next((d for d in reversed(state.of_type("datasheet")) if component and d["fields"]["component"] == component["id"]), None)
            if not sheet:
                return {"error": "That component needs to be confirmed and have a datasheet uploaded first."}
            if not sheet["fields"].get("part_match"):
                return {"error": "The part number wasn't found in that PDF. Ask the person to check it on this screen."}
            return await datasheets.extract_component(ledger, gateway, component["id"], sheet, root / "datasheets" / sheet["fields"]["file"])

        def propose_component(part_number: str, role: str, fills: str = "", generic: bool = False, datasheet_url: str = "", category: str = "") -> dict[str, str]:
            """Suggest a component for the person to confirm. fills is the needed item it provides, exactly as named in set_needed_items. One part per needed item: the same part can fill several items, but to change an item's part use raise_change_request. Set generic only for things with no datasheet, such as wire, cable and simple connectors. datasheet_url is a direct link to the PDF if you found one. category is microcontroller (a bare chip), dev_board (a board carrying a chip, such as a Nucleo or DevKit), module (a display, sensor or driver board) or generic; it decides which documents the part needs."""
            if re.search(r"\bTBD\b", part_number, re.I):
                return {"error": "Nobody can buy a TBD. Work out the rating first, or leave the item open and say what you need to decide it."}
            with _proposing:
                state = ledger.state()
                live = [c for c in state.of_type("component") if c["status"] != "rejected"]
                wanted = " ".join(fills.lower().split())
                if wanted and any(" ".join(designneeds._fills(state, c).lower().split()) == wanted for c in live if c["status"] != "superseded"):
                    return {"error": f"{fills} already has a part. To change it, use raise_change_request."}
                if not wanted and any(c["fields"]["part_number"].lower() == part_number.strip().lower() for c in live):
                    return {"status": "Already listed."}
                return {"id": ledger.propose(Actor.agent("electrical"), "component", part_number=part_number.strip(), role=role.strip(), fills=fills.strip(), generic=bool(generic), datasheet_url=datasheet_url.strip(), category=category.strip() if category.strip() in documents.CATEGORIES else "")}

        async def draft_connections() -> dict[str, Any]:
            """Draft the connection list from confirmed components. Code checks it, and a person confirms it."""
            from .electrical_api import design_connections

            return await design_connections(ledger, gateway)

        def group_circuit(name: str, part_numbers: list[str]) -> dict[str, Any]:
            """Group components into a named sub-circuit tab on the circuit diagram. This doesn't change any connections."""
            from . import circuits

            components = {c["fields"]["part_number"].lower(): c["id"] for c in ledger.state().of_type("component") if c["status"] != "rejected"}
            missing = [p for p in part_numbers if p.lower() not in components]
            if missing:
                return {"error": f"Not in this project: {', '.join(missing)}."}
            circuits.add_group(ledger, name, Actor.agent("electrical"))
            for part in part_numbers:
                circuits.assign(ledger, components[part.lower()], name, Actor.agent("electrical"))
            return {"status": "Grouped."}

        session = page_memory.PageSession(ledger, root, max_pages=8)
        def dispute_check(message: str, why: str) -> dict[str, str]:
            """Say an automatic compatibility warning doesn't apply, such as a chip's limit applied to a development board's connector. Copy message exactly. A person decides, so only use this when you're sure and can say why."""
            ledger.append(Actor.agent("electrical"), "compat.disputed", message=message.strip(), why=why.strip())
            return {"status": "A person will decide."}

        firmware_writes = {"count": 0}

        async def write_firmware(device: str, board: str, goal: str) -> dict[str, Any]:
            """Write a board's DRIVER and MIDDLEWARE layers. device is lowercase with hyphens, board is the PlatformIO board ID. Software writes the app on top of your middleware headers. Include everything in one goal, because this can only run twice per reply."""
            firmware_writes["count"] += 1
            if firmware_writes["count"] > 2:
                return {"error": "That's twice in this reply, and each rewrite costs money. Report what's left."}
            name = device.strip().lower()
            if not re.fullmatch(r"[a-z0-9-]{1,40}", name) or not board.strip():
                return {"error": "Give a device name in lowercase letters, numbers and hyphens, and the PlatformIO board ID."}
            return await firmware.generate(ledger, gateway, root, name, board.strip(), goal, scope="lower")

        async def read_facts(part_number: str) -> dict[str, Any]:
            """Read the facts code needs to talk to a part, such as I2C addresses, SPI mode and active levels, from all of its uploaded documents. A person confirms them, and firmware uses them as constants."""
            state = ledger.state()
            match = next((c for c in state.of_type("component", "confirmed") if c["fields"]["part_number"].lower() == part_number.strip().lower()), None)
            if match is None:
                return {"error": "That isn't a confirmed component. Copy its part number exactly."}
            if not any(cid == match["id"] for (cid, _kind) in documents.latest_docs(state)):
                return {"error": "That part has no documents uploaded yet."}
            return await devicefacts.extract(ledger, gateway, match["id"])

        bench_builds = {"count": 0}

        async def build_bench(device: str) -> dict[str, Any]:
            """Build the laptop test bench for a board: fake parts written from their documents, and scenarios from the requirements, then run the real firmware against them. Neither you nor the firmware writer sees the fakes being written. Use it once the board has code and its parts are wired."""
            from . import benchwriter

            bench_builds["count"] += 1
            if bench_builds["count"] > 1:
                return {"error": "The bench was already built in this reply. Report the results."}
            name = device.strip().lower()
            if name not in firmware.devices(root):
                return {"error": f"There's no device called {name}. Devices: {', '.join(firmware.devices(root)) or 'none yet'}."}
            return await benchwriter.build_bench(ledger, gateway, root, name)

        tools += [dispute_check, set_needed_items, read_limits, read_facts, propose_component, draft_connections, group_circuit, write_firmware, build_bench, WebSearchTool(), *session.tools()]
    elif department == "Mechanical":
        def propose_part(number: str, name: str, material: str, process: str, quantity: int, why: str) -> dict[str, str]:
            """Suggest a part for the person to add to the parts list. To change a part already listed, use raise_change_request."""
            with _proposing:
                state = ledger.state()
                if number.strip() in mechanical.parts_by_number(state) or any(p["fields"]["number"] == number.strip() for p in state.of_type("part_proposal", "proposed")):
                    return {"status": f"{number.strip()} is already listed."}
            return {"id": ledger.propose(Actor.agent("mechanical"), "part_proposal", number=number.strip(), name=name.strip(), material=material.strip(), process=process.strip(), qty=max(1, quantity), why=why.strip())}

        def set_assemblies(plan_json: str, why: str) -> dict[str, Any]:
            """List every joint and assembly of the complete product and the part numbers in each, as a JSON object such as {"Base joint": ["M-01", "M-02"], "Gripper": ["M-09"]}. Include all of it, even if you plan to test one joint first, because a gate checks that each has parts. Call again with the full list to update it."""
            try:
                plan = json.loads(plan_json or "{}")
            except ValueError:
                return {"error": "plan_json must be a JSON object."}
            if plan == {}:
                return {"status": "Nothing to record, which is fine for a project with no mechanical parts."}
            if not isinstance(plan, dict) or not all(isinstance(listed, list) for listed in plan.values()):
                return {"error": "Give a JSON object of assembly names, each with a list of part numbers."}
            return {"id": ledger.propose(Actor.agent("mechanical"), "assembly_plan", assemblies={str(k).strip(): [str(p).strip() for p in v] for k, v in plan.items()}, why=why.strip())}

        tools += [propose_part, set_assemblies, WebSearchTool()]
    elif department == "Software":
        writes = {"count": 0}

        async def write_code(device: str, goal: str, board: str) -> dict[str, Any]:
            """Write code for one device. device is lowercase with hyphens. Leave board empty for a computer. For a microcontroller, give its PlatformIO board ID: this writes only the APP layer, on top of the drivers and middleware Electrical wrote. Include everything in one goal, because this can only run twice per reply."""
            writes["count"] += 1
            if writes["count"] > 2:
                return {"error": "That's twice in this reply, and each rewrite costs money. Report what's left."}
            name = device.strip().lower()
            if not re.fullmatch(r"[a-z0-9-]{1,40}", name):
                return {"error": "Device names use lowercase letters, numbers and hyphens."}
            if board.strip():
                return await firmware.generate(ledger, gateway, root, name, board.strip(), goal, scope="app")
            return await software.generate(ledger, gateway, root, name, goal)

        def propose_design(title: str, html: str) -> dict[str, str]:
            """Show the person a screen design as one self-contained HTML page. Use inline CSS. Scripts and network requests are blocked."""
            if len(html) > MAX_DESIGN_CHARS:
                return {"error": "Keep the design under 100,000 characters."}
            return {"id": ledger.propose(Actor.agent("software"), "design_proposal", title=title.strip(), html=html)}

        async def define_packet(description: str) -> dict[str, Any]:
            """Define one message between devices from a plain description, such as what the laptop sends to the board. Code validates it and generates the C, Python and TypeScript. Say whether it travels over serial, Wi-Fi or similar."""
            try:
                return {"saved": await interfaces.draft_packet(ledger, gateway, description)}
            except interfaces.InterfaceError as exc:
                return {"error": str(exc)}

        async def check_messages() -> dict[str, Any]:
            """Run the free automatic check of the message definitions again and see its result. It changes nothing. Use it to retry the check, because define_packet always saves a new version."""
            return await ci.check_messages(ledger)

        tools += [define_packet, check_messages, write_code, propose_design, software.search_open_source]
    else:
        async def set_price(item: str, pack_price: float, pack_size: int, needed: int, currency: str, supplier: str, url: str) -> dict[str, str]:
            """Record a real price and supplier. item must be a buying-list name exactly as written in department_state. pack_price is what one listing costs, in the shop's own three-letter currency. pack_size is how much comes in that listing: 1 for a single servo, 200 for a reel of 200, 10 for a 10 m roll. needed is how much this project uses, in the same unit. Code works out how many packs to buy and converts the currency, so never do either yourself."""
            state = ledger.state()
            names = {" ".join(n.lower().split()) for n in designneeds.to_buy(state, mechanical.current_parts(state))}
            if " ".join(item.lower().split()) not in names:
                return {"error": "That isn't on the buying list. Copy the name exactly."}
            code = currency.strip().upper()
            if pack_price <= 0 or pack_size < 1 or needed < 1:
                return {"error": "Give a real price, pack size and amount needed."}
            if not re.fullmatch(r"[A-Z]{3}", code):
                return {"error": "Give the currency as a three-letter code, such as SGD or USD."}
            target = designneeds.currency(ledger)
            rate = await asyncio.to_thread(designneeds.exchange_rate, code, target)
            if rate is None:
                return {"error": f"Couldn't get a rate from {code} to {target}. Find a price in {target}, or report this item as unpriced."}
            packs = math.ceil(needed / pack_size)
            ledger.propose(Actor.agent("procurement"), "price", part=" ".join(item.lower().split()), label=item, unit_price=float(pack_price), quantity=packs, pack_size=int(pack_size), needed=int(needed), currency=code, rate=rate, rate_to=target, supplier=supplier.strip(), url=url.strip())
            return {"line_total": f"{target} {pack_price * packs * rate:.2f}", "packs": str(packs)}

        tools += [set_price, WebSearchTool()]
    tools.append(change_tool(ledger, department))

    def what_depends_on(name: str) -> dict[str, list[str]]:
        """List the connections and code that rely on a part, component, device or packet. Use this before changing anything."""
        return depgraph.build(ledger).dependents(name)

    def look_up_project() -> dict[str, Any]:
        """See your department's work as it stands right now, what else is running, and what the person has already answered. Free, so use it instead of guessing."""
        events = ledger.events()
        return {**_own_state(ledger, department, services), "running_now": [{k: j[k] for k in ("department", "title", "status", "activity")} for j in jobs.live(ledger)["jobs"]], "answers_from_person": [{"question": r["question"], "answer": r["answer"]} for r in requests_view(events) if r.get("route") == "ask_person" and r["status"] == "answered"]}

    from . import deliverables

    tools += [what_depends_on, look_up_project, *deliverables.tools(ledger)]
    return tools


def _act_on_report(ledger: Ledger, department: str, sent: dict[str, Any], report: protocol.Report, nested: bool) -> None:
    """This table is the whole of what happens after a department reports. No model decides it."""
    state = ledger.state()
    if report.type == "BUG" and any(n.owner != "Tool" for n in report.needs):
        report = report.model_copy(update={"type": "BLOCKED"})  # A real tool fault can't be solved by someone deciding something.
    filed = protocol.file_report(ledger, SYSTEM, department, sent, report, ci.evidence(ledger, department, gates.current_gate(state)))
    if report.type == "RISK" and report.severity in ("medium", "high") and report.risk.strip():
        run = max([f["fields"]["run"] for f in state.of_type("finding")] or [0]) + 1
        ledger.propose(Actor.agent("verifier"), "finding", run=run, severity=report.severity, safety=report.safety, text=f"{department}: {report.risk.strip()}", related=[])
    events = ledger.events()
    open_questions = {r["question"] for r in requests_view(events)}
    asked = _asked_person(events)
    for need in report.needs:
        if need.owner == "Person" and "change request" in need.what.lower():
            continue  # The change itself is already waiting on the Changes screen.
        if need.owner == "Person":
            question = question_for_person(need.what, asked)
            if question is None:
                continue
            asked.append(question)
            request_id = f"req_{uuid.uuid4().hex[:10]}"
            ledger.append(SYSTEM, "request.raised", id=request_id, sender=department, to="You", question=question, why="Only you can answer this.")
            ledger.append(SYSTEM, "request.routed", id=request_id, to="You", action="ask_person", reason="Only the person can answer this.")
        elif need.owner in shape.team(ledger.state()) and need.owner != department and not nested and need.what not in open_questions:
            ledger.append(SYSTEM, "relay.pending", sender=department, to=need.owner, what=need.what, report=filed["id"])


def _shared_context(ledger: Ledger, department: str, services: Services) -> dict[str, Any]:
    state, events = ledger.state(), ledger.events()
    history = "\n\n".join(f"{m['from'] or m['role']}: {m['text']}" for m in [t for t in thread(events, department) if not t["early"]][-10:])
    every_request = requests_view(events)
    answered = [r for r in every_request if r.get("route") == "ask_person" and r["status"] == "answered"]  # What the person already said, so nobody asks again.
    involved = answered + [r for r in every_request if department in (r["from"], r["to"]) and r not in answered][-6:]
    from . import skills as skillbook

    app_folder = ledger.root.parent / "_app"
    return {"briefing": hq_agents.build_briefing(state, department), "department_state": _department_state(ledger, department, services), "requests": involved, "earlier_discussion": history,
            "skills": skillbook.context(Ledger(app_folder), department) if app_folder.is_dir() else "None yet.",
            "services": _services_for(ledger, department)}


def _first_tool(department: str, message: str, tools: list[Any]) -> str | None:
    return "change_cad" if department == "Mechanical" and "no CAD model" in message and any(getattr(t, "__name__", "") == "change_cad" for t in tools) else None


async def run_lead(ledger: Ledger, gateway: Gateway, department: str, message: str, services: Services, nested: bool = False, attachments: Sequence[Any] = (), sent: dict[str, Any] | None = None, internal: bool = False) -> str:
    title = message.split("\n")[0] if message else "First proposal"
    async with jobs.slot(ledger, department, title, jobs.touches(ledger, department, message) if message else {jobs.UNKNOWN}, [message] if message else [], wait=not nested) as job:  # Read the project after any wait, so nothing is stale.
        tools = _lead_tools(ledger, gateway, department, services, nested)
        if not nested:
            tools.append(department_tool(ledger, gateway, department, services))
        tools.append(request_department_tool(ledger, department))
        tools.append(request_tool_tool(ledger, department))
        tools.append(hand_off_tool(ledger, department))
        sent = sent or protocol.command("BRIEF" if not message else "QUESTION" if nested else "TASK", [message] if message else [], "You've answered or finished what was asked.")
        ledger.append(SYSTEM, "command.sent", department=department, **sent)
        tier, turns = ROUTINE.get(sent["type"], (None, LEAD_TURNS)) if not attachments else (None, LEAD_TURNS)
        reply: Any = await gateway.run(agent=LEADS[department], tools=tools, request_limit=turns, tier=tier, attachments=attachments, output_type=protocol.Report, first_tool=_first_tool(department, message, tools), values={**_shared_context(ledger, department, services), "message": message, "command": sent})
        report = protocol.shorten(protocol.from_reply(reply))
        job.outcome = report.type
        _say(ledger, department, "agent", report.for_person, details=report.details, internal=internal)
        _act_on_report(ledger, department, sent, report, nested)
    return report.for_person


async def run_clones(ledger: Ledger, gateway: Gateway, department: str, groups: list[list[str]], services: Services, kind: str, done_when: str, heading: str) -> None:
    """One clone of the Lead per group. All start from the same copy of its context, so only the first pays full price for it."""
    shared = _shared_context(ledger, department, services)

    async def one(index: int, items: list[str]) -> tuple[protocol.Report, dict[str, Any]]:
        if index:
            await asyncio.sleep(CLONE_STAGGER_S)
        text = heading + "\n" + "\n".join(f"- {i}" for i in items)
        sent = protocol.command(kind, items, done_when, PARALLEL)
        ledger.append(SYSTEM, "command.sent", department=department, **sent)
        async with jobs.slot(ledger, department, items[0], set().union(*(jobs.touches(ledger, department, i) for i in items)), items, clone=index + 1) as job:
            tools = _lead_tools(ledger, gateway, department, services)
            reply: Any = await gateway.run(agent=LEADS[department], tools=tools, request_limit=LEAD_TURNS, output_type=protocol.Report, first_tool=_first_tool(department, text, tools), values={**shared, "message": text, "command": sent})
            report = protocol.from_reply(reply)
            job.outcome = report.type
            return report, sent

    outcomes = await asyncio.gather(*(one(i, g) for i, g in enumerate(groups)), return_exceptions=True)
    finished = [o for o in outcomes if not isinstance(o, BaseException)]
    for report, sent in sorted(finished, key=lambda o: o[0].type == "BUG"):  # Filed last, so a real fault stays the department's latest report.
        _act_on_report(ledger, department, sent, report, False)
    if finished:
        joined = protocol.shorten(finished[0][0].model_copy(update={"for_person": " ".join(r.for_person for r, _s in finished), "details": "\n\n".join(r.details for r, _s in finished if r.details)}))
        _say(ledger, department, "agent", joined.for_person, details=joined.details)
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome


def work_view(ledger: Ledger) -> dict[str, Any]:
    """What everyone is doing now, for the Project Network."""
    events, current = ledger.events(), jobs.live(ledger)
    waiting = {r["from"] for r in requests_view(events) if r["status"] == "waiting_for_you"}
    departments = []
    for department in shape.team(ledger.state()):
        mine = [j for j in current["jobs"] if j["department"] == department]
        report = protocol.latest(events, department)
        state = "held" if protocol.is_held(events, department) else "working" if any(j["status"] == "running" for j in mine) else "queued" if mine else "problem" if protocol.bug_open(events, department) else "waiting_for_you" if department in waiting else "blocked" if report and report["type"] == "BLOCKED" else "idle"
        departments.append({"name": department, "state": state, "jobs": mine, "lastReport": report["type"] if report else None, "lastSummary": report["for_person"] if report else "", "questions": [r["question"] for r in requests_view(events) if r["status"] == "waiting_for_you" and r["from"] == department]})
    return {"departments": departments, "agents": current["agents"], "running": current["running"], "recent": jobs.recent(events)}


async def talk_to_department(ledger: Ledger, gateway: Gateway, department: str, message: str, services: Services, attachments: Sequence[Any] = ()) -> str:
    if department not in DEPARTMENTS:
        raise HeadquartersError("Unknown department.")
    if not shape.on_team(ledger.state(), department):
        raise HeadquartersError(f"{department} isn't on this project's team. Add it on the Journey screen first.")
    if not message.strip():
        raise HeadquartersError("Type a message.")
    _say(ledger, department, "you", message.strip())
    return await run_lead(ledger, gateway, department, message.strip(), services, attachments=attachments)


async def talk_to_concept(ledger: Ledger, gateway: Gateway, message: str, services: Services) -> None:
    await hq_agents.run_concept(ledger, gateway, message, [department_tool(ledger, gateway, "Headquarters", services)])


def hand_off_tool(ledger: Ledger, sender: str) -> Callable[..., Any]:
    def hand_to_person(tool: str, title: str, what_it_changes: str, filename: str, script: str, steps: str, report_back: str) -> dict[str, Any]:
        """Hand the person a script or file to run in a tool they operate themselves, such as Altium, following your Skill for it.
        what_it_changes: in plain words, what running it will change in their design. filename: a plain name such as export_bom.pas.
        steps: the steps to run it, separated by ' | '. report_back: what to send back, such as the Messages panel text or the exported netlist.
        It appears as a card on your department's screen; when the person reports back, you're told."""
        from . import skills as skillbook

        try:
            return skillbook.hand_off(ledger, sender, tool, title, what_it_changes, filename, script, steps.split("|"), report_back, Actor.agent(sender.lower()))
        except skillbook.SkillError as exc:
            return {"error": str(exc)}

    return hand_to_person


def request_tool_tool(ledger: Ledger, sender: str) -> Callable[..., str]:
    def request_tool(capability: str, why: str) -> str:
        """Ask Reception for a tool this project doesn't have, such as structural analysis, a parts-pricing API or a circuit simulator.
        capability is what you need done, in a few words; why is what you'll use it for. The person sees it at Reception, where External
        and Internal Service help set it up. Until then, carry on with what you have and say what the missing tool would settle."""
        from . import services as reception

        answer = reception.ask(Ledger(ledger.root.parent / "_app"), ledger.root.name, sender, capability, why)
        if answer.startswith("Asked"):
            found = [reception.BY_ID[i]["group"] for i in reception.providers_of(capability) if i in reception.BY_ID]
            ledger.append(Actor.system("reception"), "tool.requested", department=sender, capability=capability.strip()[:120], why=why.strip()[:400],
                          service="External Service" if found and found[0] in ("cloud", "app") else "Internal Service")
        return answer

    return request_tool


def request_department_tool(ledger: Ledger, sender: str) -> Callable[..., str]:
    def request_department(department: str, why: str) -> str:
        """Ask the person to add a department this project doesn't have yet, such as Procurement once parts must be bought.
        Say specifically what you need it to do. The person decides; until then, carry on with what you can."""
        return shape.request_department(ledger, department.strip(), why, Actor.agent(sender.lower()))

    return request_department


def approve_concept(ledger: Ledger, actor: Actor, team: list[str] | None = None, gate_plan: list[dict[str, Any]] | None = None) -> None:
    """This is the step that turns a draft discussion into project truth. The team and gates the person chose come with it."""
    state = ledger.state()
    if concept_approved(ledger):
        raise HeadquartersError("The concept is already approved.")
    drafts = [d for d in state.of_type("concept_draft") if "summary" in d["fields"]]
    if not drafts:
        raise HeadquartersError("The Concept Partner hasn't summarised a concept yet.")
    if team is not None or gate_plan is not None:
        try:
            shape.set_shape(ledger, team, gate_plan, "Chosen with the concept.", actor)
        except (shape.ShapeError, gates.GateError) as exc:
            raise HeadquartersError(str(exc)) from exc
        state = ledger.state()
    latest = drafts[-1]["fields"]
    for text in latest["decisions"]:
        text = re.sub(r"^(proposed|tentative|draft)\s*:\s*", "", text.strip(), flags=re.I)
        record = ledger.propose(actor, "decision", text=text[:1].upper() + text[1:], source="concept")
        ledger.append(actor, "record.confirmed", id=record)
    fill = {key: latest["summary"] if key == "aim" else "As approved in the concept." for key in ("aim", "scope") if not state.meta.get(key, "").strip()}
    if fill:
        ledger.append(actor, "project.updated", **fill)
    ledger.append(actor, "concept.approved", summary=latest["summary"], decisions=latest["decisions"], open_questions=latest.get("open_questions", []))
    if gates.current_gate(ledger.state()) == "G0":
        gates.approve(ledger, "G0", actor)


def answer_request(ledger: Ledger, request_id: str, text: str, actor: Actor) -> None:
    request = next((r for r in requests_view(ledger.events()) if r["id"] == request_id), None)
    if not request or request["status"] != "waiting_for_you":
        raise HeadquartersError("That question isn't waiting for an answer.")
    if not text.strip():
        raise HeadquartersError("Type an answer.")
    ledger.append(actor, "request.person_answered", id=request_id, answer=text.strip())


def retry(ledger: Ledger, actor: Actor) -> None:
    ledger.append(actor, "hq.retry")


def waive_research(ledger: Ledger, reason: str, actor: Actor) -> None:
    if len(reason.strip()) < 8:
        raise HeadquartersError("Say why this can go ahead without sources.")
    ledger.append(actor, "research.waived", reason=reason.strip())
    ledger.append(actor, "hq.retry")  # Going ahead without sources also clears a failed search.


# ---- Running the next steps ----


async def run_step(ledger: Ledger, gateway: Gateway, action: str, services: Services) -> None:
    state, events = ledger.state(), ledger.events()
    if action == "concept":
        await talk_to_concept(ledger, gateway, "", services)
    elif action == "research":
        approved = next((e["data"] for e in reversed(events) if e["kind"] == "concept.approved"), {})
        focus = f"{approved.get('summary', '')} Decisions: {'; '.join(approved.get('decisions', []))}".strip()
        from .overview import ProjectIndex

        await hq_agents.run_research(ledger, gateway, ProjectIndex(ledger, services.papers), focus)
    elif action == "plan":
        result = await hq_agents.run_plan(ledger, gateway)
        if result["problems"]:
            raise HeadquartersError("The plan had problems the Controller couldn't fix: " + " ".join(result["problems"]))
    elif action == "revise":
        fixes = fixes_to_apply(state, events)
        gaps = [f for f in fixes if f["fields"].get("kind") != "beyond_scope"]
        extra = [f for f in fixes if f["fields"].get("kind") == "beyond_scope"]
        feedback = ("Revise the plan to fix these problems found in review:\n" + "\n".join(f"- {f['fields']['text']}" for f in gaps) + "\n" if gaps else "") \
            + ("The person asked to remove these, because they go beyond what they asked for. Remove each one, or narrow it to exactly what they asked, and don't add anything in its place:\n" + "\n".join(f"- {f['fields']['text']}" for f in extra) if extra else "")
        result = await hq_agents.run_plan(ledger, gateway, feedback=feedback.strip(), addresses=[f["id"] for f in fixes])
        if result["problems"]:
            raise HeadquartersError("The revised plan had problems the Controller couldn't fix: " + " ".join(result["problems"]))
    elif action == "verify":
        await hq_agents.run_verifier(ledger, gateway)
    elif action == "plan_tests" or action.startswith("plan_tests:"):
        await traceability.plan_tests(ledger, gateway)
    elif action.startswith("review_change:"):
        change_id = action.split(":", 1)[1]
        if not next(c for c in changes.view(ledger) if c["id"] == change_id)["review"]:
            await changes.review(ledger, gateway, change_id)
        await changes.triage(ledger, gateway, change_id)
    elif action.startswith("deliver:"):
        ids = action.split(":", 1)[1].split(",")
        answered = [r for r in requests_view(events) if r["id"] in ids]
        text = "The person answered through Headquarters.\n\n" + "\n\n".join(f"You asked: {r['question']}\n\nAnswer: {r['answer']}" for r in answered)
        sender = answered[0]["from"]
        if sender in DEPARTMENTS:
            _say(ledger, sender, "request", text, "You")
            await run_lead(ledger, gateway, sender, text, services, nested=True)
        else:
            await talk_to_concept(ledger, gateway, text, services)
        for request_id in ids:
            ledger.append(SYSTEM, "hq.finished", step=f"deliver:{request_id}", label="Delivered")
    elif action.startswith("brief_change:"):
        _, change_ids, department = action.split(":")
        ids = change_ids.split(",")
        text = "\n\n".join(changes.briefing_text(ledger, change_id, department) for change_id in ids)
        _say(ledger, department, "request", text, "Headquarters")
        await run_lead(ledger, gateway, department, text, services, nested=True, sent=protocol.command("CHANGE_NOTICE", [f"Change {i}" for i in ids], "Your work matches the approved changes."))
        for change_id in ids:
            ledger.append(SYSTEM, "hq.finished", step=f"brief_change:{change_id}:{department}", label="Briefed")
    elif action == "ci":
        await ci.run_pending(ledger)
    elif action.startswith("gate_review:"):
        await hq_agents.run_gate_review(ledger, gateway, action.split(":")[1])
    elif action.startswith("relay:"):
        data = next(e["data"] for e in events if e["id"] == action.split(":")[1])
        await department_tool(ledger, gateway, data["sender"], services)(data["to"], data["what"], f"{data['sender']}'s work, which is waiting on it")
    elif action.startswith("recheck:"):
        data = next(e["data"] for e in events if e["id"] == action.split(":")[1])
        problems = [p for s in ci.view(ledger, gates.current_gate(state)) if s["owner"] == data["department"] for p in s["problems"]]
        text = f"Please check your work again. {data['reason']}".strip()
        _say(ledger, data["department"], "request", text, "You")
        await run_lead(ledger, gateway, data["department"], text, services, sent=protocol.command("RECHECK", problems, "Every item passes the automatic checks.", data["reason"]))
    elif action.startswith("drive:"):
        _, gate, department, _token = action.split(":")
        items = _owned_work(ledger, state, gate).get(department, [])
        if items:
            tried = any(step.startswith(f"drive:{gate}:{department}:") for step in {e["data"]["step"] for e in events if e["kind"] == "hq.finished"})
            heading = f"The {gates.stage(gate, state)} stage still needs this from {department}:"
            text = heading + "\n" + "\n".join(f"- {i}" for i in items)
            _say(ledger, department, "request", text, "Headquarters")
            kind, done_when = "RECHECK" if tried else "TASK", "Every item passes the automatic checks."
            groups = jobs.split(ledger, department, items)
            if len(groups) > 1:
                await run_clones(ledger, gateway, department, groups, services, kind, done_when, heading)
            else:
                await run_lead(ledger, gateway, department, text, services, sent=protocol.command(kind, items, done_when))
    elif action.startswith("open:"):
        # Departments open in order and read each other's proposals, so they don't need to ask.
        await run_lead(ledger, gateway, action[5:], "", services, nested=True)
    else:
        raise HeadquartersError(f"Unknown step: {action}")


def label_for(action: str) -> str:
    if action == "plan_tests" or action.startswith("plan_tests:"):
        return "The Test Planner is writing tests for the requirements"
    if action.startswith("review_change:"):
        return "The reviewer is checking a change"
    if action.startswith("brief_change:"):
        return f"{action.split(':')[2]} is responding to an approved change"
    if action.startswith("deliver:"):
        return "Passing on your answer"
    if action == "ci":
        return "Running the automatic checks"
    if action.startswith("gate_review:"):
        return f"Reviewing the design for the {gates.stage(action.split(':')[1])} stage"
    if action.startswith("relay:"):
        return "Passing a request between departments"
    if action.startswith("recheck:"):
        return "A department is rechecking its work"
    if action.startswith("drive:"):
        _, gate, department, _token = action.split(":")
        return f"{department} is working on what the {gates.stage(gate)} stage needs"
    return f"{action[5:]} is preparing its first proposal" if action.startswith("open:") else LABELS[action]


async def advance(ledger: Ledger, gateway: Gateway, services: Services, guard: Callable[[Any], Awaitable[Any]] | None = None) -> None:
    """Runs steps until a person is needed. Each step takes the project's lock on its own, so chats can go between steps."""
    for _ in range(16):
        action = next_action(ledger)
        if action is None:
            return
        label = label_for(action)
        ledger.append(SYSTEM, "hq.started", step=action, label=label)
        try:
            work = run_step(ledger, gateway, action, services)
            await (guard(work) if guard else work)
        except Exception as exc:
            ledger.append(SYSTEM, "hq.failed", step=action, label=label, message=str(getattr(exc, "detail", "") or exc)[:300] or type(exc).__name__)
            return
        ledger.append(SYSTEM, "hq.finished", step=action, label=label)


def status(ledger: Ledger) -> dict[str, Any]:
    events = ledger.events()
    return {"eventCount": len(events), "needs": needs(ledger), "conceptApproved": concept_approved(ledger), "requests": requests_view(events)[-10:]}


def mark_part_proposal(ledger: Ledger, record_id: str, accept: bool, actor: Actor) -> None:
    record = ledger.state().records.get(record_id)
    if not record or record["type"] != "part_proposal" or record["status"] != "proposed":
        raise HeadquartersError("That suggestion isn't available.")
    if accept and record["fields"]["number"] in mechanical.parts_by_number(ledger.state()):
        accept = False  # The part is already on the list, so there's nothing to add.
    if accept:
        f = record["fields"]
        mechanical.add_part(ledger, {"number": f["number"], "name": f["name"], "material": f["material"], "process": f["process"], "qty": f["qty"], "mass_g": None, "unit_cost": None}, actor)
    ledger.append(actor, "record.confirmed" if accept else "record.rejected", id=record_id)
