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
    assert not problem and ".venv" in python and (tmp_path / "p" / ".venv" / ".requirements-laptop").exists()  # the project's one environment
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


def test_several_parts_and_components_are_proposed_in_one_call(tmp_path):
    led = Ledger(tmp_path / "p")
    tools = {getattr(t, "__name__", ""): t for t in hq._lead_tools(led, None, "Mechanical", hq.Services(None))}
    assert "propose_part" not in tools
    out = tools["propose_parts"]([hq.PartProposal(number=f"M-0{i}", name=f"Link {i}", material="PLA", process="3D print", quantity=1, why="Arm link") for i in range(1, 4)]
                                 + [{"number": "M-01", "name": "Link 1", "material": "PLA", "process": "3D print", "quantity": 1, "why": "again"}])
    assert [r.get("id", "") != "" for r in out["results"]] == [True, True, True, False] and "already listed" in out["results"][3]["status"]
    assert len(led.state().of_type("part_proposal", "proposed")) == 3
    tools = {getattr(t, "__name__", ""): t for t in hq._lead_tools(led, None, "Electrical", hq.Services(None))}
    out = tools["propose_components"]([{"part_number": "PCA9685", "role": "Servo driver", "fills": "Servo driver"}, {"part_number": "Fuse, rating TBD", "role": "Protection"}])
    assert "id" in out["results"][0] and "error" in out["results"][1]


def test_tab_ideas_come_from_what_is_really_there_not_tool_names(tmp_path, monkeypatch):
    import json as _json

    from buildanything import tabideas

    app_root, project = tmp_path / "_app", tmp_path / "proj"
    app_root.mkdir()
    (project / "mechanics" / "results").mkdir(parents=True)
    for name in ("frame1.png", "frame2.png", "run.csv"):
        (project / "mechanics" / "results" / name).write_text("x", encoding="utf-8")
    app, led = Ledger(app_root), Ledger(project)
    led.append(Actor.system("software"), "software.ran", device="laptop", script="run.py", args=[], ok=True,
               made=["mechanics/results/frame1.png", "mechanics/results/frame2.png", "mechanics/results/run.csv", "firmware/laptop/app.py"])
    led.append(Actor.system("reception"), "tool.requested", department="Software", capability="Render the scene to images", why="to check the layout", service="Internal Service")
    modeller = {"id": "some-modeller", "name": "Some modeller", "departments": ["Mechanical"], "operations": [
        {"name": "list_documents", "description": "List open documents", "writes": False},
        {"name": "run_script", "description": "Run a script to inspect the design", "writes": True}]}
    prices = {"id": "prices", "name": "Prices", "departments": ["Mechanical"], "operations": [{"name": "get_price", "description": "Price of a part", "writes": False}]}
    monkeypatch.setattr(cloud, "store_for", lambda root: SimpleNamespace(listing=lambda: [{"approved": modeller}, {"approved": prices}]))
    ideas = tabideas.candidates(app_root, app, project, ["Mechanical", "Software"])
    assert [i["key"] for i in ideas] == ["files:mechanics/results", "conn:some-modeller"]  # a request to Reception is not a tool with output
    assert ideas[0]["department"] == "Mechanical" and "3 result files" in ideas[0]["could_show"]  # code files aren't results
    assert ideas[1]["service"] == "External Service" and "saves into the project" in ideas[1]["could_show"]  # a price lookup has nothing to see
    tabideas.dismiss(app, "proj", "files:mechanics/results", Actor.human())
    assert "files:mechanics/results" not in [i["key"] for i in tabideas.candidates(app_root, app, project, ["Mechanical", "Software"])]
    (app_root / "tabs" / "modeller").mkdir(parents=True)
    (app_root / "tabs" / "modeller" / "manifest.json").write_text(_json.dumps({"id": "modeller", "tool": "Some modeller", "status": "proposed"}), encoding="utf-8")
    assert tabideas.candidates(app_root, app, project, ["Mechanical", "Software"]) == []  # that tool has a tab now

