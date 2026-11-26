"""Fixes from the second RUA review run: one job per department, Headquarters driving the work behind the person's
confirm items, stalls, off-team tasks, hollow connections, Verifier decisions, project files and the test environment."""
import sys
from types import SimpleNamespace

from buildanything import agents, cloud, gates, gatereport, headquarters as hq, jobs, shape, software
from buildanything.changes import Route
from buildanything.ledger import Actor, Ledger


class RouterGateway:
    async def run(self, agent, **kwargs):
        assert agent == "router"
        return Route(action="forward", handler="Software", reason="Software owns the simulation.", earlier_id=None)


async def test_a_request_to_a_busy_department_is_queued_not_run_beside_its_job(tmp_path, monkeypatch):
    led = Ledger(tmp_path / "p")
    monkeypatch.setattr(shape, "team", lambda state: ["Mechanical", "Software"])
    started = []

    async def no_second_job(*args, **kwargs):
        started.append(args)
        return "should not run"

    monkeypatch.setattr(hq, "run_lead", no_second_job)
    board = jobs._JOBS.setdefault(str(led.root), {})
    board["job_x"] = jobs.Job(id="job_x", department="Software", title="Build the simulator", items=[], touches={jobs.UNKNOWN}, clone=0, status="running")
    try:
        ask = hq.department_tool(led, RouterGateway(), "Mechanical", hq.Services(None))
        answer = await ask("Software", "the MuJoCo model with the six tray slots", "checking reach to each slot")
    finally:
        board.pop("job_x", None)
    assert not started and "busy" in answer
    pending = [e["data"] for e in led.events() if e["kind"] == "relay.pending"]
    assert pending and pending[0]["to"] == "Software" and pending[0]["sender"] == "Mechanical"
    # Headquarters passes it on only once Software is free, and counts it as Software's work.
    relay = f"relay:{next(e['id'] for e in led.events() if e['kind'] == 'relay.pending')}"
    assert hq.department_of(led, relay) == "Software"


def test_jobs_started_anywhere_make_a_department_busy(tmp_path):
    led = Ledger(tmp_path / "p")
    board = jobs._JOBS.setdefault(str(led.root), {})
    board["job_y"] = jobs.Job(id="job_y", department="Software", title="Chat", items=[], touches=set(), clone=0)
    try:
        assert jobs.busy(led) == {"Software"}
    finally:
        board.pop("job_y", None)
    assert jobs.busy(led) == set()


def _report(led, department, kind, needs, done=(), command="TASK"):
    led.append(Actor.system("t"), "report.filed", department=department, command=command, items=[], evidence=[], type=kind, done=list(done), not_checked=[], provisional=[],
               needs=[{"owner": o, "what": w} for o, w in needs], can_give_now=[], risk="", severity="none", safety=False, for_person="", details="")


def test_the_work_behind_confirm_items_and_a_departments_own_leftovers_are_driven(tmp_path, monkeypatch):
    led = Ledger(tmp_path / "p")
    monkeypatch.setattr(shape, "team", lambda state: ["Mechanical", "Software"])
    monkeypatch.setattr(gatereport, "blockers", lambda gate, state, ledger: [("You", "Confirm on the Journey screen: The arm loads in MuJoCo.")])
    monkeypatch.setattr(gates, "criteria_view", lambda state, gate: [{"index": 0, "text": "The supplied URDF loads as a controllable arm in MuJoCo.", "met": False}])
    for department, title in (("Software", "Build the MuJoCo arm model from the supplied URDF"), ("Mechanical", "Prepare tray slot coordinates")):
        task = led.propose(Actor.agent("controller"), "task", key=department[0], department=department, title=title, weeks=1, depends_on=[], start_week=0)
        led.append(Actor.human(), "record.confirmed", id=task)
    _report(led, "Software", "BLOCKED", [("Software", "Fix the RUA timing check."), ("Person", "Add the mesh folder.")])
    _report(led, "Software", "ANSWER", [], command="QUESTION")  # a reply to a question doesn't hide the leftovers
    work = hq._owned_work(led, led.state(), "C1")
    assert work["Software"][0].startswith("Make this work and show how you checked it") and "MuJoCo" in work["Software"][0]
    assert "Fix the RUA timing check." in work["Software"]
    assert "Mechanical" not in work and not any("mesh folder" in w for w in work["Software"])


def test_two_rounds_that_finish_nothing_stop_the_driving(tmp_path):
    led = Ledger(tmp_path / "p")
    _report(led, "Software", "PARTIAL", [("Software", "x")], done=["wrote sim.py"])
    assert not hq._stalled(led.events(), "Software")
    _report(led, "Software", "PARTIAL", [("Software", "x")])
    _report(led, "Software", "BLOCKED", [("Software", "x")])
    assert hq._stalled(led.events(), "Software")


def test_tasks_for_departments_off_the_team_are_sent_back():
    plan = agents.PlanOut(requirements=[], tasks=[
        agents.TaskOut(key="E1", department="Electrical", title="Define the PCA9685 interface", weeks=1, depends_on=[]),
        agents.TaskOut(key="S1", department="Software", title="Build the app", weeks=1, depends_on=[]),
    ])
    problems = agents.plan_problems(plan, ["Mechanical", "Software"])
    assert len(problems) == 1 and "Electrical" in problems[0] and "Mechanical, Software" in problems[0]
    assert agents.plan_problems(plan) == []


