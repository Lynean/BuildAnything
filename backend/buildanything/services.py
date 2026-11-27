"""Reception: the tools the app and its agents work with, and how to get the ones that are missing.

External Service connects the app to the outside world:
  Cloud: API keys, online services and MCP servers.     App: desktop programs driven by scripts, such as Fusion.
Internal Service covers what runs on this computer:
  Platform: what the app itself carries (MuJoCo, the CAD kernel, file viewers).   Project: what one project needs.

Every tool is checked by actually running it. Nothing is installed without a person's approval: a person pressing
Install approves it there and then; an agent can only propose, and a person approves. API keys go straight from the
person into the computer's keyring and are never shown to an agent. Commands are fixed: only a package name or a
winget ID checked against strict patterns (and, for winget, confirmed with `winget show`) ever goes into one.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import connectors as conn
from .connectors import clean_environment, detect_cli, CLI_TOOLS
from .ledger import Actor, Ledger, LedgerError

GROUPS = {
    "cloud": ("External Service", "Cloud", "API keys, online services and MCP servers"),
    "app": ("External Service", "App", "Desktop programs the agents drive with scripts"),
    "platform": ("Internal Service", "Platform", "What the BuildAnything app itself carries"),
    "project": ("Internal Service", "Project", "What this project needs on this computer"),
}
PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*(\[[A-Za-z0-9_,\-]+\])?(==[A-Za-z0-9_.+\-!]+)?$")
WINGET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{1,80}$")
SYSTEM = Actor.system("services")

# Each entry: what it is, what it lets agents do, how it's checked, and how to get it.
CATALOG: list[dict[str, Any]] = []  # no tools are connected to the app; Reception keeps Skills and project packages
BY_ID = {s["id"]: s for s in CATALOG}
TOOL_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
EXE = re.compile(r"^[A-Za-z0-9._-]{1,60}$")
GLOB_ROOTS = ("%LOCALAPPDATA%", "%PROGRAMFILES%", "%PROGRAMFILES(X86)%", "%APPDATA%", "%USERPROFILE%")
_custom: dict[str, dict[str, Any]] = {}  # tools a person added through an agent's proposal, loaded from the app's ledger


def entry_for(tool_id: str) -> dict[str, Any] | None:
    return BY_ID.get(tool_id) or _custom.get(tool_id)


def all_tools() -> list[dict[str, Any]]:
    return CATALOG + list(_custom.values())


def tool_proposals(app_ledger: Ledger) -> list[dict[str, Any]]:
    """Tools an agent proposed adding, and whether a person decided. Approved ones join the catalog."""
    out: dict[str, dict[str, Any]] = {}
    for e in app_ledger.events():
        d = e["data"]
        if e["kind"] == "service.tool_proposed":
            out[d["entry"]["id"]] = {"entry": d["entry"], "why": d.get("why", ""), "by": e["actor"]["name"], "at": e["at"], "status": "waiting"}
        elif e["kind"] == "service.tool_decided" and d["id"] in out:
            out[d["id"]]["status"] = "added" if d["approved"] else "rejected"
            out[d["id"]]["reason"] = d.get("reason", "")
    return list(out.values())


def load_custom(app_ledger: Ledger) -> None:
    """Programs and toolchains a person approved join the list Internal Service checks and installs from."""
    fresh = {p["entry"]["id"]: {**p["entry"], "custom": True} for p in tool_proposals(app_ledger) if p["status"] == "added"}
    _custom.clear()
    _custom.update(fresh)


def validate_tool(raw: dict[str, Any]) -> dict[str, Any]:
    """A tool an agent wants to add: what it is, how to check it's there, and how to get it. Nothing in it can become a free-form command."""
    tool_id = str(raw.get("id", "")).strip().lower()
    if not TOOL_ID.match(tool_id) or tool_id in BY_ID or tool_id == "project-python":
        raise ServiceError("Give the tool a short new id of lowercase letters, digits and dashes, such as roblox-studio.")
    group = str(raw.get("group", ""))
    if group not in ("cloud", "app", "platform"):
        raise ServiceError("group is cloud, app or platform.")
    name, what = str(raw.get("name", "")).strip()[:60], str(raw.get("what", "")).strip()[:240]
    if not name or len(what) < 10:
        raise ServiceError("Give a name and say what the tool is for.")
    caps = [c for c in (str(x).strip().lower()[:40] for x in raw.get("capabilities") or []) if re.match(r"^[a-z0-9._-]+$", c)][:8]
    check_kind, check_value = str(raw.get("check_kind", "")), str(raw.get("check_value", "")).strip()
    if check_kind == "exe" and EXE.match(check_value):
        check: dict[str, Any] = {"cli": check_value, "exe": [check_value], "version": ["--version"]}
    elif check_kind == "file" and check_value.upper().startswith(GLOB_ROOTS) and ".." not in check_value:
        check = {"glob": check_value}
    elif check_kind == "key":
        check = {"secret": f"BUILDANYTHING_TOOL_{tool_id.upper().replace('-', '_')}"}
    elif check_kind == "python" and re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,60}$", check_value):
        check = {"python": check_value}
    else:
        raise ServiceError("check_kind is exe (a program name on PATH), file (a path starting with %LOCALAPPDATA%, %PROGRAMFILES% or similar, * allowed), key, or python (a module name).")
    get_kind, get_value = str(raw.get("get_kind", "")), raw.get("get_value", "")
    if get_kind == "winget":
        if not winget_exists(str(get_value)):
            raise ServiceError(f"winget couldn't find {get_value}. Check the id with winget search first, or give steps instead.")
        get: dict[str, Any] = {"winget": str(get_value)}
    elif get_kind == "pip" and PACKAGE.match(str(get_value)):
        get = {"pip": str(get_value)}
    elif get_kind == "key" and check_kind == "key" and re.match(r"^https://[^\s]{4,200}$", str(get_value)):
        get = {"key": check["secret"], "where": str(get_value)}
    elif get_kind == "command" and isinstance(get_value, list) and 0 < len(get_value) <= 30 and all(isinstance(x, str) and 0 < len(x) < 300 for x in get_value):
        first = get_value[0].strip()
        if first.lower() in INSTALL_SHELLS or "/" in first or chr(92) in first:
            raise ServiceError("A command install runs an installer program by name, such as sdkmanager or rustup, not a shell or a path.")
        if not conn.which(first):
            raise ServiceError(f"{first} isn't on this computer yet, so it can't run this install. Propose the tool that provides it first.")
        get = {"command": [x.strip() for x in get_value]}
    elif get_kind == "steps" and isinstance(get_value, list) and 0 < len(get_value) <= 8:
        get = {"steps": [str(x).strip()[:300] for x in get_value if str(x).strip()]}
    else:
        raise ServiceError("get_kind is winget (a winget id), command (an installer that's already on this computer and its arguments, such as sdkmanager with the packages to add), pip (a package), key (an https page where the key is made; needs check_kind key), or steps (instructions for the person).")
    return {"id": tool_id, "group": group, "name": name, "what": what, "capabilities": caps, "check": check, "get": get}


