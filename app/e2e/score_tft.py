"""Scores the TFT clock and game run from the project's own records. Run by student-run.mjs."""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "backend"))
from buildanything import firmware as fw, gates, headquarters as hq, interfaces as itf, traceability as tr  # noqa: E402
from buildanything.ledger import Ledger  # noqa: E402

root = pathlib.Path(sys.argv[1])
notes = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
led = Ledger(root)
state, events = led.state(), led.events()
order = ["G0", "G1", "G2", "G3", "G4", "G5", "G6"]
current = gates.current_gate(state)
reach = order.index(current) if current else 7
boards = [d for d in fw.devices(root) if not fw.is_computer(root, d)]
computers = [d for d in fw.devices(root) if fw.is_computer(root, d)]
defines = {str(k).upper(): str(v).upper() for k, v in dict(fw.pin_defines(state)).items()} if fw.pin_defines(state) else {}
pins = " ".join(defines.values())
wanted = {"PA5", "PA7", "PB6", "PC7", "PA9", "PB10"}
source = " ".join(p.read_text(encoding="utf-8", errors="replace") for d in boards for p in (root / "firmware" / d / "src").rglob("*") if p.is_file())
later = [m for d in hq.DEPARTMENTS for m in hq.thread(events, d) if m["role"] == "agent" and not m["early"]]
words = sorted(len(m["text"].split()) for m in later) or [0]
questions = [r for r in hq.requests_view(events) if r.get("route") == "ask_person"]
serial = notes.get("serial") or {}
reqs = state.of_type("requirement", "confirmed")
trace = tr.view(led)

ON = r"digitalWrite\s*\(\s*BA_PIN_\w*BACKLIGHT\s*,\s*(?:HIGH|1|true)|[Bb]acklight\w*\s*\(\s*(?:true|1|HIGH)\s*\)"
STARTED = r"\binit[RB]\s*\(|\b0x29\b|DISPON|ST77XX_DISPON"
checks = {
    "reached_g5": ("Got through the design review to Gate 5", reach >= 5, f"current gate {current}"),
    "firmware": ("Board firmware was written", bool(boards), boards or "none"),
    "laptop_app": ("A laptop program sets the time", bool(computers), computers or "none"),
    "exact_wiring": ("Firmware uses the student's exact wiring", wanted <= set(re.findall(r"P[A-C]\d+", pins)), str(defines) if defines else "no agreed pins"),
    "pins_match": ("Code matches the agreed pins", bool(defines) and not fw.pin_problems(root, state), fw.pin_problems(root, state) or "matches"),
    "button": ("Firmware reads the B1 button", bool(re.search(r"PC13|USER_BTN|USER_BUTTON", source)), "found" if re.search(r"PC13|USER_BTN|USER_BUTTON", source) else "not found"),
    "backlight": ("Firmware turns on the backlight", bool(re.search(ON, source)), "switched on" if re.search(ON, source) else "never switched on"),
    "display_started": ("Firmware starts the display controller", bool(re.search(STARTED, source)), "started" if re.search(STARTED, source) else "never started"),
    "wired": ("The time message is really sent and read", bool(state.packets) and not itf.unwired(root, state), itf.unwired(root, state) or "wired"),
    "no_invented_parts": ("Mechanical didn't invent parts", not state.of_type("part", "confirmed"), f"{len(state.of_type('part', 'confirmed'))} parts"),
    "real_flash": ("Firmware flashed to the real board", bool(notes.get("flash")) and not str(notes["flash"]).startswith("failed"), notes.get("flash")),
    "board_received": ("The real board received a real packet", bool(serial.get("received")), serial.get("detail", "not run")),
    "few_questions": ("The student was asked 5 questions or fewer", len(questions) <= 5, f"{len(questions)} questions"),
    "short_replies": ("Replies stayed short", words[-1] <= 60, f"longest {words[-1]} words, {sum(words)} in total"),
    "requirements_traced": ("Every requirement traces to the student or a stated reason", bool(reqs) and all(r["fields"].get("person_said") or r["fields"].get("added_because") for r in reqs), f"{sum(bool(r['fields'].get('person_said')) for r in reqs)} of {len(reqs)} in the student's words"),
    "tests_exist": ("Requirements have approved tests", bool(trace["tests"]) and all(t["status"] == "confirmed" for t in trace["tests"]), f"{len(trace['tests'])} tests"),
    "no_dead_ends": ("The student never got stuck with no way forward", not notes["stalls"], notes["stalls"][:2] or "none"),
    "no_crash": ("No page crashes or errors", not notes["crashes"] and not notes["pageErrors"], f"{notes['crashes']} crashes, {len(notes['pageErrors'])} errors"),
}
out = {"spent": state.spent_usd, "gate": current, "checks": {k: {"label": v[0], "pass": bool(v[1]), "detail": v[2]} for k, v in checks.items()}}
out["passed"], out["total"] = sum(c["pass"] for c in out["checks"].values()), len(out["checks"])
print(json.dumps(out, default=str))
