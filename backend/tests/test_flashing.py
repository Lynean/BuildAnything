import subprocess

import pytest

from buildanything import firmware as fw, flashing as fl
from buildanything.ledger import Actor, Ledger, PermissionDenied

HUMAN = Actor.human()
BOARDS = {"esp32-s2-saola-1": {"id": "esp32-s2-saola-1", "name": "Saola", "mcu": "ESP32S2", "platform": "espressif32", "framework": "arduino"}, "uno": {"id": "uno", "name": "Arduino Uno", "mcu": "ATMEGA328P", "platform": "atmelavr", "framework": "arduino"}, "nucleo_f401re": {"id": "nucleo_f401re", "name": "Nucleo", "mcu": "STM32F401RET6", "platform": "ststm32", "framework": "arduino"}}
BOARD = {"chip": "ESP32-S2", "mac": "84f7037e530e", "flash_bytes": 16}


class Fake:
    """Stands in for the board and for PlatformIO. Records what really would have run."""

    def __init__(self, monkeypatch, root, backups):
        self.root, self.backups, self.board, self.calls, self.upload_code, self.verify_code, self.lines = root, backups, dict(BOARD), [], 0, 1, ["BA ok"]
        monkeypatch.setattr(fw, "board_catalog", lambda: BOARDS)
        monkeypatch.setattr(fl, "ports", lambda: [{"port": "COM8", "vid": 0x1a86, "pid": 1, "description": "CH340"}, {"port": "COM9", "vid": fl.ESPRESSIF_USB, "pid": 1, "description": "USB"}])
        monkeypatch.setattr(fl, "identify", lambda port: self.board)
        monkeypatch.setattr(fl, "listen", lambda port, seconds=6.0: self.lines)
        monkeypatch.setattr(fl, "_esptool", self.esptool)
        monkeypatch.setattr(fl.conn, "detect_cli", lambda tool: {"found": True, "path": "pio"})
        monkeypatch.setattr(fw, "build", self.build)
        monkeypatch.setattr(fl.subprocess, "run", self.pio)

    def esptool(self, port, *args, baud=None, timeout=120):
        self.calls.append(args[0])
        if args[0] == "read-flash":
            open(args[3], "wb").write(b"x" * BOARD["flash_bytes"])
            return subprocess.CompletedProcess([], 0, "", "")
        if args[0] == "verify-flash":
            return subprocess.CompletedProcess([], 0 if args[1] == "0" else self.verify_code, "", "")
        return subprocess.CompletedProcess([], 0, "", "")

    def build(self, root, device, timeout=900):
        path = root / "firmware" / device / ".pio/build/device/firmware.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"firmware")
        return {"ok": True, "ran": True, "errors": [], "detail": "Build succeeded."}

    def pio(self, command, **kwargs):
        self.calls.append("upload")
        return subprocess.CompletedProcess(command, self.upload_code, "Hash of data verified", "")


@pytest.fixture
def led(tmp_path):
    ledger = Ledger(tmp_path / "p")
    ledger.append(HUMAN, "project.created", name="T", aim="A", scope="S")
    return ledger


@pytest.fixture
def fake(monkeypatch, led, tmp_path):
    made = Fake(monkeypatch, led.root, tmp_path / "backups")
    fw.write_platformio(led.root, "node", "esp32-s2-saola-1")
    (led.root / "firmware/node/src").mkdir()
    (led.root / "firmware/node/src/main.cpp").write_text("void setup(){}")
    return made


def plan(led):
    return fl.prepare(led, led.root, "node", "COM8")["plan"]


def go(led, fake, flash_id, **kw):
    return fl.approve(led, led.root, fake.backups, "node", flash_id, kw.get("confirmed", True), kw.get("accept", False), kw.get("actor", HUMAN))


def test_ini_follows_the_chosen_board(led, fake):
    for board, platform in (("uno", "atmelavr"), ("nucleo_f401re", "ststm32"), ("esp32-s2-saola-1", "espressif32")):
        fw.write_platformio(led.root, "x", board)
        config = fw.device_config(led.root, "x")
        assert (config["board"], config["platform"], config["framework"]) == (board, platform, "arduino")
    with pytest.raises(ValueError, match="isn't a known board"):
        fw.write_platformio(led.root, "x", "made-up-board")


def test_native_usb_flags_are_added_and_removed(led, fake):
    fw.set_native_usb(led.root, "node", True, "ESP32-C6")
    assert "ARDUINO_USB_CDC_ON_BOOT=1" in (led.root / "firmware/node/platformio.ini").read_text()
    fw.set_native_usb(led.root, "node", False, "")
    assert "ARDUINO_USB_CDC_ON_BOOT" not in (led.root / "firmware/node/platformio.ini").read_text()


def test_generate_keeps_the_board_a_device_already_has(led, fake):
    assert fw.resolve_board(led.root, "node", "") == "esp32-s2-saola-1"
    with pytest.raises(ValueError, match="Choose"):
        fw.resolve_board(led.root, "other", "")


def test_a_wrong_chip_is_refused_before_anything_happens(led, fake):
    fake.board = {**BOARD, "chip": "ESP32-C6"}
    with pytest.raises(fl.FlashError, match="Nothing was changed"):
        plan(led)
    assert "upload" not in fake.calls


