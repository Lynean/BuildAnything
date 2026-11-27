"""Fixes from the sixth review run (the desk RC car): the owner's change list of 13 items."""
import sys

from buildanything import debugging, software
from buildanything.ledger import Actor, Ledger


def test_a_troubleshooting_item_closes_itself_when_a_later_run_is_fine(tmp_path, monkeypatch):
    led = Ledger(tmp_path / "p")
    base = led.root / "firmware" / "simulation"
    base.mkdir(parents=True)
    (base / "sim.py").write_text("print('hi')\n")
    stale = debugging.open_or_repeat(led, "Tests failed", "Ran 0 tests in 0.000s\nOK", "code", Actor.system("software"), device="simulation", where="simulation")["id"]
    other = debugging.open_or_repeat(led, "Tests failed", "FAIL: test_x", "code", Actor.system("software"), device="phone", where="phone")["id"]
    monkeypatch.setattr(software, "device_python", lambda root, device, install=False: (sys.executable, ""))
    software.run_tests(led, led.root, "simulation", True, Actor.human())
    status = {s["id"]: s["status"] for s in debugging.sessions(led).values()}
    assert status[stale] == "closed" and status[other] == "open"  # only the same program's item


def _board(led, name, board="esp32-c6-devkitc-1"):
    base = led.root / "firmware" / name
    (base / "src").mkdir(parents=True)
    (base / "platformio.ini").write_text(f"[env:device]\nplatform = espressif32\nboard = {board}\n")
    return base


def test_two_folders_for_one_board_are_flagged_and_one_can_be_retired(tmp_path):
    from buildanything import firmware

    led = Ledger(tmp_path / "p")
    _board(led, "esp32-c6")
    _board(led, "desk-rc-car")
    _board(led, "uno", board="uno")
    (led.root / "bench" / "desk-rc-car").mkdir(parents=True)
    assert firmware.duplicate_boards(led.root) == [["desk-rc-car", "esp32-c6"]]
    assert firmware.duplicate_boards(led.root, {"desk-rc-car"}) == []  # declared a second physical board
    assert "error" in firmware.retire_device(led, led.root, "uno", "it is the only one of its kind here", Actor.agent("electrical"))
    done = firmware.retire_device(led, led.root, "desk-rc-car", "stray copy; esp32-c6 has the building code", Actor.agent("electrical"))
    assert done["kept"] == ["esp32-c6"] and firmware.devices(led.root) == ["esp32-c6", "uno"]
    assert not (led.root / "bench" / "desk-rc-car").exists() and (led.root / done["moved_to"] / "bench").is_dir()
    assert firmware.duplicate_boards(led.root) == []


def test_tool_requests_list_this_project_first_and_close_when_the_job_is_done(tmp_path):
    from buildanything import services

    app = Ledger(tmp_path / "_app")
    services.ask(app, "arm", "Software", "Run MuJoCo tests", "Execute the simulator and regression tests on the arm")
    services.ask(app, "car", "Mechanical", "Repair the URDF exporter", "The exporter raises an error on the joints of the export copy")
    services.ask(app, "car", "Electrical", "Edit message definitions", "Apply the approved change to the drive message")
    assert [a["project"] for a in services.asks(app, "car")] == ["car", "car", "arm"]
    assert services.settle_asks(app, "car", "Mechanical", "finished") == 1
    assert [a["department"] for a in services.asks(app)] == ["Software", "Electrical"]


def test_the_story_shows_only_what_was_said_and_decided_unless_asked(tmp_path):
    from buildanything import story

    led = Ledger(tmp_path / "p")
    led.append(Actor.human(), "project.created", name="Car", aim="A small car", scope="")
    led.append(Actor.system("ci"), "ci.result", stage="Firmware:esp32", key="k", ok=False, problems=["x"], ran=True)
    led.append(Actor.system("t"), "dept.message", department="Electrical", role="agent", text="The firmware builds.", details="")
    talk = story.steps(led, automatic=False)
    assert [s["text"] for s in talk][-1] == "The firmware builds." and not any(s["from"] == "Checks" for s in talk)
    assert any(s["from"] == "Checks" for s in story.steps(led))