def test_a_tab_declares_its_files_and_the_department_that_makes_them_is_briefed(tmp_path, monkeypatch):
    import json as _json

    from buildanything import tabs

    app_root, project = tmp_path / "_app", tmp_path / "rua"
    (project / "mechanics" / "cad" / "robot" / "meshes").mkdir(parents=True)
    (project / "mechanics" / "cad" / "robot" / "meshes" / "m000.stl").write_text("solid", encoding="utf-8")
    app = Ledger(app_root)
    reads = tabs.clean_reads([{"path": "mechanics/cad/**/*.stl", "what": "CAD meshes", "made_by": "Mechanical"},
                              {"path": "mechanics/sim/runs/*.json", "what": "recorded simulation runs", "made_by": "Software"}])
    status = tabs.reads_status(project, reads)
    assert [r["found"] for r in status] == [1, 0]
    try:
        tabs.clean_reads([{"path": "../secrets/*.txt", "made_by": "Software"}])
        raise AssertionError("a path outside the project was accepted")
    except tabs.TabError:
        pass
    (app_root / "tabs" / "mujoco").mkdir(parents=True)
    (app_root / "tabs" / "mujoco" / "manifest.json").write_text(_json.dumps({"id": "mujoco", "status": "approved", "approved_hash": "v1", "approved_title": "MuJoCo",
                                                                            "approved_department": "Mechanical", "approved_reads": reads, "summary": "The simulation."}), encoding="utf-8")
    monkeypatch.setattr(tabs, "read_files", lambda root, tab_id, which="proposed": {})
    monkeypatch.setattr(tabs, "_hash", lambda files: "v1")
    assert tabs.briefs_due(app_root, app, project, ["Mechanical", "Software"]) == []  # not shown on this project yet
    tabs.set_shown(app, "mujoco", "rua", True, Actor.human())
    due = tabs.briefs_due(app_root, app, project, ["Mechanical", "Software"])
    assert [(d["department"], d["tab"]) for d in due] == [("Software", "mujoco")]  # Mechanical's meshes exist; Software's runs don't

    led = Ledger(project)
    led.append(Actor.human(), "concept.approved", summary="s", decisions=[])
    monkeypatch.setattr(shape, "team", lambda state: ["Mechanical", "Software"])
    steps = []
    nxt = lambda: hq.next_action(led)
    step = next((s for s in [nxt()] if s and s.startswith("tab_brief:")), None)
    if step is None:  # other plan steps may come first in a bare project; the brief must still be offered
        due_steps = [f"tab_brief:{b['tab']}:{b['version']}:{b['department']}" for b in hq.tab_briefs(led, led.state())]
        step = due_steps[0]
    assert step == "tab_brief:mujoco:v1-0:Software" and hq.department_of(led, step) == "Software"  # -0: no run permission yet; granting it briefs again

    async def fake_lead(ledger, gateway, department, message, services, **kwargs):
        steps.append((department, message, kwargs.get("sent", {}).get("type")))
        return "ok"

    monkeypatch.setattr(hq, "run_lead", fake_lead)
    import asyncio
    asyncio.run(hq.run_step(led, None, step, hq.Services(None)))
    assert steps and steps[0][0] == "Software" and "mechanics/sim/runs/*.json" in steps[0][1] and steps[0][2] == "TASK"


def test_tab_scripts_use_the_projects_own_python_when_it_has_one(tmp_path):
    from buildanything import tabs

    project = tmp_path / "rua"
    project.mkdir()
    assert tabs.tab_python(project) == sys.executable
    info = tabs.python_packages(project, ["json", "surely_not_a_package_xyz"])
    assert "json" in info["has"] and "surely_not_a_package_xyz" in info["missing"] and "app's own Python" in info["python"]


