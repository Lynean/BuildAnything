"""Tool tabs: a tab in a department's screen for one tool, written by a Service agent and approved by a person.

A tab is TSX (Tab.tsx plus any cards it imports) and, optionally, a Python data script (adapter.py). The app
compiles the TSX with esbuild against a small SDK and shows it in a sealed frame: no network, no access to the
app's own buttons, only a read-only bridge to this project's files and the data script's last output. The data
script runs in a sandbox (tabbox.py), only when the person has allowed tab scripts, and may run only the one program
the person approved for the tab. Nothing a Service agent writes reaches a department until a person approves it,
and an approved tab is a frozen copy: editing it afterwards makes a new proposal.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .ledger import Actor, Ledger, LedgerError

APP = Path(__file__).resolve().parents[2] / "app"
SDK = APP / "src" / "tab-sdk"
ESBUILD = APP / "node_modules" / "@esbuild" / "win32-x64" / "esbuild.exe"
TAB_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
FILE = re.compile(r"^(?:[A-Za-z0-9_-]+/)?[A-Za-z0-9_-]+\.(?:tsx|ts|py|md|json)$")
DEPARTMENTS = ("Mechanical", "Electrical", "Software", "Procurement", "Simulation")
ALLOWED_PACKAGES = ("react", "react-dom", "scheduler", "three")
MAX_FILE = 60_000
MAX_FILES = 16
RUN_SECONDS = 180
MAX_IMAGES_BYTES = 8_000_000
READ_LIMIT = 5_000_000
SECRET = re.compile(r"(^|[\\/])(\.env[^\\/]*|\.ssh|\.git|\.aws|\.azure|credentials[^\\/]*|secrets?[^\\/]*|keys?\.json|id_rsa[^\\/]*|.*\.pem|.*\.key)($|[\\/])", re.I)
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp"}
# Things that can't work in the sealed frame, or that break the app's look. Found before a person sees the tab.
LINT = [
    (re.compile(r"\b(fetch|XMLHttpRequest|WebSocket|EventSource)\s*\("), "The tab has no network access; get data from the data script (useData) or project files (useFile)."),
    (re.compile(r"\b(localStorage|sessionStorage|indexedDB|document\.cookie)\b"), "The sealed frame has no storage; keep state in React."),
    (re.compile(r"\bwindow\.(parent|top|opener)\b"), "Talk to the app only through the SDK's hooks."),
    (re.compile(r"\beval\s*\(|new\s+Function\s*\("), "No eval or new Function."),
    (re.compile(r"box-?shadow|text-?shadow|drop-shadow", re.I), "No glow or shadows: the app's theme is flat grey cards with a thin outline."),
    (re.compile(r"\bwidth\s*:\s*['\"]?\d{4,}px"), "No fixed widths over 999 px: the tab must fit its panel; use the SDK's Grid and percentages."),
    (re.compile(r"\bwhite-?[sS]pace\s*:\s*['\"]?nowrap"), "Long text must wrap; don't use nowrap except for short labels with an ellipsis."),
]
ADAPTER_LINT = [
    (re.compile(r"\b(requests|urllib\.request|httpx|socket|aiohttp)\b"), "Data scripts have no network access."),
    (re.compile(r"\bsubprocess\b|\bos\.system\b|\bos\.popen\b"), "Run the tab's program only through tool.run(args)."),
    (re.compile(r"\bopen\([^)]*['\"][wa]\+?['\"]"), "If you write files, write them under out, never in the project."),
]


class TabError(LedgerError):
    pass


def folder(app_root: Path, tab_id: str) -> Path:
    if not TAB_ID.fullmatch(tab_id):
        raise TabError("Tab ids are short lowercase words with hyphens, such as mujoco-sim or matlab-plots.")
    return app_root / "tabs" / tab_id


def _manifest(app_root: Path, tab_id: str) -> dict[str, Any]:
    path = folder(app_root, tab_id) / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save_manifest(app_root: Path, tab_id: str, data: dict[str, Any]) -> None:
    path = folder(app_root, tab_id) / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def _files(directory: Path) -> dict[str, str]:
    if not directory.is_dir():
        return {}
    return {p.relative_to(directory).as_posix(): p.read_text(encoding="utf-8") for p in sorted(directory.rglob("*")) if p.is_file() and FILE.fullmatch(p.relative_to(directory).as_posix())}


def _hash(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def write_file(app_root: Path, tab_id: str, path: str, content: str) -> dict[str, Any]:
    path = path.strip().replace("\\", "/").lstrip("/")
    if not FILE.fullmatch(path):
        raise TabError("Files are Tab.tsx, cards/<Name>.tsx, adapter.py, README.md or a .json fixture, at most one folder deep.")
    if len(content) > MAX_FILE:
        raise TabError(f"Keep each file under {MAX_FILE:,} characters.")
    work = folder(app_root, tab_id) / "proposed"
    if not (work / path).exists() and len(_files(work)) >= MAX_FILES:
        raise TabError(f"A tab has at most {MAX_FILES} files.")
    (work / path).parent.mkdir(parents=True, exist_ok=True)
    (work / path).write_text(content, encoding="utf-8")
    meta = _manifest(app_root, tab_id)
    if meta.get("status") in ("waiting", "approved") and meta.get("proposed_hash") != _hash(_files(work)):
        meta["status"] = "draft"  # an edit after proposing needs proposing again; an approved copy keeps working
        _save_manifest(app_root, tab_id, meta)
    elif not meta:
        _save_manifest(app_root, tab_id, {"id": tab_id, "status": "draft"})
    return {"written": path, "files": sorted(_files(work))}


def read_files(app_root: Path, tab_id: str, which: str = "proposed") -> dict[str, str]:
    return _files(folder(app_root, tab_id) / ("approved" if which == "approved" else "proposed"))


def lint(files: dict[str, str]) -> list[str]:
    problems = []
    if "Tab.tsx" not in files:
        problems.append("Write Tab.tsx: its default export is the tab.")
    for name, text in files.items():
        rules = ADAPTER_LINT if name.endswith(".py") else LINT if name.endswith((".tsx", ".ts")) else []
        for rule, why in rules:
            if rule.search(text):
                problems.append(f"{name}: {why}")
        if name.endswith(".py") and name == "adapter.py" and not re.search(r"^def collect\(\s*project\s*,\s*out\s*,\s*tool\s*\)", text, re.M):
            problems.append("adapter.py needs def collect(project, out, tool): returning a dict.")
    return problems


def build(app_root: Path, tab_id: str, which: str = "proposed") -> dict[str, Any]:
    """Compiles the tab with esbuild into one script for the sealed frame. Imports are limited to the SDK, React and three."""
    base = folder(app_root, tab_id)
    src = base / ("approved" if which == "approved" else "proposed")
    if not (src / "Tab.tsx").exists():
        return {"ok": False, "errors": ["There's no Tab.tsx yet."]}
    if not ESBUILD.exists():
        return {"ok": False, "errors": ["The app's compiler (esbuild) isn't installed. Run npm install in the app folder."]}
    out = base / f"build-{which}"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    entry = out / "entry.tsx"
    entry.write_text(f'import {{ mount }} from "@ba/tab";\nimport Tab from {json.dumps((src / "Tab.tsx").as_posix())};\nmount(Tab);\n', encoding="utf-8")
    meta_file = out / "meta.json"
    cmd = [str(ESBUILD), str(entry), "--bundle", "--format=iife", "--jsx=automatic", "--target=es2020", "--platform=browser", "--minify", "--log-level=error",
           f"--alias:@ba/tab={(SDK / 'index.tsx').as_posix()}", f"--alias:@ba/model={(SDK / 'model.tsx').as_posix()}", '--define:process.env.NODE_ENV="production"',
           f"--metafile={meta_file}", f"--outfile={out / 'bundle.js'}"]
    env = {**{k: v for k, v in os.environ.items() if k.upper() in ("SYSTEMROOT", "PATH", "PATHEXT", "TEMP", "TMP")}, "NODE_PATH": str(APP / "node_modules")}
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, env=env, cwd=str(out))
    except subprocess.TimeoutExpired:
        return {"ok": False, "errors": ["Compiling took over two minutes and was stopped."]}
    if done.returncode != 0:
        text = (done.stderr or done.stdout).replace(str(src), "").replace(str(src).replace("\\", "/"), "")
        return {"ok": False, "errors": [line for line in text.splitlines() if line.strip()][:20]}
    inputs = json.loads(meta_file.read_text(encoding="utf-8"))["inputs"]
    node_modules = (APP / "node_modules").resolve()
    outside = []
    for name in inputs:
        path = (out / name).resolve() if not Path(name).is_absolute() else Path(name).resolve()
        if path == entry.resolve() or src.resolve() in path.parents or SDK.resolve() in path.parents:
            continue
        if node_modules in path.parents and path.relative_to(node_modules).parts[0] in ALLOWED_PACKAGES:
            continue
        outside.append(name)
    if outside:
        return {"ok": False, "errors": [f"Tabs may import only @ba/tab, @ba/model, react and three, and their own files. Not: {', '.join(outside[:5])}"]}
    js = (out / "bundle.js").read_text(encoding="utf-8")
    return {"ok": True, "bytes": len(js), "errors": []}


def bundle(app_root: Path, tab_id: str, which: str = "approved") -> str:
    path = folder(app_root, tab_id) / f"build-{which}" / "bundle.js"
    if not path.exists():
        result = build(app_root, tab_id, which)
        if not result["ok"]:
            raise TabError("The tab doesn't compile: " + " ".join(result["errors"][:3]))
    return path.read_text(encoding="utf-8")


# ---- the data script ----

def scripts_allowed(app_ledger: Ledger) -> bool:
    on = False
    for e in app_ledger.events():
        if e["kind"] == "tabs.scripts_allowed":
            on = bool(e["data"].get("allowed"))
    return on


def set_scripts_allowed(app_ledger: Ledger, allowed: bool, actor: Actor) -> None:
    app_ledger.append(actor, "tabs.scripts_allowed", allowed=allowed)


def run_dir(app_root: Path, tab_id: str, project_id: str, which: str) -> Path:
    return folder(app_root, tab_id) / "runs" / which / project_id


def tab_python(project_root: Path) -> str:
    """Data scripts run in the project's own Python environment, so a tab uses the same packages as the program it shows.
    Before the project has one, they use the app's Python."""
    from . import services

    found = services.project_python(project_root)
    return str(found) if found else sys.executable


