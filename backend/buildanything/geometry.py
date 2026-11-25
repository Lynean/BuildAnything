"""Free, tool-independent checks on exported STEP files."""
from __future__ import annotations

from itertools import combinations
from pathlib import Path
from typing import Any


def _load(path: Path):
    import cadquery as cq

    return cq.importers.importStep(str(path)).val()


def measure(path: Path) -> dict[str, Any]:
    shape = _load(path)
    box = shape.BoundingBox()
    holes: dict[str, int] = {}
    for face in shape.Faces():
        if face.geomType() == "CYLINDER":
            radius = face.radius() if callable(getattr(face, "radius", None)) else face.radius
            key = f"{radius * 2:.2f}"
            holes[key] = holes.get(key, 0) + 1
    return {"sizeMm": sorted(round(v, 3) for v in (box.xlen, box.ylen, box.zlen)), "volumeCm3": round(shape.Volume() / 1000, 4), "valid": bool(shape.isValid()), "cylinders": holes}


def clearance(files: dict[str, Path], min_gap_mm: float = 0.2) -> list[dict[str, Any]]:
    """Smallest gap between each pair. Parts must be exported in assembly coordinates."""
    shapes = {name: _load(path) for name, path in files.items()}
    out = []
    for a, b in combinations(sorted(shapes), 2):
        gap = shapes[a].distance(shapes[b])
        out.append({"a": a, "b": b, "gapMm": round(gap, 3), "status": "clash" if gap <= 0 else "tight" if gap < min_gap_mm else "ok"})
    return sorted(out, key=lambda r: r["gapMm"])
