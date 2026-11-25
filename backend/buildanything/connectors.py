"""Connector Manager: departments ask for capabilities, and you choose what supplies them.

Connections belong to this computer, because installed tools and keys differ per machine.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .ledger import Actor, Ledger, LedgerError

LEVELS = ["Observe", "Suggest", "Act with approval", "Act"]
CAPPED_RISKS = {"hardware", "deploy", "spending"}
RISK_ORDER = ["read", "write", "deploy", "hardware", "spending"]

CAPABILITIES = [
    {"id": "cad.run_script", "department": "Mechanical", "label": "Run CAD scripts", "risk": "write"},
    {"id": "cad.export_step", "department": "Mechanical", "label": "Export STEP files", "risk": "write"},
    {"id": "cad.mass_properties", "department": "Mechanical", "label": "Read mass properties", "risk": "read"},
    {"id": "cad.clearance", "department": "Mechanical", "label": "Check clearance", "risk": "read"},
    {"id": "cad.make_drawing", "department": "Mechanical", "label": "Make drawings", "risk": "write"},
    {"id": "fea.solve", "department": "Mechanical", "label": "Run structural simulation", "risk": "write"},
    {"id": "eda.schematic", "department": "Electrical", "label": "Edit schematics", "risk": "write"},
    {"id": "eda.erc", "department": "Electrical", "label": "Run electrical rule checks", "risk": "read"},
    {"id": "eda.drc", "department": "Electrical", "label": "Run PCB design rule checks", "risk": "read"},
    {"id": "spice.run", "department": "Electrical", "label": "Simulate circuits", "risk": "read"},
    {"id": "fw.build", "department": "Electrical", "label": "Build firmware", "risk": "write"},
    {"id": "fw.flash", "department": "Electrical", "label": "Flash firmware", "risk": "hardware"},
    {"id": "serial.monitor", "department": "Electrical", "label": "Read serial output", "risk": "read"},
    {"id": "vcs.commit", "department": "Software", "label": "Commit to version control", "risk": "write"},
    {"id": "pkg.install", "department": "Software", "label": "Install packages", "risk": "write"},
    {"id": "sim.run", "department": "Software", "label": "Run simulations", "risk": "write"},
    {"id": "cloud.deploy", "department": "Software", "label": "Deploy to the cloud", "risk": "deploy"},
    {"id": "lit.search", "department": "Research", "label": "Search papers", "risk": "read"},
]
BY_ID = {c["id"]: c for c in CAPABILITIES}

# Fixed commands only. Nothing from a request is ever placed in these.
CLI_TOOLS = {
    "git": {"name": "Git", "exe": ["git"], "version": ["--version"], "capabilities": ["vcs.commit"]},
    "idf": {"name": "ESP-IDF", "exe": ["idf.py"], "version": ["--version"], "capabilities": ["fw.build", "fw.flash", "serial.monitor"]},
    "pio": {"name": "PlatformIO", "exe": ["pio", "platformio"], "version": ["--version"], "capabilities": ["fw.build", "fw.flash", "serial.monitor"]},
    "arduino-cli": {"name": "Arduino CLI", "exe": ["arduino-cli"], "version": ["version"], "capabilities": ["fw.build", "fw.flash"]},
    "kicad-cli": {"name": "KiCad CLI", "exe": ["kicad-cli"], "version": ["version"], "capabilities": ["eda.erc", "eda.drc"]},
    "ngspice": {"name": "ngspice", "exe": ["ngspice"], "version": ["--version"], "capabilities": ["spice.run"]},
    "freecadcmd": {"name": "FreeCAD", "exe": ["freecadcmd", "FreeCADCmd"], "version": ["--version"], "capabilities": ["cad.run_script", "cad.export_step", "cad.mass_properties", "fea.solve"]},
    "npm": {"name": "npm", "exe": ["npm"], "version": ["--version"], "capabilities": ["pkg.install"]},
    "matlab": {"name": "MATLAB", "exe": ["matlab"], "version": None, "capabilities": ["sim.run"]},
}

KEYWORDS = [
    ("cad.clearance", r"clearance|interference|collision"),
    ("cad.mass_properties", r"mass|center of gravity|volume"),
    ("cad.export_step", r"export.*step|step.*export"),
    ("cad.make_drawing", r"drawing|techdraw"),
    ("fea.solve", r"\bfem\b|\bfea\b|finite element|stress"),
    ("eda.erc", r"\berc\b|electrical rule"),
    ("eda.drc", r"\bdrc\b|design rule"),
    ("eda.schematic", r"schematic"),
    ("spice.run", r"spice"),
    ("fw.flash", r"\bflash\b|upload.*firmware"),
    ("fw.build", r"build.*firmware|compile"),
    ("serial.monitor", r"serial|uart monitor"),
    ("vcs.commit", r"\bgit\b|commit"),
    ("pkg.install", r"install.*package"),
    ("cloud.deploy", r"deploy"),
    ("lit.search", r"paper|literature|arxiv"),
    ("cad.run_script", r"script|execute.*python|run.*python"),
]

RUNGS = {"mcp": 1, "vendor": 2, "cli": 3, "file": 4}
RUNG_LABEL = {"mcp": "MCP server", "vendor": "Vendor scripting API", "cli": "Command line", "file": "File exchange"}
SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.I)


class ConnectorError(LedgerError):
    pass


def max_level(capability_id: str) -> str:
    return "Act with approval" if BY_ID[capability_id]["risk"] in CAPPED_RISKS else "Act"


def propose_mapping(name: str, description: str) -> str | None:
    text = f"{name} {description}".lower().replace("_", " ")
    for capability, pattern in KEYWORDS:
        if re.search(pattern, text):
            return capability
    return None


def classify_risk(name: str, description: str) -> str:
    text = f"{name} {description}".lower().replace("_", " ")
    if re.search(r"\b(buy|order|purchase|payment|checkout)\b", text):
        return "spending"
    if re.search(r"\b(flash|actuat\w*|motor|arm|disarm|takeoff|power on)\b", text):
        return "hardware"
    if re.search(r"\bdeploy\w*\b", text):
        return "deploy"
    if re.search(r"\b(create|write|edit|delete|update|set|modify|run|execute|export|save|install|remove|build)\b", text):
        return "write"
    return "read"


def fingerprint(tools: list[dict[str, Any]]) -> str:
    """A changed description can smuggle in instructions, so it counts as a change."""
    body = sorted((t["name"], t.get("description") or "", json.dumps(t.get("schema") or {}, sort_keys=True)) for t in tools)
    return hashlib.sha256(json.dumps(body).encode("utf-8")).hexdigest()


def clean_environment() -> dict[str, str]:
    """A connected program never inherits this app's API keys."""
    return {k: v for k, v in os.environ.items() if not SECRET_NAME.search(k)}


