"""Runs one file converter the Platform agent wrote: python -I convbox.py <workdir> <converter.py> <input file>

A converter is a Python file with convert(path) -> dict. It turns one file the app has no viewer for into something
a viewer can show, and the dict is printed as JSON for the app. Guard rails, like the simulation sandbox: no network,
no other programs, no secrets or Git files, nothing written outside its own scratch folder, and the only project file
it may open is the one it was given. These are guard rails against honest mistakes, not a security boundary against
code written to escape them; the app also strips the environment (no keys) and kills the run at its time limit.
"""
from __future__ import annotations

import json
import os
import re
import runpy
import sys
from pathlib import Path

SECRET = re.compile(r"(^|[\\/])(\.env[^\\/]*|\.ssh|\.git|\.aws|\.azure|\.config[\\/]gcloud|credentials[^\\/]*|secrets?[^\\/]*|keys?\.json|id_rsa[^\\/]*|.*\.pem|.*\.key)($|[\\/])", re.I)
BLOCKED = ("socket.connect", "socket.bind", "socket.sendto", "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.startfile", "os.kill", "winreg.", "ctypes.dlopen", "webbrowser.open")
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
KINDS = {"markdown", "html", "table", "image", "text"}
MAX_OUT = 6_000_000


def _resolved(path: object) -> Path | None:
    try:
        return Path(os.path.abspath(os.fsdecode(path))).resolve()
    except (TypeError, ValueError, OSError):
        return None


def install(work: Path, source: Path, projects: Path, converter: Path) -> None:
    work, source, projects, converter = work.resolve(), source.resolve(), projects.resolve(), converter.resolve()

    def hook(event: str, args: tuple) -> None:
        if event.startswith(BLOCKED):
            raise PermissionError(f"Converters can't use {event}: they run without network access or other programs.")
        if event == "open" and args and not isinstance(args[0], int):
            path, mode, flags = (list(args) + [None, None, 0])[:3]
            target = _resolved(path)
            if target is None:
                return
            if SECRET.search(str(target)):
                raise PermissionError("Converters can't read secrets or Git files.")
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & WRITE_FLAGS)
            if writing and not target.is_relative_to(work):
                raise PermissionError("Converters can only write in their own scratch folder.")
            if target.is_relative_to(projects) and target not in (source, converter) and not target.is_relative_to(work):
                raise PermissionError("Converters can only read the one file they were given.")
        elif event in ("os.remove", "os.unlink", "os.rmdir", "os.rename", "os.replace", "shutil.rmtree", "shutil.move", "os.mkdir", "os.chmod", "os.truncate"):
            for a in args[:2]:
                if isinstance(a, (str, bytes, os.PathLike)):
                    target = _resolved(a)
                    if target is None or not target.is_relative_to(work):
                        raise PermissionError("Converters can only change files in their own scratch folder.")

    sys.addaudithook(hook)


def check(result: object) -> dict:
    """What a converter returns, so a viewer can show it."""
    if not isinstance(result, dict) or result.get("kind") not in KINDS:
        raise ValueError(f"convert() must return a dict whose kind is one of {sorted(KINDS)}.")
    kind = result["kind"]
    need = {"markdown": "text", "text": "text", "html": "html", "image": "png_base64", "table": "sheets"}[kind]
    if need not in result:
        raise ValueError(f"A {kind} result needs a '{need}' field.")
    return result


def main() -> None:
    work, converter, source, projects = (Path(a) for a in sys.argv[1:5])
    os.chdir(work)
    sys.dont_write_bytecode = True
    try:  # loaded before the guard, since they load their libraries the ordinary way
        import numpy  # noqa: F401
        import PIL.Image  # noqa: F401
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot  # noqa: F401
    except Exception:  # noqa: BLE001 - a converter that needs them will say so
        pass
    install(work, source, projects, converter)
    namespace = runpy.run_path(str(converter), run_name="converter")
    if "convert" not in namespace:
        raise SystemExit("The converter has no convert(path) function.")
    result = check(namespace["convert"](str(source)))
    text = json.dumps(result, ensure_ascii=False)
    if len(text) > MAX_OUT:
        raise SystemExit("The converter's output is too big to show. Summarise or downsample it.")
    sys.stdout.write("\n@@CONVERTED@@" + text)


if __name__ == "__main__":
    main()
