"""Wall-clock implementation of the Clock port, for real robots and for adapters without a simulated time."""

from __future__ import annotations

import asyncio
import time


class WallClock:
    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
