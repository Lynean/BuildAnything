"""Circuit diagrams drawn from the project's real components and connections.

Nothing here is stored as a drawing. Groups and layout are worked out from the connection list every time,
so a diagram can't disagree with the design. People and the Electrical Lead can move parts between sub-circuits.
"""
from __future__ import annotations

import re
from typing import Any

from . import compat, page_memory
from .ledger import Actor, Ledger, LedgerError, ProjectState

PART_WIDTH, COLUMN_START, PIN_GAP, PART_TOP, PART_GAP, TOP, TRACK_GAP, BASE_GAP, PORT_WIDTH, PORT_HEIGHT = 170, 110, 24, 36, 56, 44, 15, 96, 130, 34
CONTROLLER = re.compile(r"esp32|esp8266|stm32|atmega|rp2040|nrf5|mcu|microcontroller|controller|arduino", re.I)
POWER_ROLES = ("power_in", "power_out", "gnd")


class CircuitError(LedgerError):
    pass


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "circuit"


def _live_nets(state: ProjectState) -> list[dict[str, Any]]:
    return [n for n in state.of_type("net") if n["status"] not in ("rejected", "superseded")]


def _assignments(ledger: Ledger) -> tuple[dict[str, str], list[str]]:
    moved: dict[str, str] = {}
    added: list[str] = []
    for event in ledger.events():
        if event["kind"] == "circuit.assigned":
            moved[event["data"]["component"]] = event["data"]["group"]
        elif event["kind"] == "circuit.added" and event["data"]["name"] not in added:
            added.append(event["data"]["name"])
    return moved, added


def _auto_groups(components: list[dict[str, Any]], nets: list[dict[str, Any]], moved: dict[str, str]) -> dict[str, str]:
    """Parts joined by signals belong together. Shared power or ground alone doesn't connect them."""
    parent = {c["id"]: c["id"] for c in components}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for net in nets:
        if net["fields"]["kind"] in ("signal", "i2c"):
            members = [m["component"] for m in net["fields"]["members"] if m["component"] in parent and m["component"] not in moved]
            for other in members[1:]:
                parent[find(other)] = find(members[0])
    clusters: dict[str, list[dict[str, Any]]] = {}
    for component in components:
        if component["id"] not in moved:
            clusters.setdefault(find(component["id"]), []).append(component)
    supplies = {m["component"] for n in nets for m in n["fields"]["members"] if m["role"] == "power_out"}
    names: dict[str, str] = {}
    used: set[str] = set(moved.values())
    number = 1
    for cluster in sorted(clusters.values(), key=lambda c: -len(c)):
        text = " ".join(f"{c['fields']['part_number']} {c['fields'].get('role', '')}" for c in cluster)
        if CONTROLLER.search(text):
            name = "Controller"
        elif all(c["id"] in supplies for c in cluster):
            name = "Power"
        elif len(cluster) == 1:
            name = (cluster[0]["fields"].get("role") or cluster[0]["fields"]["part_number"]).strip()[:32].capitalize() or "Circuit"
        else:
            name = f"Circuit {number}"
            number += 1
        base, suffix = name, 2
        while name in used:
            name, suffix = f"{base} {suffix}", suffix + 1
        used.add(name)
        for component in cluster:
            names[component["id"]] = name
    return names


def _flagged(state: ProjectState) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    numbers = {c["fields"]["part_number"]: c["id"] for c in state.of_type("component")}
    for result in compat.run_checks(state):
        if result["status"] in ("fail", "warn"):
            for name in result["components"]:
                out.setdefault(numbers.get(name, name), []).append(result["message"])
    return out


def _columns(parts: list[str], links: dict[str, set[str]]) -> dict[str, int]:
    """Two columns facing a shared channel, so a wire never has to cross a part."""
    column: dict[str, int] = {}
    for start in sorted(parts, key=lambda p: -len(links[p])):
        if start in column:
            continue
        column[start] = 0
        queue = [start]
        while queue:
            current = queue.pop(0)
            for other in sorted(links[current]):
                if other not in column:
                    column[other] = 1 - column[current]
                    queue.append(other)
    if len(parts) > 1 and len(set(column.values())) == 1:  # Unconnected parts: use both columns anyway.
        for index, part in enumerate(sorted(parts)):
            column[part] = index % 2
    return column