def propose_tool(app_ledger: Ledger, raw: dict[str, Any], why: str, actor: Actor) -> dict[str, Any]:
    entry = validate_tool(raw)
    if any(p["entry"]["id"] == entry["id"] and p["status"] == "waiting" for p in tool_proposals(app_ledger)):
        raise ServiceError("That tool is already waiting for the person's approval.")
    app_ledger.append(actor, "service.tool_proposed", entry=entry, why=why.strip()[:600])
    return entry


def decide_tool(app_ledger: Ledger, tool_id: str, approve: bool, reason: str, actor: Actor) -> None:
    if not any(p["entry"]["id"] == tool_id and p["status"] == "waiting" for p in tool_proposals(app_ledger)):
        raise ServiceError("That tool isn't waiting for approval.")
    app_ledger.append(actor, "service.tool_decided", id=tool_id, approved=bool(approve), reason=reason.strip())
    load_custom(app_ledger)
INSTALL_SHELLS = {"cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe", "bash", "bash.exe", "sh", "wsl", "wsl.exe"}
IMPORT_TO_PACKAGE = {"cv2": "opencv-python", "PIL": "Pillow", "sklearn": "scikit-learn", "yaml": "PyYAML", "serial": "pyserial", "skimage": "scikit-image", "Pynite": "PyNiteFEA",
                     "OCP": "cadquery-ocp", "dotenv": "python-dotenv", "bs4": "beautifulsoup4", "usb": "pyusb"}

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()


