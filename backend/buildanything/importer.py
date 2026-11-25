"""Import an existing project. The originals are only ever read."""
from __future__ import annotations

import os
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .citations import normalize
from .gateway import Gateway
from .ledger import Actor, Ledger
from .procurement import is_clean, redact

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pio", ".next", "dist", "build", ".cache"}
CATEGORIES = {
    "Source code": {".c", ".cpp", ".h", ".hpp", ".ino", ".py", ".ts", ".tsx", ".js", ".m"},
    "CAD models and drawings": {".step", ".stp", ".sldprt", ".sldasm", ".slddrw", ".f3z", ".f3d", ".ipt", ".dxf", ".stl", ".fcstd"},
    "PDF documents": {".pdf"},
    "Spreadsheets": {".xlsx", ".xls", ".csv"},
    "Text notes": {".md", ".txt"},
    "Circuit designs": {".kicad_sch", ".kicad_pcb", ".sch", ".brd"},
}
READ_FIRST = re.compile(r"^(readme|changes?|overview|requirements?|spec|architecture)", re.I)
SIZE = re.compile(r"\b\d+(?:\.\d+)?\s*x\s*\d+(?:\.\d+)?(?:\s*x\s*\d+(?:\.\d+)?)?\b", re.I)
PART_PREFIX = re.compile(r"^([A-Z]{1,3}\d{1,3}[a-z]?)\b")
DISABLED = re.compile(r"#define\s+(\w*(?:ENABLE|ENABLED)\w*)\s+0\b")
MD_LINK = re.compile(r"`([^`\n]+\.md)`|\]\(([^)#\s]+\.md)\)")
TOKENS_PER_CHAR = 0.3
PRICE_PER_TOKEN = 0.10 / 1e6


def _walk(root: Path):
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            yield Path(folder) / name


