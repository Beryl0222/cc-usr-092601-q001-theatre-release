"""演示种子：在指定目录生成一份可联调的样例数据。

用法：``python -m theatre_release.demo <store_dir>``
随后可运行 ``python -m theatre_release.api <store_dir>`` 查询当晚内容。
"""

from __future__ import annotations

import argparse
from datetime import datetime, time, timedelta, timezone

from .clock import SystemClock
from .service import ReleaseService
from .store import JsonlStore

_CST = timezone(timedelta(hours=8))


def seed(store_dir: str) -> dict[str, str]:
    clock = SystemClock()
    service = ReleaseService(JsonlStore(store_dir), clock)

    service.register_script("play-minning", "闽宁镇之夜")
    service.advance_stage("play-minning", "resident")
    service.add_segment("seg-1", "play-minning", 1, "我们搬出了西海固的大山。", kind="dialogue")
    service.add_segment(
        "seg-2",
        "play-minning",
        2,
        "第一晚，我们就睡在沙滩上，听着黄河的水声。",
        kind="oral_account",
        involves=["villager-xie"],
    )
    service.record_consent(
        "consent-xie",
        "villager-xie",
        "play-minning",
        withdraw_by=(clock.now() + timedelta(days=90)).isoformat(),
    )
    service.confirm_facts("seg-2", "villager-xie")

    service.submit_translation("tr-1-en", "seg-1", "en", "We moved out of the mountains of Xihaigu.", "alice")
    service.submit_translation(
        "tr-2-en", "seg-2", "en", "On our first night we slept on the sand, listening to the Yellow River.", "alice"
    )
    service.add_note("note-1-en", "seg-1", "en", "Xihaigu: former resettlement area in southern Ningxia.", "carol")

    service.submit_version(
        "ver-1",
        "play-minning",
        [
            {"segment_ref": "seg-1", "language": "en", "translation_ref": "tr-1-en", "note_refs": ["note-1-en"]},
            {"segment_ref": "seg-2", "language": "en", "translation_ref": "tr-2-en"},
        ],
        submitted_by="alice",
    )
    service.sign_version("ver-1", "translator", "dave")
    service.sign_version("ver-1", "director", "bob")
    service.sign_version("ver-1", "publisher", "carol")
    service.confirm_rehearsal("ver-1", "stage-manager")

    now = clock.now()
    start = datetime.combine(now.astimezone(_CST).date(), time(19, 30), tzinfo=_CST)
    if now >= start - timedelta(hours=1):
        start += timedelta(days=1)
    service.schedule_run("run-tonight", "play-minning", start, start - timedelta(hours=1))
    service.request_language("run-tonight", "en")
    service.lock_run("run-tonight", "ver-1")
    return {"run_id": "run-tonight", "starts_at": start.isoformat(), "store_dir": store_dir}


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description="生成演示数据")
    parser.add_argument("store_dir", help="事件日志目录")
    args = parser.parse_args(argv)
    info = seed(args.store_dir)
    print(f"已生成演示数据: {info['store_dir']}")
    print(f"今晚场次 {info['run_id']} 开演于 {info['starts_at']}")
    print(f"查询: curl 'http://127.0.0.1:8000/tonight?lang=en'  (先运行 python -m theatre_release.api {info['store_dir']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
