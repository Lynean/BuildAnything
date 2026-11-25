"""Mechanical, Procurement and Import routes."""
from __future__ import annotations

import asyncio
import hashlib
import io
import re
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pypdf import PdfReader

from . import gates, geometry, importer, mechanical as mech, procurement as proc
from .gateway import Gateway
from .ledger import Actor, Ledger

MAX_UPLOAD = 60 * 1024 * 1024


class PartIn(BaseModel):
    number: str
    name: str
    material: str = ""
    process: str = ""
    qty: int = 1
    mass_g: float | None = None
    unit_cost: float | None = None


class ReviseIn(BaseModel):
    reason: str
    material: str | None = None
    process: str | None = None
    qty: int | None = None
    mass_g: float | None = None
    unit_cost: float | None = None


class CloseIn(BaseModel):
    part: str
    check: str
    evidence: str


class BudgetIn(BaseModel):
    kind: str
    limit: float
    currency: str = ""


class ReleaseIn(BaseModel):
    recipient: str
    override: str = ""


class ClearanceIn(BaseModel):
    parts: list[str]


class OrderIn(BaseModel):
    supplier: str = ""
    reference: str = ""
    order_date: str = ""
    currency: str = ""
    items: str = ""
    buyer: str = ""
    funding: str = ""
    total: float | None = None
    shipping: float | None = None
    tax: float | None = None
    duties: float | None = None
    status: str = "Ordered"


class StatusIn(BaseModel):
    status: str


class InvoiceIn(BaseModel):
    text: str
    people: list[str] = []


class ScanIn(BaseModel):
    folder: str


class ResumeIn(BaseModel):
    gate: str


class ImportIn(BaseModel):
    folder: str
    files: list[str]
    people: list[str] = []


def view(led: Ledger) -> dict[str, Any]:
    state = led.state()
    by_number = mech.parts_by_number(state)
    parts = []
    for number, revisions in by_number.items():
        parts.append({"number": number, "current": revisions[-1], "superseded": revisions[:-1]})
    return {
        "parts": parts, "checks": list(mech.checks_state(led).values()), "rollup": mech.rollup(led, state), "releases": mech.releases(led),
        "orders": proc.orders_view(state), "imported": state.of_type("imported_item"), "spent": round(state.spent_usd, 6), "limit": state.limit_usd,
    }


