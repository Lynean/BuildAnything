"""Terminals inside the app, for the person: a shell in the project folder, with the project's own Python first on PATH.

Only the person uses these, from the app's screen; no agent has a tool for them. Each terminal is a real shell whose
output streams back to the screen. Without a pseudo-terminal, full-screen programs (editors, progress bars that redraw)
don't render, but anything that prints lines, and programs that open their own window, work. Stop ends whatever is
running in it, with everything it started, and starts a fresh shell in the same folder.
"""
from __future__ import annotations

import atexit
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import connectors as conn

MAX_CHUNKS = 4000  # kept per terminal for the screen; older output is dropped
MAX_TERMINALS = 8


class TerminalError(ValueError):
    pass


def _shell() -> list[str]:
    if os.name == "nt":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/Q", "/K", "prompt $P$G$S"]
    return [os.environ.get("SHELL", "/bin/bash"), "-i"]


class Terminal:
    def __init__(self, project: str, project_root: Path, folder: str):
        base = project_root.resolve()
        cwd = (base / folder).resolve() if folder else base
        if cwd != base and base not in cwd.parents:
            raise TerminalError("A terminal opens inside the project folder.")
        if not cwd.is_dir():
            raise TerminalError(f"{folder} isn't a folder in this project.")
        self.id = f"term_{uuid.uuid4().hex[:8]}"
        self.project, self.root, self.cwd = project, base, cwd
        self.title = cwd.name if cwd != base else "project"
        self.chunks: list[tuple[int, str]] = []
        self.seq = 0
        self.lock = threading.Lock()
        self.started = time.time()
        self.proc: subprocess.Popen | None = None
        self._start()

    def _env(self) -> dict[str, str]:
        env = conn.clean_environment()  # no API keys; includes programs installed since the app started
        venv = self.root / ".venv" / ("Scripts" if os.name == "nt" else "bin")
        if venv.is_dir():
            env["PATH"] = str(venv) + os.pathsep + env.get("PATH", "")
            env["VIRTUAL_ENV"] = str(self.root / ".venv")
        env.update({"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "TERM": "dumb"})
        return env

    def _start(self) -> None:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.proc = subprocess.Popen(_shell(), cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     env=self._env(), creationflags=flags, bufsize=0, start_new_session=os.name != "nt")
        self._add(f"Terminal in {self.cwd}\n")
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc: subprocess.Popen) -> None:
        stream = proc.stdout
        while stream is not None:
            data = stream.read1(4096) if hasattr(stream, "read1") else stream.read(4096)
            if not data:
                break
            self._add(data.decode("utf-8", errors="replace").replace("\r\n", "\n"))
        if proc is self.proc:
            self._add(f"\n[The shell ended with code {proc.wait()}. Press Stop to start a fresh one.]\n")

    def _add(self, text: str) -> None:
        with self.lock:
            self.seq += 1
            self.chunks.append((self.seq, text))
            del self.chunks[:-MAX_CHUNKS]

    def output(self, after: int = 0) -> dict[str, Any]:
        with self.lock:
            fresh = [t for s, t in self.chunks if s > after]
            return {"text": "".join(fresh), "seq": self.seq, "running": self.alive()}

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def send(self, line: str) -> None:
        if not self.alive():
            raise TerminalError("The shell has ended. Press Stop to start a fresh one.")
        self._add(line + "\n")
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write((line + ("\r\n" if os.name == "nt" else "\n")).encode("utf-8"))
        self.proc.stdin.flush()

    def _kill(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True)
        else:
            import signal

            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except OSError:
                self.proc.kill()

    def stop(self) -> None:
        """Ends whatever is running, with everything it started, and opens a fresh shell in the same folder."""
        self._kill()
        self._add("\n[Stopped.]\n")
        self._start()  # the old shell's reader sees it's no longer the current one, so it doesn't report the end

    def close(self) -> None:
        self._kill()
        self.proc = None

    def view(self) -> dict[str, Any]:
        return {"id": self.id, "project": self.project, "title": self.title, "folder": self.cwd.relative_to(self.root).as_posix() if self.cwd != self.root else "", "running": self.alive()}


class Terminals:
    def __init__(self) -> None:
        self.open: dict[str, Terminal] = {}
        atexit.register(self.close_all)

    def create(self, project: str, project_root: Path, folder: str = "") -> Terminal:
        if len(self.open) >= MAX_TERMINALS:
            raise TerminalError(f"{MAX_TERMINALS} terminals are open. Close one first.")
        t = Terminal(project, project_root, folder.strip().replace(chr(92), "/").strip("/"))
        self.open[t.id] = t
        return t

    def get(self, terminal_id: str) -> Terminal:
        t = self.open.get(terminal_id)
        if t is None:
            raise TerminalError("That terminal is closed.")
        return t

    def close(self, terminal_id: str) -> None:
        t = self.open.pop(terminal_id, None)
        if t:
            t.close()

    def listing(self, project: str = "") -> list[dict[str, Any]]:
        return [t.view() for t in self.open.values() if not project or t.project == project]

    def close_all(self) -> None:
        for t in list(self.open.values()):
            t.close()
        self.open.clear()
