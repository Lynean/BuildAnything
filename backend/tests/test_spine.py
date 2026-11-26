"""Offline tests. No network and no API spend."""
import json

import pytest
from fastapi.testclient import TestClient
from fake_model import TestModel

from buildanything import agents, api, gates
from buildanything.citations import Paper, quote_in_text
from buildanything.gateway import Gateway, Price, SpendLimitReached
from buildanything.ledger import Actor, Ledger, LedgerError, PermissionDenied
from buildanything.providers import MemoryStore

HUMAN = Actor.human()
VALUES = {"project": "p", "confirmed_claims": "c", "earlier_discussion": "d", "message": "m", "shape_options": "o"}
QUOTE = "flexible wings improved lift by a measurable margin in hover"
PAPER = Paper("1234.5678", "Flexible wings", f"We show that {QUOTE} across trials.", "https://arxiv.org/abs/1234.5678")


@pytest.fixture
def led(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.append(HUMAN, "project.created", name="Test", aim="Fly", scope="Small")
    return ledger


class Index:
    def search(self, query):
        return [PAPER]

    def get(self, paper_id):
        return PAPER if paper_id == PAPER.id else None


class Provider:
    def __init__(self, model, name="fake", ):
        self.model, self.name = model, name

    def resolve(self, tier):
        return self.model, self.name, {}


class Scripted:
    """A stand-in gateway that returns scripted outputs and can call tools."""

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.prompts = []

    async def run(self, *, tools=(), values=None, **_):
        self.prompts.append(json.dumps(values, ensure_ascii=False))
        for tool in tools:
            if callable(tool):
                tool("wings")
        return self.outputs.pop(0)


# ---- Ledger ----


def test_state_is_replayed_from_events(led):
    rid = led.propose(Actor.agent("research"), "claim", statement="x")
    led.append(HUMAN, "record.confirmed", id=rid)
    assert Ledger(led.root).state().records[rid]["status"] == "confirmed"


def test_agent_cannot_write_human_only_events(led):
    rid = led.propose(Actor.agent("research"), "claim", statement="x")
    for kind, data in [("record.confirmed", {"id": rid}), ("gate.approved", {"gate": "G0"}), ("limit.changed", {"usd": 999})]:
        with pytest.raises(PermissionDenied):
            led.append(Actor.agent("controller"), kind, **data)


def test_agent_limited_to_its_own_record_types(led):
    with pytest.raises(PermissionDenied):
        led.propose(Actor.agent("research"), "task", title="x")
    with pytest.raises(PermissionDenied):
        led.propose(Actor.agent("concept"), "claim", statement="x")


def test_edited_event_is_detected(led):
    text = led.path.read_text(encoding="utf-8").replace('"Fly"', '"Sink"')
    led.path.write_text(text, encoding="utf-8")
    with pytest.raises(LedgerError):
        led.state()


def test_cache_can_be_deleted_and_rebuilt(led):
    rid = led.propose(Actor.agent("research"), "claim", statement="x")
    assert [r["id"] for r in led.query("claim")] == [rid]
    led.cache_path.unlink()
    assert [r["id"] for r in led.query("claim")] == [rid]


def test_cache_picks_up_new_events(led):
    led.propose(Actor.agent("research"), "claim", statement="a")
    assert len(led.query("claim")) == 1
    led.propose(Actor.agent("research"), "claim", statement="b")
    assert len(led.query("claim")) == 2


def test_rapid_events_keep_their_order(led):
    ids = [led.propose(Actor.agent("research"), "claim", statement=str(i)) for i in range(40)]
    for rid in ids:
        led.append(HUMAN, "record.confirmed", id=rid)
    assert all(r["status"] == "confirmed" for r in led.state().of_type("claim"))
    stamps = [e["at"] for e in led.events()]
    assert stamps == sorted(set(stamps))


def test_two_people_merging_logs_keeps_all_events(tmp_path, led):
    first = led.path.read_text(encoding="utf-8")
    other = Ledger(tmp_path / "other")
    other.path.parent.mkdir(parents=True)
    other.path.write_text(first, encoding="utf-8")
    led.propose(Actor.agent("research"), "claim", statement="mine")
    other.propose(Actor.agent("research"), "claim", statement="theirs")
    merged = dict.fromkeys(led.path.read_text(encoding="utf-8").splitlines() + other.path.read_text(encoding="utf-8").splitlines())
    led.path.write_text("\n".join(merged) + "\n", encoding="utf-8")
    assert {r["fields"]["statement"] for r in led.state().of_type("claim")} == {"mine", "theirs"}


# ---- Gates ----


def test_gates_must_go_in_order(led):
    with pytest.raises(gates.GateError):
        gates.approve(led, "G1", HUMAN)


def test_g0_needs_aim_and_scope(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.append(HUMAN, "project.created", name="T", aim="", scope="")
    with pytest.raises(gates.GateError):
        gates.approve(ledger, "G0", HUMAN)


def test_agent_cannot_approve_a_gate(led):
    with pytest.raises(PermissionDenied):
        gates.approve(led, "G0", Actor.agent("controller"))


def test_g1_needs_confirmed_claim_and_decision(led):
    gates.approve(led, "G0", HUMAN)
    assert len(gates.missing("G1", led.state())) == 2
    rid = led.propose(Actor.agent("research"), "claim", statement="x")
    led.append(HUMAN, "record.confirmed", id=rid)
    did = led.propose(HUMAN, "decision", text="d")
    led.append(HUMAN, "record.confirmed", id=did)
    gates.approve(led, "G1", HUMAN)
    assert gates.current_gate(led.state()) == "G2"


def _confirmed_baseline(led):
    gates.approve(led, "G0", HUMAN)
    for kind, fields, agent in [("claim", {"statement": "x"}, "research"), ("requirement", {"text": "r"}, "controller"), ("task", {"title": "t"}, "controller")]:
        led.append(HUMAN, "record.confirmed", id=led.propose(Actor.agent(agent), kind, **fields))
    led.append(HUMAN, "record.confirmed", id=led.propose(HUMAN, "decision", text="d"))
    gates.approve(led, "G1", HUMAN)


def test_high_finding_blocks_g2_until_accepted(led):
    _confirmed_baseline(led)
    fid = led.propose(Actor.agent("verifier"), "finding", run=1, severity="high", text="bad", related=[])
    with pytest.raises(gates.GateError):
        gates.approve(led, "G2", HUMAN)
    led.append(HUMAN, "record.confirmed", id=fid)
    gates.approve(led, "G2", HUMAN)


def test_a_clean_rerun_does_not_clear_an_open_high_finding(led):
    _confirmed_baseline(led)
    led.propose(Actor.agent("verifier"), "finding", run=1, severity="high", text="bad", related=[])
    led.propose(Actor.agent("verifier"), "finding", run=2, severity="low", text="ok", related=[])
    with pytest.raises(gates.GateError):
        gates.approve(led, "G2", HUMAN)


def test_unavailable_gate_cannot_be_approved(led):
    assert gates.missing("G4", led.state()) == ["Open this project to see what this stage needs."]


# ---- Spend guard ----


PRICED = {"fake": Price(1000.0, 0.0, 0.0, 1000.0)}


@pytest.mark.asyncio
async def test_run_records_spend(led):
    gateway = Gateway(led, Provider(TestModel(custom_output_text="hi")), PRICED)
    assert await gateway.run(agent="concept", values=VALUES) == "hi"
    state = led.state()
    assert state.spent_usd > 0 and state.spend_by_area["Headquarters"] == state.spent_usd


@pytest.mark.asyncio
async def test_no_run_once_limit_is_reached(led):
    led.append(Actor.system(), "spend.recorded", agent="x", area="Headquarters", usd=20.0)
    gateway = Gateway(led, Provider(TestModel()), PRICED)
    with pytest.raises(SpendLimitReached):
        await gateway.run(agent="concept", values=VALUES)


@pytest.mark.asyncio
async def test_run_stops_at_remaining_budget_and_still_records(led):
    led.append(Actor.system(), "spend.recorded", agent="x", area="Headquarters", usd=19.9999)
    before = led.state().spent_usd
    gateway = Gateway(led, Provider(TestModel(custom_output_text="word " * 400)), PRICED)
    with pytest.raises(SpendLimitReached):
        await gateway.run(agent="concept", values=VALUES)
    assert led.state().spent_usd > before


@pytest.mark.asyncio
async def test_unknown_price_is_refused(led):
    gateway = Gateway(led, Provider(TestModel(), name="mystery"), PRICED)
    with pytest.raises(RuntimeError):
        await gateway.run(agent="concept", values=VALUES)
    assert led.state().spent_usd == 0


# ---- Citations and research ----


def test_quote_check():
    assert quote_in_text(QUOTE.upper(), PAPER.text)
    assert not quote_in_text("flexible wings doubled range in every single trial", PAPER.text)
    assert not quote_in_text("wings", PAPER.text)


@pytest.mark.asyncio
async def test_research_keeps_only_verified_claims(led):
    output = agents.ResearchOut(claims=[
        agents.ClaimOut(paper_id=PAPER.id, statement="real", quote=QUOTE, applicability="hover"),
        agents.ClaimOut(paper_id=PAPER.id, statement="bad quote", quote="an invented sentence that is not in the paper", applicability="-"),
        agents.ClaimOut(paper_id="9999.0000", statement="fake paper", quote=QUOTE, applicability="-"),
    ])
    result = await agents.run_research(led, Scripted(output), Index())
    state = led.state()
    assert result["accepted"] == 1 and len(result["rejected"]) == 2
    assert [c["fields"]["statement"] for c in state.of_type("claim")] == ["real"]
    assert len(state.of_type("source")) == 1


@pytest.mark.asyncio
async def test_real_paper_not_seen_in_search_is_rejected(led):
    class Hidden(Index):
        def search(self, query):
            return []

    output = agents.ResearchOut(claims=[agents.ClaimOut(paper_id=PAPER.id, statement="s", quote=QUOTE, applicability="a")])
    result = await agents.run_research(led, Scripted(output), Hidden())
    assert result["accepted"] == 0


# ---- Controller and Verifier ----


def _plan(tasks):
    return agents.PlanOut(requirements=[agents.RequirementOut(key="R1", text="r", verification="v")], tasks=tasks)


def _task(key, deps=(), weeks=1, dept="Electrical"):
    return agents.TaskOut(key=key, department=dept, title=key, weeks=weeks, depends_on=list(deps))


def test_schedule_uses_longest_dependency():
    plan = _plan([_task("A", weeks=2), _task("B", weeks=3), _task("C", ["A", "B"])])
    assert agents.schedule(plan) == {"A": 0, "B": 0, "C": 3}


def test_plan_cycle_and_unknown_dependency_are_found():
    assert agents.plan_problems(_plan([_task("A", ["B"]), _task("B", ["A"])])) == ["The task dependencies contain a cycle."]
    assert "unknown task" in agents.plan_problems(_plan([_task("A", ["Z"])]))[0]


@pytest.mark.asyncio
async def test_invalid_plan_is_retried_with_feedback(led):
    bad, good = _plan([_task("A", ["A"])]), _plan([_task("A")])
    gateway = Scripted(bad, good)
    result = await agents.run_plan(led, gateway)
    assert result["created"] == 2 and "cycle" in gateway.prompts[1]


@pytest.mark.asyncio
async def test_plan_given_up_writes_nothing(led):
    bad = _plan([_task("A", ["A"])])
    result = await agents.run_plan(led, Scripted(bad, bad, bad))
    assert result["created"] == 0 and result["problems"]
    assert not led.state().of_type("task")


@pytest.mark.asyncio
async def test_verifier_sees_the_persons_words_but_not_the_agents_reasoning(led):
    led.propose(Actor.agent("concept"), "concept_draft", message="PERSON-ASKED", reply="SECRET-REPLY", summary="SECRET-SUMMARY", speculative=True)
    gateway = Scripted(agents.VerifyOut(findings=[]))
    await agents.run_verifier(led, gateway)
    assert "SECRET" not in gateway.prompts[0] and "PERSON-ASKED" in gateway.prompts[0]
    assert led.state().of_type("finding")[0]["fields"]["run"] == 1


def test_briefing_has_only_confirmed_records_for_that_department(led):
    actor = Actor.agent("controller")
    mine = led.propose(actor, "task", department="Electrical", title="mine")
    led.append(HUMAN, "record.confirmed", id=mine)
    led.append(HUMAN, "record.confirmed", id=led.propose(actor, "task", department="Mechanical", title="theirs"))
    led.propose(actor, "task", department="Electrical", title="unconfirmed")
    assert [t["title"] for t in agents.build_briefing(led.state(), "Electrical")["tasks"]] == ["mine"]


# ---- API ----


@pytest.fixture
def client(tmp_path):
    output = agents.ResearchOut(claims=[agents.ClaimOut(paper_id=PAPER.id, statement="real", quote=QUOTE, applicability="hover")]).model_dump()
    app = api.create_app(tmp_path, Provider(TestModel(custom_output_args=output, call_tools=["search_papers"])), Index(), PRICED, secrets=MemoryStore())
    return TestClient(app)


def test_project_flow_over_api(client):
    p = client.post("/api/projects", json={"name": "Butterfly Drone", "aim": "Fly", "scope": "Small"}).json()
    pid = p["id"]
    assert p["currentGate"] == "G0" and p["limit"] == 20.0
    assert client.post(f"/api/projects/{pid}/gates/G0/approve").json()["currentGate"] == "G1"
    assert client.post(f"/api/projects/{pid}/gates/G1/approve").status_code == 409
    r = client.post(f"/api/projects/{pid}/research", json={"focus": "wings"}).json()
    assert r["result"]["accepted"] == 1
    claim = next(x for x in r["project"]["records"] if x["type"] == "claim")
    client.post(f"/api/projects/{pid}/records/{claim['id']}/confirm")
    client.post(f"/api/projects/{pid}/decisions", json={"text": "Two wings"})
    assert client.post(f"/api/projects/{pid}/gates/G1/approve").json()["currentGate"] == "G2"


def test_bad_project_id_is_rejected(client):
    assert client.get("/api/projects/..%2Fsecret").status_code == 404
    assert client.get("/api/projects/nope").status_code == 404


def test_limit_can_be_changed_by_person(client):
    pid = client.post("/api/projects", json={"name": "A"}).json()["id"]
    assert client.post(f"/api/projects/{pid}/limit", json={"usd": 50}).json()["limit"] == 50
    assert client.post(f"/api/projects/{pid}/limit", json={"usd": 0}).status_code == 400


def test_events_contain_no_api_key_field(client, tmp_path):
    pid = client.post("/api/projects", json={"name": "A"}).json()["id"]
    assert "api_key" not in json.dumps(Ledger(tmp_path / pid).events())


def _high(led, safety=False):
    return led.propose(Actor.agent("verifier"), "finding", run=1, severity="high", safety=safety, text="bad", related=[])


def test_fix_keeps_blocking_until_a_new_run_and_a_person_closes_it(led):
    _confirmed_baseline(led)
    finding = _high(led)
    gates.decide_finding(led, finding, "fix", "", HUMAN)
    assert gates.open_high_findings(led.state())
    with pytest.raises(gates.GateError):
        gates.decide_finding(led, finding, "fixed", "", HUMAN)
    led.propose(Actor.agent("verifier"), "finding", run=2, severity="low", text="ok", related=[])
    gates.decide_finding(led, finding, "fixed", "", HUMAN)
    assert not gates.open_high_findings(led.state())


def test_closing_a_finding_needs_a_reason_and_safety_needs_more(led):
    plain, safety = _high(led), _high(led, safety=True)
    with pytest.raises(gates.GateError):
        gates.decide_finding(led, plain, "dismiss", "", HUMAN)
    with pytest.raises(gates.GateError):
        gates.decide_finding(led, safety, "dismiss", "not an issue", HUMAN)
    gates.decide_finding(led, safety, "dismiss", "The tether limits travel to 1 m and nobody is inside that area.", HUMAN)
    assert led.state().records[safety]["decision"]["decision"] == "dismiss"


def test_an_agent_cannot_close_a_finding(led):
    finding = _high(led)
    with pytest.raises(Exception):
        gates.decide_finding(led, finding, "accept", "looks fine to me", Actor.agent("verifier"))
    assert gates.open_high_findings(led.state())


def test_gate_record_and_briefing_keep_accepted_risks(led):
    _confirmed_baseline(led)
    finding = _high(led)
    gates.decide_finding(led, finding, "accept", "We accept this for the prototype.", HUMAN)
    gates.approve(led, "G2", HUMAN)
    event = [e for e in led.events() if e["kind"] == "gate.approved" and e["data"]["gate"] == "G2"][0]
    assert event["data"]["findings"][0]["reason"] == "We accept this for the prototype."


def test_briefings_include_accepted_risks(led):
    finding = _high(led)
    gates.decide_finding(led, finding, "accept", "We accept this for the prototype.", HUMAN)
    assert agents.build_briefing(led.state(), "Electrical")["accepted_risks"][0]["decision"] == "accept"


@pytest.mark.asyncio
async def test_verifier_is_told_what_was_decided_and_what_is_open(led):
    decided, still = _high(led), led.propose(Actor.agent("verifier"), "finding", run=1, severity="high", safety=False, text="still bad", related=[])
    gates.decide_finding(led, decided, "dismiss", "Covered by the supplier's test.", HUMAN)
    gateway = Scripted(agents.VerifyOut(findings=[]))
    await agents.run_verifier(led, gateway)
    assert "Covered by the supplier" in gateway.prompts[0] and "still bad" in gateway.prompts[0]


def test_every_agent_prompt_explains_every_input():
    from buildanything.agent_specs import SPECS, render_input, system_prompt

    for spec in SPECS.values():
        prompt = system_prompt(spec)
        assert "KIND_HELP" not in prompt
        assert all(f"<{name}>" in prompt for name in spec.inputs)
        with pytest.raises(ValueError):
            render_input(spec, {})


def test_findings_are_decided_only_through_their_own_route(client, tmp_path):
    pid = client.post("/api/projects", json={"name": "A", "aim": "B", "scope": "C"}).json()["id"]
    led = Ledger(next(tmp_path.rglob("events.jsonl")).parent.parent)
    finding = _high(led)
    assert client.post(f"/api/projects/{pid}/records/{finding}/reject").status_code == 400
    assert client.post(f"/api/projects/{pid}/findings/{finding}/decide", json={"decision": "dismiss", "reason": ""}).status_code == 400
    done = client.post(f"/api/projects/{pid}/findings/{finding}/decide", json={"decision": "accept", "reason": "Accepted for the bench prototype."})
    assert done.status_code == 200 and led.state().records[finding]["decision"]["decision"] == "accept"
    assert client.get("/api/agents").status_code == 200 and len(client.get("/api/agents").json()) == 31


def test_simulation_first_and_automatic_tests_over_api(client):
    pid = client.post("/api/projects", json={"name": "Arm", "aim": "Move", "scope": "Bench"}).json()["id"]
    assert client.get(f"/api/projects/{pid}/scope").json() == {"simulation_first": None}
    assert client.post(f"/api/projects/{pid}/scope", json={"simulation_first": True, "reason": ""}).status_code == 400
    assert client.post(f"/api/projects/{pid}/scope", json={"simulation_first": True, "reason": "No hardware yet"}).json() == {"simulation_first": "No hardware yet"}
    assert client.get(f"/api/projects/{pid}/scope").json() == {"simulation_first": "No hardware yet"}
    setup = client.get(f"/api/projects/{pid}/electrical/firmware/laptop/setup").json()
    assert setup["autotest"] is False
    assert client.post(f"/api/projects/{pid}/electrical/firmware/laptop/autotest", json={"allowed": True}).status_code == 400  # no such program yet
