"""Device facts: what code needs to talk to a part, read once from its documents and handed to the agents.

Like pins, each fact has one source. It's extracted with the page and exact words that state it,
confirmed by a person where it decides something, and becomes a constant in ba_parts.h. Code uses the
constant, never a typed number, so a wrong fact is one visible record instead of a hidden guess.
"""
from __future__ import annotations

import asyncio

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from . import datasheets as ds
from . import documents
from .gateway import Gateway, PdfPage
from .ledger import Actor, Ledger, ProjectState

FACTS: dict[str, tuple[str, str]] = {
    "i2c_address": ("The 7-bit I2C address this part uses, as 0x..", r"^0x[0-9a-f]{1,2}$"),
    "i2c_address_options": ("Every 7-bit address the part can take, comma separated", r"^0x[0-9a-f]{1,2}(\s*,\s*0x[0-9a-f]{1,2})+$"),
    "i2c_address_select": ("Which pin or jumper chooses the address, and how", r".{3,}"),
    "i2c_max_clock_hz": ("The highest I2C clock in hertz", r"^\d+$"),
    "spi_mode": ("The SPI mode, 0 to 3", r"^[0-3]$"),
    "spi_max_clock_hz": ("The highest SPI clock in hertz", r"^\d+$"),
    "spi_bit_order": ("MSB or LSB first", r"^(MSB|LSB)$"),
    "uart_baud": ("The default UART baud rate", r"^\d+$"),
    "uart_frame": ("The UART frame, such as 8N1", r"^[5-9][NEO][12]$"),
    "active_level": ("Whether a named signal is active HIGH or LOW. Give the signal", r"^(HIGH|LOW)$"),
    "pull": ("The pull resistor on a named signal: up, down or none. Give the signal", r"^(up|down|none)$"),
    "display_controller": ("The display's controller chip", r".{3,}"),
    "display_resolution": ("Width x height in pixels, such as 128x160", r"^\d+x\d+$"),
    "startup_delay_ms": ("How long after power-up before it can be used, in ms", r"^\d+(\.\d+)?$"),
    "pwm_frequency_hz": ("The PWM or servo signal frequency the part needs or uses by default, in hertz. Not a maximum", r"^\d+(\.\d+)?$"),
    "pulse_range_us": ("The control pulse width range in microseconds, as min-max, such as 500-2500", r"^\d+(\.\d+)?\s*-\s*\d+(\.\d+)?$"),
    "reset_pulse_ms": ("The shortest reset pulse, in ms", r"^\d+(\.\d+)?$"),
    "stall_torque_nm": ("A motor or servo's stall torque in N·m. Give the supply voltage it's stated at as the signal. Convert kgf·cm (x 0.0980665) or oz·in (x 0.00706155) and show the working", r"^\d+(\.\d+)?$"),
    "speed_s_per_60": ("A servo's no-load speed in seconds per 60 degrees. Give the supply voltage it's stated at as the signal", r"^\d+(\.\d+)?$"),
    "mass_g": ("The part's mass in grams", r"^\d+(\.\d+)?$"),
    "no_load_speed_rpm": ("A motor or gear motor's no-load speed at its output shaft in rpm. Give the supply voltage it's stated at as the signal", r"^\d+(\.\d+)?$"),
}
NEEDS_SIGNAL = ("active_level", "pull", "stall_torque_nm", "speed_s_per_60", "no_load_speed_rpm")
NUMERIC = ("i2c_max_clock_hz", "spi_max_clock_hz", "uart_baud", "startup_delay_ms", "reset_pulse_ms", "pwm_frequency_hz", "pulse_range_us", "stall_torque_nm", "speed_s_per_60", "mass_g", "no_load_speed_rpm")
QUALIFIER_WORDS = {"max": re.compile(r"\b(max|maximum|up\s+to)\b", re.I), "min": re.compile(r"\b(min|minimum|at\s+least)\b", re.I), "typical": re.compile(r"\b(typ|typical)\b", re.I)}
DECIDING = ("i2c_address", "i2c_address_options", "i2c_address_select", "spi_mode", "active_level", "pull", "uart_baud", "uart_frame", "spi_bit_order")  # Wrong, and the device doesn't work at all.
DEFINES_AS_NUMBER = ("i2c_address", "i2c_max_clock_hz", "spi_mode", "spi_max_clock_hz", "uart_baud", "startup_delay_ms", "reset_pulse_ms", "pwm_frequency_hz", "stall_torque_nm", "speed_s_per_60", "mass_g")
PARTS_HEADER = "include/ba_parts.h"
PARTS_MARK = "Generated from the project's confirmed device facts"
_HINTS = re.compile(r"no[\s-]load\s+speed|\brpm\b|r/min|gear\s+ratio|stall\s+torque|torque|operating\s+speed|s\s*/\s*60|weight|kgf?\s*[·.]?\s*cm|slave\s+address|i2c\s+address|device\s+address|\b0x[0-9a-f]{2}\b|spi\s+mode|\bcpol\b|\bcpha\b|clock\s+polarity|clock\s+phase|rising\s+edge|falling\s+edge|serial\s+(interface|clock|data)|[34][\s-](line|wire)\s+serial|\bscl\b|\bsda\b|\bsck\b|\bmosi\b|\bcsx\b|\bd/cx\b|chip\s+select|baud|active[\s-](low|high)|pull[\s-]?up|pull[\s-]?down|power[\s-]on|reset\s+(pulse|timing|low)|\bt\s?rw\b|\btresw\b|\btrt\b|user\s+button|push[\s-]?button|\bled\b|resolution|controller|\bmax(imum)?\s+(clock|frequency)|\bf\s?scl\b|\bt\s?scycw\b", re.I)


