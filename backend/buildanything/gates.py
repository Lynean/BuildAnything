"""Gates: checkpoints only a person can pass. Prerequisites are checked in code.

Each project has its own list of gates. Headquarters proposes it with the concept and a person confirms it.
A gate is made of built-in checks (worked out from the project's data) and requirements the person wrote,
which the person ticks off. Projects that never chose keep the original seven gates.
"""
from __future__ import annotations

import re
from typing import Any

from .ledger import Actor, Ledger, LedgerError, ProjectState

GATES = [
    ("G0", "Intake"),
    ("G1", "Concepts"),
    ("G2", "Baseline"),
    ("G3", "Interface freeze"),
    ("G4", "Design review"),
    ("G5", "Build and bring-up"),
    ("G6", "Integration and test"),
]
# What people see: a journey with plain names. The G numbers stay as IDs underneath.
JOURNEY = {
    "G0": ("Idea", "Say what you want to build"),
    "G1": ("Research", "Find real facts, with their sources"),
    "G2": ("Plan", "Turn it into requirements and tasks"),
    "G3": ("Agree", "Lock what parts and code must agree on"),
    "G4": ("Check", "Check the whole design before buying anything"),
    "G5": ("Build", "Buy, build, flash and power it on"),
    "G6": ("Prove", "Prove it works on the real device"),
}
ORDER = [g for g, _ in GATES]

# Built-in checks a gate can require. Each is worked out in code from the project's data.
CHECKS: dict[str, tuple[str, str]] = {
    "aim_scope": ("The aim and scope are written down", "G0"),
    "research": ("Sourced facts are confirmed, or research was skipped", "G1"),
    "concept_decision": ("The chosen concept is recorded", "G1"),
    "plan": ("Requirements and tasks are confirmed", "G2"),
    "verifier": ("The Verifier reviewed the plan and its serious findings are decided", "G2"),
    "interfaces": ("How the devices talk to each other is agreed and checked", "G3"),
    "design_review": ("The whole design is checked before anything is bought", "G4"),
    "build_bring_up": ("Bought, built, flashed and powered on", "G5"),
    "integration": ("Every requirement is proven on the real device", "G6"),
}
TEMPLATE_CHECKS: dict[str, list[str]] = {
    "G0": ["aim_scope"], "G1": ["research", "concept_decision"], "G2": ["plan", "verifier"], "G3": ["interfaces"],
    "G4": ["design_review"], "G5": ["build_bring_up"], "G6": ["integration"],
}
GATE_ID = re.compile(r"^(G[0-6]|C\d{1,2})$")
YOU = "You"


class GateError(LedgerError):
    pass


def template_entry(gate: str) -> dict[str, Any]:
    return {"id": gate, "name": JOURNEY[gate][0], "title": dict(GATES)[gate], "purpose": JOURNEY[gate][1], "phase": gate, "checks": list(TEMPLATE_CHECKS[gate]), "criteria": []}


def plan(state: ProjectState) -> list[dict[str, Any]]:
    """This project's gates in order."""
    return [dict(g) for g in state.gate_plan] if state.gate_plan is not None else [template_entry(g) for g in ORDER]


def order(state: ProjectState) -> list[str]:
    return [g["id"] for g in plan(state)]


def entry(state: ProjectState | None, gate: str) -> dict[str, Any]:
    if state is not None:
        for g in plan(state):
            if g["id"] == gate:
                return g
    if gate in JOURNEY:
        return template_entry(gate)
    raise GateError(f"Unknown stage {gate}.")


def phase(state: ProjectState, gate: str | None) -> str | None:
    """Which stage of the usual journey a gate stands for, such as G5 for a custom Run gate."""
    return entry(state, gate)["phase"] if gate else None


def passed(state: ProjectState, gate: str) -> bool:
    """Approved, or left out of this project's plan with every gate up to that point of the journey done."""
    if gate in state.gates_approved:
        return True
    gates = plan(state)
    if any(g["id"] == gate for g in gates) or gate not in TEMPLATE_CHECKS:
        return False
    upto = ORDER.index(gate)
    return all(g["id"] in state.gates_approved for g in gates if ORDER.index(g["phase"]) <= upto)


