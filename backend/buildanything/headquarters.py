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
from pydantic import BaseModel, Field

from . import shape, jobs, ci, devicefacts, documents, datasheets, designneeds, protocol, agents as hq_agents, changes, compat, depgraph, traceability, connectors as conn, firmware, gates, interfaces, mechanical, page_memory, procurement, software
from .citations import PaperIndex
from .gateway import Gateway, TurnLimitReached
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
        if event["kind"] == "hq.failed" and not event["data"].get("department"):  # a department's own job failing doesn't stop the others
            return event["data"]
    return None


def plan_version(events: list[dict[str, Any]]) -> int:
    return sum(1 for e in events if e["kind"] == "plan.written")


def _addressed(events: list[dict[str, Any]]) -> set[str]:
    return {f for e in events if e["kind"] == "plan.written" for f in e["data"].get("addresses", [])}


def settled_plan_context(state: ProjectState, events: list[dict[str, Any]], fixing: list[dict[str, Any]]) -> str:
    """For a revision: the plan as it stands, and what earlier reviews and the person already settled, so a rewrite
    changes only what the new problems need and doesn't undo earlier fixes."""
    current = [r for r in state.of_type("requirement") if r["status"] in ("proposed", "confirmed")]
    tasks = [t for t in state.of_type("task") if t["status"] in ("proposed", "confirmed")]
    fixing_ids = {f["id"] for f in fixing}
    done = _addressed(events)
    settled = [f for f in state.of_type("finding") if f["id"] not in fixing_ids and (f["id"] in done or (f.get("decision") or {}).get("decision") in ("fix", "fixed"))]
    decided = [f for f in state.of_type("finding") if (f.get("decision") or {}).get("decision") in ("dismiss", "accept")]  # "later" means no work now, so it isn't shown
    out = []
    if current or tasks:
        out.append("Start from the current plan below. Keep every requirement and task exactly as it is, word for word with the same key, unless one of the problems listed after it requires changing it; add only what those problems need.")
        out += [f"- {r['fields'].get('key')}: {r['fields'].get('text')} (Tested by: {r['fields'].get('verification', '')})" for r in current]
        out += [f"- Task {t['fields'].get('key')} ({t['fields'].get('department')}): {t['fields'].get('title')}" for t in tasks]
    if settled:
        out.append("These problems were fixed in earlier revisions. The plan must still satisfy every one of them:")
        out += [f"- {f['fields']['text'][:400]}" for f in settled]
    if decided:
        out.append("The person decided these; respect each decision exactly:")
        out += [f"- {f['fields']['text'][:300]} Decision: {f['decision']['decision']}. Their reason: {f['decision'].get('reason', '')}" for f in decided]
    return ("\n".join(out) + "\n\n") if out else ""


def fixes_to_apply(state: ProjectState, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Waits until every open high and medium finding has a decision, so the plan is revised once, not per click.
    A low finding marked Fix it is included then too."""
    deciding = gates.open_findings_to_decide(state)
    if any(not f.get("decision") for f in deciding):
        return []
    done = _addressed(events)
    opened = [f for f in state.of_type("finding") if f["status"] == "proposed" and f.get("decision")]
    return [f for f in opened if f["decision"]["decision"] == "fix" and f["id"] not in done]


AUTO_CI = True
GATE_REVIEW = True
_proposing = threading.Lock()  # An agent can call tools in parallel, which could otherwise add one part twice.
CLONE_STAGGER_S = 3  # The first clone goes slightly early, so the others find its context already saved at the cheaper rate.
PARALLEL = "Other jobs in your department are running at the same time. Stay within your items. look_up_project shows the current state and what the others are doing. If you need anything outside your items, report BLOCKED."
SIM_DEVICE = "simulation"  # the Simulation department's own program, in firmware/simulation/
MAX_CODE_WRITES = 4  # per Lead reply: the first pass plus up to three to finish what it left
MAX_DRIVES = 6  # Per department and gate, so an agent that can't finish can never spend without limit.
ROUTINE = {"QUESTION": ("Fast", 8), "CHANGE_NOTICE": ("Fast", 8)}  # Answering a question or acknowledging a change is routine: cheaper tier, fewer turns.
LEAD_TURNS = 12


def _criterion_owner(state: ProjectState, text: str, team: list[str]) -> str | None:
    """The department whose planned tasks best match a stage requirement the person will confirm."""
    words = _meaningful_words(text)
    scores: dict[str, float] = {}
    for task in state.of_type("task", "confirmed"):
        department = task["fields"].get("department")
        if department in team:
            overlap = len(words & _meaningful_words(task["fields"].get("title", "") + " " + task["fields"].get("description", "")))
            scores[department] = scores.get(department, 0) + overlap + 0.01  # owning tasks at all breaks ties
    return max(scores, key=lambda d: scores[d]) if scores else (team[0] if len(team) == 1 else None)


def own_remaining(events: list[dict[str, Any]], department: str) -> list[str]:
    """What a department said it still has to do itself, from its latest report on real work (not a reply to a question)."""
    report = next((e["data"] for e in reversed(events) if e["kind"] == "report.filed" and e["data"]["department"] == department and e["data"]["type"] != "ANSWER"), None)
    if not report or report["type"] not in ("PARTIAL", "BLOCKED"):
        return []
    return [n["what"] for n in report.get("needs", []) if n.get("owner") == department]


def _owned_work(ledger: Ledger, state: ProjectState, gate: str, own: bool = True) -> dict[str, list[str]]:
    from . import gatereport

    out: dict[str, list[str]] = {}
    team = shape.team(state)
    try:
        planning = gates.phase(state, gate) == "G2"  # in Plan, only the work behind the person's own items, never Build work
    except gates.GateError:
        planning = False
    for owner, text in [] if planning else gatereport.blockers(gate, state, ledger):
        if owner in team:
            out.setdefault(owner, []).append(text)
    # A requirement the person confirms has to be made true first, by the department whose tasks it matches.
    for criterion in gates.criteria_view(state, gate):
        if not criterion["met"]:
            owner = _criterion_owner(state, criterion["text"], team)
            if owner:
                out.setdefault(owner, []).append(f"Make this work and show how you checked it, so the person can confirm it: {criterion['text']}")
    events = ledger.events()
    for department in [] if planning or not own else team:
        for what in own_remaining(events, department):
            if what not in out.get(department, []):
                out.setdefault(department, []).append(what)
    return out


def _stalled(events: list[dict[str, Any]], department: str) -> bool:
    """Two rounds of work in a row that finished nothing: a person hears about it instead of paying for a third."""
    work = [e["data"] for e in events if e["kind"] == "report.filed" and e["data"]["department"] == department and e["data"]["command"] in ("TASK", "RECHECK")][-2:]
    return len(work) == 2 and all(not r.get("done") for r in work)


def _stage_mark(ledger: Ledger, state: ProjectState, gate: str, department: str) -> str:
    """What the stage itself still needs from a department, without the department's own to-do list (which it rewords
    every round). If two rounds of work leave this unchanged, more rounds won't change it either."""
    items = sorted(_owned_work(ledger, state, gate, own=False).get(department, []))
    return hashlib.sha256(json.dumps(items).encode()).hexdigest()[:10] if items else ""


def stuck_on_stage(events: list[dict[str, Any]], gate: str, department: str, mark: str) -> bool:
    """Two rounds in a row that left the stage's needs exactly as they were, with nothing new from the person since."""
    if not mark:
        return False
    marks = [(i, e["data"]["mark"]) for i, e in enumerate(events) if e["kind"] == "hq.drive_mark" and e["data"]["gate"] == gate and e["data"]["department"] == department]
    if len(marks) < 2 or marks[-1][1] != mark or marks[-2][1] != mark:
        return False
    last = marks[-1][0]
    if any(e["kind"] == "job.started" and e["data"].get("department") == department for e in events[last:]) and not any(e["kind"] == "job.finished" and e["data"].get("department") == department for e in events[last:]):
        return False  # the second round is still running
    return not any(e["actor"].get("kind") == "human" or (e["kind"] == "service.note" and e["data"]["department"] == department) for e in events[last + 1:])  # anything the person or Reception gave since is a new fact


