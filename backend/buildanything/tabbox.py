"""Runs one tool tab's data script: python -I tabbox.py <work> <adapter.py> <project> <out> <projects_root> [exe]

A data script is a Python file with collect(project, out, tool) -> dict. It reads the project's files (read-only),
may run the one program its tab was approved for through tool.run(...), writes pictures and files into out, and
returns JSON for the tab. Guard rails, like the converter sandbox: no network, no other programs, no secrets or Git
files, no other project's files, nothing written outside out and its own scratch folder. These are guard rails
against honest mistakes, not a security boundary against code written to escape them; the app also strips the
environment (no keys) and kills the run at its time limit.
"""
from __future__ import annotations

import json
import os
import re
import runpy
import subprocess
import sys
from pathlib import Path

SECRET = re.compile(r"(^|[\\/])(\.env[^\\/]*|\.ssh|\.git|\.aws|\.azure|\.config[\\/]gcloud|credentials[^\\/]*|secrets?[^\\/]*|keys?\.json|id_rsa[^\\/]*|.*\.pem|.*\.key)($|[\\/])", re.I)
BLOCKED = ("socket.connect", "socket.bind", "socket.sendto", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.startfile", "os.kill", "winreg.", "webbrowser.open")
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
MAX_OUT = 2_000_000


def _resolved(path: object) -> Path | None:
    try:
        return Path(os.path.abspath(os.fsdecode(path))).resolve()
    except (TypeError, ValueError, OSError):
        return None


def _inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


class Tool:
    """The one program this tab may run, if it was approved with one."""

    def __init__(self, exe: str | None, project: Path):
        self.exe = exe
        self._project = project

    def run(self, args: list[str], timeout: float = 120) -> dict[str, object]:
        if not self.exe:
            raise PermissionError("This tab was approved without a program to run, so tool.run isn't available.")
        if not all(isinstance(a, str) for a in args):
            raise TypeError("tool.run takes a list of strings.")
        done = subprocess.run([self.exe, *args], cwd=self._project, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return {"code": done.returncode, "stdout": done.stdout[-20000:], "stderr": done.stderr[-8000:]}


def install(work: Path, adapter: Path, project: Path, out: Path, projects_root: Path, exe: str | None) -> None:
    work, adapter, project, out, projects_root = (p.resolve() for p in (work, adapter, project, out, projects_root))
    allowed_exe = _resolved(exe) if exe else None

    def hook(event: str, args: tuple) -> None:
        if event.startswith(BLOCKED):
            raise PermissionError(f"Tab scripts can't use {event}: they run without network access, and can run only their own approved program.")
        if event == "subprocess.Popen":
            executable, argv = args[0], args[1]
            first = executable or (argv[0] if isinstance(argv, (list, tuple)) and argv else argv)
            target = _resolved(first) if first else None
            if allowed_exe is None or target != allowed_exe:
                raise PermissionError(f"Tab scripts can run only the program this tab was approved for, through tool.run. Not: {first}")
            return
        if event == "open" and args and not isinstance(args[0], int):
            path, mode, flags = (list(args) + [None, None, 0])[:3]
            target = _resolved(path)
            if target is None:
                return
            text = str(target)
            if SECRET.search(text):
                raise PermissionError(f"Tab scripts can't open secrets or Git files: {target.name}")
            if _inside(target, projects_root) and not _inside(target, project) and not _inside(target, adapter.parent.parent.parent):
                raise PermissionError("Tab scripts can read only the project they run on.")
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or bool((flags or 0) & WRITE_FLAGS)
            if writing and not (_inside(target, out) or _inside(target, work)):
                raise PermissionError(f"Tab scripts write only into their out folder, not {target.name}. The project's files are read-only.")
        if event in ("os.remove", "os.rename", "os.replace", "os.rmdir", "os.mkdir", "shutil.rmtree", "shutil.move", "shutil.copyfile"):
            for p in args[:2]:
                target = _resolved(p) if isinstance(p, (str, bytes, os.PathLike)) else None
                if target is not None and not (_inside(target, out) or _inside(target, work)):
                    raise PermissionError(f"Tab scripts change files only inside their out folder, not {target.name}.")

    sys.addaudithook(hook)


def main() -> None:
    work, adapter, project, out, projects_root = (Path(a) for a in sys.argv[1:6])
    exe = sys.argv[6] if len(sys.argv) > 6 and sys.argv[6] else None
    out.mkdir(parents=True, exist_ok=True)
    install(work, adapter, project, out, projects_root, exe)
    module = runpy.run_path(str(adapter))
    collect = module.get("collect")
    if not callable(collect):
        raise SystemExit("The data script needs a function collect(project, out, tool).")
    result = collect(project, out, Tool(exe, project))
    text = json.dumps(result if result is not None else {}, default=str)
    if len(text) > MAX_OUT:
        raise SystemExit(f"collect returned {len(text):,} characters of JSON; keep it under {MAX_OUT:,}. Save big data as files in out.")
    print("TAB_RESULT " + text, flush=True)


if __name__ == "__main__":
    main()
