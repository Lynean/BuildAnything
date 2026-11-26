import pytest

from buildanything import agents as ag, changes, gates, traceability, headquarters as hq, interfaces as itf
from buildanything.citations import Paper
from buildanything.ledger import Actor, Ledger, PermissionDenied

HUMAN = Actor.human()
QUOTE = "Wings with independent drive produce stable hover at low speed."


class Papers:
    def search(self, query):
        return [Paper(id="1", title="T", url="u", text=QUOTE)]

    def get(self, paper_id):
        return Paper(id="1", title="T", url="u", text=QUOTE)


class Gateway:
    """Answers each agent by name, and can call the tools a real agent would."""

    def __init__(self, ledger=None, tool_calls=None):
        self.calls, self.tool_calls, self.ledger = [], tool_calls or {}, ledger

    async def run(self, *, agent, values, output_type=str, tools=(), **_):
        self.calls.append((agent, values, [getattr(t, "__name__", type(t).__name__) for t in tools]))
        for name, args in self.tool_calls.pop(agent, []):
            tool = next(t for t in tools if getattr(t, "__name__", "") == name)
            result = tool(**args)
            if hasattr(result, "__await__"):
                result = await result
            self.last_tool_result = result
        if agent == "test_planner":
            return traceability.TestPlan(tests=[], cannot_test=[r["key"] + " is vague" for r in values["requirements"]])
        if agent == "router":
            return changes.Route(action="forward", handler="None", reason="Routine.", earlier_id=None)
        if agent == "concept":
            return ag.ConceptOut(reply="A proposal", summary="Two degrees of freedom on a pan-tilt head.", decisions=["Use two degrees of freedom"], open_questions=["Where does processing live?"])
        if agent == "research":
            for tool in tools:
                if callable(tool):
                    tool("wings")
            return ag.ResearchOut(claims=[ag.ClaimOut(paper_id="1", statement="s", quote=QUOTE, applicability="a")])
        if agent == "controller":
            return ag.PlanOut(requirements=[ag.RequirementOut(key="R1", text="t", verification="v")], tasks=[ag.TaskOut(key="T1", department="Software", title="t", weeks=1, depends_on=[])])
        if agent == "verifier":
            return self.verdict if hasattr(self, "verdict") else ag.VerifyOut(findings=[])
        return f"{agent} answered"