def _drives_since_person(events: list[dict[str, Any]], gate: str, department: str) -> int:
    """Rounds given to a department for this stage since the person last did anything (or Reception sent it guidance):
    what the person gives is new, so the limit starts again from there."""
    count = 0
    for e in reversed(events):
        if e["actor"].get("kind") == "human" or (e["kind"] == "service.note" and e["data"].get("department") == department):
            break
        if e["kind"] == "hq.finished" and e["data"].get("step", "").startswith(f"drive:{gate}:{department}:"):
            count += 1
    return count


def _drive(ledger: Ledger, state: ProjectState, done: set[str], busy: set[str] | None = None) -> str | None:
    """Gives each department what the gate needs from it. It runs again only when the facts have changed."""
    gate = gates.current_gate(state)
    if gate is None or gates.phase(state, gate) in ("G0", "G1"):
        return None
    if gates.phase(state, gate) == "G2" and (state.of_type("requirement", "proposed") or state.of_type("task", "proposed") or not state.of_type("task", "confirmed")):
        return None  # in Plan, only once the plan itself is agreed
    events = ledger.events()
    waiting_on_person = {r["from"] for r in requests_view(events) if r["status"] == "waiting_for_you"}
    for department, items in _owned_work(ledger, state, gate).items():
        if designneeds.deferred(ledger, department) or department in (busy or set()):
            continue
        if protocol.is_held(events, department) or protocol.bug_open(events, department) or department in waiting_on_person or _stalled(events, department):
            continue
        if stuck_on_stage(events, gate, department, _stage_mark(ledger, state, gate, department)):
            continue  # shown to the person in Needs you instead of paying for more rounds
        # The same list twice means the agent already tried, so a person hears about it instead of paying again. A permission
        # the person granted since (such as letting the app run the program's code) is a new fact, so the work is tried again.
        granted = sum(1 for e in events if e["kind"] == "software.autotest" and e["data"].get("allowed"))
        token = hashlib.sha256(json.dumps([items, granted]).encode()).hexdigest()[:10]
        action = f"drive:{gate}:{department}:{token}"
        if action not in done and _drives_since_person(events, gate, department) < MAX_DRIVES:
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


def docs_to_fetch(ledger: Ledger, state: ProjectState) -> list[dict[str, Any]]:
    """Confirmed parts still waiting on a document whose proposal gave a direct PDF link the app hasn't tried yet."""
    from . import documents

    tried = {(e["data"]["component"], e["data"]["url"]) for e in ledger.events() if e["kind"] == "documents.fetch_tried"}
    have = {cid for (cid, _kind) in documents.latest_docs(state)}
    return [c for c in state.of_type("component", "confirmed") if c["fields"].get("datasheet_url", "").startswith("http")
            and c["id"] not in have and (c["id"], c["fields"]["datasheet_url"]) not in tried and documents.status(ledger, state, c)["missing"]]


async def fetch_docs(ledger: Ledger) -> None:
    """Saves the documents of confirmed parts from the PDF links given when they were proposed, so nobody has to upload them."""
    from . import citations, datasheets

    for part in docs_to_fetch(ledger, ledger.state()):
        url = part["fields"]["datasheet_url"]
        try:
            data = await asyncio.to_thread(datasheets.fetch_pdf, url)
            saved = datasheets.save_pdf(ledger, part["id"], data, url.rsplit("/", 1)[-1][:120] or "datasheet.pdf", "datasheet", Actor.system("documents"), source_url=url, fetched_by="Headquarters")
            ledger.append(SYSTEM, "documents.fetch_tried", component=part["id"], url=url, ok=True, matches=bool(saved["part_match"]))
        except (citations.PageError, OSError, ValueError) as exc:
            ledger.append(SYSTEM, "documents.fetch_tried", component=part["id"], url=url, ok=False, message=str(exc)[:200])


def paused(events: list[dict[str, Any]]) -> bool:
    """The person paused the project: jobs already running finish, nothing new starts until they resume it."""
    for e in reversed(events):
        if e["kind"] == "hq.paused":
            return bool(e["data"].get("paused"))
    return False


# ---- Office hours: agents answer the person at once, but new work waits until the person has finished a round ----

QUIET_SECONDS = 60
RELEASES = ("hq.send_now", "hq.paused", "limit.changed")  # these go through at once and release what's waiting
OFFICE = ("hq.send_now", "hq.office")