class FactOut(BaseModel):
    key: Literal[tuple(FACTS)]  # type: ignore[valid-type]
    signal: str = Field(default="", description="For active_level and pull: the signal or pin it describes, such as B1 or PC13. For stall_torque_nm and speed_s_per_60: the supply voltage it is stated at, such as 4.8 V. Otherwise empty")
    value: str
    qualifier: Literal["as stated", "max", "min", "typical", "default", "required"] = Field(default="as stated", description="Copy the page's qualifier: max for a maximum or 'up to', min for a minimum or 'at least', typical, default, or required. 'as stated' when the page gives no qualifier")
    quote: str = Field(description="The exact words or numbers on the page that state this fact, or that it is worked out from")
    derived: bool = Field(default=False, description="True when the value is worked out from what the page states, such as a maximum clock from a minimum clock period, or an SPI mode from which clock edge latches data")
    working: str = Field(default="", description="For a derived value: the calculation or reasoning, naming the page's values, such as '1 / 66 ns minimum SCL period = 15.2 MHz'. Empty otherwise")


class FactSheet(BaseModel):
    facts: list[FactOut]


RATIO = re.compile(r"(\d+(?:\.\d+)?)\s*:\s*1(?!\d)")


def fact_pages(texts: list[str], limit: int = 6, variant: str = "") -> list[int]:
    """Zero-based pages most likely to state bus settings, addresses, levels and timing. When the person named their
    variant's gear ratio, pages about other ratios are left out: a catalogue of 30 gearboxes isn't 30 facts about one."""
    mine = {float(r) for r in RATIO.findall(variant)}

    def other_variant(text: str) -> bool:
        found = {float(r) for r in RATIO.findall(text)}
        return bool(mine) and bool(found) and not (mine & found)

    scored = sorted(((len(_HINTS.findall(t)) + (10 if mine & {float(r) for r in RATIO.findall(t)} else 0), i) for i, t in enumerate(texts) if not ds._CONTENTS.search(t) and not other_variant(t)), reverse=True)
    return sorted(i for hits, i in scored[:limit] if hits >= 2)


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9.]", "", text.lower())


