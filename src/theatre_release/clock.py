"""可控时钟。

领域服务只依赖 ``Clock.now()``：生产用 ``SystemClock``，测试与"截稿/开演/撤回"
推演用 ``SettableClock`` 固定或拨动时间。重启恢复不依赖墙钟以外的状态。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

# 闽宁镇位于东八区；默认墙钟也显式带时区，杜绝朴素时间。
LOCAL_TZ = timezone(timedelta(hours=8))


class Clock(Protocol):
    def now(self) -> datetime: ...


def parse_datetime(value: str) -> datetime:
    """解析 ISO 8601，并强制要求显式时区。"""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"时间缺少显式时区: {value}")
    return parsed


class SystemClock:
    """真实墙钟，始终返回带时区的当前时间。"""

    def __init__(self, tz: timezone = LOCAL_TZ) -> None:
        self._tz = tz

    def now(self) -> datetime:
        return datetime.now(self._tz)


@dataclass
class SettableClock:
    """测试与运维推演用时钟：时间只能被显式设置或向前拨动。"""

    current: datetime

    def __post_init__(self) -> None:
        if self.current.tzinfo is None or self.current.utcoffset() is None:
            raise ValueError("SettableClock 的初始时间必须显式携带时区")

    def now(self) -> datetime:
        return self.current

    def set(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("设置的时间必须显式携带时区")
        self.current = value

    def advance(self, **delta: float) -> None:
        self.current = self.current + timedelta(**delta)