def python_packages(project_root: Path, names: list[str]) -> dict[str, Any]:
    """Which of these packages the tab's Python can import, for the Service agents."""
    python = tab_python(project_root)
    script = "import importlib.util, json, sys; print(json.dumps({n: importlib.util.find_spec(n) is not None for n in sys.argv[1:]}))"
    try:
        done = subprocess.run([python, "-c", script, *names], capture_output=True, text=True, timeout=30)
        have = json.loads(done.stdout.strip().splitlines()[-1]) if done.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        have = {}
    own = python != sys.executable
    return {"python": "the project's own environment (.venv in the project folder)" if own else "the app's own Python, because this project has no environment yet; once Internal Service sets one up, tab scripts move to it",
            "has": sorted(n for n, ok in have.items() if ok), "missing": sorted(n for n, ok in have.items() if not ok)}


def _safe_glob(project_root: Path, pattern: str) -> list[Path]:
    pattern = pattern.strip().replace("\\", "/").lstrip("/")
    if not pattern or ".." in Path(pattern).parts:
        return []
    root = project_root.resolve()
    return [p for p in sorted(root.glob(pattern))[:500] if p.is_file() and not SECRET.search(p.relative_to(root).as_posix())]


def reads_status(project_root: Path, reads: list[dict[str, str]]) -> list[dict[str, Any]]:
    """For each file pattern a tab declares, how many files match in this project now."""
    return [{**r, "found": len(_safe_glob(project_root, r["path"]))} for r in reads]