def _seconds(at: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(at).timestamp()


def outbox_wait(events: list[dict[str, Any]], now: float | None = None) -> float | None:
    """Seconds until Headquarters starts new work after the person's last action, 0 when it may start now, or None while
    the person holds everything until they press Send. Running jobs carry on either way; chats are answered at once."""
    import time

    now = time.time() if now is None else now
    last_action = next((e for e in reversed(events) if e["actor"]["kind"] == "human" and e["kind"] not in OFFICE), None)
    released = next((e for e in reversed(events) if e["kind"] in RELEASES and e["actor"]["kind"] == "human"), None)
    if last_action is None or (released is not None and _seconds(released["at"]) >= _seconds(last_action["at"])):
        return 0.0
    hold = next((e["data"]["hold"] for e in reversed(events) if e["kind"] == "hq.office"), False)
    if hold:
        return None
    return max(0.0, QUIET_SECONDS - (now - _seconds(last_action["at"])))


def outbox_view(ledger: Ledger) -> dict[str, Any]:
    events = ledger.events()
    wait = outbox_wait(events)
    return {"wait": None if wait is None else round(wait), "hold": next((e["data"]["hold"] for e in reversed(events) if e["kind"] == "hq.office"), False),
            "pending": wait != 0 and next_action(ledger) is not None, "quietSeconds": QUIET_SECONDS}


def queue_followup(ledger: Ledger, department: str, text: str) -> None:
    """The result of a step a department asked for, such as an approved script's output. It's delivered with any others
    for that department in one message, at the department's next turn after the person's round."""
    ledger.append(SYSTEM, "dept.followup_queued", department=department, text=text[:30000])


def _followups_due(events: list[dict[str, Any]]) -> dict[str, list[str]]:
    sent = {i for e in events if e["kind"] == "dept.followup_sent" for i in e["data"]["ids"]}
    out: dict[str, list[str]] = {}
    for e in events:
        if e["kind"] == "dept.followup_queued" and e["id"] not in sent:
            out.setdefault(e["data"]["department"], []).append(e["id"])
    return out


def next_action(ledger: Ledger, running: set[str] | None = None, busy: set[str] | None = None) -> str | None:
    """The next step Headquarters should take. Steps in running are already under way; departments in busy are working,
    so they get nothing new until they finish, while other departments can be started."""
    running, busy = running or set(), busy or set()
    state, events = ledger.state(), ledger.events()
    if failure(events) or paused(events):
        return None
    waiting = changes.waiting_review(ledger)
    if waiting and f"review_change:{waiting[0]}" not in running:
        return f"review_change:{waiting[0]}"
    done = {e["data"]["step"] for e in events if e["kind"] == "hq.finished"} | running
    for sender, ids in _answers_to_deliver(events, done).items():
        if sender not in busy:
            return f"deliver:{','.join(ids)}"
    for department, ids in _followups_due(events).items():
        step = f"followup:{department}:{ids[-1]}"
        if department not in busy and step not in running:
            return step
    due = [(c, d) for c, d in changes.briefings_due(ledger, done) if d not in busy]
    if due:
        department = due[0][1]  # One message per department, however many changes were approved together.
        step = f"brief_change:{','.join(c for c, d in due if d == department)}:{department}"
        if step not in running:
            return step
    for relay in [e for e in events if e["kind"] == "relay.pending"]:
        if f"relay:{relay['id']}" not in done and relay["data"].get("to") not in busy:
            return f"relay:{relay['id']}"
    for asked in [e for e in events if e["kind"] == "command.person" and e["data"]["type"] == "RECHECK"]:
        if f"recheck:{asked['id']}" not in done and asked["data"].get("department") not in busy:
            return f"recheck:{asked['id']}"
    for brief in tab_briefs(ledger, state):  # a tab on this project reads files a department hasn't made yet
        step = f"tab_brief:{brief['tab']}:{brief['version']}:{brief['department']}"
        if step not in done and brief["department"] not in busy:
            return step
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
    opened = {e["data"]["step"] for e in events if e["kind"] == "hq.finished"} | running
    for department in shape.team(state):
        if department in busy:
            continue
        if f"open:{department}" not in opened and not designneeds.declared_none(ledger, department) and not designneeds.deferred(ledger, department):  # No paying for a proposal nobody needs yet.
            return f"open:{department}"
    if traceability.uncovered(state) and "plan_tests" not in opened and not changes.waiting_review(ledger) and not any(r.startswith("open:") for r in running):
        return "plan_tests"
    answers = traceability.clarifications(events)
    waiting_tests = {traceability._key(r): answers[traceability._key(r)] for r in traceability.uncovered(state) if traceability._key(r) in answers}
    if waiting_tests:
        step = "plan_tests:" + hashlib.sha256(json.dumps(waiting_tests, sort_keys=True).encode()).hexdigest()[:10]
        if step not in opened:
            return step
    if AUTO_CI and gates.current_gate(state) and ci.pending(ledger) and "ci" not in running:
        return "ci"
    if docs_to_fetch(ledger, state) and "fetch_docs" not in running:
        return "fetch_docs"
    work = _drive(ledger, state, opened, busy)
    if work:
        return work
    gate = gates.current_gate(state)
    if GATE_REVIEW and gates.phase(state, gate) in ("G4", "G5", "G6") and f"gate_review:{gate}" not in opened:
        from . import gatereport

        if not gatereport.blockers(gate, state, ledger) and not running:  # the whole design is reviewed once everyone has stopped
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
    if paused(events):
        out.append({"kind": "paused", "text": "Paused: departments finish what they're doing and start nothing new. Resume when you're ready."})
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
            gate = "G1" if "G1" in gates.order(state) else gates.current_gate(state) or "G1"
            out.append({"kind": "gate", "gate": gate, "text": f"Sources are reviewed. {gates.continue_text(gate, state)}{' to start the plan' if gates.next_stage(gate, state) == gates.stage('G2', state) and 'G2' in gates.order(state) else ''}."})
        return out
    if not gates.passed(state, "G2"):
        if next_action(ledger):
            return out
        plan = state.of_type("requirement", "proposed") + state.of_type("task", "proposed")
        high = gates.open_high_findings(state)
        undecided = [f for f in gates.open_findings_to_decide(state) if not f.get("decision")]
        if undecided:
            serious = sum(1 for f in undecided if f["fields"]["severity"] == "high")
            out.append({"kind": "findings", "text": f"Decide {len(undecided)} problems the Verifier found in the plan{f' ({serious} serious)' if serious else ''}, under Independent Verifier below. The plan is revised once they're all decided."})
        fixing = [f for f in high if f.get("decision", {}).get("decision") == "fix"]
        if fixing:
            out.append({"kind": "findings", "text": f"Check that {len(fixing)} fixes worked, then mark them fixed."})
        if plan:
            out.append({"kind": "plan", "text": f"Review {len(plan)} requirements and tasks."})
        if not out and gates.missing("G2", state):  # such as a requirement the person wrote for this stage
            out.append({"kind": "gate_waiting", "text": f"To finish {gates.stage('G2', state)}: {gates.missing('G2', state)[0]}", "goto": "Journey"})
        if not out and not gates.missing("G2", state):
            gate = "G2" if "G2" in gates.order(state) else gates.current_gate(state) or "G2"
            out.append({"kind": "gate", "gate": gate, "text": f"The plan is reviewed. {gates.continue_text(gate, state)} to brief the departments."})
        return out
    for department in shape.team(state):
        report = protocol.latest(events, department)
        if protocol.bug_open(events, department) and not designneeds.declared_none(ledger, department):
            out.append({"kind": "bug", "text": f"{department} found a tool problem: {(report['risk'] or report['for_person'])[:200]}", "goto": department})
    gate = gates.current_gate(state)
    if gate and not next_action(ledger):
        for department, items in _owned_work(ledger, state, gate, own=False).items():
            if stuck_on_stage(events, gate, department, _stage_mark(ledger, state, gate, department)):
                out.append({"kind": "stuck", "goto": department, "text": f"{department} has tried twice and the {gates.stage(gate, state)} stage hasn't moved, so it has stopped to save money. Still needed: {'; '.join(i[:120] for i in items[:3])}. Tell {department} what to do, or provide what it's missing, and it tries again."})
    questions = [r for r in requests_view(events) if r["status"] == "waiting_for_you"]
    if questions:
        more = f" And {len(questions) - 1} more in the Review queue." if len(questions) > 1 else ""
        out.append({"kind": "question", "text": f"{questions[0]['from']} needs an answer from you: {questions[0]['question'][:180]}{more}"})
    decided = {e["data"].get("device") for e in events if e["kind"] == "software.autotest"}
    for device in firmware.devices(ledger.root):
        if device not in decided and firmware.is_computer(ledger.root, device) and any(not f.get("generated") for f in firmware.scan_workspace(ledger.root, device)):
            if device == SIM_DEVICE:
                out.append({"kind": "autotest", "device": device, "text": "Let the app run the simulation's code? Then Simulation runs its simulations itself and saves the results, instead of you running them. It installs the simulation's requirements.txt into this project's Python environment. Allow 'Run code and tests' for it in Settings.", "goto": "Settings"})
            else:
                out.append({"kind": "autotest", "device": device, "text": f"Let the app run the {device} program's code, builds and tests? Then Software builds and tests after each write and fixes what fails, instead of you running them. Python packages go into this project's environment; build tools run as they are on this computer. Allow 'Run code and tests' for it in Settings.", "goto": "Settings"})
            break
    app_root = ledger.root.parent / "_app"
    if app_root.is_dir() and concept_approved(ledger):
        from . import tabideas

        from . import tabs

        for wrong in tabs.mismatches(app_root, Ledger(app_root), ledger.root):
            first = wrong["reads"][0]
            out.append({"kind": "tab_mismatch", "key": wrong["tab"], "text": f"The {wrong['title']} tab doesn't match this project's files: it looks for {first['path']}, but this project has {', '.join(first['project_has'][:3])}. Ask {wrong['service']} to update the tab to find this project's files.", "goto": wrong["service"]})
        ideas = tabideas.candidates(app_root, Ledger(app_root), ledger.root, shape.team(state))
        if ideas:
            idea = ideas[0]
            more = f" {len(ideas) - 1} more tab ideas: {', '.join(i['tool'] for i in ideas[1:4])}." if len(ideas) > 1 else ""
            out.append({"kind": "tab_idea", "key": idea["key"], "text": f"Make a tab in {idea['department']} for {idea['tool']}? It could show {idea['could_show']}. Ask {idea['service']}.{more}", "goto": idea["service"]})
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
    return [{"role": e["data"]["role"], "text": e["data"]["text"], "from": e["data"].get("from"), "at": e["at"], "early": i < approved, "details": e["data"].get("details", ""), "choices": e["data"].get("choices", []), "internal": bool(e["data"].get("internal")) or (e["data"]["role"] == "request" and e["data"].get("from") != "You")} for i, e in enumerate(events) if e["kind"] == "dept.message" and e["data"]["department"] == department]


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


def _say(ledger: Ledger, department: str, role: str, text: str, sender: str | None = None, details: str = "", internal: bool = False, choices: Sequence[str] = ()) -> None:
    actor = Actor.human() if role == "you" else SYSTEM
    picks = [c.strip()[:200] for c in choices if c.strip()][:4]
    ledger.append(actor, "dept.message", department=department, role=role, text=text, **({"from": sender} if sender else {}), **({"details": details} if details else {}), **({"internal": True} if internal else {}), **({"choices": picks} if picks and not internal else {}))


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
            earlier = next((r for r in reversed(requests_view(ledger.events())) if r["status"] == "answered" and r.get("answer")
                            and (r["question"].startswith(f"{sender} and {department} each need the other") or r["question"].startswith(f"{department} and {sender} each need the other"))), None)
            if earlier:
                return f"Headquarters: the person already decided who does this between you and {department}: \"{earlier['answer'][:400]}\". Follow that; don't ask again."
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
        if target in jobs.busy(ledger):  # two jobs in one department would write over each other's work
            ledger.append(SYSTEM, "request.routed", id=request_id, to=target, action="forward", reason=f"{route.reason} {target} is busy, so it's queued.")
            ledger.append(SYSTEM, "request.answered", id=request_id, answer=f"Queued: {target} is busy with other work. Headquarters will pass this on as soon as it's free.", outcome="QUEUED")
            ledger.append(SYSTEM, "relay.pending", sender=sender, to=target, what=question, why=why)
            return f"{target} is busy with other work right now, so Headquarters queued your request and will pass it on as soon as {target} is free. Carry on with everything you can do without it."
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


def change_tool(ledger: Ledger, department: str, person_said: str = "") -> Callable[..., dict[str, str]]:
    def raise_change_request(title: str, why: str, kind: str, target: str, details_json: str) -> dict[str, str]:
        """Ask for a change to something already agreed. A person decides. kind is part, component, requirement, add_requirement, drop_requirement, deviation, packet, middleware or other. target is the part number, record ID or packet name, or device:src/mid/header.h for middleware; empty for add_requirement. details_json is a JSON object, such as {"material": "PETG"} for a part, {"part_number": "MG90S"} for a component, {"text": "new wording"} for a requirement, {"text": "the new requirement", "verification": "how it will be tested"} for add_requirement, or {"what": "what isn't met", "restores": "what would put it right"} for a deviation."""
        try:
            details = json.loads(details_json or "{}")
        except ValueError:
            return {"error": "details_json must be a JSON object."}
        details = details if isinstance(details, dict) else {}
        if person_said and kind in ("add_requirement", "requirement"):
            details["person_said"] = person_said[:600]  # the person's own words in this chat, so the requirement is credited to them
        try:
            change_id = changes.propose(ledger, title, why, kind, target, details, Actor.agent(department.lower()))
        except changes.ChangeError as exc:
            return {"error": str(exc)}
        number = next((c["number"] for c in changes.view(ledger) if c["id"] == change_id), "")
        return {"id": change_id, "status": f"Change {number} is waiting on the Changes screen. An independent review checks it first, then the person approves or rejects it. Nothing has changed yet."}

    return raise_change_request


SKIP_DIRS = {"ledger", "__pycache__", ".venv", "node_modules", ".git", "generated", "backups"}


def project_files(root: Path, limit: int = 120) -> dict[str, Any]:
    """What's in the project folder, by folder: files anyone added, including the person. Big folders are counted, not listed."""
    out: dict[str, Any] = {}
    for folder in sorted({p.parent for p in root.rglob("*") if p.is_file() and not (set(p.relative_to(root).parts) & SKIP_DIRS)}):
        files = sorted(p.name for p in folder.iterdir() if p.is_file())
        key = folder.relative_to(root).as_posix() or "."
        kinds = sorted({Path(f).suffix for f in files})
        out[key] = files if len(files) <= 12 else f"{len(files)} files ({', '.join(kinds)}), e.g. {', '.join(files[:4])}"
        if len(out) >= limit:
            out["…"] = "more folders not listed"
            break
    return out


def _cad_files(ledger: Ledger) -> dict[str, Any] | None:
    """The CAD export in the project folder, if any, so Software's programs can read the real model's files."""
    meta_file = ledger.root / "mechanics" / "cad" / "meta.json"
    if not meta_file.exists():
        found = sorted((ledger.root / "mechanics" / "cad").rglob("*.urdf")) if (ledger.root / "mechanics" / "cad").is_dir() else []
        found = [p for p in found if p.name != "ba_prepared.urdf"]
        if not found:
            return None
        model = found[0].relative_to(ledger.root).as_posix()
        return {"model": model, "folder": found[0].parent.relative_to(ledger.root).as_posix(), "format": "urdf",
                "from_a_device_folder": f"../../{model}",
                "note": "A URDF the person added, with its meshes in the folder next to it. Programs in firmware/<device>/ reach it with the relative path given."}
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
    return {**_own_state(ledger, department, services), **scope, "project_files": project_files(ledger.root), "project_folder_on_this_computer": f"{ledger.root.resolve()} (programs on this computer, such as a CAD script, save results here with this full path; they don't start in it)", "other_departments_latest": others,
            "deliverables": "Every department's reports and files can be read with list_deliverables, read_deliverable and read_department_reports. Look there before asking for something."}


def _own_state(ledger: Ledger, department: str, services: Services) -> dict[str, Any]:
    state = ledger.state()
    if department == "Electrical":
        sheets = page_memory.latest_sheets(state)
        return {"components": [{"id": c["id"], "part": c["fields"]["part_number"], "role": c["fields"].get("role"), "status": c["status"], "datasheet": c["id"] in sheets} for c in state.of_type("component") if c["status"] != "rejected"], "nets": len(_active(state, "net")), "failed_checks": [c for c in compat.run_checks(state) if c.get("status") == "fail"][:10], "packets": interfaces.summary_for_agents(state), "needed_items": designneeds.view(ledger), "agreed_pins": firmware.pin_defines(state)}
    if department == "Mechanical":
        return {"no_mechanical_work": designneeds.declared_none(ledger, "Mechanical"), "parts": [p["fields"] for p in _active(state, "part")], "suggested_parts": [p["fields"] for p in state.of_type("part_proposal", "proposed")]}
    if department == "Simulation":
        events = ledger.events()
        runs = [{"script": e["data"]["script"], "args": e["data"]["args"], "ok": e["data"]["ok"], "made": e["data"].get("made", [])[:12], "at": e["at"]} for e in events if e["kind"] == "software.ran" and e["data"].get("device") == SIM_DEVICE][-8:]
        tests = [{"key": t["fields"].get("key"), "title": t["fields"].get("title"), "covers": t["fields"].get("covers"), "pass_if": t["fields"].get("pass_if"), "metric": t["fields"].get("metric"), "unit": t["fields"].get("unit"), "min": t["fields"].get("min"), "max": t["fields"].get("max"), "owner": t["fields"].get("owner")} for t in state.of_type("test", "confirmed")]
        return {"simulation_code": [f["path"] for f in firmware.scan_workspace(ledger.root, SIM_DEVICE) if not f.get("generated")] if (ledger.root / "firmware" / SIM_DEVICE).is_dir() else [],
                "product_code": {d: [f["path"] for f in firmware.scan_workspace(ledger.root, d) if not f.get("generated")] for d in firmware.devices(ledger.root) if d != SIM_DEVICE},
                "cad_model_files": _cad_files(ledger), "recent_runs": runs, "tests": tests,
                "can_run": software.autotest_allowed(ledger, SIM_DEVICE), "results_folder": "simulation/results/"}
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
        """Use a connected outside service listed in services: run one of its operations by name with params as a JSON object. Reading runs straight away; an operation that changes something waits for the person's approval, so say why. When you only need to look, use an operation that reads: running a read-only script through an operation that changes something still waits for the person. To do several steps with an operation that changes something, such as a few scripts in a row, send them together as a JSON list of params objects (up to 8, run in order, stopping at the first failure): the person approves them once and you get every result back at once."""
        store = cloud.store_for(ledger.root.parent / "_app")
        if store is None:
            return {"error": "No services are connected."}
        try:
            params = json.loads(params_json or "{}")
            m = store.approved(connection)
            if not m:
                mine = [c["connection"] for c in store.for_department(department)]
                return {"error": f"There's no outside service called {connection}. use_service is only for services listed in services ({', '.join(mine) or 'none for you yet'}). Your own project's records and files are read with look_up_project, list_deliverables and read_deliverable, which are always available."}
            if department not in m.departments:
                return {"error": f"{m.name} isn't connected for {department}. Ask External Service, through the person, to add {department} to it."}
            op = next((o for o in m.operations if o.name == operation), None)
            if op and op.writes:
                if not why.strip():
                    return {"error": "This changes something in the service, so give the person a reason in why."}
                return await store.request_write(connection, operation, params, why, ledger.root.name, department, Actor.agent(department.lower()))
            return await store.call(connection, operation, params, Actor.agent(department.lower()), ledger.root.name)
        except (ValueError, cloud.CloudError) as exc:
            return {"error": str(exc)}

    return use_service


def _note_tool(ledger: Ledger, department: str) -> Any:
    from . import cloud

    def save_tool_note(connection: str, note: str) -> str:
        """Keep something you learned about using a connected service, so you and every later job and project don't repeat
        the mistake: an API quirk an error revealed, a method that works or one that breaks things. One fact per note,
        in plain words, such as "Fusion: root.allJoints has no .count; use len()". It's shown with the service from then on."""
        store = cloud.store_for(ledger.root.parent / "_app")
        if store is None or not store.approved(connection):
            return "There's no connected service by that name; see services."
        return store.add_note(connection, department, note, Actor.agent(department.lower()))

    return save_tool_note


def _packet_tools(ledger: Ledger, gateway: Gateway) -> tuple[Any, Any]:
    """Messages between devices are shared by Software and Electrical: either may define one or carry out an approved
    change to one. Once interfaces are frozen, a breaking change needs the person's approved change request."""

    async def define_packet(description: str) -> dict[str, Any]:
        """Define one message between devices from a plain description, such as what the laptop sends to the board, or carry out an approved change to an existing one under the same name. Code validates it and generates the C, Python and TypeScript. Say whether it travels over serial, Wi-Fi or similar."""
        try:
            return {"saved": await interfaces.draft_packet(ledger, gateway, description)}
        except interfaces.InterfaceError as exc:
            return {"error": str(exc)}

    async def check_messages() -> dict[str, Any]:
        """Run the free automatic check of the message definitions again and see its result. It changes nothing. Use it to retry the check, because define_packet always saves a new version."""
        return await ci.check_messages(ledger)

    return define_packet, check_messages


class PartProposal(BaseModel):
    number: str = Field(description="Part number, such as M-01")
    name: str
    material: str
    process: str = Field(description="How it's made or bought, such as 3D print, laser cut or bought")
    quantity: int = Field(ge=1)
    why: str = Field(description="What it's for, in one sentence")


class ComponentProposal(BaseModel):
    part_number: str
    role: str = Field(description="What it does in this design")
    fills: str = Field(default="", description="The needed item it provides, exactly as named in set_needed_items")
    generic: bool = Field(default=False, description="True only for things with no datasheet, such as wire and simple connectors")
    datasheet_url: str = Field(default="", description="A direct link to its datasheet PDF, if found")
    category: str = Field(default="", description="microcontroller, dev_board, module or generic")


def _lead_tools(ledger: Ledger, gateway: Gateway, department: str, services: Services, nested: bool = False) -> list[Any]:
    root = ledger.root
    if nested and not concept_approved(ledger):
        return [WebSearchTool()]  # Before the concept is agreed a department can advise, but it can't add anything to the design.
    tools: list[Any] = [_service_tool(ledger, department), _note_tool(ledger, department)]
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

        def propose_components(components: list[ComponentProposal]) -> dict[str, Any]:
            """Suggest components for the person to confirm, all in one call: list every component you're proposing now. For each, fills is the needed item it provides, exactly as named in set_needed_items. One part per needed item: the same part can fill several items, but to change an item's part use raise_change_request. Set generic only for things with no datasheet, such as wire, cable and simple connectors. datasheet_url is a direct link to the PDF if you found one. category is microcontroller (a bare chip), dev_board (a board carrying a chip, such as a Nucleo or DevKit), module (a display, sensor or driver board) or generic; it decides which documents the part needs."""
            results = []
            for item in components:
                c = item if isinstance(item, ComponentProposal) else ComponentProposal.model_validate(item)
                results.append({"part_number": c.part_number, **one_component(c.part_number, c.role, c.fills, c.generic, c.datasheet_url, c.category)})
            return {"results": results}

        def one_component(part_number: str, role: str, fills: str = "", generic: bool = False, datasheet_url: str = "", category: str = "") -> dict[str, str]:
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

        async def write_firmware(device: str, board: str, goal: str, new_board: bool = False) -> dict[str, Any]:
            """Write a board's DRIVER and MIDDLEWARE layers. device is lowercase with hyphens, board is the PlatformIO board ID. Software writes the app on top of your middleware headers. Include everything in one goal, because this can only run twice per reply.
            To change a board that already has code, use its existing device name. new_board is only for a second, physically separate board."""
            firmware_writes["count"] += 1
            if firmware_writes["count"] > 2:
                return {"error": "That's twice in this reply, and each rewrite costs money. Report what's left."}
            name = device.strip().lower()
            if not re.fullmatch(r"[a-z0-9-]{1,40}", name) or not board.strip():
                return {"error": "Give a device name in lowercase letters, numbers and hyphens, and the PlatformIO board ID."}
            boards = {d: firmware.device_config(root, d) for d in firmware.devices(root) if not firmware.is_computer(root, d)}
            if name not in boards and boards and not new_board:  # a second name for the same board splits its code in two folders
                listed = "; ".join(f"{d} (board {c['board'] if c else '?'})" for d, c in boards.items())
                firmware_writes["count"] -= 1  # nothing was written
                return {"error": f"This project's board code already lives in: {listed}. To change it, use that device name. Only if this is a second, physically separate board, call again with new_board true."}
            if name not in boards and new_board:
                ledger.append(Actor.agent("electrical"), "firmware.second_board", device=name, board=board.strip())
            return await firmware.generate(ledger, gateway, root, name, board.strip(), goal, scope="lower")

        def retire_device(device: str, why: str) -> dict[str, Any]:
            """Retire a duplicate folder of the same board's code: it moves out of firmware/ (with its bench) into retired/, kept for reference. Use it when two folders hold code for one physical board; keep the one with the real, building code and say why."""
            return firmware.retire_device(ledger, root, device, why, Actor.agent("electrical"))

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
            twins = next((g for g in firmware.duplicate_boards(root, firmware.separate_boards(ledger)) if name in g), None)
            if twins:
                bench_builds["count"] -= 1
                return {"error": f"{', '.join(twins)} are all code for the same board. Retire the duplicate with retire_device first, so the bench tests the code that will be flashed."}
            return await benchwriter.build_bench(ledger, gateway, root, name)

        tools += [dispute_check, set_needed_items, read_limits, read_facts, propose_components, draft_connections, group_circuit, write_firmware, build_bench, retire_device, *_packet_tools(ledger, gateway), WebSearchTool(), *session.tools()]
    elif department == "Mechanical":
        def propose_parts(parts: list[PartProposal]) -> dict[str, Any]:
            """Suggest parts for the person to add to the parts list, all in one call: list every part you're proposing now, each with its number, name, material, process, quantity and why it's needed. To change a part already listed, use raise_change_request."""
            results = []
            with _proposing:
                for item in parts:
                    p = item if isinstance(item, PartProposal) else PartProposal.model_validate(item)
                    number = p.number.strip()
                    state = ledger.state()
                    if not number:
                        results.append({"number": "", "error": "Give the part a number, such as M-01."})
                    elif number in mechanical.parts_by_number(state) or any(x["fields"]["number"] == number for x in state.of_type("part_proposal", "proposed")):
                        results.append({"number": number, "status": f"{number} is already listed."})
                    else:
                        results.append({"number": number, "id": ledger.propose(Actor.agent("mechanical"), "part_proposal", number=number, name=p.name.strip(), material=p.material.strip(), process=p.process.strip(), qty=max(1, p.quantity), why=p.why.strip())})
            return {"results": results}

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

        def save_deliverable(name: str, content: str) -> dict[str, Any]:
            """Save a file Software and the person can read: an interface table, joint limits, collision shapes. name is a short file name ending in .md, .csv, .json or .txt; it goes in mechanics/deliverables/."""
            clean = name.strip().replace("\\", "/").split("/")[-1]
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,60}\.(md|csv|json|txt)", clean):
                return {"error": "Use a short file name ending in .md, .csv, .json or .txt, such as cad_interface.md."}
            if len(content) > 200_000:
                return {"error": "Keep it under 200,000 characters."}
            target = root / "mechanics" / "deliverables" / clean
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            ledger.append(Actor.agent("mechanical"), "deliverable.saved", department="Mechanical", path=f"mechanics/deliverables/{clean}", chars=len(content))
            return {"saved": f"mechanics/deliverables/{clean}", "note": "Software reads it with read_deliverable."}

        tools += [propose_parts, set_assemblies, save_deliverable, WebSearchTool()]
    elif department == "Software":
        writes = {"count": 0}

        async def write_code(device: str, goal: str, board: str) -> dict[str, Any]:
            """Write code for one device. device is lowercase with hyphens. Leave board empty for a computer. For a microcontroller, give its PlatformIO board ID: this writes only the APP layer, on top of the drivers and middleware Electrical wrote. Put everything in the first goal; call again only to finish what it says is not done. It can run up to 4 times per reply."""
            writes["count"] += 1
            if writes["count"] > MAX_CODE_WRITES:
                return {"error": f"That's {MAX_CODE_WRITES} code writes in this reply, and each costs money. Report what's left as PARTIAL, naming exactly what isn't done."}
            name = device.strip().lower()
            if not re.fullmatch(r"[a-z0-9-]{1,40}", name):
                return {"error": "Device names use lowercase letters, numbers and hyphens."}
            if name == SIM_DEVICE:
                return {"error": "firmware/simulation/ is the Simulation department's code. Write the product's code for its own device or computer."}
            if board.strip():
                return await firmware.generate(ledger, gateway, root, name, board.strip(), goal, scope="app")
            return await software.generate(ledger, gateway, root, name, goal)

        def propose_design(title: str, html: str) -> dict[str, str]:
            """Show the person a screen design as one self-contained HTML page. Use inline CSS. Scripts and network requests are blocked."""
            if len(html) > MAX_DESIGN_CHARS:
                return {"error": "Keep the design under 100,000 characters."}
            return {"id": ledger.propose(Actor.agent("software"), "design_proposal", title=title.strip(), html=html)}

        define_packet, check_messages = _packet_tools(ledger, gateway)

        def run_command(device: str, command: list[str]) -> dict[str, Any]:
            """Run a build, code-generation or test command for one of your programs, from its folder in firmware/<device>/: the program and its arguments, such as ["gradlew.bat", "assembleDebug"], ["npm", "run", "build"] or ["generate_icons.py"]. It returns the output and the files it made. It must finish by itself, with no window. Only works once the person let the app run this program's code."""
            name = device.strip().lower()
            if name == SIM_DEVICE:
                return {"error": "firmware/simulation/ is the Simulation department's program."}
            try:
                return software.run_command(ledger, root, name, list(command or []), Actor.agent("software"))
            except software.SoftwareError as exc:
                return {"error": str(exc)}

        tools += [define_packet, check_messages, write_code, run_command, propose_design, software.search_open_source, WebSearchTool()]
    elif department == "Simulation":
        sim_writes = {"count": 0}

        async def write_sim_code(goal: str) -> dict[str, Any]:
            """Write or change the simulation code, in firmware/simulation/. goal says what the simulation must do: which model and code it loads (by their paths), the scenario, what it measures, and where it saves results (simulation/results/<run name>/). Put everything in the first goal; call again only to finish what it says is not done."""
            sim_writes["count"] += 1
            if sim_writes["count"] > MAX_CODE_WRITES:
                return {"error": f"That's {MAX_CODE_WRITES} code writes in this reply. Report what's left as PARTIAL."}
            context = ("This is the Simulation department's code, not the product's. It builds and runs a simulation from the other departments' deliverables and leaves them unchanged: "
                       "load model files from mechanics/ and import the product's code from the other folders in firmware/ by adding them to the import path. "
                       "Save every result under the project's simulation/results/<run name>/ (two folders up from here: ../../simulation/results/), as CSV or JSON data, PNG plots and screenshots, and GIF or MP4 recordings. Every run saves a recording of what happened (a GIF or MP4 of the scene), a summary.json of what it measured and an assumptions.json of what it assumed: the person watches runs on the Simulation screen from these files. "
                       "Every script must run to the end by itself with no window, and print a short summary of what it measured.\n\n")
            return await software.generate(ledger, gateway, root, SIM_DEVICE, context + goal)

        def run_simulation(command: list[str]) -> dict[str, Any]:
            """Run a simulation command from firmware/simulation/: one of its scripts with arguments, such as ["run_handover.py", "--runs", "20"], or a simulator on this computer, such as ["ngspice", "-b", "circuit.cir"]. It returns the output and the files it made or changed. It must finish by itself, with no window. Only works once the person let the app run the simulation's code."""
            try:
                return software.run_command(ledger, root, SIM_DEVICE, list(command or []), Actor.agent("simulation"))
            except software.SoftwareError as exc:
                return {"error": str(exc)}

        def propose_test_result(test_key: str, value: float | None, outcome: str, evidence: str, files: list[str]) -> dict[str, Any]:
            """Propose a test's result from a simulation run, for the person to accept. test_key is the test's key, such as T4. value is the measured number when the test has a metric (the app judges it against the limits), otherwise outcome is passed or failed. evidence says how it was measured, so someone could repeat it, and files lists the result files (paths in the project)."""
            try:
                return {"id": traceability.propose_result(ledger, test_key, value, outcome, evidence, files, Actor.agent("simulation")), "status": "It waits on the Tests screen for the person to accept."}
            except traceability.TraceError as exc:
                return {"error": str(exc)}

        tools += [write_sim_code, run_simulation, propose_test_result, WebSearchTool()]
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
    if report.type == "DONE" and sent["type"] in ("TASK", "RECHECK") and (ledger.root.parent / "_app").is_dir():
        from . import services as reception

        reception.settle_asks(Ledger(ledger.root.parent / "_app"), ledger.root.name, department, "The department later finished its job without it.")
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
            "services": _services_for(ledger, department),
            "guidance_from_reception": "\n\n".join(e["data"]["text"] for e in events if e["kind"] == "service.note" and e["data"]["department"] == department)[-6000:] or "None."}


