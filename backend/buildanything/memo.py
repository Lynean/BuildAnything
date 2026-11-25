"""Remembers expensive answers about a project until the project or its code actually changes."""
from __future__ import annotations

import hashlib
import os
import pickle
from typing import Any, Callable

from .ledger import Ledger

_answers: dict[tuple[str, str], tuple[str, bytes]] = {}
SKIP = {".pio", "node_modules", "__pycache__", ".git"}


def _fingerprint(ledger: Ledger) -> str:
    digest = hashlib.sha256(ledger.path.read_bytes() if ledger.path.exists() else b"")
    for folder in ("firmware", "parts"):
        for path, dirs, files in os.walk(ledger.root / folder):
            dirs[:] = sorted(d for d in dirs if d not in SKIP)
            for name in sorted(files):
                info = os.stat(os.path.join(path, name))
                digest.update(f"{path}/{name}:{info.st_mtime_ns}:{info.st_size}".encode())
    return digest.hexdigest()


def remembered(ledger: Ledger, question: str, work: Callable[[], Any]) -> Any:
    """Each caller gets its own copy, so nobody can change what the next caller sees."""
    key, print_now = (str(ledger.root), question), _fingerprint(ledger)
    known = _answers.get(key)
    if known and known[0] == print_now:
        return pickle.loads(known[1])
    answer = work()
    _answers[key] = (print_now, pickle.dumps(answer))
    return answer