# The unit each numeric fact is kept in. A value written with exactly that unit keeps its number; any other unit,
# such as MHz for a value in hertz, is refused so the reader converts it and shows the working.
BASE_UNIT = {"_hz": r"hz", "_ms": r"ms", "_us": r"(?:us|µs|μs)", "_nm": r"n\s*[·.*]?\s*m", "_rpm": r"(?:rpm|r/min)", "_s_per_60": r"s(?:ec)?\s*/\s*60\s*(?:°|º|deg(?:rees?)?)?", "mass_g": r"g", "uart_baud": r"(?:baud|bps)"}


def bare_number(key: str, value: str) -> str:
    """A numeric fact keeps its number when the reader added its own unit, such as '55 g' or '0.17 s/60°'."""
    if key not in NUMERIC or key == "pulse_range_us":
        return value
    unit = next((u for end, u in BASE_UNIT.items() if key.endswith(end) or key == end), None)
    value = re.sub(r"^\s*(?:approx(?:imately|\.)?|about|around|≈|~)\s*", "", value, flags=re.I)  # "approximately 4.8 N·m" is still 4.8 N·m
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(.*?)\s*$", value)
    if unit and m and m.group(2) and re.fullmatch(unit, m.group(2), re.I):
        return m.group(1)
    return value


CONVERSIONS = {  # (key ending, unit pattern) -> factor to the key's own unit. Arithmetic, so code does it, not the reader.
    ("_nm", r"kgf?\s*[·.*-]?\s*cm"): 0.0980665,
    ("_nm", r"oz\s*[·.*-]?\s*in"): 0.00706155,
    ("_nm", r"mn\s*[·.*-]?\s*m"): 0.001,
    ("_hz", r"khz"): 1e3,
    ("_hz", r"mhz"): 1e6,
}


def convert(fact: FactOut) -> FactOut:
    """A value given in another common unit is converted, marked derived, with the working shown."""
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(.+?)\s*$", fact.value)
    if not m or fact.key not in NUMERIC:
        return fact
    for (end, unit), factor in CONVERSIONS.items():
        if fact.key.endswith(end) and re.fullmatch(unit, m.group(2), re.I):
            number = float(m.group(1)) * factor
            text = f"{number:.6g}" if not fact.key.endswith("_hz") else str(round(number))
            return fact.model_copy(update={"value": text, "derived": True, "working": (fact.working + "; " if fact.working else "") + f"{m.group(1)} {m.group(2)} x {factor:g} = {text}"})
    return fact


def valid(fact: FactOut) -> str | None:
    pattern = FACTS[fact.key][1]
    if not re.match(pattern, fact.value.strip(), re.I):
        return f"{fact.key} must look like {FACTS[fact.key][0].lower()}"
    if fact.key in NEEDS_SIGNAL and not fact.signal.strip():
        return f"{fact.key} needs the signal it describes"
    if fact.derived and len(fact.working.strip()) < 8:
        return "a derived value needs its working"
    for qualifier, words in QUALIFIER_WORDS.items():
        if words.search(fact.quote) and fact.qualifier != qualifier and fact.key in NUMERIC:
            return f"the quote says {qualifier}, so the qualifier must be {qualifier}"
    return None


