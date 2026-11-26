"""Each test here is a problem a first-year student actually hit in the 6DOF arm run."""
import json

import pytest

from buildanything import agent_specs, agents as ag, api, citations, codegen, designneeds as dn, firmware as fw, gatereport as gr, gates, headquarters as hq, interfaces as itf, mechanical as mech, traceability as tr
from buildanything.gateway import Photo
from buildanything.ledger import Actor, Ledger, PermissionDenied

HUMAN = Actor.human("Sam")
ELECTRICAL = Actor.agent("electrical")
SYSTEM = Actor.system("headquarters")


@pytest.fixture
def led(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.append(HUMAN, "project.created", name="Arm", aim="A", scope="S")
    for gate in ("G0", "G1", "G2", "G3"):
        ledger.append(HUMAN, "gate.approved", gate=gate)
    return ledger


def blockers(led, gate="G4"):
    return " ".join(gates.missing(gate, led.state(), led))


def component(led, number, fills="", confirm=True):
    record = led.propose(ELECTRICAL, "component", part_number=number, role="r", fills=fills)
    if confirm:
        led.append(HUMAN, "record.confirmed", id=record)
    return record


def sheet(led, record):
    led.propose(HUMAN, "datasheet", component=record, file="a.pdf", filename="a.pdf", pages=1, part_match=[1])
    led.append(Actor.system("datasheet"), "datasheet.read", component=record, created=0)


def net(led, name, members, kind="signal"):
    record = led.propose(ELECTRICAL, "net", name=name, kind=kind, nominal_v=3.3, members=members)
    led.append(HUMAN, "record.confirmed", id=record)
    return record


# ---- Gate 4 must notice what isn't there


def test_a_gate_can_no_longer_pass_with_nothing_listed(led):
    text = blockers(led)
    assert "List everything the design needs" in text and "List the parts" in text


def test_every_needed_item_must_be_chosen(led):
    dn.set_needed(led, ["Servos", "Power supply", "Fuse"], "To move safely", ELECTRICAL)
    servo = component(led, "MG90S", "Servos")
    sheet(led, servo)
    text = blockers(led)
    assert "Not chosen yet: Power supply, Fuse" in text and "Servos" not in text.split("Not chosen yet")[1]


def test_rejecting_the_only_power_supply_reopens_the_need(led):
    dn.set_needed(led, ["Power supply"], "w", ELECTRICAL)
    supply = component(led, "PSU1", "Power supply", confirm=False)
    assert dn.view(led)[0]["status"] == "chosen"
    led.append(HUMAN, "record.rejected", id=supply)
    assert dn.view(led)[0]["status"] == "open" and "Not chosen yet: Power supply" in blockers(led)


def test_a_person_can_say_an_item_is_not_needed_and_must_say_why(led):
    dn.set_needed(led, ["Fuse"], "w", ELECTRICAL)
    with pytest.raises(dn.NeedsError):
        dn.waive(led, dn.view(led)[0]["id"], "no", HUMAN)
    with pytest.raises(PermissionDenied):
        dn.waive(led, dn.view(led)[0]["id"], "The bench supply limits current.", ELECTRICAL)
    dn.waive(led, dn.view(led)[0]["id"], "The bench supply limits current.", HUMAN)
    assert "Not chosen" not in blockers(led)


def test_shortening_the_list_cannot_lose_a_decision(led):
    dn.set_needed(led, ["Fuse", "Servos"], "w", ELECTRICAL)
    component(led, "MG90S", "Servos")
    dn.set_needed(led, ["Fuse"], "w", ELECTRICAL)
    assert sorted(n["item"] for n in dn.view(led)) == ["Fuse", "Servos"]


def test_two_components_with_no_connections_blocks_the_gate(led):
    dn.mark_none(led, "Mechanical", "Electronics only project.", HUMAN)
    dn.set_needed(led, ["Board", "Servo"], "w", ELECTRICAL)
    for number, item in (("C6", "Board"), ("MG90S", "Servo")):
        sheet(led, component(led, number, item))
    assert "Draft the connection list" in blockers(led)


def test_a_component_left_off_the_wiring_blocks_the_gate(led):
    dn.set_needed(led, ["Board", "Servo", "Psu"], "w", ELECTRICAL)
    a, b, c = component(led, "C6", "Board"), component(led, "MG90S", "Servo"), component(led, "PSU", "Psu")
    for r in (a, b, c):
        sheet(led, r)
    net(led, "SERVO_1", [{"component": a, "pin": "GPIO3", "role": "output"}, {"component": b, "pin": "SIG", "role": "input"}])
    assert "Not connected to anything yet: PSU" in blockers(led)


def test_limits_must_be_read(led):
    record = component(led, "C6")
    led.propose(HUMAN, "datasheet", component=record, file="a.pdf", filename="a.pdf", pages=1, part_match=[1])
    assert "Read the limits from the datasheets of: C6" in blockers(led)
    led.append(Actor.system("datasheet"), "datasheet.read", component=record, created=0)
    assert "Read the limits" not in blockers(led)


def part(led, number="M-001", process="FDM 3D printing"):
    mech.add_part(led, {"number": number, "name": f"Link {number}", "material": "PLA", "process": process, "qty": 1}, HUMAN)


def test_a_printed_part_needs_a_model_and_bought_parts_do_not(led):
    part(led)
    part(led, "M-002", "Purchased hardware")
    text = blockers(led)
    assert "no CAD model yet: M-001." in text


def tagged(number):
    return {"bodies": [{"part": number, "name": "x", "volumeCm3": 1, "sizeMm": [1, 1, 1]}], "bodyCount": 1, "timeline": 2, "marker": 2, "parametric": True, "document": "U", "unhealthy": []}


def test_a_tagged_model_satisfies_the_gate_until_undone(led):
    part(led)
    change = led.propose(Actor.agent("mechanical"), "cad_change", title="P", intent="Make the part.", script="x", expected_bodies=1, document="U", goal="g")
    led.append(Actor.system("cad"), "cad.applied", id=change, before={"bodies": []}, after=tagged("M-001"), contacts=[], issues=[], approved_by="human")
    assert "M-001" in gr.modelled_parts(led)
    led.append(HUMAN, "cad.rolled_back", id=change)
    assert "M-001" not in gr.modelled_parts(led)


# ---- Gate 5 must see orders


def test_things_to_buy_must_be_ordered_or_owned(led):
    for g in ("G4",):
        led.append(HUMAN, "gate.approved", gate=g)
    board, servo = component(led, "ESP32-C6"), component(led, "MG996R")
    assert "Order these, or mark them as already owned: ESP32-C6, MG996R" in blockers(led, "G5")
    dn.mark_owned(led, board, HUMAN)
    order = led.propose(HUMAN, "order", supplier="Shop", items="2 x mg996r servo", status="Ordered")
    led.append(HUMAN, "record.confirmed", id=order)
    assert "Order these" not in blockers(led, "G5")
    assert servo


def test_only_a_person_can_say_they_own_something(led):
    with pytest.raises(PermissionDenied):
        dn.mark_owned(led, component(led, "C6"), ELECTRICAL)


# ---- Code must really use the packets and pins


PACKET = {"name": "SET_ANGLES", "id": 1, "transport": {"type": "serial"}, "rate_hz": None, "endianness": "little", "producer": "laptop", "consumers": ["esp32"], "fields": [{"name": "command_id", "type": "uint32"}, {"name": "a", "type": "float32", "unit": "deg"}], "checksum": True}


def saved(led):
    itf.save_packet(led, PACKET, HUMAN)


def write(led, device, name, text):
    target = led.root / "firmware" / device / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def test_an_app_that_never_sends_is_caught(led):
    saved(led)
    write(led, "laptop", "app.py", "from generated.packets import EvidenceLog\nprint('sliders')\n")
    assert any("laptop's code doesn't send SET_ANGLES" in t for t in itf.unwired(led.root, led.state()))


def test_an_app_that_sends_and_a_board_that_reads_pass(led):
    saved(led)
    write(led, "laptop", "app.py", "port.write(serial_frame(SetAngles(1, 2.0).encode()))\n")
    write(led, "esp32", "platformio.ini", "[env:d]\nboard = b\n")
    write(led, "esp32", "src/main.cpp", "ba_serial_unframe(line, bytes, 36); ba_set_angles_decode(bytes, 36, &c);\n")
    assert itf.unwired(led.root, led.state()) == []


def test_inventing_a_different_serial_format_is_caught(led):
    saved(led)
    write(led, "esp32", "src/main.cpp", 'if (strncmp(line, "PKT ", 4) == 0) ba_set_angles_decode(b, 36, &c);\n')
    assert any("esp32's code doesn't read SET_ANGLES yet over the serial link" in t for t in itf.unwired(led.root, led.state()))


def test_generated_serial_frames_round_trip(led):
    source = codegen.PY_PRELUDE
    space = {}
    exec(compile(source, "packets", "exec"), space)  # noqa: S102
    line = space["serial_frame"](b"\x01\xfe")
    assert line == b"@pkt 01fe\n" and space["serial_unframe"](line) == b"\x01\xfe" and space["serial_unframe"](b"@ba info ok\n") is None and space["serial_unframe"](b"@pkt zz\n") is None


def signal_net(led):
    a, b = component(led, "C6"), component(led, "SERVO")
    net(led, "Shoulder Servo", [{"component": a, "pin": "GPIO4", "role": "output"}, {"component": b, "pin": "SIG", "role": "input"}])
    net(led, "5V", [{"component": a, "pin": "5V", "role": "power_in"}, {"component": b, "pin": "VCC", "role": "power_in"}], "power")


def test_pins_come_only_from_the_connection_list(led):
    signal_net(led)
    assert fw.pin_defines(led.state()) == {"SHOULDER_SERVO": "4"}


def test_code_that_ignores_the_pin_list_is_caught(led):
    signal_net(led)
    write(led, "esp32", "platformio.ini", "[env:d]\nboard = b\n")
    write(led, "esp32", "src/main.cpp", "#define SHOULDER 7\n")
    assert "esp32's code doesn't use the agreed pin list yet." in fw.pin_problems(led.root, led.state())
    fw.write_pins(led.root, led.state(), "esp32")
    write(led, "esp32", "src/main.cpp", '#include "ba_pins.h"\nint a = BA_PIN_SHOULDER_SERVO;\n')
    assert fw.pin_problems(led.root, led.state()) == []


def test_changing_a_pin_makes_the_code_out_of_date_again(led):
    signal_net(led)
    write(led, "esp32", "platformio.ini", "[env:d]\nboard = b\n")
    fw.write_pins(led.root, led.state(), "esp32")
    write(led, "esp32", "src/main.cpp", '#include "ba_pins.h"\n')
    a = led.state().of_type("component", "confirmed")[0]["id"]
    net(led, "Elbow", [{"component": a, "pin": "GPIO5", "role": "output"}])
    assert fw.pin_problems(led.root, led.state())


# ---- Headquarters keeps the work moving


class Lead:
    def __init__(self):
        self.calls = []

    async def run(self, *, agent, values, tools=(), **_):
        self.calls.append((agent, values["message"], [getattr(t, "__name__", type(t).__name__) for t in tools]))
        return "done"


def opened(led):
    for department in hq.DEPARTMENTS:
        led.append(SYSTEM, "hq.finished", step=f"open:{department}", label="x")
    led.append(SYSTEM, "hq.finished", step="plan_tests", label="x")


@pytest.fixture
def driving(monkeypatch):
    monkeypatch.setattr(hq, "MAX_DRIVES", 5)


@pytest.mark.asyncio
async def test_headquarters_sends_each_department_what_the_gate_needs(led, driving):
    opened(led)
    gateway = Lead()
    await hq.advance(led, gateway, hq.Services(None))
    assert {c[0] for c in gateway.calls} == {"lead_electrical", "lead_mechanical"}
    assert "List everything the design needs" in next(c[1] for c in gateway.calls if c[0] == "lead_electrical")


@pytest.mark.asyncio
async def test_a_department_is_not_asked_twice_about_the_same_facts(led, driving):
    opened(led)
    gateway = Lead()
    await hq.advance(led, gateway, hq.Services(None))
    count = len(gateway.calls)
    await hq.advance(led, gateway, hq.Services(None))
    assert len(gateway.calls) == count


@pytest.mark.asyncio
async def test_new_facts_send_it_back_to_work(led, driving):
    opened(led)
    gateway = Lead()
    await hq.advance(led, gateway, hq.Services(None))
    count = len(gateway.calls)
    component(led, "C6")
    await hq.advance(led, gateway, hq.Services(None))
    assert len(gateway.calls) > count


@pytest.mark.asyncio
async def test_there_is_a_hard_limit_on_how_often_it_tries(led, driving):
    opened(led)
    gateway = Lead()
    for i in range(12):
        dn.set_needed(led, [f"Item {i}"], "w", ELECTRICAL)
        await hq.advance(led, gateway, hq.Services(None))
    calls = sum(1 for c in gateway.calls if c[0] == "lead_electrical")
    assert calls == 2 and calls <= hq.MAX_DRIVES  # two rounds that finished nothing stop it, well before the overall cap


@pytest.mark.asyncio
async def test_when_a_department_cannot_finish_the_person_is_told(led, driving):
    dn.mark_none(led, "Mechanical", "Electronics only project.", HUMAN)
    led.append(HUMAN, "record.confirmed", id=led.propose(Actor.agent("controller"), "requirement", key="R1", text="t", verification="v"))
    led.append(HUMAN, "record.confirmed", id=led.propose(Actor.agent("controller"), "test", key="T1", covers=["R1"], title="t", owner="Software", procedure="p", pass_if="q", metric=None, unit=None, min=None, max=None, uses=[]))
    opened(led)
    await hq.advance(led, Lead(), hq.Services(None))
    need = hq.needs(led)[0]
    assert need["kind"] == "stuck" and need["goto"] == "Electrical" and "Electrical couldn't finish" in need["text"]


def test_a_question_for_the_person_appears_on_headquarters(led):
    opened(led)
    led.append(SYSTEM, "request.raised", id="r1", sender="Software", to="Electrical", question="Which board is it?", why="w")
    led.append(SYSTEM, "request.routed", id="r1", to="You", action="ask_person", reason="r")
    need = next(n for n in hq.needs(led) if n["kind"] == "question")
    assert "Software needs an answer from you" in need["text"] and need["goto"] == "Review queue"


def test_every_need_says_where_to_go(led):
    dn.mark_none(led, "Mechanical", "Electronics only project.", HUMAN)
    dn.mark_none(led, "Electrical", "Mechanical only project here.", HUMAN)
    opened(led)
    component(led, "C6", confirm=False)
    need = hq.needs(led)[0]
    assert need["goto"] == "Electrical" and "Review 1 components" in need["text"]


def test_concept_stage_chat_is_kept_apart_from_real_work(led):
    hq._say(led, "Electrical", "agent", "Use an STM32")
    led.append(HUMAN, "concept.approved", summary="s", decisions=[], open_questions=[])
    hq._say(led, "Electrical", "agent", "Using the C6")
    assert [m["early"] for m in hq.thread(led.events(), "Electrical")] == [True, False]


@pytest.mark.asyncio
async def test_a_concept_question_cannot_add_parts_to_the_design(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.append(HUMAN, "project.created", name="A", aim="A", scope="S")
    names = [getattr(t, "__name__", type(t).__name__) for t in hq._lead_tools(ledger, Lead(), "Electrical", hq.Services(None), nested=True)]
    assert names == ["WebSearchTool"]


def test_other_departments_see_the_latest_view_not_the_first(led):
    hq._say(led, "Electrical", "agent", "First idea")
    hq._say(led, "Electrical", "agent", "Final idea")
    assert hq._department_state(led, "Software", hq.Services(None))["other_departments_latest"]["Electrical"] == "Final idea"


# ---- Prompts and photos


def test_every_prompt_has_its_safety_rule_filled_in():
    for spec in agent_specs.SPECS.values():
        assert "{UNTRUSTED}" not in agent_specs.system_prompt(spec)


def test_photos_are_kept_and_checked(led):
    png = "data:image/png;base64,iVBORw0KGgo="
    name, photo = api.chat_photos(led, [{"name": "my board.png", "data": png}])[0]
    assert name == "my_board.png" and isinstance(photo, Photo) and list((led.root / "uploads" / "chat").iterdir())
    with pytest.raises(api.HTTPException):
        api.chat_photos(led, [{"name": "x", "data": "data:text/html;base64,AAAA"}])


# ---- CAD


GOOD = "def run(_context):\n    part('M-001', 'Link').box(0, 0, 0, 10, 10, 10)\n"


class OneScript:
    def __init__(self, script, title="Parts"):
        self.script, self.title, self.seen = script, title, None

    async def run(self, *, values, tools, **_):
        self.seen = values
        self.answer = next(t for t in tools if t.__name__ == "propose_change")(self.title, "Make all the arm parts for printing.", self.script)
        return "ok"


# ---- Research


class Pages:
    def search(self, query):
        return []

    def get(self, paper_id):
        return None


QUOTE = "The MG996R draws about 2.5 A when it stalls at 6 volts."


def research(output, tools_called=True):
    class G:
        async def run(self, *, tools, **_):
            if tools_called:
                next(t for t in tools if getattr(t, "__name__", "") == "open_web_page")("https://maker.example/mg996r")
            return output

    return G()


@pytest.mark.asyncio
async def test_a_quote_from_a_real_page_is_accepted(led, monkeypatch):
    monkeypatch.setattr(ag, "fetch_page", lambda url: ("MG996R spec", f"Header text. {QUOTE} Footer."))
    out = ag.ResearchOut(claims=[ag.ClaimOut(source_url="https://maker.example/mg996r", statement="s", quote=QUOTE, applicability="a")])
    result = await ag.run_research(led, research(out), Pages(), "servos")
    claim = led.state().of_type("claim", "proposed")[0]["fields"]
    assert result["accepted"] == 1 and claim["url"] == "https://maker.example/mg996r" and claim["quote_verified"]


@pytest.mark.asyncio
async def test_a_quote_not_on_the_page_is_refused(led, monkeypatch):
    monkeypatch.setattr(ag, "fetch_page", lambda url: ("T", "Some other text entirely about nothing relevant here."))
    out = ag.ResearchOut(claims=[ag.ClaimOut(source_url="https://maker.example/mg996r", statement="s", quote=QUOTE, applicability="a")])
    assert (await ag.run_research(led, research(out), Pages(), "servos"))["accepted"] == 0


@pytest.mark.asyncio
async def test_a_page_the_agent_never_opened_is_refused(led):
    out = ag.ResearchOut(claims=[ag.ClaimOut(source_url="https://maker.example/x", statement="s", quote=QUOTE, applicability="a")])
    assert (await ag.run_research(led, research(out, tools_called=False), Pages(), "servos"))["accepted"] == 0


@pytest.mark.parametrize("url", ["http://127.0.0.1:8787/api", "http://localhost/x", "file:///c:/secrets", "http://169.254.169.254/latest"])
def test_research_cannot_open_private_addresses(url):
    with pytest.raises(citations.PageError):
        citations.fetch_page(url)


# ---- Second run: what a complete design looks like to a beginner


def test_a_part_with_tbd_in_its_name_is_not_chosen(led):
    component(led, "Inline fuse, rating TBD")
    assert "Too vague to buy: Inline fuse, rating TBD" in blockers(led)


def test_wire_needs_no_datasheet_but_only_a_person_can_say_so_for_others(led):
    led.append(HUMAN, "record.confirmed", id=led.propose(ELECTRICAL, "component", part_number="Red wire", role="r", generic=True))
    assert "Upload documents for: Red wire" not in blockers(led)
    other = component(led, "Fuse F1")
    assert "Upload documents for: Fuse F1" in blockers(led)
    with pytest.raises(PermissionDenied):
        dn.mark_no_datasheet(led, other, "A long enough reason.", ELECTRICAL)
    with pytest.raises(dn.NeedsError):
        dn.mark_no_datasheet(led, other, "no", HUMAN)
    dn.mark_no_datasheet(led, other, "This basic fuse has none published.", HUMAN)
    assert "Upload documents for: Fuse F1" not in blockers(led)


def test_one_read_datasheet_covers_every_use_of_that_part(led):
    first, second = component(led, "MG996R", "Base"), component(led, "MG996R", "Shoulder")
    for record in (first, second):
        led.propose(HUMAN, "datasheet", component=record, file="a.pdf", filename="a.pdf", pages=1, part_match=[1])
    led.append(Actor.system("datasheet"), "datasheet.read", component=first, created=0)
    assert "Read the limits" not in blockers(led)


# ---- Reading the history must stay honest when it's fast


def test_cached_history_still_notices_an_outside_edit(led):
    led.events()
    text = led.path.read_text(encoding="utf-8")
    led.path.write_text(text.replace('"Arm"', '"Ham"', 1), encoding="utf-8")
    with pytest.raises(hq.LedgerError if hasattr(hq, "LedgerError") else Exception, match="changed after"):
        led.events()


def test_callers_cannot_change_each_others_copy(led):
    led.state().records["x"] = {}
    led.events()[0]["kind"] = "tampered"
    assert "x" not in led.state().records and led.events()[0]["kind"] == "project.created"


def test_a_new_event_is_seen_immediately(led):
    before = len(led.events())
    led.state()
    led.propose(HUMAN, "component", part_number="X", role="r")
    assert len(led.events()) == before + 1 and led.state().of_type("component")


# ---- Third run: keeping one clean design


def propose_tool(led):
    batch = next(t for t in hq._lead_tools(led, Lead(), "Electrical", hq.Services(None)) if getattr(t, "__name__", "") == "propose_components")
    return lambda part_number, role, fills="": {k: v for k, v in batch([{"part_number": part_number, "role": role, "fills": fills}])["results"][0].items() if k != "part_number"}


def test_a_tbd_part_cannot_even_be_proposed(led):
    assert "error" in propose_tool(led)("Fuse, rating TBD", "r", "Fuse")
    assert not led.state().of_type("component")


def test_an_item_gets_one_part_and_changing_it_is_a_change_request(led):
    tool = propose_tool(led)
    assert "id" in tool("F1", "r", "Fuse")
    result = tool("F2", "r", "fuse")
    assert "raise_change_request" in result["error"] and len(led.state().of_type("component")) == 1


def test_the_same_servo_can_fill_several_items(led):
    tool = propose_tool(led)
    assert "id" in tool("MG996R", "r", "Base servo") and "id" in tool("MG996R", "r", "Elbow servo")


@pytest.mark.parametrize("number, text, expected", [("TowerPro MG996R", "MG996R servo specifications", True), ("ESP32-C6-DevKitC-1 (identity provisional)", "ESP32-C6 Series Datasheet", True), ("MG996R", "An unrelated op amp LM358", False)])
def test_a_brand_or_board_name_does_not_hide_the_part(number, text, expected):
    from buildanything import datasheets

    assert datasheets.part_matches(number, [text]) is expected


@pytest.mark.asyncio
async def test_a_new_wiring_draft_replaces_the_old_one(led):
    from buildanything import electrical_api as ea

    a, b = component(led, "C6"), component(led, "SERVO")
    net(led, "OLD", [{"component": a, "pin": "GPIO4", "role": "output"}, {"component": b, "pin": "SIG", "role": "input"}])

    class Draft:
        async def run(self, **_):
            return ea.DesignOut(nets=[ea.NetIn(name="NEW", kind="signal", nominal_v=3.3, members=[ea.Member(component=a, pin="GPIO5", role="output"), ea.Member(component=b, pin="SIG", role="input")])], notes=[])

    await ea.design_connections(led, Draft())
    assert not led.state().of_type("net", "confirmed") and led.state().of_type("net", "proposed")[0]["fields"]["name"] == "NEW"


# ---- Fourth run


def untestable_requirement(led):
    led.append(HUMAN, "record.confirmed", id=led.propose(Actor.agent("controller"), "requirement", key="R5", text="About S$150.", verification="Check"))
    led.append(SYSTEM, "test.untestable", items=["R5: no maximum is given."])


def test_a_vague_requirement_asks_the_person_what_they_would_accept(led):
    untestable_requirement(led)
    assert "Say what you'd accept for R5" in blockers(led) and "Every requirement needs a test" not in blockers(led)


def test_only_a_person_can_clarify_and_it_must_say_something(led):
    untestable_requirement(led)
    with pytest.raises(tr.TraceError):
        tr.clarify(led, "R5", "", HUMAN)
    with pytest.raises(PermissionDenied):
        tr.clarify(led, "R5", "At most S$170", ELECTRICAL)


@pytest.mark.asyncio
async def test_the_answer_goes_to_the_test_planner_once(led):
    untestable_requirement(led)
    opened(led)
    tr.clarify(led, "R5", "At most S$170", HUMAN)
    seen = []

    class Planner:
        async def run(self, *, agent, values, **_):
            seen.append(values["requirements"][0]["the_person_would_accept"])
            return tr.TestPlan(tests=[], cannot_test=["R5: still unclear"])

    await hq.advance(led, Planner(), hq.Services(None))
    await hq.advance(led, Planner(), hq.Services(None))
    assert seen == ["At most S$170"]


def test_a_replaced_wiring_list_no_longer_counts_as_connected(led):
    from buildanything import compat

    a, b = component(led, "C6"), component(led, "SERVO")
    old = net(led, "OLD", [{"component": a, "pin": "IO4", "role": "output"}, {"component": b, "pin": "SIG", "role": "input"}])
    led.append(Actor.system("electrical"), "record.superseded", id=old)
    net(led, "NEW", [{"component": a, "pin": "IO4", "role": "output"}, {"component": b, "pin": "SIG", "role": "input"}])
    assert not [r for r in compat.run_checks(led.state()) if "is on both" in r["message"]]


# ---- Staying fast must not mean staying wrong


def test_remembered_answers_change_when_the_project_does(led):
    assert "List the parts" in blockers(led)
    dn.mark_none(led, "Mechanical", "Electronics only project.", HUMAN)
    assert "List the parts" not in blockers(led)


def test_remembered_answers_change_when_the_code_does(led):
    saved(led)
    write(led, "laptop", "app.py", "print('nothing')\n")
    first = itf.unwired(led.root, led.state())
    write(led, "laptop", "app.py", "port.write(serial_frame(SetAngles(1, 2.0).encode()))\n")
    assert first != itf.unwired(led.root, led.state())
    write(led, "esp32", "platformio.ini", "[env:d]\nboard = b\n")
    led.append(HUMAN, "gate.approved", gate="G4")
    assert "esp32" in blockers(led, "G5")
    write(led, "esp32", "src/main.cpp", "ba_serial_unframe(line, bytes, 36); ba_set_angles_decode(bytes, 36, &c);\n")
    assert "esp32 has no code" not in blockers(led, "G5")


def test_a_remembered_answer_cannot_be_changed_by_a_caller(led):
    from buildanything import memo

    memo.remembered(led, "q", lambda: {"a": 1})["a"] = 2
    assert memo.remembered(led, "q", lambda: {"a": 3}) == {"a": 1}


def test_a_replacement_part_still_fills_the_item(led):
    dn.set_needed(led, ["Board"], "w", ELECTRICAL)
    old = component(led, "C6 generic", "Board")
    new = led.propose(HUMAN, "component", part_number="C6-DevKit", role="r", replaces=old)
    led.append(HUMAN, "record.confirmed", id=new)
    led.append(Actor.system("electrical"), "record.superseded", id=old)
    assert dn.view(led)[0]["status"] == "met" and dn.view(led)[0]["parts"] == ["C6-DevKit"]


def test_a_plain_cable_does_not_need_a_net(led):
    a, b = component(led, "C6"), component(led, "SERVO")
    led.append(HUMAN, "record.confirmed", id=led.propose(ELECTRICAL, "component", part_number="USB cable", role="r", generic=True))
    net(led, "S", [{"component": a, "pin": "GPIO4", "role": "output"}, {"component": b, "pin": "SIG", "role": "input"}])
    assert "Not connected" not in blockers(led)


def test_a_pdf_without_the_exact_part_number_goes_to_the_person(led):
    record = component(led, "XYZ-123")
    led.propose(HUMAN, "datasheet", component=record, file="a.pdf", filename="a.pdf", pages=1, part_match=False)
    waiting = gr.blockers("G4", led.state(), led)
    assert ("You", "Check the datasheet for XYZ-123: its exact part number isn't in the PDF. If it's the right document, press Read limits.") in waiting


def false_alarm(led, monkeypatch):
    from buildanything import compat

    monkeypatch.setattr(compat, "run_checks", lambda state: [{"status": "fail", "message": "VBUS exceeds 3.6 V"}])


def test_a_disputed_warning_goes_to_the_person_with_the_reason(led, monkeypatch):
    false_alarm(led, monkeypatch)
    assert ("Electrical", "Compatibility: VBUS exceeds 3.6 V") in gr._blockers("G4", led.state(), led)
    led.append(ELECTRICAL, "compat.disputed", message="VBUS exceeds 3.6 V", why="That limit is the chip's, and the board regulates USB.")
    assert any(o == "You" and "doesn't apply" in t and "regulates USB" in t for o, t in gr._blockers("G4", led.state(), led))


def test_only_a_person_can_accept_a_warning_and_it_shows_in_the_report(led, monkeypatch):
    false_alarm(led, monkeypatch)
    with pytest.raises(PermissionDenied):
        dn.accept_warning(led, "VBUS exceeds 3.6 V", "A long enough reason.", ELECTRICAL)
    with pytest.raises(dn.NeedsError):
        dn.accept_warning(led, "VBUS exceeds 3.6 V", "no", HUMAN)
    dn.accept_warning(led, "VBUS exceeds 3.6 V", "The board regulates its USB supply.", HUMAN)
    assert not any("VBUS" in t for _o, t in gr._blockers("G4", led.state(), led))
    assert "The board regulates its USB supply." in gr.report(led, "G4")


def part_tool(led):
    batch = next(t for t in hq._lead_tools(led, Lead(), "Mechanical", hq.Services(None)) if getattr(t, "__name__", "") == "propose_parts")
    return lambda number, name, material, process, quantity, why: batch([{"number": number, "name": name, "material": material, "process": process, "quantity": quantity, "why": why}])["results"][0]


def test_a_part_already_listed_cannot_be_suggested_again(led):
    tool = part_tool(led)
    assert "id" in tool("M-01", "Base", "PLA", "print", 1, "w")
    assert "already listed" in tool("M-01", "Base", "PLA", "print", 1, "w")["status"]
    hq.mark_part_proposal(led, led.state().of_type("part_proposal", "proposed")[0]["id"], True, HUMAN)
    assert "already listed" in tool("M-01", "Base", "PLA", "print", 1, "w")["status"]


def test_adding_a_duplicate_suggestion_clears_it_instead_of_failing(led):
    part(led, "M-01")
    record = led.propose(Actor.agent("mechanical"), "part_proposal", number="M-01", name="B", material="PLA", process="print", qty=1, why="w")
    hq.mark_part_proposal(led, record, True, HUMAN)
    assert not led.state().of_type("part_proposal", "proposed") and len(mech.current_parts(led.state())) == 1


def test_the_generated_pin_file_needs_no_description(led):
    signal_net(led)
    write(led, "esp32", "platformio.ini", "[env:d]\nboard = b\n")
    fw.write_pins(led.root, led.state(), "esp32")
    listed = fw.scan_workspace(led.root, "esp32")
    assert all(f.get("generated") for f in listed if f["path"].endswith("ba_pins.h"))


def test_two_builds_of_one_device_wait_for_each_other(tmp_path, monkeypatch):
    import threading
    import time

    running, most = [0], [0]

    def slow(root, device, timeout=900):
        running[0] += 1
        most[0] = max(most[0], running[0])
        time.sleep(0.05)
        running[0] -= 1
        return {}

    monkeypatch.setattr(fw, "_build", slow)
    workers = [threading.Thread(target=fw.build, args=(tmp_path, "board")) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert most[0] == 1


def test_a_test_file_cannot_stand_in_for_the_real_code(led):
    saved(led)
    write(led, "esp32", "platformio.ini", "[env:d]\nboard = b\n")
    write(led, "esp32", "src/main.cpp", "void loop() { Serial.read(); }\n")
    write(led, "esp32", "src/contract_codec_test.cpp", "ba_serial_unframe(l, b, 36); ba_set_angles_decode(b, 36, &c);\n")
    assert any("esp32's code doesn't read SET_ANGLES" in t for t in itf.unwired(led.root, led.state()))


# ---- A packet called STATUS broke the generated C header, so the firmware agent stubbed its program out


def packet_named(name):
    return {"name": name, "id": 2, "transport": {"type": "serial"}, "rate_hz": None, "endianness": "little", "producer": "esp32", "consumers": ["laptop"], "version": "1.0", "fields": [{"name": "state", "type": "uint8"}], "checksum": True, "raw": False}


def test_a_packet_called_status_compiles(led, tmp_path):
    import shutil
    import subprocess

    itf.save_packet(led, {k: v for k, v in packet_named("STATUS").items() if k not in ("version", "raw")}, HUMAN)
    text = codegen.generate_c(itf.packets(led.state()))
    assert text.count("} ba_status_t;") == 1 and "ba_result_t ba_status_decode" in text
    if shutil.which("gcc"):
        (tmp_path / "ba_packets.h").write_text(text, encoding="utf-8")
        (tmp_path / "a.c").write_text('#include "ba_packets.h"\\nint main(void){return 0;}\\n', encoding="utf-8")
        done = subprocess.run(["gcc", "-fsyntax-only", str(tmp_path / "a.c")], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("name", sorted(itf.RESERVED_NAMES))
def test_names_the_generated_code_uses_are_refused(name):
    assert any("used by the generated code" in p for p in itf.validate(packet_named(name)))


# ---- The command and report protocol, and the automatic checks


from buildanything import ci, protocol  # noqa: E402


def report(kind, for_person="ok", needs=(), risk="", severity="none", safety=False):
    return protocol.Report(type=kind, done=[], not_checked=[], provisional=[], needs=list(needs), risk=risk, severity=severity, safety=safety, for_person=for_person)


class Reporting(Lead):
    def __init__(self, *replies):
        super().__init__()
        self.replies = list(replies)

    async def run(self, *, agent, values, tools=(), **_):
        self.calls.append((agent, values.get("command"), values.get("message")))
        return self.replies.pop(0) if self.replies else report("DONE")


async def say(led, gateway, department="Electrical", text="go"):
    return await hq.talk_to_department(led, gateway, department, text, hq.Services(None))


@pytest.mark.asyncio
async def test_the_person_only_sees_the_plain_summary(led):
    assert await say(led, Reporting(report("DONE", "Done, chose a fuse."))) == "Done, chose a fuse."
    assert hq.thread(led.events(), "Electrical")[-1]["text"] == "Done, chose a fuse."


@pytest.mark.asyncio
async def test_every_command_and_report_is_on_record(led):
    await say(led, Reporting(report("PARTIAL")))
    kinds = [e["kind"] for e in led.events()]
    assert "command.sent" in kinds and protocol.latest(led.events(), "Electrical")["type"] == "PARTIAL"


@pytest.mark.asyncio
async def test_evidence_comes_from_the_checks_not_the_agent(led):
    await say(led, Reporting(report("DONE")))
    evidence = protocol.latest(led.events(), "Electrical")["evidence"]
    assert evidence and evidence[0]["result"] == "fail"


@pytest.mark.asyncio
async def test_blocked_on_the_person_becomes_one_question(led):
    need = protocol.Need(owner="Person", what="Which board do you have?")
    await say(led, Reporting(report("BLOCKED", needs=[need])))
    await say(led, Reporting(report("BLOCKED", needs=[need])))
    waiting = [r for r in hq.requests_view(led.events()) if r["status"] == "waiting_for_you"]
    assert [r["question"] for r in waiting] == ["Which board do you have?"]


@pytest.mark.asyncio
async def test_blocked_on_a_department_is_relayed_once(led):
    opened(led)
    await say(led, Reporting(report("BLOCKED", needs=[protocol.Need(owner="Mechanical", what="What is the arm mass?")])))
    assert hq.next_action(led).startswith("relay:")


@pytest.mark.asyncio
async def test_a_bug_stops_that_department_and_tells_the_person(led, driving):
    opened(led)
    await say(led, Reporting(report("BUG", risk="The header doesn't compile.")))
    assert not (hq.next_action(led) or "").startswith("drive:G4:Electrical")
    need = next(n for n in hq.needs(led) if n["kind"] == "bug")
    assert "header" in need["text"] and need["goto"] == "Electrical"


@pytest.mark.asyncio
async def test_a_serious_risk_becomes_a_finding_that_blocks_the_gate(led):
    await say(led, Reporting(report("RISK", risk="The arm can fall on your hand.", severity="high", safety=True)))
    assert "1 serious Verifier findings" in blockers(led)


@pytest.mark.asyncio
async def test_hold_stops_automatic_work_and_resume_restarts_it(led, driving):
    opened(led)
    led.append(HUMAN, "command.person", department="Electrical", type="HOLD", reason="")
    led.append(HUMAN, "command.person", department="Mechanical", type="HOLD", reason="")
    assert not hq.next_action(led)
    led.append(HUMAN, "command.person", department="Electrical", type="RESUME", reason="")
    assert hq.next_action(led).startswith("drive:G4:Electrical")


@pytest.mark.asyncio
async def test_recheck_from_a_person_sends_the_real_problems(led):
    opened(led)
    led.append(HUMAN, "command.person", department="Electrical", type="RECHECK", reason="Look at the fuse.")
    gateway = Reporting()
    await hq.advance(led, gateway, hq.Services(None))
    sent = gateway.calls[0][1]
    assert sent["type"] == "RECHECK" and sent["reason"] == "Look at the fuse." and sent["items"]


@pytest.mark.asyncio
async def test_several_approved_changes_cost_one_message_per_department(led):
    from buildanything import changes as ch

    opened(led)
    part(led, "P1")
    for material in ("PETG", "ABS"):
        change = ch.propose(led, f"Use {material}", "Stronger than before", "part", "P1", {"material": material}, HUMAN)
        led.append(SYSTEM, "change.reviewed", id=change, review=ch.ChangeReview(recommendation="approve", summary="s", risks=[], missed=[]).model_dump())
        ch.approve(led, change, "", HUMAN)
    gateway = Reporting()
    await hq.advance(led, gateway, hq.Services(None))
    briefed = [c for c in gateway.calls if c[1]["type"] == "CHANGE_NOTICE"]
    assert len(briefed) == len({c[0] for c in briefed})


def test_nothing_runs_again_when_nothing_changed(led):
    saved(led)
    write(led, "esp32", "platformio.ini", "[env:d]\nboard = b\n")
    write(led, "esp32", "src/main.cpp", "int a;\n")
    assert len(ci.pending(led)) >= 1
    for stage, key in ci.pending(led):
        ci.record(led, stage, key, True, [])
    assert ci.pending(led) == []
    write(led, "esp32", "src/main.cpp", "int b;\n")
    assert [stage for stage, _ in ci.pending(led)] == ["Firmware:esp32"]


def test_a_failed_build_blocks_the_gate_with_the_real_error(led):
    write(led, "esp32", "platformio.ini", "[env:d]\nboard = b\n")
    write(led, "esp32", "src/main.cpp", "int a;\n")
    stage, key = ci.pending(led)[0]
    ci.record(led, stage, key, False, ["main.cpp:1: expected ;"])
    assert "expected ;" in blockers(led)


@pytest.mark.asyncio
async def test_checks_run_before_any_agent_is_paid(led, driving, monkeypatch):
    monkeypatch.setattr(hq, "AUTO_CI", True)
    write(led, "esp32", "platformio.ini", "[env:d]\nboard = b\n")
    write(led, "esp32", "src/main.cpp", "int a;\n")
    opened(led)
    assert hq.next_action(led) == "ci"


class Reviewer:
    def __init__(self):
        self.calls = 0

    async def run(self, *, agent, **_):
        self.calls += 1
        assert agent == "gate_reviewer"
        return ag.VerifyOut(findings=[])


def complete(led):
    dn.mark_none(led, "Electrical", "Mechanical only project here.", HUMAN)
    dn.mark_none(led, "Mechanical", "Electronics only project.", HUMAN)
    led.append(HUMAN, "record.confirmed", id=led.propose(Actor.agent("controller"), "requirement", key="R1", text="t", verification="v"))
    led.append(HUMAN, "record.confirmed", id=led.propose(Actor.agent("controller"), "test", key="T1", covers=["R1"], title="t", owner="Software", procedure="p", pass_if="q", metric=None, unit=None, min=None, max=None, uses=[]))
    opened(led)


@pytest.mark.asyncio
async def test_the_gate_review_runs_once_and_only_when_the_checks_are_clean(led, monkeypatch):
    monkeypatch.setattr(hq, "GATE_REVIEW", True)
    opened(led)
    assert not hq.next_action(led)
    complete(led)
    reviewer = Reviewer()
    await hq.advance(led, reviewer, hq.Services(None))
    await hq.advance(led, reviewer, hq.Services(None))
    assert reviewer.calls == 1


def tool_named(led, department, name):
    return next(t for t in hq._lead_tools(led, Lead(), department, hq.Services(None)) if getattr(t, "__name__", "") == name)


@pytest.mark.asyncio
async def test_every_thing_to_buy_needs_a_price_and_a_budget(led):
    component(led, "MG996R")
    text = blockers(led)
    assert "Find a price and supplier for: MG996R" in text and "Set the parts budget" in text
    assert "error" in await tool_named(led, "Procurement", "set_price")("Not on the list", 1.0, 1, 1, "SGD", "Shop", "u")
    await tool_named(led, "Procurement", "set_price")("MG996R", 12.0, 1, 2, "SGD", "Shop", "u")
    mech.set_budget(led, "cost", 20.0, HUMAN)
    assert "about SGD 24, over your SGD 20 budget" in blockers(led)
    mech.set_budget(led, "cost", 30.0, HUMAN)
    assert "over your" not in blockers(led) and "Find a price" not in blockers(led)


@pytest.mark.asyncio
async def test_code_writes_per_reply_are_capped(led, monkeypatch):
    calls = []

    async def fake(*args, **kwargs):
        calls.append(args)
        return {"written": []}

    monkeypatch.setattr(hq.software, "generate", fake)
    write_code = tool_named(led, "Software", "write_code")
    for _ in range(hq.MAX_CODE_WRITES + 2):
        result = await write_code("laptop", "g", "")
    assert len(calls) == hq.MAX_CODE_WRITES and "error" in result


def test_the_procedure_reaches_every_lead_and_only_leads():
    assert "# Standard procedure" in agent_specs.system_prompt(agent_specs.SPECS["lead_electrical"])
    assert "# Standard procedure" not in agent_specs.system_prompt(agent_specs.SPECS["datasheet"])


def test_a_declared_pyserial_covers_import_serial(led):
    from buildanything import software

    write(led, "laptop", "app.py", "import serial\nprint(serial)\n")
    result = software.build(led.root, "laptop", ["pyserial"])
    assert not [w for w in result.get("warnings", []) + result.get("errors", []) if "serial" in w]


@pytest.mark.asyncio
async def test_a_bug_that_needs_a_decision_is_treated_as_blocked(led):
    await say(led, Reporting(report("BUG", risk="x", needs=[protocol.Need(owner="Software", what="Finish after the change.")])))
    assert protocol.latest(led.events(), "Electrical")["type"] == "BLOCKED"
    assert not [n for n in hq.needs(led) if n["kind"] == "bug"]


@pytest.mark.asyncio
async def test_a_waiting_change_request_is_not_asked_twice(led):
    await say(led, Reporting(report("BLOCKED", needs=[protocol.Need(owner="Person", what="Approve or reject change request chg_1.")])))
    assert not [r for r in hq.requests_view(led.events()) if r["status"] == "waiting_for_you"]


# ---- What Gate 4 requires, honest prices, and one delivery per department


async def priced(led, *args):
    return await tool_named(led, "Procurement", "set_price")(*args)


@pytest.mark.asyncio
async def test_a_reel_costs_one_reel_not_one_reel_per_capacitor(led):
    component(led, "100nF capacitor")
    result = await priced(led, "100nF capacitor", 12.0, 200, 4, "SGD", "Shop", "u")
    assert result["line_total"] == "SGD 12.00" and result["packs"] == "1"


@pytest.mark.asyncio
async def test_two_rolls_are_bought_when_one_is_too_short(led):
    component(led, "Wire")
    assert (await priced(led, "Wire", 5.0, 10, 15, "SGD", "Shop", "u"))["packs"] == "2"


@pytest.mark.asyncio
async def test_a_foreign_price_is_converted_before_it_is_added(led, monkeypatch):
    component(led, "A")
    component(led, "B")
    monkeypatch.setattr(dn, "exchange_rate", lambda source, target: 1.3 if source == "USD" else 1.0)
    await priced(led, "A", 10.0, 1, 1, "USD", "Shop", "u")
    await priced(led, "B", 10.0, 1, 1, "SGD", "Shop", "u")
    assert dn.estimated_total(led.state(), [], "SGD") == 23.0


@pytest.mark.asyncio
async def test_a_price_with_no_rate_is_refused_not_guessed(led, monkeypatch):
    component(led, "A")
    monkeypatch.setattr(dn, "exchange_rate", lambda source, target: None)
    assert "error" in await priced(led, "A", 10.0, 1, 1, "USD", "Shop", "u")
    assert "Find a price and supplier for: A" in blockers(led)


@pytest.mark.asyncio
async def test_changing_the_budget_currency_asks_for_new_prices(led):
    component(led, "A")
    await priced(led, "A", 10.0, 1, 1, "SGD", "Shop", "u")
    assert "Find a price" not in blockers(led)
    mech.set_budget(led, "cost", 100.0, HUMAN, "USD")
    assert "Find a price and supplier for: A" in blockers(led)
    assert dn.estimated_total(led.state(), [], "USD") == 0


def test_a_board_with_no_firmware_blocks_the_design_review(led):
    component(led, "ESP32-C6-DevKitC-1")
    assert "No firmware exists yet for ESP32-C6-DevKitC-1" in blockers(led)


def test_a_computer_program_does_not_count_as_board_firmware(led):
    component(led, "ESP32-C6-DevKitC-1")
    write(led, "laptop", "app.py", "print(1)\n")
    assert "No firmware exists yet" in blockers(led)
    write(led, "board", "platformio.ini", "[env:d]\nboard = b\n")
    assert "No firmware exists yet" not in blockers(led)


def test_a_part_that_only_mentions_a_microcontroller_is_not_a_board(led):
    led.append(HUMAN, "record.confirmed", id=led.propose(Actor.agent("electrical"), "component", part_number="PCA9685", role="Servo driver controlled by the microcontroller", fills=""))
    assert "No firmware exists yet" not in blockers(led)


def plan(led, **assemblies):
    return tool_named(led, "Mechanical", "set_assemblies")(json.dumps(assemblies), "w")


def test_parts_must_be_checked_against_the_whole_product(led):
    part(led, "M-01")
    assert "List every joint and assembly" in blockers(led)
    assert "id" in plan(led, Shoulder=["M-01"], Elbow=["M-02"], Wrist=[])
    text = blockers(led)
    assert "List every joint" not in text and "No parts listed yet for: Elbow, Wrist." in text
    assert "**Elbow:** M-02" in gr.report(led, "G4")


def test_a_bad_assembly_list_is_refused(led):
    assert "error" in tool_named(led, "Mechanical", "set_assemblies")("[1]", "w")
    assert "error" in tool_named(led, "Mechanical", "set_assemblies")("not json", "w")


def waiting_question(led, request_id, department, question):
    led.append(SYSTEM, "request.raised", id=request_id, sender=department, to="You", question=question, why="w")
    led.append(SYSTEM, "request.routed", id=request_id, to="You", action="ask_person", reason="r")


@pytest.mark.asyncio
async def test_answers_reach_a_department_together_in_one_call(led):
    opened(led)
    waiting_question(led, "r1", "Electrical", "Which board?")
    waiting_question(led, "r2", "Electrical", "What voltage?")
    assert "1 more in the Review queue" in next(n for n in hq.needs(led) if n["kind"] == "question")["text"]
    hq.answer_request(led, "r1", "ESP32", HUMAN)
    assert not (hq.next_action(led) or "").startswith("deliver:")
    hq.answer_request(led, "r2", "5 V", HUMAN)
    assert hq.next_action(led) == "deliver:r1,r2"
    gateway = Reporting()
    await hq.advance(led, gateway, hq.Services(None))
    assert len(gateway.calls) == 1 and "ESP32" in gateway.calls[0][2] and "5 V" in gateway.calls[0][2]
    assert not (hq.next_action(led) or "").startswith("deliver:")


@pytest.mark.asyncio
async def test_each_department_gets_its_own_delivery(led):
    opened(led)
    waiting_question(led, "r1", "Electrical", "Which board?")
    waiting_question(led, "r2", "Software", "Which app?")
    hq.answer_request(led, "r1", "ESP32", HUMAN)
    assert hq.next_action(led) == "deliver:r1"


def test_a_department_waiting_on_the_person_is_not_paid_to_work(led, driving):
    opened(led)
    assert hq.next_action(led).startswith("drive:G4:Electrical")
    waiting_question(led, "r1", "Electrical", "Which board?")
    assert not (hq.next_action(led) or "").startswith("drive:G4:Electrical")


def test_over_budget_points_to_procurement(led):
    assert hq._place("The parts come to about SGD 200, over your SGD 150 budget. Raise the budget.") == "Procurement"
    assert hq._place("Set the parts budget on the Mechanical screen.") == "Mechanical"


# ---- Fewer questions, shorter replies, tracing back to the person, and test stages


from buildanything import procurement as proc, traceability as tr  # noqa: E402


@pytest.mark.parametrize("what", ["Approve the CAD proposal before it is applied.", "Approve or decline the existing shoulder prototype CAD proposal.", "Confirm the proposed controller and shoulder-prototype components in the project before their limits are checked."])
def test_something_with_a_button_is_not_a_question(what):
    assert hq.question_for_person(what, []) is None


@pytest.mark.parametrize("what", ["Decide whether to approve or decline the proposed shoulder CAD.", "If you want the shoulder CAD applied, reply approve; otherwise reply decline.", "To proceed, explicitly approve or decline the CAD proposal."])
def test_a_reworded_approval_is_not_a_question(what):
    assert hq.question_for_person(what, []) is None


def test_a_real_question_mentioning_approval_of_something_else_is_kept():
    assert hq.question_for_person("Who needs to approve the lab booking?", [])


def test_only_the_fact_is_kept_from_a_mixed_question():
    assert hq.question_for_person("Send a photo of your board and approve or decline the proposed CAD.", []) == "Send a photo of your board."


def test_a_real_question_starting_with_confirm_is_kept():
    assert hq.question_for_person("Confirm which ESP32 board you own.", [])


def test_a_reworded_repeat_is_dropped():
    asked = ["Provide a clear photo or model number of the ESP32-C6 board you already own."]
    assert hq.question_for_person("Provide the requested board photo or model", asked) is None


def test_a_different_question_about_the_same_thing_is_kept():
    assert hq.question_for_person("What is the arm length?", ["What is the arm mass?"])


@pytest.mark.asyncio
async def test_a_department_can_not_ask_for_a_button_press(led):
    await say(led, Reporting(report("BLOCKED", needs=[protocol.Need(owner="Person", what="Approve the CAD proposal.")])))
    assert not [r for r in hq.requests_view(led.events()) if r["status"] == "waiting_for_you"]


@pytest.mark.asyncio
async def test_what_the_person_answered_reaches_every_department(led):
    waiting_question(led, "r1", "Electrical", "Which board?")
    hq.answer_request(led, "r1", "ESP32-C6", HUMAN)
    seen = {}

    class Watching(Reporting):
        async def run(self, *, agent, values, tools=(), **kwargs):
            seen.update(values)
            return await super().run(agent=agent, values=values, tools=tools, **kwargs)

    await say(led, Watching(), "Software")
    assert any(r["answer"] == "ESP32-C6" for r in seen["requests"])


@pytest.mark.asyncio
async def test_a_long_reply_is_cut_and_the_rest_kept_behind_show_more(led):
    long = "First sentence. Second sentence. " + "word " * 100
    assert await say(led, Reporting(report("DONE", long))) == "First sentence. Second sentence."
    message = hq.thread(led.events(), "Electrical")[-1]
    assert message["text"] == "First sentence. Second sentence." and "word word" in message["details"]


@pytest.mark.asyncio
async def test_requests_between_departments_stay_out_of_the_persons_chat(led):
    hq._say(led, "Electrical", "request", "How heavy is it?", "Mechanical")
    hq._say(led, "Electrical", "request", "Your answer", "You")
    assert [m["internal"] for m in hq.thread(led.events(), "Electrical")] == [True, False]


class Planning:
    def __init__(self, *requirements):
        self.requirements = requirements

    async def run(self, *, agent, **_):
        return ag.PlanOut(requirements=list(self.requirements), tasks=[ag.TaskOut(key="A", department="Software", title="t", weeks=1, depends_on=[])])


@pytest.mark.asyncio
async def test_a_requirement_only_counts_as_yours_if_you_said_it(led):
    led.append(HUMAN, "project.updated", aim="I can spend about S$150 on parts.", scope="Six joints, please.")
    await ag.run_plan(led, Planning(
        ag.RequirementOut(key="R1", text="Budget", verification="v", person_said="spend about s$150 on parts"),
        ag.RequirementOut(key="R2", text="Fuse", verification="v", person_said="", added_because="Stops a fire."),
        ag.RequirementOut(key="R3", text="Faked", verification="v", person_said="I need a prototype stage"),
    ))
    fields = {r["fields"]["key"]: r["fields"] for r in led.state().of_type("requirement")}
    assert fields["R1"]["person_said"] and not fields["R1"]["added_because"]
    assert fields["R2"]["added_because"] == "Stops a fire."
    assert not fields["R3"]["person_said"] and fields["R3"]["added_because"] == "From the approved concept and your answers in it."


def test_every_needed_item_needs_a_reason(led):
    tool = tool_named(led, "Electrical", "set_needed_items")
    assert "error" in tool(json.dumps({"Fuse": ""}))
    assert "error" not in tool(json.dumps({"Fuse": "Stops a stalled servo overheating the wires."}))
    assert dn.view(led)[0]["why"].startswith("Stops")


def test_confirm_all_leaves_safety_items_and_vague_parts(led):
    component(led, "MG996R", confirm=False)
    component(led, "Littelfuse 0218004", fills="Servo power fuse", confirm=False)
    component(led, "Regulator TBD", confirm=False)
    assert dn.confirm_all(led, HUMAN) == 1
    assert [c["fields"]["part_number"] for c in led.state().of_type("component", "confirmed")] == ["MG996R"]


def add_test(led, key="T1", stage="design", automatic="none", covers=("R1",), metric=None, confirm=True):
    if not any(r["fields"]["key"] == covers[0] for r in led.state().of_type("requirement")):
        led.append(HUMAN, "record.confirmed", id=led.propose(Actor.agent("controller"), "requirement", key=covers[0], text="t", verification="v"))
    record = led.propose(Actor.agent("controller"), "test", key=key, covers=list(covers), title="t", owner="Software", procedure="p", pass_if="q", metric=metric, unit=None, min=None, max=None, uses=[], stage=stage, automatic=automatic)
    if confirm:
        led.append(HUMAN, "record.confirmed", id=record)
    return record


def test_a_design_test_must_pass_before_the_design_review(led):
    record = add_test(led)
    assert "Do these design-stage tests on the Tests screen and record the results: T1." in blockers(led)
    assert hq._place("Do these design-stage tests on the Tests screen and record the results: T1.") == "Tests"
    tr.record_result(led, record, None, "passed", "Read it through on screen.", HUMAN)
    assert "design-stage" not in blockers(led)


def test_a_built_test_waits_for_the_built_thing(led):
    add_test(led, stage="built")
    assert "tests on the Tests screen" not in blockers(led)


def test_a_before_buying_test_blocks_orders_until_it_passes(led):
    record = add_test(led, stage="before_buying")
    order = {"supplier": "Shop", "currency": "SGD", "order_date": "2026-11-10", "total": 10, "buyer": "a", "funding": "b", "items": "x"}
    with pytest.raises(proc.ProcurementError, match="T1"):
        proc.add_order(led, order, HUMAN)
    assert "before-buying tests" in blockers(led, "G5")
    tr.record_result(led, record, None, "passed", "Checked the sums.", HUMAN)
    assert proc.add_order(led, order, HUMAN)


def test_an_automatic_test_follows_the_check_and_nobody_can_mark_it(led):
    saved(led)
    record = add_test(led, automatic="message_check")
    state = lambda: tr.view(led)["tests"][0]["state"]  # noqa: E731
    assert state() == "not_tested"
    stage, key = next(item for item in ci.pending(led) if item[0] == "Interface")
    ci.record(led, stage, key, False, ["bad"])
    assert state() == "failed" and "T1 needs its automatic check to pass" in blockers(led)
    ci.record(led, stage, key, True, [])
    assert state() == "passed"
    with pytest.raises(tr.TraceError):
        tr.record_result(led, record, None, "passed", "I say it passed.", HUMAN)


def fresh_at_gate_3(tmp_path):
    ledger = Ledger(tmp_path / "g3")
    ledger.append(HUMAN, "project.created", name="Arm", aim="A", scope="S")
    for gate in ("G0", "G1", "G2"):
        ledger.append(HUMAN, "gate.approved", gate=gate)
    return ledger


def test_gate_3_needs_approved_tests_and_a_passing_message_check(tmp_path):
    ledger = fresh_at_gate_3(tmp_path)
    saved(ledger)
    for name, entry in list(ledger.state().packets.items()):
        for consumer in entry["packet"]["consumers"]:
            ledger.append(HUMAN, "packet.acked", name=name, consumer=consumer, major=entry["major"], minor=entry["minor"])
    add_test(ledger, stage="built", confirm=False)
    text = " ".join(gates.missing("G3", ledger.state(), ledger))
    assert "Approve 1 test." in text and "haven't had their automatic check" in text
    assert ("Software", gr.MESSAGE_CHECK_WAITING) in gr.blockers("G3", ledger.state(), ledger)
    stage, key = next(item for item in ci.pending(ledger) if item[0] == "Interface")
    ci.record(ledger, stage, key, True, [])
    assert "automatic check" not in " ".join(gates.missing("G3", ledger.state(), ledger))


def test_the_report_counts_tests_waiting_for_approval(led):
    add_test(led, stage="built", confirm=False)
    assert "Test waiting for approval: 1." in gr.report(led, "G4")


class TestPlanning:
    __test__ = False

    def __init__(self, *plans):
        self.plans = list(plans)
        self.feedback = []

    async def run(self, *, values, **_):
        self.feedback.append(values["feedback"])
        return self.plans.pop(0)


def planned(stage):
    return tr.PlannedTest(covers=["R1"], title="Poses", owner="Mechanical", procedure="p", pass_if="q", metric=None, unit=None, min=None, max=None, uses=[], stage=stage)


@pytest.mark.asyncio
async def test_a_before_purchase_requirement_can_not_wait_for_the_built_arm(led):
    led.append(HUMAN, "record.confirmed", id=led.propose(Actor.agent("controller"), "requirement", key="R1", text="Before servo purchase, define the poses.", verification="v"))
    gateway = TestPlanning(tr.TestPlan(tests=[planned("built")], cannot_test=[]), tr.TestPlan(tests=[planned("before_buying")], cannot_test=[]))
    await tr.plan_tests(led, gateway)
    assert "can't be built" in gateway.feedback[1]
    assert led.state().of_type("test")[0]["fields"]["stage"] == "before_buying"


# ---- The router, clones, and live job states


import asyncio  # noqa: E402

from buildanything import gateway as gw, jobs  # noqa: E402


def software_items():
    return ["laptop has no code yet to send SET.", "No firmware exists yet for ESP32. Write it.", "esp32's code doesn't read SET yet."]


def test_laptop_and_board_work_can_run_side_by_side(led):
    write(led, "laptop", "app.py", "x = 1\n")
    write(led, "esp32", "platformio.ini", "[env:d]\nboard = b\n")
    groups = jobs.split(led, "Software", software_items())
    assert sorted(map(len, groups)) == [1, 2]
    assert any("laptop" in g[0] and len(g) == 1 for g in groups)


def test_work_code_cannot_place_stays_in_one_job(led):
    assert len(jobs.split(led, "Mechanical", ["Do the first thing.", "Do another thing."])) == 1


def test_only_one_job_can_use_fusion_at_a_time(led):
    part(led, "M-01")
    part(led, "M-02")
    assert len(jobs.split(led, "Mechanical", ["M-01 needs a CAD model.", "M-02 needs a CAD model."])) == 1


def test_clones_are_capped(led):
    for name in ("a1", "b2", "c3", "d4", "e5"):
        component(led, name)
    assert len(jobs.split(led, "Procurement", [f"Price {n}." for n in ("a1", "b2", "c3", "d4", "e5")])) == jobs.MAX_CLONES


@pytest.mark.asyncio
async def test_clashing_work_waits_and_independent_work_does_not(led):
    order = []
    release = asyncio.Event()

    async def first():
        async with jobs.slot(led, "Software", "first", {"board"}):
            order.append("first starts")
            await release.wait()
            order.append("first ends")

    async def clashing():
        async with jobs.slot(led, "Software", "second", {"board"}):
            order.append("clash starts")

    async def separate():
        async with jobs.slot(led, "Software", "third", {"computer:laptop"}):
            order.append("separate starts")

    tasks = [asyncio.create_task(first())]
    await asyncio.sleep(0)
    tasks += [asyncio.create_task(clashing()), asyncio.create_task(separate())]
    await asyncio.sleep(0.01)
    assert order == ["first starts", "separate starts"]
    assert [j["status"] for j in jobs.live(led)["jobs"]] == ["running", "queued"]
    release.set()
    await asyncio.gather(*tasks)
    assert order == ["first starts", "separate starts", "first ends", "clash starts"]
    assert jobs.live(led)["jobs"] == []


@pytest.mark.asyncio
async def test_a_question_from_another_department_never_waits(led):
    release = asyncio.Event()

    async def holder():
        async with jobs.slot(led, "Mechanical", "busy", {"*"}):
            await release.wait()

    task = asyncio.create_task(holder())
    await asyncio.sleep(0)
    async with jobs.slot(led, "Mechanical", "question", {"*"}, wait=False):
        pass
    release.set()
    await task


class Parallel(Reporting):
    def __init__(self):
        super().__init__()
        self.running = self.most = 0
        self.values = []

    async def run(self, *, agent, values, tools=(), **kwargs):
        self.running += 1
        self.most = max(self.most, self.running)
        self.values.append((values, [getattr(t, "__name__", type(t).__name__) for t in tools]))
        await asyncio.sleep(0.01)
        self.running -= 1
        return report("DONE", f"Finished {values['command']['items'][0][:6]}.", needs=[])


@pytest.mark.asyncio
async def test_clones_run_together_from_one_copy_of_the_context(led):
    write(led, "laptop", "app.py", "x = 1\n")
    gateway = Parallel()
    await hq.run_clones(led, gateway, "Software", [["laptop work"], ["board work"]], hq.Services(None), "TASK", "d", "h")
    assert gateway.most == 2
    first, second = (v for v, _ in gateway.values)
    assert all(first[k] == second[k] for k in ("briefing", "department_state", "requests", "earlier_discussion"))
    assert all("look_up_project" in names and "ask_department" not in names for _, names in gateway.values)


@pytest.mark.asyncio
async def test_clones_give_the_person_one_message(led):
    await hq.run_clones(led, Parallel(), "Software", [["laptop work"], ["board work"]], hq.Services(None), "TASK", "d", "h")
    messages = [m for m in hq.thread(led.events(), "Software") if m["role"] == "agent"]
    assert len(messages) == 1 and "Finished laptop" in messages[0]["text"] and "Finished board" in messages[0]["text"]
    assert len([e for e in led.events() if e["kind"] == "report.filed"]) == 2


@pytest.mark.asyncio
async def test_a_bug_from_one_clone_is_not_hidden_by_another(led):
    class Mixed(Parallel):
        async def run(self, *, agent, values, tools=(), **kwargs):
            await super().run(agent=agent, values=values, tools=tools, **kwargs)
            return report("BUG", "x", risk="Broken header.") if "board" in values["command"]["items"][0] else report("DONE", "y")

    await hq.run_clones(led, Mixed(), "Software", [["board work"], ["laptop work"]], hq.Services(None), "TASK", "d", "h")
    assert protocol.latest(led.events(), "Software")["type"] == "BUG"


@pytest.mark.asyncio
async def test_one_clone_failing_keeps_the_others_work_and_reports_the_failure(led):
    class Failing(Parallel):
        async def run(self, *, agent, values, tools=(), **kwargs):
            if "board" in values["command"]["items"][0]:
                raise RuntimeError("no")
            return await super().run(agent=agent, values=values, tools=tools, **kwargs)

    with pytest.raises(RuntimeError):
        await hq.run_clones(led, Failing(), "Software", [["board work"], ["laptop work"]], hq.Services(None), "TASK", "d", "h")
    assert protocol.latest(led.events(), "Software")["type"] == "DONE"
    assert [e["data"]["outcome"] for e in led.events() if e["kind"] == "job.finished"].count("FAILED") == 1


@pytest.mark.asyncio
async def test_a_gate_list_is_split_between_clones(led, driving):
    opened(led)
    write(led, "laptop", "app.py", "x = 1\n")
    write(led, "esp32", "platformio.ini", "[env:d]\nboard = b\n")
    gateway = Parallel()

    from unittest.mock import patch
    with patch.object(hq, "_owned_work", return_value={"Software": software_items()}):
        await hq.run_step(led, gateway, "drive:G4:Software:x", hq.Services(None))
    assert gateway.most == 2 and len(gateway.values) == 2


def test_look_up_project_shows_what_the_person_answered(led):
    waiting_question(led, "r1", "Electrical", "Which board?")
    hq.answer_request(led, "r1", "ESP32-C6", HUMAN)
    result = tool_named(led, "Software", "look_up_project")()
    assert result["answers_from_person"] == [{"question": "Which board?", "answer": "ESP32-C6"}]


def test_the_network_says_what_each_department_is_doing(led):
    led.append(HUMAN, "command.person", department="Electrical", type="HOLD", reason="")
    waiting_question(led, "r1", "Software", "Which app?")
    states = {d["name"]: d for d in hq.work_view(led)["departments"]}
    assert states["Electrical"]["state"] == "held" and states["Mechanical"]["state"] == "idle"
    assert states["Software"]["state"] == "waiting_for_you" and states["Software"]["questions"] == ["Which app?"]


@pytest.mark.asyncio
async def test_a_running_job_shows_its_current_activity(led):
    async with jobs.slot(led, "Software", "Write the app", {"computer:laptop"}):
        live = jobs.agent_began(led, "lead_software", "Software", "Software Lead")
        jobs.note(live, "Write code for one device or computer.")
        view = hq.work_view(led)
        software = next(d for d in view["departments"] if d["name"] == "Software")
        assert software["state"] == "working" and software["jobs"][0]["activity"].startswith("Write code")
        assert view["agents"] == []  # It's shown on its job, not twice.
        jobs.agent_ended(led, live)
    assert hq.work_view(led)["recent"][0]["title"] == "Write the app"


def test_runs_side_by_side_count_each_others_unrecorded_spending(led):
    class Used:
        input_tokens = 1_000_000
        output_tokens = 0
        requests = 1
        input_tokens_details = None

    price = gw.Price(1.0, 0.1, 1.25, 5.0)
    a, b = gw._Budget(price, led), gw._Budget(price, led)
    a.usage = Used()
    gw._IN_FLIGHT[str(led.root)] = [a, b]
    try:
        assert gw._unrecorded(led) == 1.0 and gw._unrecorded(led, a) == 0
    finally:
        gw._IN_FLIGHT.pop(str(led.root))


# ---- A paper search that fails doesn't cost the whole research step


import io  # noqa: E402
import urllib.error  # noqa: E402


ARXIV_REPLY = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/1</id><title>T</title><summary>S</summary></entry></feed>"""


class ArxivReply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def busy_then_fine(monkeypatch, failures):
    attempts = []

    def urlopen(_request, timeout=0):
        attempts.append(1)
        if len(attempts) <= failures:
            raise urllib.error.HTTPError("u", 429, "busy", {"Retry-After": "1"}, None)
        return ArxivReply(ARXIV_REPLY)

    monkeypatch.setattr(citations.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(citations.time, "sleep", lambda _s: None)
    return attempts


def test_a_busy_paper_search_waits_and_tries_again(monkeypatch):
    attempts = busy_then_fine(monkeypatch, 2)
    assert citations.ArxivIndex(min_interval=0).search("tft")[0].title == "T" and len(attempts) == 3


def test_a_paper_search_gives_up_after_three_tries(monkeypatch):
    attempts = busy_then_fine(monkeypatch, 9)
    with pytest.raises(urllib.error.HTTPError):
        citations.ArxivIndex(min_interval=0).search("tft")
    assert len(attempts) == 3


def test_a_request_arxiv_rejects_is_not_retried(monkeypatch):
    attempts = []

    def urlopen(_request, timeout=0):
        attempts.append(1)
        raise urllib.error.HTTPError("u", 400, "bad", {}, None)

    monkeypatch.setattr(citations.urllib.request, "urlopen", urlopen)
    with pytest.raises(urllib.error.HTTPError):
        citations.ArxivIndex(min_interval=0).search("tft")
    assert len(attempts) == 1


class DownPapers:
    def search(self, query):
        raise TimeoutError("down")

    def get(self, paper_id):
        return None


class ResearchUsingTheWeb:
    async def run(self, *, tools, **_):
        tools[0]("tft screens")
        tools[2]("https://example.com/a")
        return ag.ResearchOut(claims=[ag.ClaimOut(source_url="https://example.com/a", statement="s", quote="a real quote from the page", applicability="here")])


@pytest.mark.asyncio
async def test_web_sources_survive_a_failed_paper_search(led, monkeypatch):
    monkeypatch.setattr(ag, "fetch_page", lambda url: ("Title", "This has a real quote from the page in it."))
    result = await ag.run_research(led, ResearchUsingTheWeb(), DownPapers(), "screens")
    assert result["accepted"] == 1 and len(led.state().of_type("claim", "proposed")) == 1


# ---- A project with no mechanical parts, and getting past a reported fault


def test_an_empty_assembly_list_is_fine(led):
    result = tool_named(led, "Mechanical", "set_assemblies")("{}", "w")
    assert "error" not in result and not dn.assembly_plan(led.state())


def test_a_department_with_nothing_to_do_is_not_paid_to_propose(led):
    dn.mark_none(led, "Mechanical", "It's only a board and a screen.", HUMAN)
    for department in hq.DEPARTMENTS:
        if department != "Mechanical":
            led.append(SYSTEM, "hq.finished", step=f"open:{department}", label="x")
    led.append(SYSTEM, "hq.finished", step="plan_tests", label="x")
    assert not (hq.next_action(led) or "").startswith("open:Mechanical")


@pytest.mark.asyncio
async def test_a_person_can_clear_a_reported_fault(led, driving):
    opened(led)
    await say(led, Reporting(report("BUG", risk="The header doesn't compile.")))
    assert [n for n in hq.needs(led) if n["kind"] == "bug"]
    led.append(HUMAN, "command.person", department="Electrical", type="RESUME", reason="")
    assert not [n for n in hq.needs(led) if n["kind"] == "bug"] and hq.next_action(led).startswith("drive:G4:Electrical")


@pytest.mark.asyncio
async def test_a_new_fault_after_carrying_on_stops_it_again(led):
    led.append(HUMAN, "command.person", department="Electrical", type="RESUME", reason="")
    await say(led, Reporting(report("BUG", risk="Still broken.")))
    assert protocol.bug_open(led.events(), "Electrical")


@pytest.mark.asyncio
async def test_a_fault_from_a_department_with_no_work_does_not_ask_the_person(led):
    await say(led, Reporting(report("BUG", risk="x")), "Mechanical")
    dn.mark_none(led, "Mechanical", "It's only a board and a screen.", HUMAN)
    assert not [n for n in hq.needs(led) if n["kind"] == "bug"]


def test_the_gate_list_never_answers_from_an_older_copy_of_the_project(led):
    record = component(led, "MG996R", confirm=False)
    older = led.state()
    led.append(HUMAN, "record.confirmed", id=record)
    assert "Review 1 components" not in " ".join(text for _owner, text in gr.blockers("G4", older, led))


@pytest.mark.asyncio
async def test_the_connection_drafter_is_told_the_wiring_the_person_stated(led):
    from buildanything import electrical_api as ea

    led.append(HUMAN, "project.updated", scope="PA5 to SCK, already wired.")
    component(led, "NUCLEO-F401RE")
    seen = {}

    class Drafting:
        async def run(self, *, agent, values, **_):
            seen.update(values)
            return ea.DesignOut(nets=[], notes=[])

    await ea.design_connections(led, Drafting())
    assert seen["scope"] == "PA5 to SCK, already wired."
    assert set(seen) == set(agent_specs.SPECS["electrical_design"].inputs)


# ---- Fix it means someone does the work


def high_finding(led, text):
    record = led.propose(Actor.agent("verifier"), "finding", run=1, severity="high", safety=False, text=text, related=[])
    return record


def test_fix_it_sends_the_finding_to_the_right_department(led):
    record = high_finding(led, "There is no firmware for the display.")
    assert "Decide 1 serious Verifier findings" in blockers(led)
    gates.decide_finding(led, record, "fix", "", HUMAN)
    assert ("Software", "Fix this Verifier finding: There is no firmware for the display.") in gr.blockers("G4", led.state(), led)


@pytest.mark.asyncio
async def test_a_fixed_finding_comes_back_for_a_person_to_check(led):
    record = high_finding(led, "The firmware is empty.")
    gates.decide_finding(led, record, "fix", "", HUMAN)
    await say(led, Reporting(report("DONE")), "Software")
    text = blockers(led)
    assert "Check that 1 fix worked" in text and "Fix this Verifier finding" not in text
    gates.decide_finding(led, record, "fixed", "", HUMAN)
    assert "Check that" not in blockers(led) and "Verifier findings" not in blockers(led)


def test_findings_point_to_headquarters_where_the_buttons_are():
    assert hq._place("Decide 2 serious Verifier findings.") == "Headquarters"
    assert hq._place("Check that 1 fix worked, then mark them fixed on Headquarters.") == "Headquarters"


# ---- Code that compiles and does nothing, and libraries


HIDDEN = """#ifndef APPROVED
#define APPROVED 0
#endif
#if APPROVED
pinMode(BA_PIN_TFT_CS, OUTPUT);
#endif
"""


def test_a_feature_switched_off_by_default_is_caught():
    assert fw.switched_off(HIDDEN) == ["APPROVED"]


def test_a_switch_that_is_turned_on_is_fine():
    assert fw.switched_off(HIDDEN, "build_flags = -DAPPROVED=1") == []


def test_a_debug_switch_that_hides_no_pins_is_fine():
    assert fw.switched_off("#define DEBUG 0\n#if DEBUG\nprint();\n#endif\n") == []


def test_libraries_are_added_and_survive_a_board_settings_rewrite(led, monkeypatch):
    monkeypatch.setattr(fw, "board_catalog", lambda: {})
    write(led, "nucleo", "platformio.ini", "[env:device]\nplatform = ststm32\nboard = nucleo_f401re\nframework = arduino\n")
    fw.set_libraries(led.root, "nucleo", ["adafruit/Adafruit GFX Library", "bad\nname = x"])
    fw.write_platformio(led.root, "nucleo", "nucleo_f401re", "-DA=1")
    text = (led.root / "firmware" / "nucleo" / "platformio.ini").read_text(encoding="utf-8")
    assert "lib_deps =\n    adafruit/Adafruit GFX Library\n" in text and "bad" not in text and "-DA=1" in text


def test_the_display_library_gets_its_missing_header_on_stm32(led):
    write(led, "nucleo", "platformio.ini", "[env:device]\nplatform = ststm32\nboard = nucleo_f401re\nframework = arduino\n")
    fw.set_libraries(led.root, "nucleo", ["adafruit/Adafruit ST7735 and ST7789 Library"])
    assert (led.root / "firmware" / "nucleo" / "lib" / "wiring_private" / "wiring_private.h").exists()


def test_other_boards_do_not_get_that_header(led):
    write(led, "esp", "platformio.ini", "[env:device]\nplatform = espressif32\nboard = b\nframework = arduino\n")
    fw.set_libraries(led.root, "esp", ["adafruit/Adafruit ST7735 and ST7789 Library"])
    assert not (led.root / "firmware" / "esp" / "lib").exists()


def test_the_stand_in_header_needs_no_description(led):
    write(led, "nucleo", "platformio.ini", "[env:device]\nplatform = ststm32\nboard = nucleo_f401re\nframework = arduino\n")
    fw.set_libraries(led.root, "nucleo", ["adafruit/Adafruit ST7735 and ST7789 Library"])
    assert all(f["generated"] for f in fw.scan_workspace(led.root, "nucleo") if f["path"].startswith("lib/"))


def agreed(led, *names):
    for name in names:
        led.append(HUMAN, "record.confirmed", id=led.propose(ELECTRICAL, "net", name=name, kind="signal", nominal_v=3.3, members=[{"component": "c", "pin": "PA1", "role": "io"}]))


def board_code(led, text):
    write(led, "board", "platformio.ini", "[env:d]\nboard = b\n")
    write(led, "board", "src/main.cpp", '#include "ba_pins.h"\n' + text)
    fw.write_pins(led.root, led.state(), "board")


def test_an_agreed_pin_the_code_never_uses_is_caught(led):
    agreed(led, "TFT_BACKLIGHT")
    board_code(led, "void setup() {}\n")
    assert any("never uses these agreed pins: TFT_BACKLIGHT" in p for p in fw.pin_problems(led.root, led.state()))


def test_a_used_pin_passes(led):
    agreed(led, "TFT_BACKLIGHT")
    board_code(led, "void setup() { pinMode(BA_PIN_TFT_BACKLIGHT, 1); }\n")
    assert fw.pin_problems(led.root, led.state()) == []


def test_usb_serial_pins_do_not_need_naming_in_the_code(led):
    agreed(led, "VCP_MCU_TX")
    board_code(led, "void setup() {}\n")
    assert fw.pin_problems(led.root, led.state()) == []


def test_writing_a_pin_before_pinmode_is_caught(led):
    agreed(led, "TFT_BACKLIGHT")
    board_code(led, "void setup() { digitalWrite(BA_PIN_TFT_BACKLIGHT, 1); pinMode(BA_PIN_TFT_BACKLIGHT, 1); }\n")
    assert any("before pinMode" in p for p in fw.pin_problems(led.root, led.state()))


def _file(path, code, whole=False):
    return fw.FileOut(path=path, owner="Software", blocks=[fw.BlockOut(title="Everything", description="All of the program in one place, start to end.", whole_file=whole, code=code)])


def test_a_long_block_with_many_functions_must_be_split():
    body = "\n".join(f"  x += {i};" for i in range(25))
    code = f"#include <a.h>\nint x = 0;\nvoid setup() {{\n{body}\n}}\n\nvoid loop() {{\n{body}\n}}\n"
    assert any("holds 2 functions" in p for p in fw.documentation_problems([_file("src/main.cpp", code, True)]))


def test_one_function_per_block_passes():
    body = "\n".join(f"  x += {i};" for i in range(50))
    assert fw.documentation_problems([_file("src/main.cpp", f"static void draw(int y) {{\n{body}\n}}\n")]) == []


def test_python_files_are_split_by_def_and_class():
    body = "\n".join(f"    x = {i}" for i in range(25))
    code = f"class Link:\n{body}\n\ndef main():\n{body}\n"
    assert any("holds 2 functions" in p for p in fw.documentation_problems_host([_file("laptop.py", code)]))


def test_calls_and_control_lines_are_not_counted_as_definitions():
    code = "void setup() {\n  if (a) {\n  }\n  for (int i = 0; i < 3; i++) {\n  }\n  begin(1);\n}\n"
    assert fw.definitions("src/main.cpp", code) == 1


def test_tips_follow_the_parts():
    assert fw.part_skill_names("Jump when B1 is pressed", [], ["1.8 inch TFT SPI 128x160"], "nucleo_f401re", {"platform": "ststm32"}) == ["buttons", "displays", "nucleo-64", "st7735-tft", "stm32"]
    assert fw.part_skill_names("Log the temperature", [], ["BME280"], "esp32dev", {"platform": "espressif32"}) == ["esp32"]
    assert fw.part_skills("Log the temperature", [], ["BME280"]) == ""


def test_code_can_be_written_against_drafted_connections(led):
    led.propose(ELECTRICAL, "net", name="TFT_CS", kind="signal", nominal_v=3.3, members=[{"component": "c", "pin": "PB6", "role": "io"}])
    assert fw.pin_defines(led.state()) == {"TFT_CS": "PB6"}


def _mod(layer, name, uses=(), pins=()):
    return fw.ModulePlan(layer=layer, name=name, purpose="p", functions=[f"void {name}_Init(void)"], uses=list(uses), pins=list(pins))


def _src(path, code, owner):
    return fw.FileOut(path=path, owner=owner, blocks=[fw.BlockOut(title="t", description="A description long enough.", code=code)])


LAYERED_PLAN = [_mod("driver", "gpio", pins=["BA_PIN_USER_BUTTON_B1"]), _mod("mid", "button", uses=["gpio"]), _mod("app", "game", uses=["button"])]
LAYERED_FILES = [
    _src("src/driver/gpio.h", "void gpio_Init(void);", "Electrical"),
    _src("src/driver/gpio.cpp", '#include "gpio.h"\nvoid gpio_Init(void) { pinMode(BA_PIN_USER_BUTTON_B1, INPUT); }', "Electrical"),
    _src("src/mid/button.h", '#include "../driver/gpio.h"\nvoid button_Init(void);', "Electrical"),
    _src("src/app/game.cpp", '#include "../mid/button.h"\nvoid game_Init(void) { button_Init(); }', "Software"),
    _src("src/main.cpp", '#include "app/game.h"\nvoid setup() { game_Init(); }\nvoid loop() {}', "Software"),
]


def test_a_layered_program_passes():
    assert fw.layer_problems(LAYERED_PLAN, LAYERED_FILES, [], {"USER_BUTTON_B1": "PC13", "VCP_TX": "PA2"}) == []


def test_the_app_may_not_touch_pins_or_drivers():
    bad = LAYERED_FILES + [_src("src/app/cheat.cpp", '#include "../driver/gpio.h"\nint x() { return digitalRead(BA_PIN_USER_BUTTON_B1); }', "Software")]
    text = " ".join(fw.layer_problems(LAYERED_PLAN, bad, [], {}))
    assert "touches pins directly" in text and "only uses mid modules" in text


def test_every_planned_module_needs_a_file_and_every_pin_a_driver():
    text = " ".join(fw.layer_problems(LAYERED_PLAN + [_mod("mid", "display", uses=["tft"])], LAYERED_FILES, [], {"USER_BUTTON_B1": "PC13", "TFT_CS": "PB6"}))
    assert "no src/mid/display.h" in text and "uses tft" in text and "TFT_CS" in text


def test_files_outside_the_layers_and_wrong_owners_are_caught():
    text = " ".join(fw.layer_problems(LAYERED_PLAN, LAYERED_FILES + [_src("src/hardware.cpp", "int a;", "Electrical"), _src("src/app/x.cpp", "int b;", "Electrical")], [], {}))
    assert "src/hardware.cpp isn't in a layer" in text and "belongs to Software" in text


def test_the_firmware_agent_gets_the_skill():
    from buildanything import agent_specs
    prompt = agent_specs.system_prompt(agent_specs.SPECS["firmware"])
    assert "# Skill: FIRMWARE" in prompt
    assert not any(word in prompt for word in ("Nucleo", "ST7735", "INITR_BLACKTAB", "PC13", "ESP32", "Uno"))  # Board and part facts load only for projects that use them.


def test_electrical_may_not_write_the_app_and_software_may_not_write_the_middleware():
    lower = " ".join(fw.layer_problems(LAYERED_PLAN[:2], LAYERED_FILES, [], {}, ("driver", "mid")))
    assert "src/app/game.cpp is in the app layer, which this step doesn't write" in lower and "src/main.cpp is in the app layer" in lower
    app = " ".join(fw.layer_problems(LAYERED_PLAN[2:], LAYERED_FILES, [], {}, ("app",), LAYERED_PLAN[:2]))
    assert "src/driver/gpio.h is in the driver layer" in app and "uses button" not in app  # The app may use Electrical's planned modules.


def _board(led, path, text):
    p = led.root / "firmware" / "board" / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_who_writes_what_follows_the_layers(led):
    assert not fw.has_lower(led.root, "board")
    _board(led, "src/driver/gpio.cpp", "int a;")
    _board(led, "src/mid/button.h", "void Button_Init(void);")
    _board(led, "src/main.cpp", fw.APP_STUB)
    assert fw.has_lower(led.root, "board") and not fw.has_app(led.root, "board")
    _board(led, "src/app/game.cpp", "int g;")
    assert fw.has_app(led.root, "board")


def test_software_cannot_write_an_app_before_the_middleware_exists(led):
    import asyncio
    result = asyncio.run(fw.generate(led, None, led.root, "board", "nucleo_f401re", "game", scope="app"))
    assert "Electrical writes those first" in result["build"]["detail"]


def test_a_header_the_app_is_built_on_is_frozen_until_a_change_is_approved(led):
    from buildanything import changes
    header = fw.FileOut(path="src/mid/button.h", owner="Electrical", blocks=[fw.BlockOut(title="Button", description="Button events for the app.", code="void Button_Init(void);")])
    _board(led, "src/mid/button.h", fw.render(header))
    led.append(HUMAN, "firmware.api_used", device="board", headers={"src/mid/button.h": fw.code_hash(fw.render(header))})
    assert fw.frozen_problems(led, led.root, "board", [header]) == []  # Unchanged is fine.
    changed = fw.FileOut(path="src/mid/button.h", owner="Electrical", blocks=[fw.BlockOut(title="Button", description="Button events for the app.", code="void Button_Init(int pin);")])
    assert "frozen" in fw.frozen_problems(led, led.root, "board", [changed])[0]
    cid = changes.propose(led, "Add the pin", "The button needs its pin passed in.", "middleware", "board:src/mid/button.h", {"change": "Init takes a pin"}, HUMAN)
    assert changes.compute_impact(led, {"kind": "middleware", "target": "board:src/mid/button.h"})[0]["department"] == "Software"
    led.append(HUMAN, "change.approved", id=cid, reason="Fine")
    assert fw.frozen_problems(led, led.root, "board", [changed]) == []
    _board(led, "src/mid/button.h", fw.render(changed))
    assert fw.api_changed(led, led.root, "board") == ["src/mid/button.h"]  # So Software is told to update the app.


def test_checking_messages_changes_nothing_and_reruns_after_a_generator_fix(led, monkeypatch):
    import asyncio
    from buildanything import ci, interfaces as itf
    itf.save_packet(led, {"name": "READY", "id": 1, "transport": {"type": "serial"}, "producer": "board", "consumers": ["laptop"], "fields": []}, HUMAN)
    before = dict(led.state().packets)
    assert asyncio.run(ci.check_messages(led))["verdict"] == "passed"
    assert led.state().packets == before and not ci.pending(led)
    monkeypatch.setattr(ci, "_generator_hash", lambda: "fixed")
    assert ci.pending(led)  # A fixed generator means the old verdict no longer counts.


def test_stub_functions_are_refused():
    code = "bool Display_BeginRegion(uint16_t x, uint16_t y) {\n    (void)x; (void)y;\n    return false;\n}\nvoid Display_Process(uint32_t now) { (void)now; Tft_Process(); }\n"
    assert fw.stubs(code) == ["Display_BeginRegion"]
    file = _src("src/mid/display.cpp", code, "Electrical")
    assert any("do nothing or always fail: Display_BeginRegion" in p for p in fw.layer_problems([], [file], [], {}, ("driver", "mid")))


def test_what_software_says_is_missing_goes_to_electrical_and_unfreezes_the_headers(led):
    header = fw.FileOut(path="src/mid/display.h", owner="Electrical", blocks=[fw.BlockOut(title="Display", description="Drawing for the app.", code="void Display_Init(void);")])
    _board(led, "src/mid/display.h", fw.render(header))
    led.append(HUMAN, "firmware.api_used", device="board", headers={"src/mid/display.h": fw.code_hash(fw.render(header))})
    led.append(HUMAN, "firmware.generated", device="board", files=[], ok=True, ran=True, scope="app", missing=["display: draw text"])
    assert fw.middleware_wanted(led, "board") == ["display: draw text"]
    grown = fw.FileOut(path="src/mid/display.h", owner="Electrical", blocks=[fw.BlockOut(title="Display", description="Drawing for the app.", code="void Display_Init(void);\nvoid Display_Text(const char *t);")])
    assert fw.frozen_problems(led, led.root, "board", [grown]) == []  # Software asked for it, so it may change.
    led.append(HUMAN, "firmware.generated", device="board", files=[], ok=True, ran=True, scope="lower")
    assert fw.middleware_wanted(led, "board") == []


def test_board_code_waits_for_the_connection_list(led):
    assert fw.wiring_ready(led.state())  # A project with no hardware never waits.
    led.propose(ELECTRICAL, "component", part_number="NUCLEO-F401RE", role="board")
    assert fw.wiring_ready(led.state())  # A board on its own has nothing to wire.
    led.propose(ELECTRICAL, "component", part_number="1.8 inch TFT", role="display")
    assert not fw.wiring_ready(led.state())
    led.propose(ELECTRICAL, "net", name="TFT_CS", kind="signal", nominal_v=3.3, members=[{"component": "c", "pin": "PB6", "role": "io"}])
    assert fw.wiring_ready(led.state())


def test_error_placeholders_are_refused_and_each_step_plans_only_its_layers():
    file = _src("src/app/app.cpp", '#ifndef BAUD\n#error "Provide the baud rate"\n#endif\n', "Software")
    text = " ".join(fw.layer_problems(LAYERED_PLAN, [file], [], {}, ("app",), LAYERED_PLAN[:2]))
    assert "stops the build with #error" in text and "src/driver" not in text
    main_plan = [_mod("app", "main", uses=["button"])]
    assert fw.layer_problems(main_plan, [_src("src/main.cpp", '#include "mid/button.h"\nvoid setup() {}\nvoid loop() {}', "Software")], [], {}, ("app",), LAYERED_PLAN[:2]) == []


def test_problems_in_the_lower_layers_go_to_electrical():
    from buildanything import ci, gatereport
    assert ci._owner("Firmware:board", [r"src\driver\tft.cpp:12: error: x"]) == "Electrical"
    assert ci._owner("Firmware:board", ["src/mid/display.cpp:4: error: x"]) == "Electrical"
    assert ci._owner("Firmware:board", ["src/app/game.cpp:3: error: y"]) == "Software"
    assert gatereport._finding_owner("The display driver never initialises the controller") == "Electrical"
    assert gatereport._finding_owner("The game never shows the score on screen") == "Software"


def test_each_category_needs_its_documents_and_a_missing_one_becomes_an_assumption(led):
    from buildanything import documents as docs
    board = component(led, "NUCLEO-F401RE", "board")
    record = led.state().records[board]
    assert docs.category(record) == "dev_board"
    assert docs.missing(led, led.state()) == [("NUCLEO-F401RE", "datasheet"), ("NUCLEO-F401RE", "board user manual or board schematic")]
    led.propose(HUMAN, "datasheet", component=board, file="a.pdf", filename="a.pdf", pages=1, part_match=True, doc="datasheet")
    led.append(HUMAN, "component.doc_missing", id=board, doc="user_manual", reason="I can't find the manual.")
    assert docs.missing(led, led.state()) == []
    assumed = docs.assumptions(led, led.state())
    assert len(assumed) == 1 and "no board user manual" in assumed[0]["text"]
    assert docs.unchecked(led, led.state())
    led.append(HUMAN, "assumption.checked", id=assumed[0]["id"], note="Board works as expected.")
    assert docs.unchecked(led, led.state()) == []


def test_an_on_board_button_needs_the_board_manual_behind_it(led):
    from buildanything import documents as docs, page_memory as pm
    board = component(led, "NUCLEO-F401RE", "board")
    net = led.propose(ELECTRICAL, "net", name="USER_BUTTON_B1", kind="signal", nominal_v=3.3, members=[{"component": board, "pin": "PC13", "role": "io"}, {"component": board, "pin": "B1", "role": "io"}])
    assert any("USER_BUTTON_B1" in a["text"] for a in docs.assumptions(led, led.state()))
    manual = led.propose(HUMAN, "datasheet", component=board, file="um.pdf", filename="um.pdf", pages=1, part_match=True, doc="user_manual")
    led.propose(ELECTRICAL, "page_note", component=board, part="NUCLEO-F401RE board user manual", datasheet=manual, file="um.pdf", page=1, finding="B1 USER: the user button is connected to the I/O PC13", why="button pin")
    assert not any("USER_BUTTON_B1" in a["text"] for a in docs.assumptions(led, led.state()))
    note = pm.current_notes(led.state())[0]
    assert pm.source_of(led.state(), note) == ("board user manual", "high")


def test_unchecked_assumptions_block_gate_six(led):
    board = component(led, "NUCLEO-F401RE", "board")
    led.append(HUMAN, "component.doc_missing", id=board, doc="user_manual", reason="I can't find the manual.")
    from buildanything import gatereport
    assert any("assumption" in text for who, text in gatereport._integration(led, led.state()))


def _fact(led, component_id, key, value, signal="", doc="datasheet", quote="stated on the page"):
    return led.propose(Actor.agent("device_facts"), "device_fact", component=component_id, datasheet="d", doc=doc, page=3, key=key, signal=signal, value=value, quote=quote, on_page=True, confidence="high")


def test_device_facts_become_constants_and_typed_addresses_are_refused(led):
    from buildanything import devicefacts as dfx
    sensor = component(led, "BME280", "temperature sensor")
    _fact(led, sensor, "i2c_address", "0x76")
    _fact(led, sensor, "spi_mode", "0")
    text = dfx.header_text(led.state())
    assert "#define BA_BME280_I2C_ADDRESS 0x76" in text and "#define BA_BME280_SPI_MODE 0" in text
    assert dfx.typed_values(led.state(), "src/driver/bme.cpp", "Wire.beginTransmission(0x76);")
    assert dfx.typed_values(led.state(), "src/driver/bme.cpp", "Wire.beginTransmission(BA_BME280_I2C_ADDRESS);") == []
    assert dfx.for_agents(led.state())[0]["constant"] in ("BA_BME280_I2C_ADDRESS", "BA_BME280_SPI_MODE")


def test_a_confirmed_fact_wins_over_a_newer_guess(led):
    from buildanything import devicefacts as dfx
    sensor = component(led, "BME280", "temperature sensor")
    first = _fact(led, sensor, "i2c_address", "0x77")
    led.append(HUMAN, "record.confirmed", id=first)
    _fact(led, sensor, "i2c_address", "0x76")
    assert [f["fields"]["value"] for f in dfx.current(led.state())] == ["0x77"]


def test_fact_values_must_have_the_right_shape():
    from buildanything import devicefacts as dfx
    assert dfx.valid(dfx.FactOut(key="i2c_address", value="0x76", quote="q")) is None
    assert dfx.valid(dfx.FactOut(key="i2c_address", value="0x76 or 0x77", quote="q"))
    assert dfx.valid(dfx.FactOut(key="active_level", value="LOW", quote="q"))  # Needs the signal.
    assert dfx.valid(dfx.FactOut(key="active_level", signal="B1", value="LOW", quote="q")) is None


def test_a_fact_from_the_board_manual_backs_the_on_board_button(led):
    from buildanything import documents as docs
    board = component(led, "NUCLEO-F401RE", "board")
    led.propose(ELECTRICAL, "net", name="USER_BUTTON_B1", kind="signal", nominal_v=3.3, members=[{"component": board, "pin": "PC13", "role": "io"}, {"component": board, "pin": "B1", "role": "io"}])
    assert any("USER_BUTTON_B1" in a["text"] for a in docs.assumptions(led, led.state()))
    _fact(led, board, "active_level", "LOW", signal="B1", doc="user_manual", quote="B1 USER is connected to PC13 and reads low when pressed")
    assert not any("USER_BUTTON_B1" in a["text"] for a in docs.assumptions(led, led.state()))


def test_parts_with_documents_need_their_facts_read(led):
    from buildanything import devicefacts as dfx
    sensor = component(led, "BME280", "temperature sensor")
    assert dfx.unread(led, led.state()) == []  # No documents yet, so nothing to read.
    led.propose(HUMAN, "datasheet", component=sensor, file="b.pdf", filename="b.pdf", pages=1, part_match=True, doc="datasheet")
    assert dfx.unread(led, led.state()) == ["BME280"]
    led.append(Actor.system("device_facts"), "facts.read", component=sensor, created=2)
    assert dfx.unread(led, led.state()) == []


def test_a_document_says_what_it_is():
    from buildanything import documents as docs
    assert docs.detect_kind(["UM1724 User manual STM32 Nucleo-64 boards", ""]) == "user_manual"
    assert docs.detect_kind(["STM32F401xD/xE Errata sheet", ""]) == "errata"
    assert docs.detect_kind(["RM0368 Reference manual", ""]) == "reference_manual"
    assert docs.detect_kind(["ST7735 262K Color Single-Chip TFT Controller/Driver Datasheet", ""]) is None


def test_a_derived_fact_needs_its_working_and_says_so_in_the_header(led):
    from buildanything import devicefacts as dfx
    assert dfx.valid(dfx.FactOut(key="spi_max_clock_hz", value="15000000", quote="TSCYCW 66 ns", derived=True))
    assert dfx.valid(dfx.FactOut(key="spi_max_clock_hz", value="15000000", quote="TSCYCW 66 ns", derived=True, working="1 / 66 ns = 15.2 MHz")) is None
    tft = component(led, "ST7735 TFT", "display")
    led.propose(Actor.agent("device_facts"), "device_fact", component=tft, datasheet="d", doc="datasheet", page=24, key="spi_max_clock_hz", signal="", value="15000000", quote="TSCYCW 66 ns", on_page=True, confidence="medium", derived=True, working="1 / 66 ns = 15.2 MHz")
    assert "derived: 1 / 66 ns" in dfx.header_text(led.state())


def test_a_maximum_stays_a_maximum():
    from buildanything import devicefacts as dfx
    bad = dfx.FactOut(key="pwm_frequency_hz", value="1600", quote="This is the maximum PWM frequency")
    assert "max" in dfx.valid(bad)
    assert dfx.valid(dfx.FactOut(key="pwm_frequency_hz", value="1600", qualifier="max", quote="This is the maximum PWM frequency")) is None


def test_only_facts_that_matter_and_could_not_be_verified_need_a_person(led):
    from buildanything import devicefacts as dfx
    part = component(led, "BME280", "sensor")
    good = led.propose(Actor.agent("device_facts"), "device_fact", component=part, datasheet="d", doc="datasheet", page=3, key="i2c_address", signal="", value="0x76", quote="the 7-bit device address is 0x76", on_page=True, confidence="high", derived=False, working="", qualifier="as stated")
    shaky = led.propose(Actor.agent("device_facts"), "device_fact", component=part, datasheet="d", doc="datasheet", page=4, key="spi_mode", signal="", value="0", quote="data latched on rising edge", on_page=True, confidence="medium", derived=True, working="idle low, rising edge: mode 0", qualifier="as stated")
    idle = led.propose(Actor.agent("device_facts"), "device_fact", component=part, datasheet="d", doc="datasheet", page=5, key="startup_delay_ms", signal="", value="2", quote="start-up takes about 2 ms", on_page=False, confidence="high", derived=False, working="", qualifier="as stated")
    verdicts = dfx.review(led, led.state())
    assert verdicts[good]["verdict"] == "verified"
    assert verdicts[shaky]["verdict"] == "needs_you"  # Decides how the bus works, and was worked out.
    assert verdicts[idle]["verdict"] == "unused"  # Unverified, but no code relies on it.
    assert [f["id"] for f in dfx.needing_you(led, led.state())] == [shaky]
    assert dfx.constant(led.state(), led.state().records[good]) == "BA_BME280_I2C_ADDRESS"


def test_the_story_comes_from_real_events(led):
    from buildanything import story
    start = len(led.events()) - 1
    led.append(Actor.system("headquarters"), "command.sent", department="Electrical", type="BRIEF", items=[], done_when="x", reason="")
    led.propose(Actor.agent("electrical"), "page_note", component="c", part="NUCLEO", datasheet="d", file="f", page=24, finding="B1 USER", why="button")
    led.append(Actor.system("headquarters"), "report.filed", department="Electrical", type="DONE", done=["Read the manual."])
    s = story.steps(led, after=start)
    assert [(x["kind"], x["from"], x["to"]) for x in s] == [("say", "Headquarters", "Electrical"), ("hand", "Ledger", "Electrical"), ("hand", "Electrical", "Ledger"), ("say", "Electrical", "Headquarters")]
    assert s[2]["label"] == "finding p.24"
    assert story.steps(led, after=s[0]["i"])[0]["kind"] == "hand"  # Only what's new since a given point.


def test_a_board_with_32_bit_double_builds_unless_a_message_needs_float64():
    # The Uno's double is 32-bit. Only a message with a 64-bit float may demand a 64-bit double.
    assert "sizeof(double)" not in codegen.C_PRELUDE.replace("%F64%", "")
    assert "sizeof(double) == 8" in codegen.C_F64 and "%F64%" in codegen.C_PRELUDE


def test_every_change_kind_has_a_reason_a_person_must_decide(tmp_path) -> None:
    """A middleware change crashed the reviewer step because its kind had no reason text."""
    from buildanything import changes
    from buildanything.ledger import Actor, Ledger

    led = Ledger(tmp_path)
    led.append(Actor.human(), "project.created", name="X", aim="", scope="")
    review = {"recommendation": "approve", "risks": [], "missed": []}
    for kind in changes.KINDS:
        if kind in changes.ROUTINE_KINDS:
            continue
        reasons = changes.must_ask_person(led, {"kind": kind, "target": "", "details": {}}, review, [])
        assert reasons and all(isinstance(r, str) for r in reasons), kind


def test_parts_the_person_already_has_arent_on_the_buying_list() -> None:
    from buildanything.designneeds import is_owned, to_buy_part

    def part(process):
        return {"fields": {"process": process}}

    assert not to_buy_part(part("Existing purchased kit components")) and is_owned(part("Existing purchased kit components"))
    assert is_owned(part("Existing 3D-printed part")) and is_owned(part("Already owned servo"))
    assert to_buy_part(part("Existing or purchased padded support")) and not is_owned(part("Existing or purchased padded support"))
    assert to_buy_part(part("Purchased off the shelf")) and not is_owned(part("3D printed in PLA"))
