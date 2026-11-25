"""Parts, revisions, checks reopened by change, mass and cost roll-up, and frozen releases."""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
import zipfile
from pathlib import Path
from typing import Any

from .ledger import Actor, Ledger, LedgerError, ProjectState

FIELD_CHECKS = {
    "material": ["Strength", "Stiffness", "Mass", "Cost", "Manufacturing process", "Thermal"],
    "file": ["Clearance", "Motion sweep", "Strength", "Mass", "Drawings"],
    "process": ["Manufacturing process", "Cost"],
    "qty": ["Mass", "Cost"],
    "mass_g": ["Mass"],
    "unit_cost": ["Cost"],
    "size_mm": ["Clearance", "Motion sweep", "Strength", "Drawings"],
}
DENSITY_G_CM3 = {"cast acrylic": 1.19, "acrylic": 1.19, "petg": 1.27, "pla": 1.24, "pa12": 1.01, "abs": 1.04, "aluminium": 2.70, "aluminum": 2.70, "al 6061-t6": 2.70, "steel": 7.85, "carbon rod": 1.6}


class MechanicalError(LedgerError):
    pass


def parts_by_number(state: ProjectState) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for part in state.of_type("part"):
        if part["status"] != "rejected":
            out.setdefault(part["fields"]["number"], []).append(part)
    return out


def current_parts(state: ProjectState) -> list[dict[str, Any]]:
    """Only the newest revision of each part is current. Exports can't include anything else."""
    return [revisions[-1] for revisions in parts_by_number(state).values()]


def checks_state(ledger: Ledger) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for event in ledger.events():
        data = event["data"]
        if event["kind"] == "check.reopened":
            out[(data["part"], data["check"])] = {"part": data["part"], "check": data["check"], "status": "open", "reason": data["reason"], "evidence": None}
        elif event["kind"] == "check.closed" and (data["part"], data["check"]) in out:
            out[(data["part"], data["check"])].update(status="closed", evidence=data["evidence"])
    return out


def open_checks(ledger: Ledger) -> list[dict[str, Any]]:
    return [c for c in checks_state(ledger).values() if c["status"] == "open"]


def add_part(ledger: Ledger, fields: dict[str, Any], actor: Actor) -> str:
    if not str(fields.get("number", "")).strip() or not str(fields.get("name", "")).strip():
        raise MechanicalError("A part needs a number and a name.")
    if fields["number"] in parts_by_number(ledger.state()):
        raise MechanicalError(f"{fields['number']} already exists. Revise it instead.")
    record = ledger.propose(actor, "part", revision="A", **fields)
    ledger.append(actor, "record.confirmed", id=record)
    return record


def _next_revision(revision: str) -> str:
    return chr(ord(revision[-1]) + 1) if revision and revision[-1].isalpha() and revision[-1] not in "zZ" else f"{revision}1"


def releases(ledger: Ledger) -> list[dict[str, Any]]:
    return [{**e["data"], "at": e["at"]} for e in ledger.events() if e["kind"] == "release.created"]


def revise(ledger: Ledger, number: str, changes: dict[str, Any], reason: str, actor: Actor) -> dict[str, Any]:
    state = ledger.state()
    revisions = parts_by_number(state).get(number)
    if not revisions:
        raise MechanicalError("Part not found.")
    if not reason.strip():
        raise MechanicalError("Say why this part is changing.")
    latest = revisions[-1]
    changed = {k: v for k, v in changes.items() if k in FIELD_CHECKS and latest["fields"].get(k) != v}
    if not changed:
        raise MechanicalError("Nothing changed.")
    fields = {**latest["fields"], **changed, "revision": _next_revision(latest["fields"]["revision"]), "supersedes": latest["id"]}
    record = ledger.propose(actor, "part", **fields)
    ledger.append(actor, "record.confirmed", id=record)
    reopened: list[str] = []
    for field in changed:
        for check in FIELD_CHECKS[field]:
            if check not in reopened:
                reopened.append(check)
                ledger.append(actor, "check.reopened", part=number, check=check, reason=f"{field} changed: {reason.strip()}")
    sent = [r["id"] for r in releases(ledger) if any(p["id"] == latest["id"] for p in r["parts"])]
    return {"revision": fields["revision"], "reopened": reopened, "alreadySentIn": sent}


def close_check(ledger: Ledger, part: str, check: str, evidence: str, actor: Actor) -> None:
    if not evidence.strip():
        raise MechanicalError("Say what proves this check passed.")
    found = checks_state(ledger).get((part, check))
    if not found or found["status"] != "open":
        raise MechanicalError("That check isn't open.")
    ledger.append(actor, "check.closed", part=part, check=check, evidence=evidence.strip())


