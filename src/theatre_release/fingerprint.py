"""文本与内容指纹。

- 译文指纹用于场次回执的幂等判定：编号相同而指纹不同即争议。
- 原文指纹挂在译文版本上，用于勘误链路与审计还原。
规范化只做 Unicode NFKC 与换行统一，不删改任何可见字符。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
import unicodedata


def canonical_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).replace("\r\n", "\n").replace("\r", "\n")


def _normalize(obj: Any) -> Any:
    if isinstance(obj, str):
        return canonical_text(obj)
    if isinstance(obj, dict):
        return {key: _normalize(obj[key]) for key in sorted(obj)}
    if isinstance(obj, list):
        return [_normalize(item) for item in obj]
    return obj


def content_fingerprint(obj: Any) -> str:
    """对任意 JSON 可序列化内容计算稳定的 sha256 指纹。"""
    blob = json.dumps(
        _normalize(obj),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
