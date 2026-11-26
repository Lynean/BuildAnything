"""Does the design do what we said? Each requirement points to a test, and each test to a measured result.

Results are recorded by a person, or come from the free automatic checks. Pass or fail against a number is worked out by code.
A result stops counting as soon as something it depended on changes.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from . import memo, depgraph
from .gateway import Gateway
from .ledger import Actor, Ledger, LedgerError, ProjectState

OWNERS = ("Mechanical", "Electrical", "Software", "Procurement", "Simulation")
ACTOR = Actor.agent("controller")
EARLY = re.compile(r"\bbefore\s+(?:\w+\s+){0,2}(?:purchas|buy|order|construct)", re.I)


class TraceError(LedgerError):
    pass


class PlannedTest(BaseModel):
    covers: list[str] = Field(description="Keys of the requirements this test proves")
    title: str
    owner: Literal["Mechanical", "Electrical", "Software", "Procurement", "Simulation"]
    procedure: str = Field(description="The steps a person follows, in order")
    pass_if: str = Field(description="The exact condition for passing, in plain words")
    metric: str | None = Field(description="What is measured, if it's a number. Null for a yes-or-no test")
    unit: str | None
    min: float | None = Field(description="Lowest passing value. Null if there's no lower limit")
    max: float | None = Field(description="Highest passing value. Null if there's no upper limit")
    uses: list[str] = Field(description="Part numbers from the project that this result depends on. Use only names listed")
    stage: Literal["design", "before_buying", "built"] = Field(default="built", description="design: can be done on paper or on screen now. before_buying: must be done before money is spent. built: needs the real hardware")
    automatic: Literal["none", "message_check", "firmware_build", "app_check"] = Field(default="none", description="The free automatic check that truly proves this, or none")


class TestPlan(BaseModel):
    tests: list[PlannedTest]
    cannot_test: list[str] = Field(description="Keys of requirements too vague to test, each followed by what's missing")


def _active(state: ProjectState, status: str | None = None) -> list[dict[str, Any]]:
    return [t for t in state.of_type("test", status) if t["status"] != "rejected"]


def _key(requirement: dict[str, Any]) -> str:
    return requirement["fields"].get("key", requirement["id"])


def requirements(state: ProjectState) -> list[dict[str, Any]]:
    return state.of_type("requirement", "confirmed")


def _known_names(state: ProjectState) -> set[str]:
    return {p["fields"]["number"] for p in state.of_type("part") if p["status"] != "rejected"} | {c["fields"]["part_number"] for c in state.of_type("component") if c["status"] != "rejected"} | set(state.packets)


def _described_names(state: ProjectState) -> list[dict[str, str]]:
    parts = [{"name": p["fields"]["number"], "what": p["fields"].get("name", "")} for p in state.of_type("part") if p["status"] != "rejected"]
    components = [{"name": c["fields"]["part_number"], "what": c["fields"].get("role", "")} for c in state.of_type("component") if c["status"] != "rejected"]
    return parts + components + [{"name": n, "what": "Data packet"} for n in state.packets]


def uncovered(state: ProjectState) -> list[dict[str, Any]]:
    covered = {key for t in _active(state) for key in t["fields"]["covers"]}
    return [r for r in requirements(state) if _key(r) not in covered]


def clarifications(events: list[dict[str, Any]]) -> dict[str, str]:
    return {e["data"]["key"]: e["data"]["answer"] for e in events if e["kind"] == "requirement.clarified"}


def clarify(ledger: Ledger, key: str, answer: str, actor: Actor) -> None:
    """A vague requirement needs one fact from a person, such as the most they'd pay. Then a test can be written."""
    if len(answer.strip()) < 3:
        raise TraceError("Say what you'd accept.")
    if key not in {_key(r) for r in uncovered(ledger.state())}:
        raise TraceError("That requirement doesn't need this.")
    ledger.append(actor, "requirement.clarified", key=key, answer=answer.strip())


