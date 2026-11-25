"""Who is working on what, right now, and whether two pieces of work can run at once.

The router is plain code, so routing costs nothing. Work that changes different things runs side by side.
Work that changes the same thing, or that code can't be sure about, waits its turn.
"""
from __future__ import annotations

import asyncio
import contextvars
import datetime as dt
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from .ledger import Actor, Ledger

SYSTEM = Actor.system("router")
UNKNOWN = "*"  # Code can't tell what this touches, so it runs alone.
MAX_CLONES = 3  # So one list of work can't start an unlimited number of paid agents.
RECENT = 30
CAD_WORK = re.compile(r"\b(cad|fusion|models?|modelled|stl|sketch)\b", re.I)
WIRING_WORK = re.compile(r"\b(connections?|wiring|wires?|pins?|nets?)\b", re.I)
BOARD_WORK = re.compile(r"\b(firmware|board|microcontroller)\b", re.I)
COMPUTER_NAME = re.compile(r"laptop|computer|desktop|\bpc\b|\bapp\b|host|ground|server", re.I)


@dataclass
class Job:
    id: str
    department: str
    title: str
    items: list[str]
    touches: set[str]
    clone: int
    status: str = "queued"
    activity: str = "Waiting its turn"
    outcome: str = ""
    started: float = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).timestamp())
    done: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass
class LiveAgent:
    id: str
    agent: str
    area: str
    title: str
    job: str | None
    activity: str = "Thinking"
    started: float = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).timestamp())
    steps: list[dict[str, Any]] = field(default_factory=list)  # what it's doing, for the person: thoughts, tools, what it said


_JOBS: dict[str, dict[str, Job]] = {}
_AGENTS: dict[str, dict[str, LiveAgent]] = {}
CURRENT: contextvars.ContextVar[Job | None] = contextvars.ContextVar("current_job", default=None)


def conflict(a: set[str], b: set[str]) -> bool:
    return UNKNOWN in a or UNKNOWN in b or bool(a & b)


def _mentions(name: str, text: str) -> bool:
    return bool(name) and re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text, re.I) is not None


def touches(ledger: Ledger, department: str, text: str) -> set[str]:
    """What this work would change. Reading something another job also reads isn't a clash."""
    from . import firmware, interfaces, mechanical

    state, found = ledger.state(), set()
    if department == "Software":
        devices = set(firmware.devices(ledger.root)) | {d for p in interfaces.packets(state) for d in (p["producer"], *p["consumers"])}
        for device in devices:
            if _mentions(device, text):
                found.add(f"computer:{device}" if firmware.is_computer(ledger.root, device) or COMPUTER_NAME.search(device) else "board")
        if BOARD_WORK.search(text):
            found.add("board")
    elif department == "Mechanical":
        found |= {p["fields"]["number"] for p in mechanical.current_parts(state) if _mentions(p["fields"]["number"], text)}
        if CAD_WORK.search(text):
            found.add("fusion")  # There's only one open Fusion design.
    else:
        found |= {c["fields"]["part_number"] for c in state.of_type("component") if c["status"] != "rejected" and _mentions(c["fields"]["part_number"], text)}
        if department == "Electrical" and WIRING_WORK.search(text):
            found.add("wiring")
    return found or {UNKNOWN}


def split(ledger: Ledger, department: str, items: list[str]) -> list[list[str]]:
    """Groups a list of work into jobs that can run side by side. Anything that clashes stays in one job."""
    groups: list[tuple[list[str], set[str]]] = []
    for item in items:
        mine = touches(ledger, department, item)
        clashing = [g for g in groups if conflict(g[1], mine)]
        groups = [g for g in groups if not conflict(g[1], mine)]
        groups.append(([i for g in clashing for i in g[0]] + [item], mine.union(*(g[1] for g in clashing))))
    while len(groups) > MAX_CLONES:
        extra = groups.pop()
        groups[-1] = (groups[-1][0] + extra[0], groups[-1][1] | extra[1])
    return [g[0] for g in groups]


