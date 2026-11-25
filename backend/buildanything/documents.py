"""Which documents each part needs, how much each source can be trusted, and what's only assumed.

A fact is only as good as where it came from. A part's category decides which documents it needs.
Each one is either uploaded, or marked by a person as not existing, and whatever that missing document
would have settled becomes an assumption, checked on the real device before Gate 6.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .ledger import Ledger, ProjectState

DOC_KINDS = {
    "datasheet": "datasheet",
    "reference_manual": "reference manual",
    "errata": "errata sheet",
    "user_manual": "board user manual",
    "schematic": "board schematic",
    "vendor_code": "vendor sample code or library",
}
CONFIDENCE = {"datasheet": "high", "reference_manual": "high", "errata": "high", "user_manual": "high", "schematic": "high", "vendor_code": "medium"}
CATEGORIES = ("microcontroller", "dev_board", "module", "generic")

# Each inner tuple is satisfied by any one of its kinds.
REQUIRED: dict[str, list[tuple[str, ...]]] = {
    "microcontroller": [("datasheet",), ("reference_manual",), ("errata",)],
    "dev_board": [("datasheet",), ("user_manual", "schematic")],
    "module": [("datasheet", "vendor_code")],
    "generic": [],
}
DEV_BOARD = re.compile(r"nucleo|devkit|dev\s*kit|dev\s*module|arduino|\bpico\b|launchpad|discovery|thunderboard|feather|blackpill|bluepill|supermini|wemos|nodemcu|\bboard\b", re.I)
MICROCONTROLLER = re.compile(r"\bstm32\w*|\besp32\w*|\batmega\w*|\befr32\w*|\brp2040\b|\bnrf5\d\w*|\bsamd\d+\w*", re.I)
ON_BOARD_PART = re.compile(r"button|\bbtn\b|\bled\b|\bsw\d|user", re.I)


_TITLES = (("errata", re.compile(r"errata\s+sheet|\berrata\b", re.I)), ("reference_manual", re.compile(r"reference\s+manual", re.I)),
           ("user_manual", re.compile(r"user\s+manual|user'?s\s+guide|hardware\s+(user\s+)?guide", re.I)), ("schematic", re.compile(r"\bschematics?\b", re.I)))


def detect_kind(texts: list[str]) -> str | None:
    """What a PDF calls itself on its first pages, such as an ST user manual uploaded as a datasheet."""
    figure = re.compile(r"(simplified|typical|application|functional|block)\s+schematics?", re.I)  # a figure in a datasheet, not the document's kind
    title = figure.sub("", (texts[0] if texts else "")[:600])
    if re.search(r"\bdata\s*sheet\b", title, re.I):
        return None
    head = figure.sub("", " ".join(texts[:2])[:4000])
    return next((kind for kind, found in _TITLES if found.search(title if kind == "schematic" else head)), None)


def label(kinds: tuple[str, ...] | list[str]) -> str:
    return " or ".join(DOC_KINDS[k] for k in kinds)


def doc_kind(sheet: dict[str, Any]) -> str:
    return sheet["fields"].get("doc", "datasheet")


def category(component: dict[str, Any]) -> str:
    f = component["fields"]
    if f.get("category") in CATEGORIES:
        return f["category"]
    if f.get("generic"):
        return "generic"
    text = f"{f.get('part_number', '')} {f.get('role', '')}"
    if DEV_BOARD.search(text):
        return "dev_board"
    if MICROCONTROLLER.search(text):
        return "microcontroller"
    return "module"


def latest_docs(state: ProjectState) -> dict[tuple[str, str], dict[str, Any]]:
    """The newest upload of each kind of document, per component."""
    return {(d["fields"]["component"], doc_kind(d)): d for d in state.of_type("datasheet")}


def waived(ledger: Ledger, component: dict[str, Any]) -> dict[str, str]:
    """Kinds a person said don't exist for this part, with their reason."""
    out = {k: component["fields"]["no_datasheet"] for k in DOC_KINDS} if component["fields"].get("no_datasheet") else {}
    for e in ledger.events():
        if e["kind"] == "component.doc_missing" and e["data"]["id"] == component["id"]:
            out[e["data"]["doc"]] = e["data"]["reason"]
    return out


