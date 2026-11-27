"""Electrical department routes."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import circuits, compat, datasheets, debugging, designneeds, devicefacts, documents, firmware, flashing, links, page_memory, software
from .gateway import Gateway
from .ledger import Actor, Ledger, ProjectState

MAX_PDF_BYTES = 40 * 1024 * 1024


class ComponentIn(BaseModel):
    part_number: str
    role: str = ""


class Member(BaseModel):
    component: str
    pin: str
    role: Literal["power_in", "power_out", "gnd", "input", "output", "io", "pullup"]
    current_ma: float | None = None
    address: str | None = None
    is_controller: bool = False


class NetIn(BaseModel):
    name: str
    kind: Literal["power", "ground", "signal", "i2c"]
    nominal_v: float | None = None
    members: list[Member]


class DesignOut(BaseModel):
    nets: list[NetIn] = Field(description="The complete connection list")
    notes: list[str] = Field(default_factory=list, description="Anything missing that a person must supply")


class DebugIn(BaseModel):
    title: str
    error: str
    domain: str
    device: str | None = None
    where: str = ""


class TextIn(BaseModel):
    text: str


class MoveIn(BaseModel):
    to: str
    reason: str


class PathIn(BaseModel):
    path: str


class FirmwareIn(BaseModel):
    device: str
    board: str = ""
    goal: str
    computer: bool = False


def _safe_name(device: str) -> str:
    if not re.fullmatch(r"[a-z0-9-]{1,40}", device):
        raise HTTPException(400, "Device names use lowercase letters, numbers and hyphens.")
    return device


def net_problems(design: DesignOut, known: set[str]) -> list[str]:
    problems = []
    names = [n.name for n in design.nets]
    if len(names) != len(set(names)):
        problems.append("Net names must be unique.")
    for net in design.nets:
        if net.kind in ("power", "signal", "i2c") and net.nominal_v is None:
            problems.append(f"{net.name} needs a voltage.")
        for member in net.members:
            if member.component not in known:
                problems.append(f"{net.name} uses unknown component {member.component}.")
    return problems


def note_view(ledger: Ledger, state: ProjectState) -> list[dict[str, Any]]:
    current = {n["id"] for n in page_memory.current_notes(state)}
    used: dict[str, list[str]] = {}
    for device in firmware.devices(ledger.root):
        for file in firmware.scan_workspace(ledger.root, device):
            for block in file["blocks"]:
                for item in block.get("based_on", []):
                    if item.startswith("note:"):
                        used.setdefault(item[5:], []).append(f"{device}/{file['path']}: {block.get('title')}")
    return [{**n["fields"], "id": n["id"], "current": n["id"] in current, "usedBy": used.get(n["id"], []), "source": page_memory.source_of(state, n)[0], "confidence": page_memory.source_of(state, n)[1]} for n in state.of_type("page_note")]


def view(ledger: Ledger, state: ProjectState) -> dict[str, Any]:
    verdicts = devicefacts.review(ledger, state)
    return {
        "pageNotes": note_view(ledger, state),
        "components": state.of_type("component"),
        "datasheets": state.of_type("datasheet"),
        "documents": {c["id"]: documents.status(ledger, state, c) for c in state.of_type("component") if c["status"] != "rejected"},
        "docKinds": documents.DOC_KINDS,
        "assumptions": [{**a, "checked": documents.checked(ledger).get(a["id"])} for a in documents.assumptions(ledger, state)],
        "deviceFacts": [{**f, "fields": {**f["fields"], "constant": devicefacts.constant(state, f)}, "review": verdicts.get(f["id"], {"verdict": "replaced"})} for f in state.of_type("device_fact") if f["status"] not in ("rejected", "superseded")],
        "needs": designneeds.view(ledger),
        "disputes": designneeds.disputes(ledger),
        "noneNeeded": designneeds.declared_none(ledger, "Electrical"),
        "parameters": state.of_type("parameter"),
        "nets": [n for n in state.of_type("net") if n["status"] in ("proposed", "confirmed")],  # the current list, not every earlier draft
        "checks": compat.run_checks(state),
        "powerTree": compat.power_tree(state),
        "debug": list(debugging.sessions(ledger).values()),
        "spent": round(state.spent_usd, 6),
        "limit": state.limit_usd,
    }


async def design_connections(led: Ledger, gateway: Gateway) -> dict[str, Any]:
    state = led.state()
    parts = [{"id": c["id"], "part_number": c["fields"]["part_number"], "role": c["fields"]["role"]} for c in state.of_type("component", "confirmed")]
    if not parts:
        return {"created": 0, "problems": ["Ask the person to confirm the components first."], "notes": []}
    known = {p["id"] for p in parts}
    feedback = ""
    for _attempt in range(2):
        output: DesignOut = await gateway.run(agent="electrical_design", values={"aim": state.meta.get("aim"), "scope": state.meta.get("scope"), "components": parts, "limits": firmware.limits_text(state), "feedback": feedback}, output_type=DesignOut)
        problems = net_problems(output, known)
        if not problems:
            break
        feedback = "\n".join(problems)
    else:
        return {"created": 0, "problems": problems, "notes": []}
    for old in state.of_type("net"):  # A new draft replaces the whole list, and a person confirms it again.
        if old["status"] in ("proposed", "confirmed"):
            led.append(Actor.system("electrical"), "record.superseded", id=old["id"])
    for net in output.nets:
        led.propose(Actor.agent("electrical"), "net", **net.model_dump())
    return {"created": len(output.nets), "problems": [], "notes": output.notes}


def register(app: FastAPI, get_ledger: Callable[[str], Ledger], get_gateway: Callable[[Ledger], Gateway], guarded: Callable[..., Any], backups: Path) -> None:
    prefix = "/api/projects/{project_id}/electrical"

    @app.post(prefix + "/needs/{need_id}/waive")
    def need_waive(project_id: str, need_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        designneeds.waive(led, need_id, str(body.get("reason", "")), Actor.human())
        return view(led, led.state())

    @app.post(prefix + "/needs/none")
    def need_none(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        designneeds.mark_none(led, str(body.get("department", "Electrical")), str(body.get("reason", "")), Actor.human())
        return view(led, led.state())

    @app.post(prefix + "/components/{component_id}/no-datasheet")
    def component_no_datasheet(project_id: str, component_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        designneeds.mark_no_datasheet(led, component_id, str(body.get("reason", "")), Actor.human())
        return view(led, led.state())

    @app.post(prefix + "/warnings/accept")
    def warning_accept(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        designneeds.accept_warning(led, str(body.get("message", "")), str(body.get("reason", "")), Actor.human())
        return view(led, led.state())

    @app.post(prefix + "/components/confirm-all")
    def components_confirm_all(project_id: str) -> dict[str, Any]:
        led = get_ledger(project_id)
        designneeds.confirm_all(led, Actor.human())
        return view(led, led.state())

    @app.post(prefix + "/components/{component_id}/owned")
    def component_owned(project_id: str, component_id: str) -> dict[str, Any]:
        led = get_ledger(project_id)
        designneeds.mark_owned(led, component_id, Actor.human())
        return view(led, led.state())

    @app.get(prefix)
    def overview(project_id: str) -> dict[str, Any]:
        led = get_ledger(project_id)
        return view(led, led.state())

    @app.post(prefix + "/components")
    def add_component(project_id: str, body: ComponentIn) -> dict[str, Any]:
        if not body.part_number.strip():
            raise HTTPException(400, "Enter a part number.")
        led = get_ledger(project_id)
        led.propose(Actor.human(), "component", part_number=body.part_number.strip(), role=body.role.strip())
        return view(led, led.state())

    def component(led: Ledger, component_id: str) -> dict[str, Any]:
        record = led.state().records.get(component_id)
        if not record or record["type"] != "component":
            raise HTTPException(404, "Component not found.")
        return record

    def latest_datasheet(state: ProjectState, component_id: str) -> dict[str, Any] | None:
        found = [d for d in state.of_type("datasheet") if d["fields"]["component"] == component_id and documents.doc_kind(d) == "datasheet"]
        return found[-1] if found else None

    @app.post(prefix + "/components/{component_id}/document-missing")
    def document_missing(project_id: str, component_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        component(led, component_id)
        doc, reason = str(body.get("doc", "")), str(body.get("reason", "")).strip()
        if doc not in documents.DOC_KINDS or len(reason) < 4:
            raise HTTPException(400, "Choose the document, and say why it doesn't exist or can't be found.")
        led.append(Actor.human(), "component.doc_missing", id=component_id, doc=doc, reason=reason[:300])
        return view(led, led.state())

    @app.post(prefix + "/components/{component_id}/variant")
    def component_variant(project_id: str, component_id: str, body: dict[str, str] = Body(...)) -> dict[str, Any]:
        """The person says which variant they own, such as the gear ratio and voltage, so facts come from its rows and pages."""
        led = get_ledger(project_id)
        component(led, component_id)
        text = (body.get("text") or "").strip()
        if len(text) < 3:
            raise HTTPException(400, "Say which variant, such as 150:1, 12 V, with encoder.")
        led.append(Actor.human(), "component.variant", id=component_id, text=text[:300])
        return view(led, led.state())

    @app.post(prefix + "/components/{component_id}/facts")
    async def read_facts(project_id: str, component_id: str) -> dict[str, Any]:
        led = get_ledger(project_id)
        component(led, component_id)
        if not any(cid == component_id for (cid, _kind) in documents.latest_docs(led.state())):
            raise HTTPException(400, "Upload this part's documents first.")
        result = await guarded(project_id, devicefacts.extract(led, get_gateway(led), component_id))
        return {"result": result, "view": view(led, led.state())}

    @app.get(prefix + "/facts/{fact_id}/trace")
    def fact_trace(project_id: str, fact_id: str) -> dict[str, Any]:
        led = get_ledger(project_id)
        record = led.state().records.get(fact_id)
        if not record or record["type"] != "device_fact":
            raise HTTPException(404, "Fact not found.")
        return devicefacts.trace(led, led.state(), fact_id)

    @app.post(prefix + "/assumptions/{assumption_id}/checked")
    def assumption_checked(project_id: str, assumption_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        state = led.state()
        if assumption_id not in {a["id"] for a in documents.assumptions(led, state)}:
            raise HTTPException(404, "That assumption isn't current.")
        note = str(body.get("note", "")).strip()
        if len(note) < 4:
            raise HTTPException(400, "Say what you saw on the device.")
        led.append(Actor.human(), "assumption.checked", id=assumption_id, note=note[:300])
        return view(led, led.state())

    @app.post(prefix + "/components/{component_id}/datasheet")
    async def upload(project_id: str, component_id: str, filename: str, request: Request, doc: str = "datasheet") -> dict[str, Any]:
        led = get_ledger(project_id)
        record = component(led, component_id)
        if doc not in documents.DOC_KINDS:
            raise HTTPException(400, "Unknown kind of document.")
        data = await request.body()
        if len(data) > MAX_PDF_BYTES:
            raise HTTPException(413, "That PDF is too large.")
        if not data.startswith(b"%PDF-"):
            raise HTTPException(400, "That file isn't a PDF.")
        try:
            datasheets.save_pdf(led, component_id, data, filename, doc, Actor.human())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return view(led, led.state())

    @app.get(prefix + "/datasheets/{datasheet_id}/file")
    def datasheet_file(project_id: str, datasheet_id: str) -> FileResponse:
        led = get_ledger(project_id)
        record = led.state().records.get(datasheet_id)
        if not record or record["type"] != "datasheet":
            raise HTTPException(404, "Datasheet not found.")
        return FileResponse(led.root / "datasheets" / record["fields"]["file"], media_type="application/pdf")

    @app.post(prefix + "/components/{component_id}/extract")
    async def extract(project_id: str, component_id: str, override: bool = False) -> dict[str, Any]:
        led = get_ledger(project_id)
        component(led, component_id)
        state = led.state()
        sheet = latest_datasheet(state, component_id)
        if sheet is None:
            raise HTTPException(400, "Upload this component's datasheet first.")
        if not sheet["fields"]["part_match"] and not override:
            raise HTTPException(409, "This PDF doesn't mention the part number. Check that it's the right datasheet.")
        for old in state.of_type("parameter"):
            if old["fields"]["component"] == component_id and old["status"] == "proposed":
                led.append(Actor.human(), "record.rejected", id=old["id"])
        result = await guarded(project_id, datasheets.extract_component(led, get_gateway(led), component_id, sheet, led.root / "datasheets" / sheet["fields"]["file"]))
        return {"result": result, "view": view(led, led.state())}

    @app.post(prefix + "/design")
    async def design(project_id: str) -> dict[str, Any]:
        led = get_ledger(project_id)
        if not led.state().of_type("component", "confirmed"):
            raise HTTPException(400, "Confirm at least one component first.")
        result = await guarded(project_id, design_connections(led, get_gateway(led)))
        return {"result": result, "view": view(led, led.state())}

    @app.get(prefix + "/circuits")
    def circuits_view(project_id: str) -> dict[str, Any]:
        return circuits.build(get_ledger(project_id))

    @app.post(prefix + "/circuits/add")
    def circuits_add(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        circuits.add_group(led, str(body.get("name", "")), Actor.human())
        return circuits.build(led)

    @app.post(prefix + "/circuits/assign")
    def circuits_assign(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        circuits.assign(led, str(body.get("component", "")), str(body.get("group", "")), Actor.human())
        return circuits.build(led)

    @app.post(prefix + "/nets")
    def add_net(project_id: str, body: NetIn) -> dict[str, Any]:
        led = get_ledger(project_id)
        known = {c["id"] for c in led.state().of_type("component")}
        problems = net_problems(DesignOut(nets=[body]), known)
        if problems:
            raise HTTPException(400, " ".join(problems))
        led.propose(Actor.human(), "net", **body.model_dump())
        return view(led, led.state())

    # ---- Debug sessions ----

    @app.post(prefix + "/debug")
    def debug_open(project_id: str, body: DebugIn) -> dict[str, Any]:
        led = get_ledger(project_id)
        debugging.open_or_repeat(led, body.title, body.error, body.domain, Actor.human(), body.device, body.where)
        return view(led, led.state())

    @app.post(prefix + "/debug/{session_id}/hypothesis")
    async def debug_hypothesis(project_id: str, session_id: str) -> dict[str, Any]:
        led = get_ledger(project_id)
        state = led.state()
        tools = page_memory.PageSession(led, led.root, max_pages=6).tools()
        await guarded(project_id, debugging.propose_hypothesis(led, get_gateway(led), session_id, firmware.limits_text(state), tools, page_memory.notes_text(state)))
        return view(led, led.state())

    @app.post(prefix + "/debug/{session_id}/measurement")
    def debug_measure(project_id: str, session_id: str, body: TextIn) -> dict[str, Any]:
        led = get_ledger(project_id)
        debugging.add_measurement(led, session_id, body.text, Actor.human())
        return view(led, led.state())

    @app.post(prefix + "/debug/{session_id}/repro")
    def debug_repro(project_id: str, session_id: str) -> dict[str, Any]:
        led = get_ledger(project_id)
        debugging.mark_repro(led, session_id, Actor.human())
        return view(led, led.state())

    @app.post(prefix + "/debug/{session_id}/move")
    def debug_move(project_id: str, session_id: str, body: MoveIn) -> dict[str, Any]:
        led = get_ledger(project_id)
        debugging.move(led, session_id, body.to, body.reason, Actor.human())
        return view(led, led.state())

    @app.post(prefix + "/debug/{session_id}/close")
    def debug_close(project_id: str, session_id: str, body: TextIn) -> dict[str, Any]:
        led = get_ledger(project_id)
        debugging.close(led, session_id, body.text, Actor.human())
        return view(led, led.state())

    # ---- Firmware ----

    @app.post(prefix + "/firmware")
    async def firmware_generate(project_id: str, body: FirmwareIn) -> dict[str, Any]:
        led = get_ledger(project_id)
        device = _safe_name(body.device)
        if body.computer or firmware.is_computer(led.root, device):
            result = await guarded(project_id, software.generate(led, get_gateway(led), led.root, device, body.goal))
        else:
            try:
                firmware.resolve_board(led.root, device, body.board)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            result = await guarded(project_id, firmware.generate(led, get_gateway(led), led.root, device, body.board, body.goal))
        return {"result": result, "files": firmware.scan_workspace(led.root, device, firmware.valid_note_ids(led.state()), firmware.component_names(led.state()))}

    @app.get(prefix + "/firmware")
    def firmware_devices(project_id: str) -> dict[str, Any]:
        root = get_ledger(project_id).root
        found = firmware.devices(root)
        return {"devices": found, "computers": [d for d in found if firmware.is_computer(root, d)]}

    # ---- Boards and flashing: routes for people only. No agent tool reaches these. ----

    @app.get("/api/boards")
    async def boards(q: str = "") -> list[dict[str, Any]]:
        await asyncio.to_thread(firmware.board_catalog)
        return firmware.search_boards(q)

    @app.get("/api/ports")
    def serial_ports() -> list[dict[str, Any]]:
        return flashing.ports()

    @app.post(prefix + "/firmware/{device}/board")
    async def set_board(project_id: str, device: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        try:
            await asyncio.to_thread(firmware.write_platformio, led.root, _safe_name(device), str(body.get("board", "")))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"config": firmware.device_config(led.root, device)}

    @app.get(prefix + "/firmware/{device}/flash")
    async def flash_status(project_id: str, device: str) -> dict[str, Any]:
        led = get_ledger(project_id)
        config = firmware.device_config(led.root, _safe_name(device))
        await asyncio.to_thread(firmware.board_catalog)
        return {"config": config, "boardInfo": firmware.find_board(config["board"]) if config else None, "backups": flashing.list_backups(backups), "history": flashing.history(led, device)}

    @app.post(prefix + "/firmware/{device}/flash/prepare")
    async def flash_prepare(project_id: str, device: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        return await asyncio.to_thread(flashing.prepare, led, led.root, _safe_name(device), str(body.get("port", "")))

    @app.post(prefix + "/firmware/{device}/flash/{flash_id}/approve")
    async def flash_approve(project_id: str, device: str, flash_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        return await asyncio.to_thread(flashing.approve, led, led.root, backups, _safe_name(device), flash_id, body.get("confirmed") is True, body.get("accept_no_backup") is True, Actor.human())

    @app.post(prefix + "/backups/restore")
    async def backup_restore(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        return await asyncio.to_thread(flashing.restore, led, backups, str(body.get("file", "")), str(body.get("port", "")), body.get("confirmed") is True, Actor.human())

    @app.post(prefix + "/firmware/{device}/tests")
    async def firmware_tests(project_id: str, device: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        result = await asyncio.to_thread(software.run_tests, led, led.root, _safe_name(device), body.get("confirmed") is True, Actor.human())
        return {"result": result, "view": view(led, led.state())}

    @app.post(prefix + "/firmware/{device}/autotest")
    def firmware_autotest(project_id: str, device: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """The person lets the app run this program's tests after each agent write, or stops it."""
        led = get_ledger(project_id)
        try:
            software.allow_autotest(led, _safe_name(device), body.get("allowed") is True, Actor.human())
        except software.SoftwareError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"allowed": software.autotest_allowed(led, _safe_name(device))}

    @app.get(prefix + "/firmware/{device}/setup")
    def firmware_setup(project_id: str, device: str) -> dict[str, Any]:
        led = get_ledger(project_id)
        return {**software.setup_view(led.events(), _safe_name(device)), "linkIssues": links.open_for_device(led, device), "autotest": software.autotest_allowed(led, _safe_name(device))}

    @app.post(prefix + "/firmware/{device}/setup/{index}")
    def firmware_setup_step(project_id: str, device: str, index: int, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        software.mark_step(led, _safe_name(device), index, "worked" if body.get("worked") else "failed", Actor.human())
        return {**software.setup_view(led.events(), device), "linkIssues": links.open_for_device(led, device)}

    def workspace_file(led: Ledger, device: str, path: str) -> None:
        if not (firmware.SAFE_PATH.match(path) or firmware.HOST_PATH.match(path)) or ".." in path or not (led.root / "firmware" / device / path).is_file():
            raise HTTPException(404, "File not found.")

    @app.post(prefix + "/firmware/{device}/describe")
    async def firmware_describe(project_id: str, device: str, body: PathIn) -> dict[str, Any]:
        led = get_ledger(project_id)
        device = _safe_name(device)
        workspace_file(led, device, body.path)
        current = next((f for f in firmware.scan_workspace(led.root, device) if f["path"] == body.path), None)
        if current is None or current["blocks"]:
            raise HTTPException(409, "This file already has descriptions.")
        await guarded(project_id, firmware.describe_file(led, get_gateway(led), led.root, device, body.path))
        return {"files": firmware.scan_workspace(led.root, device, firmware.valid_note_ids(led.state()), firmware.component_names(led.state()))}

    @app.post(prefix + "/firmware/{device}/build")
    async def firmware_build(project_id: str, device: str) -> dict[str, Any]:
        led = get_ledger(project_id)
        device = _safe_name(device)
        if device not in firmware.devices(led.root):
            raise HTTPException(404, "Device not found.")
        result = await asyncio.to_thread(firmware.build_and_track, led, led.root, device)
        return {"build": result, "view": view(led, led.state())}

    @app.get(prefix + "/firmware/{device}")
    def firmware_files(project_id: str, device: str) -> dict[str, Any]:
        led = get_ledger(project_id)
        return {"files": firmware.scan_workspace(led.root, _safe_name(device), firmware.valid_note_ids(led.state()), firmware.component_names(led.state()))}
