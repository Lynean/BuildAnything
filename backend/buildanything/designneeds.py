"""What a design must contain before anyone spends money, and which of those things exist yet.

A department states what the design needs. A gate can then see what's missing, which it can't
work out from the records alone: a list of confirmed parts looks complete even with no power supply.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from .ledger import Actor, Ledger, LedgerError, ProjectState


DEFAULT_CURRENCY = "SGD"
RATES_URL = "https://api.frankfurter.dev/v1/latest"  # European Central Bank reference rates. Only currency codes are sent.


class NeedsError(LedgerError):
    pass


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def declared_none(ledger: Ledger, department: str) -> str | None:
    """The reason a person gave for this project having nothing from a department, if they did.
    A department left off the project's team needs nothing either."""
    from . import shape

    if shape.known(department) and not shape.on_team(ledger.state(), department):
        return "Not on this project's team."
    return next((e["data"]["reason"] for e in ledger.events() if e["kind"] == "needs.none" and e["data"]["department"] == department), None)


HARDWARE_DEPARTMENTS = ("Electrical", "Procurement")


def simulation_first(ledger: Ledger) -> str | None:
    """The reason a person gave for building the software and simulation before any hardware, while it applies."""
    reason = None
    for event in ledger.events():
        if event["kind"] == "scope.set":
            reason = event["data"]["reason"] if event["data"].get("simulation_first") else None
    return reason


def set_scope(ledger: Ledger, simulation: bool, reason: str, actor: Actor) -> None:
    if simulation and len(reason.strip()) < 4:
        raise ValueError("Say why the hardware waits, such as 'no hardware yet'.")
    ledger.append(actor, "scope.set", simulation_first=bool(simulation), reason=reason.strip())


def deferred(ledger: Ledger, department: str) -> bool:
    """Hardware work waits while the project is simulation-first, so nobody pays for parts, wiring or firmware yet."""
    return department in HARDWARE_DEPARTMENTS and simulation_first(ledger) is not None


def _fills(state: ProjectState, component: dict[str, Any]) -> str:
    """A replacement does the job of the part it replaced."""
    while not component["fields"].get("fills") and component["fields"].get("replaces") in state.records:
        component = state.records[component["fields"]["replaces"]]
    return component["fields"].get("fills", "")


def view(ledger: Ledger) -> list[dict[str, Any]]:
    state = ledger.state()
    waived = {e["data"]["id"]: e["data"]["reason"] for e in ledger.events() if e["kind"] == "need.waived"}
    out = []
    for record in state.of_type("need"):
        if record["status"] in ("rejected", "superseded"):
            continue
        item = record["fields"]["item"]
        matching = [c for c in state.of_type("component") if c["status"] not in ("rejected", "superseded") and _key(_fills(state, c)) == _key(item)]
        confirmed = [c["fields"]["part_number"] for c in matching if c["status"] == "confirmed"]
        status = "waived" if record["id"] in waived else "met" if confirmed else "chosen" if matching else "open"
        out.append({"id": record["id"], "item": item, "why": record["fields"].get("why", ""), "status": status, "parts": confirmed or [c["fields"]["part_number"] for c in matching], "reason": waived.get(record["id"])})
    return out


def set_needed(ledger: Ledger, items: list[str], why: str, actor: Actor, reasons: dict[str, str] | None = None) -> dict[str, Any]:
    """Replaces the list. Anything already chosen or waived stays, so a shorter list can't lose a decision."""
    wanted = {_key(i): i.strip() for i in items if i.strip()}
    existing = view(ledger)
    known = {_key(n["item"]): n for n in existing}
    for key, item in wanted.items():
        if key not in known:
            ledger.propose(actor, "need", item=item, why=(reasons or {}).get(item, why).strip())
    for key, need in known.items():
        if key not in wanted and need["status"] == "open":
            ledger.append(Actor.system("electrical"), "record.superseded", id=need["id"])
    return {"needs": [n["item"] for n in view(ledger)]}