FOLLOW_UP_DROPS = ("other_departments_latest", "project_files")


def _follow_up_context(ledger: Ledger, department: str, services: Services) -> dict[str, Any]:
    """For the result of a step the department itself asked for, such as an approved script's output: it is continuing
    work it already knows, so it gets its own recent conversation, not the whole project again."""
    ctx = _shared_context(ledger, department, services)
    state = ctx["department_state"]
    recent = [t for t in thread(ledger.events(), department) if not t["early"]][-6:]
    return {**ctx, "briefing": "Follow-up: this is the result of a step you asked for, inside work you're already doing. The full project briefing and other departments' news are left out to save cost; look_up_project, project_files and read_department_reports have them if you need a detail.",
            "department_state": {k: v for k, v in state.items() if k not in FOLLOW_UP_DROPS} if isinstance(state, dict) else state,
            "requests": [], "earlier_discussion": "\n\n".join(f"{m['from'] or m['role']}: {m['text']}" for m in recent)}


def _first_tool(department: str, message: str, tools: list[Any]) -> str | None:
    return "change_cad" if department == "Mechanical" and "no CAD model" in message and any(getattr(t, "__name__", "") == "change_cad" for t in tools) else None


def _out_of_turns(department: str) -> protocol.Report:
    """What a department reports when it used every turn of a job: unfinished, with its saved work kept, to continue next round."""
    return protocol.Report(type="PARTIAL", done=[], not_checked=["This job ended before the agent finished; check what it saved."], provisional=[],
                           needs=[protocol.Need(owner=department, what="Continue the unfinished work from this job, starting from what's already saved.")],
                           can_give_now=[], risk="", severity="none", safety=False,
                           for_person="I ran out of steps for this job before finishing. What I saved is kept, and I'll continue in the next round.", details="")


