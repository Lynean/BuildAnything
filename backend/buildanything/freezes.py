"""Records what the server was doing whenever its main loop is stuck, so a freeze in the app can be traced to the code
that caused it instead of guessed at. Writes to _app/logs/freezes.log on this computer only."""
from __future__ import annotations

import asyncio
import sys
import threading
import time
import traceback
from pathlib import Path

STUCK_SECONDS = 2.0


class Recorder:
    def __init__(self, log: Path):
        self.log = log
        self.beat = time.monotonic()
        self.loop_thread: int | None = None
        self.reported = False

    async def heartbeat(self) -> None:
        self.loop_thread = threading.get_ident()
        while True:
            self.beat = time.monotonic()
            self.reported = False
            await asyncio.sleep(0.5)

    def watch(self) -> None:
        while True:
            time.sleep(0.5)
            stuck = time.monotonic() - self.beat
            if stuck > STUCK_SECONDS and not self.reported and self.loop_thread is not None:
                frame = sys._current_frames().get(self.loop_thread)
                if frame is not None:
                    self.reported = True
                    stack = "".join(traceback.format_stack(frame)[-14:])
                    try:
                        self.log.parent.mkdir(parents=True, exist_ok=True)
                        with self.log.open("a", encoding="utf-8") as f:
                            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} main loop stuck for {stuck:.1f}s, doing:\n{stack}\n")
                    except OSError:
                        pass


def start(log: Path) -> Recorder:
    recorder = Recorder(log)
    threading.Thread(target=recorder.watch, name="freeze-recorder", daemon=True).start()
    asyncio.get_running_loop().create_task(recorder.heartbeat())
    return recorder