def clean_reads(raw: Any) -> list[dict[str, str]]:
    """The files a tab reads: [{path, what, made_by}], path a glob relative to the project such as mechanics/sim/runs/*.json."""
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not str(item.get("path", "")).strip():
            continue
        path = str(item["path"]).strip().replace("\\", "/").lstrip("/")
        if ".." in Path(path).parts:
            raise TabError(f"{path}: a tab reads only inside the project.")
        made_by = str(item.get("made_by", "")).strip()
        if made_by not in (*DEPARTMENTS, "Person", ""):
            raise TabError(f"{path}: made_by is the department that makes these files ({', '.join(DEPARTMENTS)}), or Person.")
        out.append({"path": path, "what": str(item.get("what", "")).strip()[:200], "made_by": made_by})
    return out[:20]


SKIP_DIRS = {".venv", "node_modules", ".git", "retired", "__pycache__", ".pio", "dist", "build"}


def similar_files(project_root: Path, pattern: str, limit: int = 6) -> list[str]:
    """Project files of the same type as a tab's read that the tab doesn't find, such as robot/Desk/Desk.urdf when the
    tab looks for robot/robot.urdf: the sign that the tab was written for another project's layout."""
    suffix = Path(pattern.replace("*", "x")).suffix.lower()
    if not suffix or suffix == ".x":
        return []
    root = project_root.resolve()
    found = []
    for path in root.rglob(f"*{suffix}"):
        rel = path.relative_to(root)
        if path.is_file() and not SKIP_DIRS.intersection(rel.parts) and not SECRET.search(rel.as_posix()):
            found.append(rel.as_posix())
            if len(found) >= limit:
                break
    return found


