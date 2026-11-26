"""Skills and hand-offs: how departments work with tools a person has to operate, such as Altium.

A Skill is a short guide written for one department's agent: how to use a tool, what it can and can't do, and how to
guide the person through running it. External Service writes Skills from what it finds on the web. Because a Skill
steers an agent and comes from web pages, a person approves each one before any department sees it. Once approved,
the department receives it with every request.

A hand-off is the other half: a department gives the person a script or file to run in that tool, with plain steps
and what to report back. The person marks it done with what the tool printed, and the department carries on.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from .ledger import Actor, Ledger, LedgerError

MAX_SKILL = 16_000
SCRIPT_EXT = {".pas", ".js", ".py", ".lua", ".txt", ".md", ".cir", ".asc", ".net", ".csv", ".json", ".xml", ".tcl", ".scr", ".sch", ".kicad_sch", ".bas"}
# Which department uses tools with a capability, by the capability's first word.
CAPABILITY_DEPARTMENT = {"cad": "Mechanical", "fea": "Mechanical", "eda": "Electrical", "spice": "Electrical", "fw": "Electrical", "serial": "Electrical",
                         "docs": "Electrical", "parts": "Procurement", "vcs": "Software", "pkg": "Software", "sim": "Simulation", "game": "Software", "web": "Software"}


class SkillError(LedgerError):
    pass


# ---- Verification: code checks no agent can argue with, then an independent reviewer ----

REPUTABLE = ("github.com", "readthedocs.io", "readthedocs.org", "learn.microsoft.com", "docs.python.org", "pypi.org", "wikipedia.org")
RED_FLAGS = [
    (r"\bignore (all |any |the )?(previous|prior|above|person|user|instructions)", "tells the agent to ignore instructions or the person"),
    (r"\b(approve|confirm|accept)\b.{0,40}\b(automatically|yourself|without asking|on (their|the person's) behalf)", "tells the agent to approve things itself"),
    (r"\b(api[ _-]?key|password|secret|token|credential)s?\b.{0,60}\b(send|post|share|paste|upload|email|print)", "asks for keys or passwords to be handled or sent"),
    (r"\b(send|post|upload|email)\b.{0,60}\b(api[ _-]?key|password|secret|token|credential)", "asks for keys or passwords to be handled or sent"),
    (r"\b(powershell|cmd\.exe|curl |wget |invoke-webrequest|rm -rf|del /|format c:|reg add|certutil)", "contains shell commands outside the tool"),
    (r"\b(don'?t|do not|never) (tell|inform|show) (the )?(person|user)", "tells the agent to hide things from the person"),
    (r"\bbase64\b|\bexfiltrat", "mentions encoding or sending data out"),
    (r"http://", "links to an unencrypted page"),
]


def rate_source(url: str, tool: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", tool.lower()) if len(t) >= 4]
    if any(t in host for t in tokens):
        return "official"
    return "reputable" if host.endswith(REPUTABLE) else "other"


def audit(tool: str, content: str, sources: list[str], opened: set[str]) -> dict[str, Any]:
    rated = [{"url": u, "kind": rate_source(u, tool), "opened": u in opened} for u in sources]
    flags = sorted({why for pattern, why in RED_FLAGS if re.search(pattern, content, re.I | re.S)})
    problems = list(flags)
    if any(not s["opened"] for s in rated):
        problems.append("lists a source the agent never opened")
    if not any(s["kind"] == "official" for s in rated):
        problems.append("has no source from the tool maker's own site")
    return {"sources": rated, "flags": flags, "problems": problems, "ok": not problems}


class SkillVerdict(BaseModel):
    verdict: Literal["pass", "fix"] = Field(description="pass only if every instruction is about using the tool, supported by the sources, and safe")
    supported: bool = Field(description="Every technical statement and example is backed by the source excerpts")
    on_topic: bool = Field(description="Nothing tells the agent to do anything except use this tool and guide the person")
    problems: list[str] = Field(description="Each specific problem, quoting the Skill. Empty when it passes")


def departments_for(capabilities: list[str], explicit: list[str] | None = None) -> list[str]:
    out = list(explicit or [])
    for c in capabilities:
        d = CAPABILITY_DEPARTMENT.get(c.split(".")[0])
        if d and d not in out:
            out.append(d)
    return out


def _folder(app_root: Path, skill_id: str) -> Path:
    return app_root / "skills" / skill_id


def write(app_ledger: Ledger, tool: str, departments: list[str], title: str, summary: str, content: str, sources: list[str], actor: Actor, opened: set[str] | None = None) -> dict[str, Any]:
    from .shape import DEPARTMENT_TYPES

    departments = [d for d in departments if d in DEPARTMENT_TYPES]
    if not departments:
        raise SkillError(f"Say which department the Skill is for: {', '.join(DEPARTMENT_TYPES)}.")
    content = content.strip()
    if len(content) < 200:
        raise SkillError("A Skill needs real guidance: how to use the tool, its limits, and how to guide the person.")
    if len(content) > MAX_SKILL:
        raise SkillError(f"Keep a Skill under {MAX_SKILL} characters; summarise and link to the sources for the rest.")
    sources = [s.strip() for s in sources if re.match(r"^https://\S{4,300}$", s.strip())][:12]
    if not sources:
        raise SkillError("List the https pages the Skill is based on, so the person can check them.")
    checks = audit(tool, content, sources, opened if opened is not None else set(sources))
    for older in listing(app_ledger):  # a rewrite replaces the version still waiting
        if older["status"] == "waiting" and older["tool"] == tool and set(older["departments"]) == set(departments):
            app_ledger.append(actor, "skill.withdrawn", id=older["id"])
    skill_id = re.sub(r"[^a-z0-9]+", "-", f"{tool}-{title}".lower()).strip("-")[:60] + "-" + uuid.uuid4().hex[:4]
    folder = _folder(app_ledger.root, skill_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(content, encoding="utf-8")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    app_ledger.append(actor, "skill.proposed", id=skill_id, tool=tool, departments=departments, title=title.strip()[:120], summary=summary.strip()[:600], sources=sources, hash=digest, audit=checks)
    return {"id": skill_id, "departments": departments, "audit": checks}


def record_review(app_ledger: Ledger, skill_id: str, verdict: SkillVerdict) -> None:
    app_ledger.append(Actor.system("skill_reviewer"), "skill.reviewed", id=skill_id, **verdict.model_dump())


def auto_enabled(app_ledger: Ledger) -> bool:
    on = False
    for e in app_ledger.events():
        if e["kind"] == "skills.auto_set":
            on = bool(e["data"]["on"])
    return on


def verified(skill: dict[str, Any]) -> bool:
    review = skill.get("review") or {}
    return bool(skill.get("audit", {}).get("ok")) and review.get("verdict") == "pass" and review.get("supported") and review.get("on_topic") and skill.get("intact")


def maybe_auto_approve(app_ledger: Ledger, skill_id: str) -> bool:
    """While the person has it switched on, a Skill that passed every check is approved on their behalf, and says so."""
    skill = next((s for s in listing(app_ledger) if s["id"] == skill_id), None)
    if not skill or skill["status"] != "waiting" or not verified(skill) or not auto_enabled(app_ledger):
        return False
    app_ledger.append(Actor.delegated("external_service"), "skill.decided", id=skill_id, approved=True, verified=True, reason="Passed verification; approved automatically as the person chose.", hash=skill["hash"])
    return True


def listing(app_ledger: Ledger) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for e in app_ledger.events():
        d = e["data"]
        if e["kind"] == "skill.proposed":
            out[d["id"]] = {**d, "status": "waiting", "by": e["actor"]["name"], "at": e["at"]}
        elif e["kind"] == "skill.decided" and d["id"] in out:
            out[d["id"]].update(status="approved" if d["approved"] else "rejected", reason=d.get("reason", ""), decided_by=e["actor"]["name"], automatic=e["actor"]["kind"] == "delegated")
        elif e["kind"] == "skill.reviewed" and d["id"] in out:
            out[d["id"]]["review"] = {k: v for k, v in d.items() if k != "id"}
        elif e["kind"] == "skill.withdrawn" and d["id"] in out and out[d["id"]]["status"] == "waiting":
            out[d["id"]]["status"] = "replaced"
    for s in out.values():
        f = _folder(app_ledger.root, s["id"]) / "SKILL.md"
        text = f.read_text(encoding="utf-8") if f.is_file() else ""
        s["content"] = text
        s["intact"] = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] == s["hash"]  # edited after it was proposed: not used
    return sorted(out.values(), key=lambda s: s["at"], reverse=True)


def decide(app_ledger: Ledger, skill_id: str, approve: bool, reason: str, actor: Actor) -> dict[str, Any]:
    skill = next((s for s in listing(app_ledger) if s["id"] == skill_id), None)
    if not skill or skill["status"] != "waiting":
        raise SkillError("That Skill isn't waiting for approval.")
    if not skill["intact"]:
        raise SkillError("The Skill's text changed after it was proposed. Ask External Service to write it again.")
    if not approve and len(reason.strip()) < 4:
        raise SkillError("Say why, so External Service can do better.")
    app_ledger.append(actor, "skill.decided", id=skill_id, approved=bool(approve), reason=reason.strip(), hash=skill["hash"])
    return skill


def for_department(app_ledger: Ledger, department: str) -> list[dict[str, Any]]:
    """Approved, unedited Skills a department works with."""
    return [s for s in listing(app_ledger) if s["status"] == "approved" and s["intact"] and department in s["departments"]]


def context(app_ledger: Ledger, department: str) -> str:
    """What a department's agent reads about its Skills with every request."""
    skills = for_department(app_ledger, department)
    if not skills:
        return "None yet. If a task needs a tool you don't know how to drive, ask Reception with request_tool."
    return "\n\n".join(f"## Skill: {s['title']} (for {s['tool']})\n{s['content']}" for s in skills)[:40_000]


