import re

import pytest

from buildanything import circuits as cc
from buildanything.ledger import Actor, Ledger

HUMAN = Actor.human()


@pytest.fixture
def led(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.append(HUMAN, "project.created", name="T", aim="A", scope="S")
    return ledger


def part(led, number, role):
    record = led.propose(HUMAN, "component", part_number=number, role=role)
    led.append(HUMAN, "record.confirmed", id=record)
    return record


def net(led, name, kind, volts, *members, confirmed=True):
    record = led.propose(HUMAN, "net", name=name, kind=kind, nominal_v=volts, members=[{"component": c, "pin": p, "role": r} for c, p, r in members])
    if confirmed:
        led.append(HUMAN, "record.confirmed", id=record)
    return record


@pytest.fixture
def design(led):
    reg, mcu, imu, drv = part(led, "AMS1117", "3.3 V regulator"), part(led, "ESP32-S3", "Microcontroller"), part(led, "MPU6050", "IMU"), part(led, "DRV8833", "Motor driver")
    net(led, "3V3", "power", 3.3, (reg, "VOUT", "power_out"), (mcu, "3V3", "power_in"), (imu, "VCC", "power_in"), (drv, "VCC", "power_in"))
    net(led, "GND", "ground", None, (reg, "GND", "gnd"), (mcu, "GND", "gnd"), (imu, "GND", "gnd"), (drv, "GND", "gnd"))
    net(led, "SDA", "i2c", 3.3, (mcu, "SDA", "io"), (imu, "SDA", "io"))
    net(led, "SCL", "i2c", 3.3, (mcu, "SCL", "output"), (imu, "SCL", "input"))
    net(led, "AIN1", "signal", 3.3, (mcu, "IO4", "output"), (drv, "AIN1", "input"), confirmed=False)
    return {"reg": reg, "mcu": mcu, "imu": imu, "drv": drv}


def by_name(view):
    return {g["name"]: g for g in view["subCircuits"]}


def test_groups_come_from_signals_not_shared_power(led, design):
    groups = by_name(cc.build(led))
    assert set(groups) == {"Power", "Controller"}
    assert {p["part"] for p in groups["Power"]["parts"]} == {"AMS1117"}
    assert {p["part"] for p in groups["Controller"]["parts"]} == {"ESP32-S3", "MPU6050", "DRV8833"}
    assert [g["name"] for g in cc.build(led)["subCircuits"]] == ["Power", "Controller"]


def test_power_shows_as_labels_and_signals_as_wires(led, design):
    controller = by_name(cc.build(led))["Controller"]
    assert {w["net"] for w in controller["wires"]} == {"SDA", "SCL", "AIN1"}
    mcu = next(p for p in controller["parts"] if p["part"] == "ESP32-S3")
    assert {p["flag"] for p in mcu["pins"] if p["flag"]} == {"3V3", "GND"}


def test_unconfirmed_connections_are_marked(led, design):
    wires = {w["net"]: w for w in by_name(cc.build(led))["Controller"]["wires"]}
    assert wires["AIN1"]["draft"] and not wires["SDA"]["draft"]


def test_rejected_connections_disappear(led, design):
    state = led.state()
    record = next(n["id"] for n in state.of_type("net") if n["fields"]["name"] == "AIN1")
    led.append(HUMAN, "record.rejected", id=record)
    assert "AIN1" not in {w["net"] for w in by_name(cc.build(led))["Controller"]["wires"]}


def test_every_wire_end_touches_a_pin_and_parts_do_not_overlap(led, design):
    for group in cc.build(led)["subCircuits"]:
        boxes = group["parts"]
        for a in boxes:
            for b in boxes:
                if a is not b and a["x"] == b["x"]:
                    assert a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"]
        ends = {(p["x"] + p["w"] + 16 if pin["side"] == "right" else p["x"] - 16, pin["y"]) for p in boxes for pin in p["pins"]} | {(q["x"], q["y"] + q["h"] // 2) for q in group["ports"]}
        for wire in group["wires"]:
            for x, y in re.findall(r"M(\d+) (\d+) H", wire["path"]):
                assert (int(x), int(y)) in ends
        assert group["width"] > max(p["x"] + p["w"] for p in boxes)


def test_moving_a_part_makes_ports_between_sub_circuits(led, design):
    cc.assign(led, design["imu"], "Sensors", HUMAN)
    groups = by_name(cc.build(led))
    assert {p["part"] for p in groups["Sensors"]["parts"]} == {"MPU6050"}
    controller = groups["Controller"]
    assert {p["net"] for p in controller["ports"]} == {"SDA", "SCL"} and controller["ports"][0]["to"] == "sensors"
    assert {c["name"] for c in controller["connects"]} >= {"Sensors"}
    assert {w["net"] for w in groups["Sensors"]["wires"]} == {"SDA", "SCL"}


def test_power_group_lists_who_it_feeds(led, design):
    assert {c["name"] for c in by_name(cc.build(led))["Power"]["connects"]} == {"Controller"}


def test_empty_sub_circuits_can_be_added_on_request(led, design):
    cc.add_group(led, "Radio", HUMAN)
    radio = by_name(cc.build(led))["Radio"]
    assert radio["parts"] == [] and radio["wires"] == []


def test_unconnected_components_are_listed(led, design):
    part(led, "BME280", "Pressure sensor")
    assert cc.build(led)["unconnected"][0]["part"] == "BME280"


def test_no_connections_is_empty(led):
    part(led, "X", "y")
    view = cc.build(led)
    assert view["subCircuits"] == [] and len(view["unconnected"]) == 1


@pytest.mark.parametrize("name", ["", "x" * 33])
def test_names_are_checked(led, design, name):
    with pytest.raises(cc.CircuitError):
        cc.add_group(led, name, HUMAN)


def test_a_compatibility_failure_marks_the_part(led, design, monkeypatch):
    monkeypatch.setattr(cc.compat, "run_checks", lambda state: [{"rule": "r", "status": "fail", "message": "Too much current", "components": ["DRV8833"], "evidence": []}])
    drv = next(p for p in by_name(cc.build(led))["Controller"]["parts"] if p["part"] == "DRV8833")
    assert drv["flagged"] and drv["problems"] == ["Too much current"]
