import pytest

from buildanything import changes as ch, depgraph, firmware as fw, headquarters as hq, mechanical as mech, overview as ov, traceability as tr, workspace as ws
from buildanything.ledger import Actor, Ledger, PermissionDenied

HUMAN = Actor.human()


def planned(covers=("R1",), metric="Tracking distance", low=1.0, high=None, uses=("P1",), title="Distance test"):
    return tr.PlannedTest(covers=list(covers), title=title, owner="Mechanical", procedure="Walk away and measure.", pass_if="Tracks at 1 m or more", metric=metric, unit="m", min=low, max=high, uses=list(uses))


class Gateway:
    def __init__(self, *plans):
        self.plans, self.calls = list(plans), []

    async def run(self, *, agent, values, output_type=str, **_):
        self.calls.append((agent, values))
        if agent == "test_planner":
            return self.plans.pop(0)
        return f"{agent} ok"


@pytest.fixture
def led(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.append(HUMAN, "project.created", name="T", aim="A", scope="S")
    mech.add_part(ledger, {"number": "P1", "name": "Bracket", "material": "PLA", "process": "print", "qty": 1, "mass_g": 10.0, "unit_cost": 1.0}, HUMAN)
    return ledger


def requirement(led, key="R1", text="Tracks at 1 m"):
    record = led.propose(Actor.agent("controller"), "requirement", key=key, text=text, verification="Bench")
    led.append(HUMAN, "record.confirmed", id=record)
    return record


async def ready(led, *plans):
    await tr.plan_tests(led, Gateway(*plans))
    tr.approve_all(led, HUMAN)
    return tr.view(led)["tests"][0]["id"]


def status(led, key="R1"):
    return next(r for r in tr.view(led)["rows"] if r["key"] == key)["status"]


def test_a_requirement_with_no_test_is_visible(led):
    requirement(led)
    assert status(led) == "no_test" and tr.view(led)["counts"]["no_test"] == 1


@pytest.mark.asyncio
async def test_the_status_moves_from_review_to_proven(led):
    requirement(led)
    await tr.plan_tests(led, Gateway(tr.TestPlan(tests=[planned()], cannot_test=[])))
    assert status(led) == "needs_approval"
    tr.approve_all(led, HUMAN)
    assert status(led) == "not_tested"
    test = tr.view(led)["tests"][0]["id"]
    assert tr.record_result(led, test, 1.4, "", "Tape measure on the bench floor", HUMAN) == "passed"
    assert status(led) == "proven"


@pytest.mark.asyncio
async def test_code_judges_numbers_whatever_is_claimed(led):
    requirement(led)
    test = await ready(led, tr.TestPlan(tests=[planned()], cannot_test=[]))
    assert tr.record_result(led, test, 0.6, "passed", "Tape measure on the bench floor", HUMAN) == "failed"
    assert status(led) == "failed"


@pytest.mark.asyncio
async def test_yes_or_no_tests_need_an_outcome_and_evidence(led):
    requirement(led)
    test = await ready(led, tr.TestPlan(tests=[planned(metric=None, low=None)], cannot_test=[]))
    with pytest.raises(tr.TraceError, match="passed"):
        tr.record_result(led, test, None, "", "Watched it for a full minute", HUMAN)
    with pytest.raises(tr.TraceError, match="measured"):
        tr.record_result(led, test, None, "passed", "ok", HUMAN)
    assert tr.record_result(led, test, None, "passed", "Watched it for a full minute", HUMAN) == "passed"


@pytest.mark.asyncio
async def test_measured_tests_need_the_number(led):
    requirement(led)
    test = await ready(led, tr.TestPlan(tests=[planned()], cannot_test=[]))
    with pytest.raises(tr.TraceError, match="Tracking distance"):
        tr.record_result(led, test, None, "passed", "Tape measure on the bench", HUMAN)


@pytest.mark.asyncio
async def test_only_a_person_records_results_and_only_on_approved_tests(led):
    requirement(led)
    await tr.plan_tests(led, Gateway(tr.TestPlan(tests=[planned()], cannot_test=[])))
    test = tr.view(led)["tests"][0]["id"]
    with pytest.raises(tr.TraceError, match="Approve"):
        tr.record_result(led, test, 2.0, "", "Tape measure on the bench", HUMAN)
    tr.approve_all(led, HUMAN)
    with pytest.raises(PermissionDenied):
        tr.record_result(led, test, 2.0, "", "Tape measure on the bench", Actor.agent("mechanical"))


@pytest.mark.asyncio
async def test_changing_a_part_makes_its_results_need_retesting(led):
    requirement(led)
    test = await ready(led, tr.TestPlan(tests=[planned()], cannot_test=[]))
    tr.record_result(led, test, 1.4, "", "Tape measure on the bench", HUMAN)
    mech.revise(led, "P1", {"material": "PETG"}, "Stronger", HUMAN)
    row = tr.view(led)["tests"][0]
    assert row["state"] == "retest" and row["staleBecause"] == ["P1: Bracket"] and status(led) == "retest"
    tr.record_result(led, test, 1.5, "", "Tape measure after the change", HUMAN)
    assert status(led) == "proven"


@pytest.mark.asyncio
async def test_rewording_a_requirement_needs_retesting(led):
    old = requirement(led)
    test = await ready(led, tr.TestPlan(tests=[planned(uses=())], cannot_test=[]))
    tr.record_result(led, test, 1.4, "", "Tape measure on the bench", HUMAN)
    change = ch.propose(led, "Further", "The room is bigger", "requirement", old, {"text": "Tracks at 3 m"}, HUMAN)
    led.append(Actor.system("headquarters"), "change.reviewed", id=change, review=ch.ChangeReview(recommendation="approve", summary="s", risks=[], missed=[]).model_dump())
    assert any(i["item"].startswith("Test T1") for i in next(c for c in ch.view(led) if c["id"] == change)["impact"])
    ch.approve(led, change, "", HUMAN)
    assert status(led) == "retest"


@pytest.mark.asyncio
async def test_an_unrelated_change_leaves_results_alone(led):
    requirement(led)
    test = await ready(led, tr.TestPlan(tests=[planned()], cannot_test=[]))
    tr.record_result(led, test, 1.4, "", "Tape measure on the bench", HUMAN)
    mech.add_part(led, {"number": "P2", "name": "Other", "material": "PLA", "process": "print", "qty": 1}, HUMAN)
    assert status(led) == "proven"


@pytest.mark.asyncio
async def test_planner_gets_feedback_for_gaps_and_impossible_limits(led):
    requirement(led)
    requirement(led, "R2", "Weighs under 1 kg")
    first = tr.TestPlan(tests=[planned(covers=("R1",), low=5.0, high=1.0)], cannot_test=[])
    good = tr.TestPlan(tests=[planned(covers=("R1",))], cannot_test=["R2 has no method for weighing the finished unit"])
    gateway = Gateway(first, good)
    await tr.plan_tests(led, gateway)
    feedback = gateway.calls[1][1]["feedback"]
    assert "above the highest" in feedback and "R2 has no test" in feedback
    assert tr.view(led)["untestable"] == good.cannot_test


@pytest.mark.asyncio
async def test_unknown_parts_are_dropped_from_the_links(led):
    requirement(led)
    await tr.plan_tests(led, Gateway(tr.TestPlan(tests=[planned(uses=("P1", "Invented"))], cannot_test=[])))
    assert tr.view(led)["tests"][0]["uses"] == ["P1"]


@pytest.mark.asyncio
async def test_headquarters_writes_tests_after_the_baseline(led):
    requirement(led)
    for gate in ("G0", "G1", "G2"):
        led.append(HUMAN, "gate.approved", gate=gate)
    for department in hq.DEPARTMENTS:
        led.append(Actor.system("headquarters"), "hq.finished", step=f"open:{department}", label="x")
    gateway = Gateway(tr.TestPlan(tests=[planned()], cannot_test=[]))
    await hq.advance(led, gateway, hq.Services(None))
    assert [c[0] for c in gateway.calls] == ["test_planner"] and tr.view(led)["tests"]
    await hq.advance(led, gateway, hq.Services(None))
    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_failures_and_waiting_tests_reach_the_review_queue_and_handoff(led):
    requirement(led)
    await tr.plan_tests(led, Gateway(tr.TestPlan(tests=[planned()], cannot_test=[])))
    assert any(i["screen"] == "Tests" for i in ov.review_queue(led) if i["kind"] == "open")
    tr.approve_all(led, HUMAN)
    test = tr.view(led)["tests"][0]["id"]
    tr.record_result(led, test, 0.2, "", "Tape measure on the bench", HUMAN)
    assert any("failed" in i["title"] for i in ov.review_queue(led))
    assert "Failed: 1" in ws.handoff_markdown(led.state(), led)


# ---- The dependency graph


def comp(led, number="SG90", role="Servo"):
    record = led.propose(HUMAN, "component", part_number=number, role=role)
    led.append(HUMAN, "record.confirmed", id=record)
    return record


def wire(led, component, name="PWM", pin="S"):
    record = led.propose(HUMAN, "net", name=name, kind="signal", nominal_v=3.3, members=[{"component": component, "pin": pin, "role": "input"}])
    led.append(HUMAN, "record.confirmed", id=record)
    return record


def code(led, device="node", text="int a;", components=("SG90",), title="Servo drive", path="src/main.cpp"):
    fw.write_files(led.root, device, [fw.FileOut(path=path, owner="Electrical", blocks=[fw.BlockOut(title=title, description="Drives the servo using its timing.", components=list(components), code=text)])])
    (led.root / "firmware" / device / "platformio.ini").write_text("[env:device]\nboard = b\n")


async def passed_test(led, uses, covers=("R1",), metric=None):
    requirement(led)
    await tr.plan_tests(led, Gateway(tr.TestPlan(tests=[planned(covers=covers, metric=None, low=None, uses=uses)], cannot_test=[])))
    tr.approve_all(led, HUMAN)
    test = tr.view(led)["tests"][0]["id"]
    tr.set_dependencies(led, test, list(uses), HUMAN)
    tr.record_result(led, test, None, "passed", "Watched it for a full minute", HUMAN)
    return test


def state_of(led):
    return tr.view(led)["tests"][0]


@pytest.mark.asyncio
async def test_editing_code_a_component_relies_on_expires_the_result(led):
    comp(led)
    code(led)
    await passed_test(led, ("SG90",))
    assert state_of(led)["state"] == "passed"
    code(led, text="int b;")
    now = state_of(led)
    assert now["state"] == "retest" and now["staleBecause"] == ["node/src/main.cpp: Servo drive"]


@pytest.mark.asyncio
async def test_rewiring_a_component_expires_the_result(led):
    wired = comp(led)
    old = wire(led, wired)
    await passed_test(led, ("SG90",))
    led.append(Actor.system("x"), "record.superseded", id=old)
    wire(led, wired, pin="T")
    assert state_of(led)["state"] == "retest"


@pytest.mark.asyncio
async def test_a_new_connection_to_it_also_expires_the_result(led):
    wired = comp(led)
    await passed_test(led, ("SG90",))
    wire(led, wired, name="EXTRA")
    assert "Connection EXTRA" in state_of(led)["staleBecause"]


@pytest.mark.asyncio
async def test_a_breaking_packet_change_reaches_a_test_on_the_device(led):
    from buildanything import interfaces as itf

    comp(led)
    code(led, text="ba_ping_t m; int z = ba_ping_decode(0,0,&m);")
    definition = {"name": "PING", "id": 1, "transport": {"type": "udp", "port": 9}, "producer": "a", "consumers": ["b"], "fields": [{"name": "n", "type": "uint8"}]}
    itf.save_packet(led, definition, HUMAN)
    await passed_test(led, ("device:node",))
    itf.save_packet(led, {**definition, "fields": [{"name": "n", "type": "uint16"}]}, HUMAN)
    assert "PING" in state_of(led)["staleBecause"]


@pytest.mark.asyncio
async def test_other_devices_code_leaves_the_result_alone(led):
    comp(led)
    comp(led, "BME280", "Pressure")
    code(led)
    code(led, device="other", components=("BME280",), title="Pressure")
    await passed_test(led, ("SG90",))
    code(led, device="other", text="int c;", components=("BME280",), title="Pressure")
    assert state_of(led)["state"] == "passed"


@pytest.mark.asyncio
async def test_a_block_citing_a_datasheet_finding_is_linked_without_declaring_it(led):
    target = comp(led)
    note = led.propose(Actor.agent("electrical"), "page_note", component=target, part="SG90", datasheet="d", file="f", page=1, finding="x", why="y")
    fw.write_files(led.root, "node", [fw.FileOut(path="src/a.cpp", owner="Electrical", blocks=[fw.BlockOut(title="Timing", description="Uses the servo's pulse timing.", based_on=[f"note:{note}"], code="int a;")])])
    (led.root / "firmware/node/platformio.ini").write_text("[env:device]\nboard = b\n")
    assert depgraph.build(led).dependents("SG90")["code"] == ["node/src/a.cpp: Timing"]


@pytest.mark.asyncio
async def test_nothing_listed_means_the_whole_department(led):
    requirement(led)
    await tr.plan_tests(led, Gateway(tr.TestPlan(tests=[planned(metric=None, low=None, uses=())], cannot_test=[])))
    tr.approve_all(led, HUMAN)
    tr.record_result(led, tr.view(led)["tests"][0]["id"], None, "passed", "Watched it for a full minute", HUMAN)
    mech.add_part(led, {"number": "P9", "name": "New", "material": "PLA", "process": "print", "qty": 1}, HUMAN)
    assert state_of(led)["state"] == "retest"


@pytest.mark.asyncio
async def test_dependencies_can_be_edited_only_by_a_person_and_must_exist(led):
    requirement(led)
    await tr.plan_tests(led, Gateway(tr.TestPlan(tests=[planned()], cannot_test=[])))
    test = tr.view(led)["tests"][0]["id"]
    with pytest.raises(tr.TraceError, match="Not in this project"):
        tr.set_dependencies(led, test, ["Nothing"], HUMAN)
    with pytest.raises(PermissionDenied):
        tr.set_dependencies(led, test, ["P1"], Actor.agent("controller"))
    tr.set_dependencies(led, test, [], HUMAN)
    assert state_of(led)["uses"] == []


def test_blocks_can_only_name_real_components(led):
    comp(led)
    good = fw.FileOut(path="a.py", owner="Software", blocks=[fw.BlockOut(title="T", description="Long enough description here.", components=["sg90"], code="x=1")])
    bad = fw.FileOut(path="a.py", owner="Software", blocks=[fw.BlockOut(title="T", description="Long enough description here.", components=["Invented"], code="x=1")])
    known = fw.component_names(led.state())
    assert fw.component_problems([good], known) == [] and "Invented" in fw.component_problems([bad], known)[0]


def test_a_block_is_flagged_when_its_component_is_replaced(led):
    target = comp(led)
    code(led)
    led.append(HUMAN, "record.rejected", id=target)
    block = fw.scan_workspace(led.root, "node", None, fw.component_names(led.state()))[0]["blocks"][0]
    assert block["state"] == "source_changed" and block["components"] == ["SG90"]


@pytest.mark.asyncio
async def test_a_change_request_names_the_code_and_tests_it_touches(led):
    target = comp(led)
    code(led)
    await passed_test(led, ("SG90",))
    change = ch.propose(led, "Metal gears", "Plastic gears strip under load", "component", target, {"part_number": "MG90S"}, HUMAN)
    items = {i["item"] for i in next(c for c in ch.view(led) if c["id"] == change)["impact"]}
    assert "node/src/main.cpp: Servo drive" in items and any(i.startswith("Test T1") for i in items)


@pytest.mark.asyncio
async def test_csv_is_a_faithful_copy(led):
    await passed_test(led, ("P1",))
    text = tr.csv_export(led)
    assert text.splitlines()[0].startswith("Requirement,Text") and "R1" in text.splitlines()[1] and "passed" in text.splitlines()[1]


def test_leads_can_ask_what_depends_on_something(led):
    comp(led)
    code(led)
    tools = hq._lead_tools(led, Gateway(), "Software", hq.Services(None))
    lookup = next(t for t in tools if getattr(t, "__name__", "") == "what_depends_on")
    assert lookup("SG90")["code"] == ["node/src/main.cpp: Servo drive"]


# ---- Deviations and dropped requirements


OK_REVIEW = ch.ChangeReview(recommendation="approve", summary="s", risks=[], missed=[])


def reviewed_change(led, title, kind, target, details=None, actor=HUMAN):
    change = ch.propose(led, title, "The bench setup can't reach this", kind, target, details or {}, actor)
    led.append(Actor.system("headquarters"), "change.reviewed", id=change, review=OK_REVIEW.model_dump())
    return change


DEV = {"what": "Tracking only reaches 1.5 m", "restores": "A wider-angle camera"}


@pytest.mark.parametrize("details, text", [({"what": "x"}, "put it right"), ({"restores": "y"}, "put it right")])
def test_a_deviation_must_say_what_and_how_to_fix_it(led, details, text):
    record = requirement(led)
    with pytest.raises(ch.ChangeError, match=text):
        ch.propose(led, "T", "A good reason here", "deviation", record, details, HUMAN)


@pytest.mark.parametrize("kind, details", [("deviation", DEV), ("drop_requirement", {})])
def test_both_need_a_stated_reason_to_approve(led, kind, details):
    record = requirement(led)
    change = reviewed_change(led, "T", kind, record, details)
    with pytest.raises(ch.ChangeError, match="Say why"):
        ch.approve(led, change, "", HUMAN)


@pytest.mark.parametrize("kind, details", [("deviation", DEV), ("drop_requirement", {})])
@pytest.mark.asyncio
async def test_headquarters_can_never_settle_them(led, kind, details):
    record = requirement(led)
    change = ch.propose(led, "T", "A good reason here", kind, record, details, Actor.agent("mechanical"))

    class Gate:
        async def run(self, *, agent, **_):
            return OK_REVIEW if agent == "change_reviewer" else ch.Route(action="forward", handler="None", reason="r", earlier_id=None)

    await ch.review(led, Gate(), change)
    await ch.triage(led, Gate(), change)
    item = next(c for c in ch.view(led) if c["id"] == change)
    assert item["status"] == "open" and item["escalation"]


def accept_deviation(led, record):
    change = reviewed_change(led, "Shorter range", "deviation", record, DEV)
    ch.approve(led, change, "Accepted for the demo", HUMAN)


def test_a_deviation_lets_the_project_finish_and_stays_visible(led):
    from buildanything import gatereport, gates

    record = requirement(led)
    for gate in ("G0", "G1", "G2", "G3", "G4", "G5"):
        led.append(HUMAN, "gate.approved", gate=gate)
    assert "R1" in " ".join(gates.missing("G6", led.state(), led))
    accept_deviation(led, record)
    assert status(led) == "deviated" and gates.missing("G6", led.state(), led) == []
    page = gatereport.report(led, "G6")
    assert "Tracking only reaches 1.5 m" in page and "A wider-angle camera" in page and "Accepted deviation" in page


@pytest.mark.asyncio
async def test_a_proven_result_outranks_a_deviation(led):
    record = requirement(led)
    test = await ready(led, tr.TestPlan(tests=[planned(metric=None, low=None)], cannot_test=[]))
    accept_deviation(led, record)
    tr.record_result(led, test, None, "passed", "Reached the full distance after all", HUMAN)
    assert status(led) == "proven"


@pytest.mark.asyncio
async def test_rewording_the_requirement_cancels_the_deviation(led):
    record = requirement(led)
    accept_deviation(led, record)
    change = reviewed_change(led, "Reword", "requirement", record, {"text": "Tracks at 5 m"})
    ch.approve(led, change, "", HUMAN)
    assert status(led) == "no_test" and tr.deviations(led)[0]["status"] == "outdated"


def test_closing_needs_a_person_and_a_reason(led):
    record = requirement(led)
    accept_deviation(led, record)
    with pytest.raises(tr.TraceError, match="what changed"):
        tr.close_deviation(led, "dev_1", "", HUMAN)
    with pytest.raises(PermissionDenied):
        tr.close_deviation(led, "dev_1", "Fixed in the new build", Actor.agent("mechanical"))
    tr.close_deviation(led, "dev_1", "Fixed in the new build", HUMAN)
    assert status(led) == "no_test"


@pytest.mark.asyncio
async def test_dropping_removes_it_retires_its_test_and_keeps_the_record(led):
    from buildanything import gatereport

    record = requirement(led)
    await tr.plan_tests(led, Gateway(tr.TestPlan(tests=[planned()], cannot_test=[])))
    change = reviewed_change(led, "Not needed", "drop_requirement", record)
    assert any(i["item"].startswith("Test T1") for i in next(c for c in ch.view(led) if c["id"] == change)["impact"])
    ch.approve(led, change, "The customer removed this", HUMAN)
    view = tr.view(led)
    assert view["rows"] == [] and view["tests"] == [] and view["dropped"][0]["key"] == "R1"
    assert "The bench setup" in gatereport.report(led, "G4")


@pytest.mark.asyncio
async def test_a_shared_test_survives_dropping_one_requirement(led):
    first = requirement(led)
    requirement(led, "R2", "Weighs under 1 kg")
    await tr.plan_tests(led, Gateway(tr.TestPlan(tests=[planned(covers=("R1", "R2"))], cannot_test=[])))
    ch.approve(led, reviewed_change(led, "Not needed", "drop_requirement", first), "The customer removed this", HUMAN)
    view = tr.view(led)
    assert [t["covers"] for t in view["tests"]] == [["R2"]] and [r["key"] for r in view["rows"]] == ["R2"]
