import shutil

import pytest

from buildanything import ci
from buildanything import codegen as gen, gates, interfaces as itf
from buildanything.ledger import Actor, Ledger, PermissionDenied

HUMAN = Actor.human()
AGENT = Actor.agent("electrical")


def telemetry(**extra):
    return {
        "name": "TELEMETRY", "id": 1, "transport": {"type": "udp", "port": 5005}, "rate_hz": 10, "producer": "drone", "consumers": ["hub"],
        "fields": [
            {"name": "drone_id", "type": "uint8"},
            {"name": "ned_x", "type": "float32", "unit": "m", "min": -500, "max": 500},
            {"name": "nav_state", "type": "enum8", "enum": {"IDLE": 0, "FLYING": 1}},
            {"name": "tag_id", "type": "int8", "note": "-1 if no tag"},
            {"name": "reloc_age_s", "type": "uint16", "unit": "s", "min": 0, "max": 600, "sentinel": 65535},
            {"name": "stamp", "type": "uint64"},
            {"name": "blocked", "type": "bytes[4]"},
        ], **extra,
    }


def upload():
    return {"name": "TRAJECTORY", "id": 2, "transport": {"type": "espnow"}, "producer": "hub", "consumers": ["drone"], "chunked": True, "endianness": "big", "fields": [{"name": "chunk", "type": "var_bytes", "max": 180}]}