async def run_lead(ledger: Ledger, gateway: Gateway, department: str, message: str, services: Services, nested: bool = False, attachments: Sequence[Any] = (), sent: dict[str, Any] | None = None, internal: bool = False, follow_up: bool = False) -> str:
    title = message.split("\n")[0] if message else "First proposal"
    async with jobs.slot(ledger, department, title, jobs.touches(ledger, department, message) if message else {jobs.UNKNOWN}, [message] if message else [], wait=not nested) as job:  # Read the project after any wait, so nothing is stale.
        tools = _lead_tools(ledger, gateway, department, services, nested)
        if not nested:
            tools.append(department_tool(ledger, gateway, department, services))
        tools.append(request_department_tool(ledger, department))
        tools.append(request_tool_tool(ledger, department))
        if any(c["status"] == "confirmed" for c in ledger.state().of_type("component")):
            tools.append(fetch_document_tool(ledger, department))
        if department == "Mechanical":
            tools.append(link_cad_tool(ledger))
        tools.append(hand_off_tool(ledger, department))
        sent = sent or protocol.command("BRIEF" if not message else "QUESTION" if nested else "TASK", [message] if message else [], "You've answered or finished what was asked.")
        ledger.append(SYSTEM, "command.sent", department=department, **sent)
        tier, turns = ROUTINE.get(sent["type"], (None, LEAD_TURNS)) if not attachments else (None, LEAD_TURNS)
        try:
            reply: Any = await gateway.run(agent=LEADS[department], tools=tools, request_limit=turns, tier=tier, attachments=attachments, output_type=protocol.Report, first_tool=_first_tool(department, message, tools), values={**(_follow_up_context if follow_up else _shared_context)(ledger, department, services), "message": message, "command": sent})
        except TurnLimitReached:
            reply = _out_of_turns(department)
        report = protocol.shorten(protocol.from_reply(reply))
        job.outcome = report.type
        _say(ledger, department, "agent", report.for_person, details=report.details, internal=internal, choices=report.choices)
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
            try:
                reply: Any = await gateway.run(agent=LEADS[department], tools=tools, request_limit=LEAD_TURNS, output_type=protocol.Report, first_tool=_first_tool(department, text, tools), values={**shared, "message": text, "command": sent})
            except TurnLimitReached:
                reply = _out_of_turns(department)
            report = protocol.from_reply(reply)
            job.outcome = report.type
            return report, sent

    outcomes = await asyncio.gather(*(one(i, g) for i, g in enumerate(groups)), return_exceptions=True)
    finished = [o for o in outcomes if not isinstance(o, BaseException)]
    for report, sent in sorted(finished, key=lambda o: o[0].type == "BUG"):  # Filed last, so a real fault stays the department's latest report.
        _act_on_report(ledger, department, sent, report, False)
    if finished:
        joined = protocol.shorten(finished[0][0].model_copy(update={"for_person": " ".join(r.for_person for r, _s in finished), "details": "\n\n".join(r.details for r, _s in finished if r.details)}))
        _say(ledger, department, "agent", joined.for_person, details=joined.details, choices=joined.choices)
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


