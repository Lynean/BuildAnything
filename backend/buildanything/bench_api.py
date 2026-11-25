"""Routes for the laptop test bench: what's faked, what was tried, and the pictures of each fake screen."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Callable

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse

from . import bench, benchwriter, ci, firmware
from .gateway import Gateway
from .ledger import Actor, Ledger


def view(led: Ledger) -> dict[str, Any]:
    state = led.state()
    devices = []
    for device in firmware.devices(led.root):
        if firmware.is_computer(led.root, device):
            continue
        folder = bench.bench_dir(led.root, device)
        meta = folder / "scenarios.json"
        devices.append({
            "device": device,
            "ready": bench.ready(led.root, device),
            "parts": [{"part_number": p["component"]["fields"]["part_number"], "connections": p["connections"], "built_in": p["built_in"]} for p in benchwriter.parts_for(state, led.root, device)],
            "fakes": benchwriter.fakes(led.root, device),
            "coverage": json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else None,
            "results": bench.last_results(led.root, device),
            "stale": bench.ready(led.root, device) and any(item[0] == f"Bench:{device}" for item in ci.pending(led)),
            "accepted": ci.bench_accepted(led, device),
            "screen": bench.settings(led.root, device).get("screen", "none"),
        })
    return {"compiler": bool(bench.compiler()), "devices": devices}


def register(app: FastAPI, get_ledger: Callable[[str], Ledger], get_gateway: Callable[[Ledger], Gateway], guarded: Callable[..., Any]) -> None:
    @app.get("/api/projects/{project_id}/bench")
    def bench_view(project_id: str) -> dict[str, Any]:
        return view(get_ledger(project_id))

    @app.post("/api/projects/{project_id}/bench/{device}/run")
    async def bench_run(project_id: str, device: str) -> dict[str, Any]:
        """Free: runs the existing fakes and scenarios again."""
        led = get_ledger(project_id)
        if device not in firmware.devices(led.root) or not bench.ready(led.root, device):
            raise HTTPException(404, "That device has no bench yet.")
        result = await asyncio.to_thread(bench.run, led.root, device)
        ci.record(led, f"Bench:{device}", bench.fingerprint(led.root, device), *ci.bench_verdict(led, device, result))
        return view(led)

    @app.post("/api/projects/{project_id}/bench/{device}/build")
    async def bench_build(project_id: str, device: str) -> dict[str, Any]:
        """Costs AI money: writes any missing fakes and the scenarios, then runs them."""
        led = get_ledger(project_id)
        if device not in firmware.devices(led.root):
            raise HTTPException(404, "No such device.")
        result = await guarded(project_id, benchwriter.build_bench(led, get_gateway(led), led.root, device))
        if isinstance(result, dict) and result.get("error"):
            raise HTTPException(400, result["error"])
        return view(led)

    @app.post("/api/projects/{project_id}/bench/{device}/accept")
    def bench_accept(project_id: str, device: str) -> dict[str, Any]:
        """A person has looked at the fakes and pictures: from now on this bench's failures send work to departments."""
        led = get_ledger(project_id)
        if device not in firmware.devices(led.root) or not bench.ready(led.root, device):
            raise HTTPException(404, "That device has no bench yet.")
        led.append(Actor.human(), "bench.accepted", device=device, version=bench.version(led.root, device))
        return view(led)

    @app.post("/api/projects/{project_id}/bench/{device}/screen")
    async def bench_screen_turn(project_id: str, device: str, body: dict[str, str] = Body(...)) -> dict[str, Any]:
        """Turns the fake screen to match the real one, then runs the bench again. Free."""
        led = get_ledger(project_id)
        if device not in firmware.devices(led.root) or body.get("screen") not in bench.SCREEN_TURNS:
            raise HTTPException(400, "Choose none, rotate_180, mirror_x or mirror_y.")
        bench.set_screen(led.root, device, body["screen"])
        led.append(Actor.human(), "bench.screen", device=device, screen=body["screen"])
        if bench.ready(led.root, device):
            result = await asyncio.to_thread(bench.run, led.root, device)
            ci.record(led, f"Bench:{device}", bench.fingerprint(led.root, device), *ci.bench_verdict(led, device, result))
        return view(led)

    @app.get("/api/projects/{project_id}/bench/{device}/screens/{index}/{name}")
    def bench_screen(project_id: str, device: str, index: int, name: str) -> FileResponse:
        led = get_ledger(project_id)
        if device not in firmware.devices(led.root) or not re.fullmatch(r"[A-Za-z0-9_]+\.(png|ppm)", name):
            raise HTTPException(404, "Not found.")
        path = bench.bench_dir(led.root, device) / "results" / str(int(index)) / name
        if not path.exists():
            raise HTTPException(404, "Not found.")
        return FileResponse(path, media_type="image/png" if name.endswith(".png") else "image/x-portable-pixmap")


