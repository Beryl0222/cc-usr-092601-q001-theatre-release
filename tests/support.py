"""测试共用的搭景工具：一个含普通台词与两段亲历者口述的小剧目。"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

from theatre_release.clock import SettableClock
from theatre_release.service import TheatreService
from theatre_release.store import EventStore

TZ = timezone(timedelta(hours=8))

SCRIPT_ID = "script-minning"
SUBJECT_LI = "li-anhua"
SUBJECT_MA = "ma-defu"


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


def make_service(start: datetime | None = None) -> tuple[TheatreService, str]:
    tmp = tempfile.mkdtemp(prefix="theatre-test-")
    store = EventStore(os.path.join(tmp, "events.jsonl"))
    service = TheatreService(store, SettableClock(start or at(1, 9)))
    return service, tmp


def seed_script(service: TheatreService) -> None:
    service.register_script(
        SCRIPT_ID,
        "闽宁纪事",
        "zh-CN",
        [
            {"segment_id": "seg-line", "kind": "line", "subject_ref": None, "text": "村口的戏台搭起来了。"},
            {
                "segment_id": "seg-li",
                "kind": "testimony",
                "subject_ref": SUBJECT_LI,
                "text": "我是一九九七年从西海固搬下来的。",
            },
            {
                "segment_id": "seg-ma",
                "kind": "testimony",
                "subject_ref": SUBJECT_MA,
                "text": "当年的蘑菇棚是我带头建的。",
            },
        ],
        "evt-script",
    )


def seed_consents(service: TheatreService, *, withdrawal_deadline: str | None = None) -> None:
    extra = {"withdrawal_deadline": withdrawal_deadline} if withdrawal_deadline else {}
    service.record_consent(
        "consent-li", SUBJECT_LI,
        {"script_id": SCRIPT_ID, "segment_ids": ["seg-li"], **extra},
        SUBJECT_LI, "evt-consent-li",
    )
    service.record_consent(
        "consent-ma", SUBJECT_MA,
        {"script_id": SCRIPT_ID, "segment_ids": ["seg-ma"], **extra},
        SUBJECT_MA, "evt-consent-ma",
    )


def english_entries(*segment_ids: str) -> list[dict[str, str]]:
    texts = {
        "seg-line": "The village stage has gone up.",
        "seg-li": "I moved down from Xihaigu in 1997.",
        "seg-ma": "I led the building of the mushroom sheds back then.",
    }
    return [{"segment_id": sid, "text": texts[sid]} for sid in segment_ids]


def propose_sign_approve(
    service: TheatreService,
    version_id: str,
    *,
    entries: list[dict[str, str]] | None = None,
    language: str = "en",
    translator: str = "trans-a",
    director: str = "dir-b",
    publisher: str = "pub-c",
    prefix: str = "v1",
    confirmations: dict[str, list[str]] | None = None,
) -> str:
    entries = entries if entries is not None else english_entries("seg-line", "seg-li", "seg-ma")
    subjects = {sid: subj for sid, subj in (("seg-li", SUBJECT_LI), ("seg-ma", SUBJECT_MA))}
    service.propose_version(version_id, SCRIPT_ID, language, translator, entries, [], f"evt-{prefix}-propose")
    needed = {subjects[e["segment_id"]] for e in entries if e["segment_id"] in subjects}
    for subject in sorted(needed):
        service.confirm_rehearsal(
            version_id, subject, subject,
            [sid for sid in (confirmations or {}).get(subject, []) if sid] or
            [e["segment_id"] for e in entries if subjects.get(e["segment_id"]) == subject],
            f"evt-{prefix}-confirm-{subject}",
        )
    service.sign_version(version_id, "translator", translator, f"evt-{prefix}-sign-t")
    service.sign_version(version_id, "director", director, f"evt-{prefix}-sign-d")
    service.sign_version(version_id, "publisher", publisher, f"evt-{prefix}-sign-p")
    service.approve_version(version_id, publisher, f"evt-{prefix}-approve")
    return version_id