async def talk_to_department(ledger: Ledger, gateway: Gateway, department: str, message: str, services: Services, attachments: Sequence[Any] = (), follow_up: bool = False) -> str:
    if department not in DEPARTMENTS:
        raise HeadquartersError("Unknown department.")
    if not shape.on_team(ledger.state(), department):
        raise HeadquartersError(f"{department} isn't on this project's team. Add it on the Journey screen first.")
    if not message.strip():
        raise HeadquartersError("Type a message.")
    _say(ledger, department, "you", message.strip())
    if department in jobs.busy(ledger):
        _say(ledger, department, "request", f"{department} is finishing another job. Your message is next, and it'll answer as soon as that's done.", "Headquarters")
    return await run_lead(ledger, gateway, department, message.strip(), services, attachments=attachments, follow_up=follow_up)


async def talk_to_concept(ledger: Ledger, gateway: Gateway, message: str, services: Services) -> None:
    tools: list[Any] = [department_tool(ledger, gateway, "Headquarters", services)]
    if concept_approved(ledger) and message.strip():  # once there's an agreed plan, the person can ask Headquarters to change it
        tools.append(change_tool(ledger, "Headquarters", person_said=message.strip()))
    await hq_agents.run_concept(ledger, gateway, message, tools)


def hand_off_tool(ledger: Ledger, sender: str) -> Callable[..., Any]:
    def hand_to_person(tool: str, title: str, what_it_changes: str, filename: str, script: str, steps: str, report_back: str) -> dict[str, Any]:
        """Hand the person a script or file to run in a tool they operate themselves, such as Altium, following your Skill for it,
        or steps to do by hand, such as clicking through an add-in's export dialog (then leave filename and script empty).
        what_it_changes: in plain words, what running it will change in their design. filename: a plain name such as export_bom.pas.
        steps: the steps to run it, separated by ' | '. report_back: what to send back, such as the Messages panel text or the exported netlist.
        It appears as a card on your department's screen; when the person reports back, you're told."""
        from . import skills as skillbook

        try:
            return skillbook.hand_off(ledger, sender, tool, title, what_it_changes, filename, script, steps.split("|"), report_back, Actor.agent(sender.lower()))
        except skillbook.SkillError as exc:
            return {"error": str(exc)}

    return hand_to_person