def status(ledger: Ledger, state: ProjectState, component: dict[str, Any]) -> dict[str, Any]:
    latest = latest_docs(state)
    have = sorted(kind for (cid, kind) in latest if cid == component["id"])
    gone = waived(ledger, component)
    needs = REQUIRED[category(component)]
    return {"category": category(component), "have": have, "waived": gone,
            "needs": [list(group) for group in needs],
            "missing": [list(group) for group in needs if not any(k in have or k in gone for k in group)],
            "assumed": [list(group) for group in needs if not any(k in have for k in group) and any(k in gone for k in group)]}


def missing(ledger: Ledger, state: ProjectState) -> list[tuple[str, str]]:
    """(part number, what's needed) for confirmed parts still waiting on a document. One entry per part number."""
    out, seen = [], set()
    for c in state.of_type("component", "confirmed"):
        number = c["fields"]["part_number"]
        if number.lower() in seen:
            continue
        seen.add(number.lower())
        out += [(number, label(group)) for group in status(ledger, state, c)["missing"]]
    return out


def _id(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def assumptions(ledger: Ledger, state: ProjectState) -> list[dict[str, str]]:
    """Facts nobody could back with a document. Each is checked on the real device before Gate 6."""
    from .page_memory import current_notes

    out: list[dict[str, str]] = []
    confirmed = state.of_type("component", "confirmed")
    seen = set()
    for c in confirmed:
        number = c["fields"]["part_number"]
        if number.lower() in seen:
            continue
        seen.add(number.lower())
        for group in status(ledger, state, c)["assumed"]:
            reason = next(r for k, r in status(ledger, state, c)["waived"].items() if k in group)
            text = f"{number} has no {label(group)} ({reason}), so anything it would settle is assumed."
            out.append({"id": _id(text), "text": text})
    boards = {c["id"]: c for c in confirmed if category(c) == "dev_board"}
    sheets = {d["id"]: d for d in state.of_type("datasheet")}
    manual_notes = [n for n in current_notes(state) if doc_kind(sheets.get(n["fields"]["datasheet"], {"fields": {}})) in ("user_manual", "schematic")]
    for net in state.of_type("net"):
        if net["status"] in ("rejected", "superseded"):
            continue
        members = net["fields"]["members"]
        owners = {m["component"] for m in members}
        if len(owners) != 1 or not owners <= set(boards) or not ON_BOARD_PART.search(f"{net['fields']['name']} {' '.join(str(m['pin']) for m in members)}"):
            continue
        board = boards[next(iter(owners))]
        pins = [str(m["pin"]) for m in members]
        facts = [f for f in state.of_type("device_fact") if f["status"] not in ("rejected", "superseded") and f["fields"]["component"] == board["id"] and f["fields"].get("doc") in ("user_manual", "schematic")]
        cited = any(n["fields"]["component"] == board["id"] and any(re.search(rf"\b{re.escape(p)}\b", n["fields"]["finding"], re.I) for p in pins) for n in manual_notes) \
            or any(any(re.search(rf"\b{re.escape(p)}\b", f"{f['fields'].get('signal', '')} {f['fields']['quote']}", re.I) for p in pins) for f in facts)
        if not cited:
            text = f"{net['fields']['name']} ({', '.join(pins)}) on {board['fields']['part_number']}: its active level and pull resistor aren't backed by the board user manual or schematic."
            out.append({"id": _id(text), "text": text})
    return out


def checked(ledger: Ledger) -> dict[str, str]:
    return {e["data"]["id"]: e["data"].get("note", "") for e in ledger.events() if e["kind"] == "assumption.checked"}


def unchecked(ledger: Ledger, state: ProjectState) -> list[dict[str, str]]:
    done = checked(ledger)
    return [a for a in assumptions(ledger, state) if a["id"] not in done]
