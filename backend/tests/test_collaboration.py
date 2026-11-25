"""Fixes from the MA-6 controller run: collision shapes, shared deliverables, requests between departments,
the code writer finishing its work, the simulation-first scope and cheaper routine steps."""
import math
import struct

import pytest

from buildanything import deliverables, designneeds, firmware as fw, gatereport, headquarters as hq, protocol, robot_runtime as rt, software as sw
from buildanything.ledger import Actor, Ledger, PermissionDenied
from tests.test_headquarters import Gateway, Papers

HUMAN = Actor.human()


@pytest.fixture
def led(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.append(HUMAN, "project.created", name="T", aim="Move an arm", scope="Bench")
    return ledger


@pytest.fixture
def services():
    return hq.Services(Papers())


# ---- Collision shapes ----

def _arm(fold_limit=math.pi):
    """A base block and a 0.2 m arm hinged above it about Y: folding down by 90° drives the arm into the base."""
    return {"root": "base", "joints": [{"name": "shoulder", "type": "revolute", "parent": "base", "child": "arm", "xyz": [0, 0, 0.1], "rpy": [0, 0, 0], "axis": [0, 1, 0], "lower": -fold_limit, "upper": fold_limit},
                                       {"name": "elbow", "type": "revolute", "parent": "arm", "child": "hand", "xyz": [0.2, 0, 0], "rpy": [0, 0, 0], "axis": [0, 1, 0], "lower": -math.pi, "upper": math.pi}],
            "links": {"base": {"boxes": [{"c": [0, 0, 0.04], "h": [0.15, 0.15, 0.04]}]}, "arm": {"boxes": [{"c": [0.1, 0, 0], "h": [0.1, 0.01, 0.01]}]}, "hand": {"boxes": [{"c": [0.05, 0, 0], "h": [0.05, 0.01, 0.01]}]}},
            "ignore": [["base", "arm"], ["arm", "hand"]]}


def test_a_folded_arm_hits_its_base_and_a_straight_one_doesnt():
    model = _arm()
    assert rt.collisions(model, [0.0, 0.0]) == []
    assert ("base", "hand") in rt.collisions(model, [0.0, math.radians(120)])  # the hand folds down into the base
    hit = rt.path_collisions(model, [0.0, 0.0], [0.0, math.radians(120)])
    assert hit is not None and 0 < hit[0] <= 1


def test_added_parts_count_too():
    model = _arm()
    assert rt.collisions(model, [0.0, 0.0]) == []
    rt.add_box(model, "hand", [-0.1, 0, -0.05], [0.01, 0.01, 0.05])  # a pen hanging 10 cm below the hand, over the base
    assert ("base", "hand") in rt.collisions(model, [0.0, 0.0])


def _cube(path, size):
    facets = []
    s = size
    corners = [(0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0), (0, 0, s), (s, 0, s), (s, s, s), (0, s, s)]
    for a, b, c in [(0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (2, 3, 7), (2, 7, 6), (1, 2, 6), (1, 6, 5), (0, 3, 7), (0, 7, 4)]:
        facets.append(struct.pack("<12fH", 0, 0, 0, *corners[a], *corners[b], *corners[c], 0))
    path.write_bytes(b"\0" * 80 + struct.pack("<I", len(facets)) + b"".join(facets))


# ---- Deliverables ----

def test_any_agent_can_read_other_departments_files_but_nothing_else(led):
    (led.root / "mechanics" / "cad").mkdir(parents=True)
    (led.root / "mechanics" / "cad" / "collision.json").write_text('{"ok": 1}', encoding="utf-8")
    assert deliverables.read(led, "mechanics/cad/collision.json")["text"] == '{"ok": 1}'
    assert "error" in deliverables.read(led, "ledger/events.jsonl")
    assert "error" in deliverables.read(led, "../outside.txt")
    assert any(f["path"] == "mechanics/cad/collision.json" for f in deliverables.index(led)["files"])


def test_leads_have_the_deliverable_tools(led, services):
    names = [getattr(t, "__name__", "") for t in hq._lead_tools(led, Gateway(), "Software", services)]
    assert {"list_deliverables", "read_deliverable", "read_department_reports"} <= set(names)


# ---- Requests between departments ----

def _report(led, department, kind, can=()):
    protocol.file_report(led, hq.SYSTEM, department, protocol.command("QUESTION"), protocol.Report(type=kind, done=[], not_checked=[], provisional=[], needs=[], can_give_now=list(can), risk="", severity="none", safety=False, for_person=f"{department} replied"), [])


def test_a_request_needs_a_specific_deliverable(led, services):
    tool = hq.department_tool(led, Gateway(), "Software", services)
    assert "Say exactly" in pytest.importorskip("asyncio").run(tool("Mechanical", "inputs", ""))


@pytest.mark.asyncio
async def test_two_departments_asking_each_other_go_to_the_plans_owner(led, services):
    led.append(HUMAN, "concept.approved", summary="s", decisions=[], open_questions=[])
    led.propose(Actor.agent("controller"), "task", key="M1", department="Mechanical", title="Collision shapes for each arm link from the CAD", weeks=1, depends_on=[])
    gateway = Gateway()
    await hq.department_tool(led, gateway, "Software", services)("Mechanical", "collision shapes for each arm link", "the path planner")
    _report(led, "Mechanical", "BLOCKED")
    led.append(hq.SYSTEM, "request.answered", id=hq.requests_view(led.events())[-1]["id"], answer="blocked", outcome="BLOCKED")
    reply = await hq.department_tool(led, gateway, "Mechanical", services)("Software", "collision shapes for each arm link", "checking clearance")
    assert "plan gives this work to you" in reply  # Mechanical asked Software for its own job


@pytest.mark.asyncio
async def test_nobodys_job_goes_to_the_person_as_one_question(led, services):
    led.append(HUMAN, "concept.approved", summary="s", decisions=[], open_questions=[])
    gateway = Gateway()
    await hq.department_tool(led, gateway, "Software", services)("Mechanical", "clearance inputs for every link", "the planner")
    reply = await hq.department_tool(led, gateway, "Mechanical", services)("Software", "clearance inputs for every link", "the check")
    assert "asked whose job" in reply
    waiting = [r for r in hq.requests_view(led.events()) if r["status"] == "waiting_for_you"]
    assert waiting and "Which department should do it" in waiting[0]["question"]


@pytest.mark.asyncio
async def test_a_blocked_answer_still_passes_on_what_can_be_given(led, services, monkeypatch):
    async def lead(ledger, gateway, department, message, services, **_):
        _report(ledger, department, "BLOCKED", ["shapes for the six CAD links, with a 6 cm cylinder for the pen holder"])
        return "Blocked on the pen holder."

    monkeypatch.setattr(hq, "run_lead", lead)
    answer = await hq.department_tool(led, Gateway(), "Software", services)("Mechanical", "collision shapes for each arm link", "the path planner")
    assert "6 cm cylinder" in answer
    assert hq.requests_view(led.events())[-1]["outcome"] == "BLOCKED"


# ---- The code writer ----

def test_unchanged_blocks_keep_the_old_code(tmp_path):
    old = fw.FileOut(path="app.py", owner="Software", blocks=[fw.BlockOut(title="Setup", description="d", code="x = 1"), fw.BlockOut(title="Run", description="d", code="print(x)")])
    fw.write_files(tmp_path, "laptop", [old], host=True)
    new = fw.FileOut(path="app.py", owner="Software", blocks=[fw.BlockOut(title="Setup", description="", unchanged=True, code=""), fw.BlockOut(title="Run", description="d", code="print(x + 1)")])
    merged, problems = fw.merge_unchanged(tmp_path, "laptop", [new])
    assert not problems and merged[0].blocks[0].code == "x = 1" and merged[0].blocks[0].description == "d"
    _, problems = fw.merge_unchanged(tmp_path, "laptop", [new.model_copy(update={"blocks": [fw.BlockOut(title="Gone", description="", unchanged=True, code="")]})])
    assert problems and "Gone" in problems[0]


def test_modules_nothing_starts_are_reported(tmp_path):
    base = tmp_path / "firmware" / "laptop"
    base.mkdir(parents=True)
    (base / "main.py").write_text("import helper\nif __name__ == '__main__':\n    helper.go()\n", encoding="utf-8")
    (base / "helper.py").write_text("def go():\n    pass\n", encoding="utf-8")
    (base / "collision_check.py").write_text("def check():\n    pass\n", encoding="utf-8")
    (base / "test_collision_check.py").write_text("import collision_check\n", encoding="utf-8")
    loose = sw.unconnected(tmp_path, "laptop", ["helper.py", "collision_check.py", "test_collision_check.py"])
    assert len(loose) == 1 and loose[0].startswith("collision_check.py")


def test_outlines_send_the_relevant_files_in_full(tmp_path):
    files = [fw.FileOut(path="app.py", owner="Software", blocks=[fw.BlockOut(title="Main", description="d", code="if __name__ == '__main__':\n    pass")]),
             fw.FileOut(path="other.py", owner="Software", blocks=[fw.BlockOut(title="Big", description="d", code="x = 1\n" * 20000)])]
    fw.write_files(tmp_path, "laptop", files, host=True)
    text = fw.outline_code(tmp_path, "laptop", "change the app", full_chars=1000)
    assert "app.py (full)" in text and "other.py (outline" in text


def test_automatic_test_runs_need_a_persons_permission(led):
    base = led.root / "firmware" / "hub"
    base.mkdir(parents=True)
    (base / "test_x.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        pass\n", encoding="utf-8")
    with pytest.raises(PermissionDenied):
        sw.run_tests(led, led.root, "hub", True, Actor.system("software"))
    sw.allow_autotest(led, "hub", True, HUMAN)
    assert sw.run_tests(led, led.root, "hub", True, Actor.system("software"))["ok"]
    with pytest.raises(PermissionDenied):
        sw.allow_autotest(led, "hub", True, Actor.agent("software"))


# ---- Gate checks ----


# ---- Simulation first ----

def test_simulation_first_defers_hardware_departments(led):
    for gate in ("G0", "G1", "G2"):
        led.append(HUMAN, "gate.approved", gate=gate)
    led.append(HUMAN, "concept.approved", summary="s", decisions=[], open_questions=[])
    assert hq.next_action(led) == "open:Electrical"
    designneeds.set_scope(led, True, "No hardware yet", HUMAN)
    assert hq.next_action(led) == "open:Mechanical"
    assert designneeds.deferred(led, "Procurement") and not designneeds.deferred(led, "Software")
    assert not any(owner in ("Electrical", "Procurement") for owner, _t in gatereport._design_review(led, led.state()))
    designneeds.set_scope(led, False, "", HUMAN)
    assert hq.next_action(led) == "open:Electrical"
    with pytest.raises(PermissionDenied):
        designneeds.set_scope(led, True, "No hardware yet", Actor.agent("controller"))


# ---- Cheaper routine steps ----

@pytest.mark.asyncio
async def test_answering_a_question_runs_on_the_cheaper_tier(led, services):
    seen = []

    class Recorder(Gateway):
        async def run(self, **kwargs):
            seen.append((kwargs["agent"], kwargs.get("tier"), kwargs.get("request_limit")))
            return await super().run(**kwargs)

    await hq.run_lead(led, Recorder(), "Electrical", "Which pin?", services, nested=True)
    await hq.talk_to_department(led, Recorder(), "Electrical", "Design it", services)
    assert seen[0] == ("lead_electrical", "Fast", 8)
    assert seen[1][1] is None  # a person's task keeps the agent's own tier


def test_saying_there_is_no_placeholder_isnt_a_placeholder():
    assert not gatereport._stand_in("Middleware umbrella. No application or placeholder main is provided.")
    assert gatereport._stand_in("Illustrative forward geometry. A made-up 2D arm.")


def test_scripts_a_page_loads_by_name_count_as_used(tmp_path):
    base = tmp_path / "firmware" / "laptop"
    base.mkdir(parents=True)
    (base / "app.py").write_text("PAGE = '<script src=\"/client.js\"></script>'\nif __name__ == '__main__':\n    pass\n", encoding="utf-8")
    (base / "client.js").write_text("console.log(1)\n", encoding="utf-8")
    (base / "unused.js").write_text("console.log(2)\n", encoding="utf-8")
    loose = sw.unconnected(tmp_path, "laptop", ["client.js", "unused.js"])
    assert len(loose) == 1 and loose[0].startswith("unused.js")


def test_imported_drivers_and_middleware_count_as_planned(tmp_path):
    led = Ledger(tmp_path)
    base = tmp_path / "firmware" / "bot" / "src"
    (base / "driver").mkdir(parents=True)
    (base / "mid").mkdir(parents=True)
    (base / "driver" / "drv_motor.h").write_text("#pragma once\n", encoding="utf-8")
    (base / "mid" / "mid_sync.h").write_text("#pragma once\n", encoding="utf-8")
    plan = fw.lower_plan(led, "bot")
    assert {(m.layer, m.name) for m in plan} == {("driver", "drv_motor"), ("mid", "mid_sync")}
    app = fw.ModulePlan(layer="app", name="main", purpose="p", functions=[], uses=["mid_sync"])
    files = [fw.FileOut(path="src/app/main.cpp", owner="Software", blocks=[fw.BlockOut(title="Main", description="d", code='#include "mid_sync.h"')])]
    assert not [p for p in fw.layer_problems([app], files, [], {}, ("app",), plan) if "isn't a mid module" in p]


def test_a_datasheet_with_a_simplified_schematic_stays_a_datasheet():
    from buildanything import documents
    page = "DRV5055 Ratiometric Linear Hall-Effect Sensor\n1 Features\n... Simplified Schematic ... Typical Application Schematic"
    assert documents.detect_kind([page]) is None
    assert documents.detect_kind(["ESP32-C6-DevKitC-1 Schematics v1.4\nSheet 1"]) == "schematic"
    assert documents.detect_kind(["NUCLEO-F401RE User manual\nUM1724"]) == "user_manual"


def test_a_person_can_take_a_part_off_the_list(led):
    from buildanything import changes, mechanical
    mechanical.add_part(led, {"number": "ST-01", "name": "Stair frame", "material": "Al", "process": "Fabricate", "qty": 1, "mass_g": None, "unit_cost": None}, HUMAN)
    change = changes.propose(led, "Remove the stair frame", "The stair is only in the simulation.", "part", "ST-01", {"remove": True}, HUMAN)
    led.append(Actor.system("headquarters"), "change.reviewed", id=change, review={"recommendation": "approve", "summary": "ok", "risks": [], "missed": []})
    changes.approve(led, change, "Not building a real stair.", HUMAN)
    assert "ST-01" not in mechanical.parts_by_number(led.state())


def test_reused_existing_assemblies_count_as_owned():
    assert designneeds.is_owned({"fields": {"process": "Reuse existing assembly"}})
    assert designneeds.is_owned({"fields": {"process": "Existing purchased kit"}})
    assert not designneeds.is_owned({"fields": {"process": "Existing or purchased"}})
    assert not designneeds.is_owned({"fields": {"process": "Fabricate and assemble"}})
