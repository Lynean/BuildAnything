"""What each checkpoint requires, and a one-page report of where the project really stands.

The requirements are worked out from the project's own data. No model writes or judges them.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
import re
from typing import Any

from . import ci, memo, changes, compat, debugging, depgraph, designneeds, firmware, flashing, gates, interfaces, links, mechanical as mech, traceability
from . import devicefacts, documents
from .ledger import Ledger, ProjectState

STATUS = {"proven": "Proven", "deviated": "Accepted deviation", "failed": "Failed", "retest": "Test again", "not_tested": "Not tested yet", "needs_approval": "Test needs approval", "no_test": "No test"}
SUCCESSFUL_FLASH = ("running", "no_output")
BOARD_PART = re.compile(r"esp32|esp8266|stm32|nucleo|arduino|rp2040|raspberry pi pico|teensy|atmega", re.I)
BOARD_ROLE = re.compile(r"(main |primary |control )?(microcontroller|mcu)\b", re.I)


YOU = "You"
Blocker = tuple[str, str]


def _plural(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def _unsettled(ledger: Ledger) -> list[Blocker]:
    waiting = [c for c in changes.view(ledger) if c["status"] == "open"]
    return [(YOU, f"Decide {len(waiting)} open change {_plural(len(waiting), 'request', 'requests')}.")] if waiting else []


def modelled_parts(ledger: Ledger) -> set[str]:
    """Part numbers with a body in the CAD model as it stands now."""
    latest: dict[str, Any] | None = None
    before: dict[str, Any] = {}  # each applied change's model before it, for an undo
    for event in ledger.events():
        d = event["data"]
        if event["kind"] == "cad.applied":
            before[d.get("id")] = d.get("before")
        if event["kind"] in ("cad.applied", "cad.snapshot") or (event["kind"] == "cad.kept" and d.get("after")):
            latest = d["after"]
        elif event["kind"] == "cad.rolled_back":
            latest = before.get(d.get("id"), latest)
    linked = {e["data"]["part"] for e in ledger.events() if e["kind"] == "cad.linked"}  # modelled in a CAD tool the person or a connection runs
    return {b["part"] for b in (latest or {}).get("bodies", []) if b.get("part")} | linked


def _electrical_design(ledger: Ledger, state: ProjectState) -> list[Blocker]:
    out: list[Blocker] = []
    needs = designneeds.view(ledger)
    if not needs and not designneeds.declared_none(ledger, "Electrical"):
        out.append(("Electrical", "List everything the design needs to be built and safe, including power and protection."))
    empty = [n["item"] for n in needs if n["status"] == "open"]
    if empty:
        out.append(("Electrical", f"Not chosen yet: {', '.join(empty)}."))
    confirmed = state.of_type("component", "confirmed")
    sheets = {d["fields"]["component"] for d in state.of_type("datasheet") if documents.doc_kind(d) == "datasheet"}
    exempt = {c["id"] for c in confirmed if c["fields"].get("generic") or c["fields"].get("no_datasheet")}
    lacking = documents.missing(ledger, state)
    if lacking:
        out.append(("Electrical", f"Fetch the public documents for: {'; '.join(f'{part} ({needed})' for part, needed in lacking)}, with fetch_document from their direct PDF links, preferably the manufacturer's. Say which ones aren't public, so the person uploads those."))
        out.append((YOU, f"Upload documents for: {'; '.join(f'{part} ({needed})' for part, needed in lacking)}. If one doesn't exist, say so on the Electrical screen."))
    read = {e["data"]["component"] for e in ledger.events() if e["kind"] == "datasheet.read"} | {p["fields"]["component"] for p in state.of_type("parameter")}
    number = {c["id"]: c["fields"]["part_number"] for c in confirmed}
    known = {number[i] for i in read if i in number}  # One datasheet read covers every use of the same part.
    unread = sorted({c["fields"]["part_number"] for c in confirmed if c["id"] in sheets and c["fields"]["part_number"] not in known})
    matched = {d["fields"]["component"] for d in state.of_type("datasheet") if d["fields"].get("part_match") and documents.doc_kind(d) == "datasheet"}
    unsure = sorted({c["fields"]["part_number"] for c in confirmed if c["id"] in sheets and c["id"] not in matched and c["fields"]["part_number"] in unread})
    if unsure:
        out.append((YOU, f"Check the datasheet for {', '.join(unsure)}: its exact part number isn't in the PDF. If it's the right document, press Read limits."))
    readable = [number for number in unread if number not in unsure]
    if readable:
        out.append(("Electrical", f"Read the limits from the datasheets of: {', '.join(readable)}."))
    for_you = devicefacts.needing_you(ledger, state)
    if for_you:
        out.append((YOU, f"Check {len(for_you)} device {_plural(len(for_you), 'fact', 'facts')} the app couldn't verify itself, on the Electrical screen. The rest were checked automatically."))
    facts_due = devicefacts.unread(ledger, state)
    if facts_due:
        out.append(("Electrical", f"Read the device facts from the documents of: {', '.join(facts_due)}, with read_facts."))
    vague = [c["fields"]["part_number"] for c in confirmed if re.search(r"\bTBD\b", c["fields"]["part_number"], re.I)]
    if vague:
        out.append(("Electrical", f"Too vague to buy: {', '.join(vague)}. Give each a real part number or rating."))
    for label, kind in (("components", "component"), ("datasheet limits", "parameter"), ("connections", "net"), ("electrical and mechanical interfaces", "interface")):
        count = len(state.of_type(kind, "proposed"))
        if count:
            out.append((YOU, f"Review {count} {label}."))
    accepted = designneeds.accepted_warnings(ledger)
    disputed = {d["message"]: d["why"] for d in designneeds.disputes(ledger)}
    for check in compat.run_checks(state):
        if check["status"] != "fail" or check["message"] in accepted:
            continue
        if check["message"] in disputed:
            out.append((YOU, f"Electrical says this warning doesn't apply: {check['message']} Why: {disputed[check['message']]} Decide on the Electrical screen."))
        else:
            out.append(("Electrical", f"Compatibility: {check['message']}"))
    wires = state.of_type("net", "confirmed")
    undecided = state.of_type("component", "proposed") or state.of_type("net", "proposed")
    if len(confirmed) >= 2 and not undecided:
        if not wires:
            out.append(("Electrical", "Draft the connection list, so every pin is agreed before anything is wired."))
        else:
            joined = {m["component"] for n in wires for m in n["fields"]["members"]}
            loose = [c["fields"]["part_number"] for c in confirmed if c["id"] not in joined and c["id"] not in exempt]  # A plain cable isn't a circuit node.
            if loose:
                out.append(("Electrical", f"Not connected to anything yet: {', '.join(loose)}."))
    return out


def _mechanical_design(ledger: Ledger, state: ProjectState) -> list[Blocker]:
    out: list[Blocker] = []
    parts = mech.current_parts(state)
    if not parts and not designneeds.declared_none(ledger, "Mechanical"):
        out.append(("Mechanical", "List the parts this design is made from."))
    waiting = len(state.of_type("part_proposal", "proposed"))
    if waiting:
        out.append((YOU, f"Add or dismiss {waiting} suggested {_plural(waiting, 'part', 'parts')} on the Mechanical screen."))
    modelled = modelled_parts(ledger)
    waived = designneeds.model_waivers(ledger)
    missing = [p for p in parts if not p["fields"].get("file") and p["fields"]["number"] not in modelled and p["fields"]["number"] not in waived and not designneeds.is_loose(p)]
    made = [p["fields"]["number"] for p in missing if not designneeds.is_purchased(p) and not designneeds.is_owned(p)]
    bought = [p["fields"]["number"] for p in missing if p["fields"]["number"] not in made]
    if made:
        out.append(("Mechanical", f"These parts have no CAD model yet: {', '.join(made)}."))
    if bought:  # a bought motor or battery still takes space and must fit its mount, so the whole product is checked, not just the printed parts
        out.append(("Mechanical", f"These bought or existing parts aren't in the CAD model yet: {', '.join(bought)}. Add each as the maker's CAD model or a block at its datasheet size, placed where it mounts, so fits and clearances can be checked; the person can waive one that really needs none."))
    if parts and not designneeds.assembly_plan(state) and not designneeds.declared_none(ledger, "Mechanical"):
        out.append(("Mechanical", "List every joint and assembly of the complete product with set_assemblies, so the parts can be checked against the whole design and not just a test piece."))
    missing_parts = designneeds.uncovered(state, parts)
    if missing_parts:
        out.append(("Mechanical", f"No parts listed yet for: {', '.join(missing_parts)}."))
    checks = mech.open_checks(ledger)
    if checks:
        out.append((YOU, f"Close {len(checks)} open part checks: {', '.join(sorted({c['part'] + ' ' + c['check'] for c in checks})[:6])}."))
    return out


def _procurement(ledger: Ledger, state: ProjectState) -> list[Blocker]:
    parts = mech.current_parts(state)
    wanted = sorted(set(designneeds.to_buy(state, parts)))
    if not wanted:
        return []
    known = designneeds.prices(state)
    missing = [w for w in wanted if designneeds.line_total(known.get(designneeds._key(w)), designneeds.currency(ledger)) is None]
    out: list[Blocker] = [("Procurement", f"Find a price and supplier for: {', '.join(missing[:14])}.")] if missing else []
    budget = mech.rollup(ledger, state)["budget"].get("cost")
    currency = designneeds.currency(ledger)
    total = designneeds.estimated_total(state, parts, currency)
    if not budget:
        from . import shape

        screen = "Procurement" if shape.on_team(state, "Procurement") or not shape.on_team(state, "Mechanical") else "Mechanical"
        out.append((YOU, f"Set the parts budget on the {screen} screen."))
    elif total > budget:
        out.append((YOU, f"The parts come to about {currency} {total:g}, over your {currency} {budget:g} budget. Raise the budget, or ask Procurement for cheaper options."))
    return out


FINDING_OWNERS = (("Electrical", r"\bdrivers?\b|middleware|src[\\/](?:driver|mid)|backlight|initiali[sz]"), ("Software", r"firmware|software|\bcode\b|laptop|program|app\b|driver|display|screen|game"), ("Electrical", r"wir|\bpins?\b|power|volt|current|circuit|fuse"), ("Mechanical", r"\bcad\b|bracket|mechanical|mount|print"), ("Procurement", r"price|supplier|\border\b|budget"))


def _finding_owner(text: str, team: list[str] | None = None) -> str:
    """The department a finding's fix goes to: the best match on the project's team, so a fix is never sent to nobody."""
    team = team or ["Electrical", "Mechanical", "Software", "Procurement"]
    matched = [owner for owner, pattern in FINDING_OWNERS if re.search(pattern, text, re.I)]
    return next((o for o in matched if o in team), "Software" if "Software" in team else team[0])


def _serious_findings(ledger: Ledger, state: ProjectState) -> list[Blocker]:
    """Fix it sends the work to a department once. Then a person checks it and marks it fixed."""
    high = gates.open_high_findings(state)
    undecided = [f for f in high if not f.get("decision")]
    out: list[Blocker] = [(YOU, f"Decide {len(undecided)} serious Verifier findings.")] if undecided else []
    events, to_check = ledger.events(), 0
    from . import shape

    team = shape.team(state) or [YOU]
    for finding in [f for f in high if f.get("decision", {}).get("decision") == "fix"]:
        owner = _finding_owner(finding["fields"]["text"], team)
        decided = next(i for i, e in enumerate(events) if e["kind"] == "finding.decided" and e["data"]["id"] == finding["id"])
        if any(e["kind"] == "report.filed" and e["data"]["department"] == owner for e in events[decided:]):
            to_check += 1
        else:
            out.append((owner, f"Fix this Verifier finding: {finding['fields']['text']}"))
    if to_check:
        out.append((YOU, f"Check that {to_check} {_plural(to_check, 'fix', 'fixes')} worked, then mark them fixed on Headquarters."))
    return out


def _bench_waiting(ledger: Ledger, device: str) -> bool:
    from . import bench

    return bench.ready(ledger.root, device) and bench.last_results(ledger.root, device) is not None and not ci.bench_accepted(ledger, device)


def _bench_due(ledger: Ledger, state: ProjectState, device: str) -> bool:
    """A board with an app and parts wired to it gets a bench, when this computer can compile one."""
    from . import bench, benchwriter

    if bench.ready(ledger.root, device) or not bench.compiler():
        return False
    has_code = firmware.has_app(ledger.root, device) or (not firmware.has_lower(ledger.root, device) and any((ledger.root / "firmware" / device / "src").rglob("*.cpp")))
    return has_code and bool(benchwriter.parts_for(state, ledger.root, device))


def _code_exists(ledger: Ledger, state: ProjectState) -> list[Blocker]:
    """A design isn't reviewed until the code for it exists. It waits for the wiring, because firmware's pins come from there."""
    confirmed = state.of_type("component", "confirmed")
    if designneeds.declared_none(ledger, "Software"):
        return []
    if not firmware.wiring_ready(state):
        return []
    boards = [d for d in firmware.devices(ledger.root) if not firmware.is_computer(ledger.root, d)]
    chips = sorted({c["fields"]["part_number"] for c in confirmed if BOARD_PART.search(c["fields"]["part_number"]) or BOARD_ROLE.match(c["fields"].get("role", "")) or BOARD_ROLE.match(c["fields"].get("fills", ""))})
    out: list[Blocker] = [("Electrical", f"No firmware exists yet for {', '.join(chips)}. Write its drivers and middleware with write_firmware, giving the PlatformIO board ID.")] if chips and not boards else []
    for device in boards:
        if firmware.has_lower(ledger.root, device) and not firmware.has_app(ledger.root, device):
            out.append(("Software", f"{device} has drivers and middleware from Electrical, but no app yet. Write the app with write_code."))
        wanted = firmware.middleware_wanted(ledger, device)
        if wanted:
            out.append(("Electrical", f"Software's app for {device} needs these from the middleware: {'; '.join(wanted)}. Add them with write_firmware."))
        changed = firmware.api_changed(ledger, ledger.root, device)
        if changed:
            out.append(("Software", f"{device}'s middleware changed since the app was built ({', '.join(changed)}). Update the app with write_code."))
        if _bench_waiting(ledger, device):
            out.append((YOU, f"Look at the laptop bench for {device} on the Tests screen: check the fake screen pictures and what the fakes guessed, then choose Use these results, or rebuild it if a fake is wrong."))
        if _bench_due(ledger, state, device):
            out.append(("Electrical", f"{device} has no laptop test bench yet. Build it with build_bench, so its firmware is tried against fake parts before anything is flashed."))
    return out + [("Software", text) for text in interfaces.unwired(ledger.root, state)]


def _tests_written(ledger: Ledger) -> list[Blocker]:
    trace = traceability.view(ledger)
    out: list[Blocker] = []
    vague_keys = {entry.split()[0].rstrip(":,") for entry in trace["untestable"] if entry.split()}
    lacking_tests = [r["key"] for r in trace["rows"] if r["status"] == "no_test" and r["key"] not in vague_keys]
    if lacking_tests:
        out.append((YOU, f"Every requirement needs a test. Missing for: {', '.join(lacking_tests)}."))
    too_vague = [r["key"] for r in trace["rows"] if r["status"] == "no_test" and r["key"] in vague_keys]
    if too_vague:
        out.append((YOU, f"Say what you'd accept for {', '.join(too_vague)} on the Tests screen, so a test can be written."))
    waiting = sum(1 for t in trace["tests"] if t["status"] == "proposed")
    if waiting:
        out.append((YOU, f"Approve {waiting} {_plural(waiting, 'test', 'tests')}."))
    return out


def _tests_of_stage(ledger: Ledger, stage: str, label: str) -> list[Blocker]:
    due = traceability.tests_due(ledger, stage)
    mine = [t["key"] for t in due if t["automatic"] == "none"]
    out: list[Blocker] = [("Software", f"{t['key']} needs its automatic check to pass: {t['title']}.") for t in due if t["automatic"] != "none"]
    if mine:
        out.append((YOU, f"Do these {label} tests on the Tests screen and record the results: {', '.join(mine)}."))
    return out


MESSAGE_CHECK_FAILED = "The message definitions failed their automatic check."
MESSAGE_CHECK_WAITING = "The message definitions haven't had their automatic check yet."


def interface_tests(ledger: Ledger, state: ProjectState) -> list[str]:
    """Gate 3 freezes how devices talk, so the messages must be proven and the tests agreed."""
    verdict = ci.automatic_state(ledger, "message_check") if state.packets else "passed"
    out = [] if verdict == "passed" else [MESSAGE_CHECK_FAILED if verdict == "failed" else MESSAGE_CHECK_WAITING]
    return out + [text for _owner, text in _tests_written(ledger)]


PLACEHOLDER = re.compile(r"\b(illustrative|placeholder|made[- ]up|dummy|stand-in|stub)\b", re.I)
MOVES_MACHINE = re.compile(r"robot\.urdf|\.urdf\b|urdf_kinematics|inverse kinematics|\bik\b", re.I)


NEGATION = re.compile(r"\b(no|not|without|never|instead of|replaces?|replaced)\b[^.;]{0,40}$", re.I)


def _stand_in(text: str) -> bool:
    """A block that says it's a placeholder, not one that says it has none."""
    return any(not NEGATION.search(text[:m.start()]) for m in PLACEHOLDER.finditer(text))