def parse_target(target: str) -> dict[str, Any]:
    target = target.strip()
    if not target:
        raise ConnectorError("Enter a command or URL.")
    if re.match(r"https?://", target, re.I):
        return {"url": target}
    parts = [p.strip('"').strip("'") for p in shlex.split(target, posix=False)]
    return {"command": parts[0], "args": parts[1:]}


async def list_mcp_tools(spec: dict[str, Any], timeout: float = 30) -> list[dict[str, Any]]:
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    source: Any = spec["url"] if "url" in spec else StdioTransport(command=spec["command"], args=spec["args"], env=clean_environment())

    async def fetch() -> list[dict[str, Any]]:
        async with Client(source) as client:
            tools = await client.list_tools()
        return [{"name": t.name, "description": t.description or "", "schema": getattr(t, "input_schema", None) or {}} for t in tools]

    try:
        return await asyncio.wait_for(fetch(), timeout)
    except Exception as exc:
        raise ConnectorError(f"Couldn't read tools from that server ({type(exc).__name__}).") from exc


def _usual_install(tool: str) -> str | None:
    """PlatformIO's own installer doesn't add itself to PATH."""
    if tool != "pio":
        return None
    home = Path.home() / ".platformio" / "penv"
    return next((str(c) for c in (home / "Scripts" / "pio.exe", home / "bin" / "pio") if c.is_file()), None)