def test_software_can_run_its_own_script_to_make_tab_files_once_allowed(tmp_path):
    root = tmp_path / "rua"
    base = root / "firmware" / "laptop"
    base.mkdir(parents=True)
    (base / "requirements.txt").write_text("", encoding="utf-8")
    (base / "sim.py").write_text(
        "import sys, json, pathlib\n"
        "out = pathlib.Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'run.json').write_text(json.dumps({'frames': 3}))\n"
        "print('saved run')\n", encoding="utf-8")
    led = Ledger(root)
    try:
        software.run_program(led, root, "laptop", "sim.py", ["--export", "../../mechanics/sim"], Actor.agent("software"))
        raise AssertionError("ran without the person's permission")
    except software.SoftwareError as exc:
        assert "Run code and tests" in str(exc)
    software.allow_autotest(led, "laptop", True, Actor.human())
    result = software.run_program(led, root, "laptop", "sim.py", ["--export", "../../mechanics/sim"], Actor.agent("software"))
    assert result["ok"] and "saved run" in result["output"] and "mechanics/sim/run.json" in result["files_made_or_changed"]
    for bad in ("../../outside.py", "requirements.txt"):
        try:
            software.run_program(led, root, "laptop", bad, [], Actor.agent("software"))
            raise AssertionError(f"{bad} was run")
        except software.SoftwareError:
            pass
    assert [e["data"]["ok"] for e in led.events() if e["kind"] == "software.ran"] == [True]


def test_simulation_is_a_department_with_its_own_program_and_tools(tmp_path, monkeypatch):
    from buildanything import agent_specs, traceability

    assert "Simulation" in shape.DEPARTMENT_TYPES and agent_specs.SPECS["lead_simulation"].area == "Simulation"
    led = Ledger(tmp_path / "p")
    monkeypatch.setattr(shape, "team", lambda state: ["Mechanical", "Software", "Simulation"])
    sim_tools = {getattr(t, "__name__", "") for t in hq._lead_tools(led, None, "Simulation", hq.Services(None))}
    assert {"write_sim_code", "run_simulation", "propose_test_result"} <= sim_tools and "write_code" not in sim_tools
    sw_tools = {getattr(t, "__name__", ""): t for t in hq._lead_tools(led, None, "Software", hq.Services(None))}
    assert "run_program" not in sw_tools and "write_sim_code" not in sw_tools
    import asyncio
    assert "Simulation department's code" in asyncio.run(sw_tools["write_code"]("simulation", "anything", ""))["error"]
    plan = agents.PlanOut(requirements=[], tasks=[agents.TaskOut(key="S1", department="Simulation", title="Simulate the handover", weeks=1, depends_on=[])])
    assert agents.plan_problems(plan, ["Mechanical", "Software", "Simulation"]) == []


def test_a_simulation_result_is_proposed_and_only_a_person_records_it(tmp_path):
    from buildanything import traceability

    led = Ledger(tmp_path / "p")
    (tmp_path / "p" / "simulation" / "results" / "run1").mkdir(parents=True)
    (tmp_path / "p" / "simulation" / "results" / "run1" / "stop.csv").write_text("t,d\n0,0.1\n", encoding="utf-8")
    test = led.propose(Actor.agent("controller"), "test", key="T4", covers=["R12"], title="Stops at 10 cm", owner="Simulation", procedure="p", pass_if="stop distance at least 0.10 m",
                       metric="stop distance", unit="m", min=0.10, max=None, uses=[])
    led.append(Actor.human(), "record.confirmed", id=test)
    proposal = traceability.propose_result(led, "T4", 0.093, "", "Ran the approach scenario 20 times in the physics simulation; the closest stop was 0.093 m.",
                                           ["simulation/results/run1/stop.csv", "../outside.txt"], Actor.agent("simulation"))
    waiting = traceability.proposals(led)[test]
    assert waiting[0]["files"] == ["simulation/results/run1/stop.csv"]  # nothing outside the project
    assert not [e for e in led.events() if e["kind"] == "test.result"]  # an agent can't record a result itself
    outcome = traceability.accept_proposal(led, proposal, Actor.human())
    assert outcome == "failed"  # the app judges the number: 0.093 m is under the 0.10 m limit
    result = next(e for e in led.events() if e["kind"] == "test.result")
    assert result["actor"]["kind"] == "human" and "stop.csv" in result["data"]["evidence"]
    assert traceability.proposals(led) == {}



