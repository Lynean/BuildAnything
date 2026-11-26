"""Interface definitions: one definition per packet, versioned, and acknowledged by whoever uses it."""
from __future__ import annotations

import importlib.util
import keyword
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from . import codegen as gen
from .gateway import Gateway
from .ledger import Actor, Ledger, LedgerError, ProjectState


class InterfaceError(LedgerError):
    pass


NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,40}$")
FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")
DEVICE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
RESERVED = set(keyword.kwlist) | {"auto", "break", "case", "char", "const", "continue", "default", "do", "double", "else", "enum", "extern", "float", "for", "goto", "if", "inline", "int", "long", "register", "restrict", "return", "short", "signed", "sizeof", "static", "struct", "switch", "typedef", "union", "unsigned", "void", "volatile", "while", "bool", "class", "new", "delete", "this", "template", "namespace", "public", "private", "protected", "virtual", "operator", "friend", "function", "var", "let", "export", "import", "typeof", "instanceof", "in", "of", "out", "data", "view", "m", "i", "cls", "self", "length", "capacity", "pkt_type", "schema", "crc", "ID", "VERSION", "SIZE", "MIN_SIZE", "encode", "decode", "var_length", "varLength"}
LIMITS = {"espnow": 250, "udp": 1472, "ble": 244, "can": 8, "tcp": None, "serial": None, "i2c": None, "spi": None, "other": None}
CHUNK_FIELDS = [{"name": "transfer_id", "type": "uint16", "note": "Identifies this transfer"}, {"name": "chunk_index", "type": "uint16"}, {"name": "chunk_count", "type": "uint16"}]
GENERATED_HEADER = "include/ba_packets.h"
GENERATED_PY = "generated/packets.py"
GENERATED_TS = "generated/packets.ts"


def _type_ok(kind: Any) -> bool:
    return isinstance(kind, str) and (gen.is_int(kind) or gen.is_float(kind) or kind == "var_bytes" or (gen.fixed_bytes(kind) or 0) > 0)