# ---- Hand-offs ----

def hand_off(ledger: Ledger, department: str, tool: str, title: str, what_it_changes: str, filename: str, script: str, steps: list[str], report_back: str, actor: Actor) -> dict[str, Any]:
    name = Path(filename.strip()).name
    ext = "".join(Path(name).suffixes[-1:]).lower() if name else ""
    if not re.match(r"^[A-Za-z0-9_.-]{3,80}$", name) or ext not in SCRIPT_EXT:
        raise SkillError(f"Name the file plainly with one of these endings: {', '.join(sorted(SCRIPT_EXT))}.")
    if not script.strip() or len(script) > 200_000:
        raise SkillError("Give the script or file content.")
    steps = [s.strip() for s in steps if s.strip()][:15]
    if not steps or len(what_it_changes.strip()) < 10:
        raise SkillError("Say what it will change, and give the steps to run it.")
    handoff_id = f"ho_{uuid.uuid4().hex[:10]}"
    folder = ledger.root / "handoffs" / department.lower()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{handoff_id}_{name}"
    target.write_text(script, encoding="utf-8")
    ledger.append(actor, "handoff.created", id=handoff_id, department=department, tool=tool.strip()[:60], title=title.strip()[:160], changes=what_it_changes.strip()[:800],
                  file=target.relative_to(ledger.root).as_posix(), steps=steps, report_back=report_back.strip()[:400])
    return {"id": handoff_id, "file": target.relative_to(ledger.root).as_posix()}


def handoffs(ledger: Ledger, department: str | None = None) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for e in ledger.events():
        d = e["data"]
        if e["kind"] == "handoff.created":
            out[d["id"]] = {**d, "status": "waiting", "at": e["at"]}
        elif e["kind"] == "handoff.reported" and d["id"] in out:
            out[d["id"]].update(status="done" if d["ran"] else "couldnt", output=d.get("output", ""), reported_at=e["at"])
    rows = [h for h in out.values() if department is None or h["department"] == department]
    return sorted(rows, key=lambda h: h["at"], reverse=True)


def report(ledger: Ledger, handoff_id: str, ran: bool, output: str, actor: Actor) -> dict[str, Any]:
    h = next((x for x in handoffs(ledger) if x["id"] == handoff_id), None)
    if not h or h["status"] != "waiting":
        raise SkillError("That hand-off isn't waiting.")
    ledger.append(actor, "handoff.reported", id=handoff_id, ran=bool(ran), output=output.strip()[:20_000])
    return h