def waive(ledger: Ledger, need_id: str, reason: str, actor: Actor) -> None:
    if len(reason.strip()) < 8:
        raise NeedsError("Say why this design doesn't need it.")
    if not any(n["id"] == need_id and n["status"] != "waived" for n in view(ledger)):
        raise NeedsError("That item isn't open.")
    ledger.append(actor, "need.waived", id=need_id, reason=reason.strip())


def mark_none(ledger: Ledger, department: str, reason: str, actor: Actor) -> None:
    if len(reason.strip()) < 8:
        raise NeedsError("Say why this project doesn't need anything from this department.")
    ledger.append(actor, "needs.none", department=department, reason=reason.strip())


def mark_owned(ledger: Ledger, component_id: str, actor: Actor) -> None:
    record = ledger.state().records.get(component_id)
    if not record or record["type"] != "component":
        raise NeedsError("Component not found.")
    ledger.append(actor, "component.owned", id=component_id)


def mark_no_datasheet(ledger: Ledger, component_id: str, reason: str, actor: Actor) -> None:
    """For things like wire and cable, where no datasheet exists. A person says so and says why."""
    record = ledger.state().records.get(component_id)
    if not record or record["type"] != "component":
        raise NeedsError("Component not found.")
    if len(reason.strip()) < 8:
        raise NeedsError("Say why this part has no datasheet.")
    ledger.append(actor, "component.no_datasheet", id=component_id, reason=reason.strip())


def disputes(ledger: Ledger) -> list[dict[str, str]]:
    """Automatic warnings Electrical says don't apply. They block a gate until a person decides."""
    accepted = {e["data"]["message"] for e in ledger.events() if e["kind"] == "compat.accepted"}
    found = {e["data"]["message"]: e["data"]["why"] for e in ledger.events() if e["kind"] == "compat.disputed"}
    return [{"message": m, "why": w} for m, w in found.items() if m not in accepted]


def accepted_warnings(ledger: Ledger) -> dict[str, str]:
    return {e["data"]["message"]: e["data"]["reason"] for e in ledger.events() if e["kind"] == "compat.accepted"}


def accept_warning(ledger: Ledger, message: str, reason: str, actor: Actor) -> None:
    if len(reason.strip()) < 8:
        raise NeedsError("Say why this warning is safe to accept.")
    ledger.append(actor, "compat.accepted", message=message, reason=reason.strip())


def to_buy(state: ProjectState, parts: list[dict[str, Any]]) -> list[str]:
    return [c["fields"]["part_number"] for c in state.of_type("component", "confirmed") if not c["fields"].get("owned")] + [p["fields"]["name"] for p in parts if to_buy_part(p)]


def prices(state: ProjectState) -> dict[str, dict[str, Any]]:
    """The latest price for each thing on the buying list."""
    return {_key(r["fields"]["part"]): r["fields"] for r in state.of_type("price")}


SAFETY_ITEM = re.compile(r"fuse|breaker|protect|disconnect|emergency|e-?stop|mains|battery|charger", re.I)


def bulk_confirmable(state: ProjectState) -> list[dict[str, Any]]:
    """Safety items and anything too vague to buy still get looked at one by one."""
    return [c for c in state.of_type("component", "proposed") if not SAFETY_ITEM.search(" ".join(str(c["fields"].get(k, "")) for k in ("part_number", "role", "fills"))) and not re.search(r"\bTBD\b", c["fields"]["part_number"], re.I)]


def confirm_all(ledger: Ledger, actor: Actor) -> int:
    items = bulk_confirmable(ledger.state())
    for component in items:
        ledger.append(actor, "record.confirmed", id=component["id"])
    return len(items)


def currency(ledger: Ledger) -> str:
    """The currency the person set their budget in."""
    return next((e["data"]["currency"] for e in reversed(ledger.events()) if e["kind"] == "budget.set" and e["data"].get("currency")), DEFAULT_CURRENCY)