def glance(led: Ledger) -> dict[str, Any]:
    """Headquarters at a glance: numbers and pictures from each department's real output, each linking to its screen."""
    from . import traceability

    state = led.state()
    of = lambda t: [r for r in state.of_type(t) if r["status"] not in ("rejected", "superseded")]  # noqa: E731
    trace = traceability.view(led)
    findings = of("finding")
    run = max([f["fields"].get("run", 0) for f in findings] or [0])
    current_findings = [f for f in findings if f["fields"].get("run", 0) == run]
    benches = []
    for device in firmware.devices(led.root):
        if firmware.is_computer(led.root, device):
            continue
        res = bench.last_results(led.root, device)
        if not res:
            continue
        scenarios = res.get("scenarios", [])
        shot = next(((s["index"], s["screens"][0]) for s in scenarios if s.get("screens")), None)
        benches.append({"device": device, "passed": sum(1 for s in scenarios if s.get("passed")), "total": len(scenarios), "accepted": ci.bench_accepted(led, device),
                        "picture": f"/projects/{led.root.name}/bench/{device}/screens/{shot[0]}/{shot[1]}" if shot else None})
    return {
        "requirements": {"total": trace["total"], **trace["counts"]},
        "claims": {"total": len(of("claim")), "confirmed": sum(1 for c in of("claim") if c["status"] == "confirmed")},
        "decisions": len(of("decision")),
        "findings": {sev: sum(1 for f in current_findings if f["fields"].get("severity") == sev) for sev in ("high", "medium", "low")},
        "electrical": {"components": len(of("component")), "nets": len(of("net")), "facts": len(of("device_fact"))},
        "mechanical": {"parts": len(of("part")), "summary": None, "ok": None, "kind": None, "picture": None},
        "benches": benches,
        "code": {"devices": [d for d in firmware.devices(led.root)]},
        "changes": sum(1 for e in led.events() if e["kind"] == "change.raised"),
    }


def register_glance(app: FastAPI, get_ledger: Callable[[str], Ledger]) -> None:
    @app.get("/api/projects/{project_id}/glance")
    def project_glance(project_id: str) -> dict[str, Any]:
        return glance(get_ledger(project_id))


def software_overview(led: Ledger, device: str) -> dict[str, Any]:
    """One device at a glance: its code as layers, the checks on it, its bench and what's on the board."""
    from . import flashing

    computer = firmware.is_computer(led.root, device)
    files = firmware.scan_workspace(led.root, device)
    layers: dict[str, list[dict[str, Any]]] = {"app": [], "mid": [], "driver": [], "other": []}
    for f in files:
        path = f["path"]
        layer = next((name for name in ("driver", "mid", "app") if f"/{name}/" in f"/{path}"), "other")
        blocks = f["blocks"]
        layers[layer].append({"path": path, "owner": (blocks[0].get("owner") if blocks else None) or ("Generated" if f.get("generated") else ""),
                              "blocks": len(blocks), "stale": sum(1 for b in blocks if b["state"] != "documented") + (0 if blocks or f.get("generated") else 1), "generated": bool(f.get("generated"))})
    wanted = dict(ci._wanted(led))
    known = ci._results(led)
    checks = []
    for stage in ([f"App:{device}"] if computer else [f"Firmware:{device}", f"Bench:{device}"]) + (["Interface"] if led.state().packets else []):
        key = wanted.get(stage)
        result = known.get((stage, key)) if key else None
        checks.append({"stage": stage.split(":")[0], "state": "none" if key is None else "waiting" if result is None else "not run" if not result.get("ran", True) else "pass" if result["ok"] else "fail",
                       "problems": (result or {}).get("problems", [])[:3]})
    flash = None
    if not computer:
        events = led.events()
        finished = [e for e in events if e["kind"] == "flash.finished" and e["data"].get("device") == device]
        if finished:
            last = finished[-1]
            plan = next((e["data"] for e in events if e["kind"] == "flash.prepared" and e["data"].get("id") == last["data"].get("id")), {})
            current = flashing.source_hash(led.root / "firmware" / device)
            flash = {"at": last["at"], "outcome": last["data"].get("outcome"), "board": plan.get("board"), "chip": plan.get("chip"), "port": plan.get("port"),
                     "size": plan.get("size"), "current": plan.get("source_sha") == current}
    res = None if computer else bench.last_results(led.root, device)
    bench_row = None
    if res:
        scen = res.get("scenarios", [])
        shot = next(((s["index"], s["screens"][0]) for s in scen if s.get("screens")), None)
        bench_row = {"passed": sum(1 for s in scen if s.get("passed")), "total": len(scen), "picture": f"/projects/{led.root.name}/bench/{device}/screens/{shot[0]}/{shot[1]}" if shot else None}
    config = None if computer else firmware.device_config(led.root, device)
    return {"device": device, "computer": computer, "layers": layers, "checks": checks, "flash": flash, "bench": bench_row, "board": (config or {}).get("board")}


def register_software(app: FastAPI, get_ledger: Callable[[str], Ledger]) -> None:
    @app.get("/api/projects/{project_id}/software/{device}/overview")
    def device_overview(project_id: str, device: str) -> dict[str, Any]:
        led = get_ledger(project_id)
        if device not in firmware.devices(led.root):
            raise HTTPException(404, "No such device.")
        return software_overview(led, device)