def _range_of(kind: str) -> tuple[int, int] | None:
    if kind == "enum8":
        kind = "uint8"
    if not gen.is_int(kind):
        return None
    bits = gen.INT_SIZES[kind] * 8
    return (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if kind.startswith("int") else (0, (1 << bits) - 1)


RESERVED_NAMES = {"RESULT", "CHUNK", "PRINT", "REASSEMBLY"}  # Each would generate a C type the generated code already defines.


def validate(packet: dict[str, Any]) -> list[str]:
    problems = []
    if not NAME.match(str(packet.get("name", ""))):
        problems.append("The packet name must be capitals, numbers and underscores, such as TELEMETRY.")
    elif packet["name"] in RESERVED_NAMES:
        problems.append(f"{packet['name']} is used by the generated code itself. Choose another packet name.")
    if not isinstance(packet.get("id"), int) or isinstance(packet.get("id"), bool) or not 0 <= packet["id"] <= 255:
        problems.append("The packet ID must be a number from 0 to 255.")
    transport = packet.get("transport") or {}
    if transport.get("type") not in LIMITS:
        problems.append(f"Transport must be one of: {', '.join(LIMITS)}.")
    if transport.get("type") == "udp" and not (isinstance(transport.get("port"), int) and 1 <= transport["port"] <= 65535):
        problems.append("A UDP packet needs a port from 1 to 65535.")
    if not DEVICE.match(str(packet.get("producer", ""))):
        problems.append("Name the device that sends this packet, in lowercase.")
    consumers = packet.get("consumers") or []
    if not consumers or not all(DEVICE.match(str(c)) for c in consumers):
        problems.append("List at least one device that receives this packet, in lowercase.")
    if packet.get("producer") in consumers:
        problems.append("A device can't be both the sender and a receiver of the same packet.")
    if packet.get("endianness", "little") not in ("little", "big"):
        problems.append("Byte order must be little or big.")
    if packet.get("standard"):
        return problems
    fields = packet.get("fields") or []
    seen: set[str] = set()
    for index, field in enumerate(fields):
        name, kind = field.get("name"), field.get("type")
        if not FIELD.match(str(name or "")) or name in RESERVED or str(name).endswith("_len"):
            problems.append(f"Field {index + 1}: \"{name}\" can't be used as a field name.")
        if name in seen:
            problems.append(f"Field {name} appears twice.")
        seen.add(str(name))
        if not _type_ok(kind):
            problems.append(f"{name}: unknown type {kind}.")
            continue
        if kind == "var_bytes":
            if index != len(fields) - 1:
                problems.append(f"{name}: only the last field can have variable length.")
            if not (isinstance(field.get("max"), int) and 1 <= field["max"] <= 65535):
                problems.append(f"{name}: variable-length fields need a maximum size.")
            continue
        limits = _range_of(kind)
        for key in ("const", "sentinel"):
            value = field.get(key)
            if value is not None and limits and not (isinstance(value, int) and limits[0] <= value <= limits[1]):
                problems.append(f"{name}: {key} {value} doesn't fit in {kind}.")
            if value is not None and not gen.is_int(kind) and key == "const":
                problems.append(f"{name}: only whole-number fields can be constants.")
        for key in ("min", "max"):
            if field.get(key) is not None and not gen.is_int(kind) and not gen.is_float(kind):
                problems.append(f"{name}: bytes can't have a {key}.")
            if field.get(key) is not None and limits and not limits[0] <= field[key] <= limits[1]:
                problems.append(f"{name}: {key} {field[key]} doesn't fit in {kind}.")
        if field.get("min") is not None and field.get("max") is not None and field["min"] > field["max"]:
            problems.append(f"{name}: the minimum is above the maximum.")
        if kind == "enum8":
            enum = field.get("enum") or {}
            if not enum or not all(re.match(r"^[A-Z][A-Z0-9_]*$", k) and isinstance(v, int) and 0 <= v <= 255 for k, v in enum.items()):
                problems.append(f"{name}: an enum needs named values from 0 to 255.")
        elif field.get("enum"):
            problems.append(f"{name}: only enum8 fields can have named values.")
    if packet.get("chunked") and (not fields or fields[-1].get("type") != "var_bytes"):
        problems.append("A chunked packet must end with a variable-length field holding each chunk.")
    if packet.get("raw") and packet.get("chunked"):
        problems.append("A raw layout can't be chunked automatically.")
    return problems


def wire(packet: dict[str, Any], major: int) -> list[dict[str, Any]]:
    """Every field on the wire, in order, with offsets."""
    fields: list[dict[str, Any]] = []
    if not packet.get("raw"):
        fields += [{"name": "pkt_type", "type": "uint8", "const": packet["id"], "auto": True}, {"name": "schema", "type": "uint8", "const": major % 256, "auto": True}]
    if packet.get("chunked"):
        fields += [{**f, "auto": True} for f in CHUNK_FIELDS]
    fields += [dict(f) for f in packet.get("fields", [])]
    at = 0
    for field in fields:
        field["offset"] = at
        at += 2 if field["type"] == "var_bytes" else gen._wire_size(field)
    return fields


def expand(name: str, entry: dict[str, Any], limits: bool = True) -> dict[str, Any]:
    """A packet as the rest of the app sees it."""
    packet = dict(entry["packet"])
    major, minor = entry["major"], entry["minor"]
    checksum = packet.get("checksum", not packet.get("raw", False))
    packet.update(major=major, minor=minor, version=f"{major}.{minor}", checksum=checksum, endianness=packet.get("endianness", "little"), acks=entry["acks"], history=entry["history"], waiting=[c for c in packet["consumers"] if c not in entry["acks"]])
    if packet.get("standard"):
        packet.update(wire=[], fixed_size=None, variable=False, header_size=0, simulated=entry.get("simulated"))
        return packet
    fields = wire(packet, major)
    var = next((f for f in fields if f["type"] == "var_bytes"), None)
    size = sum(2 if f["type"] == "var_bytes" else gen._wire_size(f) for f in fields) + (2 if checksum else 0)
    packet.update(wire=fields, fixed_size=size, variable=bool(var), max_size=size + (var["max"] if var else 0), header_size=2 if not packet.get("raw") else 0, simulated=entry.get("simulated"))
    return packet


def packets(state: ProjectState) -> list[dict[str, Any]]:
    return [expand(name, entry) for name, entry in sorted(state.packets.items(), key=lambda kv: kv[1]["packet"]["id"])]


def compare(old: dict[str, Any], new: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Returns (breaking changes, other changes). Anything that alters bytes or their meaning breaks."""
    breaking, minor = [], []
    for key, label in (("id", "ID"), ("endianness", "byte order"), ("checksum", "checksum"), ("raw", "framing"), ("chunked", "chunking"), ("standard", "standard")):
        default: Any = "little" if key == "endianness" else None
        before, after = old.get(key, default), new.get(key, default)
        if bool(before) != bool(after) if key in ("raw", "chunked", "checksum") else before != after:
            breaking.append(f"The {label} changed.")
    if (old.get("transport") or {}) != (new.get("transport") or {}):
        breaking.append("The transport changed.")
    before = old.get("fields") or []
    after = new.get("fields") or []
    if [f.get("name") for f in before] != [f.get("name") for f in after]:
        breaking.append("Fields were added, removed or reordered.")
    else:
        for a, b in zip(before, after):
            for key in ("type", "const", "unit", "sentinel"):
                if a.get(key) != b.get(key):
                    breaking.append(f"{a['name']}: {key} changed.")
            if a.get("type") == "var_bytes" and a.get("max") != b.get("max"):
                breaking.append(f"{a['name']}: maximum size changed.")
            old_enum, new_enum = a.get("enum") or {}, b.get("enum") or {}
            if any(new_enum.get(k) != v for k, v in old_enum.items()):
                breaking.append(f"{a['name']}: existing values changed.")
            elif old_enum != new_enum:
                minor.append(f"{a['name']}: values added.")
            if (a.get("min"), a.get("max")) != (b.get("min"), b.get("max")) and a.get("type") != "var_bytes":
                minor.append(f"{a['name']}: limits changed.")
            if a.get("note") != b.get("note"):
                minor.append(f"{a['name']}: note changed.")
    for key, label in (("rate_hz", "rate"), ("producer", "sender"), ("consumers", "receivers"), ("note", "note")):
        if old.get(key) != new.get(key):
            minor.append(f"The {label} changed.")
    return breaking, minor


def is_frozen(state: ProjectState) -> bool:
    from . import gates

    return gates.passed(state, "G3")


CLEAN_KEYS = ("name", "id", "transport", "rate_hz", "endianness", "producer", "consumers", "fields", "checksum", "raw", "chunked", "standard", "note")


def _approved_change(ledger: Ledger, name: str) -> str | None:
    """The person-approved packet change for this packet, if there is one: it lets an agent make that breaking change."""
    from . import changes

    for c in changes._fold(ledger).values():
        if c["kind"] == "packet" and c["target"] == name and c["status"] == "approved" and not (c["decision"] or {}).get("auto"):
            return f"#{c.get('number')}: {c['title']}"
    return None


def save_packet(ledger: Ledger, definition: dict[str, Any], actor: Actor, reason: str = "") -> dict[str, Any]:
    packet = {k: definition[k] for k in CLEAN_KEYS if definition.get(k) not in (None, "")}
    packet.setdefault("endianness", "little")
    problems = validate(packet)
    state = ledger.state()
    for other, entry in state.packets.items():
        if other != packet.get("name") and entry["packet"].get("id") == packet.get("id"):
            problems.append(f"ID {packet.get('id')} is already used by {other}.")
    if problems:
        raise InterfaceError(" ".join(problems))
    previous = state.packets.get(packet["name"])
    if previous is None:
        major, minor, breaking, changes = 1, 0, False, ["First version."]
    else:
        hard, soft = compare(previous["packet"], packet)
        if not hard and not soft:
            return expand(packet["name"], previous)
        breaking = bool(hard)
        major, minor = (previous["major"] + 1, 0) if breaking else (previous["major"], previous["minor"] + 1)
        changes = hard + soft
        if breaking and is_frozen(state):
            approved = _approved_change(ledger, packet["name"]) if actor.kind == "agent" else None
            if approved:  # a person already approved this exact change, so the agent carries it out under the same name
                reason = reason.strip() or f"Approved change {approved}"
            elif actor.kind == "agent":
                raise InterfaceError(f"Interfaces are frozen. A person must approve a breaking change to {packet['name']}: raise a change request of kind packet for it. Never save it under a new name to get round this.")
            if len(reason.strip()) < 8:
                raise InterfaceError("Interfaces are frozen. Give a reason for this breaking change.")
    built = expand(packet["name"], {"packet": packet, "major": major, "minor": minor, "acks": {}, "history": []})
    limit = LIMITS[packet["transport"]["type"]]
    if limit is not None and not packet.get("standard") and (built.get("max_size") or 0) > limit:
        raise InterfaceError(f"{packet['name']} can reach {built['max_size']} bytes, but {packet['transport']['type']} carries {limit}. Make it smaller or chunk it.")
    ledger.append(actor, "packet.saved", packet=packet, major=major, minor=minor, breaking=breaking, changes=changes, reason=reason.strip())
    return expand(packet["name"], ledger.state().packets[packet["name"]])


def acknowledge(ledger: Ledger, name: str, consumer: str, actor: Actor) -> None:
    entry = ledger.state().packets.get(name)
    if entry is None:
        raise InterfaceError("Packet not found.")
    if consumer not in entry["packet"]["consumers"]:
        raise InterfaceError(f"{consumer} doesn't use {name}.")
    ledger.append(actor, "packet.acked", name=name, consumer=consumer, major=entry["major"])


def remove_packet(ledger: Ledger, name: str, reason: str, actor: Actor) -> None:
    state = ledger.state()
    if name not in state.packets:
        raise InterfaceError("Packet not found.")
    if is_frozen(state) and len(reason.strip()) < 8:
        raise InterfaceError("Interfaces are frozen. Give a reason for removing this packet.")
    ledger.append(actor, "packet.removed", name=name, reason=reason.strip())


# ---- Generated files ----


def _wanted_files(root: Path, built: list[dict[str, Any]]) -> dict[Path, str]:
    files = gen.generate_all(built)
    out = {root / "interfaces" / "generated" / name: text for name, text in files.items()}
    involved = {d for p in built for d in [p["producer"], *p["consumers"]]}
    base = root / "firmware"
    if base.exists():
        for device in sorted(p for p in base.iterdir() if p.is_dir() and p.name in involved):
            if (device / "platformio.ini").exists():
                out[device / GENERATED_HEADER] = files["ba_packets.h"]
            else:
                out[device / GENERATED_PY] = files["packets.py"]
                out[device / GENERATED_TS] = files["packets.ts"]
    return out


def regenerate(root: Path, state: ProjectState) -> list[str]:
    built = packets(state)
    if not built:
        return []
    written = []
    for path, text in _wanted_files(root, built).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8", newline="\n")
            written.append(path.relative_to(root).as_posix())
    return written


def _device_source(root: Path, device: str) -> dict[str, str]:
    """The code a person or agent wrote for a device: generated files and tests don't count."""
    base = root / "firmware" / device
    if not base.is_dir():
        return {}
    out = {}
    for path in base.rglob("*"):
        parts = path.relative_to(base).parts
        if not path.is_file() or path.suffix not in (".c", ".cpp", ".ino", ".h", ".py", ".ts", ".tsx", ".js") or {".pio", "generated", "node_modules", "__pycache__", ".venv", "tests", "test"} & set(parts) or path.stem.startswith("test_") or path.stem.endswith(("_test", "_tests")) or path.name in ("ba_packets.h", "ba_pins.h"):
            continue
        out[path.suffix] = out.get(path.suffix, "") + path.read_text(encoding="utf-8", errors="replace")
    return out


def _uses(source: dict[str, str], name: str, action: str, serial: bool) -> bool:
    """action is encode or decode. C names are per packet, and Python and TypeScript use a class."""
    snake = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    cls = gen.class_name(name)
    for suffix, text in source.items():
        if suffix in (".c", ".cpp", ".ino", ".h"):
            found = f"ba_{snake}_{action}" in text
            framed = ("ba_serial_frame" if action == "encode" else "ba_serial_unframe") in text
        elif suffix == ".py":
            found = cls in text and (f".{action}(" in text or (action == "decode" and "decode_any" in text))
            framed = ("serial_frame" if action == "encode" else "serial_unframe") in text
        else:
            found = cls in text
            framed = ("serialFrame" if action == "encode" else "serialUnframe") in text
        if found and (framed or not serial):
            return True
    return False


def unwired(root: Path, state: ProjectState, only: str | None = None) -> list[str]:
    """A packet that's defined but never sent or read does nothing, even if everything compiles."""
    out = []
    for packet in packets(state):
        serial = (packet.get("transport") or {}).get("type") == "serial"
        for device, action, verb in [(packet["producer"], "encode", "send"), *[(c, "decode", "read") for c in packet["consumers"]]]:
            if only and device != only:
                continue
            source = _device_source(root, device)
            if not source:
                out.append(f"{device} has no code yet to {verb} {packet['name']}.")
            elif not _uses(source, packet["name"], action, serial):
                out.append(f"{device}'s code doesn't {verb} {packet['name']} yet" + (" over the serial link." if serial else "."))
    return out


def drift(root: Path, state: ProjectState) -> list[str]:
    """Generated files that no longer match the definitions, for example after a hand edit."""
    built = packets(state)
    if not built:
        return []
    return [p.relative_to(root).as_posix() for p, text in _wanted_files(root, built).items() if not p.exists() or p.read_text(encoding="utf-8") != text]


def generated_text(root: Path, name: str) -> str:
    if name not in ("ba_packets.h", "packets.py", "packets.ts"):
        raise InterfaceError("File not found.")
    path = root / "interfaces" / "generated" / name
    if not path.exists():
        raise InterfaceError("Nothing has been generated yet.")
    return path.read_text(encoding="utf-8")


def load_python(root: Path) -> Any:
    path = root / "interfaces" / "generated" / "packets.py"
    spec = importlib.util.spec_from_file_location(f"ba_generated_{uuid.uuid4().hex}", path)
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = module  # Dataclasses look up their module while being defined.
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def sample_values(packet: dict[str, Any], seed: int = 0) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field in gen.payload(packet):
        kind = field["type"]
        count = gen.fixed_bytes(kind)
        if count is not None:
            values[field["name"]] = bytes((i + seed) % 256 for i in range(count))
        elif kind == "var_bytes":
            values[field["name"]] = bytes((i + seed) % 256 for i in range(min(5, field["max"])))
        elif field.get("enum"):
            values[field["name"]] = min(field["enum"].values())
        elif gen.is_float(kind):
            low, high = field.get("min"), field.get("max")
            value = 1.5 + seed
            values[field["name"]] = float(max(low, value) if low is not None else value) if high is None else min(float(high), max(float(low if low is not None else high), value))
        else:
            low = field.get("min")
            high = field.get("max")
            top = _range_of(kind)[1]
            value = 7 + seed
            value = max(value, low) if low is not None else value
            values[field["name"]] = min(value, high if high is not None else top)
    if packet.get("chunked"):
        values.update(chunk_index=0, chunk_count=1, transfer_id=seed + 1)
    return values


# ---- Checking the generated code against itself, across languages ----


C_HARNESS_TOP = '#include <stdio.h>\n#include "ba_packets.h"\nstatic uint8_t in_buf[70000], out_buf[70000];\nstatic int hexv(char c){return c<=\'9\'?c-\'0\':c-\'a\'+10;}\nint main(int argc,char**argv){\n (void)argc; size_t n=0; for(char*p=argv[2];*p;p+=2) in_buf[n++]=(uint8_t)(hexv(p[0])*16+hexv(p[1]));\n'


def _c_harness(built: list[dict[str, Any]]) -> str:
    body = [C_HARNESS_TOP]
    for p in built:
        low = p["name"].lower()
        body.append(f' if(!strcmp(argv[1],"{p["name"]}")){{ static ba_{low}_t m; ba_result_t s=ba_{low}_decode(in_buf,n,&m); printf("%d ",(int)s); if(s==BA_OK){{ size_t k=ba_{low}_encode(&m,out_buf,sizeof(out_buf)); for(size_t i=0;i<k;i++)printf("%02x",out_buf[i]); }} return 0; }}\n')
    body.append(" return 9;\n}\n")
    return "".join(body)


def _ts_harness(built: list[dict[str, Any]]) -> str:
    names = sorted({gen.class_name(p["name"]) for p in built})
    imports = ", ".join(f"encode{n}, decode{n}" for n in names)
    table = ",".join(f'{p["name"]}:[decode{gen.class_name(p["name"])},encode{gen.class_name(p["name"])}]' for p in built)
    return f'''import {{ {imports} }} from "./packets.ts";
const table: any = {{{table}}};
const [name, hex] = process.argv.slice(2);
try {{
  const [decode, encode] = table[name];
  const out = encode(decode(new Uint8Array(Buffer.from(hex, "hex"))));
  process.stdout.write("0 " + Buffer.from(out).toString("hex"));
}} catch (e: any) {{ process.stdout.write(e.status ?? String(e)); }}
'''


def _run(command: list[str], cwd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)


STATUS_CODE = {"size": "1", "type": "2", "version": "3", "checksum": "4", "const": "5", "range": "6"}


def _cases(module: Any, built: list[dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    """(packet, bytes as hex, expected status, expected re-encoded hex)."""
    cases = []
    for p in built:
        cls = getattr(module, gen.class_name(p["name"]))
        good = cls(**sample_values(p)).encode()
        cases.append((p["name"], good.hex(), "ok", good.hex()))
        if p.get("checksum"):
            bad = bytearray(good)
            bad[-3 if len(good) > 4 else -1] ^= 0x40  # A data byte, or the checksum itself when there's no data.
            cases.append((p["name"], bytes(bad).hex(), "checksum", ""))
        if not p.get("raw"):
            old = bytearray(good)
            old[1] = (old[1] + 1) % 256
            cases.append((p["name"], bytes(old).hex(), "version", ""))
            cases.append((p["name"], good[:-1].hex(), "size", ""))
    return cases


def check_generated(root: Path, state: ProjectState) -> dict[str, Any]:
    """Round-trips sample packets through each language, and confirms damage is detected."""
    built = [p for p in packets(state) if not p.get("standard")]
    if not built:
        raise InterfaceError("Define a packet first.")
    regenerate(root, state)
    module = load_python(root)
    cases = _cases(module, built)
    results: dict[str, Any] = {}

    failures = []
    for name, data, status, again in cases:
        cls = getattr(module, gen.class_name(name))
        try:
            out = cls.decode(bytes.fromhex(data)).encode().hex()
            got = "ok"
        except module.PacketError as exc:
            out, got = "", exc.status
        if got != status or (status == "ok" and out != again):
            failures.append(f"{name}: expected {status}, got {got}")
    results["Python"] = {"ran": True, "ok": not failures, "detail": failures[0] if failures else f"{len(cases)} cases passed."}

    with tempfile.TemporaryDirectory(prefix="ba-check-") as folder:
        generated = root / "interfaces" / "generated"
        for name in ("ba_packets.h", "packets.ts"):
            shutil.copy(generated / name, folder)
        compiler = shutil.which("gcc") or shutil.which("cc")
        if compiler is None:
            results["C"] = {"ran": False, "ok": False, "detail": "No C compiler was found on this computer."}
        else:
            results["C"] = _check_c(folder, compiler, built, cases)
        results["TypeScript"] = _check_ts(folder, built, cases)
    return results


def _check_c(folder: str, compiler: str, built: list[dict[str, Any]], cases: list[tuple[str, str, str, str]]) -> dict[str, Any]:
    source = Path(folder) / "harness.c"
    source.write_text(_c_harness(built), encoding="utf-8", newline="\n")
    done = _run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-o", "harness.exe", "harness.c"], folder)
    if done.returncode != 0:
        return {"ran": True, "ok": False, "detail": (done.stderr or done.stdout)[:500]}
    cpp = shutil.which("g++")
    if cpp:
        (Path(folder) / "cpp.cpp").write_text('#include "ba_packets.h"\nint main(){return 0;}\n', encoding="utf-8", newline="\n")
        again = _run([cpp, "-std=c++17", "-Wall", "-Werror", "-o", "cpp.exe", "cpp.cpp"], folder)
        if again.returncode != 0:
            return {"ran": True, "ok": False, "detail": "C++: " + (again.stderr or again.stdout)[:500]}
    for name, data, status, expected in cases:
        out = _run([str(Path(folder) / "harness.exe"), name, data], folder).stdout.strip()
        want = "0" if status == "ok" else STATUS_CODE[status]
        got, _, body = out.partition(" ")
        if got != want or (status == "ok" and body != expected):
            return {"ran": True, "ok": False, "detail": f"{name}: expected {status}, C returned {out[:60]}"}
    return {"ran": True, "ok": True, "detail": f"{len(cases)} cases passed, and the header compiles as C and C++." if cpp else f"{len(cases)} cases passed."}


def _check_ts(folder: str, built: list[dict[str, Any]], cases: list[tuple[str, str, str, str]]) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        return {"ran": False, "ok": False, "detail": "Node.js wasn't found on this computer."}
    version = _run([node, "--version"], folder).stdout.strip().lstrip("v").split(".")
    if tuple(int(v) for v in version[:2]) < (22, 18):
        return {"ran": False, "ok": False, "detail": "This check needs Node.js 22.18 or newer."}
    (Path(folder) / "harness.ts").write_text(_ts_harness(built), encoding="utf-8", newline="\n")
    for name, data, status, expected in cases:
        done = _run([node, "--disable-warning=ExperimentalWarning", "harness.ts", name, data], folder)
        out = done.stdout.strip()
        if status == "ok":
            if out != f"0 {expected}":
                return {"ran": True, "ok": False, "detail": f"{name}: {(out or done.stderr)[:300]}"}
        elif out != status:
            return {"ran": True, "ok": False, "detail": f"{name}: expected {status}, got {(out or done.stderr)[:300]}"}
    return {"ran": True, "ok": True, "detail": f"{len(cases)} cases passed."}


# ---- Simulator: real UDP on this computer, no hardware ----


FAULTS = ("none", "drop", "corrupt", "old_version", "truncate", "wrong_port")
CHECK_NAMES = ["The sender sent it", "It reached the device", "The device could read it", "The device accepted it", "The device carried it out"]


def simulate(ledger: Ledger, name: str, fault: str = "none", nodes: int = 2, count: int = 10, actor: Actor | None = None) -> dict[str, Any]:
    state = ledger.state()
    if name not in state.packets:
        raise InterfaceError("Packet not found.")
    if fault not in FAULTS:
        raise InterfaceError("Unknown fault.")
    packet = expand(name, state.packets[name])
    if packet.get("standard"):
        raise InterfaceError("This packet follows an outside standard, so it has no generated code to simulate.")
    nodes, count = max(1, min(nodes, 8)), max(1, min(count, 100))
    regenerate(ledger.root, state)
    module = load_python(ledger.root)
    cls = getattr(module, gen.class_name(name))
    hub = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    hub.bind(("127.0.0.1", 0))
    hub.settimeout(0.4)
    address = hub.getsockname()
    closed = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    closed.bind(("127.0.0.1", 0))
    wrong = closed.getsockname()
    closed.close()
    sent, acked = [0] * nodes, [0] * nodes

    def node(index: int) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.05)
        for n in range(count):
            data = bytearray(cls(**sample_values(packet, seed=index + n)).encode())
            sent[index] += 1
            if fault == "drop" and n % 2:
                continue
            if fault == "corrupt" and n % 2 and packet["checksum"]:
                data[-3] ^= 0x40
            if fault == "old_version" and not packet.get("raw"):
                data[1] = (data[1] + 1) % 256
            if fault == "truncate":
                data = data[:-1]
            try:
                sock.sendto(bytes(data), wrong if fault == "wrong_port" else address)
                reply, _ = sock.recvfrom(16)
                if reply == b"ack":
                    acked[index] += 1
            except OSError:  # A timeout, or the reset Windows reports for a closed port.
                pass
            time.sleep(0.002)
        sock.close()

    threads = [threading.Thread(target=node, args=(i,)) for i in range(nodes)]
    for thread in threads:
        thread.start()
    received, by_status, senders = 0, {}, set()
    stop = time.time() + 12
    while time.time() < stop and (any(t.is_alive() for t in threads) or received == 0):
        try:
            data, sender = hub.recvfrom(70000)
        except socket.timeout:
            if not any(t.is_alive() for t in threads):
                break
            continue
        except OSError:
            continue
        received += 1
        senders.add(sender)
        try:
            cls.decode(data)
            by_status["ok"] = by_status.get("ok", 0) + 1
            hub.sendto(b"ack", sender)
        except module.PacketError as exc:
            by_status[exc.status] = by_status.get(exc.status, 0) + 1
    for thread in threads:
        thread.join(timeout=2)
    hub.close()

    total_sent, total_acked = sum(sent), sum(acked)
    unreadable = by_status.get("size", 0) + by_status.get("checksum", 0)
    refused = by_status.get("type", 0) + by_status.get("version", 0) + by_status.get("const", 0) + by_status.get("range", 0)
    results = [
        (total_sent > 0, f"{nodes} simulated nodes sent {total_sent} packets."),
        (received == total_sent, f"The hub received {received} of {total_sent}."),
        (unreadable == 0, f"{unreadable} of {received} failed the size or checksum test."),
        (refused == 0, f"{refused} of {received - unreadable} were refused." + (f" Reasons: {', '.join(f'{v} wrong {k}' for k, v in by_status.items() if k in ('type', 'version', 'const', 'range'))}." if refused else "")),
        (total_acked == total_sent, f"{total_acked} of {total_sent} were confirmed back to the sender."),
    ]
    checks, stopped = [], False
    for label, (passed, evidence) in zip(CHECK_NAMES, results):
        state_name = "todo" if stopped else "pass" if passed else "fail"
        checks.append({"name": label, "state": state_name, "evidence": evidence if not stopped else ""})
        stopped = stopped or not passed
    ok = not stopped
    report = {"packet": name, "version": packet["version"], "fault": fault, "ok": ok, "checks": checks, "nodes": nodes}
    ledger.append(actor or Actor.system("simulator"), "packet.simulated", **report)
    return report


# ---- Agent ----


class FieldOut(BaseModel):
    name: str
    type: str = Field(description="uint8, uint16, uint32, uint64, int8, int16, int32, int64, float32, float64, enum8, bytes[N] or var_bytes")
    unit: str | None
    note: str | None
    const: int | None
    min: float | None
    max: float | None = Field(description="Accepted upper limit. For var_bytes, the maximum length in bytes")
    sentinel: int | None = Field(description="A value meaning 'not available', which skips the limits")
    enum_names: list[str] = Field(description="For enum8 only: the value names in capitals")
    enum_values: list[int] = Field(description="For enum8 only: the numbers, in the same order as enum_names")


class PacketOut(BaseModel):
    name: str
    id: int
    transport_type: str = Field(description="udp, tcp, serial, espnow, ble, can, i2c, spi or other")
    port: int | None
    rate_hz: float | None
    endianness: str = Field(description="little or big")
    producer: str
    consumers: list[str]
    chunked: bool
    note: str
    fields: list[FieldOut]


def definition_from(output: PacketOut) -> dict[str, Any]:
    fields = []
    for f in output.fields:
        item: dict[str, Any] = {"name": f.name, "type": f.type}
        for key in ("unit", "note", "const", "sentinel"):
            if getattr(f, key) not in (None, ""):
                item[key] = getattr(f, key)
        whole = gen.is_int(f.type) or f.type == "var_bytes"
        for key in ("min", "max"):
            value = getattr(f, key)
            if value is not None and (f.type == "var_bytes") <= (key == "max"):
                item[key] = int(value) if whole else value
        if f.enum_names:
            item["enum"] = dict(zip(f.enum_names, f.enum_values))
        fields.append(item)
    transport: dict[str, Any] = {"type": output.transport_type}
    if output.port:
        transport["port"] = output.port
    return {"name": output.name, "id": output.id, "transport": transport, "rate_hz": output.rate_hz, "endianness": output.endianness, "producer": output.producer, "consumers": output.consumers, "chunked": output.chunked, "note": output.note, "fields": fields}


def summary_for_agents(state: ProjectState) -> str:
    rows = []
    for p in packets(state):
        if p.get("standard"):
            rows.append(f"- {p['name']} follows {p['standard']}.")
            continue
        names = ", ".join(f"{f['name']}:{f['type']}" for f in gen.payload(p))
        rows.append(f"- {p['name']} v{p['version']} id {p['id']}, {p['transport']}, {p['producer']} sends to {', '.join(p['consumers'])}. Fields: {names}")
    return "\n".join(rows) or "No packets are defined."


def known_devices(ledger: Ledger) -> list[str]:
    state = ledger.state()
    folder = ledger.root / "firmware"
    found = {p.name for p in folder.iterdir() if p.is_dir()} if folder.exists() else set()
    for entry in state.packets.values():
        found |= {entry["packet"]["producer"], *entry["packet"]["consumers"]}
    return sorted(found)


async def draft_packet(ledger: Ledger, gateway: Gateway, request: str) -> dict[str, Any]:
    state = ledger.state()
    nets = [n["fields"] for n in state.of_type("net") if n["status"] not in ("rejected", "superseded")]
    feedback = ""
    for _attempt in range(3):
        output: PacketOut = await gateway.run(agent="interface", values={"aim": state.meta.get("aim", ""), "devices": known_devices(ledger), "connections": nets, "existing_packets": summary_for_agents(ledger.state()), "request": request, "feedback": feedback}, output_type=PacketOut)
        try:
            return save_packet(ledger, definition_from(output), Actor.agent("electrical"))
        except InterfaceError as exc:
            feedback = str(exc)
    raise InterfaceError(f"The agent couldn't produce a valid packet. Last problem: {feedback}")


def physical(state: ProjectState) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {"Electrical": [], "Mechanical": []}
    for record in state.of_type("interface"):
        if record["status"] != "rejected":
            out[record["fields"]["kind"]].append(record)
    return out


def add_physical(ledger: Ledger, kind: str, name: str, agreed: str, between: str, actor: Actor) -> str:
    if kind not in ("Electrical", "Mechanical"):
        raise InterfaceError("Unknown interface type.")
    if not name.strip() or not agreed.strip() or not between.strip():
        raise InterfaceError("Fill in the name, what's agreed and who it's between.")
    return ledger.propose(actor, "interface", kind=kind, name=name.strip(), agreed=agreed.strip(), between=between.strip())


def view(ledger: Ledger) -> dict[str, Any]:
    state = ledger.state()
    return {"packets": packets(state), "physical": physical(state), "drift": drift(ledger.root, state), "frozen": is_frozen(state), "spent": state.spent_usd, "limit": state.limit_usd}
