"""Datasheet intake: match a PDF to one component, find the right pages, extract limits."""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from pypdf import PdfReader, PdfWriter

from .gateway import Gateway, PdfPage
from .ledger import Actor, Ledger

Kind = Literal[
    "abs_max_supply_v", "op_supply_v", "abs_max_input_v", "vih_min_v", "vil_max_v", "voh_min_v", "vol_max_v",
    "abs_max_pin_current_ma", "supply_current_ma", "op_temp_c", "tj_max_c",
    "reg_vin_v", "reg_vout_v", "reg_dropout_v", "reg_iout_ma", "theta_ja_c_per_w",
    "driver_peak_ma", "stall_current_ma", "battery_v", "battery_capacity_mah", "battery_c_rate", "i2c_address", "other",
]

KIND_HELP = """Use exactly one kind per row:
abs_max_supply_v: absolute maximum supply voltage
op_supply_v: recommended operating supply range (min and max)
abs_max_input_v: absolute maximum voltage on digital input pins
vih_min_v, vil_max_v, voh_min_v, vol_max_v: logic thresholds
abs_max_pin_current_ma: maximum current per I/O pin
supply_current_ma: supply current of the device
op_temp_c: operating temperature range
tj_max_c: maximum junction temperature
reg_vin_v: regulator input voltage range
reg_vout_v: regulator output voltage
reg_dropout_v: regulator dropout voltage
reg_iout_ma: regulator maximum output current
theta_ja_c_per_w: junction-to-ambient thermal resistance
driver_peak_ma: motor driver peak output current
stall_current_ma: motor stall current
battery_v, battery_capacity_mah, battery_c_rate: battery ratings
i2c_address: I2C address; put each possible address in hex in addresses
other: anything else worth keeping"""

_PAGE_HEADINGS = [
    re.compile(r"absolute\s+maximum\s+ratings", re.I),
    re.compile(r"recommended\s+operating\s+conditions", re.I),
    re.compile(r"electrical\s+characteristics|dc\s+characteristics", re.I),
    re.compile(r"(slave|target|device|i2c)\s+address", re.I),
]
_TABLE_WORDS = re.compile(r"\b(min|max|typ)\b", re.I)
_CONTENTS = re.compile(r"\.{8,}")


_TEXTS: dict[tuple[str, int, int], list[str]] = {}
_PAGES: dict[tuple[str, int, int, int], str] = {}
BIG_PDF = 1_500_000  # bytes: above this, reading the text happens in a separate process so the app never stalls
_POOL = None


def _stamp(path: Path) -> tuple[str, int, int]:
    info = path.stat()
    return str(path), info.st_mtime_ns, info.st_size


def _cache_dir() -> Path:
    import os

    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache")
    return base / "BuildAnything" / "document-text"


def _cache_file(path: Path) -> Path:
    """Named by the document's content, so a renamed or copied file still finds its text."""
    import hashlib

    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:32]
    return _cache_dir() / f"{digest}.json"


def _extract_all(path: str) -> list[str]:
    """Every page's text. Runs in a separate process for big documents."""
    reader = PdfReader(path)
    out = []
    for page in reader.pages:
        try:
            out.append(page.extract_text() or "")
        except Exception:
            out.append("")
    return out


def _in_process(path: Path) -> list[str]:
    global _POOL
    from concurrent.futures import ProcessPoolExecutor

    try:
        if _POOL is None:
            _POOL = ProcessPoolExecutor(max_workers=1)
        return _POOL.submit(_extract_all, str(path)).result()
    except Exception:  # a process that can't start or dies: read it here instead, slower but still correct
        _POOL = None
        return _extract_all(str(path))


