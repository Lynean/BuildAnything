"""Scores a finished run from the project's own records. Run by student-run.mjs."""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "backend"))
from buildanything import designneeds as dn, firmware as fw, fusion as fu, gatereport as gr, gates, headquarters as hq, interfaces as itf, mechanical as mech, traceability as tr  # noqa: E402
from buildanything.ledger import Ledger  # noqa: E402

root = pathlib.Path(sys.argv[1])
notes = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
led = Ledger(root)
state = led.state()
events = led.events()
needs = dn.view(led)
parts = mech.current_parts(state)
printable = [p for p in parts if not dn.is_purchased(p)]
modelled = gr.modelled_parts(led)
confirmed = state.of_type("component", "confirmed")
nets = state.of_type("net", "confirmed")
changes = list(fu.changes(led).values())
sources = state.of_type("source")
later = [m for d in hq.DEPARTMENTS for m in hq.thread(events, d) if m["role"] == "agent" and not m["early"]]
stl = list((root / "print").glob("*.stl")) if (root / "print").exists() else []
latest = next((e["data"]["after"] for e in reversed(events) if e["kind"] in ("cad.applied", "cad.kept") and e["data"].get("after")), {"bodies": [], "massG": 0})
volume = sum(b["volumeCm3"] for b in latest["bodies"])
served = state.meta.get("aim", "") and " ".join(s["fields"]["title"] for s in sources)
relevant = [s for s in sources if re.search(r"arm|manipulat|servo|esp32|robot|torque|gripper|pwm", s["fields"]["title"], re.I)]
gate_order = ["G0", "G1", "G2", "G3", "G4", "G5", "G6"]
current = gates.current_gate(state)
reach = gate_order.index(current) if current else 7
serial = notes.get("serial") or {}
rehearsal = notes.get("rehearsal") or {}
assemblies = dn.assembly_plan(state)
joints = [a for a in assemblies if re.search(r"joint|base|shoulder|elbow|wrist|yaw|pitch|roll", a, re.I)]
boards = [d for d in fw.devices(root) if not fw.is_computer(root, d)]
currency = dn.currency(led)
prices = dn.prices(state)
buying = set(dn.to_buy(state, parts))
person_questions = [r for r in hq.requests_view(events) if r.get("route") == "ask_person"]
reply_words = sorted(len(m["text"].split()) for m in later) or [0]
reqs = state.of_type("requirement", "confirmed")
trace = tr.view(led)
early_tests = [t for t in trace["tests"] if t["stage"] != "built"]


def item(pattern):
    return [n for n in needs if re.search(pattern, n["item"], re.I)]


def done(found):
    return bool(found) and all(n["status"] in ("met", "waived") for n in found)


