"""The Project Ledger: an append-only event log, with SQLite as a disposable cache.

The event log is the only source of truth. It is plain text so teammates can merge it
through Git. Events are ordered by time and ID, so a union merge of two logs is valid.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pickle
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class LedgerError(Exception):
    pass


class PermissionDenied(LedgerError):
    pass


_person = "owner"


def set_person(name: str) -> None:
    """Who is using this computer. Their name goes on everything they approve."""
    global _person
    _person = name.strip() or "owner"


def current_person() -> str:
    return _person


@dataclass(frozen=True)
class Actor:
    kind: str  # "human", "agent" or "system"
    name: str

    @staticmethod
    def human(name: str | None = None) -> "Actor":
        return Actor("human", name or _person)

    @staticmethod
    def delegated(name: str = "headquarters") -> "Actor":
        """Headquarters acting under the written policy. Never counts as a person."""
        return Actor("delegated", name)

    @staticmethod
    def agent(name: str) -> "Actor":
        return Actor("agent", name)

    @staticmethod
    def system(name: str = "app") -> "Actor":
        return Actor("system", name)


# Only a person can make these changes. No agent credential can write them.
HUMAN_ONLY = {
    "gate.approved", "record.confirmed", "record.rejected", "limit.changed", "test.result_accepted", "test.result_dismissed",
    "connector.saved", "connector.removed", "connector.approved", "autonomy.changed",
    "debug.measurement", "check.closed", "budget.set", "release.created", "gate.imported", "finding.decided", "command.person", "compat.accepted", "requirement.clarified", "component.owned", "component.variant", "component.no_datasheet", "component.doc_missing", "assumption.checked", "bench.accepted", "bench.screen", "need.waived", "part.model_waived", "needs.none", "deviation.closed", "order.arrived", "test.result", "test.dependencies", "change.approved", "change.rejected", "change.reopened", "request.person_answered", "task.done", "software.tests", "software.autotest", "sim.code_allowed", "cad.trials_allowed", "scope.set", "flash.approved", "flash.restored", "concept.approved", "project.updated", "hq.retry", "research.waived", "packet.acked", "packet.removed", "link.answer", "link.routed", "link.closed", "software.step", "cad.kept", "cad.rolled_back", "cad.contact_allowed", "cad.synced", "cad.drawing", "shape.set", "shape.request_declined", "gate.criterion_met", "criterion.proposal_rejected", "platform.converter_decided", "service.install_approved", "service.install_rejected", "service.ask_closed", "hq.paused", "project.disconnected", "hq.send_now", "hq.office", "service.tool_decided", "skill.decided", "handoff.reported", "skills.auto_set", "tab.decided", "tabs.scripts_allowed", "tab.shown", "conn.decided", "conn.key_set", "conn.write_decided", "conn.write_trust", "conn.removed",
}

# A model may suggest these checks but can never report their results.
NO_AGENT = {"link.check", "packet.simulated", "change.auto_approved", "change.auto_rejected", "change.escalated"}
DELEGATED_RECORDS = {"part", "task"}  # All Headquarters may confirm, and only while applying a routine change.

# Which record types each agent may propose.
AGENT_WRITES: dict[str, set[str]] = {
    "concept": {"concept_draft"},
    "research": {"source", "claim"},
    "controller": {"requirement", "task", "test"},
    "verifier": {"finding"},
    "datasheet": {"parameter"},
    "device_facts": {"device_fact"},
    "electrical": {"net", "page_note", "component", "need"},
    "software": {"design_proposal"},
    "procurement": {"order", "price"},
    "importer": {"imported_item"},
    "mechanical": {"cad_change", "part_proposal", "assembly_plan", "mechanism", "stair_course"},
}

DEFAULT_LIMIT_USD = 20.0

# One writer lock per ledger file, shared by every Ledger object in this process.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(str(path.resolve()), threading.Lock())


# Screens ask for the project's history many times per request. The file is the truth: these only skip
# parsing when its bytes haven't changed, so an edit made outside the app is still caught.
_stamps: dict[Path, tuple[tuple[int, int] | None, bytes]] = {}  # path -> (size and time, content hash)
_parsed: dict[Path, tuple[bytes, list[dict[str, Any]]]] = {}  # path -> (content hash, events); callers get copies
_states: dict[Path, tuple[bytes, bytes]] = {}


@dataclass
class ProjectState:
    meta: dict[str, Any] = field(default_factory=dict)
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    gates_approved: list[str] = field(default_factory=list)
    gates_imported: list[str] = field(default_factory=list)
    limit_usd: float = DEFAULT_LIMIT_USD
    spent_usd: float = 0.0
    spend_by_area: dict[str, float] = field(default_factory=dict)
    event_count: int = 0
    packets: dict[str, dict[str, Any]] = field(default_factory=dict)
    research_waived: bool = False
    team: list[str] | None = None  # None: the original four departments
    gate_plan: list[dict[str, Any]] | None = None  # None: the original seven gates
    criteria_met: dict[str, dict[str, Any]] = field(default_factory=dict)  # "gate:index" -> who ticked it and why

    def of_type(self, record_type: str, status: str | None = None) -> list[dict[str, Any]]:
        return [r for r in self.records.values() if r["type"] == record_type and (status is None or r["status"] == status)]


def _content_hash(event: dict[str, Any]) -> str:
    body = {k: v for k, v in event.items() if k != "sha"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


class Ledger:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / "ledger" / "events.jsonl"
        self.cache_path = self.root / ".cache" / "ledger.sqlite"
        self._lock = _lock_for(self.path)

    def _authorize(self, actor: Actor, kind: str, data: dict[str, Any]) -> None:
        if kind in ("record.confirmed", "record.rejected"):
            status = (self.state().records.get(data.get("id")) or {}).get("status")
            if status == "superseded":  # a screen showing an older list can't bring back what a newer draft replaced
                raise LedgerError("That was replaced by a newer version. Refresh to see the current one.")
        delegated = actor.kind == "delegated" and kind == "record.confirmed" and self.state().records.get(data.get("id"), {}).get("type") in DELEGATED_RECORDS
        if actor.kind == "delegated" and kind == "skill.decided" and data.get("approved") and data.get("verified"):
            from .skills import auto_enabled, listing, verified  # the person chose to let Skills through that really passed every check

            skill = next((x for x in listing(self) if x["id"] == data.get("id")), None)
            delegated = auto_enabled(self) and bool(skill) and verified(skill) and skill["hash"] == data.get("hash")
        if actor.kind == "delegated" and kind == "conn.write_decided" and data.get("approved"):
            delegated = self._write_trusted(data.get("request"))  # the person chose to let this department run this operation without asking
        if kind in HUMAN_ONLY and actor.kind != "human" and not delegated:
            raise PermissionDenied(f"Only a person can write {kind}.")
        if kind in NO_AGENT and actor.kind == "agent":
            raise PermissionDenied(f"An agent can't report {kind}.")
        if kind == "record.proposed" and actor.kind == "agent":
            allowed = AGENT_WRITES.get(actor.name, set())
            if data.get("type") not in allowed:
                raise PermissionDenied(f"Agent {actor.name} cannot propose {data.get('type')} records.")

    def _write_trusted(self, request_id: Any) -> bool:
        events = self.events()
        req = next((e["data"] for e in events if e["kind"] == "conn.write_requested" and e["id"] == request_id), None)
        if req is None:
            return False
        on = False
        for e in events:
            d = e["data"]
            if e["kind"] == "conn.write_trust" and e["actor"]["kind"] == "human" and (d["id"], d["operation"], d["project"], d["department"]) == (req["id"], req["operation"], req.get("project"), req.get("department")):
                on = bool(d["on"])
        return on

    def append(self, actor: Actor, kind: str, **data: Any) -> dict[str, Any]:
        self._authorize(actor, kind, data)
        with self._lock:
            event = {
                "id": uuid.uuid4().hex,
                "at": self._next_timestamp(),
                "actor": {"kind": actor.kind, "name": actor.name},
                "kind": kind,
                "data": data,
            }
            event["sha"] = _content_hash(event)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            before = _stamps.get(self.path)
            with self.path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
            self._remember_append(before, event)
        return event

    def _remember_append(self, before: tuple[tuple[int, int] | None, bytes] | None, event: dict[str, Any]) -> None:
        """The record only grows, so the remembered events and state just take the new event, without re-reading the file."""
        parsed, states = _parsed.get(self.path), _states.get(self.path)
        if before is None or parsed is None or parsed[0] != before[1]:
            return
        try:
            key = self._read()[0]
        except OSError:
            return
        _stamps[self.path] = (self._stamp(), key)
        if parsed[1] and (parsed[1][-1]["at"], parsed[1][-1]["id"]) > (event["at"], event["id"]):
            return  # out of order (a merge): the next read rebuilds everything
        _parsed[self.path] = (key, [*parsed[1], json.loads(json.dumps(event, ensure_ascii=False))])
        if states and states[0] == before[1]:
            state = pickle.loads(states[1])
            _apply(state, event)
            _states[self.path] = (key, pickle.dumps(state))

    def _next_timestamp(self) -> str:
        """Timestamps strictly increase, so a confirmation can never sort before its proposal."""
        now = dt.datetime.now(dt.timezone.utc)
        if self.path.exists():
            parsed, stamp = _parsed.get(self.path), _stamps.get(self.path)
            if parsed and stamp and stamp[0] == self._stamp() and parsed[0] == stamp[1]:
                newest = [parsed[1][-1]["at"]] if parsed[1] else []  # events are kept sorted, so the last is the newest
            else:
                lines = [l for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]
                newest = [max((json.loads(l)["at"] for l in lines), key=dt.datetime.fromisoformat)] if lines else []  # after a merge the newest may not be last
            if newest:
                last = dt.datetime.fromisoformat(newest[0])
                if now <= last:
                    now = last + dt.timedelta(microseconds=1)
        return now.isoformat(timespec="microseconds")

    def _read(self) -> tuple[bytes, str]:
        raw = self.path.read_bytes()
        return hashlib.sha256(raw).digest(), raw.decode("utf-8")

    def _stamp(self) -> tuple[int, int] | None:
        try:
            info = self.path.stat()
        except OSError:
            return None
        return info.st_mtime_ns, info.st_size

    def _key(self) -> bytes:
        """The content hash, re-read only when the file's size or time changed (the ledger is only ever appended to)."""
        stamp = self._stamp()
        known = _stamps.get(self.path)
        if known and known[0] == stamp:
            return known[1]
        key = self._read()[0]
        _stamps[self.path] = (stamp, key)
        return key

    def events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        key = self._key()
        known = _parsed.get(self.path)
        if known and known[0] == key:  # Same bytes, so the hash chain was already checked. Each caller gets its own copy.
            return _copies(known[1])
        key, text = self._read()
        out = []
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            event = json.loads(line)
            if _content_hash(event) != event.get("sha"):
                raise LedgerError(f"Ledger line {number} was changed after it was written.")
            out.append(event)
        out.sort(key=lambda e: (e["at"], e["id"]))
        _parsed[self.path] = (key, out)
        return _copies(out)

    def state(self) -> ProjectState:
        key = self._key() if self.path.exists() else b""
        known = _states.get(self.path)
        if known and known[0] == key:
            return pickle.loads(known[1])
        state = ProjectState()
        for event in self.events():
            _apply(state, event)
        _states[self.path] = (key, pickle.dumps(state))
        return state

    def propose(self, actor: Actor, record_type: str, **fields: Any) -> str:
        record_id = f"{record_type[:3]}_{uuid.uuid4().hex[:10]}"
        self.append(actor, "record.proposed", id=record_id, type=record_type, fields=fields)
        return record_id

    # ---- SQLite cache: safe to delete at any time ----

    def rebuild_cache(self) -> None:
        """Builds the cache in a new file and swaps it in, under a lock, so departments working side by side never see
        it half-written or deleted while they read it."""
        import os
        import uuid as _uuid

        with _lock_for(self.cache_path):
            state = self.state()
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            fresh = self.cache_path.with_name(f"ledger-{_uuid.uuid4().hex[:8]}.sqlite")
            db = sqlite3.connect(fresh)
            try:
                with db:
                    db.execute("create table records (id text primary key, type text, status text, body text)")
                    db.execute("create table info (key text primary key, value text)")
                    db.executemany("insert into records values (?,?,?,?)", [(r["id"], r["type"], r["status"], json.dumps(r, ensure_ascii=False)) for r in state.records.values()])
                    db.execute("insert into info values ('event_count', ?)", (str(state.event_count),))
            finally:
                db.close()
            for _attempt in range(20):  # on Windows a reader can hold the old file open for a moment
                try:
                    os.replace(fresh, self.cache_path)
                    break
                except PermissionError:
                    import time as _time

                    _time.sleep(0.05)
            else:
                fresh.unlink(missing_ok=True)

    def query(self, record_type: str, status: str | None = None) -> list[dict[str, Any]]:
        if not self._cache_is_current():
            self.rebuild_cache()
        with _lock_for(self.cache_path):
            db = sqlite3.connect(self.cache_path)
            try:
                sql, args = "select body from records where type=?", [record_type]
                if status:
                    sql, args = sql + " and status=?", args + [status]
                return [json.loads(row[0]) for row in db.execute(sql, args)]
            finally:
                db.close()

    def _cache_is_current(self) -> bool:
        with _lock_for(self.cache_path):
            if not self.cache_path.exists():
                return False
            db = sqlite3.connect(self.cache_path)
            try:
                row = db.execute("select value from info where key='event_count'").fetchone()
            except sqlite3.Error:
                return False
            finally:
                db.close()
        return row is not None and int(row[0]) == len(self.events())


