"""Read-only view of a project's folder for the file explorer: CAD, firmware, simulations, reports, .git and the rest.

Nothing here writes. Every path is resolved inside the project folder, so a request can't read outside it.
"""
from __future__ import annotations

import base64
import csv
import io
import os
import re
import zipfile
from pathlib import Path
from typing import Any

from defusedxml import ElementTree as SafeET
from defusedxml import minidom as safe_minidom

MAX_ENTRIES = 2000
MAX_TEXT = 1_000_000  # bytes shown as text; bigger files are described, not sent
MAX_IMAGE = 8_000_000
IMAGES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp"}
MAX_ROWS, MAX_COLS = 1000, 60  # table viewers show this much of a sheet
MAX_RAW = 60_000_000

# How the app opens each kind of file. The Platform screen lists these, and the file types a project has that none cover.
VIEWERS: dict[str, str] = {
    **{e: "image" for e in IMAGES},
    ".pdf": "pdf", ".stl": "model", ".csv": "table", ".tsv": "table", ".xlsx": "table",
    ".md": "markdown", ".markdown": "markdown",
    ".json": "json", ".jsonl": "json", ".ipynb": "json",
    ".xml": "xml", ".urdf": "xml", ".sdf": "xml", ".svgz": "binary", ".kicad_sch": "text", ".kicad_pcb": "text",
}
TEXT_HINT = {".py", ".h", ".hpp", ".c", ".cpp", ".ino", ".ts", ".tsx", ".js", ".txt", ".ini", ".toml", ".yaml", ".yml", ".cfg", ".gitignore", ".gitattributes", ".log", ".map", ".html", ".css"}
# Files only the raw endpoint serves: what a viewer loads straight into the browser. Never HTML or script.
RAW_TYPES = {".pdf": "application/pdf", ".stl": "model/stl", **{e: t for e, t in IMAGES.items() if e != ".svg"}}


class FilesError(ValueError):
    pass


def inside(root: Path, rel: str) -> Path:
    base = root.resolve()
    target = (base / (rel or "").lstrip("/\\")).resolve()
    if target != base and base not in target.parents:
        raise FilesError("That path is outside the project.")
    if not target.exists():
        raise FilesError("Not found.")
    return target


def listing(root: Path, rel: str = "") -> dict[str, Any]:
    folder = inside(root, rel)
    if not folder.is_dir():
        raise FilesError("That's a file, not a folder.")
    base = root.resolve()
    entries = []
    for child in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:MAX_ENTRIES]:
        try:
            is_dir = child.is_dir()
            size = None if is_dir else child.stat().st_size
        except OSError:
            continue
        entries.append({"name": child.name, "path": child.resolve().relative_to(base).as_posix(), "dir": is_dir, "size": size})
    return {"path": folder.relative_to(base).as_posix() if folder != base else "", "entries": entries, "truncated": len(entries) >= MAX_ENTRIES}


def viewer_for(path: Path) -> str:
    suffix = path.suffix.lower() or path.name.lower()
    if suffix in VIEWERS:
        return VIEWERS[suffix]
    return "text" if suffix in TEXT_HINT else "unknown"


def raw(root: Path, rel: str) -> tuple[Path, str]:
    """The file itself, for viewers the browser runs (PDF, 3D models, images). Only those types, never HTML or scripts."""
    path = inside(root, rel)
    kind = RAW_TYPES.get(path.suffix.lower())
    if path.is_dir() or not kind:
        raise FilesError("This file can't be opened directly.")
    if path.stat().st_size > MAX_RAW:
        raise FilesError("This file is too big to open here.")
    return path, kind


