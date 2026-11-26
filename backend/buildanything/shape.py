"""A project's shape: which departments it has and which gates it passes through.

Headquarters proposes a lean shape with the concept: only the departments the project needs now and only
the gates worth stopping at. A person confirms it, and can change it later. A department that turns out to
be needed is requested, and a person adds it. Projects that never set a shape keep the original four
departments and seven gates.

New kinds of department are added to DEPARTMENT_TYPES (with a lead agent spec), and nothing else changes.
"""
from __future__ import annotations

import re
from typing import Any

from .ledger import Actor, Ledger, LedgerError, ProjectState

# Every kind of department the app knows how to run. Add one here, with its lead agent, to make it available.
DEPARTMENT_TYPES: dict[str, dict[str, str]] = {
    "Mechanical": {"blurb": "Parts, structure, mounting, materials, CAD and fabrication.", "lead": "lead_mechanical"},
    "Electrical": {"blurb": "Components, datasheets, pins, power, circuits and low-level firmware.", "lead": "lead_electrical"},
    "Software": {"blurb": "The product's own code: the programs, apps and device logic that ship, with their tests.", "lead": "lead_software"},
    "Procurement": {"blurb": "Buying parts: prices, suppliers and orders.", "lead": "lead_procurement"},
    "Simulation": {"blurb": "Proves the design works before it's built: runs physics, circuit, structural or other simulations of the other departments' models and code, and produces results (data, plots, renders, recordings) against the requirements.", "lead": "lead_simulation"},
}
DEFAULT_TEAM = ("Electrical", "Mechanical", "Software", "Procurement")
SYSTEM = Actor.system("headquarters")


class ShapeError(LedgerError):
    pass


def team(state: ProjectState) -> list[str]:
    """The departments this project has, in the app's usual order."""
    chosen = DEFAULT_TEAM if state.team is None else state.team
    return [d for d in DEFAULT_TEAM if d in chosen] + [d for d in chosen if d not in DEFAULT_TEAM]


def on_team(state: ProjectState, department: str) -> bool:
    return department in team(state)


def known(department: str) -> bool:
    return department in DEPARTMENT_TYPES


def chosen(state: ProjectState) -> bool:
    """Whether a person has set this project's shape, rather than it running on the defaults."""
    return state.team is not None or state.gate_plan is not None


def set_shape(ledger: Ledger, team_names: list[str] | None, gate_plan: list[dict[str, Any]] | None, reason: str, actor: Actor) -> None:
    """Only a person sets the shape. Either part can be left as it is by passing None."""
    from . import gates

    state = ledger.state()
    if team_names is not None:
        unknown = [d for d in team_names if not known(d)]
        if unknown:
            raise ShapeError(f"Unknown department: {', '.join(unknown)}. Choose from {', '.join(DEPARTMENT_TYPES)}.")
        team_names = list(dict.fromkeys(team_names))
    if gate_plan is not None:
        gate_plan = gates.validate_plan(state, gate_plan)
    ledger.append(actor, "shape.set", team=team_names, gates=gate_plan, reason=reason.strip())


def add_department(ledger: Ledger, department: str, reason: str, actor: Actor) -> None:
    state = ledger.state()
    if on_team(state, department):
        raise ShapeError(f"{department} is already on the team.")
    set_shape(ledger, team(state) + [department], None, reason or f"Added {department}.", actor)


def remove_department(ledger: Ledger, department: str, reason: str, actor: Actor) -> None:
    state = ledger.state()
    if not on_team(state, department):
        raise ShapeError(f"{department} isn't on the team.")
    if len(reason.strip()) < 4:
        raise ShapeError("Say why this project doesn't need it.")
    set_shape(ledger, [d for d in team(state) if d != department], None, reason, actor)


def request_department(ledger: Ledger, department: str, why: str, actor: Actor) -> str:
    """A department or Headquarters asks for another department. A person decides."""
    state = ledger.state()
    if not known(department):
        return f"There's no {department} department. The app can run: {', '.join(DEPARTMENT_TYPES)}."
    if on_team(state, department):
        return f"{department} is already on the team."
    if len(why.strip()) < 12:
        return "Say what you need that department to do, specifically."
    if any(r["department"] == department for r in requests(ledger)):
        return f"{department} has already been requested. The person will decide."
    ledger.append(actor, "shape.requested", department=department, why=why.strip())
    return f"Requested. The person will be asked whether to add {department}."