def mismatches(app_root: Path, app_ledger: Ledger, project_root: Path) -> list[dict[str, Any]]:
    """Approved tabs shown on this project that look for files in places this project doesn't use, while files of that
    type exist elsewhere in it. The tab should follow the project, so this goes to the Service agent that made it,
    not to the departments."""
    out = []
    for t in listing(app_root, app_ledger):
        if not (t.get("approved_hash") and t["intact"] and project_root.name in t["projects"]):
            continue
        wrong = []
        for r in reads_status(project_root, t.get("approved_reads") or []):
            if r["found"] == 0:
                similar = similar_files(project_root, r["path"])
                if similar:
                    wrong.append({"path": r["path"], "what": r.get("what", ""), "project_has": similar})
        if wrong:
            out.append({"tab": t["id"], "title": t.get("approved_title", t["id"]), "service": "Internal Service" if t.get("by") == "internal_service" else "External Service", "reads": wrong})
    return out


def briefs_due(app_root: Path, app_ledger: Ledger, project_root: Path, team: list[str]) -> list[dict[str, Any]]:
    """Departments that make files an approved tab on this project reads, where some of those files don't exist yet.
    Keyed by the tab's approved version, so a department hears again only when the tab changes."""
    out = []
    for t in listing(app_root, app_ledger):
        if not (t.get("approved_hash") and t["intact"] and project_root.name in t["projects"]):
            continue
        status = reads_status(project_root, t.get("approved_reads") or [])
        for department in sorted({r["made_by"] for r in status if r["made_by"] in team}):
            mine = [r for r in status if r["made_by"] == department]
            if any(r["found"] == 0 and not similar_files(project_root, r["path"], 1) for r in mine):  # brief only when nothing like it exists
                out.append({"tab": t["id"], "version": t["approved_hash"], "department": department, "title": t.get("approved_title", t["id"]),
                            "shown_in": t.get("approved_department", ""), "summary": t.get("summary", ""), "reads": mine})
    return out