def rollup(ledger: Ledger, state: ProjectState) -> dict[str, Any]:
    parts = current_parts(state)
    missing_mass = [p["fields"]["number"] for p in parts if p["fields"].get("mass_g") is None]
    missing_cost = [p["fields"]["number"] for p in parts if p["fields"].get("unit_cost") is None]
    mass = sum((p["fields"].get("mass_g") or 0) * (p["fields"].get("qty") or 1) for p in parts)
    cost = sum((p["fields"].get("unit_cost") or 0) * (p["fields"].get("qty") or 1) for p in parts)
    budget: dict[str, float] = {}
    for event in ledger.events():
        if event["kind"] == "budget.set":
            budget[event["data"]["budget"]] = float(event["data"]["limit"])
    problems = []
    if "mass_g" in budget and mass > budget["mass_g"]:
        problems.append(f"Parts weigh {mass:g} g, over the {budget['mass_g']:g} g budget.")
    if "cost" in budget and cost > budget["cost"]:
        problems.append(f"Parts cost {cost:g}, over the {budget['cost']:g} budget.")
    return {"massG": round(mass, 3), "cost": round(cost, 2), "missingMass": missing_mass, "missingCost": missing_cost, "budget": budget, "problems": problems}


def set_budget(ledger: Ledger, kind: str, limit: float, actor: Actor, currency: str = "") -> None:
    if kind not in ("mass_g", "cost") or limit <= 0:
        raise MechanicalError("Enter a budget above zero.")
    code = currency.strip().upper()
    if code and not re.fullmatch(r"[A-Z]{3}", code):
        raise MechanicalError("Use a three-letter currency code, such as SGD.")
    ledger.append(actor, "budget.set", budget=kind, limit=limit, **({"currency": code} if code and kind == "cost" else {}))


def bom_csv(parts: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["Part", "Name", "Revision", "Material", "Process", "Qty", "Unit mass (g)", "Unit cost", "File"])
    for p in parts:
        f = p["fields"]
        writer.writerow([f["number"], f["name"], f["revision"], f.get("material", ""), f.get("process", ""), f.get("qty", 1), f.get("mass_g", ""), f.get("unit_cost", ""), f.get("file", "")])
    return buffer.getvalue()


def create_release(ledger: Ledger, root: Path, recipient: str, override: str, actor: Actor) -> dict[str, Any]:
    """A frozen pack built from current records, so superseded parts can't get in."""
    state = ledger.state()
    if not recipient.strip():
        raise MechanicalError("Say who this release is for.")
    parts = current_parts(state)
    if not parts:
        raise MechanicalError("There are no parts to release.")
    still_open = open_checks(ledger)
    if still_open and not override.strip():
        raise MechanicalError(f"{len(still_open)} checks are still open. Close them, or give a reason to release anyway.")
    number = len(releases(ledger)) + 1
    release_id = f"R{number}"
    folder = root / "releases"
    folder.mkdir(exist_ok=True)
    target = folder / f"{release_id}.zip"
    readme = [f"Fabrication pack {release_id}", f"Project: {state.meta.get('name')}", f"For: {recipient.strip()}", f"Date: {dt.date.today().isoformat()}", "", "Generated from the project record. Do not edit.", ""]
    if still_open:
        readme += [f"Released with {len(still_open)} open checks. Reason: {override.strip()}", ""]
    readme += [f"{p['fields']['number']} rev {p['fields']['revision']}: {p['fields']['name']}, {p['fields'].get('material', 'material not set')}, qty {p['fields'].get('qty', 1)}" for p in parts]
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as pack:
        pack.writestr("README.txt", "\n".join(readme) + "\n")
        pack.writestr("BOM.csv", bom_csv(parts))
        for drawing in sorted((root / "drawings").glob("*.pdf")) if (root / "drawings").exists() else []:
            pack.write(drawing, f"drawings/{drawing.name}")
        for stl in sorted((root / "print").glob("*.stl")) if (root / "print").exists() else []:
            pack.write(stl, f"print/{stl.name}")
        for p in parts:
            stored = p["fields"].get("file")
            if stored and (root / "parts" / stored).exists():
                suffix = Path(stored).suffix
                pack.write(root / "parts" / stored, f"parts/{p['fields']['number']} rev {p['fields']['revision']}{suffix}")
    ledger.append(actor, "release.created", id=release_id, recipient=recipient.strip(), file=target.name, override=override.strip(),
                  parts=[{"id": p["id"], "number": p["fields"]["number"], "revision": p["fields"]["revision"]} for p in parts])
    return {"id": release_id, "file": target.name}
