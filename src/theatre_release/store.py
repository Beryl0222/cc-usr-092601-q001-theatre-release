"""JSONL 追加事件存储。

- 每个事件一行 JSON，只追加、不改写；重启后重新读入即可继续未完成会签。
- ``event_id`` 全局幂等：相同编号 + 相同载荷直接返回旧事件；编号相同而内容不同报错。
- 同一聚合的版本号必须严格递增。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Mapping

from .errors import ConflictError


class EventStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._events: list[dict] = []
        self._by_id: dict[str, dict] = {}
        self._reload()

    def _reload(self) -> None:
        self._events = []
        self._by_id = {}
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                event = json.loads(line)
                self._index(event)

    def _index(self, event: dict) -> None:
        event_id = event["event_id"]
        if event_id in self._by_id:
            raise ConflictError(f"事件编号重复: {event_id}")
        self._by_id[event_id] = event
        self._events.append(event)

    @property
    def events(self) -> list[dict]:
        with self._lock:
            return list(self._events)

    def get(self, event_id: str) -> dict | None:
        with self._lock:
            return self._by_id.get(event_id)

    def next_version(self, aggregate_type: str, aggregate_id: str) -> int:
        """该聚合下一个事件版本号（不存在则为 1）。"""
        with self._lock:
            return 1 + max(
                (
                    e["version"]
                    for e in self._events
                    if e["aggregate_type"] == aggregate_type and e["aggregate_id"] == aggregate_id
                ),
                default=0,
            )

    def append(self, event: Mapping) -> dict:
        """追加事件；相同 event_id 且内容一致时幂等返回旧事件。"""
        event = dict(event)
        event_id = event["event_id"]
        with self._lock:
            existing = self._by_id.get(event_id)
            if existing is not None:
                if existing == event:
                    return existing
                raise ConflictError(f"事件编号 {event_id} 已用于不同内容")
            key = (event["aggregate_type"], event["aggregate_id"])
            expected = max(
                (e["version"] for e in self._events if (e["aggregate_type"], e["aggregate_id"]) == key),
                default=0,
            ) + 1
            if event["version"] != expected:
                raise ConflictError(
                    f"聚合 {key} 版本冲突：期望 {expected}，收到 {event['version']}"
                )
            encoded = json.dumps(event, ensure_ascii=False)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(encoded + "\n")
                handle.flush()
            self._index(event)
            return event