def _program_checks(ledger: Ledger, state: ProjectState) -> list[Blocker]:
    """What only reading the code shows: placeholders left in, features nothing uses, and moves nobody checks for collisions."""
    from . import software

    out: list[Blocker] = []
    swept = None  # the collision sweep went with the MuJoCo integration
    for device in firmware.devices(ledger.root):
        files = [f for f in firmware.scan_workspace(ledger.root, device) if not f.get("generated")]
        if not files:
            continue
        runtime = [f for f in files if not Path(f["path"]).stem.startswith("test_")]
        stand_ins: dict[str, list[str]] = {}
        for f in runtime:
            for b in f["blocks"]:
                if _stand_in(f"{b.get('title', '')}. {b.get('description', '')}"):
                    owner = "Electrical" if b.get("owner") == "Electrical" else "Software"
                    stand_ins.setdefault(owner, []).append(f"{f['path']}: {b.get('title', '?')}")
        for owner, found in stand_ins.items():
            if designneeds.deferred(ledger, owner):
                continue
            out.append((owner, f"{device} still has placeholder or illustrative code after the plan was agreed: {'; '.join(found[:4])}{' and more' if len(found) > 4 else ''}. Replace it with the real thing, using other departments' deliverables, or say what's missing."))
        if not firmware.is_computer(ledger.root, device):
            continue
        loose = software.unconnected(ledger.root, device, [f["path"] for f in runtime])
        if loose:
            out.append(("Software", f"{device}: {' '.join(loose[:3])} Connect it to the program people start, or remove it."))
        if swept:
            text = "\n".join("\n".join(b["code"] for b in f["blocks"]) or (f["plain"] or "") for f in runtime)
            if MOVES_MACHINE.search(text) and "robot_model" not in text:
                hits = f" The sweep found the arm hitting itself or its base in {swept['colliding_share']:.0%} of poses." if swept["colliding_pairs"] else ""
                out.append(("Software", f"{device} moves the machine but doesn't check moves for collisions. Check every move with path_collisions from generated/robot_model.py, which has the real CAD shapes.{hits}"))
    return out


