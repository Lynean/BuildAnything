"""Gates 5 and 6 on a throwaway copy. Everything entered is labelled SIMULATED, and no model is called."""
import json
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "backend"))
from buildanything import changes as ch, designneeds as dn, gates, interfaces as itf, mechanical as mech, traceability as tr  # noqa: E402
from buildanything.ledger import Actor, Ledger  # noqa: E402

SOURCE = pathlib.Path(sys.argv[1])
workspace = pathlib.Path(tempfile.mkdtemp(prefix="ba-rehearse-"))
root = workspace / SOURCE.name
shutil.copytree(SOURCE, root, ignore=shutil.ignore_patterns(".pio", ".git", "__pycache__", "node_modules"))
led = Ledger(root)
HUMAN = Actor.human("Student (rehearsal)")
result = {"finished": False, "g6_refused_failure": False, "detail": ""}
try:
    state = led.state()
    parts = mech.current_parts(state)
    for component in state.of_type("component", "confirmed"):
        if not component["fields"].get("owned"):
            dn.mark_owned(led, component["id"], HUMAN)
    if any(dn.is_purchased(p) for p in parts):
        names = ", ".join(p["fields"]["name"] for p in parts if dn.is_purchased(p))
        order = led.propose(HUMAN, "order", supplier="SIMULATED supplier", items=f"SIMULATED: {names}", status="Ordered")
        led.append(HUMAN, "record.confirmed", id=order)
        led.append(HUMAN, "order.arrived", id=order)
    led.append(HUMAN, "release.created", id="R1", recipient="SIMULATED printer", file="R1.zip", override="SIMULATED", parts=[{"id": p["id"], "number": p["fields"]["number"], "revision": p["fields"].get("revision")} for p in parts])
    blocked = gates.missing("G5", led.state(), led)
    flash_only = all("Flash" in text for text in blocked)
    if blocked and not flash_only:
        result["detail"] = f"Gate 5 still blocked: {blocked[:3]}"
        raise SystemExit
    if blocked:
        result["detail"] = "Gate 5 needs a real flash, so the rehearsal skipped it. "
    led.append(HUMAN, "gate.approved", gate="G5")
    view = tr.view(led)
    failing = view["tests"][-1]
    for test in view["tests"]:
        limit = test.get("min") if test.get("min") is not None else test.get("max")
        value = None if not test.get("metric") else (limit if limit is not None else 1.0)
        passed = test["id"] != failing["id"]
        tr.record_result(led, test["id"], value, "passed" if passed else "failed", "SIMULATED result for a rehearsal. Nothing was measured.", HUMAN)
    try:
        gates.approve(led, "G6", HUMAN)
    except gates.GateError:
        result["g6_refused_failure"] = True
    state = led.state()
    for key in [r["key"] for r in tr.view(led)["rows"] if r["status"] == "failed"]:
        requirement = next(r for r in state.of_type("requirement", "confirmed") if r["fields"]["key"] == key)
        change = ch.propose(led, f"SIMULATED: {key} not met", "SIMULATED rehearsal of a failed test", "deviation", requirement["id"], {"what": "SIMULATED: this was not achieved", "restores": "SIMULATED: a later build"}, HUMAN)
        led.append(Actor.system("headquarters"), "change.reviewed", id=change, review=ch.ChangeReview(recommendation="approve", summary="SIMULATED", risks=[], missed=[]).model_dump())
        ch.approve(led, change, "SIMULATED acceptance for the rehearsal", HUMAN)
    gates.approve(led, "G6", HUMAN)
    result["finished"] = gates.current_gate(led.state()) is None
    result["detail"] += f"{len((root / 'reports').glob('*.md').__class__.__name__) and len(list((root / 'reports').glob('*.md')))} reports. Unwired packets at the end: {len(itf.unwired(root, led.state()))}."
except SystemExit:
    pass
except Exception as exc:  # A rehearsal failure is a finding, not a crash.
    result["detail"] += f"{type(exc).__name__}: {exc}"[:220]
finally:
    shutil.rmtree(workspace, ignore_errors=True)
print(json.dumps(result))
