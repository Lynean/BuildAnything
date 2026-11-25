"""Compatibility engine. Code decides. If a value is unknown, the answer is unknown."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .ledger import ProjectState

DEFAULT_DERATE = 0.20
AMBIENT_C = 40.0


@dataclass
class Param:
    id: str
    min: float | None
    typ: float | None
    max: float | None
    page: int | None
    label: str


def _is_replaced(parameter: dict[str, Any], latest: dict[str, str]) -> bool:
    """A limit read from a datasheet that has since been replaced can't be trusted."""
    current = latest.get(parameter["fields"]["component"])
    return current is not None and parameter["fields"].get("datasheet") != current


class Board:
    def __init__(self, state: ProjectState):
        self.components = {c["id"]: c for c in state.of_type("component") if c["status"] != "rejected"}
        self.nets = [n for n in state.of_type("net") if n["status"] not in ("rejected", "superseded")]
        self.params: dict[tuple[str, str], list[Param]] = {}
        self.pending: set[tuple[str, str]] = set()
        latest = {d["fields"]["component"]: d["id"] for d in state.of_type("datasheet")}
        for p in state.of_type("parameter"):
            f = p["fields"]
            if _is_replaced(p, latest):
                if p["status"] == "confirmed":
                    self.pending.add((f["component"], f["kind"]))
            elif p["status"] == "confirmed":
                self.params.setdefault((f["component"], f["kind"]), []).append(Param(p["id"], f.get("min"), f.get("typ"), f.get("max"), f.get("page"), f.get("label", "")))
            elif p["status"] == "proposed":
                self.pending.add((f["component"], f["kind"]))
        self.addresses: dict[str, list[str]] = {}
        for p in state.of_type("parameter", "confirmed"):
            if p["fields"]["kind"] == "i2c_address" and not _is_replaced(p, latest):
                self.addresses.setdefault(p["fields"]["component"], []).extend(p["fields"].get("addresses", []))

    def name(self, cid: str) -> str:
        c = self.components.get(cid)
        return c["fields"]["part_number"] if c else cid

    def param(self, cid: str, kind: str) -> Param | None:
        return (self.params.get((cid, kind)) or [None])[0]

    def highest(self, cid: str, kind: str, field: str) -> tuple[float | None, Param | None]:
        best: tuple[float | None, Param | None] = (None, None)
        for p in self.params.get((cid, kind), []):
            value = getattr(p, field)
            if value is not None and (best[0] is None or value > best[0]):
                best = (value, p)
        return best

    def unknown_reason(self, cid: str, kind: str) -> str:
        if (cid, kind) in self.pending:
            return f"{self.name(cid)}: limit awaiting your confirmation."
        return f"{self.name(cid)}: limit not in its datasheet record. Upload or extract the datasheet."


def _result(rule: str, status: str, message: str, components: list[str], *evidence: Param | None) -> dict[str, Any]:
    return {
        "rule": rule, "status": status, "message": message, "components": components,
        "evidence": [{"parameter": p.id, "page": p.page, "label": p.label} for p in evidence if p],
    }


def _members(net: dict, *roles: str) -> list[dict]:
    return [m for m in net["fields"]["members"] if m["role"] in roles]


def check_supply(board: Board) -> list[dict]:
    out = []
    for net in board.nets:
        volts = net["fields"].get("nominal_v")
        if net["fields"]["kind"] != "power" or volts is None:
            continue
        for member in _members(net, "power_in"):
            cid = member["component"]
            label = f"{board.name(cid)} {member['pin']} on {net['fields']['name']} ({volts:g} V)"
            ceiling = board.param(cid, "abs_max_supply_v")
            if ceiling and ceiling.max is not None and volts > ceiling.max:
                out.append(_result("Supply voltage", "fail", f"{label} exceeds the absolute maximum of {ceiling.max:g} V. This can destroy the part.", [cid], ceiling))
                continue
            op = board.param(cid, "op_supply_v") or board.param(cid, "reg_vin_v")
            if op is None and ceiling is None:
                out.append(_result("Supply voltage", "unknown", f"{label}: {board.unknown_reason(cid, 'abs_max_supply_v' if (cid, 'abs_max_supply_v') in board.pending else 'op_supply_v')}", [cid]))
            elif op is None:
                out.append(_result("Supply voltage", "warn", f"{label} is under the absolute maximum, but no operating range is confirmed.", [cid], ceiling))
            elif (op.max is not None and volts > op.max) or (op.min is not None and volts < op.min):
                out.append(_result("Supply voltage", "fail", f"{label} is outside the operating range of {op.min:g} to {op.max:g} V." if op.min is not None and op.max is not None else f"{label} is outside the operating range.", [cid], op))
            else:
                out.append(_result("Supply voltage", "pass", f"{label} is within its operating range.", [cid], op))
    return out


