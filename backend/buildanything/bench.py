"""The laptop test bench: a device's real firmware, compiled for the laptop and run against fake parts.

The fakes are written from each part's documents by an agent that never sees the firmware, and the
scenarios from the requirements. Time is simulated from what the firmware does, so slow drawing or a late
response shows up here before anything is flashed. What it can't catch: wiring, electrical faults, and
facts that are wrong in the documents themselves. Those stay with the facts check and the real device.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

RUNTIME = Path(__file__).parent / "bench_runtime"
SKIP_DIRS = {"examples", "example", "test", "tests", "extras", "docs", "doc", ".git", "fontconvert"}
BUILT_IN = {"SPI", "Wire", "Servo"}  # The bench supplies these itself.
WALL_SECONDS = 90
SOURCE = (".c", ".cpp", ".cc")

# Each board family's timing and quirks. Rough, but in the right direction: a slow chip is slow here too.
FAMILIES: dict[str, dict[str, int]] = {
    "ststm32": {"BENCH_STM32_PORTS": 1, "BENCH_SPI_MAX_HZ": 42000000, "BENCH_SPI_BASE_HZ": 84000000, "BENCH_SPI_BYTE_OVERHEAD_NS": 1000, "BENCH_LOOP_NS": 1000, "BENCH_PIN_WRITE_NS": 600, "BENCH_SERIAL_BUFFER": 64},
    "espressif32": {"BENCH_STM32_PORTS": 0, "BENCH_SPI_MAX_HZ": 80000000, "BENCH_SPI_BASE_HZ": 0, "BENCH_SPI_BYTE_OVERHEAD_NS": 600, "BENCH_LOOP_NS": 400, "BENCH_PIN_WRITE_NS": 150, "BENCH_SERIAL_BUFFER": 256},
    "atmelavr": {"BENCH_STM32_PORTS": 0, "BENCH_SPI_MAX_HZ": 8000000, "BENCH_SPI_BASE_HZ": 16000000, "BENCH_SPI_BYTE_OVERHEAD_NS": 2000, "BENCH_LOOP_NS": 4000, "BENCH_PIN_WRITE_NS": 4000, "BENCH_SERIAL_BUFFER": 64},
}
# Arduino header names on Nucleo-64 boards, so D13 and PA5 are the same pin here as on the board.
NUCLEO64 = {"D0": "PA3", "D1": "PA2", "D2": "PA10", "D3": "PB3", "D4": "PB5", "D5": "PB4", "D6": "PB10", "D7": "PA8", "D8": "PA9", "D9": "PC7", "D10": "PB6", "D11": "PA7", "D12": "PA6",
            "D13": "PA5", "D14": "PB9", "D15": "PB8", "A0": "PA0", "A1": "PA1", "A2": "PA4", "A3": "PB0", "A4": "PC1", "A5": "PC0", "LED_BUILTIN": "PA5", "USER_BTN": "PC13"}


def bench_dir(root: Path, device: str) -> Path:
    return root / "bench" / device


def _ini(base: Path) -> dict[str, str]:
    path = base / "platformio.ini"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    return {k.strip(): v.strip() for k, v in re.findall(r"^\s*(platform|board)\s*=\s*(\S+)", text, re.M)}


def board_header(base: Path) -> str:
    ini = _ini(base)
    family = FAMILIES.get(ini.get("platform", ""), FAMILIES["ststm32"])
    lines = ["/* Written by the bench from platformio.ini. */", "#pragma once"]
    lines += [f"#define {k} {v}UL" for k, v in family.items()]
    root, device = base.parent.parent, base.name
    lines.append(f"#define BENCH_SCREEN_TURN {SCREEN_TURNS.get(settings(root, device).get('screen', 'none'), 0)}  /* set by a person to match the real screen */")
    if ini.get("board", "").startswith("nucleo_"):
        lines += [f"#define {k} {v}" for k, v in NUCLEO64.items()]
    return "\n".join(lines) + "\n"


def _sources(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.suffix in SOURCE and p.is_file() and not (set(q.name.lower() for q in p.relative_to(folder).parents) & SKIP_DIRS))


def libraries(base: Path) -> list[Path]:
    """Libraries the firmware uses: its own lib/ folders and what PlatformIO downloaded."""
    found = [p for p in sorted((base / "lib").glob("*")) if p.is_dir()]
    for env in sorted((base / ".pio" / "libdeps").glob("*")):
        found += [p for p in sorted(env.glob("*")) if p.is_dir() and p.name not in BUILT_IN]
    return found


def _hash(*parts: bytes | str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else p.encode())
    return h.hexdigest()[:20]


def _headers_hash(*folders: Path) -> str:
    h = hashlib.sha256()
    for folder in folders:
        if folder.exists():
            for p in sorted(folder.rglob("*.h*")):
                if p.is_file() and ".pio" not in p.parts:
                    h.update(p.as_posix().encode())
                    h.update(p.read_bytes())
    return h.hexdigest()[:20]


def runtime_hash() -> str:
    return _hash(*(p.read_bytes() for p in sorted(RUNTIME.rglob("*")) if p.is_file()))


def fingerprint(root: Path, device: str) -> str:
    """Changes to the firmware, the fakes, the scenarios or the bench itself make old results stale."""
    from . import flashing

    folder = bench_dir(root, device)
    bench_files = [p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file() and "build" not in p.relative_to(folder).parts and "results" not in p.relative_to(folder).parts]
    return _hash(flashing.source_hash(root / "firmware" / device), runtime_hash(), *bench_files)


def version(root: Path, device: str) -> str:
    """The fakes and scenarios as they stand. Accepting the bench is for this version only."""
    folder = bench_dir(root, device)
    files = sorted([*(folder / "fakes").glob("*.h"), folder / "scenarios.cpp"]) if folder.exists() else []
    return _hash(*(p.read_bytes() for p in files if p.exists()))


SCREEN_TURNS = {"none": 0, "rotate_180": 1, "mirror_x": 2, "mirror_y": 3}


def settings(root: Path, device: str) -> dict[str, Any]:
    path = bench_dir(root, device) / "settings.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"screen": "none"}


def set_screen(root: Path, device: str, turn: str) -> None:
    if turn not in SCREEN_TURNS:
        raise ValueError(turn)
    folder = bench_dir(root, device)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "settings.json").write_text(json.dumps({**settings(root, device), "screen": turn}), encoding="utf-8")


def ready(root: Path, device: str) -> bool:
    return (bench_dir(root, device) / "scenarios.cpp").exists()


def compiler() -> str | None:
    return shutil.which("g++")


ERROR = re.compile(r"^(.*?):(\d+)(?::\d+)?:\s*(?:fatal )?error:\s*(.*)$", re.M)


def _errors(output: str, base: Path) -> list[dict[str, str]]:
    out = []
    for path, line, text in ERROR.findall(output):
        p = path.replace("\\", "/")
        where = "bench" if "/bench/" in p and "/bench_runtime/" not in p else "runtime" if "/bench_runtime/" in p else "library" if "/.pio/" in p or "/lib/" in p else "firmware"
        try:
            short = Path(path).resolve().relative_to(base.resolve().parent.parent).as_posix()
        except ValueError:
            short = Path(path).name
        out.append({"where": where, "text": f"{short}:{line}: {text.strip()}"[:300]})
    return out


def _compile_one(source: Path, obj: Path, includes: list[Path], defines: list[str]) -> tuple[Path, str | None]:
    if obj.exists():
        return obj, None
    is_c = source.suffix == ".c"
    cmd = ["gcc" if is_c else "g++", "-c", str(source), "-o", str(obj), "-O1", "-w", "-DARDUINO=10819", "-DBENCH=1", *defines, *[f"-I{p}" for p in includes]]
    cmd += ["-std=gnu11"] if is_c else ["-std=gnu++17", "-fpermissive"]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if done.returncode != 0:
        obj.unlink(missing_ok=True)
        return obj, (done.stdout + done.stderr)
    return obj, None


def write_fakes_header(root: Path, device: str) -> None:
    folder = bench_dir(root, device)
    fakes = sorted((folder / "fakes").glob("*.h"))
    text = "/* Written by the bench: every fake part on this device. */\n#pragma once\n#include \"bench.h\"\n" + "".join(f'#include "fakes/{p.name}"\n' for p in fakes)
    (folder / "fakes.h").write_text(text, encoding="utf-8", newline="\n")


def build(root: Path, device: str, extra: list[Path] | None = None) -> dict[str, Any]:
    """Compiles the firmware, its libraries, the fakes and the scenarios into one laptop program."""
    gpp = compiler()
    if not gpp:
        return {"ok": False, "ran": False, "errors": [{"where": "bench", "text": "No laptop C++ compiler (g++) was found."}]}
    base = root / "firmware" / device
    folder = bench_dir(root, device)
    build_dir = folder / "build"
    (build_dir / "obj").mkdir(parents=True, exist_ok=True)
    (build_dir / "board").mkdir(parents=True, exist_ok=True)
    (build_dir / "board" / "bench_board.h").write_text(board_header(base), encoding="utf-8", newline="\n")
    write_fakes_header(root, device)

    libs = libraries(base)
    lib_includes = []
    for lib in libs:
        lib_includes += [lib] + ([lib / "src"] if (lib / "src").is_dir() else [])
    includes = [build_dir / "board", RUNTIME, base / "include", base / "src", folder, *lib_includes]
    project_headers = _headers_hash(base / "include", base / "src", base / "lib", folder, RUNTIME, build_dir / "board")
    jobs: list[tuple[Path, Path, str]] = []
    for src in [*_sources(base / "src"), *(extra or []), *([folder / "scenarios.cpp"] if (folder / "scenarios.cpp").exists() else []), RUNTIME / "bench.cpp"]:
        jobs.append((src, build_dir / "obj" / f"{_hash(src.as_posix(), src.read_bytes(), project_headers)}.o", "own"))
    lib_jobs = []
    for lib in libs:
        lib_hash = _headers_hash(lib, RUNTIME, build_dir / "board")
        for src in _sources(lib):
            lib_jobs.append((src, build_dir / "obj" / f"{_hash(src.as_posix(), src.read_bytes(), lib_hash)}.o", lib.name))

    errors: list[dict[str, str]] = []
    skipped_libs: dict[str, str] = {}
    objects: list[Path] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        own = list(pool.map(lambda j: (_compile_one(j[0], j[1], includes, []), j), jobs))
        theirs = list(pool.map(lambda j: (_compile_one(j[0], j[1], includes, []), j), lib_jobs))
    for (obj, failure), (src, _o, _k) in own:
        if failure:
            errors += _errors(failure, base) or [{"where": "firmware", "text": failure.strip().splitlines()[-1][:300] if failure.strip() else f"{src.name} didn't compile."}]
        else:
            objects.append(obj)
    lib_objects = []
    for (obj, failure), (src, _o, name) in theirs:
        if failure:
            skipped_libs.setdefault(name, (_errors(failure, base) or [{"text": failure[-200:]}])[0]["text"])
        else:
            lib_objects.append(obj)
    if errors:
        return {"ok": False, "ran": True, "stage": "compile", "errors": errors[:12]}

    exe = build_dir / "bench.exe"
    archive = build_dir / "libs.a"
    archive.unlink(missing_ok=True)
    if lib_objects:
        subprocess.run(["ar", "rcs", str(archive), *map(str, lib_objects)], capture_output=True, timeout=120)
    link = subprocess.run([gpp, "-o", str(exe), *map(str, objects), *([str(archive)] if lib_objects else []), "-static"], capture_output=True, text=True, timeout=300)
    if link.returncode != 0:
        missing = sorted(set(re.findall(r"undefined reference to `([^']+)'", link.stdout + link.stderr)))[:8]
        detail = [{"where": "runtime", "text": f"The bench doesn't provide {name} yet."} for name in missing] or [{"where": "bench", "text": (link.stdout + link.stderr).strip()[-300:]}]
        detail += [{"where": "library", "text": f"Library {k} couldn't be built for the laptop: {v}"} for k, v in list(skipped_libs.items())[:3]]
        return {"ok": False, "ran": True, "stage": "link", "errors": detail}
    return {"ok": True, "ran": True, "exe": str(exe), "skipped_libraries": skipped_libs}


def _png(ppm: Path) -> str:
    try:
        from PIL import Image

        png = ppm.with_suffix(".png")
        Image.open(ppm).resize((Image.open(ppm).width * 3, Image.open(ppm).height * 3), Image.NEAREST).save(png)
        ppm.unlink(missing_ok=True)
        return png.name
    except Exception:  # noqa: BLE001 - a missing picture never fails a run
        return ppm.name


def _run_one(exe: str, index: int, out: Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*"):
        old.unlink()
    try:
        done = subprocess.run([exe, "run", str(index), str(out)], capture_output=True, text=True, timeout=WALL_SECONDS)
    except subprocess.TimeoutExpired as e:
        last = (e.stderr or b"").decode(errors="replace").strip().splitlines()[-1:] if isinstance(e.stderr, bytes) else []
        return {"index": index, "error": f"The firmware stopped responding: it got stuck in a loop that never waits or returns{' after ' + last[0] if last else ''}.", "checks": [], "problems": [], "notes": [], "measures": [], "screens": []}
    lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
    if not lines:
        last = done.stderr.strip().splitlines()[-1:] if done.stderr.strip() else []
        return {"index": index, "error": f"The firmware crashed (exit code {done.returncode}){' after ' + last[0] if last else ''}: usually a memory error such as writing past the end of an array.", "checks": [], "problems": [], "notes": [], "measures": [], "screens": []}
    result = json.loads(lines[-1])
    result["index"] = index
    result["screens"] = [_png(out / s) for s in result.get("screens", [])]
    return result


def run(root: Path, device: str) -> dict[str, Any]:
    """Builds and runs every scenario. Each runs in its own process, from power-on."""
    built = build(root, device)
    if not built["ok"]:
        return {**built, "scenarios": []}
    exe = built["exe"]
    listed = json.loads(subprocess.run([exe, "list"], capture_output=True, text=True, timeout=30).stdout or "[]")
    results_dir = bench_dir(root, device) / "results"
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: _run_one(exe, i, results_dir / str(i)), range(len(listed))))
    for r, meta in zip(results, listed):
        r.setdefault("title", meta["title"])
        r.setdefault("covers", meta["covers"])
        r["passed"] = not r.get("error") and bool(r.get("checks")) and all(c["ok"] for c in r["checks"]) and not r.get("problems")
    summary = {"ok": bool(results) and all(r["passed"] for r in results), "ran": True, "stage": "run", "scenarios": results, "skipped_libraries": built.get("skipped_libraries", {})}
    (results_dir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


def last_results(root: Path, device: str) -> dict[str, Any] | None:
    path = bench_dir(root, device) / "results" / "summary.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