@pytest.fixture
def led(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.append(HUMAN, "project.created", name="T", aim="A", scope="S")
    return ledger


def test_layout_size_and_framing(led):
    p = itf.save_packet(led, telemetry(), AGENT)
    assert p["version"] == "1.0" and p["fixed_size"] == 2 + 1 + 4 + 1 + 1 + 2 + 8 + 4 + 2
    assert p["wire"][0]["name"] == "pkt_type" and p["wire"][2]["offset"] == 2 and p["waiting"] == ["hub"]


@pytest.mark.parametrize("change, text", [
    ({"id": 300}, "0 to 255"), ({"name": "bad name"}, "capitals"), ({"consumers": []}, "receives"), ({"consumers": ["drone"]}, "both"),
    ({"transport": {"type": "udp"}}, "port"), ({"transport": {"type": "carrier-pigeon"}}, "Transport"),
    ({"fields": [{"name": "for", "type": "uint8"}]}, "can't be used"), ({"fields": [{"name": "a", "type": "uint9"}]}, "unknown type"),
    ({"fields": [{"name": "a", "type": "uint8"}, {"name": "a", "type": "uint8"}]}, "twice"),
    ({"fields": [{"name": "a", "type": "var_bytes", "max": 5}, {"name": "b", "type": "uint8"}]}, "last field"),
    ({"fields": [{"name": "a", "type": "var_bytes"}]}, "maximum size"), ({"fields": [{"name": "a", "type": "uint8", "max": 999}]}, "doesn't fit"),
    ({"fields": [{"name": "a", "type": "enum8"}]}, "enum needs"), ({"chunked": True}, "chunked packet"),
])
def test_bad_definitions_are_refused(led, change, text):
    with pytest.raises(itf.InterfaceError, match=text):
        itf.save_packet(led, {**telemetry(), **change}, HUMAN)


def test_ids_are_unique_and_transport_size_is_enforced(led):
    itf.save_packet(led, telemetry(), HUMAN)
    with pytest.raises(itf.InterfaceError, match="already used"):
        itf.save_packet(led, {**upload(), "id": 1}, HUMAN)
    with pytest.raises(itf.InterfaceError, match="carries 250"):
        itf.save_packet(led, {**upload(), "fields": [{"name": "chunk", "type": "var_bytes", "max": 400}]}, HUMAN)


def test_breaking_change_bumps_major_and_clears_acks(led):
    itf.save_packet(led, telemetry(), HUMAN)
    itf.acknowledge(led, "TELEMETRY", "hub", HUMAN)
    fields = telemetry()["fields"]
    fields[1] = {**fields[1], "unit": "cm"}
    p = itf.save_packet(led, {**telemetry(), "fields": fields}, HUMAN)
    assert p["version"] == "2.0" and p["waiting"] == ["hub"] and p["history"][-1]["breaking"]
    assert p["wire"][1]["const"] == 2


def test_harmless_change_keeps_acks(led):
    itf.save_packet(led, telemetry(), HUMAN)
    itf.acknowledge(led, "TELEMETRY", "hub", HUMAN)
    p = itf.save_packet(led, telemetry(rate_hz=20), HUMAN)
    assert p["version"] == "1.1" and p["waiting"] == [] and itf.save_packet(led, telemetry(rate_hz=20), HUMAN)["version"] == "1.1"


def test_only_a_person_can_acknowledge_or_remove(led):
    itf.save_packet(led, telemetry(), HUMAN)
    with pytest.raises(PermissionDenied):
        itf.acknowledge(led, "TELEMETRY", "hub", AGENT)
    with pytest.raises(PermissionDenied):
        itf.remove_packet(led, "TELEMETRY", "", AGENT)


def test_gate_needs_every_acknowledgement_then_freezes_breaking_changes(led):
    for gate in ("G0", "G1", "G2"):
        led.append(HUMAN, "gate.approved", gate=gate)
    assert "Define at least one" in gates.missing("G3", led.state())[0]
    itf.save_packet(led, telemetry(), HUMAN)
    with pytest.raises(gates.GateError, match="hub"):
        gates.approve(led, "G3", HUMAN)
    itf.acknowledge(led, "TELEMETRY", "hub", HUMAN)
    with pytest.raises(gates.GateError, match="automatic check"):
        gates.approve(led, "G3", HUMAN)
    ci.record(led, *ci.pending(led)[0], True, [])
    gates.approve(led, "G3", HUMAN)
    changed = {**telemetry(), "fields": telemetry()["fields"][:-1]}
    with pytest.raises(itf.InterfaceError, match="person"):
        itf.save_packet(led, changed, AGENT)
    with pytest.raises(itf.InterfaceError, match="reason"):
        itf.save_packet(led, changed, HUMAN)
    assert itf.save_packet(led, changed, HUMAN, "The blocked map moved to its own packet.")["version"] == "2.0"


def test_python_round_trip_and_every_rejection(led, tmp_path):
    itf.save_packet(led, telemetry(), HUMAN)
    itf.regenerate(tmp_path, led.state())
    m = itf.load_python(tmp_path)
    good = m.Telemetry(drone_id=3, ned_x=2.5, nav_state=1, tag_id=-1, reloc_age_s=65535, stamp=2**40, blocked=b"abcd").encode()
    back = m.decode_any(good)
    assert (back.ned_x, back.tag_id, back.reloc_age_s, back.stamp, back.blocked) == (2.5, -1, 65535, 2**40, b"abcd")

    def status(data):
        with pytest.raises(m.PacketError) as caught:
            m.Telemetry.decode(data)
        return caught.value.status

    assert status(good[:-1]) == "size"
    assert status(bytes([9]) + good[1:]) == "type"
    assert status(bytes([1, 7]) + good[2:]) == "version"
    bad = bytearray(good)
    bad[4] ^= 1
    assert status(bytes(bad)) == "checksum"

    def with_crc(**values):
        return m.Telemetry(**{"blocked": b"abcd", **values}).encode()

    assert status(with_crc(ned_x=900.0)) == "range"
    assert status(with_crc(nav_state=9)) == "range"
    assert status(with_crc(reloc_age_s=601)) == "range"


def test_chunks_never_release_partial_data_or_run_twice(led, tmp_path):
    itf.save_packet(led, upload(), HUMAN)
    itf.regenerate(tmp_path, led.state())
    m = itf.load_python(tmp_path)
    r = m.Reassembler(4)
    assert r.add(7, 1, 2, b"xy") is None
    assert r.add(7, 1, 2, b"xy") is None
    assert r.add(7, 0, 2, b"abcd") == b"abcdxy"
    with pytest.raises(m.PacketError):
        r.add(7, 0, 2, b"abcd")
    assert r.add(8, 0, 3, b"abcd") is None
    assert r.add(9, 0, 2, b"zzzz") is None
    assert r.add(9, 1, 2, b"q") == b"zzzzq"
    with pytest.raises(m.PacketError):
        r.add(10, 0, 3, b"short")


@pytest.mark.skipif(not shutil.which("gcc") or not shutil.which("node"), reason="needs gcc and node")
def test_c_python_and_typescript_agree(led, tmp_path):
    itf.save_packet(led, telemetry(), HUMAN)
    itf.save_packet(led, upload(), HUMAN)
    itf.save_packet(led, {"name": "BARE", "id": 3, "raw": True, "transport": {"type": "serial"}, "producer": "drone", "consumers": ["hub"], "fields": [{"name": "start", "type": "uint8", "const": 170}, {"name": "value", "type": "int32"}]}, HUMAN)
    result = itf.check_generated(tmp_path, led.state())
    assert all(r["ok"] and r["ran"] for r in result.values()), result


def test_generated_files_reach_devices_and_hand_edits_are_detected(led, tmp_path):
    (tmp_path / "firmware" / "drone").mkdir(parents=True)
    (tmp_path / "firmware" / "drone" / "platformio.ini").write_text("[env:device]\n")
    (tmp_path / "firmware" / "hub").mkdir()
    (tmp_path / "firmware" / "other").mkdir()
    itf.save_packet(led, telemetry(), HUMAN)
    itf.regenerate(tmp_path, led.state())
    assert (tmp_path / "firmware/drone/include/ba_packets.h").exists() and (tmp_path / "firmware/hub/generated/packets.py").exists()
    assert not (tmp_path / "firmware/other/generated").exists() and itf.drift(tmp_path, led.state()) == []
    (tmp_path / "firmware/hub/generated/packets.py").write_text("# edited")
    assert itf.drift(tmp_path, led.state()) == ["firmware/hub/generated/packets.py"]


def test_simulator_passes_clean_link(led):
    itf.save_packet(led, telemetry(), HUMAN)
    report = itf.simulate(led, "TELEMETRY", count=5)
    assert report["ok"] and all(c["state"] == "pass" for c in report["checks"])
    assert led.state().packets["TELEMETRY"]["simulated"]["ok"]


@pytest.mark.parametrize("fault, failed", [("drop", 1), ("wrong_port", 1), ("corrupt", 2), ("truncate", 2), ("old_version", 3)])
def test_simulator_failure_lands_on_the_right_check(led, fault, failed):
    itf.save_packet(led, telemetry(), HUMAN)
    report = itf.simulate(led, "TELEMETRY", fault, count=4)
    states = [c["state"] for c in report["checks"]]
    assert states[failed] == "fail" and all(s == "pass" for s in states[:failed]) and all(s == "todo" for s in states[failed + 1:])


def test_agents_cannot_report_simulator_results(led):
    with pytest.raises(PermissionDenied):
        led.append(AGENT, "packet.simulated", packet="X", fault="none", ok=True, version="1.0", checks=[], nodes=1)


def test_definition_from_agent_output():
    output = itf.PacketOut(name="CMD", id=4, transport_type="udp", port=9, rate_hz=None, endianness="little", producer="hub", consumers=["node"], chunked=False, note="", fields=[
        itf.FieldOut(name="mode", type="enum8", unit=None, note=None, const=None, min=None, max=None, sentinel=None, enum_names=["A"], enum_values=[0]),
        itf.FieldOut(name="blob", type="var_bytes", unit=None, note=None, const=None, min=2, max=20.0, sentinel=None, enum_names=[], enum_values=[]),
    ])
    made = itf.definition_from(output)
    assert itf.validate(made) == [] and made["fields"][1]["max"] == 20 and "min" not in made["fields"][1] and made["fields"][0]["enum"] == {"A": 0}


def test_every_status_name_is_documented():
    assert set(itf.STATUS_CODE) == set(gen.STATUS) - {"ok"}


# ---- Link issues ----

from buildanything import firmware as fw, links, software as sw, workspace as ws  # noqa: E402


@pytest.fixture
def issue(led):
    itf.save_packet(led, telemetry(), HUMAN)
    return links.open_issue(led, "No telemetry", ["drone", "hub"], "TELEMETRY", "Reported", HUMAN)["id"]


def test_free_checks_run_on_open(led, issue):
    found = links.issues(led)[issue]["automatic"]
    assert any("match the definition" in t for t in found) and any("passed all five" in t for t in found) and any("acknowledged" in t for t in found)


def test_checks_are_ordered_and_need_evidence(led, issue):
    with pytest.raises(links.LinkError, match="evidence"):
        links.record_check(led, issue, 0, "pass", " ", HUMAN)
    with pytest.raises(links.LinkError, match="order"):
        links.record_check(led, issue, 2, "pass", "x", HUMAN)
    links.record_check(led, issue, 0, "pass", "TX LED blinked", HUMAN)
    links.record_check(led, issue, 1, "fail", "tcpdump shows nothing", HUMAN)
    now = links.record_check(led, issue, 0, "pass", "Logged 10 sends", HUMAN)
    assert now["failed"] is None and now["current"] == 1
    links.record_check(led, issue, 0, "fail", "Actually nothing", HUMAN)
    assert links.issues(led)[issue]["checks"][1]["state"] == "todo"


def test_an_agent_cannot_record_a_result(led, issue):
    with pytest.raises(PermissionDenied):
        links.record_check(led, issue, 0, "pass", "trust me", AGENT)


def test_only_a_full_transfer_closes_an_issue(led, issue):
    for index in range(4):
        links.record_check(led, issue, index, "pass", "seen", HUMAN)
    with pytest.raises(links.LinkError, match="end-to-end"):
        links.close(led, issue, "looks fine", HUMAN)
    links.record_check(led, issue, 4, "pass", "The drone moved to the waypoint.", HUMAN)
    assert links.close(led, issue, "Full transfer worked.", HUMAN)["status"] == "closed"


@pytest.mark.parametrize("cause, device, to, domain", [
    ("wrong_address", "hub", "Software workspace: hub", "code"), ("rejected_by_device", "drone", "Software workspace: drone", "code"),
    ("physical_fault", "drone", "Electrical Troubleshooting", "wiring"), ("firewall", "hub", "Setup of hub", None),
    ("weak_signal", "", "Stays here until measured", None), ("format_mismatch", "", "Interface", None),
])
def test_routing_follows_the_table_and_the_issue_stays_open(led, issue, cause, device, to, domain):
    from buildanything import debugging

    with pytest.raises(links.LinkError, match="failed"):
        links.route(led, issue, cause, device, "", HUMAN)
    links.record_check(led, issue, 0, "fail", "No log line", HUMAN)
    now = links.route(led, issue, cause, device, "Port 5006 in code", HUMAN)
    assert now["routes"][0]["to"] == to and now["status"] == "open"
    sessions = list(debugging.sessions(led).values())
    assert (sessions[0]["domain"] == domain and sessions[0]["device"] == device) if domain else not sessions


def test_antenna_placement_creates_a_mechanical_task(led, issue):
    links.record_check(led, issue, 0, "fail", "RSSI -90 near the frame", HUMAN)
    links.route(led, issue, "antenna_placement", "", "", HUMAN)
    assert led.state().of_type("task", "proposed")[0]["fields"]["department"] == "Mechanical"


class StubGateway:
    def __init__(self, reply=None, spend=None, led=None):
        self.reply, self.spend, self.led, self.calls = reply, spend, led, []

    async def run(self, *, agent, values, output_type=str, **_):
        self.calls.append((agent, values))
        if self.spend:
            self.led.append(Actor.system("gateway"), "spend.recorded", agent=agent, area="Interface", tier="Standard", model="x", usd=self.spend, requests=1, input=1, output=1)
        return self.reply(values) if callable(self.reply) else self.reply


ADVICE = links.Advice(check=2, possible=["wrong_address"], ruled_out=["format_mismatch"], instruction="Watch the hub log", look_for="A line per packet", why_person="Faster to see")


@pytest.mark.asyncio
async def test_advice_is_only_advice_and_has_a_cap(led, issue):
    gateway = StubGateway(ADVICE, spend=0.3, led=led)
    now = await links.advise(led, gateway, issue)
    assert now["advice"]["check"] == 2 and all(c["state"] == "todo" for c in now["checks"]) and "automatic_findings" in gateway.calls[0][1]
    await links.advise(led, gateway, issue)
    with pytest.raises(links.LinkError, match="limit"):
        await links.advise(led, gateway, issue)


@pytest.mark.asyncio
async def test_a_bad_agent_packet_gets_feedback_then_saves(led):
    bad = itf.PacketOut(name="bad name", id=1, transport_type="udp", port=9, rate_hz=None, endianness="little", producer="hub", consumers=["node"], chunked=False, note="", fields=[])
    good = bad.model_copy(update={"name": "PING", "fields": [itf.FieldOut(name="n", type="uint8", unit=None, note=None, const=None, min=None, max=None, sentinel=None, enum_names=[], enum_values=[])]})
    replies = [bad, good]
    gateway = StubGateway(lambda v: replies.pop(0))
    made = await itf.draft_packet(led, gateway, "ping")
    assert made["name"] == "PING" and "capitals" in gateway.calls[1][1]["feedback"]


# ---- Software workspaces ----


def host_file(path="app/hub.py", code="def main():\n    return 1\n"):
    return fw.FileOut(path=path, owner="Software", blocks=[fw.BlockOut(title="Hub loop", description="Receives telemetry from each node and logs it.", code=code)])


def test_python_blocks_round_trip_with_python_comments(tmp_path):
    fw.write_files(tmp_path, "hub", [host_file()], host=True)
    text = (tmp_path / "firmware/hub/app/hub.py").read_text()
    assert text.startswith("# @block") and compile(text, "x", "exec")
    assert fw.scan_workspace(tmp_path, "hub")[0]["blocks"][0]["state"] == "documented"


def test_agents_cannot_overwrite_generated_files():
    assert sw.path_problems([host_file("generated/packets.py")]) and sw.path_problems([host_file("app/evil.sh")])
    assert fw.documentation_problems([fw.FileOut(path="include/ba_packets.h", owner="Electrical", blocks=[])])


@pytest.mark.asyncio
async def test_software_agent_retries_bad_syntax_and_flags_licences(led, tmp_path):
    good = sw.SoftwareOut(files=[host_file()], dependencies=[sw.DependencyOut(name="a/b", licence="GPL-3.0", url="u"), sw.DependencyOut(name="c/d", licence="MIT", url="u")], setup_steps=[sw.StepOut(instruction="pip install x", expect="Installed", source="docs")])
    bad = good.model_copy(update={"files": [host_file(code="def (:\n")]})
    replies = [bad, good]
    gateway = StubGateway(lambda v: replies.pop(0))
    result = await sw.generate(led, gateway, tmp_path, "hub", "Log telemetry")
    assert result["build"]["ok"] and result["licenceReview"] == ["a/b: GPL-3.0"] and "line" in gateway.calls[1][1]["feedback"]
    plan = sw.setup_view(led.events(), "hub")
    assert plan["steps"][0]["done"] is None
    sw.mark_step(led, "hub", 0, "worked", HUMAN)
    assert sw.setup_view(led.events(), "hub")["steps"][0]["done"] == "worked"
    (tmp_path / "firmware/hub/app/hub.py").write_text("def (:\n")
    assert not sw.build(tmp_path, "hub")["ok"]


def test_github_failure_is_reported_not_invented(monkeypatch):
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("x")

    monkeypatch.setattr(sw.httpx, "get", boom)
    assert "couldn't be searched" in sw.search_open_source("x")["error"]


# ---- Two people ----


def _project(root, name="Shared"):
    path = root / "a"
    path.mkdir()
    ws.write_git_files(path)
    ledger = Ledger(path)
    ledger.append(HUMAN, "project.created", name=name, aim="A", scope="S")
    ws.init(path)
    ws.commit(path, "Create")
    return path, ledger


@pytest.fixture
def pair(tmp_path):
    bare = tmp_path / "team.git"
    ws.subprocess.run(["git", "init", "--bare", "--quiet", str(bare)], check=True)
    a, led_a = _project(tmp_path)
    ws.set_remote(a, str(bare))
    assert ws.sync(a, "Ann", lambda: None)["pushed"]
    home = tmp_path / "second"
    joined = ws.clone_project(home, str(bare))
    return a, led_a, home / joined, Ledger(home / joined)


def test_two_people_merge_ledgers_without_losing_or_reordering(pair):
    a, led_a, b, led_b = pair
    first = led_a.propose(Actor.human("Ann"), "decision", text="Use UDP")
    second = led_b.propose(Actor.human("Bo"), "decision", text="Use ESP32")
    assert ws.sync(b, "Bo", lambda: None)["ok"]
    result = ws.sync(a, "Ann", lambda: None)
    assert result["ok"] and result["brought"] == 1
    ws.sync(b, "Bo", lambda: None)
    assert first in led_b.state().records and second in led_a.state().records
    assert [e["id"] for e in led_a.events()] == [e["id"] for e in led_b.events()]
    newest = led_b.append(Actor.human("Bo"), "record.confirmed", id=first)
    assert newest["at"] > max(e["at"] for e in led_b.events() if e["id"] != newest["id"])


def test_opposite_decisions_are_flagged(pair):
    a, led_a, b, led_b = pair
    record = led_a.propose(HUMAN, "decision", text="Use UDP")
    ws.sync(a, "Ann", lambda: None)
    ws.sync(b, "Bo", lambda: None)
    led_a.append(Actor.human("Ann"), "record.confirmed", id=record)
    led_b.append(Actor.human("Bo"), "record.rejected", id=record)
    ws.sync(a, "Ann", lambda: None)
    result = ws.sync(b, "Bo", lambda: None)
    assert "Use UDP" in result["review"][0]


def test_a_real_file_conflict_changes_nothing(pair):
    a, led_a, b, led_b = pair
    (a / "notes.txt").write_text("one\n")
    ws.sync(a, "Ann", lambda: None)
    ws.sync(b, "Bo", lambda: None)
    (a / "notes.txt").write_text("ann\n")
    (b / "notes.txt").write_text("bo\n")
    ws.sync(a, "Ann", lambda: None)
    result = ws.sync(b, "Bo", lambda: None)
    assert not result["ok"] and result["conflicts"] == ["notes.txt"] and not result["pushed"]
    assert (b / "notes.txt").read_text() == "bo\n" and not (b / ".git" / "MERGE_HEAD").exists()


def test_generated_file_conflicts_resolve_themselves(pair):
    a, led_a, b, led_b = pair
    for side, text in ((a, "ann"), (b, "bo")):
        (side / "AGENTS.md").write_text(text)
    ws.sync(a, "Ann", lambda: None)
    assert ws.sync(b, "Bo", lambda: None)["ok"]


def test_a_tampered_history_is_refused(pair):
    a, led_a, b, led_b = pair
    led_a.append(HUMAN, "limit.changed", usd=50)
    lines = led_a.path.read_text(encoding="utf-8").replace('"usd": 50', '"usd": 5000')
    led_a.path.write_text(lines, encoding="utf-8", newline="\n")
    ws.commit(a, "Tamper")
    ws._git(a, "push", "origin", "HEAD")
    with pytest.raises(ws.SyncError, match="integrity"):
        ws.sync(b, "Bo", lambda: None)
    assert not (b / ".git" / "MERGE_HEAD").exists()


@pytest.mark.parametrize("url", ["--upload-pack=evil", "ext::sh -c evil", "https://me:secret@host/r.git", "nonsense"])
def test_dangerous_addresses_are_refused(tmp_path, url):
    with pytest.raises(ws.SyncError):
        ws.clone_project(tmp_path, url)


def test_handoff_says_what_a_newcomer_needs(led, issue, tmp_path):
    (tmp_path / "firmware" / "hub").mkdir(parents=True)
    text = ws.handoff_markdown(led.state(), led)
    assert "TELEMETRY v1.0" in text and "No telemetry" in text and "waiting for hub" in text and "hub (computer)" in text


def test_a_message_with_no_fields_is_a_valid_event(led, tmp_path):
    # Such as "the board is ready": the message itself is the news.
    itf.save_packet(led, {"name": "BOARD_READY", "id": 4, "transport": {"type": "serial"}, "producer": "drone", "consumers": ["hub"], "fields": []}, HUMAN)
    itf.regenerate(tmp_path, led.state())
    m = itf.load_python(tmp_path)
    assert isinstance(m.decode_any(m.BoardReady().encode()), m.BoardReady)
    if shutil.which("gcc") and shutil.which("node"):
        result = itf.check_generated(tmp_path, led.state())
        assert all(r["ok"] and r["ran"] for r in result.values()), result
