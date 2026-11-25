import subprocess

import pytest

from buildanything import agents as ag, firmware as fw, flashing as fl, gates, interfaces as itf, overview as ov, software as sw
from buildanything.citations import Paper
from buildanything.ledger import Actor, Ledger, PermissionDenied

HUMAN = Actor.human()


@pytest.fixture
def led(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.append(HUMAN, "project.created", name="T", aim="A", scope="S")
    return ledger


def task(led, key, depends=()):
    return led.propose(Actor.agent("controller"), "task", key=key, department="Software", title=key, weeks=1, depends_on=list(depends), start_week=0)


def test_review_queue_has_only_real_waiting_items(led):
    assert ov.review_queue(led) == []
    req = led.propose(Actor.agent("controller"), "requirement", key="R1", text="Tracks a person", verification="Bench test")
    led.propose(Actor.agent("datasheet"), "parameter", component="c", kind="x", value=1)
    itf.save_packet(led, {"name": "PING", "id": 1, "transport": {"type": "udp", "port": 9}, "producer": "a", "consumers": ["b"], "fields": [{"name": "n", "type": "uint8"}]}, HUMAN)
    items = {i["id"]: i for i in ov.review_queue(led)}
    assert items[req]["kind"] == "record" and items["PING:b"]["kind"] == "ack" and items["parameter"]["screen"] == "Electrical"
    led.append(HUMAN, "record.confirmed", id=req)
    assert req not in {i["id"] for i in ov.review_queue(led)}


def test_a_ready_gate_and_serious_findings_are_listed(led):
    led.propose(Actor.agent("verifier"), "finding", run=1, severity="high", safety=True, text="No stop", related=[])
    for gate in ("G0", "G1"):
        led.append(HUMAN, "gate.approved", gate=gate)
    item = next(i for i in ov.review_queue(led) if i["title"] == "No stop")
    assert item["type"] == "Safety finding" and item["screen"] == "Headquarters"


def test_tasks_wait_for_their_dependencies(led):
    first, second = task(led, "T1"), task(led, "T2", ["T1"])
    assert {t["key"]: t["state"] for t in ov.task_view(led)} == {"T1": "Needs review", "T2": "Needs review"}
    for record in (first, second):
        led.append(HUMAN, "record.confirmed", id=record)
    assert {t["key"]: t["state"] for t in ov.task_view(led)} == {"T1": "Ready", "T2": "Waiting: T1"}
    ov.mark_task_done(led, first, True, HUMAN)
    assert {t["key"]: t["state"] for t in ov.task_view(led)} == {"T1": "Done", "T2": "Ready"}
    with pytest.raises(PermissionDenied):
        ov.mark_task_done(led, second, True, Actor.agent("controller"))


def test_uploads_are_validated(led):
    with pytest.raises(ov.OverviewError, match="kind"):
        ov.upload_source(led, "a.pdf", "Nonsense", b"%PDF-", HUMAN)
    with pytest.raises(ov.OverviewError, match="isn't a PDF"):
        ov.upload_source(led, "a.pdf", "Standard", b"hello", HUMAN)


class Down:
    def search(self, query):
        raise TimeoutError("down")

    def get(self, paper_id):
        return None


def test_uploaded_sources_are_searchable_even_when_arxiv_is_down(led, monkeypatch):
    (led.root / "sources").mkdir()
    (led.root / "sources/a.pdf").write_bytes(b"%PDF-")
    monkeypatch.setattr(ov.datasheets, "page_texts", lambda path: ["Servo stall current is 650 mA.", "Unrelated."])
    led.append(HUMAN, "record.confirmed", id=led.propose(HUMAN, "source", ref="abc", title="SG90", url=None, tier="Vendor document", origin="upload", file="a.pdf", pages=2, has_text=True))
    index = ov.ProjectIndex(led, Down())
    assert index.search("servo stall")[0].id == "abc" and "650 mA" in index.get("abc").text
    with pytest.raises(TimeoutError):
        index.search("unmatched zebra")


# ---- Code checks for computer programs ----


def write(tmp_path, name, code):
    path = tmp_path / "firmware/hub" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code)


def test_undefined_names_and_bad_packet_fields_fail(tmp_path):
    write(tmp_path, "generated/packets.py", "# generated\nclass Ping:\n    pass\n")
    write(tmp_path, "good.py", "import json\nprint(json.dumps({}))\n")
    assert sw.build(tmp_path, "hub")["ok"]
    write(tmp_path, "bad.py", "print(missing_name)\n")
    assert any("missing_name" in e for e in sw.build(tmp_path, "hub")["errors"])