def run(app_root: Path, app_ledger: Ledger, tab_id: str, project_root: Path, which: str = "approved") -> dict[str, Any]:
    """Runs the tab's data script on one project in the sandbox and keeps its output for the tab."""
    if not scripts_allowed(app_ledger):
        return {"ok": False, "error": "Tab data scripts are switched off. Switch them on at Reception if you want this tab to collect data."}
    src = folder(app_root, tab_id) / ("approved" if which == "approved" else "proposed")
    if not (src / "adapter.py").exists():
        return {"ok": False, "error": "This tab has no data script."}
    meta = _manifest(app_root, tab_id)
    exe = (meta.get("approved_exe") if which == "approved" else meta.get("exe")) or ""
    out = run_dir(app_root, tab_id, project_root.name, which)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    work = folder(app_root, tab_id) / "work"
    work.mkdir(exist_ok=True)
    keep = {k: v for k, v in os.environ.items() if k.upper() in ("SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS", "LOCALAPPDATA", "PROGRAMFILES", "USERPROFILE")}
    env = {**keep, "TEMP": str(work), "TMP": str(work), "PYTHONIOENCODING": "utf-8", "MPLBACKEND": "Agg", "MUJOCO_GL": os.environ.get("MUJOCO_GL", "")}
    box = Path(__file__).parent / "tabbox.py"
    import time

    started = time.time()
    try:
        done = subprocess.run([tab_python(project_root), "-I", str(box), str(work), str(src / "adapter.py"), str(project_root), str(out), str(project_root.parent), exe],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=RUN_SECONDS, env=env, cwd=str(work))
    except subprocess.TimeoutExpired:
        result = {"ok": False, "error": f"The data script ran for over {RUN_SECONDS} s and was stopped."}
    else:
        line = next((l for l in reversed(done.stdout.splitlines()) if l.startswith("TAB_RESULT ")), None)
        if done.returncode == 0 and line:
            result = {"ok": True, "output": json.loads(line[len("TAB_RESULT "):]), "printed": done.stdout.replace(line, "")[-3000:]}
        else:
            err = (done.stderr or done.stdout).strip().splitlines()
            reason = next((l for l in reversed(err) if l.strip() and not l.startswith(" ")), "it stopped without saying why")
            result = {"ok": False, "error": f"The data script failed: {reason[:500]}", "log": "\n".join(err[-25:])[-4000:]}
    result.update({"ranAt": time.strftime("%Y-%m-%dT%H:%M:%S"), "seconds": round(time.time() - started, 1)})
    (out / "_result.json").write_text(json.dumps(result, default=str), encoding="utf-8")
    return result


def data(app_root: Path, tab_id: str, project_id: str, which: str = "approved") -> dict[str, Any]:
    """The last run's output with its pictures as data URLs, for the sealed frame."""
    out = run_dir(app_root, tab_id, project_id, which)
    saved = out / "_result.json"
    if not saved.exists():
        return {"ok": None, "output": None, "images": {}, "ranAt": None}
    result = json.loads(saved.read_text(encoding="utf-8"))
    images, total = {}, 0
    for p in sorted(out.rglob("*")):
        kind = IMAGE_TYPES.get(p.suffix.lower())
        if kind and p.is_file():
            size = p.stat().st_size
            if total + size > MAX_IMAGES_BYTES:
                result.setdefault("warnings", []).append(f"Pictures over {MAX_IMAGES_BYTES // 1_000_000} MB in total weren't sent: {p.name}")
                continue
            total += size
            images[p.relative_to(out).as_posix()] = f"data:{kind};base64," + base64.b64encode(p.read_bytes()).decode("ascii")
    return {**result, "images": images}