def detect_cli(tool: str) -> dict[str, Any]:
    info = CLI_TOOLS[tool]
    path = next((p for p in (shutil.which(e) for e in info["exe"]) if p), None) or _usual_install(tool)
    if not path:
        return {"tool": tool, "found": False, "detail": f"{info['name']} wasn't found on PATH."}
    if info["version"] is None:
        return {"tool": tool, "found": True, "path": path, "version": "Found", "detail": "Found on PATH."}
    try:
        done = subprocess.run([path, *info["version"]], capture_output=True, text=True, timeout=20, env=clean_environment())
    except (OSError, subprocess.SubprocessError) as exc:
        return {"tool": tool, "found": False, "detail": f"{info['name']} was found but didn't run ({type(exc).__name__})."}
    if done.returncode != 0:
        return {"tool": tool, "found": False, "detail": f"{info['name']} was found but returned an error."}
    version = (done.stdout or done.stderr).strip().splitlines()
    return {"tool": tool, "found": True, "path": path, "version": version[0][:120] if version else "Found", "detail": "Found and ran."}


def describe_changes(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> dict[str, list[str]]:
    before, after = {t["name"]: t for t in old}, {t["name"]: t for t in new}
    changed = []
    for name in sorted(before.keys() & after.keys()):
        a, b = before[name], after[name]
        if (a.get("description") or "") != (b.get("description") or ""):
            changed.append(f"{name}: description was \"{a.get('description') or ''}\", now \"{b.get('description') or ''}\"")
        elif json.dumps(a.get("schema") or {}, sort_keys=True) != json.dumps(b.get("schema") or {}, sort_keys=True):
            changed.append(f"{name}: its inputs changed")
    return {"added": sorted(after.keys() - before.keys()), "removed": sorted(before.keys() - after.keys()), "changed": changed}


class Registry:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger

    def _state(self) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
        connectors: dict[str, dict[str, Any]] = {}
        autonomy: dict[str, str] = {}
        for event in self.ledger.events():
            kind, data = event["kind"], event["data"]
            if kind == "connector.saved":
                connectors[data["id"]] = {**data, "health": data.get("health", "untested"), "detail": data.get("detail", "")}
            elif kind == "connector.removed":
                connectors.pop(data["id"], None)
            elif kind == "connector.checked" and data["id"] in connectors:
                connectors[data["id"]].update(health=data["health"], detail=data["detail"], current=data.get("current"), changes=data.get("changes"))
            elif kind == "connector.approved" and data["id"] in connectors:
                connectors[data["id"]].update(fingerprint=data["fingerprint"], tools=data["tools"], health="passed", detail="Re-approved.", changes=None, current=None)
            elif kind == "autonomy.changed":
                autonomy[data["capability"]] = data["level"]
        return connectors, autonomy

    def connectors(self) -> list[dict[str, Any]]:
        return list(self._state()[0].values())

    def get(self, connector_id: str) -> dict[str, Any]:
        found = self._state()[0].get(connector_id)
        if not found:
            raise ConnectorError("Connector not found.")
        return found

    def overview(self) -> list[dict[str, Any]]:
        connectors, autonomy = self._state()
        out = []
        for capability in CAPABILITIES:
            suppliers = [c for c in connectors.values() if capability["id"] in c["capabilities"] and c["health"] == "passed"]
            suppliers.sort(key=lambda c: RUNGS[c["rung"]])
            best = suppliers[0] if suppliers else None
            out.append({
                **capability,
                "connector": best["name"] if best else None,
                "route": RUNG_LABEL[best["rung"]] if best else "Guided manual steps",
                "health": "Passed" if best else "Missing",
                "autonomy": autonomy.get(capability["id"], "Suggest"),
                "maxAutonomy": max_level(capability["id"]),
                "consequence": None if best else f"No tool connected: the agent will guide you, and you confirm the result of \"{capability['label']}\".",
            })
        return out

    def tools_for(self, capability_id: str) -> list[dict[str, Any]]:
        """The only tools an agent sees: mapped, healthy and approved."""
        out = []
        for connector in self.connectors():
            if connector["health"] != "passed":
                continue
            out += [t for t in connector.get("tools", []) if t.get("capability") == capability_id]
        return out

    async def session_tools(self, capability_id: str) -> list[dict[str, Any]]:
        """Re-check each MCP server just before an agent session, so a change can't slip in between checks."""
        for connector in self.connectors():
            if connector["rung"] == "mcp" and connector["health"] == "passed" and capability_id in connector["capabilities"]:
                await self.check(connector["id"])
        return self.tools_for(capability_id)

    def set_autonomy(self, capability_id: str, level: str, actor: Actor) -> None:
        if capability_id not in BY_ID:
            raise ConnectorError("Unknown capability.")
        if level not in LEVELS:
            raise ConnectorError("Unknown autonomy level.")
        if LEVELS.index(level) > LEVELS.index(max_level(capability_id)):
            raise ConnectorError(f"{BY_ID[capability_id]['label']} can never go above {max_level(capability_id)}.")
        self.ledger.append(actor, "autonomy.changed", capability=capability_id, level=level)

    def save_cli(self, result: dict[str, Any], actor: Actor) -> dict[str, Any]:
        info = CLI_TOOLS[result["tool"]]
        existing = next((c for c in self.connectors() if c.get("cli") == result["tool"]), None)
        connector_id = existing["id"] if existing else f"con_{uuid.uuid4().hex[:10]}"
        self.ledger.append(actor, "connector.saved", id=connector_id, name=info["name"], rung="cli", cli=result["tool"],
                           capabilities=info["capabilities"], version=result.get("version"), health="passed", detail=result["detail"])
        return self.get(connector_id)

    def save_mcp(self, name: str, target: str, tools: list[dict[str, Any]], mapping: dict[str, dict[str, str]], actor: Actor) -> dict[str, Any]:
        if not name.strip():
            raise ConnectorError("Give this connector a name.")
        mapped = []
        for tool in tools:
            choice = mapping.get(tool["name"], {})
            capability = choice.get("capability") or None
            if capability and capability not in BY_ID:
                raise ConnectorError(f"Unknown capability {capability}.")
            risk = choice.get("risk") or classify_risk(tool["name"], tool["description"])
            if risk not in RISK_ORDER:
                raise ConnectorError(f"Unknown risk class {risk}.")
            if capability and RISK_ORDER.index(risk) < RISK_ORDER.index(BY_ID[capability]["risk"]):
                risk = BY_ID[capability]["risk"]  # A person can't mark a flash tool as read-only.
            mapped.append({**tool, "capability": capability, "risk": risk})
        connector_id = f"con_{uuid.uuid4().hex[:10]}"
        self.ledger.append(actor, "connector.saved", id=connector_id, name=name.strip(), rung="mcp", spec=parse_target(target),
                           capabilities=sorted({t["capability"] for t in mapped if t["capability"]}), tools=mapped,
                           fingerprint=fingerprint(tools), health="passed", detail="Tools listed and approved.")
        return self.get(connector_id)

    async def check(self, connector_id: str) -> dict[str, Any]:
        connector = self.get(connector_id)
        system = Actor.system("connector-check")
        if connector["rung"] == "cli":
            result = await asyncio.to_thread(detect_cli, connector["cli"])
            self.ledger.append(system, "connector.checked", id=connector_id, health="passed" if result["found"] else "failed", detail=result["detail"])
            return self.get(connector_id)
        try:
            tools = await list_mcp_tools(connector["spec"])
        except ConnectorError as exc:
            self.ledger.append(system, "connector.checked", id=connector_id, health="failed", detail=str(exc))
            return self.get(connector_id)
        if fingerprint(tools) != connector["fingerprint"]:
            self.ledger.append(system, "connector.checked", id=connector_id, health="changed", current=tools,
                               changes=describe_changes(connector.get("tools", []), tools), detail="This server's tools or descriptions changed. Review before agents can use it.")
        else:
            self.ledger.append(system, "connector.checked", id=connector_id, health="passed", detail="Connected and answering. Its tools are the same as when you approved them.")
        return self.get(connector_id)

    def reapprove(self, connector_id: str, actor: Actor) -> dict[str, Any]:
        connector = self.get(connector_id)
        current = connector.get("current")
        if connector["health"] != "changed" or not current:
            raise ConnectorError("Nothing to re-approve.")
        previous = {t["name"]: t for t in connector.get("tools", [])}
        merged = [{**t, "capability": previous.get(t["name"], {}).get("capability"), "risk": classify_risk(t["name"], t["description"])} for t in current]
        self.ledger.append(actor, "connector.approved", id=connector_id, fingerprint=fingerprint(current), tools=merged)
        return self.get(connector_id)

    def remove(self, connector_id: str, actor: Actor) -> None:
        self.get(connector_id)
        self.ledger.append(actor, "connector.removed", id=connector_id)
