"""The automatic checks. Free, repeatable, and run only when what they depend on has changed.

An agent's claim is never evidence. Evidence comes from here.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

from . import bench, firmware, flashing, interfaces, software
from .ledger import Actor, Ledger

SYSTEM = Actor.system("ci")
OWNERS = {"Interface": "Software", "Firmware": "Software", "App": "Software", "Bench": "Software", "Mechanics": "Mechanical"}


def _results(ledger: Ledger) -> dict[tuple[str, str], dict[str, Any]]:
    return {(e["data"]["stage"], e["data"]["key"]): e["data"] for e in ledger.events() if e["kind"] == "ci.result"}


def record(ledger: Ledger, stage: str, key: str, ok: bool, problems: list[str], owners: list[str] | None = None, ran: bool = True) -> None:
    extra = {"owners": owners[:8]} if owners else {}
    ledger.append(SYSTEM, "ci.result", stage=stage, key=key, ok=ok, problems=problems[:8], ran=ran, **extra)


def _generator_hash() -> str:
    """A fix to the code generator or its check makes old results stale, even when the messages didn't change."""
    from pathlib import Path

    here = Path(__file__).parent
    return hashlib.sha256(b"".join((here / name).read_bytes() for name in ("codegen.py", "interfaces.py"))).hexdigest()[:16]


def _wanted(ledger: Ledger) -> list[tuple[str, str]]:
    """Each stage that uses a real tool, and a fingerprint of exactly what it depends on."""
    state = ledger.state()
    out: list[tuple[str, str]] = []
    if state.packets:
        out.append(("Interface", hashlib.sha256((json.dumps(state.packets, sort_keys=True) + _generator_hash()).encode()).hexdigest()))
    for device in firmware.devices(ledger.root):
        kind = "App" if firmware.is_computer(ledger.root, device) else "Firmware"
        out.append((f"{kind}:{device}", flashing.source_hash(ledger.root / "firmware" / device)))
        if kind == "Firmware" and bench.ready(ledger.root, device):
            out.append((f"Bench:{device}", bench.fingerprint(ledger.root, device)))
    return out


def pending(ledger: Ledger) -> list[tuple[str, str]]:
    known = _results(ledger)
    return [item for item in _wanted(ledger) if item not in known]


def _interface(ledger: Ledger) -> list[str]:
    results = interfaces.check_generated(ledger.root, ledger.state())
    return [f"{language}: {result['detail']}" for language, result in results.items() if result.get("ran") and not result.get("ok")]


async def run_pending(ledger: Ledger) -> int:
    """Builds and compiles go off the main thread, because each can take a minute."""
    jobs = pending(ledger)
    jobs.sort(key=lambda j: j[0].startswith("Bench"))  # The bench runs after the firmware it needs has built.
    for stage, key in jobs:
        name, _, device = stage.partition(":")
        if name == "Bench":
            built = next((r for (s, k), r in _results(ledger).items() if s == f"Firmware:{device}" and (s, k) in _wanted(ledger)), None)
            if not built or not built["ok"]:
                continue  # Waits until the firmware builds with the real toolchain.
            result = await asyncio.to_thread(bench.run, ledger.root, device)
            record(ledger, stage, key, *bench_verdict(ledger, device, result))
            continue
        if name == "Interface":
            problems = await asyncio.to_thread(_interface, ledger)
        else:
            if name == "Firmware":
                result = await asyncio.to_thread(firmware.build, ledger.root, device)
            else:
                declared = [d["name"] for d in software.setup_view(ledger.events(), device)["dependencies"]]
                result = await asyncio.to_thread(software.build, ledger.root, device, declared)
            problems = [] if result["ok"] or not result["ran"] else (result["errors"] or ["The build failed."])
        record(ledger, stage, key, not problems, problems)
    return len(jobs)


async def check_messages(ledger: Ledger) -> dict[str, Any]:
    """Runs the message check if anything it depends on changed, and reports the current verdict. Nothing is edited."""
    if not ledger.state().packets:
        return {"verdict": "no messages", "problems": []}
    await run_pending(ledger)
    result = next((r for item, r in _results(ledger).items() if item in _wanted(ledger) and item[0] == "Interface"), None)
    return {"verdict": "passed" if result and result["ok"] else "failed", "problems": (result or {}).get("problems", [])}


AUTOMATIC = {"message_check": "Interface", "firmware_build": "Firmware", "app_check": "App"}


