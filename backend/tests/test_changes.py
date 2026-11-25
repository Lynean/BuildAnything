import pytest

from buildanything import changes as ch, headquarters as hq, interfaces as itf, mechanical as mech, overview as ov
from buildanything.ledger import Actor, Ledger, PermissionDenied

HUMAN = Actor.human()
OK = ch.ChangeReview(recommendation="approve", summary="Fine.", risks=[], missed=[])
CAUTION = ch.ChangeReview(recommendation="caution", summary="Risky.", risks=["Heat"], missed=[ch.MissedImpact(department="Software", item="Motor tuning", why="Inertia changes", action="Retune")])


class Gateway:
    def __init__(self, verdict=OK, route=None):
        self.verdict, self.calls, self.route = verdict, [], route or ch.Route(action="ask_person", handler="None", reason="Needs a look.", earlier_id=None)

    async def run(self, *, agent, values, output_type=str, tools=(), **_):
        self.calls.append((agent, values, [getattr(t, "__name__", "") for t in tools]))
        if agent == "router":
            return self.route
        return self.verdict if agent == "change_reviewer" else f"{agent} on it"


@pytest.fixture
def led(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.append(HUMAN, "project.created", name="T", aim="A", scope="S")
    for gate in ("G0", "G1", "G2"):
        ledger.append(HUMAN, "gate.approved", gate=gate)
    return ledger


@pytest.fixture
def services():
    return hq.Services(None)


def part(led):
    mech.add_part(led, {"number": "P1", "name": "Bracket", "material": "PLA", "process": "3D print", "qty": 2, "mass_g": 10.0, "unit_cost": 1.0}, HUMAN)


def component(led, number="SG90"):
    record = led.propose(HUMAN, "component", part_number=number, role="Servo")
    led.append(HUMAN, "record.confirmed", id=record)
    return record


def net(led, comp):
    record = led.propose(HUMAN, "net", name="PWM", kind="signal", nominal_v=3.3, members=[{"component": comp, "pin": "S", "role": "input"}])
    led.append(HUMAN, "record.confirmed", id=record)
    return record


def requirement(led):
    record = led.propose(Actor.agent("controller"), "requirement", key="R1", text="Tracks at 1 m", verification="Bench")
    led.append(HUMAN, "record.confirmed", id=record)
    return record


def get(led, change):
    return next(c for c in ch.view(led) if c["id"] == change)


async def reviewed(led, change, verdict=OK):
    await ch.review(led, Gateway(verdict), change)


@pytest.mark.parametrize("args, text", [
    (("", "A good reason", "other", "", {}), "title"), (("T", "short", "other", "", {}), "why"), (("T", "A good reason", "magic", "", {}), "Unknown"),
    (("T", "A good reason", "part", "NOPE", {"material": "x"}), "part"), (("T", "A good reason", "component", "NOPE", {"part_number": "x"}), "component"), (("T", "A good reason", "requirement", "NOPE", {"text": "x"}), "requirement"), (("T", "A good reason", "packet", "NOPE", {}), "packet"),
])
def test_bad_requests_are_refused(led, args, text):
    with pytest.raises(ch.ChangeError, match=text):
        ch.propose(led, *args, HUMAN)


def test_part_impact_lists_reopened_checks_and_budget(led):
    part(led)
    mech.set_budget(led, "mass_g", 21.0, HUMAN)
    change = ch.propose(led, "Stronger bracket", "PLA creeps under load", "part", "P1", {"material": "PETG", "mass_g": 11.0}, HUMAN)
    impact = get(led, change)["impact"]
    items = {i["item"] for i in impact}
    assert "Strength check on P1" in items and "Mass budget" in items and any(i["department"] == "Procurement" for i in impact)
    assert "over the 21 g budget" in next(i for i in impact if i["item"] == "Mass budget")["why"]


def test_component_impact_follows_connections_code_and_orders(led):
    comp = component(led)
    net(led, comp)
    note = led.propose(Actor.agent("electrical"), "page_note", component=comp, part="SG90", datasheet="d", file="f", page=1, finding="x", why="y")
    src = led.root / "firmware/node/src"
    src.mkdir(parents=True)
    from buildanything import firmware as fw

    fw.write_files(led.root, "node", [fw.FileOut(path="src/main.cpp", owner="Electrical", blocks=[fw.BlockOut(title="Servo drive", description="Drives the servo from the datasheet timing.", based_on=[f"note:{note}"], code="int x;")])])
    led.append(HUMAN, "record.confirmed", id=led.propose(HUMAN, "order", supplier="Shop", items="2x SG90 servo", total=5))
    impact = ch.compute_impact(led, {"kind": "component", "target": comp, "details": {"part_number": "MG90S"}})
    items = {i["item"] for i in impact}
    assert {"Connection PWM", "Datasheet for MG90S", "node/src/main.cpp: Servo drive", "Order from Shop", "Buy MG90S"} <= items


def test_packet_impact_reaches_every_device(led):
    itf.save_packet(led, {"name": "PING", "id": 1, "transport": {"type": "udp", "port": 9}, "producer": "a", "consumers": ["b", "c"], "fields": [{"name": "n", "type": "uint8"}]}, HUMAN)
    items = {i["item"] for i in ch.compute_impact(led, {"kind": "packet", "target": "PING", "details": {}})}
    assert {"a using PING", "b using PING", "c using PING"} <= items


@pytest.mark.asyncio
async def test_nothing_changes_until_a_person_approves(led):
    part(led)
    change = ch.propose(led, "Stronger", "PLA creeps under load", "part", "P1", {"material": "PETG"}, Actor.agent("mechanical"))
    await reviewed(led, change)
    assert mech.current_parts(led.state())[0]["fields"]["material"] == "PLA"
    with pytest.raises(PermissionDenied):
        ch.approve(led, change, "", Actor.agent("mechanical"))
    assert mech.current_parts(led.state())[0]["fields"]["material"] == "PLA"


def test_approval_waits_for_the_review(led):
    part(led)
    change = ch.propose(led, "Stronger", "PLA creeps under load", "part", "P1", {"material": "PETG"}, HUMAN)
    with pytest.raises(ch.ChangeError, match="review"):
        ch.approve(led, change, "", HUMAN)


@pytest.mark.asyncio
async def test_caution_needs_a_reason_and_missed_items_become_work(led):
    part(led)
    change = ch.propose(led, "Stronger", "PLA creeps under load", "part", "P1", {"material": "PETG"}, HUMAN)
    await reviewed(led, change, CAUTION)
    with pytest.raises(ch.ChangeError, match="caution"):
        ch.approve(led, change, "", HUMAN)
    result = ch.approve(led, change, "Accepted for the prototype.", HUMAN)
    assert any(f["department"] == "Software" for f in result["followUps"])


@pytest.mark.asyncio
async def test_approving_a_part_revises_it_and_reopens_checks(led):
    part(led)
    change = ch.propose(led, "Stronger", "PLA creeps under load", "part", "P1", {"material": "PETG"}, HUMAN)
    await reviewed(led, change)
    ch.approve(led, change, "", HUMAN)
    latest = mech.current_parts(led.state())[0]["fields"]
    assert (latest["material"], latest["revision"]) == ("PETG", "B") and {c["check"] for c in mech.open_checks(led)} >= {"Strength", "Cost"}
    keys = {t["fields"]["key"] for t in led.state().of_type("task", "confirmed")}
    assert {"CR1-MECH", "CR1-PROC"} <= keys


@pytest.mark.asyncio
async def test_replacing_a_component_retires_its_connections(led):
    comp, wire = component(led), None
    wire = net(led, comp)
    change = ch.propose(led, "Metal gears", "Plastic gears strip under load", "component", comp, {"part_number": "MG90S"}, HUMAN)
    await reviewed(led, change)
    ch.approve(led, change, "", HUMAN)
    state = led.state()
    assert state.records[comp]["status"] == "rejected" and state.records[wire]["status"] == "superseded"
    assert state.of_type("component", "confirmed")[0]["fields"]["part_number"] == "MG90S"


@pytest.mark.asyncio
async def test_rewording_a_requirement_keeps_its_key(led):
    old = requirement(led)
    change = ch.propose(led, "Further", "The room is bigger than planned", "requirement", old, {"text": "Tracks at 3 m"}, HUMAN)
    await reviewed(led, change)
    ch.approve(led, change, "", HUMAN)
    state = led.state()
    assert state.records[old]["status"] == "superseded"
    new = state.of_type("requirement", "confirmed")[0]["fields"]
    assert (new["key"], new["text"], new["verification"]) == ("R1", "Tracks at 3 m", "Bench")


@pytest.mark.asyncio
async def test_rejecting_needs_a_reason_and_changes_nothing(led):
    part(led)
    change = ch.propose(led, "Stronger", "PLA creeps under load", "part", "P1", {"material": "PETG"}, HUMAN)
    with pytest.raises(ch.ChangeError, match="why"):
        ch.reject(led, change, "", HUMAN)
    ch.reject(led, change, "Not needed for the prototype.", HUMAN)
    assert mech.current_parts(led.state())[0]["fields"]["material"] == "PLA" and get(led, change)["status"] == "rejected"
    with pytest.raises(ch.ChangeError, match="open"):
        ch.reject(led, change, "Not needed for the prototype.", HUMAN)


@pytest.mark.asyncio
async def test_headquarters_reviews_then_briefs_departments_once(led, services):
    for department in hq.DEPARTMENTS:
        led.append(Actor.system("headquarters"), "hq.finished", step=f"open:{department}", label="x")
    part(led)
    change = ch.propose(led, "Stronger", "PLA creeps under load", "part", "P1", {"material": "PETG"}, Actor.agent("mechanical"))
    gateway = Gateway()
    await hq.advance(led, gateway, services)
    assert [c[0] for c in gateway.calls] == ["change_reviewer", "router"] and hq.next_action(led) is None
    ch.approve(led, change, "", HUMAN)
    await hq.advance(led, gateway, services)
    assert sorted(c[0] for c in gateway.calls[2:]) == ["lead_mechanical", "lead_procurement"]
    assert "Change 1 was approved" in gateway.calls[2][1]["message"]
    await hq.advance(led, gateway, services)
    assert len(gateway.calls) == 4


@pytest.mark.asyncio
async def test_leads_can_raise_a_request_but_not_decide(led, services):
    part(led)
    tools = hq._lead_tools(led, Gateway(), "Mechanical", services)
    raise_it = next(t for t in tools if getattr(t, "__name__", "") == "raise_change_request")
    result = raise_it("Stronger", "PLA creeps under load", "part", "P1", '{"material": "PETG"}')
    assert "id" in result and not mech.open_checks(led)
    assert raise_it("X", "A good reason here", "part", "P1", "not json")["error"]
    assert not any("approve" in getattr(t, "__name__", "") for t in tools)


@pytest.mark.asyncio
async def test_open_changes_reach_the_review_queue(led):
    part(led)
    change = ch.propose(led, "Stronger", "PLA creeps under load", "part", "P1", {"material": "PETG"}, HUMAN)
    await reviewed(led, change)
    item = next(i for i in ov.review_queue(led) if i["id"] == change)
    assert item["screen"] == "Changes" and item["type"] == "Change Request"


ROUTINE = ch.Route(action="forward", handler="Mechanical", reason="A routine correction.", earlier_id=None)


async def advance(led, gateway):
    await hq.advance(led, gateway, hq.Services(None))


def agent_change(led, details=None, kind="part", target="P1"):
    return ch.propose(led, "Correct the mass", "The measured mass differs", kind, target, details or {"mass_g": 12.0}, Actor.agent("mechanical"))


@pytest.mark.asyncio
async def test_routine_agent_changes_are_settled_without_interrupting(led):
    part(led)
    change = agent_change(led)
    await advance(led, Gateway(route=ROUTINE))
    item = get(led, change)
    assert item["status"] == "approved" and item["decision"]["by"] == "Headquarters"
    assert mech.current_parts(led.state())[0]["fields"]["mass_g"] == 12.0 and not any(i["id"] == change for i in ov.review_queue(led))


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict, text", [(CAUTION, "reviewer"), (ch.ChangeReview(recommendation="approve", summary="s", risks=[], missed=CAUTION.missed), "couldn't trace")])
async def test_the_router_cannot_wave_through_a_concern(led, verdict, text):
    part(led)
    change = agent_change(led)
    gateway = Gateway(verdict, route=ROUTINE)
    await advance(led, gateway)
    item = get(led, change)
    assert item["status"] == "open" and text in " ".join(item["escalation"]) and "router" not in [c[0] for c in gateway.calls]
    assert any(i["id"] == change for i in ov.review_queue(led))


@pytest.mark.asyncio
async def test_a_budget_problem_always_reaches_the_person(led):
    part(led)
    mech.set_budget(led, "mass_g", 21.0, HUMAN)
    change = agent_change(led, {"mass_g": 11.0})
    await advance(led, Gateway(route=ROUTINE))
    assert get(led, change)["status"] == "open" and "budget" in " ".join(get(led, change)["escalation"])


@pytest.mark.asyncio
async def test_requirement_and_component_changes_always_reach_the_person(led):
    req, comp = requirement(led), component(led)
    first = agent_change(led, {"text": "Tracks at 3 m"}, "requirement", req)
    second = agent_change(led, {"part_number": "MG90S"}, "component", comp)
    await advance(led, Gateway(route=ROUTINE))
    assert get(led, first)["status"] == "open" and get(led, second)["status"] == "open"
    assert led.state().records[comp]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_a_person_s_own_change_is_never_settled_for_them(led):
    part(led)
    change = ch.propose(led, "Correct", "The measured mass differs", "part", "P1", {"mass_g": 12.0}, HUMAN)
    await advance(led, Gateway(route=ROUTINE))
    assert get(led, change)["status"] == "open"


@pytest.mark.asyncio
async def test_declines_are_visible_and_can_be_reopened(led):
    part(led)
    change = agent_change(led)
    await advance(led, Gateway(route=ch.Route(action="decline", handler="None", reason="Outside the scope.", earlier_id=None)))
    assert get(led, change)["status"] == "rejected" and get(led, change)["decision"]["reason"] == "Outside the scope."
    with pytest.raises(PermissionDenied):
        ch.reopen(led, change, Actor.agent("mechanical"))
    ch.reopen(led, change, HUMAN)
    assert get(led, change)["status"] == "open" and any(i["id"] == change for i in ov.review_queue(led))


@pytest.mark.asyncio
async def test_duplicates_are_declined_by_code(led):
    part(led)
    first = agent_change(led)
    gateway = Gateway()
    await advance(led, gateway)
    second = agent_change(led)
    await advance(led, gateway)
    assert get(led, first)["status"] == "open" and "repeats change 1" in get(led, second)["decision"]["reason"]


def test_delegated_authority_is_narrow(led):
    for kind, fields in (("requirement", {"key": "R", "text": "t", "verification": "v"}), ("component", {"part_number": "X"})):
        record = led.propose(Actor.agent("controller" if kind == "requirement" else "electrical"), kind, **fields)
        with pytest.raises(PermissionDenied):
            led.append(Actor.delegated(), "record.confirmed", id=record)
    with pytest.raises(PermissionDenied):
        led.append(Actor.delegated(), "gate.approved", gate="G3")


def test_agents_cannot_settle_their_own_changes(led):
    for kind in ("change.auto_approved", "change.auto_rejected", "change.escalated"):
        with pytest.raises(PermissionDenied):
            led.append(Actor.agent("mechanical"), kind, id="x", reason="r", reasons=[])


class Asking(Gateway):
    pass


@pytest.mark.asyncio
async def test_router_sends_a_request_to_the_right_department(led):
    gateway = Gateway(route=ch.Route(action="forward", handler="Electrical", reason="This is about pins.", earlier_id=None))
    tool = hq.department_tool(led, gateway, "Software", hq.Services(None))
    assert await tool("Mechanical", "Which pins are free?", "For the driver") == "lead_electrical on it"
    assert hq.requests_view(led.events())[0]["to"] == "Electrical"


@pytest.mark.asyncio
async def test_router_reuses_an_earlier_answer_without_running_a_department(led):
    gateway = Gateway(route=ch.Route(action="forward", handler="Electrical", reason="Pins.", earlier_id=None))
    tool = hq.department_tool(led, gateway, "Software", hq.Services(None))
    await tool("Electrical", "Which pins are free?", "x")
    first = hq.requests_view(led.events())[0]["id"]
    gateway.route = ch.Route(action="answer_from_record", handler="None", reason="Answered before.", earlier_id=first)
    count = len(gateway.calls)
    assert "Already answered" in await hq.department_tool(led, gateway, "Mechanical", hq.Services(None))("Electrical", "What pins are free?", "x")
    assert [c[0] for c in gateway.calls[count:]] == ["router"]


@pytest.mark.asyncio
async def test_a_question_only_you_can_answer_comes_to_you_and_goes_back(led):
    gateway = Gateway(route=ch.Route(action="ask_person", handler="None", reason="Only you know.", earlier_id=None))
    reply = await hq.department_tool(led, gateway, "Electrical", hq.Services(None))("Software", "What markings are on the board?", "To identify it")
    assert "passed this to the person" in reply
    request = hq.requests_view(led.events())[0]
    assert request["status"] == "waiting_for_you" and next(i for i in ov.review_queue(led) if i["id"] == request["id"])["kind"] == "question"
    with pytest.raises(PermissionDenied):
        hq.answer_request(led, request["id"], "ESP-12K", Actor.agent("electrical"))
    hq.answer_request(led, request["id"], "ESP-12K", HUMAN)
    for department in hq.DEPARTMENTS:
        led.append(Actor.system("headquarters"), "hq.finished", step=f"open:{department}", label="x")
    await advance(led, gateway)
    last = [c for c in gateway.calls if c[0] == "lead_electrical"][-1]
    assert "ESP-12K" in last[1]["message"] and not any(i["id"] == request["id"] for i in ov.review_queue(led))


@pytest.mark.asyncio
async def test_a_department_is_told_when_a_request_is_its_own_job(led):
    gateway = Gateway(route=ch.Route(action="forward", handler="Procurement", reason="Suppliers are Procurement's work.", earlier_id=None))
    reply = await hq.department_tool(led, gateway, "Procurement", hq.Services(None))("Mechanical", "Who sells this?", "Buying list")
    assert "own department" in reply and "lead_mechanical" not in [c[0] for c in gateway.calls]
    assert "aim" in gateway.calls[0][1]["project"]


def test_an_approved_packet_change_lets_the_agent_change_that_packet_after_the_freeze(led):
    """After Gate 3, an agent dodged the freeze by saving the change as ARM_COMMAND_CHANGE_14. An approved change now lets it keep the name."""
    from buildanything import changes as ch
    from buildanything.ledger import Actor

    agent = Actor.agent("electrical")
    base = {"name": "CMD", "id": 3, "transport": {"type": "serial"}, "producer": "laptop", "consumers": ["board"], "fields": [{"name": "mode", "type": "uint8"}]}
    itf.save_packet(led, base, HUMAN)
    led.append(HUMAN, "gate.approved", gate="G3")
    grown = dict(base)
    grown["fields"] = [{"name": "session_id", "type": "uint32"}, {"name": "mode", "type": "uint8"}]  # reordered: a breaking change
    with pytest.raises(itf.InterfaceError, match="Never save it under a new name"):
        itf.save_packet(led, grown, agent)
    change = ch.propose(led, "Add a session ID", "Replay-safe re-enabling needs it.", "packet", "CMD", {}, agent)
    led.append(Actor.system("headquarters"), "change.reviewed", id=change, review={"recommendation": "approve", "summary": "ok", "risks": [], "missed": []})
    ch.approve(led, change, "Owner decided on session IDs.", HUMAN)
    saved = itf.save_packet(led, grown, agent)
    assert saved["name"] == "CMD" and led.state().packets["CMD"]["major"] == 2