def page_texts(path: Path) -> list[str]:
    """Every page's text. Read once per document, ever: kept in memory and on disk (outside the project folder), so a
    600-page manual is read a single time, and that reading happens in its own process so the app keeps answering."""
    import json

    key = _stamp(path)
    if key in _TEXTS:
        return list(_TEXTS[key])
    cached = _cache_file(path)
    out = None
    if cached.is_file():
        try:
            out = json.loads(cached.read_text(encoding="utf-8"))["pages"]
        except (OSError, ValueError, KeyError):
            out = None
    if out is None:
        out = _in_process(path) if key[2] > BIG_PDF else _extract_all(str(path))
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps({"file": path.name, "pages": out}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    if len(_TEXTS) > 24:
        _TEXTS.clear()
    _TEXTS[key] = out
    return list(out)


def page_text(path: Path, page: int) -> str:
    """One page's text (1-based). Uses the document's saved text when there is some; otherwise reads only that page."""
    import json

    key = _stamp(path)
    if key in _TEXTS:
        texts = _TEXTS[key]
        return texts[page - 1] if 0 < page <= len(texts) else ""
    if (*key, page) not in _PAGES:
        text = None
        cached = _cache_file(path)
        if cached.is_file():
            try:
                pages = json.loads(cached.read_text(encoding="utf-8"))["pages"]
                _TEXTS[key] = pages
                text = pages[page - 1] if 0 < page <= len(pages) else ""
            except (OSError, ValueError, KeyError):
                text = None
        if text is None:
            reader = PdfReader(str(path))
            try:
                text = reader.pages[page - 1].extract_text() or "" if 0 < page <= len(reader.pages) else ""
            except Exception:
                text = ""
        if len(_PAGES) > 500:
            _PAGES.clear()
        _PAGES[(*key, page)] = text
    return _PAGES[(*key, page)]


def select_pages(texts: list[str], limit: int = 4) -> list[int]:
    """Zero-based pages holding limits tables. Reading only these protects the budget."""
    chosen: list[int] = []
    for heading in _PAGE_HEADINGS:
        for index, text in enumerate(texts):
            if heading.search(text) and (_TABLE_WORDS.search(text) or heading is _PAGE_HEADINGS[3]) and not _CONTENTS.search(text):
                if index not in chosen:
                    chosen.append(index)
                break
    return chosen[:limit]


_PROTOCOL = re.compile(r"\bregister|pointer|control\s+(byte|word)|command\s+byte|read\s+operation|write\s+operation|channel\s+select|data\s+format|\blsb\b|two'?s\s+complement|resolution", re.I)


def protocol_pages(texts: list[str], limit: int = 6, min_hits: int = 3) -> list[int]:
    """Zero-based pages that explain how to talk to a part: registers, pointers and bus formats."""
    scored = []
    for index, text in enumerate(texts):
        if _CONTENTS.search(text):
            continue
        hits = len(_PROTOCOL.findall(text))
        if hits >= min_hits:
            scored.append((hits, index))
    top = sorted(scored, reverse=True)[:limit]
    return sorted(index for _hits, index in top)


def one_page_pdf(path: Path, index: int) -> bytes:
    reader = PdfReader(str(path))
    writer = PdfWriter()
    writer.add_page(reader.pages[index])
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _squash(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def part_matches(part_number: str, texts: list[str]) -> bool:
    """Does this PDF describe this part? Ordering suffixes such as TMP102AIDRLR still match."""
    part = _squash(part_number)
    if len(part) < 3:
        return False
    whole = _squash(" ".join(texts[:6]))
    if part in whole:
        return True
    # A brand name or a board suffix shouldn't hide the part: TowerPro MG996R, ESP32-C6-DevKitC-1.
    for word in re.findall(r"[A-Za-z0-9-]+", part_number):
        pieces = word.split("-")
        for end in range(len(pieces), 0, -1):
            candidate = _squash("".join(pieces[:end]))
            if len(candidate) >= 5 and any(c.isdigit() for c in candidate) and candidate in whole:
                return True
    for token in re.findall(r"[A-Z0-9-]{4,}", " ".join(texts[:6]).upper()):
        family = _squash(token)
        if len(family) >= 5 and any(c.isdigit() for c in family) and part.startswith(family):
            return True
    return False


class Row(BaseModel):
    kind: Kind
    label: str
    min: float | None
    typ: float | None
    max: float | None
    unit: str | None
    conditions: str | None
    addresses: list[str] = []


class Extraction(BaseModel):
    rows: list[Row]


_UNIT_FACTOR = {"nv": 1e-9, "uv": 1e-6, "µv": 1e-6, "mv": 1e-3, "v": 1, "kv": 1e3, "ua": 1e-3, "µa": 1e-3, "na": 1e-6, "ma": 1, "a": 1e3}


def to_canonical(kind: str, value: float | None, unit: str | None) -> float | None:
    """Volts for _v kinds and milliamps for _ma kinds, so unit mistakes can't reach the checks."""
    if value is None:
        return None
    u = (unit or "").strip().lower().replace("μ", "µ")
    if kind.endswith("_v") and u in ("v", "mv", "kv", "µv", "uv", "nv"):
        return round(value * _UNIT_FACTOR[u], 9)
    if kind.endswith("_ma") and u in ("a", "ma", "ua", "µa", "na"):
        return round(value * _UNIT_FACTOR[u], 9)
    return value


def _number_forms(value: float) -> set[str]:
    magnitude = abs(value)
    forms = {f"{magnitude:g}", f"{magnitude:.1f}", f"{magnitude:.2f}", f"{magnitude:.3f}"}
    if magnitude == int(magnitude):
        forms.add(str(int(magnitude)))
    return forms


def value_on_page(row: Row, page_text: str) -> bool:
    """Every printed number must appear on its page. Signs are ignored because text extraction loses them."""
    numbers = [v for v in (row.min, row.typ, row.max) if v is not None]
    if not numbers:
        return bool(row.addresses)
    digits = re.findall(r"\d+(?:\.\d+)?", page_text)
    return all(_number_forms(n) & set(digits) for n in numbers)


async def extract_component(ledger: Ledger, gateway: Gateway, component_id: str, datasheet: dict[str, Any], path: Path) -> dict[str, Any]:
    import asyncio

    texts = await asyncio.to_thread(page_texts, path)  # a long manual takes a while: the app keeps answering meanwhile
    pages = select_pages(texts)
    if not pages:
        ledger.append(Actor.system("datasheet"), "datasheet.read", component=component_id, created=0)
        return {"pages": [], "created": 0, "problem": "No limits tables were found. This PDF may be a scan."}
    actor = Actor.agent("datasheet")
    created, unverified = 0, 0
    for index in pages:
        output: Extraction = await gateway.run(agent="datasheet", values={"part": ledger.state().records.get(component_id, {}).get("fields", {}).get("part_number", component_id), "page": f"Page {index + 1}"}, output_type=Extraction, request_limit=3, attachments=[PdfPage(await asyncio.to_thread(one_page_pdf, path, index))])
        for row in output.rows:
            on_page = value_on_page(row, texts[index])
            unverified += 0 if on_page else 1
            ledger.propose(
                actor, "parameter", component=component_id, datasheet=datasheet["id"], page=index + 1, kind=row.kind, label=row.label,
                min=to_canonical(row.kind, row.min, row.unit), typ=to_canonical(row.kind, row.typ, row.unit),
                max=to_canonical(row.kind, row.max, row.unit), unit=row.unit, conditions=row.conditions,
                addresses=row.addresses, value_on_page=on_page,
            )
            created += 1
    ledger.append(Actor.system("datasheet"), "datasheet.read", component=component_id, created=created)
    return {"pages": [p + 1 for p in pages], "created": created, "unverified": unverified, "problem": None}


MAX_PDF_BYTES = 40 * 1024 * 1024


def save_pdf(ledger: Ledger, component_id: str, data: bytes, filename: str, doc: str, actor: Actor, source_url: str = "", fetched_by: str = "") -> dict[str, Any]:
    """Files a component's PDF document, whoever brought it: the person uploading it, or a department fetching it from a
    public link. The same part used several times gets the one document."""
    import hashlib

    from . import documents

    record = ledger.state().records[component_id]
    digest = hashlib.sha256(data).hexdigest()[:10]
    folder = ledger.root / "datasheets"
    folder.mkdir(exist_ok=True)
    target = folder / f"{component_id}-{digest}.pdf"
    target.write_bytes(data)
    try:
        texts = page_texts(target)
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise ValueError("That PDF couldn't be read.") from exc
    matches = part_matches(record["fields"]["part_number"], texts)
    if doc == "datasheet":
        doc = documents.detect_kind(texts) or doc  # A board's user manual filed as its datasheet is filed as what it says it is.
    readable = folder / readable_name(record["fields"]["part_number"], doc, digest)
    if readable != target:  # named for the part and what the document is, so the folder makes sense to a person
        readable.unlink(missing_ok=True)
        target.rename(readable)
        target = readable
    same_part = [c["id"] for c in ledger.state().of_type("component") if c["status"] != "rejected" and c["fields"]["part_number"].lower() == record["fields"]["part_number"].lower()]
    extra = {**({"source_url": source_url} if source_url else {}), **({"fetched_by": fetched_by} if fetched_by else {})}
    for target_id in {component_id, *same_part}:
        ledger.propose(actor, "datasheet", component=target_id, file=target.name, filename=filename[:200], pages=len(texts), part_match=matches, doc=doc, **extra)
    return {"doc": doc, "pages": len(texts), "part_match": matches, "file": target.name}


def readable_name(part_number: str, doc: str, digest: str) -> str:
    """Such as XIAO-nRF52840-datasheet-0ce32d.pdf: the part, the kind of document, and a short fingerprint so two
    different files for one part never collide."""
    part = re.sub(r"[^A-Za-z0-9]+", "-", re.sub(r"\(.*?\)", "", part_number)).strip("-")[:48] or "part"
    kind = re.sub(r"[^a-z0-9]+", "-", (doc or "document").lower()).strip("-")[:24] or "document"
    return f"{part}-{kind}-{digest[:6]}.pdf"


def rename_documents(ledger: Ledger) -> list[tuple[str, str]]:
    """Gives documents saved under their record id (com_2f8a…-0ce3….pdf) readable names, keeping every reference."""
    state = ledger.state()
    folder = ledger.root / "datasheets"
    done: list[tuple[str, str]] = []
    seen: set[str] = set()
    for sheet in state.of_type("datasheet"):
        old = sheet["fields"].get("file", "")
        component = state.records.get(sheet["fields"].get("component", ""))
        if not old or old in seen or not component or not re.match(r"^com_[0-9a-f]+-[0-9a-f]+\.pdf$", old) or not (folder / old).is_file():
            continue
        seen.add(old)
        new = readable_name(component["fields"]["part_number"], sheet["fields"].get("doc", "datasheet"), old.rsplit("-", 1)[1].split(".")[0])
        if (folder / new).exists():
            continue
        (folder / old).rename(folder / new)
        ledger.append(Actor.system("documents"), "datasheet.renamed", old=old, new=new)
        done.append((old, new))
    return done


def fetch_pdf(url: str) -> bytes:
    """A PDF from a public web address. Addresses on this computer or a private network are refused."""
    import urllib.request

    from .citations import PageError, _public_host

    if not _public_host(url):
        raise PageError("Only public web addresses can be opened.")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; BuildAnything documents)"})
    with urllib.request.urlopen(request, timeout=60) as response:
        if not _public_host(response.geturl()):
            raise PageError("That link redirected somewhere private.")
        data = response.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise PageError("That PDF is too large.")
    if not data.startswith(b"%PDF-"):
        raise PageError("That link isn't a PDF. Find the direct link to the PDF file itself.")
    return data