def test_bought_parts_need_a_cad_body_unless_loose_or_waived(tmp_path):
    from buildanything import designneeds, gatereport

    led = Ledger(tmp_path / "p")
    for number, name, process in [("M-01", "Main chassis", "3D print"), ("M-09", "DC gearmotors", "Bought"), ("M-13", "Battery pack", "Bought"),
                                  ("M-16", "Wiring and connectors", "Bought"), ("M-11", "ESP32-C6 board", "Existing purchased kit")]:
        led.append(Actor.human(), "record.confirmed", id=led.propose(Actor.human(), "part", number=number, name=name, material="", process=process, qty=1, revision="A"))
    led.append(Actor.agent("mechanical"), "cad.linked", part="M-01", tool="Fusion", reference="x", evidence="seen")
    found = [t for _, t in gatereport._mechanical_design(led, led.state())]
    bought = next(t for t in found if "bought or existing" in t)
    assert "M-09" in bought and "M-13" in bought and "M-11" in bought and "M-16" not in bought
    assert not any("no CAD model yet" in t for t in found)  # the printed chassis is modelled
    designneeds.waive_model(led, "M-13", "The battery sits in a bought holder already modelled", Actor.human())
    assert "M-13" not in next(t for _, t in gatereport._mechanical_design(led, led.state()) if "bought or existing" in t)


def test_a_tab_written_for_another_projects_layout_goes_to_its_service_agent_not_the_departments(tmp_path, monkeypatch):
    from buildanything import tabs

    project = tmp_path / "car"
    (project / "mechanics" / "cad" / "robot" / "Desk").mkdir(parents=True)
    (project / "mechanics" / "cad" / "robot" / "Desk" / "Desk.urdf").write_text("<robot/>")
    tab = {"id": "fusion", "approved_hash": "h", "intact": True, "projects": ["car"], "approved_title": "Fusion", "by": "external_service", "approved_department": "Mechanical",
           "approved_reads": [{"path": "mechanics/cad/robot/robot.urdf", "what": "URDF", "made_by": "Mechanical"}, {"path": "mechanics/sim/run.json", "what": "replay", "made_by": "Simulation"}]}
    monkeypatch.setattr(tabs, "listing", lambda app_root, app_ledger: [tab])
    wrong = tabs.mismatches(tmp_path, None, project)
    assert wrong[0]["service"] == "External Service" and wrong[0]["reads"][0]["project_has"] == ["mechanics/cad/robot/Desk/Desk.urdf"]
    due = tabs.briefs_due(tmp_path, None, project, ["Mechanical", "Simulation"])
    assert [b["department"] for b in due] == ["Simulation"]  # nothing like run.json exists, so only that one is a real gap


def test_a_tool_result_follow_up_gets_a_short_context(tmp_path, monkeypatch):
    from buildanything import headquarters as hq

    led = Ledger(tmp_path / "p")
    full = {"briefing": "B" * 9000, "department_state": {"parts": [1], "project_files": "F" * 6000, "other_departments_latest": "O" * 11000},
            "requests": ["R"] * 50, "earlier_discussion": "E" * 5000, "skills": "s", "services": "v", "guidance_from_reception": "g"}
    monkeypatch.setattr(hq, "_shared_context", lambda ledger, department, services: dict(full))
    short = hq._follow_up_context(led, "Mechanical", None)
    assert short["department_state"] == {"parts": [1]} and short["requests"] == [] and len(short["briefing"]) < 400
    assert short["services"] == "v" and short["skills"] == "s"  # still knows how to use its tools


def test_simulation_runs_are_listed_newest_first_with_their_recording_and_results(tmp_path):
    import json, os, time
    from buildanything import simruns

    base = tmp_path / "simulation" / "results"
    old, new = base / "first", base / "second"
    for folder in (old, new):
        folder.mkdir(parents=True)
        (folder / "trajectory.gif").write_bytes(b"GIF89a")
        (folder / "summary.json").write_text(json.dumps({"speed_m_s": 0.37}))
        (folder / "assumptions.json").write_text(json.dumps(["no tires"]))
        (folder / "straight.csv").write_text("t,x\n")
    past = time.time() - 100
    for f in old.iterdir():
        os.utime(f, (past, past))
    found = simruns.runs(tmp_path)
    assert [r["name"] for r in found] == ["second", "first"]
    assert found[0]["recordings"] == ["simulation/results/second/trajectory.gif"] and found[0]["summary"] == {"speed_m_s": 0.37}
    assert found[0]["assumptions"] == ["no tires"] and found[0]["tables"] == ["simulation/results/second/straight.csv"]


def test_a_write_keeps_the_blocks_that_pass_and_holds_back_only_the_failed_ones(tmp_path):
    from buildanything import firmware as fw

    led = Ledger(tmp_path / "p")
    desc = "Does one clear thing for the program, described plainly."
    old = fw.FileOut(path="sim.py", owner="Software", blocks=[fw.BlockOut(title="Setup", description=desc, code="X = 1\n"), fw.BlockOut(title="Drive", description=desc, code="def drive():\n    return 1\n")])
    fw.write_files(led.root, "simulation", [old], host=True)
    new = fw.FileOut(path="sim.py", owner="Software", blocks=[
        fw.BlockOut(title="Setup", description=desc, code="X = 2\n"),
        fw.BlockOut(title="Drive", description=desc, code="def drive():\n    return 2\n", based_on=["note:pag_missing"]),
        fw.BlockOut(title="Brake", description=desc, code="def brake():\n    return 0\n", based_on=["note:pag_missing2"]),
    ])
    problems = fw.citation_problems([new], set())
    kept, held = fw.salvage(led.root, "simulation", [new], problems)
    blocks = {b.title: b.code for b in kept[0].blocks}
    assert blocks["Setup"] == "X = 2\n" and blocks["Drive"].strip() == "def drive():\n    return 1" and "Brake" not in blocks  # new Setup kept, failed edit keeps the saved Drive, failed new Brake left out
    assert len(held) == 2 and not fw.citation_problems(kept, set())
    # a problem that names no file, such as an interface rule, still holds back everything
    assert fw.salvage(led.root, "simulation", [new], ["The middleware header changed."]) == ([], ["The middleware header changed."])


