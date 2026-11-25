"""Flashing a real board. No agent has a route to this, and a person approves every flash.

Order: check the chip, build, show the plan, then on approval back up, flash and listen.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import serial
import serial.tools.list_ports

from . import connectors as conn, debugging, firmware as fw
from .ledger import Actor, Ledger, LedgerError

ESPRESSIF_USB = 0x303a
BACKUP_FILE = re.compile(r"^([a-z0-9]+)-([0-9a-f]{12})-[0-9a-zA-Z_-]*full\.bin$")
APP_OFFSET = "0x10000"
CRASH = re.compile(r"guru meditation|abort\(\) was called|backtrace:|panic|stack smashing|brownout|hard fault", re.I)
CONNECTION_FAILED = re.compile(r"failed to connect|no more data|no serial data|no sync reply|serial noise|timed out waiting", re.I)
UPLOAD_ATTEMPTS = 3
CONNECTION_HELP = "The board didn't answer. Hold BOOT, tap RESET and let go of RESET, then keep holding BOOT and try again. Some boards need BOOT pressed again just as flashing starts. Be careful not to hold RESET by mistake. If it keeps failing, try another data cable, because this usually means an unreliable USB connection."
BOOT_LINE = re.compile(r"^(ESP-ROM:|rst:0x|ets [A-Z])")
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


class FlashError(LedgerError):
    pass


def ports() -> list[dict[str, Any]]:
    """USB serial ports only. Bluetooth entries have no USB ID, and flashing to one would just hang."""
    return [{"port": p.device, "vid": p.vid, "pid": p.pid, "description": p.description} for p in sorted(serial.tools.list_ports.comports(), key=lambda x: x.device) if p.vid]


def _lock(port: str):
    with _guard:
        lock = _locks.setdefault(port, threading.Lock())
    if not lock.acquire(blocking=False):
        raise FlashError(f"{port} is already being used by another flash.")
    return lock


def _esptool(port: str, *args: str, baud: int | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    speed = ["--baud", str(baud)] if baud else []
    return subprocess.run([sys.executable, "-m", "esptool", "--port", port, *speed, *args], capture_output=True, text=True, timeout=timeout)


def _text(done: subprocess.CompletedProcess) -> str:
    return (done.stdout or "") + (done.stderr or "")


def _check_port(output: str, port: str) -> None:
    if re.search(r"could not open port|PermissionError|Access is denied|port is busy", output, re.I):
        raise FlashError(f"{port} is open in another program, such as a serial monitor. Close it and try again.")


def identify(port: str) -> dict[str, Any] | None:
    """Only Espressif chips answer this. None means it isn't one, or isn't responding."""
    done = _esptool(port, "flash-id", timeout=60)
    output = _text(done)
    _check_port(output, port)
    chip = re.search(r"Detecting chip type\.\.\. (\S+)", output)
    if done.returncode != 0 or not chip:
        return None
    mac = re.search(r"^BASE MAC:\s*(\S+)", output, re.M) or re.search(r"^MAC:\s*(\S+)", output, re.M)
    size = re.search(r"Detected flash size:\s*(\d+)MB", output)
    return {"chip": chip.group(1), "mac": mac.group(1).replace(":", "").lower() if mac else "", "flash_bytes": int(size.group(1)) * 1024 * 1024 if size else None}


def chip_matches(chip: str, mcu: str) -> bool:
    have, wanted = re.sub(r"[^A-Z0-9]", "", chip.upper()), re.sub(r"[^A-Z0-9]", "", mcu.upper())
    return have == wanted


def source_hash(base: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in base.rglob("*") if p.is_file() and ".pio" not in p.parts):
        digest.update(path.relative_to(base).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _binary(root: Path, device: str) -> Path:
    return root / "firmware" / device / ".pio" / "build" / "device" / "firmware.bin"


def prepare(ledger: Ledger, root: Path, device: str, port: str) -> dict[str, Any]:
    config = fw.device_config(root, device)
    if config is None:
        raise FlashError("Choose this device's board first.")
    found = next((p for p in ports() if p["port"] == port), None)
    if found is None:
        raise FlashError(f"{port} isn't connected. Check the USB cable, then refresh the list.")
    lock = _lock(port)
    try:
        board = identify(port)
        info = fw.find_board(config["board"])
        if board and info and not chip_matches(board["chip"], info["mcu"]):
            raise FlashError(f"{port} has an {board['chip']}, but this device is set to {info['name']} ({info['mcu']}). Nothing was changed.")
        if config["platform"] == "espressif32" and board is None:
            raise FlashError(f"No ESP32 answered on {port}. Hold BOOT while pressing RESET, or check the cable, then try again.")
        fw.set_native_usb(root, device, found["vid"] == ESPRESSIF_USB and board is not None, board["chip"] if board else "")
    finally:
        lock.release()
    built = fw.build_and_track(ledger, root, device)
    if not built["ok"]:
        return {"ready": False, "build": built}
    binary = _binary(root, device)
    if not binary.exists():
        raise FlashError("The build finished, but no firmware file was produced.")
    files = fw.scan_workspace(root, device)
    undescribed = sum(1 for f in files for b in f["blocks"] if b["state"] != "documented") + sum(1 for f in files if not f["blocks"] and not f.get("generated"))
    prepared = {
        "id": f"fls_{uuid.uuid4().hex[:10]}", "device": device, "port": port, "port_name": found["description"], "board": config["board"], "chip": board["chip"] if board else None, "mac": board["mac"] if board else None,
        "flash_bytes": board["flash_bytes"] if board else None, "size": binary.stat().st_size, "binary_sha": hashlib.sha256(binary.read_bytes()).hexdigest(), "source_sha": source_hash(root / "firmware" / device),
        "can_back_up": board is not None, "undescribed": undescribed,
    }
    ledger.append(Actor.system("flashing"), "flash.prepared", **prepared)
    return {"ready": True, "build": built, "plan": prepared}


def _prepared(ledger: Ledger, flash_id: str) -> dict[str, Any]:
    events = ledger.events()
    if any(e["kind"] == "flash.approved" and e["data"]["id"] == flash_id for e in events):
        raise FlashError("This plan was already used. Prepare a new one.")
    plan = next((e["data"] for e in events if e["kind"] == "flash.prepared" and e["data"]["id"] == flash_id), None)
    if plan is None:
        raise FlashError("That flash plan wasn't found.")
    return plan


def _back_up(port: str, board: dict[str, Any], backups: Path) -> tuple[str | None, str]:
    """Skips the backup only when the board still has exactly the firmware this app last put on it."""
    backups.mkdir(parents=True, exist_ok=True)
    last = backups / f"{board['mac']}-last.bin"
    if last.exists() and _esptool(port, "verify-flash", APP_OFFSET, str(last), timeout=90).returncode == 0:
        return None, "Not needed: the board still has the firmware this app last flashed."
    if not board["flash_bytes"]:
        raise FlashError("The flash size couldn't be read, so no backup could be made. Nothing was changed.")
    stamp = dt.datetime.now().strftime("%Y-%m-%d-%H%M%S")
    target = backups / f"{re.sub(r'[^a-z0-9]', '', board['chip'].lower())}-{board['mac']}-{stamp}-full.bin"
    for baud in (230400, 115200):
        target.unlink(missing_ok=True)
        done = _esptool(port, "read-flash", "0", hex(board["flash_bytes"]), str(target), baud=baud, timeout=900)
        if done.returncode == 0 and target.exists() and target.stat().st_size == board["flash_bytes"] and _esptool(port, "verify-flash", "0", str(target), timeout=180).returncode == 0:
            return target.name, f"Saved and checked against the chip: {target.name}"
    target.unlink(missing_ok=True)
    raise FlashError("The backup failed, so nothing was flashed. Try a different USB cable or port.")


def analyse_output(lines: list[str]) -> str:
    if any(CRASH.search(line) for line in lines) or sum(1 for line in lines if BOOT_LINE.match(line)) >= 4:
        return "crashing"
    return "running" if any(line.strip() for line in lines) else "no_output"


def listen(port: str, seconds: float = 6.0) -> list[str]:
    device = serial.Serial()
    device.port, device.baudrate, device.timeout, device.dtr, device.rts = port, 115200, 0.2, False, False
    data = b""
    for _ in range(25):  # Native USB ports disappear briefly while the board restarts.
        try:
            device.open()
            break
        except (serial.SerialException, OSError):
            time.sleep(0.4)
    else:
        return []
    try:
        end = time.time() + seconds
        while time.time() < end:
            data += device.read(4096)
    except (serial.SerialException, OSError):
        pass
    finally:
        device.close()
    return data.decode("utf-8", errors="replace").splitlines()


def approve(ledger: Ledger, root: Path, backups: Path, device: str, flash_id: str, confirmed: bool, accept_no_backup: bool, actor: Actor) -> dict[str, Any]:
    plan = _prepared(ledger, flash_id)
    if plan["device"] != device:
        raise FlashError("That plan is for another device.")
    if not confirmed:
        raise FlashError("Confirm it's safe for this board to restart.")
    if not plan["can_back_up"] and not accept_no_backup:
        raise FlashError("This board can't be backed up. Confirm you accept that first.")
    base = root / "firmware" / device
    binary = _binary(root, device)
    if source_hash(base) != plan["source_sha"] or not binary.exists() or hashlib.sha256(binary.read_bytes()).hexdigest() != plan["binary_sha"]:
        raise FlashError("The code changed after this plan was made. Prepare it again.")
    lock = _lock(plan["port"])
    try:
        board = identify(plan["port"]) if plan["can_back_up"] else None
        if plan["can_back_up"] and (board is None or board["mac"] != plan["mac"]):
            raise FlashError(f"A different board is now on {plan['port']}. Nothing was changed.")
        ledger.append(actor, "flash.approved", id=flash_id, device=device, port=plan["port"], mac=plan["mac"], binary_sha=plan["binary_sha"])
        backup_file, backup_note = _back_up(plan["port"], board, backups) if board else (None, "No backup: this kind of board can't be read.")
        info = conn.detect_cli("pio")
        if not info["found"]:
            raise FlashError("PlatformIO isn't connected.")
        attempts = 0
        for attempts in range(1, UPLOAD_ATTEMPTS + 1):
            done = subprocess.run([info["path"], "run", "-t", "upload", "--upload-port", plan["port"]], cwd=base, capture_output=True, text=True, timeout=600, env=conn.clean_environment())
            if done.returncode == 0 or not CONNECTION_FAILED.search(_text(done)):
                break
            time.sleep(1.5)
        if done.returncode != 0:
            detail = fw.error_lines(_text(done)) or [line[:300] for line in _text(done).splitlines() if line.strip()][-4:]
            guidance = CONNECTION_HELP if CONNECTION_FAILED.search(_text(done)) else ""
            ledger.append(Actor.system("flashing"), "flash.finished", id=flash_id, device=device, outcome="flash_failed", backup=backup_file, lines=detail, attempts=attempts)
            return {"outcome": "flash_failed", "backup": backup_note, "lines": detail, "attempts": attempts, "help": guidance}
        if board:
            backups.mkdir(parents=True, exist_ok=True)
            shutil.copy(binary, backups / f"{board['mac']}-last.bin")
        lines = listen(plan["port"])
    finally:
        lock.release()
    outcome = analyse_output(lines)
    kept = lines[:40]
    ledger.append(Actor.system("flashing"), "flash.finished", id=flash_id, device=device, outcome=outcome, backup=backup_file, lines=kept)
    if outcome == "crashing":
        debugging.open_or_repeat(ledger, "Crash after flashing", "\n".join(l for l in kept if CRASH.search(l) or BOOT_LINE.match(l))[:800] or "The board keeps restarting.", "code", Actor.system("flashing"), device=device, where=device)
    return {"outcome": outcome, "backup": backup_note, "lines": kept, "attempts": attempts}


def list_backups(backups: Path, mac: str | None = None) -> list[dict[str, Any]]:
    out = []
    for path in sorted(backups.glob("*-full.bin")) if backups.exists() else []:
        match = BACKUP_FILE.match(path.name)
        if match and (mac is None or match.group(2) == mac):
            out.append({"file": path.name, "chip": match.group(1), "mac": match.group(2), "size": path.stat().st_size})
    return out


def restore(ledger: Ledger, backups: Path, file: str, port: str, confirmed: bool, actor: Actor) -> dict[str, Any]:
    match = BACKUP_FILE.match(file)
    target = backups / file
    if not match or not target.is_file():
        raise FlashError("That backup wasn't found.")
    if not confirmed:
        raise FlashError("Confirm it's safe for this board to restart.")
    lock = _lock(port)
    try:
        board = identify(port)
        if board is None or board["mac"] != match.group(2):
            raise FlashError(f"That backup belongs to a different board from the one on {port}. Nothing was changed.")
        ledger.append(actor, "flash.restored", file=file, port=port, mac=board["mac"])
        done = _esptool(port, "write-flash", "0", str(target), baud=230400, timeout=900)
        if done.returncode != 0:
            return {"outcome": "flash_failed", "lines": [line[:300] for line in _text(done).splitlines() if line.strip()][-4:]}
        (backups / f"{board['mac']}-last.bin").unlink(missing_ok=True)  # The board no longer has the app's firmware.
        lines = listen(port)
    finally:
        lock.release()
    return {"outcome": analyse_output(lines), "lines": lines[:40]}


def history(ledger: Ledger, device: str) -> list[dict[str, Any]]:
    return [{**e["data"], "at": e["at"]} for e in ledger.events() if e["kind"] == "flash.finished" and e["data"]["device"] == device][-5:]