@pytest.fixture
def led(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.append(HUMAN, "project.created", name="T", aim="Track a person", scope="Bench")
    return ledger


@pytest.fixture
def services():
    return hq.Services(Papers())


async def go(led, gateway, services):
    await hq.advance(led, gateway, services)


def agents_run(gateway):
    return [c[0] for c in gateway.calls]


@pytest.mark.asyncio
async def test_concept_partner_speaks_first_and_waits_for_approval(led, services):
    gateway = Gateway()
    await go(led, gateway, services)
    assert agents_run(gateway) == ["concept"] and gateway.calls[0][1]["message"] == ""
    assert hq.next_action(led) is None and hq.needs(led)[0]["kind"] == "concept"
    await go(led, gateway, services)
    assert agents_run(gateway) == ["concept"]


@pytest.mark.asyncio
async def test_the_whole_pipeline_needs_only_reviews(led, services):
    gateway = Gateway()
    await go(led, gateway, services)
    hq.approve_concept(led, HUMAN)
    state = led.state()
    assert state.of_type("decision", "confirmed")[0]["fields"]["text"] == "Use two degrees of freedom" and "G0" in state.gates_approved
    await go(led, gateway, services)
    assert agents_run(gateway)[-1] == "research" and hq.needs(led)[0]["kind"] == "claims"

    claim = led.state().of_type("claim", "proposed")[0]["id"]
    led.append(HUMAN, "record.confirmed", id=claim)
    assert hq.needs(led)[0] == {"kind": "gate", "gate": "G1", "text": "Sources are reviewed. Continue to Plan on the Journey screen to start the plan."}
    gates.approve(led, "G1", HUMAN)
    await go(led, gateway, services)
    assert agents_run(gateway)[-2:] == ["controller", "verifier"] and hq.needs(led)[0]["kind"] == "plan"

    for record in led.state().of_type("requirement", "proposed") + led.state().of_type("task", "proposed"):
        led.append(HUMAN, "record.confirmed", id=record["id"])
    assert hq.needs(led)[0]["gate"] == "G2"
    gates.approve(led, "G2", HUMAN)
    await go(led, gateway, services)
    assert agents_run(gateway)[-5:] == ["lead_electrical", "lead_mechanical", "lead_software", "lead_procurement", "test_planner"]
    assert hq.thread(led.events(), "Software")[0]["role"] == "agent" and hq.next_action(led) is None


@pytest.mark.asyncio
async def test_nothing_passes_a_review_by_itself(led, services):
    gateway = Gateway()
    await go(led, gateway, services)
    hq.approve_concept(led, HUMAN)
    await go(led, gateway, services)
    state = led.state()
    assert "G1" not in state.gates_approved and state.of_type("claim", "confirmed") == []


def test_agents_cannot_approve_the_concept(led):
    with pytest.raises(PermissionDenied):
        led.append(Actor.agent("concept"), "concept.approved", summary="s", decisions=[], open_questions=[])
    with pytest.raises(PermissionDenied):
        led.append(Actor.system("headquarters"), "research.waived", reason="because")


@pytest.mark.asyncio
async def test_concept_needs_a_summary_and_only_approves_once(led, services):
    with pytest.raises(hq.HeadquartersError, match="summarised"):
        hq.approve_concept(led, HUMAN)
    await go(led, Gateway(), services)
    hq.approve_concept(led, HUMAN)
    with pytest.raises(hq.HeadquartersError, match="already"):
        hq.approve_concept(led, HUMAN)


@pytest.mark.asyncio
async def test_blank_aim_and_scope_do_not_block_approval(tmp_path, services):
    led = Ledger(tmp_path)
    led.append(HUMAN, "project.created", name="T", aim="", scope="")
    await go(led, Gateway(), services)
    hq.approve_concept(led, HUMAN)
    assert "G0" in led.state().gates_approved


@pytest.mark.asyncio
async def test_old_conversations_get_a_summary_without_a_new_prompt(led, services):
    led.propose(Actor.agent("concept"), "concept_draft", message="hi", reply="old", speculative=True)
    gateway = Gateway()
    await go(led, gateway, services)
    assert agents_run(gateway) == ["concept"] and "old" in gateway.calls[0][1]["earlier_discussion"]


@pytest.mark.asyncio
async def test_failures_stop_and_wait_for_a_person(led, services):
    class Broken(Gateway):
        async def run(self, **kwargs):
            self.calls.append((kwargs["agent"], kwargs["values"], []))
            raise RuntimeError("no connection")

    gateway = Broken()
    await go(led, gateway, services)
    await go(led, gateway, services)
    assert len(gateway.calls) == 1 and "no connection" in hq.needs(led)[0]["text"]
    hq.retry(led, HUMAN)
    assert hq.next_action(led) == "concept"


@pytest.mark.asyncio
async def test_no_sources_offers_a_way_forward(led, services):
    class Empty(Gateway):
        async def run(self, *, agent, **kwargs):
            if agent == "research":
                self.calls.append((agent, {}, []))
                return ag.ResearchOut(claims=[])
            return await super().run(agent=agent, **kwargs)

    gateway = Empty()
    await go(led, gateway, services)
    hq.approve_concept(led, HUMAN)
    await go(led, gateway, services)
    assert agents_run(gateway).count("research") == 2 and hq.needs(led)[0]["kind"] == "no_sources"
    with pytest.raises(hq.HeadquartersError):
        hq.waive_research(led, "", HUMAN)
    hq.waive_research(led, "A common bench setup with no novel claims.", HUMAN)
    assert not gates.missing("G1", led.state())


def _high(text="bad"):
    return ag.VerifyOut(findings=[ag.FindingOut(severity="high", safety=False, text=text, related=[])])


async def _to_plan(led, gateway, services):
    await go(led, gateway, services)
    hq.approve_concept(led, HUMAN)
    await go(led, gateway, services)
    led.append(HUMAN, "record.confirmed", id=led.state().of_type("claim", "proposed")[0]["id"])
    gates.approve(led, "G1", HUMAN)
    await go(led, gateway, services)


@pytest.mark.asyncio
async def test_fix_it_revises_the_plan_and_reviews_it_again(led, services):
    gateway = Gateway()
    gateway.verdict = _high("No safety stop")
    await _to_plan(led, gateway, services)
    finding = gates.open_high_findings(led.state())[0]["id"]
    gateway.verdict = ag.VerifyOut(findings=[])
    gates.decide_finding(led, finding, "fix", "", HUMAN)
    await go(led, gateway, services)
    controller = [c for c in gateway.calls if c[0] == "controller"]
    assert len(controller) == 2 and "No safety stop" in controller[1][1]["feedback"] and agents_run(gateway)[-1] == "verifier"
    state = led.state()
    assert len([r for r in state.of_type("requirement") if r["status"] == "superseded"]) == 1 and len(state.of_type("requirement", "proposed")) == 1
    gates.decide_finding(led, finding, "fixed", "", HUMAN)
    assert not gates.open_high_findings(led.state())
    await go(led, gateway, services)
    assert len([c for c in gateway.calls if c[0] == "controller"]) == 2


@pytest.mark.asyncio
async def test_revision_waits_until_every_serious_finding_is_decided(led, services):
    gateway = Gateway()
    gateway.verdict = ag.VerifyOut(findings=[*_high("one").findings, *_high("two").findings])
    await _to_plan(led, gateway, services)
    first, second = [f["id"] for f in gates.open_high_findings(led.state())]
    gates.decide_finding(led, first, "fix", "", HUMAN)
    assert hq.next_action(led) is None
    gates.decide_finding(led, second, "accept", "Accepted for the bench prototype.", HUMAN)
    assert hq.next_action(led) == "revise"


@pytest.mark.asyncio
async def test_concept_partner_can_reach_electrical_through_headquarters(led, services):
    gateway = Gateway(tool_calls={"concept": [("ask_department", {"department": "Electrical", "deliverable": "Whether the board has enough pins for the camera", "used_for": "Deciding where processing lives"})]})
    await hq.talk_to_concept(led, gateway, "Will it fit?", services)
    assert gateway.last_tool_result == "lead_electrical answered"
    request = hq.requests_view(led.events())[0]
    assert (request["from"], request["to"], request["status"]) == ("Headquarters", "Electrical", "answered")
    assert hq.thread(led.events(), "Electrical")[0]["role"] == "request"


@pytest.mark.asyncio
async def test_answers_cannot_start_more_requests(led, services):
    gateway = Gateway(tool_calls={"lead_software": [("ask_department", {"department": "Electrical", "deliverable": "the camera connector pinout", "used_for": "wiring the camera"})]})
    await hq.talk_to_department(led, gateway, "Software", "hello", services)
    nested = next(c for c in gateway.calls if c[0] == "lead_electrical")
    top = next(c for c in gateway.calls if c[0] == "lead_software")
    assert "ask_department" in top[2] and "ask_department" not in nested[2]


@pytest.mark.asyncio
async def test_requests_are_limited_and_cannot_target_yourself(led):
    gateway = Gateway()
    tool = hq.department_tool(led, gateway, "Software", hq.Services(Papers()))
    assert "Choose one of" in await tool("Software", "the camera connector pinout", "wiring the camera")
    assert "Choose one of" in await tool("Marketing", "the camera connector pinout", "wiring the camera")
    for _ in range(hq.MAX_REQUESTS_PER_RUN):
        assert await tool("Electrical", "the camera connector pinout", "wiring the camera") == "lead_electrical answered"
    assert "limit" in await tool("Electrical", "the camera connector pinout", "wiring the camera")


@pytest.mark.asyncio
async def test_lead_tools_only_propose(led, services):
    gateway = Gateway(tool_calls={"lead_electrical": [("propose_components", {"components": [{"part_number": "SG90", "role": "Pan servo"}]})], "lead_mechanical": [("propose_parts", {"parts": [{"number": "P1", "name": "Bracket", "material": "PLA", "process": "3D print", "quantity": 1, "why": "Holds the servo"}]})]})
    await hq.talk_to_department(led, gateway, "Electrical", "go", services)
    await hq.talk_to_department(led, gateway, "Mechanical", "go", services)
    state = led.state()
    assert state.of_type("component", "proposed")[0]["fields"]["part_number"] == "SG90"
    proposal = state.of_type("part_proposal", "proposed")[0]["id"]
    assert not state.of_type("part")
    hq.mark_part_proposal(led, proposal, True, HUMAN)
    assert led.state().of_type("part", "confirmed")[0]["fields"]["number"] == "P1"


@pytest.mark.asyncio
async def test_software_design_is_recorded_and_agents_cannot_write_elsewhere(led, services):
    gateway = Gateway(tool_calls={"lead_software": [("propose_design", {"title": "Dashboard", "html": "<h1>Hi</h1>"})]})
    await hq.talk_to_department(led, gateway, "Software", "go", services)
    assert led.state().of_type("design_proposal")[0]["fields"]["title"] == "Dashboard"
    with pytest.raises(PermissionDenied):
        led.propose(Actor.agent("software"), "decision", text="x")


@pytest.mark.asyncio
async def test_every_lead_gets_the_briefing(led, services):
    gateway = Gateway()
    await hq.talk_to_department(led, gateway, "Procurement", "go", services)
    assert "accepted_risks" in gateway.calls[0][1]["briefing"] and "orders" in gateway.calls[0][1]["department_state"]


def test_interface_summary_is_available_to_leads(led):
    itf.save_packet(led, {"name": "PING", "id": 1, "transport": {"type": "udp", "port": 9}, "producer": "a", "consumers": ["b"], "fields": [{"name": "n", "type": "uint8"}]}, HUMAN)
    assert "PING" in hq._department_state(led, "Software", hq.Services(Papers()))["packets"]


@pytest.mark.asyncio
async def test_answering_a_request_is_not_a_first_proposal_and_openings_do_not_ask(led, services):
    gateway = Gateway()
    await hq.department_tool(led, gateway, "Software", services)("Electrical", "the camera connector pinout", "wiring the camera")
    for gate in ("G0", "G1", "G2"):
        led.append(HUMAN, "gate.approved", gate=gate)
    assert hq.next_action(led) == "open:Electrical"
    await go(led, gateway, services)
    openings = [c for c in gateway.calls if c[1].get("message") == ""]
    assert [c[0] for c in openings] == ["lead_electrical", "lead_mechanical", "lead_software", "lead_procurement"]
    assert all("ask_department" not in c[2] for c in openings)
    assert "Electrical" in openings[1][1]["department_state"]["other_departments_latest"]


@pytest.mark.asyncio
async def test_research_must_search_before_answering(led, services):
    seen = {}

    class Spy(Gateway):
        async def run(self, **kwargs):
            seen.update(kwargs)
            return await super().run(**kwargs)

    await ag.run_research(led, Spy(), Papers(), "wings")
    assert seen["first_tool"] == "search_papers"


@pytest.mark.asyncio
async def test_approved_decisions_lose_draft_wording(led, services):
    class Tentative(Gateway):
        async def run(self, *, agent, **kwargs):
            return ag.ConceptOut(reply="r", summary="s", decisions=["Proposed: use two axes"], open_questions=[])

    await go(led, Tentative(), services)
    hq.approve_concept(led, HUMAN)
    assert led.state().of_type("decision", "confirmed")[0]["fields"]["text"] == "Use two axes"


@pytest.mark.asyncio
async def test_an_unreachable_search_is_not_reported_as_no_sources_found(led, services):
    class Down(Papers):
        def search(self, query):
            raise TimeoutError("read timed out")

    with pytest.raises(ag.ResearchUnavailable, match="TimeoutError"):
        await ag.run_research(led, Gateway(), Down(), "wings")

    gateway = Gateway()
    await go(led, gateway, hq.Services(Down()))
    hq.approve_concept(led, HUMAN)
    await go(led, gateway, hq.Services(Down()))
    need = hq.needs(led)[0]
    assert need["kind"] == "no_sources" and "couldn't be reached" in need["text"] and agents_run(gateway).count("research") == 1
    hq.waive_research(led, "A common bench setup with no novel claims.", HUMAN)
    assert hq.next_action(led) is None and not gates.missing("G1", led.state())


@pytest.mark.asyncio
async def test_the_verifier_checks_both_ways_and_a_finding_can_wait_for_a_later_stage(led, services):
    gateway = Gateway()
    gateway.verdict = ag.VerifyOut(findings=[ag.FindingOut(severity="high", safety=False, kind="beyond_scope", text="R9 adds a fall-arrest rig nobody asked for", related=["R9"]),
                                             ag.FindingOut(severity="high", safety=True, text="Powered stair tests need a catch frame", related=[])])
    await _to_plan(led, gateway, services)
    verifier = [c for c in gateway.calls if c[0] == "verifier"][-1][1]
    assert "person_asked" in verifier and any(verifier["person_asked"])
    extra, later = sorted(gates.open_high_findings(led.state()), key=lambda f: f["fields"]["kind"] != "beyond_scope")
    assert extra["fields"]["kind"] == "beyond_scope"
    with pytest.raises(gates.GateError):
        gates.decide_finding(led, later["id"], "later", "", HUMAN)
    gates.decide_finding(led, later["id"], "later", "Before testing on the real stair.", HUMAN)
    gates.decide_finding(led, extra["id"], "fix", "", HUMAN)
    gateway.verdict = ag.VerifyOut(findings=[])
    await go(led, gateway, services)
    feedback = [c for c in gateway.calls if c[0] == "controller"][-1][1]["feedback"]
    assert "Remove each one" in feedback and "fall-arrest" in feedback and "catch frame" not in feedback
    assert led.state().records[later["id"]]["status"] == "confirmed"