def check_logic(board: Board) -> list[dict]:
    out = []
    for net in board.nets:
        if net["fields"]["kind"] not in ("signal", "i2c"):
            continue
        volts = net["fields"].get("nominal_v")
        for member in _members(net, "input", "io"):
            cid = member["component"]
            label = f"{board.name(cid)} {member['pin']} on {net['fields']['name']}"
            limit = board.param(cid, "abs_max_input_v") or board.param(cid, "abs_max_supply_v")
            if volts is None:
                out.append(_result("Logic level", "unknown", f"{label}: the net has no voltage set.", [cid]))
            elif limit is None or limit.max is None:
                out.append(_result("Logic level", "unknown", f"{label}: {board.unknown_reason(cid, 'abs_max_input_v')}", [cid]))
            elif volts > limit.max:
                out.append(_result("Logic level", "fail", f"{label} would see {volts:g} V, above its {limit.max:g} V limit.", [cid], limit))
            else:
                out.append(_result("Logic level", "pass", f"{label} tolerates {volts:g} V.", [cid], limit))
    return out


def _load_ma(board: Board, net: dict) -> tuple[float, list[str], list[Param]]:
    """Worst-case load on a power net, using declared currents or datasheet values."""
    total, missing, used = 0.0, [], []
    for member in _members(net, "power_in"):
        if member.get("current_ma") is not None:
            total += float(member["current_ma"])
            continue
        value, source = board.highest(member["component"], "supply_current_ma", "max")
        if value is None:
            value, source = board.highest(member["component"], "supply_current_ma", "typ")
        if value is None:
            missing.append(board.name(member["component"]))
        else:
            total += value
            used.append(source)
    return total, missing, used


def check_current(board: Board, derate: float) -> list[dict]:
    out = []
    for net in board.nets:
        if net["fields"]["kind"] != "power":
            continue
        for source in _members(net, "power_out"):
            cid = source["component"]
            limit = board.param(cid, "reg_iout_ma")
            name = net["fields"]["name"]
            load, missing, used = _load_ma(board, net)
            if limit is None or limit.max is None:
                out.append(_result("Current budget", "unknown", f"{name}: {board.unknown_reason(cid, 'reg_iout_ma')}", [cid]))
            elif missing:
                out.append(_result("Current budget", "unknown", f"{name}: no current figure for {', '.join(missing)}.", [cid]))
            elif load > limit.max:
                out.append(_result("Current budget", "fail", f"{name} loads are {load:g} mA, above the {limit.max:g} mA source limit.", [cid], limit, *used))
            elif load > limit.max * (1 - derate):
                out.append(_result("Current budget", "warn", f"{name} loads are {load:g} mA, inside the {derate:.0%} margin on {limit.max:g} mA.", [cid], limit, *used))
            else:
                out.append(_result("Current budget", "pass", f"{name} loads are {load:g} mA of {limit.max:g} mA.", [cid], limit, *used))
    return out


def check_pin_current(board: Board) -> list[dict]:
    out = []
    for net in board.nets:
        for member in _members(net, "output", "io"):
            if member.get("current_ma") is None:
                continue
            cid = member["component"]
            limit = board.param(cid, "abs_max_pin_current_ma")
            label = f"{board.name(cid)} {member['pin']}"
            if limit is None or limit.max is None:
                out.append(_result("Pin current", "unknown", f"{label}: {board.unknown_reason(cid, 'abs_max_pin_current_ma')}", [cid]))
            elif float(member["current_ma"]) > limit.max:
                out.append(_result("Pin current", "fail", f"{label} would carry {member['current_ma']} mA, above {limit.max:g} mA.", [cid], limit))
            else:
                out.append(_result("Pin current", "pass", f"{label} is within its limit.", [cid], limit))
    return out


