"""What depends on what, worked out from the project's real data each time. Nothing here is stored.

Every item gets a fingerprint. A test remembers the fingerprints from when it passed, so a result expires
when anything it relied on changes, however the change was made.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import memo, firmware, mechanical as mech
from .ledger import Ledger

DEPARTMENT_NODES = {"Mechanical": ("part",), "Electrical": ("component", "net", "board_block"), "Software": ("block", "packet"), "Procurement": ()}
C_PACKET = re.compile(r"\bba_([a-z0-9_]+?)_(?:t|encode|decode)\b")


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


@dataclass
class Graph:
    fingerprints: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    component_nets: dict[str, set[str]] = field(default_factory=dict)
    component_blocks: dict[str, set[str]] = field(default_factory=dict)
    device_blocks: dict[str, set[str]] = field(default_factory=dict)
    block_packets: dict[str, set[str]] = field(default_factory=dict)
    packet_blocks: dict[str, set[str]] = field(default_factory=dict)
    board_blocks: set[str] = field(default_factory=set)

    def add(self, node: str, label: str, fingerprint: Any) -> None:
        self.fingerprints[node] = _hash(fingerprint)
        self.labels[node] = label

    def resolve(self, name: str) -> str | None:
        """Accepts a plain name, such as SG90, or a full node ID."""
        if name in self.fingerprints:
            return name
        for prefix in ("part", "component", "packet", "device", "net"):
            if f"{prefix}:{name}" in self.fingerprints:
                return f"{prefix}:{name}"
        return None

    def expand(self, node: str) -> set[str]:
        """One step only, so a test on one sensor doesn't end up depending on the whole robot."""
        out = {node}
        kind, name = node.split(":", 1)
        if kind == "component":
            out |= self.component_nets.get(name, set()) | self.component_blocks.get(name, set())
        elif kind == "device":
            blocks = self.device_blocks.get(name, set())
            out |= blocks
            for block in blocks:
                out |= self.block_packets.get(block, set())
        elif kind == "packet":
            out |= self.packet_blocks.get(name, set())
        elif kind == "block":
            out |= self.block_packets.get(name, set())
        return out

    def closure(self, uses: list[str], covers: list[str], owner: str) -> set[str]:
        nodes = {f"requirement:{key}" for key in covers if f"requirement:{key}" in self.fingerprints}
        resolved = [self.resolve(u) for u in uses]
        if any(resolved):
            for node in resolved:
                if node:
                    nodes |= self.expand(node)
            return nodes
        # Nothing listed: assume it relies on the whole department, so a forgotten link can only cause an extra test.
        for kind in DEPARTMENT_NODES.get(owner, ()):
            nodes |= self.board_blocks if kind == "board_block" else {n for n in self.fingerprints if n.startswith(f"{kind}:")}
        return nodes

    def snapshot(self, nodes: set[str]) -> dict[str, str]:
        return {n: self.fingerprints[n] for n in nodes}

    def dependents(self, name: str) -> dict[str, list[str]]:
        node = self.resolve(name)
        if node is None:
            return {"connections": [], "code": []}
        items = self.expand(node) - {node}
        return {"connections": sorted(self.labels[n] for n in items if n.startswith("net:")), "code": sorted(self.labels[n] for n in items if n.startswith("block:"))}


def build(ledger: Ledger) -> Graph:
    return memo.remembered(ledger, "depgraph.build", lambda: _build(ledger))


def _build(ledger: Ledger) -> Graph:
    state = ledger.state()
    graph = Graph()
    for number, revisions in mech.parts_by_number(state).items():
        graph.add(f"part:{number}", f"{number}: {revisions[-1]['fields']['name']}", revisions[-1]["id"])
    names = {}
    for component in state.of_type("component"):
        if component["status"] == "rejected":
            continue
        number = component["fields"]["part_number"]
        names[component["id"]] = number
        graph.add(f"component:{number}", number, component["id"])
    for req in state.of_type("requirement", "confirmed"):
        key = req["fields"].get("key", req["id"])
        graph.add(f"requirement:{key}", key, req["fields"]["text"])
    for net in state.of_type("net"):
        if net["status"] in ("rejected", "superseded"):
            continue
        f = net["fields"]
        node = f"net:{f['name']}"
        graph.add(node, f"Connection {f['name']}", [f["kind"], f.get("nominal_v"), sorted((m["component"], m["pin"], m["role"]) for m in f["members"])])
        for member in f["members"]:
            if member["component"] in names:
                graph.component_nets.setdefault(names[member["component"]], set()).add(node)
    packets = {}
    for name, entry in state.packets.items():
        graph.add(f"packet:{name}", name, [entry["major"]])
        packets[name.lower()] = name
    classes = {re.sub(r"_", "", n): n for n in packets}
    note_owner = {n["id"]: names.get(n["fields"]["component"]) for n in state.of_type("page_note")}
    known = {n.lower(): n for n in names.values()}
    for device in firmware.devices(ledger.root):
        graph.add(f"device:{device}", device, device)
        board = not firmware.is_computer(ledger.root, device)
        for file in firmware.scan_workspace(ledger.root, device):
            if file.get("generated"):
                continue
            blocks = file["blocks"] or [{"title": "whole file", "code": file["plain"] or "", "based_on": [], "components": []}]
            for block in blocks:
                node = f"block:{device}/{file['path']}#{block.get('title')}"
                graph.add(node, f"{device}/{file['path']}: {block.get('title')}", firmware.code_hash(block["code"]))
                graph.device_blocks.setdefault(device, set()).add(node)
                if board:
                    graph.board_blocks.add(node)
                used = {known[c.lower()] for c in block.get("components", []) if c.lower() in known}
                used |= {note_owner[b[5:]] for b in block.get("based_on", []) if b.startswith("note:") and note_owner.get(b[5:])}
                for component in used:
                    graph.component_blocks.setdefault(component, set()).add(node)
                text = block["code"]
                found = {packets[m] for m in C_PACKET.findall(text) if m in packets} | {classes[c.lower()] for c in re.findall(r"\b([A-Z][A-Za-z0-9]+)\b", text) if c.lower() in classes}
                for packet in found:
                    graph.block_packets.setdefault(node, set()).add(f"packet:{packet}")
                    graph.packet_blocks.setdefault(packet, set()).add(node)
    return graph


def options(graph: Graph) -> list[dict[str, str]]:
    order = {"part": 0, "component": 1, "device": 2, "packet": 3, "net": 4, "block": 5}
    nodes = [n for n in graph.fingerprints if n.split(":", 1)[0] in order]
    return [{"id": n, "label": graph.labels[n], "kind": n.split(":", 1)[0]} for n in sorted(nodes, key=lambda n: (order[n.split(":", 1)[0]], n))]
