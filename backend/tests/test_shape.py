"""A project's shape: the team and the gates Headquarters proposes and a person confirms."""
import asyncio

import pytest

from buildanything import agents as ag, gates, gatereport, headquarters as hq, shape
from buildanything.ledger import Actor, Ledger, PermissionDenied

from test_headquarters import Gateway, Papers

HUMAN = Actor.human()
SOFTWARE_GATES = [
    {"id": "G0"},
    {"id": "G2"},
    {"id": "G5", "name": "Build"},
    {"id": "C1", "name": "Run", "phase": "G5", "criteria": ["The app starts from the desktop shortcut"]},
    {"id": "G6", "name": "Test"},
]


@pytest.fixture
def led(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.append(HUMAN, "project.created", name="T", aim="A budgeting app", scope="Desktop")
    return ledger


def concept(led, **extra):
    led.propose(Actor.agent("concept"), "concept_draft", message="", reply="r", summary="A desktop budgeting app.", decisions=["Use Python"], open_questions=[], **extra)


def test_old_projects_keep_four_departments_and_seven_gates(led):
    state = led.state()
    assert shape.team(state) == ["Electrical", "Mechanical", "Software", "Procurement"]
    assert gates.order(state) == ["G0", "G1", "G2", "G3", "G4", "G5", "G6"]
    assert not shape.chosen(state)


def test_concept_partner_proposes_a_lean_shape(led):
    class Lean(Gateway):
        async def run(self, *, agent, values, output_type=str, tools=(), **kw):
            if agent == "concept":
                assert "shape_options" in values and "Procurement" in values["shape_options"]["departments"]
                return ag.ConceptOut(reply="r", summary="s", decisions=[], open_questions=[],
                                     team=[ag.TeamPick(department="Software", why="It's all code"), ag.TeamPick(department="Marketing", why="no such thing")],
                                     gates=[ag.GatePick(stage="G2"), ag.GatePick(stage="custom", name="Run", phase="G5", criteria=["It starts"]), ag.GatePick(stage="G6", name="Test")])
            return await super().run(agent=agent, values=values, output_type=output_type, tools=tools, **kw)

    asyncio.run(hq.talk_to_concept(led, Lean(), "", hq.Services(Papers())))
    proposed = shape.proposal(led)
    assert proposed["team"] == ["Software"]  # an unknown department is dropped
    assert [g["id"] for g in proposed["gates"]] == ["G0", "G2", "C1", "G6"]  # Idea is always first
    assert proposed["gates"][2]["criteria"] == ["It starts"]
    # A proposal changes nothing until the person approves it.
    assert shape.team(led.state()) == ["Electrical", "Mechanical", "Software", "Procurement"]


def test_approving_the_concept_saves_the_chosen_shape(led):
    concept(led)
    hq.approve_concept(led, HUMAN, ["Software"], SOFTWARE_GATES)
    state = led.state()
    assert shape.team(state) == ["Software"]
    assert gates.order(state) == ["G0", "G2", "G5", "C1", "G6"]
    assert gates.current_gate(state) == "G2"  # Idea passed with the concept
    assert gates.stage("C1", state) == "Run"


def test_only_a_person_sets_the_shape(led):
    with pytest.raises(PermissionDenied):
        led.append(Actor.agent("concept"), "shape.set", team=["Software"], gates=None, reason="")


def test_plans_must_start_at_idea_and_follow_the_journey(led):
    with pytest.raises(gates.GateError):
        shape.set_shape(led, None, [{"id": "G2"}], "", HUMAN)
    with pytest.raises(gates.GateError):
        shape.set_shape(led, None, [{"id": "G0"}, {"id": "G5"}, {"id": "G2"}], "", HUMAN)
    with pytest.raises(gates.GateError):
        shape.set_shape(led, None, [{"id": "G0"}, {"id": "C1", "name": "Run"}], "", HUMAN)  # a custom gate says where it belongs
    with pytest.raises(shape.ShapeError):
        shape.set_shape(led, ["Marketing"], None, "", HUMAN)


def test_passed_gates_stay_put(led):
    concept(led)
    hq.approve_concept(led, HUMAN, ["Software"], SOFTWARE_GATES)
    with pytest.raises(gates.GateError):
        shape.set_shape(led, None, [{"id": "G1"}, {"id": "G2"}], "", HUMAN)
    with pytest.raises(gates.GateError):
        shape.set_shape(led, None, [{"id": "G0", "criteria": ["New rule"]}, {"id": "G2"}], "", HUMAN)
    shape.set_shape(led, None, [{"id": "G0"}, {"id": "G2"}, {"id": "G6", "name": "Test"}], "Simpler", HUMAN)
    assert gates.order(led.state()) == ["G0", "G2", "G6"]


def test_skipped_research_counts_as_passed_once_idea_is_done(led):
    concept(led)
    hq.approve_concept(led, HUMAN, ["Software"], SOFTWARE_GATES)
    state = led.state()
    assert gates.passed(state, "G1")
    assert not gates.passed(state, "G3")  # the Plan gate comes first
    assert hq.next_action(led) == "plan"  # no research step


def test_a_custom_requirement_needs_a_persons_tick(led):
    shape.set_shape(led, ["Software"], [{"id": "G0", "criteria": ["The name is agreed"]}], "", HUMAN)
    assert any("The name is agreed" in m for m in gatereport.missing("G0", led.state(), led))
    with pytest.raises(gates.GateError):
        gates.mark_criterion(led, "G0", 0, True, "", HUMAN)  # say how you know
    with pytest.raises(PermissionDenied):
        led.append(Actor.agent("software"), "gate.criterion_met", gate="G0", index=0, met=True, note="trust me", text="The name is agreed")
    gates.mark_criterion(led, "G0", 0, True, "Agreed in the meeting", HUMAN)
    assert not any("The name is agreed" in m for m in gatereport.missing("G0", led.state(), led))
    # Rewording the requirement asks again.
    shape.set_shape(led, None, [{"id": "G0", "criteria": ["The name and logo are agreed"]}], "", HUMAN)
    assert any("logo" in m for m in gatereport.missing("G0", led.state(), led))


def test_departments_off_the_team_are_not_started_or_asked(led):
    concept(led)
    hq.approve_concept(led, HUMAN, ["Software"], [{"id": "G0"}, {"id": "G6"}])
    led.propose(HUMAN, "requirement", key="R1", text="t", verification="v")
    led.propose(HUMAN, "task", key="T1", department="Software", title="t", weeks=1, depends_on=[], start_week=0)
    for r in led.state().records.values():
        led.append(HUMAN, "record.confirmed", id=r["id"])
    assert hq.next_action(led) in ("open:Software", "plan_tests")
    led.append(Actor.system("headquarters"), "hq.finished", step="open:Software", label="x")
    assert not str(hq.next_action(led)).startswith("open:")
    with pytest.raises(hq.HeadquartersError):
        asyncio.run(hq.talk_to_department(led, Gateway(), "Electrical", "hello", hq.Services(Papers())))


def test_a_department_can_be_requested_and_added_by_a_person(led):
    concept(led)
    hq.approve_concept(led, HUMAN, ["Software"], [{"id": "G0"}, {"id": "G6"}])
    tool = hq.request_department_tool(led, "Software")
    assert "specifically" in tool("Procurement", "buy")
    assert tool("Procurement", "We need to buy a USB serial adapter for testing.").startswith("Requested")
    assert "already been requested" in tool("Procurement", "We need to buy a USB serial adapter for testing.")
    assert [r["department"] for r in shape.requests(led)] == ["Procurement"]
    assert any(n["kind"] == "team" for n in hq.needs(led))
    shape.add_department(led, "Procurement", "Needed for the adapter", HUMAN)
    assert shape.team(led.state()) == ["Software", "Procurement"]
    assert shape.requests(led) == []


def test_gate_list_shows_the_plan(led):
    concept(led)
    hq.approve_concept(led, HUMAN, ["Software"], SOFTWARE_GATES)
    listed = gatereport.gate_list(led)
    assert [g["stage"] for g in listed] == ["Idea", "Plan", "Build", "Run", "Test"]
    run = next(g for g in listed if g["id"] == "C1")
    assert run["phase"] == "G5" and run["criteria"][0]["text"].startswith("The app starts")


def test_an_unticked_requirement_shows_on_headquarters(led):
    concept(led)
    hq.approve_concept(led, HUMAN, ["Mechanical"], [{"id": "G0"}, {"id": "G2", "criteria": ["The deflection limit is specified"]}])
    led.propose(HUMAN, "requirement", key="R1", text="t", verification="v")
    led.propose(HUMAN, "task", key="T1", department="Mechanical", title="t", weeks=1, depends_on=[], start_week=0)
    led.propose(Actor.agent("verifier"), "finding", text="minor", severity="low", run=1, plan=1)
    for r in led.state().records.values():
        if r["type"] in ("requirement", "task"):
            led.append(HUMAN, "record.confirmed", id=r["id"])
    needs = hq.needs(led)
    assert any(n["kind"] == "gate_waiting" and "deflection" in n["text"] for n in needs), needs


def test_a_finding_fix_goes_to_a_department_on_the_team():
    assert gatereport._finding_owner("The wiring and pin voltages are wrong", ["Software"]) == "Software"
    assert gatereport._finding_owner("anything", ["Mechanical"]) == "Mechanical"