def _table_csv(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    dialect = csv.excel_tab if path.suffix.lower() == ".tsv" else csv.excel
    rows = [r[:MAX_COLS] for _, r in zip(range(MAX_ROWS), csv.reader(io.StringIO(text), dialect))]
    return [{"name": path.name, "rows": rows, "more": text.count("\n") > MAX_ROWS}]


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _table_xlsx(path: Path) -> list[dict[str, Any]]:
    """Reads values (not formulas or styles) from each sheet of an .xlsx, with a hardened XML parser."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main", "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            for si in SafeET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter("{%s}t" % ns["m"])))
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for rel in SafeET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
                rels[rel.get("Id")] = rel.get("Target")
        sheets = []
        for sheet in SafeET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", ns) or []:
            target = rels.get(sheet.get("{%s}id" % ns["r"]), "")
            member = "xl/" + target.lstrip("/").removeprefix("xl/")
            if member not in names:
                continue
            grid: list[list[str]] = []
            for row in SafeET.fromstring(z.read(member)).iter("{%s}row" % ns["m"]):
                if len(grid) >= MAX_ROWS:
                    break
                cells: list[str] = []
                for c in row.findall("m:c", ns):
                    i = _col(c.get("r", "A1")) if c.get("r") else len(cells)
                    if i >= MAX_COLS:
                        continue
                    v = c.find("m:v", ns)
                    text = "".join(t.text or "" for t in c.iter("{%s}t" % ns["m"])) if c.get("t") == "inlineStr" else (v.text or "") if v is not None else ""
                    if c.get("t") == "s" and text.isdigit() and int(text) < len(shared):
                        text = shared[int(text)]
                    cells.extend([""] * (i - len(cells)))
                    cells.append(text)
                grid.append(cells)
            sheets.append({"name": sheet.get("name", "Sheet"), "rows": grid, "more": len(grid) >= MAX_ROWS})
        return sheets


def content(root: Path, rel: str, start: int | None = None) -> dict[str, Any]:
    """A text file bigger than MAX_TEXT comes back one page at a time, cut at line ends. With no start, the last page,
    since logs such as the ledger grow at the end."""
    path = inside(root, rel)
    if path.is_dir():
        raise FilesError("That's a folder.")
    size = path.stat().st_size
    out: dict[str, Any] = {"path": path.relative_to(root.resolve()).as_posix(), "name": path.name, "size": size, "viewer": viewer_for(path)}
    if out["viewer"] in ("pdf", "model"):
        return {**out, "kind": out["viewer"]}
    if out["viewer"] == "table":
        try:
            return {**out, "kind": "table", "sheets": _table_xlsx(path) if path.suffix.lower() == ".xlsx" else _table_csv(path)}
        except (zipfile.BadZipFile, KeyError, SafeET.ParseError, AttributeError) as exc:
            return {**out, "kind": "binary", "note": f"The table couldn't be read: {type(exc).__name__}."}
    kind = IMAGES.get(path.suffix.lower())
    if kind:
        if size > MAX_IMAGE:
            return {**out, "kind": "too_big"}
        return {**out, "kind": "image", "data": f"data:{kind};base64,{base64.b64encode(path.read_bytes()).decode()}"}
    if size > MAX_TEXT:
        with path.open("rb") as f:
            head = f.read(8000)
            if b"\x00" in head:
                return {**out, "kind": "binary"}
            begin = max(0, size - MAX_TEXT) if start is None else max(0, min(int(start), size - 1))
            f.seek(begin)
            raw = f.read(MAX_TEXT)
        if begin > 0 and b"\n" in raw:  # start on a whole line
            cut = raw.index(b"\n") + 1
            raw, begin = raw[cut:], begin + cut
        end = begin + len(raw)
        if end < size and b"\n" in raw:  # end on a whole line
            raw = raw[: raw.rindex(b"\n") + 1]
            end = begin + len(raw)
        return {**out, "kind": "text", "text": raw.decode("utf-8", errors="replace"), "start": begin, "end": end, "partial": True}
    raw = path.read_bytes()
    if b"\x00" in raw[:8000]:
        return {**out, "kind": "binary"}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    out = {**out, "kind": "text", "text": text}
    if out["viewer"] == "xml":
        try:  # indented for reading; the original text stays available
            out["pretty"] = re.sub(r"\n\s*\n", "\n", safe_minidom.parseString(raw).toprettyxml(indent="  "))
        except Exception:  # noqa: BLE001 - not well-formed: show it as it is
            pass
    return out


def formats(root: Path) -> dict[str, Any]:
    """Every kind of file in the project, how many, and whether the app can open it. For the Platform screen."""
    counts: dict[str, dict[str, Any]] = {}
    base = root.resolve()
    for folder, dirs, names in os.walk(base):
        dirs[:] = [d for d in dirs if d not in (".git", ".cache", "node_modules", "__pycache__", ".pio")]
        for name in names:
            path = Path(folder) / name
            key = path.suffix.lower() or name.lower()
            entry = counts.setdefault(key, {"type": key, "count": 0, "viewer": viewer_for(path), "example": path.relative_to(base).as_posix()})
            entry["count"] += 1
    rows = sorted(counts.values(), key=lambda r: -r["count"])
    return {"types": rows, "unsupported": [r for r in rows if r["viewer"] == "unknown"], "viewers": sorted(set(VIEWERS.values()) | {"text"})}