def _design_review(ledger: Ledger, state: ProjectState) -> list[Blocker]:
    if designneeds.simulation_first(ledger):  # hardware waits: no parts, wiring, firmware or prices yet
        out = [b for b in _code_exists(ledger, state) if b[0] not in designneeds.HARDWARE_DEPARTMENTS] + ci.tool_problems(ledger) + _serious_findings(ledger, state)
    else:
        out = _electrical_design(ledger, state) + _mechanical_design(ledger, state) + _procurement(ledger, state) + _code_exists(ledger, state) + ci.tool_problems(ledger) + _serious_findings(ledger, state)
    out += _program_checks(ledger, state)
    for twins in firmware.duplicate_boards(ledger.root, firmware.separate_boards(ledger)):
        out.append(("Electrical", f"firmware/{' and firmware/'.join(twins)} are code for the same board. Keep the one with the real code and retire the other with retire_device."))
    if not traceability.view(ledger)["total"]:
        out.append((YOU, "Confirm the requirements."))
    out += _tests_written(ledger) + _tests_of_stage(ledger, "design", "design-stage")
    undescribed = 0
    for device in firmware.devices(ledger.root):
        for file in firmware.scan_workspace(ledger.root, device, firmware.valid_note_ids(state), firmware.component_names(state)):
            if not file.get("generated"):
                undescribed += sum(1 for b in file["blocks"] if b["state"] != "documented") + (0 if file["blocks"] else 1)
    if undescribed:
        out.append(("Software", f"{undescribed} code blocks or files need an up-to-date description."))
    out += [("Software", text) for text in firmware.pin_problems(ledger.root, state)]
    return out + _unsettled(ledger)


