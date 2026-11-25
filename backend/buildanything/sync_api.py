"""Routes for interfaces, link issues and working with other people."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

from . import capture as evidence, changes, gatereport, gates, headquarters as hq, traceability, interfaces as itf, links, overview, workspace
from .ledger import Actor, Ledger, current_person, set_person


def _text(body: dict[str, Any], key: str) -> str:
    value = body.get(key, "")
    return value if isinstance(value, str) else ""


def register(app: FastAPI, root: Path, get_ledger: Callable[[str], Ledger], get_gateway: Callable[[Ledger], Any], guarded: Callable[[str, Any], Any], project_output: Callable[[str, Ledger], dict[str, Any]], registry: Any = None) -> None:
    prefix = "/api/projects/{project_id}/interfaces"
    person_file = root / "_app" / "person.json"
    set_person(json.loads(person_file.read_text(encoding="utf-8"))["name"] if person_file.exists() else "owner")

    def view(led: Ledger) -> dict[str, Any]:
        return {**itf.view(led), "links": list(links.issues(led).values())}

    @app.get("/api/person")
    def person_get() -> dict[str, str]:
        return {"name": current_person()}

    @app.post("/api/person")
    def person_set(body: dict[str, Any] = Body(...)) -> dict[str, str]:
        name = _text(body, "name").strip()[:60]
        if not name:
            raise HTTPException(400, "Enter your name.")
        person_file.parent.mkdir(parents=True, exist_ok=True)
        person_file.write_text(json.dumps({"name": name}), encoding="utf-8")
        set_person(name)
        return {"name": name}

    @app.get(prefix)
    def interfaces_view(project_id: str) -> dict[str, Any]:
        return view(get_ledger(project_id))

    def after_change(led: Ledger) -> dict[str, Any]:
        itf.regenerate(led.root, led.state())
        return view(led)

    @app.post(prefix + "/packets")
    def packet_save(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        itf.save_packet(led, body.get("packet") or {}, Actor.human(), _text(body, "reason"))
        return after_change(led)

    @app.post(prefix + "/packets/draft")
    async def packet_draft(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        if not _text(body, "request").strip():
            raise HTTPException(400, "Describe the packet.")
        made = await guarded(project_id, itf.draft_packet(led, get_gateway(led), _text(body, "request")))
        return {"made": made["name"], "view": after_change(led)}

    @app.post(prefix + "/packets/{name}/ack")
    def packet_ack(project_id: str, name: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        itf.acknowledge(led, name, _text(body, "consumer"), Actor.human())
        return view(led)

    @app.post(prefix + "/packets/{name}/remove")
    def packet_remove(project_id: str, name: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        itf.remove_packet(led, name, _text(body, "reason"), Actor.human())
        return after_change(led)

    @app.post(prefix + "/packets/{name}/simulate")
    async def packet_simulate(project_id: str, name: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        report = await asyncio.to_thread(itf.simulate, led, name, _text(body, "fault") or "none", int(body.get("nodes") or 2), int(body.get("count") or 10))
        return {"report": report, "view": view(led)}

    @app.post(prefix + "/check")
    async def generated_check(project_id: str) -> dict[str, Any]:
        led = get_ledger(project_id)
        return {"results": await asyncio.to_thread(itf.check_generated, led.root, led.state()), "view": view(led)}

    @app.get(prefix + "/generated/{name}")
    def generated_file(project_id: str, name: str) -> dict[str, str]:
        return {"name": name, "text": itf.generated_text(get_ledger(project_id).root, name)}

    @app.post(prefix + "/physical")
    def physical_add(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        itf.add_physical(led, _text(body, "kind"), _text(body, "name"), _text(body, "agreed"), _text(body, "between"), Actor.human())
        return view(led)

    # ---- Link issues ----

    @app.post(prefix + "/links")
    async def link_open(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        await asyncio.to_thread(links.open_issue, led, _text(body, "title"), [_text(body, "from"), _text(body, "to")], _text(body, "packet") or None, "Reported by a person", Actor.human())
        return view(led)

    @app.post(prefix + "/links/{issue_id}/check")
    def link_check(project_id: str, issue_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        links.record_check(led, issue_id, int(body.get("index", -1)), _text(body, "result"), _text(body, "evidence"), Actor.human())
        return view(led)

    @app.post(prefix + "/links/{issue_id}/capture")
    async def link_capture(project_id: str, issue_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        ports = body.get("ports") if isinstance(body.get("ports"), dict) else {}
        result = await asyncio.to_thread(evidence.capture, led, issue_id, {str(k): str(v) for k, v in ports.items()})
        if result["failed"] is not None:
            await guarded(project_id, links.advise(led, get_gateway(led), issue_id))
        return {"result": result, "view": view(led)}

    @app.post(prefix + "/links/{issue_id}/advice")
    async def link_advice(project_id: str, issue_id: str) -> dict[str, Any]:
        led = get_ledger(project_id)
        await guarded(project_id, links.advise(led, get_gateway(led), issue_id))
        return view(led)

    @app.post(prefix + "/links/{issue_id}/answer")
    def link_answer(project_id: str, issue_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        links.add_answer(led, issue_id, _text(body, "text"), Actor.human())
        return view(led)

    @app.post(prefix + "/links/{issue_id}/route")
    def link_route(project_id: str, issue_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        links.route(led, issue_id, _text(body, "cause"), _text(body, "device"), _text(body, "detail"), Actor.human())
        return view(led)

    @app.post(prefix + "/links/{issue_id}/close")
    def link_close(project_id: str, issue_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        links.close(led, issue_id, _text(body, "evidence"), Actor.human())
        return view(led)

    # ---- Change Requests ----

    def changes_view(led: Ledger) -> dict[str, Any]:
        return {"changes": changes.view(led), "options": changes.options(led.state())}

    @app.get("/api/projects/{project_id}/changes")
    def changes_get(project_id: str) -> dict[str, Any]:
        return changes_view(get_ledger(project_id))

    @app.post("/api/projects/{project_id}/changes")
    def changes_post(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        changes.propose(led, _text(body, "title"), _text(body, "why"), _text(body, "kind"), _text(body, "target"), body.get("details") if isinstance(body.get("details"), dict) else {}, Actor.human())
        return changes_view(led)

    @app.post("/api/projects/{project_id}/changes/{change_id}/approve")
    def changes_approve(project_id: str, change_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        changes.approve(led, change_id, _text(body, "reason"), Actor.human())
        return changes_view(led)

    @app.post("/api/projects/{project_id}/changes/{change_id}/reject")
    def changes_reject(project_id: str, change_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        changes.reject(led, change_id, _text(body, "reason"), Actor.human())
        return changes_view(led)

    @app.post("/api/projects/{project_id}/changes/{change_id}/reopen")
    def changes_reopen(project_id: str, change_id: str) -> dict[str, Any]:
        led = get_ledger(project_id)
        changes.reopen(led, change_id, Actor.human())
        return changes_view(led)

    @app.post("/api/projects/{project_id}/requests/{request_id}/answer")
    def request_answer(project_id: str, request_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        try:
            hq.answer_request(led, request_id, _text(body, "text"), Actor.human())
        except hq.HeadquartersError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    # ---- Gates ----

    @app.get("/api/projects/{project_id}/memory")
    def project_memory(project_id: str) -> dict[str, Any]:
        from . import memory

        return memory.graph(get_ledger(project_id))

    @app.get("/api/projects/{project_id}/story")
    def project_story(project_id: str, after: int = -1) -> dict[str, Any]:
        from . import story

        return {"team": story.TEAM, "steps": story.steps(get_ledger(project_id), after)}

    @app.get("/api/projects/{project_id}/gates")
    def gate_list(project_id: str) -> list[dict[str, Any]]:
        return gatereport.gate_list(get_ledger(project_id))

    @app.get("/api/projects/{project_id}/gates/{gate}/report")
    def gate_report(project_id: str, gate: str) -> dict[str, str]:
        if gate not in gates.order(get_ledger(project_id).state()):
            raise HTTPException(404, "Gate not found.")
        return {"text": gatereport.saved_report(get_ledger(project_id), gate)}

    @app.post("/api/projects/{project_id}/orders/{order_id}/arrived")
    def order_arrived(project_id: str, order_id: str) -> dict[str, bool]:
        led = get_ledger(project_id)
        record = led.state().records.get(order_id)
        if not record or record["type"] != "order":
            raise HTTPException(404, "Order not found.")
        led.append(Actor.human(), "order.arrived", id=order_id)
        return {"ok": True}

    # ---- Requirements and tests ----

    @app.get("/api/projects/{project_id}/tests")
    def tests_view(project_id: str) -> dict[str, Any]:
        return traceability.view(get_ledger(project_id))

    @app.get("/api/projects/{project_id}/tests.csv")
    def tests_csv(project_id: str) -> PlainTextResponse:
        return PlainTextResponse(traceability.csv_export(get_ledger(project_id)), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="requirements-and-tests.csv"'})

    @app.post("/api/projects/{project_id}/tests/{test_id}/dependencies")
    def tests_dependencies(project_id: str, test_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        uses = body.get("uses") if isinstance(body.get("uses"), list) else []
        traceability.set_dependencies(led, test_id, [str(u) for u in uses], Actor.human())
        return traceability.view(led)

    @app.post("/api/projects/{project_id}/deviations/{deviation_id}/close")
    def deviation_close(project_id: str, deviation_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        traceability.close_deviation(led, deviation_id, _text(body, "reason"), Actor.human())
        return traceability.view(led)

    @app.post("/api/projects/{project_id}/tests/clarify")
    def tests_clarify(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        traceability.clarify(led, _text(body, "key"), _text(body, "answer"), Actor.human())
        return traceability.view(led)

    @app.post("/api/projects/{project_id}/tests/approve-all")
    def tests_approve(project_id: str) -> dict[str, Any]:
        led = get_ledger(project_id)
        traceability.approve_all(led, Actor.human())
        return traceability.view(led)

    @app.post("/api/projects/{project_id}/tests/{test_id}/result")
    def tests_result(project_id: str, test_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        raw = body.get("value")
        value = float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None
        traceability.record_result(led, test_id, value, _text(body, "outcome"), _text(body, "evidence"), Actor.human())
        return traceability.view(led)

    # ---- Project-wide views ----

    @app.get("/api/projects/{project_id}/overview")
    def project_overview(project_id: str) -> dict[str, Any]:
        return overview.summary(get_ledger(project_id), registry)

    @app.get("/api/projects/{project_id}/review")
    def project_review(project_id: str) -> list[dict[str, Any]]:
        return overview.review_queue(get_ledger(project_id))

    @app.get("/api/projects/{project_id}/tasks")
    def project_tasks(project_id: str) -> list[dict[str, Any]]:
        return overview.task_view(get_ledger(project_id))

    @app.post("/api/projects/{project_id}/tasks/{task_id}/done")
    def task_done(project_id: str, task_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        overview.mark_task_done(get_ledger(project_id), task_id, body.get("done") is True, Actor.human())
        return {"tasks": overview.task_view(get_ledger(project_id))}

    @app.get("/api/projects/{project_id}/sources")
    def project_sources(project_id: str) -> dict[str, Any]:
        return overview.source_view(get_ledger(project_id), project_id)

    @app.post("/api/projects/{project_id}/sources")
    async def source_upload(project_id: str, filename: str, tier: str, request: Request) -> dict[str, Any]:
        led = get_ledger(project_id)
        data = await request.body()
        await asyncio.to_thread(overview.upload_source, led, filename, tier, data, Actor.human())
        return overview.source_view(led, project_id)

    @app.get("/api/projects/{project_id}/sources/{source_id}/file")
    def source_file(project_id: str, source_id: str) -> FileResponse:
        led = get_ledger(project_id)
        record = led.state().records.get(source_id)
        if not record or record["type"] != "source" or not record["fields"].get("file"):
            raise HTTPException(404, "Source not found.")
        return FileResponse(led.root / "sources" / record["fields"]["file"], media_type="application/pdf")

    # ---- Working with other people ----

    @app.post("/api/projects/{project_id}/remote")
    def remote_set(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        led = get_ledger(project_id)
        workspace.set_remote(led.root, _text(body, "url"))
        return project_output(project_id, led)

    @app.post("/api/projects/{project_id}/sync")
    async def sync(project_id: str) -> dict[str, Any]:
        led = get_ledger(project_id)
        result = await asyncio.to_thread(workspace.sync, led.root, current_person(), lambda: itf.regenerate(led.root, led.state()))
        return {"result": result, "project": project_output(project_id, led)}

    @app.post("/api/projects/join")
    async def join(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        project_id = await asyncio.to_thread(workspace.clone_project, root, _text(body, "url"))
        return project_output(project_id, get_ledger(project_id))
