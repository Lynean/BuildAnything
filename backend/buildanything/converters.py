"""File converters: how the app learns to show a kind of file it has no viewer for.

When a person opens such a file, the Platform agent writes a converter for its type, tests it on that file in a
sandbox (convbox.py) until it works, and proposes it. A person approves it once; from then on every file of that type,
in every project, opens through it with no AI cost. A converter only turns a file into something an existing viewer
shows (formatted text, a page, a table, an image). It never changes the app's own code.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ledger import Actor, Ledger, LedgerError

RUN_SECONDS = 60
SAMPLE_BYTES = 6000
_cache: dict[tuple[str, str, float, int], dict[str, Any]] = {}


class ConverterError(LedgerError):
    pass


def type_of(path: Path) -> str:
    return path.suffix.lower() or path.name.lower()


def _key(file_type: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", file_type.lower().lstrip(".")) or "noext"


def folder(app_root: Path, file_type: str) -> Path:
    return app_root / "platform" / "converters" / _key(file_type)


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]


def meta(app_root: Path, file_type: str) -> dict[str, Any] | None:
    f = folder(app_root, file_type) / "meta.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None


def _save_meta(app_root: Path, file_type: str, data: dict[str, Any]) -> dict[str, Any]:
    d = folder(app_root, file_type)
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return data


def code_of(app_root: Path, file_type: str, which: str = "proposed.py") -> str:
    f = folder(app_root, file_type) / which
    return f.read_text(encoding="utf-8") if f.is_file() else ""


def approved(app_root: Path, file_type: str) -> Path | None:
    """The approved converter for a type, only if its code is exactly what the person approved."""
    m = meta(app_root, file_type)
    f = folder(app_root, file_type) / "convert.py"
    if not m or m.get("status") != "approved" or not f.is_file() or _hash(f.read_text(encoding="utf-8")) != m.get("approved_hash"):
        return None
    return f


def listing(app_root: Path) -> list[dict[str, Any]]:
    base = app_root / "platform" / "converters"
    out = []
    for d in sorted(base.iterdir()) if base.is_dir() else []:
        m = meta(app_root, "." + d.name) if (d / "meta.json").is_file() else None
        if m:
            out.append({**m, "code": code_of(app_root, m["type"], "convert.py" if m.get("status") == "approved" else "proposed.py")})
    return out


def _env(work: Path) -> dict[str, str]:
    """No keys or tokens reach a converter: only what Windows and Python need to start."""
    keep = {k: v for k, v in os.environ.items() if k.upper() in ("SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS")}
    return {**keep, "TEMP": str(work), "TMP": str(work), "PYTHONIOENCODING": "utf-8", "MPLBACKEND": "Agg", "MPLCONFIGDIR": str(work)}


def run(app_root: Path, converter: Path, source: Path, projects_root: Path, seconds: float = RUN_SECONDS) -> dict[str, Any]:
    """Runs a converter on one file in the sandbox. Returns {"ok": True, "result": {...}} or the error it hit."""
    work = app_root / "platform" / "scratch" / _hash(str(source) + str(converter))
    work.mkdir(parents=True, exist_ok=True)
    box = Path(__file__).parent / "convbox.py"
    try:
        done = subprocess.run([sys.executable, "-I", str(box), str(work), str(converter), str(source), str(projects_root)], cwd=work, env=_env(work),
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=seconds)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"The converter didn't finish within {seconds:g} s."}
    out = done.stdout or ""
    marker = out.rfind("\n@@CONVERTED@@")
    if done.returncode != 0 or marker < 0:
        return {"ok": False, "error": (done.stderr or out or f"Exit code {done.returncode}.")[-3000:], "printed": out[-1500:]}
    try:
        return {"ok": True, "result": json.loads(out[marker + len("\n@@CONVERTED@@"):]), "printed": out[:marker][-1500:]}
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"The output wasn't valid JSON: {exc}"}


def view(app_root: Path, projects_root: Path, source: Path) -> dict[str, Any]:
    """What a viewer shows for a file through its type's approved converter. Kept until the file or converter changes."""
    converter = approved(app_root, type_of(source))
    if converter is None:
        return {"ok": False, "error": "No approved converter for this type."}
    st = source.stat()
    key = (str(source), _hash(converter.read_text(encoding="utf-8")), st.st_mtime, st.st_size)
    if key not in _cache:
        _cache[key] = run(app_root, converter, source, projects_root)
    return _cache[key]


def sample(source: Path) -> dict[str, Any]:
    """What the agent sees of the file: its size, the start as text if it reads as text, and the first bytes in hex."""
    raw = source.read_bytes()[:SAMPLE_BYTES]
    text = raw.decode("utf-8", errors="replace")
    printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in text) / max(1, len(text))
    return {"name": source.name, "type": type_of(source), "size_bytes": source.stat().st_size, "looks_like_text": printable > 0.95,
            "start_as_text": text if printable > 0.95 else None, "first_bytes_hex": raw[:256].hex(" ")}


def libraries() -> list[str]:
    from importlib import metadata

    names = sorted({(d.metadata.get("Name") or "").lower() for d in metadata.distributions()} - {""})
    return [n for n in names if not n.startswith(("types-", "pytest", "pip", "setuptools", "wheel"))][:400]