async def extract(ledger: Ledger, gateway: Gateway, component_id: str) -> dict[str, Any]:
    """Reads each of the part's documents once, and proposes every fact found with its page and quote."""
    state = ledger.state()
    record = state.records[component_id]
    docs = [sheet for (cid, _kind), sheet in documents.latest_docs(state).items() if cid == component_id]
    created, unverified, read, refused = 0, 0, [], []
    seen: set[tuple[str, str, str, str]] = set()  # The same fact on several pages is saved once.
    actor = Actor.agent("device_facts")
    for sheet in docs:
        path = ledger.root / "datasheets" / sheet["fields"]["file"]
        texts = await asyncio.to_thread(ds.page_texts, path)  # a long manual takes a while: the app keeps answering meanwhile
        kind = documents.doc_kind(sheet)
        variant = record["fields"].get("variant", "")
        for index in await asyncio.to_thread(lambda: fact_pages(texts, variant=variant)):
            read.append(f"{documents.DOC_KINDS[kind]} p.{index + 1}")
            # The real page goes too, because extracted text scrambles tables and loses timing diagrams.
            output: FactSheet = await gateway.run(agent="device_facts", output_type=FactSheet, request_limit=3, attachments=[PdfPage(await asyncio.to_thread(ds.one_page_pdf, path, index))],
                                                  values={"part": record["fields"]["part_number"] + (f" (the person's variant: {record['fields']['variant']})" if record["fields"].get("variant") else ""), "document": documents.DOC_KINDS[kind], "page": f"Page {index + 1}:\n{texts[index][:6000]}"})
            for fact in output.facts:
                fact = convert(fact)
                fact.value = bare_number(fact.key, fact.value)
                same = (fact.key, fact.signal.strip().upper(), fact.value.strip().lower(), fact.qualifier)
                why = valid(fact)
                if why:
                    refused.append(f"{fact.key}={fact.value}: {why}")
                if why or same in seen:
                    continue
                seen.add(same)
                on_page = _squash(fact.quote) in _squash(texts[index]) and len(_squash(fact.quote)) >= 6
                unverified += 0 if on_page else 1
                trust = "medium" if fact.derived else documents.CONFIDENCE[kind]  # Worked-out values are never better than medium.
                ledger.propose(actor, "device_fact", component=component_id, datasheet=sheet["id"], doc=kind, page=index + 1, key=fact.key,
                               signal=fact.signal.strip(), value=fact.value.strip(), quote=fact.quote.strip(), on_page=on_page, confidence=trust,
                               derived=fact.derived, working=fact.working.strip(), qualifier=fact.qualifier)
                created += 1
    ledger.append(Actor.system("device_facts"), "facts.read", component=component_id, created=created, refused=refused[:10])
    return {"read": read, "created": created, "unverified": unverified, "refused": refused[:10]}


def _slug(text: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^A-Z0-9]+", "_", text.upper())).strip("_")[:28].strip("_")


def constant(state: ProjectState, fact: dict[str, Any]) -> str:
    f = fact["fields"]
    part = state.records.get(f["component"], {"fields": {"part_number": f["component"]}})["fields"]["part_number"]
    qualifier = f.get("qualifier", "as stated")
    tail = "" if qualifier in ("as stated", "default", "required") else qualifier.upper()
    return "BA_" + "_".join(x for x in (_slug(part), f["key"].upper(), _slug(f.get("signal", "")), tail) if x)


def current(state: ProjectState) -> list[dict[str, Any]]:
    """One fact per part, key and signal: a confirmed one wins, otherwise the newest proposal."""
    chosen: dict[tuple[str, str, str], dict[str, Any]] = {}
    for fact in state.of_type("device_fact"):
        if fact["status"] in ("rejected", "superseded"):
            continue
        f = fact["fields"]
        key = (f["component"], f["key"], f.get("signal", "").upper(), f.get("qualifier", "as stated"))
        if key not in chosen or fact["status"] == "confirmed" or chosen[key]["status"] != "confirmed":
            chosen[key] = fact
    return list(chosen.values())


def for_agents(state: ProjectState) -> list[dict[str, Any]]:
    return [{"constant": constant(state, f), "part": state.records.get(f["fields"]["component"], {"fields": {}})["fields"].get("part_number"),
             "fact": f["fields"]["key"], "signal": f["fields"].get("signal", ""), "value": f["fields"]["value"],
             "source": f"{documents.DOC_KINDS[f['fields'].get('doc', 'datasheet')]} p.{f['fields']['page']}",
             "confidence": f["fields"].get("confidence", "high"), "confirmed": f["status"] == "confirmed",
             "derived_from": f["fields"].get("working", "") if f["fields"].get("derived") else ""} for f in current(state)]


