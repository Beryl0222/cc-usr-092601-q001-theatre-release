"""事件日志存储：JSONL 追加写，重启后整卷重放。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator


class JsonlStore:
    """以 ``journal.jsonl`` 持久化领域事件的文件存储。"""

    def __init__(self, directory: "str | os.PathLike[str]") -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "journal.jsonl"
        self._path.touch(exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def append(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, ensure_ascii=False, sort_keys=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def load(self) -> list[dict[str, Any]]:
        return list(self.iter_events())

    def iter_events(self) -> Iterator[dict[str, Any]]:
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    yield json.loads(line)


class InMemoryStore:
    """测试用内存存储，与 JsonlStore 同接口。"""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    def append(self, event: dict[str, Any]) -> None:
        self._events.append(dict(event))

    def load(self) -> list[dict[str, Any]]:
        return [dict(event) for event in self._events]

    def iter_events(self) -> Iterator[dict[str, Any]]:
        return iter(self.load())