def link_cad_tool(ledger: Ledger) -> Callable[..., str]:
    def link_cad_model(part_number: str, tool: str, reference: str, evidence: str) -> str:
        """Record that a part's CAD model lives in an outside CAD tool rather than the app's own model, such as a body in
        the person's Fusion, SolidWorks or Onshape design. reference: where it is (document and component name, or an
        exported file in the project). evidence: what you checked that shows the model is there, such as an inspection
        result giving its body and size. Only for a model you've actually seen."""
        from . import mechanical as mech

        numbers = {p["fields"]["number"] for p in mech.current_parts(ledger.state())}
        if part_number.strip() not in numbers:
            return "No current part has that number. Use one of: " + ", ".join(sorted(numbers))[:600]
        if len(reference.strip()) < 6 or len(evidence.strip()) < 20:
            return "Say where the model is and what you checked that shows it's there."
        ledger.append(Actor.agent("mechanical"), "cad.linked", part=part_number.strip(), tool=tool.strip()[:60], reference=reference.strip()[:300], evidence=evidence.strip()[:600])
        return f"Recorded: {part_number.strip()} is modelled in {tool.strip()} ({reference.strip()[:120]})."

    return link_cad_model


def fetch_document_tool(ledger: Ledger, sender: str) -> Callable[..., str]:
    def fetch_document(part_number: str, url: str, kind: str = "datasheet") -> str:
        """Save a confirmed part's document from its public PDF link, as the person would by uploading it, so a stage
        waiting on documents doesn't wait for them. Find the direct PDF link with web search first, preferably from the
        manufacturer. kind is the kind of document (see the reply if it's wrong). If the PDF never names
        the part, you're told, so you can check it and fetch the right one."""
        from . import citations, datasheets, documents

        if kind not in documents.DOC_KINDS:
            return "Unknown kind. Use one of: " + ", ".join(documents.DOC_KINDS)
        wanted = part_number.strip().lower()
        parts = [c for c in ledger.state().of_type("component", "confirmed") if c["fields"]["part_number"].strip().lower() == wanted]
        if not parts:
            parts = [c for c in ledger.state().of_type("component", "confirmed") if wanted and wanted in c["fields"]["part_number"].lower()]
        if len(parts) != 1 and len({c["fields"]["part_number"] for c in parts}) != 1:
            return "No single confirmed part matches that. Use the part number exactly as listed: " + "; ".join(c["fields"]["part_number"] for c in ledger.state().of_type("component", "confirmed"))[:600]
        try:
            data = datasheets.fetch_pdf(url.strip())
        except (citations.PageError, OSError) as exc:
            return f"Couldn't fetch it: {exc}"
        try:
            saved = datasheets.save_pdf(ledger, parts[0]["id"], data, url.strip().rsplit("/", 1)[-1][:120] or "document.pdf", kind, Actor.system("documents"), source_url=url.strip(), fetched_by=sender)  # the app downloaded and read the real file
        except ValueError as exc:
            return str(exc)
        if not saved["part_match"]:
            return f"Saved, but the PDF never names {parts[0]['fields']['part_number']}, so it may be the wrong document; check it and fetch the right one if so."
        return f"Saved as the {documents.DOC_KINDS.get(saved['doc'], saved['doc'])} for {parts[0]['fields']['part_number']} ({saved['pages']} pages)."

    return fetch_document


