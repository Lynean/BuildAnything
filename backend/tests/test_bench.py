"""The laptop bench's own rules, with a tiny hand-written firmware, fake and scenarios. No AI involved."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from buildanything import bench, ci

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="needs g++")

FIRMWARE = r'''
#include <Arduino.h>
#include <SPI.h>
#include "ba_pins.h"
volatile int presses = 0;
void on_press() { presses++; }
void setup() {
  Serial.begin(115200);
  digitalWrite(BA_PIN_LIGHT, HIGH);   // a mistake: written before pinMode
  pinMode(BA_PIN_LIGHT, OUTPUT);
  pinMode(BA_PIN_BUTTON, INPUT);
  attachInterrupt(digitalPinToInterrupt(BA_PIN_BUTTON), on_press, FALLING);
  SPI.begin();
}
void loop() {
  static int seen = 0;
  if (presses != seen) { seen = presses; Serial.println("press"); }
  if (Serial.available()) { int c = Serial.read(); if (c == 'x') { SPI.beginTransaction(SPISettings(SLOW_HZ, MSBFIRST, SPI_MODE0)); for (int i = 0; i < 1000; ++i) SPI.transfer(0xAA); SPI.endTransaction(); Serial.println("sent"); } }
}
'''

FAKES = r'''
#pragma once
#include "bench.h"
#include "ba_pins.h"
class FakeButton : public bench::Device {
 public:
  FakeButton() : Device("Button") {}
  void press() { down_ = true; changed(BA_PIN_BUTTON); }
  void release() { down_ = false; changed(BA_PIN_BUTTON); }
  int drive(int pin) override { return pin == BA_PIN_BUTTON ? (down_ ? 0 : 1) : -1; }
 private:
  bool down_ = false;
};
inline FakeButton button;
class FakeLight : public bench::Device {
 public:
  FakeLight() : Device("Light") {}
  bool on() const { return bench::level(BA_PIN_LIGHT) == 1; }
};
inline FakeLight light;
class FakeBus : public bench::Device {
 public:
  FakeBus() : Device("Bus") {}
  uint8_t on_spi(uint8_t out, const bench::SpiState& s) override { if (s.hz > 10000000) problem("SPI at %u Hz, above 10 MHz", (unsigned)s.hz); ++bytes; return 0xFF; }
  long bytes = 0;
};
inline FakeBus bus;
'''

SCENARIOS = r'''
#include "fakes.h"
#include <string>
BENCH_SCENARIO("The light is on after start-up", "R1") {
  bench::run_ms(10);
  bench::check(light.on(), "light on");
}
BENCH_SCENARIO("A press is reported within 5 ms", "R2") {
  bench::run_ms(10);
  size_t before = bench::serial_output().size();
  button.press();
  double ms = bench::run_until([&] { return bench::serial_output().find("press", before) != std::string::npos; }, 50);
  bench::measure("press to report", ms, "ms");
  bench::check(ms >= 0 && ms <= 5, "reported after %.2f ms", ms);
}
BENCH_SCENARIO("1000 SPI bytes take the time the clock says", "R3") {
  bench::run_ms(10);
  bench::serial_send("x");
  double t0 = bench::now_ms();
  double ms = bench::run_until([&] { return bench::serial_output().find("sent") != std::string::npos; }, 1000);
  bench::measure("send time", ms, "ms");
  bench::check(bus.bytes == 1000, "bytes %ld", bus.bytes);
  bench::check(ms > 1.5, "1000 bytes at 5.25 MHz took %.2f ms", ms);
  (void)t0;
}
'''


def _project(tmp_path: Path, slow_hz: int) -> Path:
    base = tmp_path / "firmware" / "board"
    (base / "src").mkdir(parents=True)
    (base / "include").mkdir()
    (base / "platformio.ini").write_text("[env:device]\nplatform = ststm32\nboard = nucleo_f401re\nframework = arduino\n")
    (base / "include" / "ba_pins.h").write_text("#define BA_PIN_LIGHT PB10\n#define BA_PIN_BUTTON PC13\n")
    (base / "src" / "main.cpp").write_text(FIRMWARE.replace("SLOW_HZ", str(slow_hz)))
    folder = bench.bench_dir(tmp_path, "board")
    (folder / "fakes").mkdir(parents=True)
    (folder / "fakes" / "parts.h").write_text(FAKES)
    (folder / "scenarios.cpp").write_text(SCENARIOS)
    return tmp_path


def test_bench_runs_real_firmware_against_fakes(tmp_path: Path) -> None:
    root = _project(tmp_path, 6000000)
    result = bench.run(root, "board")
    assert result["ran"] and result["stage"] == "run", result
    by = {s["covers"]: s for s in result["scenarios"]}
    # A write before pinMode is lost on STM32, so the light stays off, and the bench says why.
    assert not by["R1"]["passed"]
    assert any("written before pinMode" in p["text"] for p in by["R1"]["problems"])
    # Interrupts fire on the fake's press, and the report comes back over the laptop link.
    assert by["R2"]["checks"][-1]["ok"], by["R2"]
    # 6 MHz asked becomes 5.25 MHz on the wire (84 MHz / 16), and 1000 bytes take at least 8000 bits of time.
    send = next(m for m in by["R3"]["measures"] if m["name"] == "send time")["value"]
    assert send > 1.5 and all(c["ok"] for c in by["R3"]["checks"]), by["R3"]


def test_a_fake_reports_a_broken_rule_and_ci_routes_it(tmp_path: Path) -> None:
    root = _project(tmp_path, 20000000)
    result = bench.run(root, "board")
    r3 = next(s for s in result["scenarios"] if s["covers"] == "R3")
    assert any("above 10 MHz" in p["text"] for p in r3["problems"])

    class FakeLedger:
        def __init__(self, root: Path) -> None:
            self.root = root
    ok, problems, owners, ran = ci.bench_verdict(FakeLedger(root), "board", result)  # type: ignore[arg-type]
    assert ran and not ok
    assert any("above 10 MHz" in p for p in problems)
    # No driver layer in this firmware, so Software owns every part of it.
    assert set(owners) == {"Software"}


def test_firmware_that_hangs_is_reported_not_waited_on_forever(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path, 6000000)
    main = root / "firmware" / "board" / "src" / "main.cpp"
    main.write_text(main.read_text().replace("static int seen = 0;", "static int seen = 0; volatile int spin = 1; while (spin) {}"))
    monkeypatch.setattr(bench, "WALL_SECONDS", 5)
    result = bench.run(root, "board")
    first = result["scenarios"][0]
    assert "stopped responding" in first["error"] and not first["passed"]


def test_bench_failures_send_no_work_until_a_person_accepts_the_bench(tmp_path: Path) -> None:
    from buildanything.ledger import Actor, Ledger

    root = _project(tmp_path, 20000000)
    led = Ledger(root)
    led.append(Actor.human(), "project.created", name="Bench", aim="", scope="")
    result = bench.run(root, "board")
    ci.record(led, "Bench:board", bench.fingerprint(root, "board"), *ci.bench_verdict(led, "board", result))
    assert not any(stage.startswith("Bench") for _owner, stage in [(o, t.split(" ")[0]) for o, t in ci.tool_problems(led)])
    led.append(Actor.human(), "bench.accepted", device="board", version=bench.version(root, "board"))
    assert any("failed on the laptop" in text for _owner, text in ci.tool_problems(led))
    # Changing a fake makes it a new bench, which needs looking at again.
    fake = bench.bench_dir(root, "board") / "fakes" / "parts.h"
    fake.write_text(fake.read_text() + "\n// changed\n")
    assert not ci.bench_accepted(led, "board")


def test_a_person_can_turn_the_fake_screen_to_match_the_real_one(tmp_path: Path) -> None:
    root = _project(tmp_path, 6000000)
    bench.set_screen(root, "board", "rotate_180")
    assert "#define BENCH_SCREEN_TURN 1" in bench.board_header(root / "firmware" / "board")
    with pytest.raises(ValueError):
        bench.set_screen(root, "board", "sideways")
