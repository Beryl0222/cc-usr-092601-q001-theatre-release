"""领域事件类型常量与文本指纹工具。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

# 剧目阶段：创作 -> 排练 -> 驻演 -> 封存
SCRIPT_STAGES = ("draft", "rehearsal", "resident", "archived")

# 会签角色：译者、导演、发布人员
SIGNING_ROLES = ("translator", "director", "publisher")

# 原文段落类型；oral_account 为亲历者口述，需授权才可对观众展示
SEGMENT_KINDS = ("dialogue", "narration", "oral_account")

EVENT_TYPES = frozenset({
    "SCRIPT_REGISTERED",
    "STAGE_ADVANCED",
    "SEGMENT_ADDED",
    "FACT_CONFIRMED",
    "CONSENT_RECORDED",
    "CONSENT_WITHDRAWN",
    "TRANSLATION_SUBMITTED",
    "NOTE_ADDED",
    "VERSION_SUBMITTED",
    "VERSION_SIGNED",
    "VERSION_APPROVED",
    "REHEARSAL_CONFIRMED",
    "RUN_SCHEDULED",
    "LANGUAGE_REQUESTED",
    "RUN_LOCKED",
    "RUN_REVISED",
    "LIVE_CHANGE_RECORDED",
    "RECEIPT_RECORDED",
    "DISPUTE_RAISED",
    "ERRATA_ISSUED",
})


def fingerprint_text(text: str) -> str:
    """单段文本指纹：UTF-8 原文的 SHA-256。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fingerprint_content(rows: Iterable[Any]) -> str:
    """整场内容的规范指纹：对排序后的行做确定性 JSON 序列化再取哈希。"""
    canonical = json.dumps(list(rows), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
