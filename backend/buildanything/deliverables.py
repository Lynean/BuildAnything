"""What every department has produced, readable by every agent.

Departments used to see only each other's last chat message. Now any agent can list and read the
others' reports and files: code, CAD results, collision shapes, bench results and stage reports.
Reading is free and changes nothing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import protocol
from .ledger import Ledger

TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".c", ".cpp", ".h", ".ino", ".ini", ".json", ".md", ".txt", ".urdf", ".xml", ".csv", ".html", ".css"}
AREAS = ("firmware", "mechanics", "interfaces", "reports", "bench")
SKIP_PARTS = {"meshes", "__pycache__", ".pio", "node_modules", "build", "obj", ".cache", ".venv"}
MAX_CHARS = 24_000
MAX_FILES = 300


def _visible(root: Path, path: Path) -> bool:
    rel = path.relative_to(root)
    return rel.parts[0] in AREAS and not (SKIP_PARTS & set(rel.parts)) and path.suffix.lower() in TEXT_SUFFIXES and path.is_file()


def report_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {k: report.get(k) for k in ("department", "type", "for_person", "done", "provisional", "not_checked", "can_give_now", "at")} | {"needs": [f"{n['owner']}: {n['what']}" for n in report.get("needs", [])]}


def index(ledger: Ledger) -> dict[str, Any]:
    """Every department's latest reports and every readable file, so an agent can find another's work instead of asking for it."""
    root, events = ledger.root, ledger.events()
    latest: dict[str, list[dict[str, Any]]] = {}
    for report in reversed(protocol.reports(events)):
        rows = latest.setdefault(report["department"], [])
        if len(rows) < 3:
            rows.append(report_summary(report))
    files = []
    for area in AREAS:
        base = root / area
        if base.is_dir():
            files += [{"path": p.relative_to(root).as_posix(), "bytes": p.stat().st_size} for p in sorted(base.rglob("*")) if _visible(root, p)]
    return {"reports": latest, "files": files[:MAX_FILES], "more_files": max(0, len(files) - MAX_FILES),
            "how": "read_deliverable(path) opens a file; department_reports(department) gives full reports with details."}


def read(ledger: Ledger, path: str, start: int = 0) -> dict[str, Any]:
    root = ledger.root.resolve()
    target = (root / path.strip().lstrip("/\\")).resolve()
    if root not in target.parents or not target.exists() or not _visible(root, target):
        return {"error": "Not a readable deliverable. Use list_deliverables to see the paths you can open."}
    text = target.read_text(encoding="utf-8", errors="replace")
    chunk = text[max(0, start): max(0, start) + MAX_CHARS]
    out: dict[str, Any] = {"path": target.relative_to(root).as_posix(), "text": chunk, "chars": len(text)}
    if start + MAX_CHARS < len(text):
        out["continue_from"] = start + MAX_CHARS
    return out


def department_reports(ledger: Ledger, department: str, count: int = 3) -> dict[str, Any]:
    rows = [r for r in protocol.reports(ledger.events()) if r["department"].lower() == department.strip().lower()][-max(1, min(count, 6)):]
    return {"reports": [{**report_summary(r), "details": (r.get("details") or "")[:4000], "evidence": r.get("evidence", [])} for r in rows]}


def tools(ledger: Ledger) -> list[Any]:
    def list_deliverables() -> dict[str, Any]:
        """Free. List every department's latest reports and every file the project has produced (code, CAD exports, deliverables, results, stage reports). Look here before asking another department for something it may already have made."""
        return index(ledger)

    def read_deliverable(path: str, start: int = 0) -> dict[str, Any]:
        """Free. Read one file from list_deliverables, such as a table in mechanics/deliverables/ or firmware/laptop/app.py. start continues a long file."""
        return read(ledger, path, start)

    def read_department_reports(department: str, count: int = 3) -> dict[str, Any]:
        """Free. Another department's recent reports in full: what it finished, what it couldn't check, what it can give now, and its details."""
        return department_reports(ledger, department, count)

    return [list_deliverables, read_deliverable, read_department_reports]