async def plan_tests(ledger: Ledger, gateway: Gateway) -> dict[str, Any]:
    state = ledger.state()
    needed = uncovered(state)
    if not needed:
        return {"created": 0}
    names = _known_names(state)
    keys = {_key(r) for r in needed}
    answers = clarifications(ledger.events())
    feedback = ""
    for _attempt in range(2):
        plan: TestPlan = await gateway.run(agent="test_planner", output_type=TestPlan, values={"project": state.meta, "requirements": [{"key": _key(r), "text": r["fields"]["text"], "planned_check": r["fields"].get("verification"), "the_person_would_accept": answers.get(_key(r))} for r in needed], "parts_and_components": _described_names(state), "feedback": feedback})
        problems = [f"{t.title}: unknown requirement {k}." for t in plan.tests for k in t.covers if k not in keys]
        problems += [f"{t.title}: the lowest value is above the highest." for t in plan.tests if t.min is not None and t.max is not None and t.min > t.max]
        problems += [f"{t.title}: a measured test needs a limit, or set metric to null." for t in plan.tests if t.metric and t.min is None and t.max is None]
        done = {k for t in plan.tests for k in t.covers} | {entry.split()[0].rstrip(":,") for entry in plan.cannot_test if entry.split()}
        problems += [f"Requirement {k} has no test. Add one, or list it under cannot_test." for k in sorted(keys - done)]
        early = {_key(r) for r in needed if EARLY.search(r["fields"]["text"])}
        problems += [f"{t.title}: its requirement says before buying or building, so stage can't be built." for t in plan.tests if t.stage == "built" and early.intersection(t.covers)]
        if not problems:
            break
        feedback = "\n".join(problems)
    else:
        raise TraceError("The test plan had problems that couldn't be fixed: " + " ".join(problems))
    number = len(state.of_type("test"))
    for test in plan.tests:
        number += 1
        ledger.propose(ACTOR, "test", key=f"T{number}", covers=[k for k in test.covers if k in keys], title=test.title, owner=test.owner, procedure=test.procedure, pass_if=test.pass_if, metric=test.metric, unit=test.unit, min=test.min, max=test.max, uses=[u for u in test.uses if u in names], stage=test.stage, automatic=test.automatic)
    if plan.cannot_test:
        ledger.append(Actor.system("headquarters"), "test.untestable", items=plan.cannot_test)
    return {"created": len(plan.tests)}


def _results(ledger: Ledger) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    results: dict[str, dict[str, Any]] = {}
    edited: dict[str, list[str]] = {}
    for event in ledger.events():
        d = event["data"]
        if event["kind"] == "test.result":
            results[d["test"]] = {**d, "by": event["actor"]["name"], "at": event["at"]}
        elif event["kind"] == "test.dependencies":
            edited[d["test"]] = d["uses"]
    return results, edited


def _test_view(ledger: Ledger, test: dict[str, Any], results: dict[str, dict[str, Any]], edited: dict[str, list[str]], graph: depgraph.Graph) -> dict[str, Any]:
    from . import ci

    f = {**test["fields"], "uses": edited.get(test["id"], test["fields"].get("uses", []))}
    result = results.get(test["id"])
    nodes = graph.closure(f["uses"], f["covers"], f["owner"])
    stale: list[str] = []
    if result:
        before = result.get("snapshot", {})
        now = graph.snapshot(nodes)
        stale = sorted({graph.labels.get(n, n.split(":", 1)[-1]) for n in {*before, *now} if before.get(n) != now.get(n)})
    automatic = f.get("automatic", "none")
    state = "needs_approval" if test["status"] == "proposed" else ci.automatic_state(ledger, automatic) if automatic != "none" else "not_tested" if not result else "retest" if stale else result["outcome"]
    return {"id": test["id"], **f, "proposals": proposals(ledger).get(test["id"], []), "stage": f.get("stage", "built"), "automatic": automatic, "status": test["status"], "state": state, "result": {k: v for k, v in result.items() if k != "snapshot"} if result else None, "staleBecause": stale, "relies": sorted(graph.labels[n] for n in nodes if not n.startswith("requirement:"))}


def deviations(ledger: Ledger) -> list[dict[str, Any]]:
    """A deviation only applies to the wording it was accepted against."""
    state = ledger.state()
    current = {_key(r): r["fields"]["text"] for r in requirements(state)}
    out: dict[str, dict[str, Any]] = {}
    for event in ledger.events():
        d = event["data"]
        if event["kind"] == "deviation.accepted":
            out[d["id"]] = {**d, "status": "active"}
        elif event["kind"] == "deviation.closed" and d["id"] in out:
            out[d["id"]].update(status="closed", closedBecause=d["reason"])
    for item in out.values():
        if item["status"] == "active" and current.get(item["key"]) != item["text"]:
            item["status"] = "outdated"
    return list(out.values())


def close_deviation(ledger: Ledger, deviation_id: str, reason: str, actor: Actor) -> None:
    if len(reason.strip()) < 8:
        raise TraceError("Say what changed, so the record explains why this closed.")
    if not any(d["id"] == deviation_id and d["status"] == "active" for d in deviations(ledger)):
        raise TraceError("That deviation isn't active.")
    ledger.append(actor, "deviation.closed", id=deviation_id, reason=reason.strip())