def check_regulators(board: Board) -> list[dict]:
    out = []
    regulators: dict[str, dict[str, dict]] = {}
    for net in board.nets:
        if net["fields"]["kind"] != "power":
            continue
        for member in net["fields"]["members"]:
            if member["role"] == "power_out" and board.param(member["component"], "reg_vout_v"):
                regulators.setdefault(member["component"], {})["out"] = net
    for net in board.nets:
        if net["fields"]["kind"] == "power":
            for member in _members(net, "power_in"):
                if member["component"] in regulators:
                    regulators[member["component"]]["in"] = net
    for cid, nets in regulators.items():
        if "in" not in nets:
            out.append(_result("Regulator", "unknown", f"{board.name(cid)} has no input net.", [cid]))
            continue
        vin, vout_param = nets["in"]["fields"].get("nominal_v"), board.param(cid, "reg_vout_v")
        vout = vout_param.typ if vout_param.typ is not None else vout_param.max
        if vin is None or vout is None:
            out.append(_result("Regulator", "unknown", f"{board.name(cid)}: input or output voltage isn't set.", [cid], vout_param))
            continue
        dropout = board.param(cid, "reg_dropout_v")
        headroom = vin - vout
        needed = None if dropout is None else (dropout.max if dropout.max is not None else dropout.typ)
        if needed is None:
            out.append(_result("Regulator dropout", "unknown", f"{board.name(cid)}: {board.unknown_reason(cid, 'reg_dropout_v')}", [cid]))
        elif headroom < needed:
            out.append(_result("Regulator dropout", "fail", f"{board.name(cid)} has {headroom:g} V of headroom but needs {needed:g} V.", [cid], dropout))
        else:
            out.append(_result("Regulator dropout", "pass", f"{board.name(cid)} has {headroom:g} V of headroom.", [cid], dropout))
        load, missing, _ = _load_ma(board, nets["out"])
        theta, tj = board.param(cid, "theta_ja_c_per_w"), board.param(cid, "tj_max_c")
        if missing or theta is None or tj is None or tj.max is None:
            reason = f"no current figure for {', '.join(missing)}" if missing else board.unknown_reason(cid, "theta_ja_c_per_w" if theta is None else "tj_max_c")
            out.append(_result("Regulator heat", "unknown", f"{board.name(cid)}: {reason}", [cid]))
            continue
        theta_value = theta.typ if theta.typ is not None else theta.max
        if theta_value is None:
            out.append(_result("Regulator heat", "unknown", f"{board.name(cid)}: thermal resistance has no value.", [cid], theta))
            continue
        watts = headroom * load / 1000
        junction = AMBIENT_C + watts * theta_value
        status = "fail" if junction > tj.max else "warn" if junction > tj.max - 20 else "pass"
        out.append(_result("Regulator heat", status, f"{board.name(cid)} dissipates {watts:.2f} W, about {junction:.0f} °C at the junction (limit {tj.max:g} °C).", [cid], theta, tj))
    return out


def check_battery_and_motors(board: Board) -> list[dict]:
    out = []
    for net in board.nets:
        if net["fields"]["kind"] != "power":
            continue
        for source in _members(net, "power_out"):
            cid = source["component"]
            capacity, c_rate = board.param(cid, "battery_capacity_mah"), board.param(cid, "battery_c_rate")
            if capacity is None and c_rate is None:
                continue
            load, missing, _ = _load_ma(board, net)
            cap_value = None if capacity is None else (capacity.typ if capacity.typ is not None else capacity.max)
            rate_value = None if c_rate is None else (c_rate.typ if c_rate.typ is not None else c_rate.max)
            if cap_value is None or rate_value is None or missing:
                out.append(_result("Battery C-rate", "unknown", f"{board.name(cid)}: capacity, C-rate or a load figure is missing.", [cid], capacity, c_rate))
            elif load > cap_value * rate_value:
                out.append(_result("Battery C-rate", "fail", f"{board.name(cid)} supplies {cap_value * rate_value:g} mA, but the load is {load:g} mA.", [cid], capacity, c_rate))
            else:
                out.append(_result("Battery C-rate", "pass", f"{board.name(cid)} covers the {load:g} mA load.", [cid], capacity, c_rate))
    for net in board.nets:
        motors = [m for m in net["fields"]["members"] if board.param(m["component"], "stall_current_ma")]
        drivers = [m for m in _members(net, "output", "io") if board.param(m["component"], "driver_peak_ma")]
        for motor in motors:
            stall = board.param(motor["component"], "stall_current_ma")
            for driver in drivers:
                peak = board.param(driver["component"], "driver_peak_ma")
                if stall.max is None or peak.max is None:
                    continue
                status = "fail" if stall.max > peak.max else "pass"
                out.append(_result("Motor stall", status, f"{board.name(motor['component'])} stalls at {stall.max:g} mA; {board.name(driver['component'])} handles {peak.max:g} mA.", [motor["component"], driver["component"]], stall, peak))
    return out


