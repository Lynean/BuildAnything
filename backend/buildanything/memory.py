"""The project's memory as a graph: what it knows and how each thing depends on the others.

Worked out from the record each time, like the dependency graph it extends; nothing here is stored. It joins what
the departments keep separately: requirements and the tests that prove them, what the design needs and the parts
chosen for it, the connections between parts, the code that uses them, the messages devices exchange, decisions and
sourced facts. A change to any item can be followed to everything that depends on it.
"""
from __future__ import annotations

import re
from typing import Any

from . import depgraph, designneeds, traceability
from .ledger import Ledger

MAX_BLOCKS = 120
KEY = re.compile(r"\bR\d{1,3}\b")


def graph(ledger: Ledger) -> dict[str, Any]:
    state = ledger.state()
    g = depgraph.build(ledger)
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []

    def node(node_id: str, kind: str, label: str, detail: str = "") -> None:
        nodes.setdefault(node_id, {"id": node_id, "kind": kind, "label": label[:80], "detail": detail[:600]})

    def edge(a: str, b: str, kind: str) -> None:
        """a depends on b: change b, and a may need another look."""
        if a in nodes and b in nodes and a != b:
            edges.append({"from": a, "to": b, "kind": kind})

    for req in state.of_type("requirement", "confirmed"):
        key = req["fields"].get("key", req["id"])
        node(f"requirement:{key}", "requirement", key, req["fields"].get("text", ""))
    trace = traceability.view(ledger)
    for test in trace["tests"]:
        node(f"test:{test['key']}", "test", f"{test['key']}: {test.get('title', '')}", f"{test.get('pass_if', '')} ({test.get('state', '')})")
        for key in test.get("covers", []):
            edge(f"test:{test['key']}", f"requirement:{key}", "proves")
    for need in designneeds.view(ledger):
        node(f"need:{need['id']}", "need", need["item"], f"{need.get('why', '')} Status: {need['status']}.")
        for key in KEY.findall(need.get("why", "")):
            edge(f"need:{need['id']}", f"requirement:{key}", "serves")
    for n in g.fingerprints:
        kind, name = n.split(":", 1)
        if kind in ("part", "component", "net", "packet", "device"):
            node(n, kind, g.labels[n])
    blocks = [n for n in g.fingerprints if n.startswith("block:")][:MAX_BLOCKS]
    for n in blocks:
        node(n, "code", g.labels[n].split("/")[-1])
    for need in designneeds.view(ledger):
        for part in need.get("parts", []):
            edge(f"component:{part}", f"need:{need['id']}", "fills")
    for component, nets in g.component_nets.items():
        for net in nets:
            edge(net, f"component:{component}", "connects")  # a connection depends on the parts it joins
    for component, used in g.component_blocks.items():
        for block in used:
            edge(block, f"component:{component}", "uses")
    for device, used in g.device_blocks.items():
        for block in used:
            edge(block, f"device:{device}", "runs on")
    for block, packets in g.block_packets.items():
        for packet in packets:
            edge(block, packet, "sends")
    for test in trace["tests"]:
        for used in test.get("uses", []) or []:
            target = g.resolve(used)
            if target:
                edge(f"test:{test['key']}", target, "checks")
    for decision in state.of_type("decision", "confirmed"):
        node(f"decision:{decision['id']}", "decision", decision["fields"].get("text", "")[:70], decision["fields"].get("text", ""))
    for claim in state.of_type("claim", "confirmed"):
        node(f"claim:{claim['id']}", "fact", claim["fields"].get("statement", "")[:70], f"{claim['fields'].get('statement', '')} Source: {claim['fields'].get('source_url') or claim['fields'].get('paper_id', '')}")
    for plan in state.of_type("assembly_plan"):
        if plan["status"] in ("rejected", "superseded"):
            continue
        for name, parts in (plan["fields"].get("assemblies") or {}).items():
            node(f"assembly:{name}", "assembly", name)
            for number in parts:
                edge(f"assembly:{name}", f"part:{number}", "contains")  # an assembly depends on its parts
    counts: dict[str, int] = {}
    for n in nodes.values():
        counts[n["kind"]] = counts.get(n["kind"], 0) + 1
    return {"nodes": list(nodes.values()), "edges": edges, "counts": counts, "truncated": len([n for n in g.fingerprints if n.startswith("block:")]) > MAX_BLOCKS}