def request_tool_tool(ledger: Ledger, sender: str) -> Callable[..., str]:
    def request_tool(capability: str, why: str) -> str:
        """Ask Reception for a tool this project doesn't have, such as structural analysis, a parts-pricing API or a circuit simulator.
        capability is what you need done, in a few words; why is what you'll use it for. The person sees it at Reception, where External
        and Internal Service help set it up. Until then, carry on with what you have and say what the missing tool would settle.
        A measurement on real hardware, buying something, or anything a person does by hand isn't a tool: put it in your report's needs instead."""
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
    if action.startswith("followup:"):
        department = action.split(":")[1]
        ids = _followups_due(events).get(department, [])
        texts = [e["data"]["text"] for e in events if e["id"] in ids]
        if not texts:
            return
        ledger.append(SYSTEM, "dept.followup_sent", department=department, ids=ids)
        text = texts[0] if len(texts) == 1 else f"While you waited, {len(texts)} results came back:\n\n" + "\n\n---\n\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
        _say(ledger, department, "request", text, "Connections")
        await run_lead(ledger, gateway, department, text, services, follow_up=True)
        return
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
        feedback = settled_plan_context(state, events, fixes) + ("Revise the plan to fix these problems found in review:\n" + "\n".join(f"- {f['fields']['text']}" for f in gaps) + "\n" if gaps else "") \
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
            remaining = own_remaining(events, sender)
            if remaining:
                text += "\n\nNow carry on with your outstanding work, using these answers:\n" + "\n".join(f"- {r}" for r in remaining)
            _say(ledger, sender, "request", text, "You")
            await run_lead(ledger, gateway, sender, text, services, nested=True, sent=protocol.command("TASK", remaining, "Your outstanding work is finished, or you say exactly what still stops it.") if remaining else None)
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
        await department_tool(ledger, gateway, data["sender"], services)(data["to"], data["what"], data.get("why") or f"{data['sender']}'s work, which is waiting on it")
    elif action.startswith("recheck:"):
        data = next(e["data"] for e in events if e["id"] == action.split(":")[1])
        problems = [p for s in ci.view(ledger, gates.current_gate(state)) if s["owner"] == data["department"] for p in s["problems"]]
        text = f"Please check your work again. {data['reason']}".strip()
        _say(ledger, data["department"], "request", text, "You")
        await run_lead(ledger, gateway, data["department"], text, services, sent=protocol.command("RECHECK", problems, "Every item passes the automatic checks.", data["reason"]))
    elif action == "fetch_docs":
        await fetch_docs(ledger)
    elif action.startswith("drive:"):
        _, gate, department, _token = action.split(":")
        items = _owned_work(ledger, state, gate).get(department, [])
        if items:
            ledger.append(SYSTEM, "hq.drive_mark", gate=gate, department=department, mark=_stage_mark(ledger, state, gate, department))
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
    elif action.startswith("tab_brief:"):
        _, tab_id, version, department = action.split(":")
        brief = next((b for b in tab_briefs(ledger, state) if b["tab"] == tab_id and b["version"] == version and b["department"] == department), None)
        if brief:
            brief = {**brief, "version": version}
            lines = "\n".join(f"- {r['path']}: {r['what'] or 'see the tab'} ({'none yet' if not r['found'] else str(r['found']) + ' found'})" for r in brief["reads"])
            text = (f"The {brief['title']} tab now shows on the {brief['shown_in']} screen. {brief['summary']}\n"
                    f"It reads these files, which your department makes:\n{lines}\n"
                    "Make the missing ones from your current work and save them at exactly these paths, so the tab shows real results. "
                    "If your work already produces them somewhere else, save or copy them to these paths too. If a program makes them, run it with run_program (when you have it) and check they appeared. If you need a tool you don't have, ask Reception with request_tool.")
            _say(ledger, department, "request", text, "Reception")
            await run_lead(ledger, gateway, department, text, services, sent=protocol.command("TASK", [f"{r['path']}: {r['what']}" for r in brief["reads"]], "Every file the tab reads exists at its path."))
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
    if action.startswith("tab_brief:"):
        return f"{action.split(':')[3]} is making the files a tool tab shows"
    if action.startswith("deliver:"):
        return "Passing on your answer"
    if action == "fetch_docs":
        return "Saving parts' documents from their datasheet links"
    if action == "ci":
        return "Running the automatic checks"
    if action.startswith("followup:"):
        return f"{action.split(':')[1]} is continuing with the results it asked for"
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


def tab_briefs(ledger: Ledger, state: ProjectState) -> list[dict[str, Any]]:
    """Approved tool tabs on this project whose files a team department still has to make."""
    app_root = ledger.root.parent / "_app"
    if not app_root.is_dir() or not concept_approved(ledger):
        return []
    from . import tabs

    allowed_runs = sum(1 for e in ledger.events() if e["kind"] == "software.autotest" and e["data"].get("allowed"))
    return [{**b, "version": f"{b['version']}-{allowed_runs}"} for b in tabs.briefs_due(app_root, Ledger(app_root), ledger.root, shape.team(state))]


def department_of(ledger: Ledger, action: str) -> str | None:
    """The department a step belongs to, if it's a department's own work. Those can run side by side."""
    if action.startswith("open:"):
        return action[5:]
    if action.startswith("followup:"):
        return action.split(":")[1]
    if action.startswith("drive:"):
        return action.split(":")[2]
    if action.startswith("brief_change:"):
        return action.split(":")[2]
    if action.startswith("tab_brief:"):
        return action.split(":")[3]
    if action.startswith(("deliver:", "relay:", "recheck:")):
        events = ledger.events()
        if action.startswith("deliver:"):
            first = action.split(":", 1)[1].split(",")[0]
            sender = next((r["from"] for r in requests_view(events) if r["id"] == first), None)
            return sender if sender in DEPARTMENTS else None
        data = next((e["data"] for e in events if e["id"] == action.split(":", 1)[1]), {})
        return data.get("to") if action.startswith("relay:") else data.get("department")
    return None


MAX_STEPS = 24


async def advance(ledger: Ledger, gateway: Gateway, services: Services, guard: Callable[[Any], Awaitable[Any]] | None = None) -> None:
    """Runs steps until a person is needed. Headquarters' own steps (concept, research, plan, review) take the project's
    lock one at a time. A department's work runs in the background, one job per department, so while one department
    works Headquarters can start another; it waits only when there's nothing else to start."""
    running: dict[asyncio.Task, tuple[str, str | None]] = {}

    async def finish(task: asyncio.Task) -> bool:
        action, _department = running.pop(task)
        try:
            task.result()
        except Exception as exc:  # noqa: BLE001 - one department's failure is recorded; the others carry on
            message = str(getattr(exc, "detail", "") or exc)[:300] or type(exc).__name__
            ledger.append(SYSTEM, "hq.failed", step=action, label=label_for(action), message=message, department=_department)
            if _department:
                _say(ledger, _department, "agent", f"This job stopped with an error: {message}. The other departments carry on; Headquarters will give me the work again when something changes, or you can ask me here.")
            return False
        ledger.append(SYSTEM, "hq.finished", step=action, label=label_for(action))
        return True

    try:
        steps = 0
        while steps < MAX_STEPS:
            for task in [t for t in running if t.done()]:
                await finish(task)
            busy = {d for _a, d in running.values() if d} | jobs.busy(ledger)
            action = next_action(ledger, {a for a, _d in running.values()}, busy)
            wait = outbox_wait(ledger.events()) if action is not None else 0.0
            if action is not None and wait != 0:  # the person is mid-round: running jobs carry on, new ones wait
                if wait is None and not running:
                    return  # held until the person presses Send, which starts Headquarters again
                await asyncio.sleep(min(wait or 5.0, 5.0))
                continue
            steps += 1
            if action is not None:
                label = label_for(action)
                department = department_of(ledger, action)
                ledger.append(SYSTEM, "hq.started", step=action, label=label)
                if department:
                    running[asyncio.ensure_future(run_step(ledger, gateway, action, services))] = (action, department)
                    continue
                try:
                    work = run_step(ledger, gateway, action, services)
                    await (guard(work) if guard else work)
                except Exception as exc:
                    ledger.append(SYSTEM, "hq.failed", step=action, label=label, message=str(getattr(exc, "detail", "") or exc)[:300] or type(exc).__name__)
                    return
                ledger.append(SYSTEM, "hq.finished", step=action, label=label)
                continue
            if not running:
                return
            finished, _pending = await asyncio.wait(list(running), return_when=asyncio.FIRST_COMPLETED)
            for task in finished:
                await finish(task)
    finally:
        if running:  # don't leave department work behind half-recorded
            await asyncio.wait(list(running))
            for task in list(running):
                await finish(task)


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