# ---- read-only project files for the tab ----

def _project_path(project_root: Path, path: str) -> Path:
    target = (project_root / path.strip().lstrip("/\\")).resolve()
    if target != project_root.resolve() and project_root.resolve() not in target.parents:
        raise TabError("Tabs can read only this project's files.")
    if SECRET.search(str(target.relative_to(project_root.resolve()))):
        raise TabError("Tabs can't read secrets or Git files.")
    return target


def read_project_file(project_root: Path, path: str) -> dict[str, Any]:
    target = _project_path(project_root, path)
    if not target.is_file():
        raise TabError(f"There's no file {path} in this project.")
    size = target.stat().st_size
    if size > READ_LIMIT:
        raise TabError(f"{path} is {size / 1e6:.1f} MB; tabs read files up to {READ_LIMIT // 1_000_000} MB.")
    raw = target.read_bytes()
    try:
        return {"path": path, "size": size, "text": raw.decode("utf-8")}
    except UnicodeDecodeError:
        return {"path": path, "size": size, "base64": base64.b64encode(raw).decode("ascii")}


def list_project_files(project_root: Path, directory: str = "", pattern: str = "*") -> list[dict[str, Any]]:
    base = _project_path(project_root, directory or ".")
    if not base.is_dir():
        return []
    out = []
    for p in sorted(base.rglob(pattern))[:500]:
        rel = p.relative_to(project_root.resolve()).as_posix()
        if p.is_file() and not SECRET.search(rel) and "node_modules" not in p.parts and ".pio" not in p.parts:
            out.append({"path": rel, "size": p.stat().st_size, "modified": p.stat().st_mtime})
    return out


# ---- proposing, approving, showing ----

def propose(app_root: Path, app_ledger: Ledger, tab_id: str, tool: str, department: str, title: str, summary: str, exe: str, why: str, actor: Actor, reads: Any = None) -> dict[str, Any]:
    reads = clean_reads(reads)
    if department not in DEPARTMENTS:
        raise TabError(f"department is one of {', '.join(DEPARTMENTS)}.")
    title = title.strip()
    if not 2 <= len(title) <= 24:
        raise TabError("The tab's title is its name in the tab row: 2 to 24 characters, such as MuJoCo or MATLAB plots.")
    files = read_files(app_root, tab_id)
    problems = lint(files)
    built = build(app_root, tab_id)
    if problems or not built["ok"]:
        raise TabError("Fix these before proposing: " + " ".join(problems + built.get("errors", []))[:1500])
    exe_path = ""
    if exe.strip():
        found = shutil.which(exe.strip()) or (exe.strip() if Path(exe.strip()).is_file() else None)
        if not found:
            raise TabError(f"The program {exe.strip()} isn't on this computer, so the tab couldn't run it. Leave exe empty, or ask the person to install it first.")
        exe_path = str(Path(found).resolve())
    meta = _manifest(app_root, tab_id)
    meta.update({"id": tab_id, "tool": tool.strip()[:60], "department": department, "title": title, "summary": summary.strip()[:600], "why": why.strip()[:600],
                 "exe": exe_path, "adapter": "adapter.py" in files, "status": "waiting", "proposed_hash": _hash(files), "by": actor.name, "reads": reads})
    _save_manifest(app_root, tab_id, meta)
    app_ledger.append(actor, "tab.proposed", id=tab_id, tool=meta["tool"], department=department, title=title, hash=meta["proposed_hash"])
    return meta