class ServiceError(LedgerError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---- Checks: each one runs the tool, it doesn't just look for a file ----

def _python_has(python: str, modules: list[str]) -> dict[str, bool]:
    code = "import importlib.util, json, sys; print(json.dumps({m: importlib.util.find_spec(m) is not None for m in sys.argv[1:]}))"
    try:
        done = subprocess.run([python, "-I", "-c", code, *modules], capture_output=True, text=True, timeout=40, env=clean_environment())
        return json.loads(done.stdout.strip().splitlines()[-1]) if done.returncode == 0 else {m: False for m in modules}
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return {m: False for m in modules}


def _python_version(python: str, module: str) -> str:
    code = "import sys\nfrom importlib import metadata\ntry:\n print(metadata.version(sys.argv[1]))\nexcept Exception:\n print('installed')"
    try:
        done = subprocess.run([python, "-I", "-c", code, module], capture_output=True, text=True, timeout=30, env=clean_environment())
        return done.stdout.strip() or "installed"
    except (OSError, subprocess.SubprocessError):
        return "installed"


def _cli(check: dict[str, Any]) -> dict[str, Any]:
    tool = check["cli"]
    if tool in CLI_TOOLS:
        found = detect_cli(tool)
        return {"ready": found["found"], "version": found.get("version", ""), "detail": found["detail"]}
    path = next((p for p in (conn.which(e) for e in check.get("exe", [tool])) if p), None)
    if not path:
        return {"ready": False, "detail": f"{tool} wasn't found on PATH."}
    try:
        done = subprocess.run([path, *check.get("version", ["--version"])], capture_output=True, text=True, timeout=20, env=clean_environment())
        line = (done.stdout or done.stderr).strip().splitlines()
        return {"ready": done.returncode == 0, "version": line[0][:120] if line else "", "detail": "Found and ran." if done.returncode == 0 else "Found but returned an error."}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ready": False, "detail": f"Found but didn't run ({type(exc).__name__})."}


def check_one(entry: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    c = entry["check"]
    if "provider" in c:
        return ctx["provider"](c["provider"])
    if "secret" in c:
        has = bool(ctx["secrets"].get(c["secret"]) or os.environ.get(c["secret"]))
        return {"ready": has, "detail": "Key saved." if has else "No key saved yet."}
    if "connector" in c:
        return ctx["connector"](c["connector"])
    if "cli" in c:
        return _cli(c)
    if "glob" in c:
        import glob as globbing

        pattern = os.path.expandvars(c["glob"].replace("%PROGRAMFILES(X86)%", os.environ.get("ProgramFiles(x86)", "%PROGRAMFILES(X86)%")))
        found = sorted(globbing.glob(pattern))
        return {"ready": bool(found), "detail": f"Found at {found[-1]}." if found else "Not found where it usually installs."}
    if "paths" in c:
        found = next((p for p in c["paths"] if Path(p).is_file()), None)
        return {"ready": bool(found), "detail": f"Found at {found}." if found else "Not installed in its usual place."}
    if "python" in c:
        has = _python_has(sys.executable, [c["python"]]).get(c["python"], False)
        return {"ready": has, "version": _python_version(sys.executable, entry["get"].get("pip", c["python"])) if has else "", "detail": "Importable in the app's Python." if has else "Not installed in the app's Python."}
    return {"ready": False, "detail": "No check for this yet."}


def _how(entry: dict[str, Any]) -> dict[str, Any]:
    g = entry["get"]
    if "winget" in g:
        return {"kind": "winget", "label": f"Install with winget ({g['winget']})", "id": g["winget"]}
    if "command" in g:
        return {"kind": "command", "label": "Install by running " + " ".join(g["command"]), "command": g["command"]}
    if "pip" in g:
        return {"kind": "pip", "label": f"Install {g['pip']} into the app's Python", "package": g["pip"]}
    if "key" in g:
        return {"kind": "key", "label": "Paste an API key", "where": g["where"]}
    return {"kind": "steps", "label": "Follow these steps", "steps": g.get("steps", [])}


def catalog_view(ctx: dict[str, Any], refresh: bool = False) -> list[dict[str, Any]]:
    """Every catalogued tool with its live status. Checks run in parallel and are kept for a minute."""
    from concurrent.futures import ThreadPoolExecutor

    def one(entry: dict[str, Any]) -> dict[str, Any]:
        with _lock:
            hit = _cache.get(entry["id"])
        if hit and not refresh and time.time() - hit[0] < 60:
            status = hit[1]
        else:
            try:
                status = check_one(entry, ctx)
            except Exception as exc:  # noqa: BLE001 - one broken check shouldn't hide the rest
                status = {"ready": False, "detail": f"The check failed: {type(exc).__name__}."}
            with _lock:
                _cache[entry["id"]] = (time.time(), status)
        return {k: entry[k] for k in ("id", "group", "name", "what", "capabilities")} | {"status": status, "how": _how(entry), "custom": bool(entry.get("custom"))}

    with ThreadPoolExecutor(8) as pool:
        return list(pool.map(one, all_tools()))


# ---- Internal · Project: what one project needs ----

def project_python(project_root: Path) -> Path | None:
    for candidate in (project_root / ".venv" / "Scripts" / "python.exe", project_root / ".venv" / "bin" / "python"):
        if candidate.is_file():
            return candidate
    return None


def _imports(project_root: Path) -> set[str]:
    found: set[str] = set()
    local = {p.stem for p in (project_root / "firmware").rglob("*.py")}
    for path in (project_root / "firmware").rglob("*.py"):
        if any(part in (".pio", ".venv", "__pycache__") for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                found.add(node.module.split(".")[0])
    return {m for m in found if m not in local and m not in sys.stdlib_module_names and m != "__future__"}


def project_view(project_root: Path) -> dict[str, Any]:
    """The project's own environment: its Python packages (from what its code imports) and its firmware toolchains."""
    python = project_python(project_root)
    modules = sorted(_imports(project_root))
    has = _python_has(str(python), modules) if python and modules else {}
    packages = [{"module": m, "package": IMPORT_TO_PACKAGE.get(m, m), "ready": bool(has.get(m))} for m in modules]
    firmware = []
    for ini in sorted((project_root / "firmware").glob("*/platformio.ini")):
        text = ini.read_text(encoding="utf-8", errors="replace")
        libs = re.findall(r"^\s*lib_deps\s*=\s*((?:.*\n?)(?:\s+\S.*\n?)*)", text, re.M)
        firmware.append({"device": ini.parent.name, "board": (re.search(r"^\s*board\s*=\s*(\S+)", text, re.M) or [None, ""])[1],
                         "platform": (re.search(r"^\s*platform\s*=\s*(\S+)", text, re.M) or [None, ""])[1],
                         "libraries": [l.strip() for l in (libs[0].split("\n") if libs else []) if l.strip()][:20]})
    return {"python": {"environment": str(python.parent.parent.relative_to(project_root)) if python else None, "packages": packages,
                       "missing": [p["package"] for p in packages if not p["ready"]]},
            "firmware": firmware, "node": (project_root / "package.json").is_file()}


# ---- Installing: proposed by an agent or pressed by a person; always run only after a person approves ----

def winget_search(query: str) -> list[str]:
    """Read-only: what winget lists for a name, so an agent proposes a real id rather than a guessed one."""
    exe = shutil.which("winget")
    if not exe or not re.match(r"^[A-Za-z0-9 ._-]{2,40}$", query):
        return []
    try:
        done = subprocess.run([exe, "search", "--name", query, "--source", "winget", "--accept-source-agreements"], capture_output=True, text=True, timeout=90, env=clean_environment())
        return [line.rstrip() for line in (done.stdout or "").splitlines() if line.strip() and not set(line.strip()) <= set("-\\|/ ")][:25]
    except (OSError, subprocess.SubprocessError):
        return []


def winget_exists(winget_id: str) -> bool:
    exe = shutil.which("winget")
    if not exe or not WINGET_ID.match(winget_id):
        return False
    try:
        done = subprocess.run([exe, "show", "--id", winget_id, "-e", "--source", "winget", "--accept-source-agreements"], capture_output=True, text=True, timeout=90, env=clean_environment())
        return done.returncode == 0 and winget_id.lower() in (done.stdout or "").lower()
    except (OSError, subprocess.SubprocessError):
        return False


def plan(service: str, project_root: Path | None, packages: list[str] | None) -> dict[str, Any]:
    """The exact command an install would run, checked before anyone approves it."""
    if service == "project-python":
        if project_root is None:
            raise ServiceError("Choose a project.")
        packages = [p.strip() for p in packages or [] if p.strip()]
        if not packages or len(packages) > 40 or not all(PACKAGE.match(p) for p in packages):
            raise ServiceError("Give the packages as plain names, optionally with ==version, such as opencv-python==4.10.0.84.")
        python = project_python(project_root)
        uv = shutil.which("uv")
        steps = []
        if python is None:
            steps.append([uv, "venv", str(project_root / ".venv")] if uv else [sys.executable, "-m", "venv", str(project_root / ".venv")])
        target = project_root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        steps.append([uv, "pip", "install", "--python", str(target), *packages] if uv else [str(target), "-m", "pip", "install", *packages])
        return {"service": service, "title": f"Install {', '.join(packages)} into this project's Python environment", "steps": steps, "project": project_root.name, "packages": packages}
    entry = entry_for(service)
    if not entry:
        raise ServiceError("Unknown tool.")
    get = entry["get"]
    if "pip" in get:
        if not PACKAGE.match(get["pip"]):
            raise ServiceError("That package name isn't allowed.")
        return {"service": service, "title": f"Install {get['pip']} into the app's Python", "steps": [[sys.executable, "-m", "pip", "install", get["pip"]]]}
    if "winget" in get:
        exe = shutil.which("winget")
        if not exe:
            raise ServiceError("winget isn't available on this computer. Install 'App Installer' from the Microsoft Store, or follow the tool's own instructions.")
        if not winget_exists(get["winget"]):
            raise ServiceError(f"winget couldn't find {get['winget']}, so nothing was installed. Install {entry['name']} from its own website.")
        return {"service": service, "title": f"Install {entry['name']} with winget", "steps": [[exe, "install", "--id", get["winget"], "-e", "--source", "winget", "--accept-source-agreements", "--accept-package-agreements", "--silent"]]}
    if "command" in get:
        exe = conn.which(get["command"][0])
        if not exe:
            raise ServiceError(f"{get['command'][0]} isn't on this computer, so {entry['name']} can't be installed this way yet. Install what provides it first.")
        return {"service": service, "title": f"Install {entry['name']} with {get['command'][0]}", "steps": [[exe, *get["command"][1:]]]}
    raise ServiceError(f"{entry['name']} can't be installed automatically. Follow its steps instead.")


class Installs:
    """Install requests and the jobs that run them. Kept in the app's own ledger, so every install is on record."""

    def __init__(self, app_ledger: Ledger):
        self.ledger = app_ledger
        self.logs = app_ledger.root / "services" / "logs"
        self.running: dict[str, threading.Thread] = {}

    def requests(self) -> list[dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for e in self.ledger.events():
            d = e["data"]
            if e["kind"] == "service.install_proposed":
                out[d["id"]] = {**d, "status": "waiting", "by": e["actor"]["name"], "at": e["at"]}
            elif e["kind"] in ("service.install_approved", "service.install_rejected") and d["id"] in out:
                out[d["id"]]["status"] = "approved" if e["kind"].endswith("approved") else "rejected"
                out[d["id"]]["reason"] = d.get("reason", "")
            elif e["kind"] == "service.install_finished" and d["id"] in out:
                out[d["id"]].update(status="done" if d["ok"] else "failed", result=d.get("summary", ""))
        for r in out.values():
            if r["status"] == "approved" and r["id"] in self.running:
                r["status"] = "installing"
            log = self.logs / f"{r['id']}.log"
            r["log"] = log.read_text(encoding="utf-8", errors="replace")[-3000:] if log.is_file() else ""
        return sorted(out.values(), key=lambda r: r["at"], reverse=True)

    def propose(self, service: str, project_root: Path | None, packages: list[str] | None, why: str, actor: Actor) -> dict[str, Any]:
        step = plan(service, project_root, packages)
        request_id = f"ins_{uuid.uuid4().hex[:10]}"
        self.ledger.append(actor, "service.install_proposed", id=request_id, service=service, title=step["title"], why=why.strip()[:600],
                           project=step.get("project"), packages=step.get("packages"), commands=[" ".join(str(x) for x in s) for s in step["steps"]])
        return {"id": request_id, **step}

    def approve(self, request_id: str, actor: Actor, project_roots: dict[str, Path]) -> None:
        r = next((x for x in self.requests() if x["id"] == request_id), None)
        if not r or r["status"] != "waiting":
            raise ServiceError("That install isn't waiting for approval.")
        step = plan(r["service"], project_roots.get(r.get("project") or ""), r.get("packages"))  # re-checked now, not trusted from the record
        self.ledger.append(actor, "service.install_approved", id=request_id)
        thread = threading.Thread(target=self._run, args=(request_id, step["steps"], r["service"]), daemon=True)
        self.running[request_id] = thread
        thread.start()

    def reject(self, request_id: str, reason: str, actor: Actor) -> None:
        if not any(x["id"] == request_id and x["status"] == "waiting" for x in self.requests()):
            raise ServiceError("That install isn't waiting for approval.")
        self.ledger.append(actor, "service.install_rejected", id=request_id, reason=reason.strip())

    def _run(self, request_id: str, steps: list[list[str]], service: str) -> None:
        self.logs.mkdir(parents=True, exist_ok=True)
        log = self.logs / f"{request_id}.log"
        ok, summary = True, "Installed."
        with log.open("w", encoding="utf-8") as out:
            for step in steps:
                out.write("$ " + " ".join(str(x) for x in step) + "\n")
                out.flush()
                try:
                    done = subprocess.run([str(x) for x in step], stdout=out, stderr=subprocess.STDOUT, timeout=1800, env=clean_environment())
                    if done.returncode != 0:
                        ok, summary = False, f"The step stopped with exit code {done.returncode}. See the log."
                        break
                except (OSError, subprocess.SubprocessError) as exc:
                    ok, summary = False, f"The step couldn't run: {type(exc).__name__}."
                    break
        with _lock:
            _cache.pop(service, None)
        self.ledger.append(SYSTEM, "service.install_finished", id=request_id, ok=ok, summary=summary)
        self.running.pop(request_id, None)


# ---- What departments ask for when no tool provides it ----

def ask(app_ledger: Ledger, project: str, department: str, capability: str, why: str) -> str:
    if len(why.strip()) < 12:
        return "Say what you need the tool to do, specifically."
    for open_ask in asks(app_ledger):
        if open_ask["project"] == project and (open_ask["capability"].strip().lower() == capability.strip().lower() or _same_need(open_ask["capability"] + " " + open_ask["why"], capability + " " + why)):
            return f"That's already asked for, by {open_ask['department']}: \"{open_ask['capability']}\". The person will see it at Reception; carry on meanwhile."
    app_ledger.append(Actor.agent(department.lower()), "service.asked", project=project, department=department, capability=capability.strip()[:120], why=why.strip()[:600])
    return "Asked. The person will see it at Reception, with the tools that can provide it."


_FILLER = {"the", "and", "for", "with", "from", "into", "that", "this", "its", "their", "run", "use", "using", "tool", "tools", "project", "files", "file", "existing", "check", "checks", "results", "data", "plus", "only", "make", "provide", "established"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in _FILLER}


def _same_need(a: str, b: str) -> bool:
    """Two requests for what is really one tool, worded differently by different jobs."""
    x, y = _words(a), _words(b)
    return bool(x and y) and len(x & y) / min(len(x), len(y)) >= 0.6


def asks(app_ledger: Ledger, project: str = "") -> list[dict[str, Any]]:
    """Open requests to the Service agents, the given project's first. They're requests, not approvals: each closes when a
    Service agent answers it, its department later finishes the job, or the person closes it."""
    closed = {e["data"]["id"] for e in app_ledger.events() if e["kind"] in ("service.ask_closed", "service.ask_answered")}
    out = [{**e["data"], "id": e["id"], "at": e["at"]} for e in app_ledger.events() if e["kind"] == "service.asked" and e["id"] not in closed]
    return sorted(out, key=lambda a: a["project"] != project) if project else out


def settle_asks(app_ledger: Ledger, project: str, department: str, why: str) -> int:
    """Closes a department's open requests once it has finished the work they were for."""
    mine = [a for a in asks(app_ledger) if a["project"] == project and a["department"] == department]
    for a in mine:
        app_ledger.append(Actor.system("reception"), "service.ask_answered", id=a["id"], department=department, resolution=why)
    return len(mine)


def providers_of(capability: str) -> list[str]:
    """Tools that provide a capability, matching its id (fea.frame) or its words (structural analysis)."""
    words = set(re.findall(r"[a-z]+", capability.lower()))
    out = []
    for s in all_tools():
        caps = s["capabilities"]
        text = (s["name"] + " " + s["what"]).lower()
        if capability in caps or (len(words) >= 2 and sum(w in text for w in words) >= max(2, len(words) - 1)):
            out.append(s["id"])
    return out


def relevance(project_root: Path, events: list[dict[str, Any]], open_asks: list[dict[str, Any]]) -> dict[str, str]:
    """Which tools this project uses, and why, from its own evidence: its files, its history and what its departments asked for."""
    used: dict[str, str] = {"openai": "Every agent in this project runs on it."}
    if (project_root / ".git").exists():
        used["git"] = "Keeps this project's history."
    devices = [ini.parent.name for ini in (project_root / "firmware").glob("*/platformio.ini")]
    if devices:
        used["platformio"] = f"Builds this project's firmware ({', '.join(devices)})."
    if _imports(project_root):
        used["uv"] = "Makes this project's own Python environment."

    def has(*patterns: str) -> bool:
        return any(next(project_root.rglob(p), None) for p in patterns)
    if has("*.step", "*.stp"):
        used["cadquery"] = "Reads this project's STEP files."
    if has("*.kicad_sch", "*.kicad_pcb", "*.kicad_pro"):
        used["kicad"] = "Opens this project's schematics and PCBs."
    if (project_root / "datasheets").is_dir() and has("*.pdf"):
        used["pypdf"] = "Reads this project's datasheets."
    if (project_root / "package.json").is_file():
        used["node"] = "Builds this project's web code."
    for a in open_asks:
        if a["project"] == project_root.name:
            for tool_id in providers_of(a["capability"]):
                used.setdefault(tool_id, f"{a['department']} asked for {a['capability']}.")
    for e in events:  # added or removed by the person, or by a Service agent when the person asked
        tool_id = (e.get("data") or {}).get("tool")
        if e["kind"] == "service.added" and tool_id:
            used[tool_id] = e["data"].get("why") or "Added to this project."
        elif e["kind"] == "service.removed" and tool_id:
            used.pop(tool_id, None)
    return used


def set_used(project_ledger: Any, tool_id: str, on: bool, actor: Any, why: str = "") -> str:
    entry = entry_for(tool_id)
    if not entry:
        return "Unknown tool. Use an id from list_tools."
    project_ledger.append(actor, "service.added" if on else "service.removed", tool=tool_id, why=why.strip()[:300])
    return f"{entry['name']} {'added to' if on else 'removed from'} this project."