def test_wrong_packet_field_is_caught(tmp_path):
    write(tmp_path, "generated/packets.py", "from dataclasses import dataclass\n@dataclass\nclass Ping:\n    ID = 1\n    seq: int = 0\n    def encode(self):\n        return b''\n")
    write(tmp_path, "app.py", "from generated.packets import Ping\nPing(sequence=1)\n")
    result = sw.build(tmp_path, "hub")
    assert not result["ok"] and any("sequence" in e for e in result["errors"])


def test_missing_library_is_a_warning(tmp_path):
    write(tmp_path, "app.py", "import numpy\nprint(numpy.pi)\n")
    result = sw.build(tmp_path, "hub", declared=[])
    assert result["ok"] and any("numpy" in w for w in result["warnings"])
    assert not sw.build(tmp_path, "hub", declared=["numpy"])["warnings"]


def test_tests_need_a_person_and_report_failures(led, tmp_path):
    write(led.root, "test_x.py", "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertEqual(1, 2)\n")
    with pytest.raises(sw.SoftwareError, match="Confirm"):
        sw.run_tests(led, led.root, "hub", False, HUMAN)
    with pytest.raises(PermissionDenied):
        sw.run_tests(led, led.root, "hub", True, Actor.agent("software"))
    result = sw.run_tests(led, led.root, "hub", True, HUMAN)
    assert not result["ok"] and "FAILED" in "\n".join(result["lines"])


def test_passing_tests_report_ok(led):
    write(led.root, "test_x.py", "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertEqual(1, 1)\n")
    assert sw.run_tests(led, led.root, "hub", True, HUMAN)["ok"]


# ---- Flash retries ----


@pytest.fixture
def flash(monkeypatch, led, tmp_path):
    monkeypatch.setattr(fw, "board_catalog", lambda: {"b": {"id": "b", "name": "B", "mcu": "ESP32S2", "platform": "espressif32", "framework": "arduino"}})
    monkeypatch.setattr(fl, "ports", lambda: [{"port": "COM8", "vid": 1, "pid": 1, "description": "x"}])
    board = {"chip": "ESP32-S2", "mac": "aaaaaaaaaaaa", "flash_bytes": 16}
    monkeypatch.setattr(fl, "identify", lambda port: board)
    monkeypatch.setattr(fl, "listen", lambda port, seconds=6.0: ["hello"])
    monkeypatch.setattr(fl, "_back_up", lambda port, b, backups: (None, "skipped"))
    monkeypatch.setattr(fl.conn, "detect_cli", lambda tool: {"found": True, "path": "pio"})
    monkeypatch.setattr(fl.time, "sleep", lambda s: None)

    def build(root, device, timeout=900):
        path = root / "firmware" / device / ".pio/build/device/firmware.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return {"ok": True, "ran": True, "errors": [], "detail": "ok"}

    monkeypatch.setattr(fw, "build", build)
    fw.write_platformio(led.root, "n", "b")
    return tmp_path / "backups"


def fails_then_works(monkeypatch, *codes):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        code = codes[len(calls) - 1]
        return subprocess.CompletedProcess(command, code, "", "" if code == 0 else "A fatal error occurred: No more data to read from the serial port")

    monkeypatch.setattr(fl.subprocess, "run", run)
    return calls


def go(led, backups):
    plan = fl.prepare(led, led.root, "n", "COM8")["plan"]
    return fl.approve(led, led.root, backups, "n", plan["id"], True, False, HUMAN)


def test_a_failed_connection_is_retried_and_flagged(monkeypatch, led, flash):
    calls = fails_then_works(monkeypatch, 1, 1, 0)
    result = go(led, flash)
    assert result["outcome"] == "running" and len(calls) == 3 and result["attempts"] == 3


def test_repeated_connection_failure_explains_what_to_do(monkeypatch, led, flash):
    calls = fails_then_works(monkeypatch, 1, 1, 1)
    result = go(led, flash)
    assert result["outcome"] == "flash_failed" and len(calls) == 3 and "BOOT" in result["help"] and "RESET" in result["help"]


def test_other_upload_errors_are_not_retried(monkeypatch, led, flash):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, "", "Error: compile problem")

    monkeypatch.setattr(fl.subprocess, "run", run)
    assert go(led, flash)["outcome"] == "flash_failed" and len(calls) == 1


def test_fixable_problems_are_reported_as_request_errors(tmp_path):
    from fastapi.testclient import TestClient

    from buildanything import api, providers

    root = tmp_path / "p"
    root.mkdir()
    client = TestClient(api.create_app(root, secrets=providers.MemoryStore()))
    pid = client.post("/api/projects", json={"name": "A", "aim": "B", "scope": "C"}).json()["id"]
    reply = client.post(f"/api/projects/{pid}/sources?filename=a.pdf&tier=Nope", content=b"%PDF-")
    assert reply.status_code == 400 and "kind" in reply.json()["detail"]