def automatic_state(ledger: Ledger, kind: str) -> str:
    """A test proven by a free check is passed, failed or waiting according to the current version of what it checks."""
    known = _results(ledger)
    results = [known.get(item) for item in _wanted(ledger) if item[0].partition(":")[0] == AUTOMATIC[kind]]
    if not results or any(r is None for r in results):
        return "not_tested"
    return "passed" if all(r["ok"] for r in results) else "failed"


LOWER_LAYERS = re.compile(r"src[\\/](?:driver|mid)[\\/]")


def bench_verdict(ledger: Ledger, device: str, result: dict[str, Any]) -> tuple[bool, list[str], list[str], bool]:
    """Turns a bench run into problems, each with the department that fixes it."""
    lower = "Electrical" if firmware.has_lower(ledger.root, device) else "Software"
    errors = result.get("errors", [])
    if not result.get("ran") or (errors and all(e["where"] in ("runtime", "library", "firmware") for e in errors)):
        why = "; ".join(e["text"] for e in errors[:3]) or "The bench couldn't run."
        return True, [f"Couldn't run on the laptop yet: {why}"], ["Software"], False
    if errors:
        return False, [f"The bench for {device} doesn't compile any more: {'; '.join(e['text'] for e in errors[:3])}. Rebuild it with build_bench."], ["Electrical"], True
    problems: list[str] = []
    owners: list[str] = []
    def add(owner: str, text: str) -> None:
        if text not in problems:
            problems.append(text)
            owners.append(owner)
    for s in result.get("scenarios", []):
        if s.get("error"):
            add("Software", f"Bench '{s['title']}': {s['error']}")
        for c in s.get("checks", []):
            if not c["ok"]:
                add("Software", f"Bench '{s['title']}' ({s['covers']}): {c['text']}")
        for p in s.get("problems", []):
            add(lower, f"Bench, {p['who']}: {p['text']}")
        if not s.get("checks") and not s.get("error"):
            add("Electrical", f"Bench '{s['title']}' made no checks. Rebuild the bench with build_bench.")
    return not problems, problems, owners, True


def bench_accepted(ledger: Ledger, device: str) -> bool:
    current = bench.version(ledger.root, device)
    return any(e["kind"] == "bench.accepted" and e["data"].get("device") == device and e["data"].get("version") == current for e in ledger.events())


def _owner(stage: str, problems: list[str]) -> str:
    """A board's drivers and middleware are Electrical's, so a build error inside them goes to Electrical."""
    if stage.startswith("Firmware") and any(LOWER_LAYERS.search(p) for p in problems):
        return "Electrical"
    return OWNERS[stage.partition(":")[0]]


def tool_problems(ledger: Ledger) -> list[tuple[str, str]]:
    """Failures in the current version of anything a real tool checked."""
    known = _results(ledger)
    out = []
    for item in _wanted(ledger):
        result = known.get(item)
        if result and not result["ok"]:
            stage = item[0]
            if stage.startswith("Bench:") and not bench_accepted(ledger, stage.partition(":")[2]):
                continue
            if result.get("owners"):
                for owner, problem in zip(result["owners"], result["problems"]):
                    out.append((owner, f"{stage.replace(':', ' ')} failed on the laptop: {problem[:300]}"))
                continue
            out.append((_owner(stage, result["problems"]), f"{stage.replace(':', ' ')} failed its automatic check: {'; '.join(result['problems'])[:300]}"))
    return out


def view(ledger: Ledger, gate: str | None) -> list[dict[str, Any]]:
    """Every stage as it stands, for people and for evidence on reports."""
    from . import gatereport

    state = ledger.state()
    known = _results(ledger)
    out = []
    for stage, key in _wanted(ledger):
        result = known.get((stage, key))
        out.append({"stage": stage.replace(":", " "), "owner": OWNERS[stage.partition(":")[0]], "state": "waiting" if result is None else "not run" if not result.get("ran", True) else "pass" if result["ok"] else "fail", "problems": (result or {}).get("problems", [])})
    if gate:
        grouped: dict[str, list[str]] = {}
        for owner, text in gatereport.blockers(gate, state, ledger):
            grouped.setdefault(owner, []).append(text)
        for owner, problems in grouped.items():
            if owner != gatereport.YOU:
                out.append({"stage": f"{owner} design", "owner": owner, "state": "fail", "problems": problems})
    return out


def evidence(ledger: Ledger, department: str, gate: str | None) -> list[dict[str, Any]]:
    return [{"check": s["stage"], "result": s["state"], "problems": s["problems"][:3]} for s in view(ledger, gate) if s["owner"] == department]
