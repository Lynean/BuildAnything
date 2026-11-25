import pytest

from buildanything import changes as ch, debugging, firmware as fw, flashing, gatereport as gr, gates, headquarters as hq, links, mechanical as mech, overview as ov, traceability as tr
from buildanything.ledger import Actor, Ledger, PermissionDenied

HUMAN = Actor.human("Ann")


@pytest.fixture
def led(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.append(HUMAN, "project.created", name="Tracker", aim="A", scope="S")
    for gate in ("G0", "G1", "G2", "G3"):
        ledger.append(HUMAN, "gate.approved", gate=gate)
    # What each gate needs from a real design has its own tests in test_fixes.py.
    for department in ("Electrical", "Mechanical"):
        ledger.append(HUMAN, "needs.none", department=department, reason="Not part of this test.")
    return ledger


def requirement(led, key="R1"):
    led.append(HUMAN, "record.confirmed", id=led.propose(Actor.agent("controller"), "requirement", key=key, text=f"{key} text", verification="Bench"))


def test_record(led, key="T1", covers=("R1",), uses=()):
    record = led.propose(Actor.agent("controller"), "test", key=key, covers=list(covers), title="Check", owner="Mechanical", procedure="Do it", pass_if="Works", metric=None, unit=None, min=None, max=None, uses=list(uses))
    led.append(HUMAN, "record.confirmed", id=record)
    return record


test_record.__test__ = False


def component(led, number="SG90"):
    record = led.propose(HUMAN, "component", part_number=number, role="Servo")
    led.append(HUMAN, "record.confirmed", id=record)
    return record


def blockers(led, gate):
    return " ".join(gates.missing(gate, led.state(), led))


def test_g4_lists_real_design_gaps(led):
    requirement(led)
    component(led)
    text = blockers(led, "G4")
    assert "Upload documents for: SG90" in text and "Every requirement needs a test" in text


def test_g4_passes_when_the_design_is_complete(led):
    requirement(led)
    test_record(led)
    assert blockers(led, "G4") == ""
    gates.approve(led, "G4", HUMAN)
    assert "G4" in led.state().gates_approved


def test_g4_counts_open_part_checks_and_undecided_changes(led):
    requirement(led)
    test_record(led)
    mech.add_part(led, {"number": "P1", "name": "B", "material": "PLA", "process": "print", "qty": 1}, HUMAN)
    mech.revise(led, "P1", {"material": "PETG"}, "Stronger", HUMAN)
    ch.propose(led, "Bigger", "It needs more room", "other", "", {}, HUMAN)
    text = blockers(led, "G4")
    assert "open part checks" in text and "1 open change request" in text


def test_only_a_person_can_approve_later_gates(led):
    requirement(led)
    test_record(led)
    with pytest.raises(PermissionDenied):
        gates.approve(led, "G4", Actor.agent("controller"))
    with pytest.raises(PermissionDenied):
        gates.approve(led, "G4", Actor.delegated())


def g5(led):
    requirement(led)
    test_record(led)
    gates.approve(led, "G4", HUMAN)


def test_g5_needs_orders_arrived_and_parts_released(led):
    g5(led)
    order = led.propose(HUMAN, "order", supplier="Shop", items="servo", status="Ordered")
    led.append(HUMAN, "record.confirmed", id=order)
    mech.add_part(led, {"number": "P1", "name": "B", "material": "PLA", "process": "print", "qty": 1}, HUMAN)
    text = blockers(led, "G5")
    assert "1 order hasn't arrived: Shop" in text and "Release the current revision of: P1" in text
    led.append(HUMAN, "order.arrived", id=order)
    assert "haven't arrived" not in blockers(led, "G5")


def board(led, device="node", text="int a;"):
    fw.write_files(led.root, device, [fw.FileOut(path="src/main.cpp", owner="Electrical", blocks=[fw.BlockOut(title="Main", description="Runs the main control loop.", code=text)])])
    (led.root / "firmware" / device / "platformio.ini").write_text("[env:device]\nboard = b\n")


def flashed(led, device="node"):
    sha = flashing.source_hash(led.root / "firmware" / device)
    led.append(Actor.system("flashing"), "flash.prepared", id="f1", device=device, source_sha=sha)
    led.append(Actor.system("flashing"), "flash.finished", id="f1", device=device, outcome="running", backup=None, lines=[])


def test_g5_needs_current_code_on_each_board(led):
    g5(led)
    board(led)
    assert "Flash node" in blockers(led, "G5")
    flashed(led)
    assert "Flash node" not in blockers(led, "G5")
    board(led, text="int changed;")
    assert "code has changed since" in blockers(led, "G5")


def test_g5_needs_problems_closed(led):
    g5(led)
    debugging.open_or_repeat(led, "Build failed", "boom", "code", HUMAN, device="n", where="n")
    links.open_issue(led, "No data", ["a", "b"], None, "r", HUMAN)
    text = blockers(led, "G5")
    assert "open debugging problems" in text and "open link issues" in text


def g6(led):
    g5(led)
    gates.approve(led, "G5", HUMAN)


def test_g6_requires_every_requirement_proven(led):
    g6(led)
    assert "haven't been tested: R1" in blockers(led, "G6")
    with pytest.raises(gates.GateError):
        gates.approve(led, "G6", HUMAN)
    tr.record_result(led, tr.view(led)["tests"][0]["id"], None, "failed", "It did not track at all", HUMAN)
    text = blockers(led, "G6")
    assert "failed on the real device" in text and "It did not track at all" in text  # Sent to the owner with what the person saw.
    owner = tr.view(led)["tests"][0]["owner"]
    led.append(HUMAN, "report.filed", department=owner, type="DONE", done="Fixed the tracking.")
    assert "Test again on the device" in blockers(led, "G6")
    tr.record_result(led, tr.view(led)["tests"][0]["id"], None, "passed", "Tracked for a full minute", HUMAN)
    assert blockers(led, "G6") == ""
    gates.approve(led, "G6", HUMAN)
    assert gates.current_gate(led.state()) is None


def test_a_design_change_after_passing_blocks_g6_again(led):
    mech.add_part(led, {"number": "P1", "name": "B", "material": "PLA", "process": "print", "qty": 1}, HUMAN)
    requirement(led)
    test_record(led, uses=("P1",))
    gates.approve(led, "G4", HUMAN) if False else None
    led.append(HUMAN, "gate.approved", gate="G4")
    led.append(HUMAN, "gate.approved", gate="G5")
    tr.record_result(led, tr.view(led)["tests"][0]["id"], None, "passed", "Tracked for a full minute", HUMAN)
    assert blockers(led, "G6") == ""
    mech.revise(led, "P1", {"material": "PETG"}, "Stronger", HUMAN)
    assert "need testing again: R1" in blockers(led, "G6")


def test_report_says_what_is_blocking_and_proven(led):
    requirement(led)
    test_record(led)
    tr.record_result(led, tr.view(led)["tests"][0]["id"], None, "passed", "Tracked for a full minute", HUMAN)
    text = gr.report(led, "G4")
    assert "# Check: Check the whole design before buying anything" in text and "1 of 1 requirements are proven" in text and "| R1 | R1 text | T1 | Proven |" in text and "**Ready.** Continue to Build" in text


def test_report_shows_accepted_risks_and_decisions_made_for_you(led):
    finding = led.propose(Actor.agent("verifier"), "finding", run=1, severity="high", safety=True, text="No emergency stop", related=[])
    gates.decide_finding(led, finding, "accept", "Bench only, and hands stay clear.", HUMAN)
    mech.add_part(led, {"number": "P1", "name": "B", "material": "PLA", "process": "print", "qty": 1, "mass_g": 10.0}, HUMAN)
    change = ch.propose(led, "Correct mass", "It weighs more", "part", "P1", {"mass_g": 12.0}, Actor.agent("mechanical"))
    led.append(Actor.system("headquarters"), "change.auto_approved", id=change, reason="Routine correction.", impact=[])
    text = gr.report(led, "G4")
    assert "No emergency stop" in text and "Bench only" in text and "(safety)" in text and "Change 1: Correct mass" in text and "Routine correction" in text


def test_approving_freezes_the_page_and_saves_a_file(led):
    requirement(led)
    test_record(led)
    gates.approve(led, "G4", HUMAN)
    frozen = gr.saved_report(led, "G4")
    requirement(led, "R2")
    assert "R2" not in gr.saved_report(led, "G4") and frozen.startswith("# Check")
    assert (led.root / "reports" / "Stage-5-Check.md").read_text(encoding="utf-8") == frozen


def test_gate_list_shows_progress(led):
    requirement(led)
    items = {g["id"]: g for g in gr.gate_list(led)}
    assert items["G3"]["state"] == "approved" and items["G3"]["by"] == "Ann" and items["G4"]["state"] == "current" and items["G4"]["missing"] and items["G5"]["state"] == "later"


def test_a_ready_gate_reaches_the_review_queue(led):
    requirement(led)
    test_record(led)
    for department in hq.DEPARTMENTS:
        led.append(Actor.system("headquarters"), "hq.finished", step=f"open:{department}", label="x")
    item = next(i for i in ov.review_queue(led) if i["kind"] == "gate")
    assert item["gate"] == "G4"


def test_headquarters_says_what_the_next_gate_needs(led):
    requirement(led)
    for department in hq.DEPARTMENTS:
        led.append(Actor.system("headquarters"), "hq.finished", step=f"open:{department}", label="x")
    led.append(Actor.system("headquarters"), "hq.finished", step="plan_tests", label="x")
    need = hq.needs(led)[0]
    assert need["kind"] == "gate_waiting" and "To finish Check" in need["text"]


def test_order_arrival_is_a_person_s_call(led):
    order = led.propose(HUMAN, "order", supplier="Shop", items="x", status="Ordered")
    with pytest.raises(PermissionDenied):
        led.append(Actor.agent("procurement"), "order.arrived", id=order)