def test_office_hours_hold_new_work_until_the_person_is_done(tmp_path, monkeypatch):
    from buildanything import headquarters as hq

    monkeypatch.setattr(hq, "QUIET_SECONDS", 60)
    led = Ledger(tmp_path / "p")
    led.append(Actor.human(), "project.created", name="Car", aim="A car", scope="")
    now = hq._seconds(led.events()[-1]["at"])
    assert 59 <= hq.outbox_wait(led.events(), now + 1) <= 60  # just acted: wait for the quiet spell
    assert hq.outbox_wait(led.events(), now + 61) == 0  # quiet for a minute: go
    led.append(Actor.human(), "hq.office", hold=True)
    led.append(Actor.human(), "record.confirmed", id="x")
    assert hq.outbox_wait(led.events(), now + 600) is None  # held until Send
    led.append(Actor.human(), "hq.send_now")
    assert hq.outbox_wait(led.events(), now + 1) == 0
    led.append(Actor.human(), "hq.office", hold=False)
    led.append(Actor.human(), "record.confirmed", id="y")
    assert hq.outbox_wait(led.events(), hq._seconds(led.events()[-1]["at"]) + 1) > 0
    led.append(Actor.human(), "hq.paused", paused=True)  # pausing always goes through at once
    assert hq.outbox_wait(led.events(), hq._seconds(led.events()[-1]["at"]) + 1) == 0


def test_results_for_a_department_are_delivered_together_in_one_turn(tmp_path, monkeypatch):
    import asyncio
    from buildanything import headquarters as hq

    led = Ledger(tmp_path / "p")
    hq.queue_followup(led, "Mechanical", "Script 1 worked: 3 joints.")
    hq.queue_followup(led, "Mechanical", "Script 2 failed: no body.")
    step = hq.next_action(led)
    assert step.startswith("followup:Mechanical:") and hq.department_of(led, step) == "Mechanical"
    seen = []

    async def lead(ledger, gateway, department, text, services, follow_up=False, **kwargs):
        seen.append((department, text, follow_up))
        return ""

    monkeypatch.setattr(hq, "run_lead", lead)
    asyncio.run(hq.run_step(led, None, step, None))
    assert len(seen) == 1 and "2 results" in seen[0][1] and "Script 2 failed" in seen[0][1] and seen[0][2]
    assert hq.next_action(led) is None or not hq.next_action(led).startswith("followup:")


def test_a_project_can_be_disconnected_and_connected_again_without_touching_its_folder(tmp_path):
    from fastapi.testclient import TestClient
    from buildanything import api, providers

    root = tmp_path / "projects"
    root.mkdir()
    client = TestClient(api.create_app(root, secrets=providers.MemoryStore()))
    pid = client.post("/api/projects", json={"name": "Car", "aim": "A car", "scope": "S"}).json()["id"]
    assert client.post(f"/api/projects/{pid}/disconnect", json={"off": True}).json()["disconnected"]
    assert [p["id"] for p in client.get("/api/projects").json()] == []
    assert (root / pid / "ledger" / "events.jsonl").exists()  # still on disk
    assert client.get("/api/projects?include_disconnected=true").json()[0]["disconnected"]
    client.post(f"/api/projects/{pid}/disconnect", json={"off": False})
    assert [p["id"] for p in client.get("/api/projects").json()] == [pid]


def test_a_department_can_offer_the_person_choices(tmp_path):
    from buildanything import headquarters as hq, protocol
    from buildanything.agent_specs import SPECS, system_prompt

    led = Ledger(tmp_path / "p")
    hq._say(led, "Mechanical", "agent", "Which caster?", choices=["Ball caster", "Swivel wheel", "", "a", "b", "c"])
    assert hq.thread(led.events(), "Mechanical")[-1]["choices"] == ["Ball caster", "Swivel wheel", "a", "b"]
    assert "choices" in protocol.Report.model_fields
    assert "numbered list" in system_prompt(SPECS["concept"]) and "in choices" in system_prompt(SPECS["lead_mechanical"])
