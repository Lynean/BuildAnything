"""Sends one real packet to the real board, using the project's generated code, and listens for it."""
import importlib.util
import json
import pathlib
import sys
import time

import serial

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "backend"))
from buildanything import firmware as fw, interfaces as itf  # noqa: E402
from buildanything.ledger import Ledger  # noqa: E402

root, port = pathlib.Path(sys.argv[1]), sys.argv[2]
led = Ledger(root)
packets = itf.packets(led.state())
computer = next((d for d in fw.devices(root) if fw.is_computer(root, d)), None)
out = {"received": False, "detail": ""}
if not packets or not computer:
    out["detail"] = "No packet or no computer device."
else:
    packet = next((p for p in packets if p["producer"] == computer), packets[0])
    spec = importlib.util.spec_from_file_location("packets", root / "firmware" / computer / "generated" / "packets.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["packets"] = module
    spec.loader.exec_module(module)
    cls = getattr(module, itf.gen.class_name(packet["name"]))
    now = time.localtime()
    CLOCK = {"hours": now.tm_hour, "minutes": now.tm_min, "seconds": now.tm_sec, "hour": now.tm_hour, "minute": now.tm_min, "second": now.tm_sec, "year": now.tm_year, "month": now.tm_mon, "day": now.tm_mday,
             "seconds_since_midnight": now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec, "unix_time": int(time.time()), "epoch_seconds": int(time.time()), "timestamp": int(time.time())}

    def value(field):
        kind = field["type"]
        if "bytes" in kind:  # A two-digit text field is filled with the real time, so a real packet also sets the clock.
            size = int(kind[kind.index("[") + 1:kind.index("]")]) if "[" in kind else 2
            if "time" in field["name"].lower() and size in (8, 9):  # A whole HH:MM:SS field, with or without its line ending.
                return (time.strftime("%H:%M:%S", now) + ("\n" if size == 9 else "")).encode()
            return (f"{CLOCK[field['name']]:0{size}d}" if field["name"] in CLOCK else "0" * size).encode()
        return 0.0 if "float" in kind else CLOCK.get(field["name"], 0)

    values = {f["name"]: value(f) for f in packet["fields"] if f["name"] not in ("pkt_type", "schema", "crc", "checksum") and "const" not in f}
    frame = module.serial_frame(cls(**values).encode())
    link = serial.Serial(port, 115200, timeout=0.2)
    time.sleep(1.5)
    link.reset_input_buffer()
    link.write(frame)
    end, seen = time.time() + 3, b""
    while time.time() < end:
        seen += link.read(800)
    link.close()
    text = seen.decode(errors="replace")
    lines = [line for line in text.splitlines() if " rx " in f" {line} " or "rx" in line.split()[:3] or line.startswith("@pkt ")]
    out["received"] = bool(lines)
    out["detail"] = (lines[0] if lines else text[-220:] or "The board said nothing.")[:220]
print(json.dumps(out))