def _live_problems(ledger: Ledger) -> list[Blocker]:
    out: list[Blocker] = []
    sessions = [s for s in debugging.sessions(ledger).values() if s["status"] == "open"]
    if sessions:
        out.append((YOU, f"Resolve {len(sessions)} open debugging problems: {', '.join(s['title'] for s in sessions[:4])}."))
    issues = [i for i in links.issues(ledger).values() if i["status"] == "open"]
    if issues:
        out.append((YOU, f"Close {len(issues)} open link issues: {', '.join(i['title'] for i in issues[:4])}."))
    return out


def _build_and_bring_up(ledger: Ledger, state: ProjectState) -> list[Blocker]:
    out: list[Blocker] = _tests_of_stage(ledger, "before_buying", "before-buying")
    current = mech.current_parts(state)
    unordered = designneeds.not_on_order(state, current)
    if unordered:
        out.append((YOU, f"Order these, or mark them as already owned: {', '.join(unordered[:12])}."))
    late = [o["fields"].get("supplier") or "Unknown supplier" for o in state.of_type("order", "confirmed") if o["fields"].get("status") != "Arrived"]
    if late:
        out.append((YOU, f"{len(late)} {'order hasn' if len(late) == 1 else 'orders haven'}'t arrived: {', '.join(late[:4])}."))
    released = {p["id"] for r in mech.releases(ledger) for p in r["parts"]}
    missing = [p["fields"]["number"] for p in current if p["id"] not in released]
    if missing:
        out.append((YOU, f"Release the current revision of: {', '.join(missing[:8])}."))
    out += [("Software", text) for text in interfaces.unwired(ledger.root, state)]
    out += ci.tool_problems(ledger) + _serious_findings(ledger, state)
    events = ledger.events()
    prepared = {e["data"]["id"]: e["data"] for e in events if e["kind"] == "flash.prepared"}
    for device in firmware.devices(ledger.root):
        if firmware.is_computer(ledger.root, device):
            continue
        flashed = [e["data"] for e in events if e["kind"] == "flash.finished" and e["data"]["device"] == device and e["data"]["outcome"] in SUCCESSFUL_FLASH]
        if not flashed:
            out.append((YOU, f"Flash {device} from the Device tab, and confirm it runs."))
        elif prepared.get(flashed[-1]["id"], {}).get("source_sha") != flashing.source_hash(ledger.root / "firmware" / device):
            out.append((YOU, f"Flash {device} again, because its code has changed since the last flash."))
    return out + _live_problems(ledger) + _unsettled(ledger)