checks = {
    "reached_g4": ("Got past Gate 3 to the design review", reach >= 4, f"current gate {current}"),
    "g4_design_complete": ("Gate 4 needed a complete design", reach < 5 or (bool(needs) and all(n["status"] in ("met", "waived") for n in needs)), f"{sum(n['status'] in ('met', 'waived') for n in needs)} of {len(needs)} needed items settled"),
    "power_supply": ("A power supply was chosen", done(item(r"power|supply|psu")), [n["parts"] for n in item(r"power|supply|psu")]),
    "protection": ("Overcurrent protection was chosen or waived", done(item(r"fuse|protect|breaker")), [n["status"] for n in item(r"fuse|protect|breaker")]),
    "servos": ("All servos were chosen", done(item(r"servo")), [n["parts"] for n in item(r"servo")]),
    "wiring": ("A wiring list was agreed", len(nets) >= 2, f"{len(nets)} connections"),
    "seven_pins": ("Every servo has an agreed control path", len(fw.pin_defines(state)) >= 7 or any(n["fields"]["kind"] == "i2c" for n in nets), f"{len(fw.pin_defines(state))} direct pins, {sum(n['fields']['kind'] == 'i2c' for n in nets)} I2C nets to a servo driver"),
    "pins_match": ("Firmware uses the agreed pins", bool(fw.pin_defines(state)) and not fw.pin_problems(root, state), fw.pin_problems(root, state) or "matches"),
    "limits_read": ("Datasheet limits were read", len(state.of_type("parameter", "confirmed")) > 0, f"{len(state.of_type('parameter', 'confirmed'))} confirmed limits"),
    "parts_listed": ("The arm's parts were listed", len(parts) >= 4, f"{len(parts)} parts"),
    "parts_modelled": ("Every printable part has a CAD model", bool(printable) and all(p["fields"]["number"] in modelled for p in printable), f"{sum(p['fields']['number'] in modelled for p in printable)} of {len(printable)} modelled"),
    "all_joints": ("Mechanical covered all 6 joints", len(joints) >= 6 and not dn.uncovered(state, parts), f"{len(joints)} joints listed, no parts for: {dn.uncovered(state, parts)}"),
    "board_firmware": ("Board firmware was written", bool(boards), boards or "none"),
    "honest_prices": ("Every price converts to one currency", bool(buying) and all(dn.line_total(prices.get(dn._key(i)), currency) is not None for i in buying), f"{currency} {dn.estimated_total(state, parts, currency)} for {len(buying)} things"),
    "few_questions": ("The student was asked 8 questions or fewer", len(person_questions) <= 8, f"{len(person_questions)} questions"),
    "short_replies": ("Replies stayed short", reply_words[-1] <= 60, f"longest {reply_words[-1]} words, {sum(reply_words)} in total"),
    "requirements_traced": ("Every requirement traces to the student or a stated reason", bool(reqs) and all(r["fields"].get("person_said") or r["fields"].get("added_because") for r in reqs), f"{sum(bool(r['fields'].get('person_said')) for r in reqs)} of {len(reqs)} in the student's words"),
    "early_tests": ("Design tests passed before Gate 5", reach < 5 or (bool(early_tests) and all(t["state"] == "passed" for t in early_tests if t["stage"] == "design")), f"{sum(t['state'] == 'passed' for t in early_tests)} of {len(early_tests)} early tests passed"),
    "print_files": ("Parts were exported for printing", len(stl) >= max(1, len(printable)), f"{len(stl)} STL files"),
    "no_cad_pileup": ("Fusion proposals didn't pile up", sum(c["stage"] == "proposed" for c in changes) <= 1, f"{sum(c['stage'] == 'proposed' for c in changes)} waiting"),
    "plastic_mass": ("CAD mass uses a plastic density", volume == 0 or latest.get("massG", 0) / max(volume, 0.001) < 2.5, f"{latest.get('massG', 0)} g, {round(volume, 1)} cm3"),
    "app_sends": ("The laptop app really sends the packet", bool(state.packets) and not itf.unwired(root, state), itf.unwired(root, state) or "wired"),
    "real_flash": ("Firmware flashed to the real board", bool(notes.get("flash")) and not str(notes["flash"]).startswith("failed"), notes.get("flash")),
    "board_received": ("The real board received a real packet", bool(serial.get("received")), serial.get("detail", "not run")),
    "no_terminal": ("No agent sent the student to a terminal", not [m for m in later if re.search(r"pio run|pio device", m["text"])], "clean"),
    "no_stale": ("Nothing stale from the concept stage", not [c for c in state.of_type("component") if re.search("nucleo|stm32", c["fields"]["part_number"], re.I)], "clean"),
    "sources_relevant": ("Research sources are about this kind of system", bool(sources) and len(relevant) >= len(sources) * 0.7, f"{len(relevant)} of {len(sources)}: {served[:90]}"),
    "web_sources": ("Research used manufacturer or web sources", any(s["fields"].get("tier") == "Web or other" for s in sources), [s["fields"].get("tier") for s in sources]),
    "no_dead_ends": ("The student never got stuck with no way forward", not notes["stalls"], notes["stalls"][:2] or "none"),
    "no_crash": ("No page crashes or errors", not notes["crashes"] and not notes["pageErrors"], f"{notes['crashes']} crashes, {len(notes['pageErrors'])} errors"),
    "rehearsal": ("Gates 5 and 6 worked on a labelled rehearsal", bool(rehearsal.get("g6_refused_failure")) and bool(rehearsal.get("finished")), rehearsal.get("detail", "not run")),
}
out = {"spent": state.spent_usd, "gate": current, "checks": {k: {"label": v[0], "pass": bool(v[1]), "detail": v[2]} for k, v in checks.items()}}
out["passed"], out["total"] = sum(c["pass"] for c in out["checks"].values()), len(out["checks"])
print(json.dumps(out, default=str))
