"""Runs a slow tool off the event loop, so the app keeps answering while it works."""
from __future__ import annotations

import asyncio
import functools
from typing import Any


def _threaded(tool: Any) -> Any:
    @functools.wraps(tool)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(tool, *args, **kwargs)

    return wrapped