@asynccontextmanager
async def slot(ledger: Ledger, department: str, title: str, work: set[str], items: list[str] | None = None, clone: int = 0, wait: bool = True) -> AsyncIterator[Job]:
    """Holds a place in the department's queue until this work is finished. Earlier clashing work always goes first."""
    board = _JOBS.setdefault(str(ledger.root), {})
    earlier = [j for j in board.values() if j.department == department and conflict(j.touches, work)]
    job = Job(id=f"job_{uuid.uuid4().hex[:10]}", department=department, title=title[:140], items=items or [], touches=set(work), clone=clone)
    board[job.id] = job
    token = None
    try:
        if wait:  # A question from another department mustn't wait, or two departments could wait on each other for ever.
            for other in earlier:
                await other.done.wait()
        job.status, job.activity = "running", "Starting"
        job.started = dt.datetime.now(dt.timezone.utc).timestamp()
        token = CURRENT.set(job)
        ledger.append(SYSTEM, "job.started", id=job.id, department=department, title=job.title, items=job.items, clone=clone)
        yield job
    except BaseException:
        job.outcome = job.outcome or "FAILED"
        raise
    finally:
        if token is not None:
            CURRENT.reset(token)
            ledger.append(SYSTEM, "job.finished", id=job.id, department=department, title=job.title, outcome=job.outcome or "DONE", seconds=round(dt.datetime.now(dt.timezone.utc).timestamp() - job.started, 1))
        job.done.set()
        board.pop(job.id, None)


def agent_began(ledger: Ledger, agent: str, area: str, title: str) -> LiveAgent:
    job = CURRENT.get()
    live = LiveAgent(id=uuid.uuid4().hex[:10], agent=agent, area=area, title=title, job=job.id if job else None)
    _AGENTS.setdefault(str(ledger.root), {})[live.id] = live
    return live


def agent_ended(ledger: Ledger, live: LiveAgent) -> None:
    _AGENTS.get(str(ledger.root), {}).pop(live.id, None)


MAX_STEPS = 60


def step(live: LiveAgent, kind: str, text: str) -> None:
    """One line of the agent's trail: "thought" (its reasoning summary), "tool" (a tool and its input) or "said"."""
    text = " ".join(str(text).split())
    if not text:
        return
    live.steps.append({"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "kind": kind, "text": text[:900]})
    del live.steps[:-MAX_STEPS]


def running(ledger: Ledger) -> list[dict[str, Any]]:
    """Every agent working right now on this project, including ones inside a department's job."""
    return [{"id": a.id, "agent": a.agent, "area": a.area, "title": a.title, "activity": a.activity, "job": a.job, "started": a.started, "steps": len(a.steps)}
            for a in _AGENTS.get(str(ledger.root), {}).values()]


def trail(ledger: Ledger, agent_id: str) -> dict[str, Any] | None:
    a = _AGENTS.get(str(ledger.root), {}).get(agent_id)
    if a is None:
        return None
    job = _JOBS.get(str(ledger.root), {}).get(a.job or "")
    return {"id": a.id, "agent": a.agent, "area": a.area, "title": a.title, "activity": a.activity, "started": a.started, "task": job.title if job else "", "steps": list(a.steps)}


def note(live: LiveAgent, activity: str) -> None:
    live.activity = activity
    job = CURRENT.get()
    if job:
        job.activity = activity


def live(ledger: Ledger) -> dict[str, list[dict[str, Any]]]:
    jobs = [{"id": j.id, "department": j.department, "title": j.title, "items": j.items, "status": j.status, "activity": j.activity, "clone": j.clone, "started": j.started} for j in _JOBS.get(str(ledger.root), {}).values()]
    agents = [{"id": a.id, "agent": a.agent, "area": a.area, "title": a.title, "activity": a.activity} for a in _AGENTS.get(str(ledger.root), {}).values() if not a.job]
    return {"jobs": jobs, "agents": agents, "running": running(ledger)}


def recent(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**e["data"], "at": e["at"]} for e in reversed([e for e in events if e["kind"] == "job.finished"][-RECENT:])]