@pytest.mark.parametrize("chip, mcu, ok", [("ESP32-S2", "ESP32S2", True), ("ESP32-C6", "ESP32C6", True), ("ESP32", "ESP32", True), ("ESP32-S3", "ESP32S2", False), ("ESP32", "ESP32S3", False)])
def test_chip_comparison(chip, mcu, ok):
    assert fl.chip_matches(chip, mcu) is ok


def test_preparing_changes_nothing_on_the_board(led, fake):
    made = plan(led)
    assert made["chip"] == "ESP32-S2" and "upload" not in fake.calls and "read-flash" not in fake.calls


def test_full_flash_backs_up_first_then_listens(led, fake):
    result = go(led, fake, plan(led)["id"])
    assert result["outcome"] == "running" and fake.calls.index("read-flash") < fake.calls.index("upload")
    assert fl.list_backups(fake.backups)[0]["mac"] == BOARD["mac"]


def test_an_agent_can_never_approve(led, fake):
    with pytest.raises(PermissionDenied):
        go(led, fake, plan(led)["id"], actor=Actor.agent("electrical"))
    assert "upload" not in fake.calls


def test_needs_explicit_confirmation(led, fake):
    with pytest.raises(fl.FlashError, match="Confirm"):
        go(led, fake, plan(led)["id"], confirmed=False)
    assert "upload" not in fake.calls


def test_a_plan_works_once(led, fake):
    made = plan(led)
    go(led, fake, made["id"])
    with pytest.raises(fl.FlashError, match="already used"):
        go(led, fake, made["id"])


def test_changed_code_needs_a_new_plan(led, fake):
    made = plan(led)
    (led.root / "firmware/node/src/main.cpp").write_text("void loop(){}")
    with pytest.raises(fl.FlashError, match="changed"):
        go(led, fake, made["id"])
    assert "upload" not in fake.calls


def test_a_swapped_board_is_refused(led, fake):
    made = plan(led)
    fake.board = {**BOARD, "mac": "aaaaaaaaaaaa"}
    with pytest.raises(fl.FlashError, match="different board"):
        go(led, fake, made["id"])
    assert "upload" not in fake.calls


def test_failed_backup_stops_the_flash(led, fake, monkeypatch):
    def broken(port, *args, baud=None, timeout=120):
        fake.calls.append(args[0])
        return subprocess.CompletedProcess([], 1, "", "noise")

    made = plan(led)
    monkeypatch.setattr(fl, "_esptool", broken)
    with pytest.raises(fl.FlashError, match="nothing was flashed"):
        go(led, fake, made["id"])
    assert "upload" not in fake.calls


def test_backup_is_skipped_only_when_the_board_still_has_our_firmware(led, fake):
    go(led, fake, plan(led)["id"])
    fake.calls.clear()
    fake.verify_code = 0
    assert "Not needed" in go(led, fake, plan(led)["id"])["backup"] and "read-flash" not in fake.calls
    fake.calls.clear()
    fake.verify_code = 1
    go(led, fake, plan(led)["id"])
    assert "read-flash" in fake.calls


def test_crash_after_flashing_opens_a_debug_session(led, fake):
    from buildanything import debugging

    fake.lines = ["rst:0x3", "Guru Meditation Error", "Backtrace: 0x1"]
    assert go(led, fake, plan(led)["id"])["outcome"] == "crashing"
    assert list(debugging.sessions(led).values())[0]["device"] == "node"


@pytest.mark.parametrize("lines, outcome", [([], "no_output"), (["hello"], "running"), (["ESP-ROM:x"] * 4, "crashing"), (["brownout detector"], "crashing")])
def test_output_analysis(lines, outcome):
    assert fl.analyse_output(lines) == outcome


def test_upload_failure_is_reported(led, fake):
    fake.upload_code = 1
    assert go(led, fake, plan(led)["id"])["outcome"] == "flash_failed"


def test_non_espressif_boards_need_the_no_backup_consent(led, fake):
    fw.write_platformio(led.root, "node", "uno")
    fake.board = None
    made = plan(led)
    assert not made["can_back_up"]
    with pytest.raises(fl.FlashError, match="backed up"):
        go(led, fake, made["id"])
    assert go(led, fake, made["id"], accept=True)["outcome"] == "running"


def test_esp_board_that_does_not_answer_is_refused(led, fake):
    fake.board = None
    with pytest.raises(fl.FlashError, match="No ESP32"):
        plan(led)


def test_restore_only_goes_back_to_the_same_board(led, fake):
    fake.backups.mkdir(parents=True)
    good = f"esp32s2-{BOARD['mac']}-2026-11-09-full.bin"
    other = "esp32s2-aaaaaaaaaaaa-2026-11-09-full.bin"
    (fake.backups / good).write_bytes(b"x")
    (fake.backups / other).write_bytes(b"x")
    with pytest.raises(fl.FlashError, match="different board"):
        fl.restore(led, fake.backups, other, "COM8", True, HUMAN)
    with pytest.raises(fl.FlashError, match="wasn't found"):
        fl.restore(led, fake.backups, "../secret.bin", "COM8", True, HUMAN)
    assert fl.restore(led, fake.backups, good, "COM8", True, HUMAN)["outcome"] == "running"
    with pytest.raises(PermissionDenied):
        fl.restore(led, fake.backups, good, "COM8", True, Actor.agent("electrical"))


def test_no_agent_is_given_a_flashing_tool():
    from buildanything import agent_specs, headquarters

    assert not any("flash" in name for spec in agent_specs.SPECS.values() for name in spec.tools)
    assert "flashing" not in headquarters.__dict__