def view(ledger: Ledger) -> dict[str, Any]:
    return memo.remembered(ledger, "traceability.view", lambda: _view(ledger))


def _view(ledger: Ledger) -> dict[str, Any]:
    state = ledger.state()
    results, edited = _results(ledger)
    graph = depgraph.build(ledger)
    live_keys = {_key(r) for r in requirements(state)}
    tests = [_test_view(ledger, t, results, edited, graph) for t in _active(state)]
    for test in tests:
        test["covers"] = [k for k in test["covers"] if k in live_keys] or test["covers"]
    accepted = {d["key"]: d for d in deviations(ledger) if d["status"] == "active"}
    rows = []
    for req in requirements(state):
        key = _key(req)
        mine = [t for t in tests if key in t["covers"]]
        states = {t["state"] for t in mine}
        status = "no_test" if not mine else "failed" if "failed" in states else "retest" if "retest" in states else "needs_approval" if "needs_approval" in states else "not_tested" if "not_tested" in states else "proven"
        if key in accepted and status != "proven":
            status = "deviated"
        rows.append({"id": req["id"], "key": key, "text": req["fields"]["text"], "personSaid": req["fields"].get("person_said", ""), "addedBecause": req["fields"].get("added_because", ""), "status": status, "tests": [t["key"] for t in mine], "deviation": accepted[key]["what"] if status == "deviated" else None})
    untestable = next((e["data"]["items"] for e in reversed(ledger.events()) if e["kind"] == "test.untestable"), [])
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("proven", "deviated", "failed", "retest", "not_tested", "needs_approval", "no_test")}
    return {"rows": rows, "tests": tests, "counts": counts, "total": len(rows), "untestable": untestable, "dependencyOptions": depgraph.options(graph), "deviations": deviations(ledger), "dropped": [e["data"] for e in ledger.events() if e["kind"] == "requirement.dropped"]}


def tests_due(ledger: Ledger, stage: str) -> list[dict[str, Any]]:
    """Approved tests from this stage that haven't passed. An accepted deviation means its test isn't owed."""
    data = view(ledger)
    excused = {r["key"] for r in data["rows"] if r["status"] == "deviated"}
    return [t for t in data["tests"] if t["status"] == "confirmed" and t["stage"] == stage and t["state"] != "passed" and not set(t["covers"]) <= excused]


def approve_all(ledger: Ledger, actor: Actor) -> int:
    waiting = ledger.state().of_type("test", "proposed")
    for test in waiting:
        ledger.append(actor, "record.confirmed", id=test["id"])
    return len(waiting)


def record_result(ledger: Ledger, test_id: str, value: float | None, outcome: str, evidence: str, actor: Actor) -> str:
    """A number is judged against the limits by code, so nobody marks their own work."""
    test = ledger.state().records.get(test_id)
    if not test or test["type"] != "test" or test["status"] != "confirmed":
        raise TraceError("Approve this test before recording a result.")
    if test["fields"].get("automatic", "none") != "none":
        raise TraceError("This test is decided by the automatic check.")
    if len(evidence.strip()) < 8:
        raise TraceError("Say how you measured it, so someone else could repeat it.")
    f = test["fields"]
    if f.get("metric"):
        if value is None:
            raise TraceError(f"Enter the measured {f['metric']}.")
        outcome = "passed" if (f.get("min") is None or value >= f["min"]) and (f.get("max") is None or value <= f["max"]) else "failed"
    elif outcome not in ("passed", "failed"):
        raise TraceError("Say whether it passed.")
    graph = depgraph.build(ledger)
    uses = _results(ledger)[1].get(test_id, f.get("uses", []))
    ledger.append(actor, "test.result", test=test_id, value=value, outcome=outcome, evidence=evidence.strip(), snapshot=graph.snapshot(graph.closure(uses, f["covers"], f["owner"])))
    return outcome