def requests(ledger: Ledger) -> list[dict[str, Any]]:
    """Open requests for a department: not yet added and not declined."""
    state = ledger.state()
    out: dict[str, dict[str, Any]] = {}
    for event in ledger.events():
        d = event["data"]
        if event["kind"] == "shape.requested":
            out.setdefault(d["department"], {"department": d["department"], "why": d["why"], "by": event["actor"]["name"], "at": event["at"][:10]})
        elif event["kind"] == "shape.request_declined":
            out.pop(d["department"], None)
    return [r for name, r in out.items() if not on_team(state, name)]


def decline_request(ledger: Ledger, department: str, reason: str, actor: Actor) -> None:
    if not any(r["department"] == department for r in requests(ledger)):
        raise ShapeError(f"Nobody has asked for {department}.")
    ledger.append(actor, "shape.request_declined", department=department, reason=reason.strip())


# ---- What Headquarters proposes ----

GATE_ID = re.compile(r"^(G[0-6]|C\d{1,2})$")


def normalise_proposal(team_names: list[str], gate_picks: list[dict[str, Any]]) -> dict[str, Any]:
    """Turns the Concept Partner's suggestion into a shape the app can run, dropping what it can't."""
    from . import gates

    team_out = [d for d in dict.fromkeys(team_names) if known(d)]
    plan: list[dict[str, Any]] = []
    custom = 0
    for pick in gate_picks:
        stage = str(pick.get("stage", "")).strip()
        if stage in gates.TEMPLATE_CHECKS:
            if any(g["id"] == stage for g in plan):
                continue
            entry = gates.template_entry(stage)
            entry["name"] = (pick.get("name") or entry["name"]).strip()
            entry["purpose"] = (pick.get("purpose") or entry["purpose"]).strip()
        else:
            custom += 1
            phase = str(pick.get("phase", "")).strip()
            entry = {"id": f"C{custom}", "name": (pick.get("name") or f"Check {custom}").strip(), "purpose": (pick.get("purpose") or "").strip(),
                     "phase": phase if phase in gates.TEMPLATE_CHECKS else (plan[-1]["phase"] if plan else "G0"), "checks": []}
        entry["checks"] = [c for c in (pick.get("checks") or entry["checks"]) if c in gates.CHECKS]
        entry["criteria"] = [str(c).strip() for c in pick.get("criteria") or [] if str(c).strip()]
        plan.append(entry)
    if not plan or plan[0]["id"] != "G0":
        plan = [g for g in plan if g["id"] != "G0"]
        plan.insert(0, gates.template_entry("G0"))
    order = list(gates.TEMPLATE_CHECKS)
    plan = [plan[0]] + sorted(plan[1:], key=lambda g: order.index(g["phase"]))  # stable: custom gates keep their place within a stage
    return {"team": team_out, "gates": plan}


def proposal(ledger: Ledger) -> dict[str, Any] | None:
    """The latest shape the Concept Partner suggested, if any."""
    drafts = [d for d in ledger.state().of_type("concept_draft") if d["fields"].get("shape")]
    return drafts[-1]["fields"]["shape"] if drafts else None


def view(ledger: Ledger) -> dict[str, Any]:
    from . import gates

    state = ledger.state()
    return {
        "chosen": chosen(state),
        "team": team(state),
        "available": [{"name": n, "blurb": t["blurb"]} for n, t in DEPARTMENT_TYPES.items()],
        "gates": gates.plan(state),
        "checks": [{"key": k, "label": label, "stage": stage} for k, (label, stage) in gates.CHECKS.items()],
        "templates": [{"id": g, "name": gates.JOURNEY[g][0], "purpose": gates.JOURNEY[g][1], "checks": gates.TEMPLATE_CHECKS[g]} for g in gates.TEMPLATE_CHECKS],
        "approved": list(state.gates_approved),
        "requests": requests(ledger),
        "proposal": proposal(ledger),
    }
