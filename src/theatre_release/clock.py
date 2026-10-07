"""可控时钟：截稿、开演与撤回期限的统一时间来源。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def ensure_aware(value: "str | datetime") -> datetime:
    """把 ISO 字符串或 datetime 归一化为带时区的时间，拒绝缺失时区的输入。"""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"时间格式无效: {value!r}") from exc
    else:
        raise TypeError(f"不支持的时间类型: {type(value).__name__}")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("时间必须携带时区")
    return parsed


class SystemClock:
    """生产环境时钟，返回 UTC 当前时间。"""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class ManualClock:
    """测试与演练用时钟，可定点、可推进。"""

    def __init__(self, start: "str | datetime") -> None:
        self._now = ensure_aware(start)

    def now(self) -> datetime:
        return self._now

    def set(self, value: "str | datetime") -> None:
        self._now = ensure_aware(value)

    def advance(self, **kwargs) -> None:
        self._now = self._now + timedelta(**kwargs)
