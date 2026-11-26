"""Where a tool tab would help: results in this project that are worth seeing and that no tab shows yet.

It knows no tool by name, so it works the same for any CAD program, simulator, game engine, analysis tool or none.
It looks only at what's really there:
- connections whose actions give visual or debugging output (renders, screenshots, logs, inspections, results),
- result files the project's own programs or departments produced (pictures, plots, data, logs, models),
The Service agents see these and decide with the person what a tab should show; the person can say one isn't needed.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .ledger import Actor, Ledger

VISUAL = re.compile(r"render|screenshot|image|picture|view|plot|chart|graph|visual|mesh|export|inspect|debug|log|trace|simulat|result|report|waveform|measure|preview|capture", re.I)
RESULT_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".mp4", ".csv", ".json", ".log", ".xml", ".stl", ".obj", ".glb", ".gltf", ".ply", ".step", ".stp", ".html"}
AREAS = {"mechanics": "Mechanical", "electronics": "Electrical", "firmware": "Software", "software": "Software", "procurement": "Procurement", "simulation": "Simulation"}  # the app's own folders
SKIP = {"ledger", ".venv", "__pycache__", "node_modules", ".pio", ".git"}


def _dismissed(app_ledger: Ledger, project_id: str) -> set[str]:
    return {e["data"]["key"] for e in app_ledger.events() if e["kind"] == "tab_idea.dismissed" and e["data"].get("project") in (project_id, "*")}


def _covered(tabs: list[dict[str, Any]], *names: str) -> bool:
    """A tab already exists for this tool, or already reads these files."""
    wanted = [n.lower() for n in names if n]
    for t in tabs:
        text = " ".join(str(t.get(k, "")) for k in ("tool", "title", "approved_title", "id")).lower()
        if any(w in text for w in wanted):
            return True
        for r in t.get("approved_reads") or t.get("reads") or []:
            prefix = str(r.get("path", "")).split("*")[0].rstrip("/").lower()
            if prefix and any("/" in n and n.lower().rstrip("/").startswith(prefix) for n in names):
                return True
    return False


def _produced(project_root: Path, project_ledger: Ledger) -> dict[str, list[str]]:
    """Result files made by the project's programs (runs the app recorded) or saved by departments, grouped by folder."""
    made: set[str] = set()
    for e in project_ledger.events():
        if e["kind"] == "software.ran":
            made.update(e["data"].get("made") or [])
        elif e["kind"] == "deliverable.saved":
            made.add(e["data"].get("path", ""))
    out: dict[str, list[str]] = {}
    for rel in sorted(made):
        parts = Path(rel).parts
        if not rel or not (project_root / rel).is_file() or Path(rel).suffix.lower() not in RESULT_SUFFIXES or set(parts) & SKIP or parts[0] not in AREAS:
            continue
        out.setdefault(Path(rel).parent.as_posix(), []).append(rel)
    return out


def candidates(app_root: Path, app_ledger: Ledger, project_root: Path, team: list[str]) -> list[dict[str, Any]]:
    """Tab ideas for this project, most useful first: which Service agent would make it, for which department, what
    its cards could show, and why it came up."""
    from . import cloud, tabs

    project_id = project_root.name
    existing = tabs.listing(app_root, app_ledger)
    gone = _dismissed(app_ledger, project_id)
    project_ledger = Ledger(project_root)
    out: list[dict[str, Any]] = []

    store = cloud.store_for(app_root)
    calls: dict[str, int] = {}
    for e in app_ledger.events():
        if e["kind"] == "conn.called" and e["data"].get("project") == project_id:
            calls[e["data"]["id"]] = calls.get(e["data"]["id"], 0) + 1
    for c in store.listing() if store else []:
        m = c.get("approved")
        if not m:
            continue
        key = f"conn:{m['id']}"
        reads = [o["name"] for o in m["operations"] if not o["writes"] and VISUAL.search(f"{o['name']} {o['description']}")]
        scripted = [o["name"] for o in m["operations"] if o["writes"] and VISUAL.search(f"{o['name']} {o['description']}")]
        department = next((d for d in m["departments"] if d in team), None)
        if not (reads or scripted) or not department or key in gone or _covered(existing, m["id"], m["name"]):
            continue
        used = calls.get(m["id"], 0)
        shows = [f"live data from {', '.join(reads[:6])}" if reads else "",
                 f"the files that {', '.join(scripted[:4])} saves into the project, such as exports, screenshots and logs" if scripted else ""]
        also = [d for d in m["departments"] if d in team and d != department]
        out.append({"key": key, "tool": m["name"], "service": "External Service", "department": department, **({"also_for": also} if also else {}), "could_show": "; ".join(s for s in shows if s),
                    "why": f"{m['name']} is connected and has actions that give visual or debugging output" + (f"; this project has called it {used} times" if used else "") + ".",
                    "weight": 2 + min(used, 10)})

    for folder, files in _produced(project_root, project_ledger).items():
        key = f"files:{folder}"
        department = AREAS[Path(folder).parts[0]]
        department = department if department in team else ("Software" if "Software" in team else (team[0] if team else ""))
        if not department or key in gone or _covered(existing, folder + "/"):
            continue
        kinds = sorted({Path(f).suffix.lower() for f in files})
        out.append({"key": key, "tool": f"results in {folder}", "service": "Internal Service", "department": department,
                    "could_show": f"the {len(files)} result files there ({', '.join(kinds)}), newest first",
                    "why": f"The project's programs or departments saved result files in {folder}, and no tab shows them.", "weight": 1 + min(len(files), 5)})

    return sorted(out, key=lambda c: -c["weight"])


def dismiss(app_ledger: Ledger, project_id: str, key: str, actor: Actor) -> None:
    app_ledger.append(actor, "tab_idea.dismissed", project=project_id, key=key.strip()[:120])
