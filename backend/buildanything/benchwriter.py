"""Writes the laptop test bench for a device: a fake for each part, then scenarios from the requirements.

Neither agent sees the firmware. The fakes come from each part's documents, so a misunderstanding in the
firmware isn't copied into the test. Agents get compile errors back, never the results of their scenarios,
so they can't tune a test until it passes.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from . import bench, devicefacts, documents, firmware, page_memory
from . import datasheets as ds
from .gateway import Gateway
from .ledger import Actor, Ledger, ProjectState

SYSTEM = Actor.system("bench")
PAGE_HINTS = re.compile(r"\bcommand\b|\bregister\b|\binstruction\b|\b0x[0-9a-f]{2}\b|\b[0-9a-f]{2}h\b|timing|reset|power[\s-]on|sleep|\bcs\b|chip\s+select|\bd/c\b|\bdcx\b|data/command|serial\s+interface|\bspi\b|\bi2c\b|\buart\b|baud|address|pull[\s-]?(up|down)|active[\s-](low|high)|push[\s-]?button|user\s+button|\bled\b|backlight|pwm|pulse|frame|pixel|ram\s+write|column|row", re.I)
ALLOWED_INCLUDES = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.M)
OK_HEADERS = re.compile(r"^(bench\.h|ba_pins\.h|ba_parts\.h|ba_packets\.h|fakes\.h|[a-z_]+(\.h)?|c(std|math|string)\w*|stdint\.h|stddef\.h|string\.h|math\.h|stdio\.h|stdlib\.h)$")
RAW_PIN = re.compile(r"\bP[A-K]\d{1,2}\b|\bGPIO_?NUM_\d+\b")


class FakeOut(BaseModel):
    name: str = Field(description="Short file name for the fake, lowercase letters, numbers and underscores, without .h.")
    instance: str = Field(description="The name of the inline global instance.")
    code: str = Field(description="The whole header.")
    observe: list[str] = Field(description="Each public method scenarios can use, with what it means, e.g. 'bool tft.lit(): the panel shows an image'.")
    rules: list[str] = Field(description="Each rule the fake enforces with problem(), and the page it comes from.")
    gaps: list[str] = Field(description="Behaviour the documents didn't give, and what the fake assumed instead.")


class Covered(BaseModel):
    requirement: str
    scenario: str
    how: str


class NotCovered(BaseModel):
    requirement: str
    reason: str


class ScenariosOut(BaseModel):
    code: str = Field(description="The whole scenarios.cpp.")
    covered: list[Covered]
    not_covered: list[NotCovered]


def _member_pin(member: dict[str, Any]) -> str:
    return str(member["pin"]).strip()


def parts_for(state: ProjectState, root: Path, device: str) -> list[dict[str, Any]]:
    """Each part wired to the device's signal pins, with how it connects. A board's own buttons and LEDs count as a part."""
    defines = firmware.pin_defines(state)
    components = {c["id"]: c for c in state.of_type("component") if c["status"] != "rejected"}
    pins_file = root / "firmware" / device / firmware.PIN_HEADER
    used = set(re.findall(r"#define BA_PIN_(\w+)", pins_file.read_text(encoding="utf-8"))) if pins_file.exists() else set(defines)
    nets = {n["fields"]["name"]: n for n in state.of_type("net") if n["status"] != "rejected"}
    out: dict[str, dict[str, Any]] = {}
    for name, value in defines.items():
        if name not in used or firmware.BUILT_IN_LINK.search(name):
            continue  # The bench plays the laptop end of the board's own serial link itself.
        net = next((n for n in nets.values() if re.sub(r"[^A-Z0-9]+", "_", n["fields"]["name"].upper()).strip("_") == name), None)
        if not net:
            continue
        members = net["fields"]["members"]
        mcu = next((m for m in members if _member_pin(m).upper() == value.upper() or _member_pin(m).upper().endswith(value.upper())), None)
        if not mcu:
            continue
        for m in members:
            if m is mcu or m["component"] not in components:
                continue
            part = out.setdefault(m["component"], {"component": components[m["component"]], "connections": [], "built_in": m["component"] == mcu["component"]})
            part["connections"].append({"define": f"BA_PIN_{name}", "mcu_pin": value, "part_pin": _member_pin(m), "net": net["fields"]["name"]})
    return list(out.values())


def pages_for(root: Path, state: ProjectState, component_id: str, limit: int = 5) -> str:
    """The document pages most likely to describe how to talk to the part."""
    chunks = []
    for (cid, kind), sheet in documents.latest_docs(state).items():
        if cid != component_id:
            continue
        path = root / "datasheets" / sheet["fields"]["file"]
        if not path.exists():
            continue
        texts = ds.page_texts(path)
        scored = sorted(((len(PAGE_HINTS.findall(t)), i) for i, t in enumerate(texts) if not ds._CONTENTS.search(t)), reverse=True)
        for hits, i in sorted(scored[:limit], key=lambda x: x[1]):
            if hits >= 3:
                chunks.append(f"--- {documents.DOC_KINDS.get(kind, kind)}, page {i + 1} ---\n{texts[i][:4500]}")
    return "\n\n".join(chunks) or "No documents uploaded."


def _limits(state: ProjectState, component_id: str) -> str:
    rows = [p["fields"] for p in state.of_type("parameter", "confirmed") if p["fields"].get("component") == component_id]
    return "\n".join(f"{r['label']}: min {r['min']} typ {r['typ']} max {r['max']} {r['unit'] or ''} (p.{r['page']})" for r in rows) or "None."


def _facts(state: ProjectState, component_id: str) -> str:
    part = state.records.get(component_id, {"fields": {}})["fields"].get("part_number")
    rows = [f for f in devicefacts.for_agents(state) if f["part"] == part]
    return json.dumps(rows, indent=1) if rows else "None."


def _syntax(root: Path, device: str, header: Path) -> list[str]:
    """Compiles a fake on its own, without the firmware."""
    base = root / "firmware" / device
    folder = bench.bench_dir(root, device)
    (folder / "build" / "board").mkdir(parents=True, exist_ok=True)
    (folder / "build" / "board" / "bench_board.h").write_text(bench.board_header(base), encoding="utf-8", newline="\n")
    probe = folder / "build" / "probe.cpp"
    probe.write_text(f'#include "Arduino.h"\n#include "fakes/{header.name}"\n', encoding="utf-8")
    cmd = ["g++", "-fsyntax-only", "-std=gnu++17", "-fpermissive", "-w", str(probe), f"-I{folder / 'build' / 'board'}", f"-I{bench.RUNTIME}", f"-I{base / 'include'}", f"-I{folder}"]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return [e["text"] for e in bench._errors(done.stdout + done.stderr, base)][:8] if done.returncode else []


def _rule_problems(code: str, connections: list[dict[str, Any]]) -> list[str]:
    problems = []
    for header in ALLOWED_INCLUDES.findall(code):
        if not OK_HEADERS.match(header.split("/")[-1]) or header.startswith(("../", "src/", "driver", "mid", "app")):
            problems.append(f"Don't include {header}: a fake may use only bench.h, ba_pins.h, ba_parts.h and standard headers.")
    if RAW_PIN.search(re.sub(r"//.*|/\*.*?\*/", "", code, flags=re.S)):
        problems.append("Use the BA_PIN_ names from connections, never a typed pin like PA5.")
    known = {c["define"] for c in connections}
    for used in set(re.findall(r"\bBA_PIN_\w+", code)) - known:
        problems.append(f"{used} isn't connected to this part. Use only: {', '.join(sorted(known))}.")
    if "bench::Device" not in code:
        problems.append("The fake must derive from bench::Device.")
    if not re.search(r"^\s*inline\s+\w+", code, re.M):
        problems.append("Declare one inline global instance of the fake.")
    return problems


async def write_fake(ledger: Ledger, gateway: Gateway, root: Path, device: str, part: dict[str, Any]) -> dict[str, Any]:
    state = ledger.state()
    comp = part["component"]
    folder = bench.bench_dir(root, device) / "fakes"
    folder.mkdir(parents=True, exist_ok=True)
    describe = f"{comp['fields']['part_number']}: {comp['fields'].get('role', '')}"
    if part["built_in"]:
        describe += ". Fake only the parts built onto this board that the connections use, such as a user button or LED; the microcontroller itself is the firmware's."
    values = {
        "part": describe,
        "connections": "\n".join(f"{c['define']} = microcontroller {c['mcu_pin']} -> this part's {c['part_pin']} (net {c['net']})" for c in part["connections"]),
        "facts": _facts(state, comp["id"]),
        "limits": _limits(state, comp["id"]),
        "pages": pages_for(root, state, comp["id"]),
        "bench_api": (bench.RUNTIME / "bench.h").read_text(encoding="utf-8"),
        "feedback": "",
    }
    write_parts_header(root, state, device)
    search = page_memory.PageSession(ledger, root, max_pages=10).tools()[:2]  # search and open only: the bench writes no findings
    for attempt in range(3):
        out: FakeOut = await gateway.run(agent="bench_fake", output_type=FakeOut, values=values, tools=search, request_limit=16)
        name = re.sub(r"[^a-z0-9_]+", "_", out.name.lower()).strip("_")[:40] or f"part_{comp['id'][-6:]}"
        header = folder / f"{name}.h"
        problems = _rule_problems(out.code, part["connections"])
        if not problems:
            header.write_text(out.code, encoding="utf-8", newline="\n")
            problems = _syntax(root, device, header)
        if not problems:
            meta = {"component": comp["id"], "part_number": comp["fields"]["part_number"], "instance": out.instance, "observe": out.observe, "rules": out.rules, "gaps": out.gaps}
            header.with_suffix(".json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
            ledger.append(SYSTEM, "bench.fake_written", device=device, component=comp["id"], file=header.name, rules=len(out.rules), gaps=out.gaps[:6])
            return {"file": header.name, **meta}
        header.unlink(missing_ok=True)
        values["feedback"] = "\n".join(problems)
    return {"error": f"The fake for {comp['fields']['part_number']} still had problems: {'; '.join(problems)[:400]}"}


def write_parts_header(root: Path, state: ProjectState, device: str) -> None:
    """The bench's own copy of ba_parts.h, for firmware written before device facts existed. The firmware's copy wins when it has one."""
    if devicefacts.current(state):
        folder = bench.bench_dir(root, device)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "ba_parts.h").write_text(devicefacts.header_text(state), encoding="utf-8", newline=chr(10))


def fakes(root: Path, device: str) -> list[dict[str, Any]]:
    folder = bench.bench_dir(root, device) / "fakes"
    return [{"file": p.with_suffix(".h").name, **json.loads(p.read_text(encoding="utf-8"))} for p in sorted(folder.glob("*.json"))] if folder.exists() else []


def _packets(root: Path, device: str) -> str:
    header = root / "firmware" / device / "include" / firmware.GENERATED_FILE
    if not header.exists():
        return "None."
    text = header.read_text(encoding="utf-8")
    protos = re.findall(r"^[^\n#/]*\b(?:ba_\w+|\w+_(?:encode|decode))\s*\([^;{]*\)\s*[;{]", text, re.M)
    structs = re.findall(r"typedef struct\s*\{[^}]*\}\s*\w+;", text)
    return "\n".join(structs + [p.rstrip("{").strip() + ";" for p in protos])[:6000] or "None."


async def write_scenarios(ledger: Ledger, gateway: Gateway, root: Path, device: str) -> dict[str, Any]:
    state = ledger.state()
    requirements = [r["fields"] for r in state.of_type("requirement") if r["status"] != "rejected"]
    tests = [t["fields"] for t in state.of_type("test") if t["status"] != "rejected"]
    made = fakes(root, device)
    values = {
        "requirements": "\n".join(f"{r['key']}: {r['text']}" for r in requirements) or "None.",
        "tests": "\n".join(f"{t['key']} covers {', '.join(t.get('covers', []))}: {t['title']}. Pass if: {t['pass_if']}" + (f" Metric {t['metric']} {t.get('min')}..{t.get('max')} {t.get('unit') or ''}" if t.get("metric") else "") for t in tests) or "None.",
        "fakes": json.dumps([{k: f[k] for k in ("part_number", "instance", "observe", "gaps")} for f in made], indent=1),
        "packets": _packets(root, device),
        "bench_api": (bench.RUNTIME / "bench.h").read_text(encoding="utf-8"),
        "feedback": "",
    }
    target = bench.bench_dir(root, device) / "scenarios.cpp"
    for attempt in range(3):
        out: ScenariosOut = await gateway.run(agent="bench_tests", output_type=ScenariosOut, values=values, request_limit=2)
        problems = [f"Don't include {h}: scenarios use fakes.h, bench.h, ba_packets.h, ba_pins.h and standard headers." for h in ALLOWED_INCLUDES.findall(out.code) if not OK_HEADERS.match(h.split("/")[-1]) or h.startswith(("../", "src/"))]
        if "BENCH_SCENARIO" not in out.code:
            problems.append("Define each scenario with BENCH_SCENARIO.")
        if not problems:
            target.write_text(out.code, encoding="utf-8", newline="\n")
            built = bench.build(root, device)
            if not built["ok"]:
                own = [e["text"] for e in built["errors"] if e["where"] == "bench"]
                if own:
                    problems = own
                else:
                    break  # The firmware or bench itself didn't build: that's a result, not the writer's mistake.
        if not problems:
            break
        target.unlink(missing_ok=True)
        values["feedback"] = "\n".join(problems)
    else:
        return {"error": f"The scenarios still didn't compile: {'; '.join(problems)[:400]}"}
    meta = {"covered": [c.model_dump() for c in out.covered], "not_covered": [n.model_dump() for n in out.not_covered]}
    target.with_suffix(".json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    ledger.append(SYSTEM, "bench.scenarios_written", device=device, scenarios=out.code.count("BENCH_SCENARIO("), not_covered=[n.requirement for n in out.not_covered])
    return meta


async def build_bench(ledger: Ledger, gateway: Gateway, root: Path, device: str) -> dict[str, Any]:
    """Writes every missing fake, then the scenarios, then runs them."""
    state = ledger.state()
    if firmware.is_computer(root, device):
        return {"error": f"{device} runs on a computer: its code is checked by its own tests, not the bench."}
    parts = parts_for(state, root, device)
    if not parts:
        return {"error": "No parts are wired to this device's pins yet, so there's nothing to fake. Draft the connection list first."}
    have = {f["component"] for f in fakes(root, device)}
    written, failed = [], []
    for part in parts:
        if part["component"]["id"] in have:
            continue
        result = await write_fake(ledger, gateway, root, device, part)
        (failed if "error" in result else written).append(result.get("error") or result["part_number"])
    scen = await write_scenarios(ledger, gateway, root, device)
    if "error" in scen:
        return {"fakes_written": written, "fake_problems": failed, **scen}
    import asyncio

    from . import ci

    result = await asyncio.to_thread(bench.run, root, device)
    ci.record(ledger, f"Bench:{device}", bench.fingerprint(root, device), *ci.bench_verdict(ledger, device, result))
    return {"fakes_written": written, "fake_problems": failed, "not_covered": scen["not_covered"], "results": summary(root, device)}


def summary(root: Path, device: str) -> dict[str, Any]:
    last = bench.last_results(root, device)
    if not last:
        return {"state": "not run"}
    return {"passed": [s["title"] for s in last.get("scenarios", []) if s.get("passed")],
            "failed": [{"title": s["title"], "why": s.get("error") or "; ".join([c["text"] for c in s.get("checks", []) if not c["ok"]] + [p["text"] for p in s.get("problems", [])])[:300]} for s in last.get("scenarios", []) if not s.get("passed")],
            "errors": [e["text"] for e in last.get("errors", [])][:5]}