def _failed_tests(ledger: Ledger, trace: dict) -> list[Blocker]:
    """What the person saw on the real device goes to the department that owns the test, once. After it reports, the person tests again."""
    events, out, again = ledger.events(), [], []
    for test in trace["tests"]:
        if test["state"] != "failed":
            continue
        failed_at = max((i for i, e in enumerate(events) if e["kind"] == "test.result" and e["data"]["test"] == test["id"]), default=0)
        if any(e["kind"] == "report.filed" and e["data"]["department"] == test["owner"] for e in events[failed_at:]):
            again.append(test["title"])
        else:
            seen = (test.get("result") or {}).get("evidence", "")
            out.append((test["owner"], f"Test \"{test['title']}\" failed on the real device. The person saw: {seen} It should: {test.get('pass_if', '')} Find the cause and fix it."))
    if again:
        out.append((YOU, f"Test again on the device, because a fix was made: {', '.join(again[:6])}."))
    return out


def _integration(ledger: Ledger, state: ProjectState) -> list[Blocker]:
    trace = traceability.view(ledger)
    out: list[Blocker] = []
    assumed = documents.unchecked(ledger, state)
    if assumed:
        out.append((YOU, f"Check {len(assumed)} {_plural(len(assumed), 'assumption', 'assumptions')} on the real device, then mark each checked on the Electrical screen: {' '.join(a['text'] for a in assumed[:4])}"))
    if not trace["total"]:
        out.append((YOU, "Confirm the requirements."))
    out += _failed_tests(ledger, trace)
    for status, label in (("retest", "need testing again"), ("not_tested", "haven't been tested"), ("needs_approval", "have tests awaiting approval"), ("no_test", "have no test")):
        keys = [r["key"] for r in trace["rows"] if r["status"] == status]
        if keys:
            out.append((YOU, f"{len(keys)} of the requirements {label}: {', '.join(keys[:10])}."))
    return out + _serious_findings(ledger, state) + _live_problems(ledger) + _unsettled(ledger)