def _layout(group: str, members: list[dict[str, Any]], nets: list[dict[str, Any]], where: dict[str, str], refs: dict[str, str], sheets: dict[str, Any], flags: dict[str, list[str]], tabs: dict[str, str]) -> dict[str, Any]:
    ids = {c["id"] for c in members}
    links: dict[str, set[str]] = {c["id"]: set() for c in members}
    pins: dict[str, list[dict[str, Any]]] = {c["id"]: [] for c in members}
    wires, ports, connects = [], [], set()
    for net in nets:
        f = net["fields"]
        inside = [m for m in f["members"] if m["component"] in ids]
        if not inside:
            continue
        others = sorted({where[m["component"]] for m in f["members"] if m["component"] in where and where[m["component"]] != group})
        flag_only = f["kind"] in ("power", "ground")
        if f["kind"] == "power":
            connects |= set(others)
        for m in inside:
            pins[m["component"]].append({"name": m["pin"], "net": f["name"], "role": m["role"], "flag": f["name"] if flag_only else None, "draft": net["status"] != "confirmed"})
        if flag_only:
            continue
        names = sorted({m["component"] for m in inside})
        for a in names:
            links[a] |= {b for b in names if b != a}
        connects |= set(others)
        wires.append({"net": f["name"], "kind": f["kind"], "volts": f.get("nominal_v"), "draft": net["status"] != "confirmed", "members": [(m["component"], m["pin"]) for m in inside], "ports": others})
    column = _columns([c["id"] for c in members], links)
    heights, boxes = [TOP, TOP], {}
    ordered = sorted(members, key=lambda c: (column[c["id"]], refs[c["id"]]))
    for track_side in (0, 1):
        for c in ordered:
            if column[c["id"]] != track_side:
                continue
            count = max(1, len(pins[c["id"]]))
            height = PART_TOP + count * PIN_GAP
            boxes[c["id"]] = {"y": heights[track_side], "h": height}
            heights[track_side] += height + PART_GAP
    for wire in wires:
        for target in wire["ports"]:
            ports.append({"id": f"{wire['net']}>{target}", "net": wire["net"], "label": wire["net"], "to": tabs[target], "toName": target, "h": PORT_HEIGHT})
    # Ports sit under the right-hand column, in the same channel as every other wire.
    for port in ports:
        port["y"] = heights[1]
        heights[1] += PORT_HEIGHT + 22
    tracks = len(wires)
    gap = BASE_GAP + tracks * TRACK_GAP
    left_x, right_x = COLUMN_START, COLUMN_START + PART_WIDTH + gap
    parts, position = [], {}
    for c in ordered:
        side = column[c["id"]]
        x = left_x if side == 0 else right_x
        box = boxes[c["id"]]
        drawn = []
        for index, pin in enumerate(sorted(pins[c["id"]], key=lambda p: (p["flag"] is not None, p["name"]))):
            # Power flags point away from the channel, signals face it.
            facing_right = (side == 0) == (pin["flag"] is None)
            y = box["y"] + PART_TOP + index * PIN_GAP + 6
            drawn.append({**pin, "side": "right" if facing_right else "left", "y": y})
            position[(c["id"], pin["name"])] = (x + PART_WIDTH + 16 if facing_right else x - 16, y)
        sheet = sheets.get(c["id"])
        parts.append({"id": c["id"], "ref": refs[c["id"]], "label": (c["fields"].get("role") or c["fields"]["part_number"])[:24], "part": c["fields"]["part_number"], "role": c["fields"].get("role", ""), "status": c["status"], "datasheet": "Uploaded" if sheet else "Missing", "x": x, "y": box["y"], "w": PART_WIDTH, "h": box["h"], "pins": drawn, "flagged": bool(flags.get(c["id"])), "problems": flags.get(c["id"], [])})
    drawn_wires = []
    channel_left = left_x + PART_WIDTH + 34
    for track, wire in enumerate(sorted(wires, key=lambda w: w["net"])):
        trunk = channel_left + track * TRACK_GAP
        points = [position[(a, b)] for a, b in wire["members"] if (a, b) in position]
        for port in ports:
            if port["net"] == wire["net"]:
                port["x"] = right_x
                points.append((right_x, port["y"] + PORT_HEIGHT // 2))
        segments = [f"M{x} {y} H{trunk}" for x, y in points]
        if len(points) > 1:
            segments.append(f"M{trunk} {min(y for _, y in points)} V{max(y for _, y in points)}")
        drawn_wires.append({"net": wire["net"], "kind": wire["kind"], "draft": wire["draft"], "volts": wire["volts"], "trunk": trunk, "path": " ".join(segments), "labelY": min((y for _, y in points), default=TOP) - 5})
    return {"id": tabs[group], "name": group, "parts": parts, "wires": drawn_wires, "ports": ports, "connects": [{"id": tabs[n], "name": n} for n in sorted(connects)], "width": right_x + PART_WIDTH + 110 if members or ports else 600, "height": max(heights) + 20}


def build(ledger: Ledger) -> dict[str, Any]:
    state = ledger.state()
    components = sorted((c for c in state.of_type("component") if c["status"] != "rejected"), key=lambda c: c["id"])
    used = {m["component"] for n in _live_nets(state) for m in n["fields"]["members"]}
    placed = [c for c in components if c["id"] in used]
    nets = _live_nets(state)
    moved, added = _assignments(ledger)
    moved = {k: v for k, v in moved.items() if k in {c["id"] for c in components}}
    where = {**_auto_groups(placed, nets, moved), **{k: v for k, v in moved.items() if k in used}}
    names = list(dict.fromkeys([*sorted(set(where.values())), *added]))
    tabs = {name: _slug(name) for name in names}
    refs = {c["id"]: f"U{i + 1}" for i, c in enumerate(components)}
    sheets = page_memory.latest_sheets(state)
    flags = _flagged(state)
    groups = [_layout(name, [c for c in placed if where.get(c["id"]) == name], nets, where, refs, sheets, flags, tabs) for name in names]
    groups.sort(key=lambda g: (g["name"] != "Power", g["name"] != "Controller", g["name"]))
    return {"subCircuits": groups, "unconnected": [{"id": c["id"], "part": c["fields"]["part_number"]} for c in components if c["id"] not in used]}


def _check_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 32:
        raise CircuitError("Give the sub-circuit a name of up to 32 characters.")
    return name


def add_group(ledger: Ledger, name: str, actor: Actor) -> None:
    ledger.append(actor, "circuit.added", name=_check_name(name))


def assign(ledger: Ledger, component: str, group: str, actor: Actor) -> None:
    """Only changes how the diagram is grouped, never the design itself."""
    record = ledger.state().records.get(component)
    if not record or record["type"] != "component":
        raise CircuitError("Component not found.")
    ledger.append(actor, "circuit.assigned", component=component, group=_check_name(group))