def tools(app_root: Path, projects_root: Path, source: Path, project: str, state: dict[str, Any]) -> list[Any]:
    file_type = type_of(source)
    d = folder(app_root, file_type)

    def inspect_file() -> dict[str, Any]:
        """Look at the file the person opened: its size, its start as text if it is text, and its first bytes in hex."""
        return sample(source)

    def list_libraries() -> dict[str, Any]:
        """The Python packages a converter can import, besides the standard library. Nothing else can be installed."""
        return {"packages": libraries()}

    def write_converter(code: str) -> dict[str, Any]:
        """Save your converter: a Python file defining convert(path) -> dict. Saving replaces the previous draft."""
        if "def convert" not in code:
            return {"error": "Define convert(path) -> dict."}
        d.mkdir(parents=True, exist_ok=True)
        (d / "draft.py").write_text(code, encoding="utf-8")
        state["draft_hash"], state["tested_hash"] = _hash(code), None
        return {"saved": True, "bytes": len(code.encode("utf-8"))}

    def test_converter() -> dict[str, Any]:
        """Run your saved converter on the person's file in the sandbox and see what it returns, or the error it hit."""
        draft = d / "draft.py"
        if not draft.is_file():
            return {"error": "Write the converter first."}
        state["tests"] = state.get("tests", 0) + 1
        result = run(app_root, draft, source, projects_root)
        if not result["ok"]:
            return {"ok": False, "error": result["error"], "printed": result.get("printed", "")}
        out = result["result"]
        state["tested_hash"], state["preview"] = _hash(draft.read_text(encoding="utf-8")), out
        shown = {k: (v[:3000] + "…" if isinstance(v, str) and len(v) > 3000 else v) for k, v in out.items() if k != "png_base64"}
        if "png_base64" in out:
            shown["png_base64"] = f"<{len(out['png_base64'])} characters of PNG>"
        if "sheets" in out:
            shown["sheets"] = [{"name": s.get("name"), "rows": (s.get("rows") or [])[:8], "total_rows": len(s.get("rows") or [])} for s in out["sheets"][:3]]
        return {"ok": True, "output": shown}

    def propose_converter(summary: str) -> dict[str, Any]:
        """Propose the converter for the person to approve. Only the exact code that last passed test_converter can be proposed."""
        draft = d / "draft.py"
        if not draft.is_file() or state.get("tested_hash") != state.get("draft_hash") or not state.get("tested_hash"):
            return {"error": "Test this exact code with test_converter and get it working first."}
        code = draft.read_text(encoding="utf-8")
        (d / "proposed.py").write_text(code, encoding="utf-8")
        preview = state.get("preview") or {}
        if preview.get("kind") == "image" and len(preview.get("png_base64", "")) > 2_000_000:
            preview = {"kind": "text", "text": "(The preview image is too big to keep; approve to see it.)"}
        old = meta(app_root, file_type) or {}
        _save_meta(app_root, file_type, {"type": file_type, "status": "proposed", "summary": summary.strip()[:800], "output_kind": preview.get("kind"),
                                         "tested_on": f"{project}: {source.name}", "proposed_hash": _hash(code), "tests": state.get("tests", 0),
                                         "proposed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "preview": preview,
                                         "approved_hash": old.get("approved_hash"), "history": old.get("history", [])})
        state["proposed"] = True
        return {"proposed": True, "note": "The person will approve or reject it."}

    return [inspect_file, list_libraries, write_converter, test_converter, propose_converter]


async def make(app_root: Path, projects_root: Path, ledger: Ledger, gateway: Any, source: Path) -> dict[str, Any]:
    """The Platform agent writes, tests and proposes a converter for this file's type. Spend goes to the project it was asked from."""
    from .threads import _threaded

    file_type = type_of(source)
    current = meta(app_root, file_type) or {}
    if approved(app_root, file_type):
        return current
    if current.get("status") == "proposed":
        return current
    state: dict[str, Any] = {}
    values = {
        "file": f"{source.name} ({file_type}, {source.stat().st_size} bytes)",
        "viewers": "markdown {text}; html {html} shown in a sandboxed frame with no scripts; table {sheets: [{name, rows: [[cells]]}]}; image {png_base64}; text {text}.",
        "earlier": f"An earlier converter for {file_type} was rejected: {current.get('reason', '')}" if current.get("status") == "rejected" else "None.",
    }
    report = await gateway.run(agent="platform", values=values, tools=[_threaded(t) for t in tools(app_root, projects_root, source, ledger.root.name, state)], request_limit=30)
    ledger.append(Actor.system("platform"), "platform.converter_made", type=file_type, proposed=bool(state.get("proposed")), tests=state.get("tests", 0), report=str(report)[:1500])
    if not state.get("proposed"):
        raise ConverterError(f"The Platform agent couldn't make a working converter for {file_type}. It said: {str(report)[:600]}")
    return meta(app_root, file_type) or {}


def decide(app_root: Path, app_ledger: Ledger, file_type: str, approve: bool, reason: str, actor: Actor) -> dict[str, Any]:
    """Only a person approves a converter, and the approval covers exactly the code they were shown."""
    m = meta(app_root, file_type)
    if not m or m.get("status") != "proposed":
        raise ConverterError("There's no proposed converter for this type.")
    code = code_of(app_root, file_type)
    if _hash(code) != m.get("proposed_hash"):
        raise ConverterError("The proposed code changed after it was tested. Ask Platform again.")
    if not approve and len(reason.strip()) < 4:
        raise ConverterError("Say why, so Platform can do better next time.")
    app_ledger.append(actor, "platform.converter_decided", type=file_type, approved=approve, code_hash=m["proposed_hash"], reason=reason.strip())
    history = m.get("history", []) + [{"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "approved": approve, "by": actor.name, "reason": reason.strip(), "hash": m["proposed_hash"]}]
    if approve:
        (folder(app_root, file_type) / "convert.py").write_text(code, encoding="utf-8")
        m.update(status="approved", approved_hash=m["proposed_hash"], approved_by=actor.name)
    else:
        m.update(status="approved" if approved(app_root, file_type) else "rejected", reason=reason.strip())
    _cache.clear()
    return _save_meta(app_root, file_type, {**m, "history": history})