def _copies(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each caller gets its own events and data dicts, so nobody can change what the next caller sees, without paying
    for a deep copy of the whole record on every call."""
    return [{**e, "data": dict(e["data"]), "actor": dict(e["actor"])} for e in events]


def _apply(state: ProjectState, event: dict[str, Any]) -> None:
    state.event_count += 1
    kind, data = event["kind"], event["data"]
    if kind == "project.created":
        state.meta = data
    elif kind == "datasheet.renamed":  # the same document under a readable name: every record that points at it follows
        for record in state.records.values():
            if record["fields"].get("file") == data["old"] and record["type"] in ("datasheet", "page_note", "device_fact", "parameter"):
                record["fields"]["file"] = data["new"]
    elif kind == "component.no_datasheet":
        record = state.records.get(data["id"])
        if record:
            record["fields"]["no_datasheet"] = data["reason"]
    elif kind == "component.variant":
        record = state.records.get(data["id"])
        if record:
            record["fields"]["variant"] = data["text"]
    elif kind == "component.owned":
        record = state.records.get(data["id"])
        if record:
            record["fields"]["owned"] = True
    elif kind == "order.arrived":
        record = state.records.get(data["id"])
        if record:
            record["fields"]["status"] = "Arrived"
    elif kind == "task.done":
        record = state.records.get(data["id"])
        if record:
            record["done"] = data["done"]
    elif kind == "research.waived":
        state.research_waived = True
    elif kind == "project.updated":
        state.meta.update(data)
    elif kind == "record.superseded":
        record = state.records.get(data["id"])
        if record:
            record["status"] = "superseded"
    elif kind == "record.proposed":
        state.records[data["id"]] = {
            "id": data["id"], "type": data["type"], "status": "proposed",
            "fields": data["fields"], "proposed_by": event["actor"], "at": event["at"],
        }
    elif kind in ("record.confirmed", "record.rejected"):
        record = state.records.get(data["id"])
        if record:
            record["status"] = "confirmed" if kind == "record.confirmed" else "rejected"
    elif kind == "packet.saved":
        old = state.packets.get(data["packet"]["name"], {})
        state.packets[data["packet"]["name"]] = {
            "packet": data["packet"], "major": data["major"], "minor": data["minor"],
            "acks": old.get("acks", {}) if old.get("major") == data["major"] else {},
            "history": [*old.get("history", []), {"version": f"{data['major']}.{data['minor']}", "breaking": data["breaking"], "changes": data["changes"], "reason": data["reason"], "by": event["actor"]["name"], "at": event["at"]}],
            "simulated": None,
        }
    elif kind == "packet.acked":
        entry = state.packets.get(data["name"])
        if entry and entry["major"] == data["major"]:
            entry["acks"][data["consumer"]] = event["actor"]["name"]
    elif kind == "packet.removed":
        state.packets.pop(data["name"], None)
    elif kind == "packet.simulated":
        entry = state.packets.get(data["packet"])
        if entry and data["fault"] == "none":
            entry["simulated"] = {"ok": data["ok"], "version": data["version"], "at": event["at"]}
    elif kind == "finding.decided":
        record = state.records.get(data["id"])
        if record:
            record["decision"] = {"decision": data["decision"], "reason": data["reason"], "run": data["run"]}
            if data["decision"] in ("accept", "fixed", "later"):
                record["status"] = "confirmed"
            elif data["decision"] == "dismiss":
                record["status"] = "rejected"
    elif kind == "gate.approved":
        if data["gate"] not in state.gates_approved:
            state.gates_approved.append(data["gate"])
    elif kind == "gate.imported":
        if data["gate"] not in state.gates_approved:
            state.gates_approved.append(data["gate"])
            state.gates_imported.append(data["gate"])
    elif kind == "shape.set":
        if data.get("team") is not None:
            state.team = list(data["team"])
        if data.get("gates") is not None:
            state.gate_plan = [dict(g) for g in data["gates"]]
    elif kind == "gate.criterion_met":
        key = f"{data['gate']}:{data['index']}"
        if data.get("met", True):
            state.criteria_met[key] = {"note": data.get("note", ""), "by": event["actor"]["name"], "text": data.get("text", ""), "at": event["at"][:16].replace("T", " "), "based_on": data.get("based_on", [])}
        else:
            state.criteria_met.pop(key, None)
    elif kind == "limit.changed":
        state.limit_usd = float(data["usd"])
    elif kind == "spend.recorded":
        state.spent_usd += float(data["usd"])
        area = data["area"]
        state.spend_by_area[area] = state.spend_by_area.get(area, 0.0) + float(data["usd"])