def header_text(state: ProjectState) -> str:
    lines = []
    for fact in sorted(current(state), key=lambda f: constant(state, f)):
        f, name = fact["fields"], constant(state, fact)
        note = f"/* {documents.DOC_KINDS[f.get('doc', 'datasheet')]} p.{f['page']}{', derived: ' + f['working'].replace('*/', '') if f.get('derived') else ''}{'' if fact['status'] == 'confirmed' else ', not confirmed yet'} */"
        value = f["value"]
        if f["key"] in DEFINES_AS_NUMBER or (f["key"] == "active_level"):
            lines.append(f"#define {name} {value} {note}")
        elif f["key"] == "spi_bit_order":
            lines.append(f"#define {name} {value}FIRST {note}")
        else:
            safe = value.replace("\\", "/").replace('"', "'")
            lines.append(f'#define {name} "{safe}" {note}')
    return f"/* {PARTS_MARK}. Do not edit. */\n#ifndef BA_PARTS_H\n#define BA_PARTS_H\n" + "\n".join(lines) + "\n#endif\n"


def write_header(root: Path, state: ProjectState, device: str) -> None:
    if current(state):
        target = root / "firmware" / device / PARTS_HEADER
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(header_text(state), encoding="utf-8", newline="\n")


def _value_in_quote(value: str, quote: str) -> bool:
    if "," in value:
        return all(_value_in_quote(part, quote) for part in value.split(",") if part.strip())
    v, q = value.strip().lower(), quote.lower()
    if re.fullmatch(r"0x[0-9a-f]+", v):
        return bool(re.search(rf"\b0x0*{int(v, 16):x}\b", q))
    if v in ("high", "low"):
        return bool(re.search(rf"\b{v}\b", q))
    digits = re.sub(r"[^0-9.]", "", v)
    return bool(digits) and digits in re.sub(r"[^0-9.]", " ", q).split() or v in q


def code_uses(root: Path) -> dict[str, list[dict[str, str]]]:
    """Which code blocks name each ba_parts.h constant."""
    from . import firmware

    out: dict[str, list[dict[str, str]]] = {}
    for device in firmware.devices(root):
        for file in firmware.scan_workspace(root, device):
            for block in file["blocks"] or [{"title": file["path"], "code": file.get("plain") or ""}]:
                for name in set(re.findall(r"\bBA_[A-Z0-9_]+\b", block.get("code", ""))):
                    out.setdefault(name, []).append({"device": device, "path": file["path"], "block": block.get("title", "")})
    return out


def review(ledger: Ledger, state: ProjectState) -> dict[str, dict[str, Any]]:
    """What the app could check itself, and whether a person still needs to look. Keyed by fact id."""
    uses = code_uses(ledger.root)
    facts = current(state)
    out = {}
    for fact in facts:
        f = fact["fields"]
        rivals = [g for g in state.of_type("device_fact") if g["id"] != fact["id"] and g["status"] not in ("rejected", "superseded")
                  and (g["fields"]["component"], g["fields"]["key"], g["fields"].get("signal", "").upper(), g["fields"].get("qualifier", "as stated")) == (f["component"], f["key"], f.get("signal", "").upper(), f.get("qualifier", "as stated"))
                  and g["fields"]["value"].strip().lower() != f["value"].strip().lower()]
        name = constant(state, fact)
        checks = [
            {"name": "Quote found word for word on the page", "ok": bool(f.get("on_page"))},
            {"name": "Value appears in the quote", "ok": f.get("derived") or _value_in_quote(f["value"], f["quote"]), "skip": bool(f.get("derived"))},
            {"name": "Stated on the page, not worked out", "ok": not f.get("derived")},
            {"name": "From a primary document", "ok": f.get("confidence", "high") == "high"},
            {"name": "No other source disagrees", "ok": not rivals, "detail": ", ".join(g["fields"]["value"] for g in rivals)},
            {"name": "Qualifier matches the quote", "ok": not any(words.search(f["quote"]) and f.get("qualifier", "as stated") != q for q, words in QUALIFIER_WORDS.items()) or f["key"] not in NUMERIC},
        ]
        verified = all(c["ok"] for c in checks if not c.get("skip"))
        used = name in uses
        matters = used or f["key"] in DECIDING
        verdict = "confirmed" if fact["status"] == "confirmed" else "verified" if verified else "needs_you" if matters else "unused"
        reasons = [c["name"] for c in checks if not c["ok"] and not c.get("skip")]
        out[fact["id"]] = {"verdict": verdict, "checks": checks, "used_by": uses.get(name, []), "matters": matters, "why": reasons}
    return out


