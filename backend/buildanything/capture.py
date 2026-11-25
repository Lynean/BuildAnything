"""Capture evidence: listen to the real devices and fill in the five checks from what they log.

This only listens. Devices log through ba_log_event and ba_log_info in the generated packet code.
A device that logs nothing is reported as unknown, because silence can't prove a failure.
"""
from __future__ import annotations

import collections
import re
import threading
import time
from pathlib import Path
from typing import Any

from . import codegen as gen, flashing, interfaces as itf, links
from .ledger import Actor, Ledger, LedgerError

EVENT = re.compile(r"^@ba (tx|rx|done) (\d+) (-?\d+)(?: ([0-9a-f]+))?$")
INFO = re.compile(r"^@ba info (.+)$")
ACTOR = Actor.system("capture")
DEFAULT_SECONDS = 8.0
ENOUGH = 0.8
MAX_INFO_LINES = 2
COMPUTER = "computer"  # This end is a program on this computer, which writes an evidence log.
SAFE_DEVICE = re.compile(r"^[a-z0-9-]{1,40}$")


class CaptureError(LedgerError):
    pass


def parse(lines: list[str], packet_id: int) -> dict[str, Any]:
    out: dict[str, Any] = {"tx": [], "rx": [], "done": 0, "info": [], "logging": False}
    for raw in lines:
        line = raw.strip()
        info = INFO.match(line)
        if info:
            out["info"].append(info.group(1))
            out["logging"] = True
            continue
        event = EVENT.match(line)
        if not event:
            continue
        out["logging"] = True
        kind, packet, value, data = event.groups()
        if int(packet) != packet_id:
            continue
        if kind == "tx":
            out["tx"].append(int(value))
        elif kind == "rx":
            out["rx"].append((int(value), bytes.fromhex(data or "")))
        else:
            out["done"] += 1
    return out


def evaluate(module: Any, name: str, sender: dict[str, Any], receiver: dict[str, Any], seconds: float) -> list[dict[str, Any]]:
    """One row per check, in order: pass, fail, or unknown when the logs can't settle it."""
    cls = getattr(module, gen.class_name(name))
    statuses: collections.Counter[str] = collections.Counter()
    for _rssi, data in receiver["rx"]:
        try:
            cls.decode(data)
            statuses["ok"] += 1
        except module.PacketError as exc:
            statuses[exc.status] += 1
    sent, accepted = len(sender["tx"]), sum(1 for r in sender["tx"] if r == 0)
    received = len(receiver["rx"])
    rssi = [r for r, _ in receiver["rx"] if r]
    signal = f" Mean signal {round(sum(rssi) / len(rssi))} dBm." if rssi else ""
    unreadable = statuses["size"] + statuses["checksum"]
    refused = statuses["type"] + statuses["version"] + statuses["const"] + statuses["range"]
    good = statuses["ok"]
    reasons = ", ".join(f"{v} wrong {k}" for k, v in statuses.items() if k != "ok" and v)
    if not sender["logging"]:
        return [{"state": "unknown", "evidence": "The sender logged nothing, so its firmware may not include the evidence logging."}]
    rows = [{"state": "pass" if accepted else "fail", "evidence": f"The sender logged {accepted} of {sent} sends accepted by its radio or port in {seconds:.0f} seconds."}]
    if not receiver["logging"]:
        return [*rows, {"state": "unknown", "evidence": "The receiver logged nothing at all, not even its status, so it may be off or not logging. That doesn't show whether data arrived."}]
    rows.append({"state": "pass" if sent and received >= ENOUGH * sent else "fail", "evidence": f"The receiver logged {received} arrivals, for {sent} sent.{signal}"})
    rows.append({"state": "pass" if received and unreadable == 0 else "fail", "evidence": f"The generated decoder found {unreadable} of {received} failed the size or checksum test."})
    rows.append({"state": "pass" if refused == 0 else "fail", "evidence": f"The generated decoder refused {refused} of {received - unreadable}." + (f" Reasons: {reasons}." if reasons else "")})
    rows.append({"state": "pass" if good and receiver["done"] >= ENOUGH * good else "fail", "evidence": f"The receiver logged {receiver['done']} actions completed, for {good} valid packets."})
    return rows


def computer_log(root: Path, device: str) -> Path:
    return root / ".cache" / "link-logs" / f"{device}.log"


def _read_log(path: Path, seconds: float) -> list[str]:
    """Only lines written during the capture count, so an old log can't pass a new test."""
    start = path.stat().st_size if path.exists() else 0
    time.sleep(seconds)
    if not path.exists():
        return []
    with path.open("rb") as f:
        f.seek(start)
        return f.read().decode("utf-8", errors="replace").splitlines()


def listen_all(ports: dict[str, str], seconds: float, root: Path) -> dict[str, list[str]]:
    locks, out = [], {}
    try:
        for port in ports.values():
            if port != COMPUTER:
                locks.append(flashing._lock(port))
        threads = []
        for device, port in ports.items():
            def work(device: str = device, port: str = port) -> None:
                out[device] = _read_log(computer_log(root, device), seconds) if port == COMPUTER else flashing.listen(port, seconds)

            threads.append(threading.Thread(target=work))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        for lock in locks:
            lock.release()
    return out


def capture(ledger: Ledger, issue_id: str, ports: dict[str, str], seconds: float = DEFAULT_SECONDS) -> dict[str, Any]:
    issue = links.issues(ledger).get(issue_id)
    if issue is None or issue["status"] == "closed":
        raise CaptureError("This issue isn't open.")
    state = ledger.state()
    name = issue.get("packet")
    if not name or name not in state.packets:
        raise CaptureError("Link this issue to a packet first, so the app knows what to look for.")
    serial_ports = [p for p in ports.values() if p != COMPUTER]
    if set(ports) != set(issue["between"]) or len(set(serial_ports)) != len(serial_ports) or not all(SAFE_DEVICE.match(d) for d in ports):
        raise CaptureError("Choose a different serial port for each of the two devices.")
    packet = itf.expand(name, state.packets[name])
    if packet.get("standard"):
        raise CaptureError("This packet follows an outside standard, so the generated decoder can't check it.")
    sender_name = packet["producer"] if packet["producer"] in ports else issue["between"][0]
    receiver_name = next(d for d in issue["between"] if d != sender_name)
    logs = listen_all(ports, seconds, ledger.root)
    itf.regenerate(ledger.root, state)
    sender, receiver = parse(logs[sender_name], packet["id"]), parse(logs[receiver_name], packet["id"])
    rows = evaluate(itf.load_python(ledger.root), name, sender, receiver, seconds)
    failed, unknown = None, None
    for index, row in enumerate(rows):
        if row["state"] == "unknown":
            unknown = row["evidence"]
            break
        links.record_check(ledger, issue_id, index, row["state"], row["evidence"], ACTOR, source="capture")
        if row["state"] == "fail":
            failed = index
            break
    for device, parsed in ((sender_name, sender), (receiver_name, receiver)):
        for info in parsed["info"][-MAX_INFO_LINES:][:1]:
            ledger.append(ACTOR, "link.automatic", id=issue_id, text=f"Status logged by {device}: {info}")
    ledger.append(ACTOR, "link.captured", id=issue_id, failed=failed, unknown=unknown)
    return {"failed": failed, "unknown": unknown}