def propose_result(ledger: Ledger, test_key: str, value: float | None, outcome: str, evidence: str, files: list[str], actor: Actor) -> str:
    """An agent proposes a test's result from its work, such as a simulation run, with the result files. Only a person
    records results, so it waits for them to accept it; the number is still judged against the limits by code."""
    import uuid

    test = next((t for t in ledger.state().of_type("test", "confirmed") if t["fields"].get("key", "").lower() == test_key.strip().lower()), None)
    if test is None:
        raise TraceError(f"There's no approved test {test_key}. Approved tests: {', '.join(t['fields'].get('key', '') for t in ledger.state().of_type('test', 'confirmed')) or 'none yet'}.")
    if test["fields"].get("metric") and value is None:
        raise TraceError(f"{test_key} is judged by its {test['fields']['metric']}: give the measured value.")
    if not test["fields"].get("metric") and outcome not in ("passed", "failed"):
        raise TraceError("Say whether it passed or failed.")
    if len(evidence.strip()) < 8:
        raise TraceError("Say how it was measured, so someone else could repeat it.")
    root = ledger.root.resolve()
    kept = []
    for f in files:
        rel = f.strip().replace("\\", "/")
        target = (root / rel).resolve()
        if rel and target.is_file() and root in target.parents:
            kept.append(rel)
    proposal = f"prop_{uuid.uuid4().hex[:10]}"
    ledger.append(actor, "test.result_proposed", id=proposal, test=test["id"], value=value, outcome=outcome, evidence=evidence.strip()[:1500], files=kept[:20])
    return proposal


def proposals(ledger: Ledger) -> dict[str, list[dict[str, Any]]]:
    """Results agents proposed that nobody has accepted or dismissed yet, by test ID."""
    out: dict[str, list[dict[str, Any]]] = {}
    events = ledger.events()
    closed = {e["data"]["id"] for e in events if e["kind"] in ("test.result_accepted", "test.result_dismissed")}
    for e in events:
        if e["kind"] == "test.result_proposed" and e["data"]["id"] not in closed:
            out.setdefault(e["data"]["test"], []).append({**e["data"], "by": e["actor"]["name"], "at": e["at"]})
    return out


def accept_proposal(ledger: Ledger, proposal_id: str, actor: Actor) -> str:
    """The person accepts a proposed result: it's recorded as theirs, with the agent's evidence and files."""
    found = next((p for ps in proposals(ledger).values() for p in ps if p["id"] == proposal_id), None)
    if found is None:
        raise TraceError("That proposed result isn't waiting.")
    evidence = found["evidence"] + (f" Files: {', '.join(found['files'])}." if found["files"] else "") + f" Proposed by {found['by']}."
    outcome = record_result(ledger, found["test"], found["value"], found["outcome"], evidence, actor)
    ledger.append(actor, "test.result_accepted", id=proposal_id)
    return outcome


def dismiss_proposal(ledger: Ledger, proposal_id: str, reason: str, actor: Actor) -> None:
    ledger.append(actor, "test.result_dismissed", id=proposal_id, reason=reason.strip()[:600])


def set_dependencies(ledger: Ledger, test_id: str, uses: list[str], actor: Actor) -> None:
    test = ledger.state().records.get(test_id)
    if not test or test["type"] != "test":
        raise TraceError("Test not found.")
    graph = depgraph.build(ledger)
    unknown = [u for u in uses if graph.resolve(u) is None]
    if unknown:
        raise TraceError(f"Not in this project: {', '.join(unknown)}.")
    ledger.append(actor, "test.dependencies", test=test_id, uses=list(dict.fromkeys(uses)))


def csv_export(ledger: Ledger) -> str:
    """A copy for reports. The project's own record stays the source of truth."""
    data = view(ledger)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Requirement", "Text", "Requirement status", "Test", "Test title", "Owner", "Passes if", "Depends on", "Test status", "Measured", "Unit", "How measured", "Recorded by"])
    tests = {t["key"]: t for t in data["tests"]}
    for row in data["rows"]:
        for key in row["tests"] or [""]:
            t = tests.get(key)
            result = (t or {}).get("result") or {}
            writer.writerow([row["key"], row["text"], row["status"], key, t["title"] if t else "", t["owner"] if t else "", t["pass_if"] if t else "", "; ".join(t["relies"]) if t else "", t["state"] if t else "", result.get("value", ""), t.get("unit") or "" if t else "", result.get("evidence", ""), result.get("by", "")])
    return out.getvalue()


def affected_tests(ledger: Ledger, name: str) -> list[dict[str, Any]]:
    """Tests already passed that a change to this named thing would undo."""
    if not name:
        return []
    graph = depgraph.build(ledger)
    node = graph.resolve(name) or f"requirement:{name}"
    touched = graph.expand(node) if ":" in node and node in graph.fingerprints else {node}
    return [t for t in view(ledger)["tests"] if t["state"] == "passed" and touched & graph.closure(t["uses"], t["covers"], t["owner"])]