def register(app: FastAPI, get_ledger: Callable[[str], Ledger], get_gateway: Callable[[Ledger], Gateway], guarded: Callable[..., Any]) -> None:
    prefix = "/api/projects/{project_id}"

    @app.get(prefix + "/build")
    def overview(project_id: str) -> dict[str, Any]:
        return view(get_ledger(project_id))

    @app.post(prefix + "/parts")
    def add_part(project_id: str, body: PartIn) -> dict[str, Any]:
        led = get_ledger(project_id)
        mech.add_part(led, body.model_dump(), Actor.human())
        return view(led)

    @app.post(prefix + "/parts/{number}/revise")
    def revise(project_id: str, number: str, body: ReviseIn) -> dict[str, Any]:
        led = get_ledger(project_id)
        changes = {k: v for k, v in body.model_dump(exclude={"reason"}).items() if v is not None}
        impact = mech.revise(led, number, changes, body.reason, Actor.human())
        return {"impact": impact, "view": view(led)}

    @app.post(prefix + "/parts/{number}/file")
    async def upload_part(project_id: str, number: str, filename: str, request: Request) -> dict[str, Any]:
        led = get_ledger(project_id)
        revisions = mech.parts_by_number(led.state()).get(number)
        if not revisions:
            raise HTTPException(404, "Part not found.")
        suffix = Path(filename).suffix.lower()
        if suffix not in (".step", ".stp", ".dxf", ".stl", ".pdf"):
            raise HTTPException(400, "Use a STEP, DXF, STL or PDF file.")
        data = await request.body()
        if not data or len(data) > MAX_UPLOAD:
            raise HTTPException(413, "That file is empty or too large.")
        folder = led.root / "parts"
        folder.mkdir(exist_ok=True)
        name = f"{re.sub(r'[^A-Za-z0-9_-]', '_', number)}-{hashlib.sha256(data).hexdigest()[:10]}{suffix}"
        (folder / name).write_bytes(data)
        if revisions[-1]["fields"].get("file"):
            impact = mech.revise(led, number, {"file": name}, f"New file uploaded: {filename[:120]}", Actor.human())
        else:
            led.append(Actor.human(), "record.rejected", id=revisions[-1]["id"])
            fields = {**revisions[-1]["fields"], "file": name}
            led.append(Actor.human(), "record.confirmed", id=led.propose(Actor.human(), "part", **fields))
            impact = None
        result: dict[str, Any] = {"impact": impact, "view": view(led), "measured": None}
        if suffix in (".step", ".stp"):
            try:
                measured = await asyncio.to_thread(geometry.measure, folder / name)
            except Exception as exc:
                raise HTTPException(400, "That STEP file couldn't be read.") from exc
            material = str(revisions[-1]["fields"].get("material", "")).lower()
            density = mech.DENSITY_G_CM3.get(material)
            measured["massG"] = round(measured["volumeCm3"] * density, 3) if density else None
            result["measured"] = measured
        return result

    @app.post(prefix + "/checks/close")
    def close(project_id: str, body: CloseIn) -> dict[str, Any]:
        led = get_ledger(project_id)
        mech.close_check(led, body.part, body.check, body.evidence, Actor.human())
        return view(led)

    @app.post(prefix + "/budget")
    def budget(project_id: str, body: BudgetIn) -> dict[str, Any]:
        led = get_ledger(project_id)
        mech.set_budget(led, body.kind, body.limit, Actor.human(), body.currency)
        return view(led)

    @app.post(prefix + "/clearance")
    async def clearance(project_id: str, body: ClearanceIn) -> dict[str, Any]:
        led = get_ledger(project_id)
        current = {p["fields"]["number"]: p for p in mech.current_parts(led.state())}
        files: dict[str, Path] = {}
        for number in body.parts:
            stored = current.get(number, {}).get("fields", {}).get("file") if number in current else None
            if not stored or Path(stored).suffix not in (".step", ".stp"):
                raise HTTPException(400, f"{number} has no STEP file.")
            files[number] = led.root / "parts" / stored
        if len(files) < 2:
            raise HTTPException(400, "Choose at least two parts.")
        return {"results": await asyncio.to_thread(geometry.clearance, files)}

    @app.post(prefix + "/releases")
    def release(project_id: str, body: ReleaseIn) -> dict[str, Any]:
        led = get_ledger(project_id)
        result = mech.create_release(led, led.root, body.recipient, body.override, Actor.human())
        return {"release": result, "view": view(led)}

    @app.get(prefix + "/releases/{release_id}/file")
    def release_file(project_id: str, release_id: str) -> FileResponse:
        led = get_ledger(project_id)
        if not re.fullmatch(r"R\d+", release_id) or not (led.root / "releases" / f"{release_id}.zip").exists():
            raise HTTPException(404, "Release not found.")
        return FileResponse(led.root / "releases" / f"{release_id}.zip", filename=f"{release_id}.zip")

    @app.post(prefix + "/orders")
    def add_order(project_id: str, body: OrderIn) -> dict[str, Any]:
        led = get_ledger(project_id)
        proc.add_order(led, body.model_dump(), Actor.human())
        return view(led)

    @app.post(prefix + "/invoices/preview")
    async def invoice_preview(project_id: str, people: str, request: Request) -> dict[str, Any]:
        get_ledger(project_id)
        data = await request.body()
        if not data.startswith(b"%PDF-") or len(data) > MAX_UPLOAD:
            raise HTTPException(400, "Use a PDF invoice.")
        try:
            raw = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
        except Exception as exc:
            raise HTTPException(400, "That PDF couldn't be read.") from exc
        names = [p for p in people.split(",") if p.strip()]
        text = proc.redact(raw, names)
        return {"text": text, "clean": proc.is_clean(text, names), "hasText": bool(raw.strip())}

    @app.post(prefix + "/invoices/read")
    async def invoice_read(project_id: str, body: InvoiceIn) -> dict[str, Any]:
        led = get_ledger(project_id)
        await guarded(project_id, proc.read_invoice(led, get_gateway(led), body.text, body.people))
        return view(led)

    # ---- Import ----

    @app.post("/api/import/scan")
    def import_scan(body: ScanIn) -> dict[str, Any]:
        root = Path(body.folder.strip().strip('"'))
        try:
            return {"scan": importer.scan(root), "gaps": importer.gaps(root)}
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/import/preview")
    def import_preview(body: ImportIn) -> dict[str, Any]:
        return importer.preview(Path(body.folder.strip().strip('"')), body.files, body.people)

    @app.post(prefix + "/import/draft")
    async def import_draft(project_id: str, body: ImportIn) -> dict[str, Any]:
        led = get_ledger(project_id)
        result = await guarded(project_id, importer.draft(led, get_gateway(led), Path(body.folder.strip().strip('"')), body.files, body.people))
        return {"result": result, "view": view(led)}

    @app.post(prefix + "/import/resume")
    def import_resume(project_id: str, body: ResumeIn) -> dict[str, Any]:
        led = get_ledger(project_id)
        try:
            gates.resume_at(led, body.gate, Actor.human())
        except gates.GateError as exc:
            raise HTTPException(409, str(exc)) from exc
        return view(led)

    @app.post(prefix + "/import/{item_id}/confirm")
    def import_confirm(project_id: str, item_id: str) -> dict[str, Any]:
        led = get_ledger(project_id)
        record = led.state().records.get(item_id)
        if not record or record["type"] != "imported_item":
            raise HTTPException(404, "Item not found.")
        importer.confirm_item(led, item_id, Actor.human())
        return view(led)