def scan(root: Path) -> dict[str, Any]:
    """Uses names and sizes only, so it costs nothing and sends nothing."""
    if not root.is_dir():
        raise FileNotFoundError("That folder wasn't found.")
    counts: Counter[str] = Counter()
    notes: list[str] = []
    total = 0
    for path in _walk(root):
        total += 1
        suffix = path.suffix.lower()
        category = next((c for c, exts in CATEGORIES.items() if suffix in exts), "Other")
        counts[category] += 1
        if suffix in (".md", ".txt") and READ_FIRST.match(path.name):
            notes.append(path.relative_to(root).as_posix())
    commits = None
    try:
        done = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=root, capture_output=True, text=True, timeout=20)
        if done.returncode == 0:
            commits = int(done.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return {"files": total, "counts": dict(counts), "commits": commits, "readFirst": sorted(notes)[:40]}


def _read(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def gaps(root: Path) -> list[dict[str, str]]:
    """Problems code can find without AI. Each one blocks only the work it affects."""
    out: list[dict[str, str]] = []
    by_folder: dict[Path, dict[str, set[str]]] = {}
    has_pdf_sheet, has_firmware, superseded = False, False, 0
    names = {p.name for p in _walk(root)}
    for path in _walk(root):
        relative = path.relative_to(root).as_posix()
        low = relative.lower()
        if "superseded" in low or "_old" in low:
            superseded += 1
            continue
        if "datasheet" in low:
            has_pdf_sheet = True
        if path.suffix.lower() in (".c", ".cpp", ".h", ".ino"):
            has_firmware = True
            if path.stat().st_size < 500_000:
                for flag in DISABLED.findall(_read(path)):
                    out.append({"level": "Review", "text": f"{flag} is switched off in {relative}", "effect": "Needs a Deviation record"})
        match = PART_PREFIX.match(path.name)
        size = SIZE.search(path.name)
        if match and size and path.suffix.lower() in (".step", ".stp", ".dxf"):
            sizes = by_folder.setdefault(path.parent, {}).setdefault(match.group(1), set())
            sizes.add(re.sub(r"\s", "", size.group(0)).lower())
        if path.suffix.lower() == ".md":
            for a, b in MD_LINK.findall(_read(path)):
                target = a or b
                if "/" not in target and target not in names:
                    out.append({"level": "Review", "text": f"{relative} refers to {target}, which is missing", "effect": "Documentation has drifted"})
    for folder, parts in by_folder.items():
        for part, sizes in parts.items():
            if len(sizes) > 1:
                relative = folder.relative_to(root).as_posix()
                out.append({"level": "Blocks that work", "text": f"{part} has {len(sizes)} different sizes in {relative}: {', '.join(sorted(sizes))}", "effect": "Blocks the next fabrication export"})
    if has_firmware and not has_pdf_sheet:
        out.append({"level": "Blocks that work", "text": "Firmware exists, but no datasheets were found", "effect": "Blocks changes to those components"})
    if superseded:
        out.append({"level": "Note", "text": f"{superseded} files are in superseded folders", "effect": "They won't be imported as current"})
    unique, seen = [], set()
    for gap in out:
        if gap["text"] not in seen:
            seen.add(gap["text"])
            unique.append(gap)
    return unique


def safe_notes(root: Path, chosen: list[str]) -> list[Path]:
    base = root.resolve()
    out = []
    for item in chosen:
        path = (root / item).resolve()
        if path.is_file() and base in path.parents:
            out.append(path)
    return out


def preview(root: Path, chosen: list[str], people: list[str]) -> dict[str, Any]:
    """Show exactly what would be sent, and what it costs, before anything leaves."""
    items = []
    for path in safe_notes(root, chosen):
        text = redact(_read(path, 40_000), people)
        clean = is_clean(text, people)
        items.append({"file": path.relative_to(root.resolve()).as_posix(), "text": text, "clean": clean})
    characters = sum(len(i["text"]) for i in items if i["clean"])
    return {"items": items, "estimatedUsd": round(characters * TOKENS_PER_CHAR * PRICE_PER_TOKEN * 3, 4)}


class ItemOut(BaseModel):
    kind: str = Field(description="requirement, component, device, or decision")
    text: str
    source_file: str
    quote: str = Field(description="Exact text from that file supporting this")


class DraftOut(BaseModel):
    items: list[ItemOut]


async def draft(ledger: Ledger, gateway: Gateway, root: Path, chosen: list[str], people: list[str]) -> dict[str, Any]:
    prepared = [i for i in preview(root, chosen, people)["items"] if i["clean"]]
    if not prepared:
        return {"created": 0, "rejected": 0}
    body = "\n\n".join(f"=== {i['file']} ===\n{i['text']}" for i in prepared)
    output: DraftOut = await gateway.run(agent="importer", values={"notes": body}, output_type=DraftOut, request_limit=3)
    texts = {i["file"]: normalize(i["text"]) for i in prepared}
    created, rejected = 0, 0
    for item in output.items:
        source = texts.get(item.source_file)
        if source is None or item.kind not in ("requirement", "component", "device", "decision") or len(normalize(item.quote)) < 6 or normalize(item.quote) not in source:
            rejected += 1
            continue
        ledger.propose(Actor.agent("importer"), "imported_item", kind=item.kind, text=item.text, source=item.source_file, quote=item.quote)
        created += 1
    return {"created": created, "rejected": rejected}


def confirm_item(ledger: Ledger, item_id: str, actor: Actor) -> None:
    """Confirming turns an imported suggestion into a real project record."""
    record = ledger.state().records[item_id]
    fields = record["fields"]
    ledger.append(actor, "record.confirmed", id=item_id)
    if fields["kind"] == "requirement":
        real = ledger.propose(actor, "requirement", key=f"IMP-{item_id[-4:]}", text=fields["text"], verification="To be set", imported_from=fields["source"])
    elif fields["kind"] == "component":
        real = ledger.propose(actor, "component", part_number=fields["text"], role="Imported", imported_from=fields["source"])
    elif fields["kind"] == "decision":
        real = ledger.propose(actor, "decision", text=fields["text"], imported_from=fields["source"])
    else:
        return
    ledger.append(actor, "record.confirmed", id=real)