def stage(gate: str, state: ProjectState | None = None) -> str:
    """The name people see for a gate, such as Check for G4."""
    try:
        return entry(state, gate)["name"]
    except GateError:
        return gate


def purpose(gate: str, state: ProjectState | None = None) -> str:
    try:
        return entry(state, gate).get("purpose", "")
    except GateError:
        return ""


def next_stage(gate: str, state: ProjectState | None = None) -> str | None:
    ids = order(state) if state is not None else ORDER
    if gate not in ids and state is not None:  # a built-in stage this project's plan doesn't have: go from where it is
        gate = current_gate(state) or gate
    later = ids[ids.index(gate) + 1:] if gate in ids else []
    return stage(later[0], state) if later else None


def continue_text(gate: str, state: ProjectState | None = None) -> str:
    following = next_stage(gate, state)
    if following:
        return f"Continue to {following} on the Journey screen"
    return "Finish the project on the Journey screen" if state is None or gate == (order(state) or [gate])[-1] else "Continue on the Journey screen"


def current_gate(state: ProjectState) -> str | None:
    for gate in order(state):
        if gate not in state.gates_approved:
            return gate
    return None


def validate_plan(state: ProjectState, gates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A plan starts at the Idea gate, keeps every gate already passed in place, and moves forward through the journey."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in gates:
        gid = str(raw.get("id", "")).strip()
        if not GATE_ID.match(gid) or gid in seen:
            raise GateError(f"Each gate needs its own ID, G0 to G6 or C1 and up. Problem with {gid or 'a gate with no ID'}.")
        seen.add(gid)
        base = template_entry(gid) if gid in TEMPLATE_CHECKS else {"id": gid, "name": "", "purpose": "", "phase": "", "checks": [], "criteria": []}
        name = str(raw.get("name") or base["name"]).strip()
        if not name:
            raise GateError(f"Give gate {gid} a name.")
        stage_of = gid if gid in TEMPLATE_CHECKS else str(raw.get("phase") or "").strip()
        if stage_of not in TEMPLATE_CHECKS:
            raise GateError(f"Say which part of the journey {name} belongs to.")
        checks = list(raw.get("checks", base["checks"]) or [])
        bad = [c for c in checks if c not in CHECKS]
        if bad:
            raise GateError(f"Unknown check: {', '.join(bad)}.")
        criteria = [str(c).strip() for c in raw.get("criteria") or [] if str(c).strip()]
        item: dict[str, Any] = {"id": gid, "name": name, "purpose": str(raw.get("purpose") or base["purpose"]).strip(), "phase": stage_of, "checks": list(dict.fromkeys(checks)), "criteria": criteria}
        if gid in TEMPLATE_CHECKS:
            item["title"] = dict(GATES)[gid]
        if out and ORDER.index(stage_of) < ORDER.index(out[-1]["phase"]):
            raise GateError(f"{name} belongs earlier in the journey than {out[-1]['name']}. Put the gates in journey order.")
        out.append(item)
    if not out or out[0]["id"] != "G0":
        raise GateError("The plan starts with the Idea gate (G0).")
    done = [g for g in order(state) if g in state.gates_approved]
    if [g["id"] for g in out[:len(done)]] != done:
        raise GateError("Gates already passed stay where they are. Change only the gates still ahead.")
    for g in out[:len(done)]:
        before = entry(state, g["id"])
        if g["checks"] != before.get("checks") or g["criteria"] != before.get("criteria", []):
            raise GateError(f"{g['name']} is already passed, so what it required can't change.")
    return out


_FILLER = {"the", "a", "an", "and", "or", "of", "to", "is", "are", "be", "it", "its", "in", "on", "for", "with", "by", "this", "that", "has", "have",
           "been", "was", "were", "documented", "recorded", "written", "defined", "agreed", "done", "met", "ready", "all", "each", "every", "any"}


def _stems(text: str) -> set[str]:
    words = set()
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if len(w) > 2 and w not in _FILLER:
            words.add(w[:6] if len(w) > 6 else w.rstrip("s"))  # "investigated" and "investigation" count as one word
    return words


def criterion_evidence(state: ProjectState, events: list[dict[str, Any]], text: str, limit: int = 4) -> list[dict[str, str]]:
    """What in the project bears on a stage requirement the person ticks: the requirements, decisions and tasks that
    match its words, and the latest department message about it. So they can judge it without searching."""
    want = _stems(text)
    if not want:
        return []
    need = 1 if len(want) <= 2 else 2
    found: list[tuple[int, dict[str, str]]] = []

    def consider(kind: str, label: str, body: str) -> None:
        score = len(want & _stems(body))
        if score >= need:
            found.append((score, {"kind": kind, "label": label, "text": body.strip()[:400]}))

    for r in state.of_type("requirement"):
        if r["status"] in ("proposed", "confirmed"):
            consider("Requirement", r["fields"].get("key", ""), f"{r['fields'].get('text', '')} Checked by: {r['fields'].get('verification', '')}")
    for d in state.of_type("decision", "confirmed"):
        consider("Decision", "", d["fields"].get("text", ""))
    for t in state.of_type("task", "confirmed"):
        consider("Task", f"{t['fields'].get('key', '')} {t['fields'].get('department', '')}".strip(), t["fields"].get("title", ""))
    said = [e for e in events if e["kind"] == "dept.message" and e["data"].get("role") == "agent"]
    for e in reversed(said[-80:]):
        d = e["data"]
        before = len(found)
        consider("Latest from " + d.get("department", ""), e["at"][:10], f"{d.get('text', '')} {d.get('details', '')[:1500]}")
        if len(found) > before:
            break  # only the newest matching message
    found.sort(key=lambda x: -x[0])
    return [item for _score, item in found[:limit]]


def criterion_fingerprint(state: ProjectState, events: list[dict[str, Any]], text: str) -> str:
    """Changes when the stage requirement's wording or what the project has for it changes, so Headquarters checks again."""
    import hashlib
    import json

    return hashlib.sha256(json.dumps([text, criterion_evidence(state, events, text, limit=8)], sort_keys=True).encode()).hexdigest()[:12]


def criterion_proposals(events: list[dict[str, Any]], gate: str) -> dict[int, dict[str, Any]]:
    """Headquarters' latest view on each of a gate's own requirements, and whether the person turned it down."""
    out: dict[int, dict[str, Any]] = {}
    for e in events:
        d = e["data"]
        if e["kind"] == "criterion.proposed" and d["gate"] == gate:
            out[d["index"]] = {**d, "rejected": ""}
        elif e["kind"] == "criterion.proposal_rejected" and d["gate"] == gate and d["index"] in out:
            out[d["index"]]["rejected"] = d.get("reason", "") or "Turned down."
    return out


def criteria_unchecked(state: ProjectState, events: list[dict[str, Any]], gate: str) -> list[int]:
    """Unticked requirements of this gate that Headquarters hasn't judged against what the project has now."""
    proposals = criterion_proposals(events, gate)
    return [c["index"] for c in criteria_view(state, gate) if not c["met"] and (proposals.get(c["index"]) or {}).get("fingerprint") != criterion_fingerprint(state, events, c["text"])]


def criteria_view(state: ProjectState, gate: str, events: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    out = []
    proposals = criterion_proposals(events, gate) if events is not None else {}
    for i, text in enumerate(entry(state, gate).get("criteria", [])):
        met = state.criteria_met.get(f"{gate}:{i}")
        # A tick counts only for the wording it was given against: rewording a requirement asks again.
        ok = bool(met) and met.get("text", text) == text
        proposal = proposals.get(i)
        out.append({"index": i, "text": text, "met": ok, **({"note": met["note"], "by": met["by"], "at": met.get("at", ""), "basedOn": met.get("based_on", [])} if ok else {}),
                    **({"evidence": criterion_evidence(state, events, text)} if events is not None else {}),
                    **({"proposal": {k: proposal[k] for k in ("met", "reason", "missing", "owner", "rejected")}} if proposal and proposal.get("text") == text and not ok else {})})
    return out


def mark_criterion(ledger: Ledger, gate: str, index: int, met: bool, note: str, actor: Actor) -> None:
    """A person says a requirement they wrote for a gate is met, and how they know."""
    state = ledger.state()
    criteria = entry(state, gate).get("criteria", [])
    if not 0 <= index < len(criteria):
        raise GateError("That requirement isn't on this gate.")
    if gate in state.gates_approved:
        raise GateError("This gate is already passed.")
    if met and len(note.strip()) < 4:
        raise GateError("Say how you know it's met.")
    # what the project showed for it at that moment, so the reason for the tick stays readable later
    based_on = [f"{e['kind']}{' ' + e['label'] if e['label'] else ''}: {e['text'][:200]}" for e in criterion_evidence(state, ledger.events(), criteria[index])] if met else []
    ledger.append(actor, "gate.criterion_met", gate=gate, index=index, met=bool(met), note=note.strip(), text=criteria[index], based_on=based_on)


def open_findings_to_decide(state: ProjectState) -> list[dict]:
    """High and medium findings a person hasn't closed: the plan is revised once all of them have a decision."""
    return [f for f in state.of_type("finding") if f["fields"]["severity"] in ("high", "medium") and f["status"] == "proposed"]


def open_high_findings(state: ProjectState) -> list[dict]:
    """Every high finding a person hasn't closed. A later Verifier run can't clear one."""
    return [f for f in state.of_type("finding") if f["fields"]["severity"] == "high" and f["status"] == "proposed"]


def decide_finding(ledger: Ledger, finding_id: str, decision: str, reason: str, actor: Actor) -> None:
    """fix keeps it open. fixed, accept, later and dismiss close it. later keeps a real concern for a later stage without adding work now."""
    state = ledger.state()
    finding = state.records.get(finding_id)
    if not finding or finding["type"] != "finding":
        raise GateError("Finding not found.")
    if finding["status"] != "proposed":
        raise GateError("This finding is already closed.")
    if decision not in ("fix", "fixed", "accept", "later", "dismiss"):
        raise GateError("Unknown decision.")
    reason = reason.strip()
    latest = max(f["fields"]["run"] for f in state.of_type("finding"))
    if decision == "fixed":
        earlier = finding.get("decision")
        if not earlier or earlier["decision"] != "fix":
            raise GateError("Choose Fix it first.")
        if phase(state, current_gate(state)) in ("G0", "G1", "G2") and latest <= earlier["run"]:  # A plan can be re-reviewed. Later, the department's work is the check.
            raise GateError("Run the Verifier again on the changed plan before closing this.")
    if decision in ("accept", "later", "dismiss") and len(reason) < 8:
        raise GateError("Give a short reason, so the decision can be understood later.")
    if decision == "dismiss" and finding["fields"].get("safety") and len(reason) < 30:
        raise GateError("This is a safety finding. Say specifically why it can't cause harm.")
    ledger.append(actor, "finding.decided", id=finding_id, decision=decision, reason=reason, run=latest)


def closed_findings(state: ProjectState) -> list[dict]:
    return [{"finding": f["fields"]["text"], "safety": bool(f["fields"].get("safety")), **{k: f["decision"][k] for k in ("decision", "reason")}} for f in state.of_type("finding") if f.get("decision") and f["status"] != "proposed"]


def check(key: str, state: ProjectState, ledger: Ledger | None) -> list[tuple[str, str]]:
    """What one built-in check still needs, as (who, what) pairs. "You" means the person."""
    if key == "aim_scope":
        out = []
        if not state.meta.get("aim", "").strip():
            out.append((YOU, "Describe the project's aim."))
        if not state.meta.get("scope", "").strip():
            out.append((YOU, "Describe the project's scope."))
        return out
    if key == "research":
        return [] if state.of_type("claim", "confirmed") or state.research_waived else [(YOU, "Confirm at least one sourced claim.")]
    if key == "concept_decision":
        return [] if state.of_type("decision", "confirmed") else [(YOU, "Record the concept you chose as a decision.")]
    if key == "plan":
        out = []
        if not state.of_type("requirement", "confirmed"):
            out.append((YOU, "Confirm the requirements."))
        unconfirmed = state.of_type("requirement", "proposed")
        if unconfirmed:
            out.append((YOU, f"Review {len(unconfirmed)} remaining requirements."))
        return out
    if key == "verifier":
        out = []
        if not state.of_type("finding"):
            out.append((YOU, "Run the Verifier on the plan."))
        high = open_high_findings(state)
        if high:
            out.append((YOU, f"Decide {len(high)} high-severity Verifier findings."))
        return out
    if key == "interfaces":
        out = []
        if not state.packets and not state.of_type("interface", "confirmed"):
            out.append(("Software", "Define at least one interface: how your devices talk to each other, such as what the laptop sends to the board."))
        waiting = [f"{c} ({name})" for name, e in state.packets.items() for c in e["packet"]["consumers"] if c not in e["acks"]]
        if waiting:
            out.append((YOU, f"These still need to acknowledge the current version: {', '.join(waiting)}."))
        drafts = state.of_type("interface", "proposed")
        if drafts:
            out.append((YOU, f"Review {len(drafts)} electrical and mechanical interfaces."))
        if ledger is not None:
            from . import gatereport

            out += [("Software" if text.startswith("The message definitions") else YOU, text) for text in gatereport.interface_tests(ledger, state)]
        return out
    if ledger is None:
        return [(YOU, "Open this project to see what this stage needs.")]
    from . import gatereport

    return gatereport.REQUIREMENTS[{"design_review": "G4", "build_bring_up": "G5", "integration": "G6"}[key]](ledger, state)


def requirements(gate: str, state: ProjectState, ledger: Ledger | None = None) -> list[tuple[str, str]]:
    """Everything a gate still needs: its built-in checks, then the requirements the person wrote for it.
    Work owned by a department that isn't on this project's team is left out until that department is added."""
    from . import shape

    item = entry(state, gate)
    out: list[tuple[str, str]] = []
    for key in item.get("checks", []):
        out += check(key, state, ledger)
    for c in criteria_view(state, gate):
        if not c["met"]:
            out.append((YOU, f"Confirm on the Journey screen: {c['text']}"))
    team = shape.team(state)
    seen: set[tuple[str, str]] = set()
    kept = []
    for who, text in out:
        if (shape.known(who) and who not in team) or (who, text) in seen:
            continue
        seen.add((who, text))
        kept.append((who, text))
    return kept


def missing(gate: str, state: ProjectState, ledger: Ledger | None = None) -> list[str]:
    if ledger is not None:
        from . import gatereport

        return gatereport.missing(gate, state, ledger)
    return [text for _who, text in requirements(gate, state, None)]


def resume_at(ledger: Ledger, gate: str, actor: Actor) -> None:
    """For imports: earlier gates count as passed, but are recorded as never reviewed."""
    state = ledger.state()
    ids = order(state)
    if gate not in ids:
        raise GateError("Unknown stage.")
    if state.gates_approved:
        raise GateError("This project has already started its journey.")
    for earlier in ids[: ids.index(gate)]:
        ledger.append(actor, "gate.imported", gate=earlier)


def approve(ledger: Ledger, gate: str, actor: Actor) -> None:
    state = ledger.state()
    now = current_gate(state)
    if gate != now:
        raise GateError(f"{stage(gate, state)} isn't the current stage. The project is at {stage(now, state) if now else 'the end'}.")
    problems = missing(gate, state, ledger)
    if problems:
        raise GateError(" ".join(problems))
    from . import gatereport

    # Written first: if this actor isn't allowed to approve, no report file should appear.
    ledger._authorize(actor, "gate.approved", {})
    page = gatereport.report(ledger, gate).replace("**Ready for your approval.**", f"**Approved by {actor.name}.**", 1)
    ledger.append(actor, "gate.approved", gate=gate, findings=closed_findings(state), report=page)
    folder = ledger.root / "reports"
    folder.mkdir(exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9-]+", "-", stage(gate, state)).strip("-") or gate
    (folder / f"Stage-{order(state).index(gate) + 1}-{name}.md").write_text(page, encoding="utf-8", newline="\n")