def needing_you(ledger: Ledger, state: ProjectState) -> list[dict[str, Any]]:
    """Only facts that matter and that the app couldn't verify itself."""
    verdicts = review(ledger, state)
    return [f for f in current(state) if verdicts.get(f["id"], {}).get("verdict") == "needs_you"]


def trace(ledger: Ledger, state: ProjectState, fact_id: str) -> dict[str, Any]:
    """Everything from the page to the code, for one fact."""
    fact = state.records[fact_id]
    f = fact["fields"]
    sheet = state.records.get(f["datasheet"], {"fields": {}})
    path = ledger.root / "datasheets" / sheet["fields"].get("file", "")
    snippet = ""
    if path.is_file():
        text = ds.page_text(path, f["page"])
        squashed_quote = re.sub(r"\s+", " ", f["quote"]).strip()
        flat = re.sub(r"\s+", " ", text)
        at = flat.lower().find(squashed_quote.lower()[:40])
        snippet = flat[max(0, at - 160): at + len(squashed_quote) + 160] if at >= 0 else flat[:420]
    name = constant(state, fact)
    header_line = next((line for line in header_text(state).splitlines() if line.startswith(f"#define {name} ")), "")
    part = state.records.get(f["component"], {"fields": {}})["fields"].get("part_number", "")
    return {"fact": {"id": fact_id, **f, "status": fact["status"], "part": part}, "document": {"kind": documents.DOC_KINDS.get(f.get("doc", "datasheet")), "filename": sheet["fields"].get("filename", ""), "page": f["page"], "id": f["datasheet"], "snippet": snippet},
            "constant": name, "header_line": header_line, "review": review(ledger, state).get(fact_id, {})}


def typed_values(state: ProjectState, path: str, code: str) -> list[str]:
    """A driver that types a part's I2C address instead of using its constant."""
    out = []
    for fact in current(state):
        f = fact["fields"]
        if f["key"] != "i2c_address":
            continue
        number = int(f["value"], 16)
        if re.search(rf"\b0x0*{number:x}\b", code, re.I):
            out.append(f"{path} types the I2C address {f['value']}. Use {constant(state, fact)} from ba_parts.h, so the address has one source.")
    return out


def unread(ledger: Ledger, state: ProjectState) -> list[str]:
    """Confirmed parts with documents whose facts haven't been read yet. One entry per part number."""
    done = {e["data"]["component"] for e in ledger.events() if e["kind"] == "facts.read"}
    numbers = {c["fields"]["part_number"].lower() for c in state.records.values() if c["type"] == "component" and c["id"] in done}
    has_docs = {cid for (cid, _kind) in documents.latest_docs(state)}
    out = []
    for c in state.of_type("component", "confirmed"):
        number = c["fields"]["part_number"]
        if c["id"] in has_docs and documents.category(c) != "generic" and number.lower() not in numbers and number not in out:
            out.append(number)
    return out