def exchange_rate(source: str, target: str) -> float | None:
    if source == target:
        return 1.0
    try:
        reply = httpx.get(RATES_URL, params={"base": source, "symbols": target}, headers={"User-Agent": "BuildAnything"}, timeout=15)
        reply.raise_for_status()
        return float(reply.json()["rates"][target])
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        return None


def line_total(price: dict[str, Any] | None, in_currency: str) -> float | None:
    """What this line costs in the budget's currency. None means it has no usable price, so nothing unlike gets added together."""
    if not price or price.get("rate_to", price.get("currency")) != in_currency:
        return None
    return price["unit_price"] * price["quantity"] * price.get("rate", 1.0)


def estimated_total(state: ProjectState, parts: list[dict[str, Any]], in_currency: str) -> float:
    known = prices(state)
    return round(sum(line_total(known.get(_key(i)), in_currency) or 0.0 for i in set(to_buy(state, parts))), 2)


def assembly_plan(state: ProjectState) -> dict[str, list[str]]:
    """Every joint and assembly of the whole product, with the part numbers in each. The latest list replaces the last."""
    plans = state.of_type("assembly_plan")
    return plans[-1]["fields"]["assemblies"] if plans else {}


def uncovered(state: ProjectState, parts: list[dict[str, Any]]) -> list[str]:
    numbers = {p["fields"]["number"] for p in parts}
    return [name for name, listed in assembly_plan(state).items() if not numbers.intersection(listed)]


def is_owned(part: dict[str, Any]) -> bool:
    """The person already has it: "Existing purchased kit" or "Existing 3D-printed part". Nothing to buy or model."""
    return bool(re.match(r"\s*(?:re-?used?\s+(?:the\s+)?|keep\s+(?:the\s+)?)?(existing|already (owned|have|bought|made|printed))\b(?!\s+or\b)", str(part["fields"].get("process", "")), re.I))


def is_purchased(part: dict[str, Any]) -> bool:
    return bool(re.search(r"purchas|buy|bought|off.the.shelf|\bstandard\b|supplied", str(part["fields"].get("process", "")), re.I))


LOOSE = re.compile(r"\b(wires?|wiring|cables?|leads?|screws?|bolts?|nuts?|washers?|fasteners?|rivets?|zip.?ties?|cable.?ties?|tape|glue|adhesive|solder|heat.?shrink|connectors?|crimps?|labels?|bands?|o-?rings?|sleeving|lubricant|grease)\b", re.I)


def is_loose(part: dict[str, Any]) -> bool:
    """Wire, screws, ties and similar: they don't take a place of their own in the product, so they need no CAD body."""
    return bool(LOOSE.search(f"{part['fields'].get('name', '')} {part['fields'].get('material', '')}"))


def model_waivers(ledger: Ledger) -> dict[str, str]:
    """Parts the person said need no body in the CAD model, with their reason."""
    return {e["data"]["part"]: e["data"]["reason"] for e in ledger.events() if e["kind"] == "part.model_waived"}


def waive_model(ledger: Ledger, part_number: str, reason: str, actor: Actor) -> None:
    if len(reason.strip()) < 8:
        raise NeedsError("Say why this part doesn't need to be in the CAD model.")
    ledger.append(actor, "part.model_waived", part=part_number.strip(), reason=reason.strip()[:400])


def to_buy_part(part: dict[str, Any]) -> bool:
    return is_purchased(part) and not is_owned(part)


def not_on_order(state: ProjectState, parts: list[dict[str, Any]]) -> list[str]:
    """Things that must be bought, and aren't on any order or already owned."""
    ordered = " ".join(str(o["fields"].get("items", "")).lower() for o in state.of_type("order", "confirmed"))
    out = [c["fields"]["part_number"] for c in state.of_type("component", "confirmed") if not c["fields"].get("owned") and c["fields"]["part_number"].lower() not in ordered]
    return out + [p["fields"]["name"] for p in parts if to_buy_part(p) and p["fields"]["name"].lower() not in ordered and p["fields"]["number"].lower() not in ordered]
