"""Debug sessions: the rules that stop 'paste the error and press enter'.

The ladder is enforced here in code, so an agent can't skip it.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from pydantic import BaseModel, Field

from .gateway import Gateway
from .ledger import Actor, Ledger, LedgerError

CODE_LADDER = ["Normal fix", "Measurement required", "Stop patching, reproduce minimally", "Possible device issue"]
HARDWARE_LADDER = ["Normal fix", "Measurement required", "Stop patching, reproduce minimally", "Manual procedure"]


class DebugError(LedgerError):
    pass


def fingerprint(error_text: str) -> str:
    """The same failure at a different address, line or time counts as a repeat."""
    text = error_text.lower()
    text = re.sub(r"0x[0-9a-f]+", "#", text)
    text = re.sub(r"[a-z]:[\\/][^\s:]+|/[\w./-]+", "@", text)
    text = re.sub(r"\d+", "#", text)
    text = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def sessions(ledger: Ledger) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for event in ledger.events():
        kind, d = event["kind"], event["data"]
        if not kind.startswith("debug."):
            continue
        if kind == "debug.opened":
            out[d["id"]] = {
                "id": d["id"], "title": d["title"], "device": d.get("device"), "where": d.get("where", ""), "domain": d["domain"],
                "fingerprint": d["fingerprint"], "repeat": 1, "status": "open", "hypotheses": [], "measurements": [], "fixes": [],
                "measured_since_repeat": False, "minimal_repro": False, "errors": [d["error"]], "history": [],
            }
            continue
        s = out.get(d.get("id"))
        if s is None:
            continue
        if kind == "debug.repeated":
            s["repeat"] += 1
            s["measured_since_repeat"] = False
            s["errors"].append(d["error"])
        elif kind == "debug.hypothesis":
            s["hypotheses"].append({k: d[k] for k in ("hypothesis", "primary", "backup", "expect", "refute")})
        elif kind == "debug.measurement":
            s["measurements"].append(d["text"])
            s["measured_since_repeat"] = True
        elif kind == "debug.repro":
            s["minimal_repro"] = True
        elif kind == "debug.fix":
            s["fixes"].append(d["text"])
        elif kind == "debug.moved":
            s["domain"] = d["to"]
            s["history"].append(d["reason"])
            if d["to"] == "device":
                s["repeat"] = 1
                s["measured_since_repeat"] = False
                s["minimal_repro"] = False
        elif kind == "debug.closed":
            s["status"] = "closed"
            s["resolution"] = d["resolution"]
    for s in out.values():
        s["ladder"] = CODE_LADDER if s["domain"] == "code" else HARDWARE_LADDER
        s["rung"] = min(s["repeat"], 4)
        s["next"] = next_requirement(s)
    return out


def next_requirement(s: dict[str, Any]) -> str:
    if s["status"] == "closed":
        return "Closed."
    if s["rung"] >= 2 and not s["measured_since_repeat"]:
        return "Take a measurement or capture a log before any more changes."
    if s["rung"] >= 3 and not s["minimal_repro"]:
        return "Build a minimal reproduction before any more changes."
    if s["rung"] >= 4 and s["domain"] != "code":
        return "Follow a manual procedure, or ask the vendor with what has been ruled out."
    if not s["hypotheses"]:
        return "State a hypothesis, with sources."
    return "Test the current hypothesis."


def open_or_repeat(ledger: Ledger, title: str, error: str, domain: str, actor: Actor, device: str | None = None, where: str = "") -> dict[str, Any]:
    if domain not in ("code", "wiring", "device"):
        raise DebugError("Unknown problem type.")
    if not error.strip():
        raise DebugError("Describe the error or symptom.")
    mark = fingerprint(error)
    for s in sessions(ledger).values():
        if s["fingerprint"] == mark and s["status"] == "open":
            ledger.append(actor, "debug.repeated", id=s["id"], error=error)
            updated = sessions(ledger)[s["id"]]
            from . import firmware

            computer = bool(device) and firmware.is_computer(ledger.root, device)  # a program on a computer has no board or wiring to blame
            if updated["domain"] == "code" and updated["rung"] >= 4 and not computer:
                ledger.append(Actor.system("debug-ladder"), "debug.moved", id=s["id"], to="device", reason="The same error came back after code fixes, so this is now a possible device issue.")
            return sessions(ledger)[s["id"]]
    session_id = f"dbg_{uuid.uuid4().hex[:10]}"
    ledger.append(actor, "debug.opened", id=session_id, title=title.strip() or "Unnamed problem", error=error, domain=domain, fingerprint=mark, device=device, where=where)
    return sessions(ledger)[session_id]


def _get(ledger: Ledger, session_id: str) -> dict[str, Any]:
    s = sessions(ledger).get(session_id)
    if s is None:
        raise DebugError("Debug session not found.")
    return s


def add_measurement(ledger: Ledger, session_id: str, text: str, actor: Actor) -> dict[str, Any]:
    if not text.strip():
        raise DebugError("Enter what you measured.")
    _get(ledger, session_id)
    ledger.append(actor, "debug.measurement", id=session_id, text=text.strip())
    return _get(ledger, session_id)


def mark_repro(ledger: Ledger, session_id: str, actor: Actor) -> dict[str, Any]:
    _get(ledger, session_id)
    ledger.append(actor, "debug.repro", id=session_id)
    return _get(ledger, session_id)


def record_fix(ledger: Ledger, session_id: str, text: str, actor: Actor) -> dict[str, Any]:
    s = _get(ledger, session_id)
    if s["status"] == "closed":
        raise DebugError("This session is closed.")
    if not s["hypotheses"]:
        raise DebugError("State a hypothesis, with sources, before changing anything.")
    if s["rung"] >= 2 and not s["measured_since_repeat"]:
        raise DebugError("This error has repeated. Take a measurement before any more changes.")
    if s["rung"] >= 3 and not s["minimal_repro"]:
        raise DebugError("Build a minimal reproduction before any more changes.")
    if s["rung"] >= 4 and s["domain"] != "code":
        raise DebugError("Stop changing things. Follow a manual procedure instead.")
    ledger.append(actor, "debug.fix", id=session_id, text=text)
    return _get(ledger, session_id)


def move(ledger: Ledger, session_id: str, to: str, reason: str, actor: Actor) -> dict[str, Any]:
    if to not in ("code", "wiring", "device"):
        raise DebugError("Unknown destination.")
    _get(ledger, session_id)
    ledger.append(actor, "debug.moved", id=session_id, to=to, reason=reason)
    return _get(ledger, session_id)


def close(ledger: Ledger, session_id: str, resolution: str, actor: Actor) -> dict[str, Any]:
    _get(ledger, session_id)
    ledger.append(actor, "debug.closed", id=session_id, resolution=resolution)
    return _get(ledger, session_id)


class HypothesisOut(BaseModel):
    hypothesis: str
    primary: str = Field(description="The specific document and section that supports this")
    backup: str = Field(description="A second, independent source to try")
    expect: str = Field(description="What will be observed if the hypothesis is right")
    refute: str = Field(description="What result would rule it out")


async def propose_hypothesis(ledger: Ledger, gateway: Gateway, session_id: str, known_limits: str, tools: list[Any] | None = None, notes: str = "") -> dict[str, Any]:
    s = _get(ledger, session_id)
    if s["status"] == "closed":
        raise DebugError("This session is closed.")
    output: HypothesisOut = await gateway.run(
        agent="debug",
        values={"problem": s["title"], "type": s["domain"], "where": s["where"], "errors": s["errors"][-3:], "earlier_hypotheses": [h["hypothesis"] for h in s["hypotheses"]], "measurements": s["measurements"], "limits": known_limits, "saved_findings": notes},
        output_type=HypothesisOut, tools=tools or [], request_limit=16,
    )
    ledger.append(Actor.agent("electrical"), "debug.hypothesis", id=session_id, **output.model_dump())
    return _get(ledger, session_id)