def test_programs_can_be_written_in_any_language_but_not_binaries_or_secrets():
    from buildanything import firmware as fw

    allowed = ["app/src/main/java/com/air/MainActivity.kt", "app/src/main/AndroidManifest.xml", "build.gradle.kts", "settings.gradle", "gradlew", "gradlew.bat",
               "Sources/App/Main.swift", "Program.cs", "game/scripts/pointer.lua", "analysis/plot.m", "web/index.html", "Makefile", "app.py"]
    refused = [".env", "config/client_secret.json", "app/build/intermediates/x.kt", "res/icon.png", "release.keystore", "lib/x.jar", "node_modules/a/index.js", "README"]
    assert all(fw.HOST_PATH.match(x) for x in allowed), [x for x in allowed if not fw.HOST_PATH.match(x)]
    assert not any(fw.HOST_PATH.match(x) for x in refused), [x for x in refused if fw.HOST_PATH.match(x)]
    assert not fw.HOST_TEXT.search("MainActivity.kt") and fw.HOST_TEXT.search("AndroidManifest.xml")  # Kotlin gets described blocks; XML is written as it is
    kotlin = fw.render(fw.FileOut(path="MainActivity.kt", owner="Software", blocks=[fw.BlockOut(title="Activity", description="Reads the gyroscope and sends samples over Bluetooth.", code="class MainActivity {}")]))
    assert kotlin.startswith("/* @block ") and fw.parse(kotlin)["blocks"][0]["title"] == "Activity"


def test_a_program_runs_its_build_script_or_a_build_tool_and_hears_when_one_is_missing(tmp_path):
    root = tmp_path / "proj"
    base = root / "firmware" / "phone"
    base.mkdir(parents=True)
    (base / "build.bat").write_text("@echo off\r\necho built> out.txt\r\necho build ok\r\n", encoding="utf-8")
    (base / "notes.txt").write_text("not a script", encoding="utf-8")
    led = Ledger(root)
    software.allow_autotest(led, "phone", True, Actor.human())
    ran = software.run_command(led, root, "phone", ["build.bat"], Actor.agent("software"))
    assert ran["ok"] and "build ok" in ran["output"] and "firmware/phone/out.txt" in ran["files_made_or_changed"]
    missing = software.run_command(led, root, "phone", ["surely-not-a-build-tool-xyz", "build"], Actor.agent("software"))
    assert not missing["ok"] and missing["missing_program"] == "surely-not-a-build-tool-xyz" and "request_tool" in missing["output"]
    for bad in (["notes.txt"], ["cmd", "/c", "del", "x"], ["../../outside.bat"]):
        try:
            software.run_command(led, root, "phone", bad, Actor.agent("software"))
            raise AssertionError(f"{bad} was run")
        except software.SoftwareError:
            pass
    led.append(Actor.system("software"), "software.build_command", device="phone", command=["build.bat"])
    assert software.build_command(led, "phone") == ["build.bat"]
    assert "isn't written in Python" in software.run_tests(led, root, "phone", True, Actor.human())["lines"][0]


async def test_a_queued_message_is_told_it_waits_its_turn(tmp_path, monkeypatch):
    led = Ledger(tmp_path / "p")
    monkeypatch.setattr(shape, "team", lambda state: ["Software"])
    monkeypatch.setattr(shape, "on_team", lambda state, d: True)

    async def fake_lead(*args, **kwargs):
        return "ok"

    monkeypatch.setattr(hq, "run_lead", fake_lead)
    board = jobs._JOBS.setdefault(str(led.root), {})
    board["job_z"] = jobs.Job(id="job_z", department="Software", title="Answer a change", items=[], touches={jobs.UNKNOWN}, clone=0, status="running")
    try:
        await hq.talk_to_department(led, None, "Software", "Please check Bluetooth support.", hq.Services(None))
    finally:
        board.pop("job_z", None)
    said = [m["text"] for m in hq.thread(led.events(), "Software")]
    assert any("finishing another job" in t for t in said)


