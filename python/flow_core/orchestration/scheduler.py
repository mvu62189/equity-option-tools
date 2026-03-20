from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


def _seconds_until(target: time, timezone: str) -> float:
    now = datetime.now(ZoneInfo(timezone))
    target_dt = now.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    if target_dt <= now:
        target_dt += timedelta(days=1)
    return (target_dt - now).total_seconds()


async def run_eod_scheduler(
    callback,
    target_time: str = "20:00",
    timezone: str = "America/New_York",
) -> None:
    hour, minute = [int(x) for x in target_time.split(":", maxsplit=1)]
    target = time(hour=hour, minute=minute)

    while True:
        await asyncio.sleep(_seconds_until(target, timezone))
        await callback()