def decide(app_root: Path, app_ledger: Ledger, tab_id: str, approve: bool, reason: str, actor: Actor) -> dict[str, Any]:
    meta = _manifest(app_root, tab_id)
    if meta.get("status") != "waiting":
        raise TabError("That tab isn't waiting for a decision.")
    files = read_files(app_root, tab_id)
    if _hash(files) != meta.get("proposed_hash"):
        raise TabError("The tab changed after it was proposed. Ask the Service agent to propose it again.")
    app_ledger.append(actor, "tab.decided", id=tab_id, approved=approve, reason=reason.strip()[:600], hash=meta["proposed_hash"])
    if approve:
        base = folder(app_root, tab_id)
        shutil.rmtree(base / "approved", ignore_errors=True)
        shutil.copytree(base / "proposed", base / "approved")
        shutil.rmtree(base / "build-approved", ignore_errors=True)
        meta.update({"status": "approved", "approved_hash": meta["proposed_hash"], "approved_exe": meta.get("exe", ""), "approved_title": meta["title"],
                     "approved_department": meta["department"], "approved_adapter": meta.get("adapter", False), "approved_reads": meta.get("reads", [])})
    else:
        meta.update({"status": "rejected", "rejected_because": reason.strip()[:600]})
    _save_manifest(app_root, tab_id, meta)
    return meta


def set_shown(app_ledger: Ledger, tab_id: str, project_id: str, on: bool, actor: Actor) -> None:
    app_ledger.append(actor, "tab.shown", id=tab_id, project=project_id, on=on)


def shown_projects(app_ledger: Ledger, tab_id: str) -> set[str]:
    out: set[str] = set()
    for e in app_ledger.events():
        if e["kind"] == "tab.shown" and e["data"].get("id") == tab_id:
            (out.add if e["data"].get("on") else out.discard)(e["data"].get("project"))
    return out


def listing(app_root: Path, app_ledger: Ledger) -> list[dict[str, Any]]:
    base = app_root / "tabs"
    out = []
    for path in sorted(base.glob("*/manifest.json")) if base.is_dir() else []:
        meta = json.loads(path.read_text(encoding="utf-8"))
        intact = meta.get("status") != "approved" or _hash(read_files(app_root, meta["id"], "approved")) == meta.get("approved_hash")
        out.append({**meta, "intact": intact, "projects": sorted(shown_projects(app_ledger, meta["id"])), "approvedCopy": bool(meta.get("approved_hash"))})
    return out


def for_department(app_root: Path, app_ledger: Ledger, project_id: str, department: str) -> list[dict[str, Any]]:
    """Approved tabs the person chose to show on this project, in this department."""
    tabs = []
    for t in listing(app_root, app_ledger):
        if t.get("approved_hash") and t["intact"] and t.get("approved_department") == department and project_id in t["projects"]:
            tabs.append({"id": t["id"], "title": t["approved_title"], "tool": t.get("tool", ""), "adapter": t.get("approved_adapter", False), "scripts": scripts_allowed(app_ledger)})
    return tabs


BUILTIN = Path(__file__).parent / "builtin_skills"


def builtin_skills(connector: bool = False) -> str:
    """The Tab-creator and Card-creator Skills for both Service agents, and the Service-connector Skill for External Service."""
    parts = []
    for name in (("service-connector",) if connector else ()) + ("tab-creator", "card-creator"):
        path = BUILTIN / name / "SKILL.md"
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n\n---\n\n".join(parts)


def example(name: str) -> dict[str, str]:
    """A worked example: a tab (fusion, mujoco, kicad, matlab, plot) or a connection (connection-github, connection-altium-365, connection-fusion-mcp)."""
    key = name.strip().lower()
    base = BUILTIN / ("service-connector" if key.startswith("connection-") else "tab-creator") / "examples" / key.removeprefix("connection-")
    if not base.is_dir():
        names = sorted(p.name for p in (BUILTIN / "tab-creator" / "examples").iterdir() if p.is_dir()) + sorted("connection-" + p.name for p in (BUILTIN / "service-connector" / "examples").iterdir() if p.is_dir())
        raise TabError(f"No example called {name}. Examples: {', '.join(names)}.")
    return {p.relative_to(base).as_posix(): p.read_text(encoding="utf-8") for p in sorted(base.rglob("*")) if p.is_file()}
