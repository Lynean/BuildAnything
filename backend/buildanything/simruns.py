"""The simulation runs a project has saved, for the Simulation screen: each run's recording, pictures, results and assumptions.

Works for any simulator, because it reads only the files a run left in simulation/results/<run name>/."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RECORDINGS = {".gif", ".mp4", ".webm"}
IMAGES = {".png", ".jpg", ".jpeg", ".svg"}
TABLES = {".csv"}
MAX_JSON = 500_000


def _read_json(path: Path) -> Any:
    try:
        if path.stat().st_size > MAX_JSON:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def runs(project_root: Path) -> list[dict[str, Any]]:
    """Newest first. A run is a folder of results; files directly in results/ count as one run called 'results'."""
    base = project_root / "simulation" / "results"
    if not base.is_dir():
        return []
    folders = [p for p in base.iterdir() if p.is_dir()]
    if any(p.is_file() for p in base.iterdir()):
        folders.append(base)
    out = []
    for folder in folders:
        files = sorted((p for p in folder.rglob("*") if p.is_file()) if folder != base else (p for p in folder.iterdir() if p.is_file()), key=lambda p: p.name.lower())
        if not files:
            continue
        rel = lambda p: p.relative_to(project_root).as_posix()  # noqa: E731
        summary = next((p for p in files if p.name.lower() in ("summary.json", "results.json", "metrics.json")), None)
        assumptions = next((p for p in files if p.name.lower() == "assumptions.json"), None)
        out.append({
            "name": folder.name if folder != base else "results",
            "modified": max(p.stat().st_mtime for p in files),
            "recordings": [rel(p) for p in files if p.suffix.lower() in RECORDINGS],
            "images": [rel(p) for p in files if p.suffix.lower() in IMAGES],
            "tables": [rel(p) for p in files if p.suffix.lower() in TABLES],
            "summary": _read_json(summary) if summary else None,
            "assumptions": _read_json(assumptions) if assumptions else None,
            "other": [rel(p) for p in files if p.suffix.lower() not in RECORDINGS | IMAGES | TABLES and p not in (summary, assumptions)][:30],
        })
    return sorted(out, key=lambda r: -r["modified"])