REQUIREMENTS = {"G4": _design_review, "G5": _build_and_bring_up, "G6": _integration}


def blockers(gate: str, state: ProjectState, ledger: Ledger) -> list[Blocker]:
    """Who has to act on each thing blocking a gate. A department can be sent to work. You can't."""
    # Read the project inside the cached work. A copy taken earlier could be older than what it's saved under, and would stay wrong.
    return memo.remembered(ledger, f"blockers:{gate}", lambda: _blockers(gate, ledger.state(), ledger))


def _blockers(gate: str, state: ProjectState, ledger: Ledger) -> list[Blocker]:
    return gates.requirements(gate, state, ledger)


def missing(gate: str, state: ProjectState, ledger: Ledger) -> list[str]:
    return [text for _owner, text in blockers(gate, state, ledger)]


def _bullets(items: list[str], empty: str) -> str:
    return "\n".join(f"- {i}" for i in items) if items else f"{empty}"


def report(ledger: Ledger, gate: str) -> str:
    """One page: what's proven, what's unproven, what was accepted, and what was decided on your behalf."""
    state = ledger.state()
    blocking = gates.missing(gate, state, ledger) if gate not in state.gates_approved else []
    trace = traceability.view(ledger)
    c = trace["counts"]
    table = "\n".join(f"| {r['key']} | {r['text'].replace('|', '/')} | {', '.join(r['tests']) or 'None'} | {STATUS[r['status']]} |" for r in trace["rows"])
    accepted = [f"{f['finding']} *Reason: {f['reason']}*" + (" (safety)" if f["safety"] else "") for f in gates.closed_findings(state) if f["decision"] in ("accept", "dismiss")]
    deviated = [f"**{d['key']}:** {d['what']} *Why: {d['why']} What puts it right: {d['restores']}*" for d in trace["deviations"] if d["status"] == "active"]
    dropped = [f"**{d['key']}:** {d['text']} *Why: {d['why']}*" for d in trace["dropped"]]
    accepted += [f"Compatibility warning: {m} *Reason: {r}*" for m, r in designneeds.accepted_warnings(ledger).items()]
    every = changes.view(ledger)
    auto = [f"Change {x['number']}: {x['title']}. *{x['decision']['reason']}*" for x in every if x["decision"] and x["decision"].get("auto")]
    made = [f"Change {x['number']}: {x['title']}" for x in every if x["status"] == "approved" and not x["decision"].get("auto")]
    roll = mech.rollup(ledger, state)
    mass, cost = roll["budget"].get("mass_g"), roll["budget"].get("cost")
    open_checks = len(mech.open_checks(ledger))
    currency = designneeds.currency(ledger)
    covers = [f"**{name}:** {', '.join(listed) or 'nothing listed'}" for name, listed in designneeds.assembly_plan(state).items()]
    verdict = f"**Ready.** {gates.continue_text(gate, state)}." if not blocking and gate not in state.gates_approved else "**Done.**" if gate in state.gates_approved else f"**Not ready.** {len(blocking)} {'thing is' if len(blocking) == 1 else 'things are'} still to do in this stage."
    return f"""# {gates.stage(gate, state)}: {gates.purpose(gate, state)}

**Project:** {state.meta.get('name', 'Project')}. **Written:** {dt.date.today().isoformat()}.

{verdict}

## Blocking this gate

{_bullets(blocking, 'Nothing.')}

## Does it do what we said?

{c['proven']} of {trace['total']} requirements are proven. Accepted deviations: {c['deviated']}. Failed: {c['failed']}. To test again: {c['retest']}. Not tested yet: {c['not_tested']}. Test waiting for approval: {c['needs_approval']}. No test: {c['no_test']}.

| Requirement | What it says | Tests | Status |
|---|---|---|---|
{table}

## Added by agents, not asked for by you

{_bullets([f"**{r['key']}:** {r['text']} *Why: {r['addedBecause']}*" for r in trace['rows'] if r['addedBecause']], 'None.')}

## Requirements knowingly not met

{_bullets(deviated, 'None.')}

## Requirements dropped

{_bullets(dropped, 'None.')}

## Risks you accepted

{_bullets(accepted, 'None.')}

## Decided by Headquarters without asking you

{_bullets(auto, 'None.')}

## Changes you approved

{_bullets(made, 'None.')}

## What the parts cover

{_bullets(covers, 'No list of assemblies yet.')}

## Assumptions to check on the real device

{_bullets([a['text'] for a in documents.assumptions(ledger, state)], 'None: every fact the design relies on has a document behind it.')}

## Budget and spend

- **Mass:** {roll['massG']} g{f' of {mass:g} g' if mass else ', no budget set'}.
- **Parts cost:** {roll['cost']}{f' of {cost:g}' if cost else ', no budget set'}.
- **Estimated parts to buy:** {currency} {designneeds.estimated_total(state, mech.current_parts(state), currency):g}.
- **AI spend:** ${state.spent_usd:.2f} of ${state.limit_usd:.2f}.
- **Open part checks:** {open_checks}.
- **Tracked items:** {len(depgraph.build(ledger).fingerprints)}.
"""