def test_an_action_that_keeps_returning_nothing_is_flagged_for_agents(tmp_path):
    assert cloud._empty(None) and cloud._empty("") and cloud._empty([{"text": ""}]) and cloud._empty({"content": []})
    assert not cloud._empty({"components": ["base_link"]}) and not cloud._empty(0.5)
    led = Ledger(tmp_path / "app")
    store = SimpleNamespace(ledger=led)
    for empty in (True, True):
        led.append(Actor.system("t"), "conn.called", id="fusion", operation="execute_script", ok=True, empty=empty)
    led.append(Actor.system("t"), "conn.called", id="fusion", operation="get_document", ok=True, empty=False)
    assert cloud.Store.returns_nothing(store, "fusion") == {"execute_script"}


def test_medium_findings_can_be_decided_and_revise_the_plan(tmp_path):
    led = Ledger(tmp_path / "p")
    ids = [led.propose(Actor.agent("verifier"), "finding", run=1, severity=s, safety=False, text=f"{s} problem", related=[]) for s in ("medium", "medium", "low")]
    assert hq.fixes_to_apply(led.state(), led.events()) == []
    gates.decide_finding(led, ids[0], "fix", "", Actor.human())
    assert hq.fixes_to_apply(led.state(), led.events()) == []  # waits until every medium has a decision
    gates.decide_finding(led, ids[1], "later", "Only matters on the real arm.", Actor.human())
    assert [f["id"] for f in hq.fixes_to_apply(led.state(), led.events())] == [ids[0]]


def test_departments_see_files_the_person_added(tmp_path):
    root = tmp_path / "p"
    robot = root / "mechanics" / "cad" / "fusion_export" / "robot"
    (robot / "meshes").mkdir(parents=True)
    (robot / "robot.urdf").write_text('<robot name="arm"><link name="base_link"/></robot>', encoding="utf-8")
    for i in range(20):
        (robot / "meshes" / f"m{i:03d}.stl").write_text("solid", encoding="utf-8")
    led = Ledger(root)
    listed = hq.project_files(root)
    assert listed["mechanics/cad/fusion_export/robot"] == ["robot.urdf"] and listed["mechanics/cad/fusion_export/robot/meshes"].startswith("20 files")
    assert not any(k.startswith("ledger") for k in listed)
    assert hq._cad_files(led)["model"] == "mechanics/cad/fusion_export/robot/robot.urdf"
    assert software.cad_brief(root).startswith("URDF: ../../mechanics/cad/fusion_export/robot/robot.urdf")


def test_the_program_gets_its_own_python_with_its_requirements(tmp_path):
    base = tmp_path / "p" / "firmware" / "laptop"
    base.mkdir(parents=True)
    (base / "requirements.txt").write_text("", encoding="utf-8")
    python, problem = software.device_python(tmp_path / "p", "laptop")
    assert not problem and python == sys.executable  # nothing installed until a person allowed it
    python, problem = software.device_python(tmp_path / "p", "laptop", install=True)
    assert not problem and ".venv" in python and (base / ".venv" / ".requirements-hash").exists()
    assert software.device_python(tmp_path / "p", "laptop")[0] == python


async def test_headquarters_chat_can_raise_a_change_once_the_concept_is_approved(tmp_path, monkeypatch):
    import json as _json

    from buildanything import changes

    led = Ledger(tmp_path / "p")
    seen = []

    async def fake_concept(ledger, gateway, message, tools):
        seen.append({getattr(t, "__name__", "") for t in tools})
        return "ok"

    monkeypatch.setattr(hq.hq_agents, "run_concept", fake_concept)
    await hq.talk_to_concept(led, None, "Add the PCA9685 please", hq.Services(None))
    assert "raise_change_request" not in seen[-1]  # nothing agreed yet to change
    led.append(Actor.human(), "concept.approved", summary="s", decisions=[])
    await hq.talk_to_concept(led, None, "The real arm must use a PCA9685 servo driver.", hq.Services(None))
    assert "raise_change_request" in seen[-1]

    raise_change = hq.change_tool(led, "Headquarters", person_said="The real arm must use a PCA9685 servo driver.")
    result = raise_change("Use a PCA9685", "The owner chose the servo driver in the Headquarters chat.", "add_requirement", "",
                          _json.dumps({"text": "The real-arm interface targets a PCA9685 over I2C.", "verification": "Inspect the interface code."}))
    assert "waiting on the Changes screen" in result["status"] and "Nothing has changed yet" in result["status"]
    assert not led.state().of_type("requirement")  # the person still decides
    led.append(Actor.system("changes"), "change.reviewed", id=result["id"], review={"recommendation": "approve", "summary": "Fine.", "concerns": [], "missed": []})
    led.append(Actor.system("changes"), "change.triaged", id=result["id"], decision="person", reason="r")
    changes.approve(led, result["id"], "Yes.", Actor.human())
    requirement = led.state().of_type("requirement", "confirmed")[0]
    assert requirement["fields"]["person_said"] == "The real arm must use a PCA9685 servo driver."
