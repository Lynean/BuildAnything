import pytest

from buildanything import capture as cp, codegen as gen, interfaces as itf, links
from buildanything.ledger import Actor, Ledger, PermissionDenied

HUMAN = Actor.human()
PORTS = {"node": "COM1", "gateway": "COM2"}


@pytest.fixture
def led(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.append(HUMAN, "project.created", name="T", aim="A", scope="S")
    itf.save_packet(ledger, {"name": "STATUS", "id": 20, "transport": {"type": "espnow"}, "producer": "node", "consumers": ["gateway"], "fields": [{"name": "seq", "type": "uint32"}, {"name": "level", "type": "uint8", "max": 50}]}, HUMAN)
    itf.regenerate(tmp_path, ledger.state())
    return ledger


@pytest.fixture
def issue(led):
    return links.open_issue(led, "No status", ["node", "gateway"], "STATUS", "Reported", HUMAN)["id"]


def frames(led, count=10, **change):
    m = itf.load_python(led.root)
    return [m.Status(seq=i, level=change.get("level", 1)).encode() for i in range(count)]


def tx(count=10, result=0):
    return [f"@ba tx 20 {result} ab" for _ in range(count)]


def rx(data, rssi=-60):
    return [f"@ba rx 20 {rssi} {d.hex()}" for d in data]


def run(monkeypatch, led, issue, sender, receiver):
    monkeypatch.setattr(cp, "listen_all", lambda ports, seconds, root: {"node": sender, "gateway": receiver})
    return cp.capture(led, issue, PORTS)


def states(led, issue):
    return [c["state"] for c in links.issues(led)[issue]["checks"]]


HEART = ["@ba info role=gateway channel=1"]


def test_a_healthy_link_passes_all_five(monkeypatch, led, issue):
    data = frames(led)
    result = run(monkeypatch, led, issue, tx(), HEART + rx(data) + ["@ba done 20 0"] * 10)
    assert result == {"failed": None, "unknown": None} and states(led, issue) == ["pass"] * 5
    assert links.issues(led)[issue]["checks"][0]["source"] == "capture"
    assert any("channel=1" in t for t in links.issues(led)[issue]["automatic"])


def test_a_live_receiver_that_heard_nothing_fails_reached(monkeypatch, led, issue):
    assert run(monkeypatch, led, issue, tx(), HEART)["failed"] == 1
    assert states(led, issue) == ["pass", "fail", "todo", "todo", "todo"]


def test_a_silent_receiver_is_unknown_not_a_failure(monkeypatch, led, issue):
    result = run(monkeypatch, led, issue, tx(), ["boot text with no evidence lines"])
    assert result["failed"] is None and "logged nothing" in result["unknown"] and states(led, issue) == ["pass", "todo", "todo", "todo", "todo"]


def test_a_silent_sender_records_nothing(monkeypatch, led, issue):
    assert "sender logged nothing" in run(monkeypatch, led, issue, [], HEART)["unknown"]
    assert states(led, issue) == ["todo"] * 5


def test_sends_refused_by_the_radio_fail_the_first_check(monkeypatch, led, issue):
    assert run(monkeypatch, led, issue, tx(result=12), HEART)["failed"] == 0


def test_damaged_frames_fail_could_read(monkeypatch, led, issue):
    data = [d[:-1] + bytes([d[-1] ^ 1]) for d in frames(led)]
    assert run(monkeypatch, led, issue, tx(), HEART + rx(data))["failed"] == 2


def test_other_version_fails_accepted_and_is_named(monkeypatch, led, issue):
    data = [d[:1] + bytes([9]) + d[2:] for d in frames(led)]
    assert run(monkeypatch, led, issue, tx(), HEART + rx(data))["failed"] == 3
    assert "wrong version" in links.issues(led)[issue]["checks"][3]["evidence"]


def test_valid_but_not_carried_out_fails_the_last_check(monkeypatch, led, issue):
    assert run(monkeypatch, led, issue, tx(), HEART + rx(frames(led)))["failed"] == 4


def test_a_fixed_link_overwrites_old_results(monkeypatch, led, issue):
    run(monkeypatch, led, issue, tx(), HEART)
    run(monkeypatch, led, issue, tx(), HEART + rx(frames(led)) + ["@ba done 20 0"] * 10)
    assert states(led, issue) == ["pass"] * 5


def test_other_packets_and_garbage_are_ignored():
    parsed = cp.parse(["@ba tx 99 0 aa", "noise", "@ba tx 20 0 zz", "@ba rx 20 -5 0a"], 20)
    assert parsed["tx"] == [] and parsed["rx"] == [(-5, b"\n")]


@pytest.mark.parametrize("ports, text", [({"node": "COM1"}, "different serial port"), ({"node": "COM1", "gateway": "COM1"}, "different serial port")])
def test_each_device_needs_its_own_port(led, issue, ports, text):
    with pytest.raises(cp.CaptureError, match=text):
        cp.capture(led, issue, ports)


def test_needs_a_packet(led):
    issue = links.open_issue(led, "x", ["node", "gateway"], None, "r", HUMAN)["id"]
    with pytest.raises(cp.CaptureError, match="packet"):
        cp.capture(led, issue, PORTS)


def test_agents_still_cannot_record_results(led, issue):
    with pytest.raises(PermissionDenied):
        links.record_check(led, issue, 0, "pass", "x", Actor.agent("electrical"))


def test_generated_header_offers_the_logging():
    packet = itf.expand("A", {"packet": {"name": "A", "id": 1, "transport": {"type": "udp", "port": 1}, "producer": "a", "consumers": ["b"], "fields": [{"name": "n", "type": "uint8"}]}, "major": 1, "minor": 0, "acks": {}, "history": []})
    assert "ba_log_event" in gen.generate_c([packet]) and "ba_log_info" in gen.generate_c([packet])


def test_computer_end_counts_only_lines_written_during_the_capture(monkeypatch, led, issue):
    log = cp.computer_log(led.root, "gateway")
    log.parent.mkdir(parents=True)
    old = "".join(line + "\\n" for line in HEART + rx(frames(led)) + ["@ba done 20 0"] * 10)
    log.write_text(old, encoding="utf-8")

    def new_lines(path, seconds):
        size = path.stat().st_size
        with path.open("a", encoding="utf-8") as f:
            f.write("".join(line + "\\n" for line in HEART))
        with path.open("rb") as f:
            f.seek(size)
            return f.read().decode().splitlines()

    monkeypatch.setattr(cp, "_read_log", new_lines)
    monkeypatch.setattr(cp.flashing, "listen", lambda port, seconds: tx())
    assert cp.capture(led, issue, {"node": "COM1", "gateway": cp.COMPUTER})["failed"] == 1


def test_generated_python_writes_lines_capture_can_read(led):
    m = itf.load_python(led.root)
    log = m.EvidenceLog(led.root / ".cache/link-logs/hub.log")
    log.event("rx", 20, -60, b"\\x01\\x02")
    log.event("done", 20, 0)
    log.info("listening")
    parsed = cp.parse(log.path.read_text().splitlines(), 20)
    assert parsed["rx"] == [(-60, b"\\x01\\x02")] and parsed["done"] == 1 and parsed["info"] == ["listening"]
