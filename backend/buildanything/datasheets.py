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


def page_texts(path: Path) -> list[str]:
    reader = PdfReader(str(path))
    out = []
    for page in reader.pages:
        try:
            out.append(page.extract_text() or "")
        except Exception:
            out.append("")
    return out


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
    texts = page_texts(path)
    pages = select_pages(texts)
    if not pages:
        ledger.append(Actor.system("datasheet"), "datasheet.read", component=component_id, created=0)
        return {"pages": [], "created": 0, "problem": "No limits tables were found. This PDF may be a scan."}
    actor = Actor.agent("datasheet")
    created, unverified = 0, 0
    for index in pages:
        output: Extraction = await gateway.run(agent="datasheet", values={"part": ledger.state().records.get(component_id, {}).get("fields", {}).get("part_number", component_id), "page": f"Page {index + 1}"}, output_type=Extraction, request_limit=3, attachments=[PdfPage(one_page_pdf(path, index))])
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