def check_temperature(board: Board, low: float, high: float) -> list[dict]:
    out = []
    for cid in board.components:
        p = board.param(cid, "op_temp_c")
        if p is None or p.min is None or p.max is None:
            out.append(_result("Temperature", "unknown", board.unknown_reason(cid, "op_temp_c"), [cid]))
        elif p.min > low or p.max < high:
            out.append(_result("Temperature", "fail", f"{board.name(cid)} is rated {p.min:g} to {p.max:g} °C, outside the project's {low:g} to {high:g} °C.", [cid], p))
        else:
            out.append(_result("Temperature", "pass", f"{board.name(cid)} covers {low:g} to {high:g} °C.", [cid], p))
    return out


def check_i2c(board: Board) -> list[dict]:
    out = []
    for net in board.nets:
        if net["fields"]["kind"] != "i2c" or "scl" in net["fields"]["name"].lower():
            continue
        seen: dict[str, list[str]] = {}
        unknown = []
        for member in _members(net, "input", "io"):
            cid = member["component"]
            if member.get("is_controller"):
                continue
            chosen = member.get("address")
            options = [chosen] if chosen else board.addresses.get(cid, [])
            if not options:
                unknown.append(cid)
            elif len(options) == 1:
                seen.setdefault(options[0].lower(), []).append(cid)
        name = net["fields"]["name"]
        for address, cids in seen.items():
            if len(cids) > 1:
                out.append(_result("I2C address", "fail", f"{name}: {', '.join(board.name(c) for c in cids)} share address {address}.", cids))
        if unknown:
            out.append(_result("I2C address", "unknown", f"{name}: no address is set for {', '.join(board.name(c) for c in unknown)}.", unknown))
        elif not any(len(c) > 1 for c in seen.values()):
            out.append(_result("I2C address", "pass", f"{name}: addresses are unique.", []))
        if not _members(net, "pullup"):
            out.append(_result("I2C pull-ups", "warn", f"{name} has no pull-up resistor recorded.", []))
    return out


def check_pins(board: Board) -> list[dict]:
    out, used = [], {}
    for net in board.nets:
        for member in net["fields"]["members"]:
            key = (member["component"], member["pin"])
            if key in used and used[key] != net["fields"]["name"]:
                out.append(_result("Pin conflict", "fail", f"{board.name(key[0])} {key[1]} is on both {used[key]} and {net['fields']['name']}.", [key[0]]))
            used[key] = net["fields"]["name"]
    return out


def check_ground(board: Board) -> list[dict]:
    grounds = [n for n in board.nets if n["fields"]["kind"] == "ground"]
    if not board.nets:
        return []
    if not grounds:
        return [_result("Common ground", "fail", "No ground net is defined.", [])]
    on_ground = {m["component"] for n in grounds for m in n["fields"]["members"]}
    talking = {m["component"] for n in board.nets if n["fields"]["kind"] in ("signal", "i2c") for m in n["fields"]["members"] if m["role"] != "pullup"}
    missing = sorted(talking - on_ground)
    if missing:
        return [_result("Common ground", "fail", f"Not connected to ground: {', '.join(board.name(c) for c in missing)}.", missing)]
    if len(grounds) > 1:
        return [_result("Common ground", "warn", "More than one ground net exists. Confirm they're joined where signals cross.", [])]
    return [_result("Common ground", "pass", "All communicating parts share a ground.", [])]


def run_checks(state: ProjectState, derate: float = DEFAULT_DERATE, temp_low: float = 0, temp_high: float = 40) -> list[dict]:
    board = Board(state)
    results = (
        check_supply(board) + check_logic(board) + check_current(board, derate) + check_pin_current(board) + check_regulators(board)
        + check_battery_and_motors(board) + check_temperature(board, temp_low, temp_high) + check_i2c(board) + check_pins(board) + check_ground(board)
    )
    order = {"fail": 0, "warn": 1, "unknown": 2, "pass": 3}
    return sorted(results, key=lambda r: order[r["status"]])


def power_tree(state: ProjectState) -> list[dict]:
    board = Board(state)
    tree = []
    for net in board.nets:
        if net["fields"]["kind"] != "power":
            continue
        tree.append({
            "net": net["fields"]["name"], "volts": net["fields"].get("nominal_v"),
            "sources": [board.name(m["component"]) for m in _members(net, "power_out")],
            "loads": [board.name(m["component"]) for m in _members(net, "power_in")],
        })
    return tree