def test_in_plan_departments_do_the_work_behind_the_persons_items_once_the_plan_is_agreed(tmp_path, monkeypatch):
    led = Ledger(tmp_path / "p")
    monkeypatch.setattr(shape, "team", lambda state: ["Software"])
    monkeypatch.setattr(hq, "MAX_DRIVES", 6)  # the shared test setup switches driving off
    monkeypatch.setattr(gates, "current_gate", lambda state: "G2")
    monkeypatch.setattr(gates, "phase", lambda state, gate: "G2")
    monkeypatch.setattr(gatereport, "blockers", lambda gate, state, ledger: [("Software", "Build checks that belong to Build")])
    monkeypatch.setattr(gates, "criteria_view", lambda state, gate: [{"index": 0, "text": "Specify the message format between phone and laptop.", "met": False}])
    task = led.propose(Actor.agent("controller"), "task", key="S1", department="Software", title="Build the phone and laptop message format", weeks=1, depends_on=[], start_week=0)
    assert hq._drive(led, led.state(), set()) is None  # the plan itself isn't agreed yet
    led.append(Actor.human(), "record.confirmed", id=task)
    work = hq._owned_work(led, led.state(), "G2")
    assert work == {"Software": ["Make this work and show how you checked it, so the person can confirm it: Specify the message format between phone and laptop."]}  # no Build work in Plan
    assert (hq._drive(led, led.state(), set()) or "").startswith("drive:G2:Software:")


def test_internal_service_can_define_and_install_a_toolchain_with_the_persons_approval(tmp_path, monkeypatch):
    from buildanything import connectors as conn, services

    app = Ledger(tmp_path / "_app")
    raw = {"id": "some-sdk", "group": "platform", "name": "Some SDK", "what": "Adds packages for building the phone app.", "check_kind": "exe", "check_value": "some-sdk-check",
           "get_kind": "command", "get_value": ["python", "-c", "print('installed')"]}
    entry = services.propose_tool(app, raw, "The phone app needs it to build.", Actor.agent("internal_service"))
    services.load_custom(app)
    assert services.entry_for("some-sdk") is None  # nothing joins the list until the person approves it
    services.decide_tool(app, "some-sdk", True, "", Actor.human())
    assert services.entry_for("some-sdk")["get"] == {"command": ["python", "-c", "print('installed')"]}
    step = services.plan("some-sdk", None, None)
    assert step["steps"][0][0].lower().endswith(("python.exe", "python")) and step["steps"][0][1:] == ["-c", "print('installed')"]
    for bad in (["powershell", "-c", "iwr x"], ["C:/tools/x.exe"], ["not-a-real-installer-xyz", "add"]):
        try:
            services.validate_tool({**raw, "id": "other-sdk", "get_value": bad})
            raise AssertionError(f"{bad} was accepted")
        except services.ServiceError:
            pass
    services._custom.clear()


def test_programs_installed_after_the_app_started_are_found(monkeypatch):
    from buildanything import connectors as conn

    monkeypatch.setattr(conn, "_registry_environment", lambda: {"PATH": r"C:\NewTool\bin", "ANDROID_HOME": r"C:\Android\Sdk", "MY_TOKEN_PATH": "x"})
    monkeypatch.setenv("PATH", r"C:\Old\bin")
    monkeypatch.delenv("ANDROID_HOME", raising=False)
    env = conn.clean_environment()
    assert env["PATH"].split(";")[0] == r"C:\Old\bin" and r"C:\NewTool\bin" in env["PATH"].split(";")  # what was set stays first
    assert env["ANDROID_HOME"] == r"C:\Android\Sdk"