def gate_list(ledger: Ledger) -> list[dict[str, Any]]:
    state = ledger.state()
    current = gates.current_gate(state)
    approved = {e["data"]["gate"]: e for e in ledger.events() if e["kind"] == "gate.approved"}
    out = []
    for item in gates.plan(state):
        gate = item["id"]
        entry: dict[str, Any] = {"id": gate, "name": item.get("title") or item["name"], "stage": item["name"], "purpose": item.get("purpose", ""), "phase": item["phase"],
                                 "checks": [{"key": k, "label": gates.CHECKS[k][0]} for k in item.get("checks", [])], "criteria": gates.criteria_view(state, gate),
                                 "state": "approved" if gate in approved else "imported" if gate in state.gates_imported else "current" if gate == current else "later"}
        if gate in approved:
            entry["by"] = approved[gate]["actor"]["name"]
            entry["at"] = approved[gate]["at"][:10]
        if gate == current:
            entry["missing"] = gates.missing(gate, state, ledger)
            entry["waiting"] = [{"who": who, "text": text} for who, text in blockers(gate, state, ledger)]
        out.append(entry)
    return out


def saved_report(ledger: Ledger, gate: str) -> str:
    """Approved gates keep the page as it stood when they were approved."""
    stored = next((e["data"].get("report") for e in reversed(ledger.events()) if e["kind"] == "gate.approved" and e["data"]["gate"] == gate), None)
    return stored or report(ledger, gate)
