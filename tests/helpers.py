"""服务测试共用的场景搭建。"""

from __future__ import annotations

from theatre_release.service import ReleaseService

RUN_START = "2026-10-07T19:30:00+08:00"
RUN_CUTOFF = "2026-10-05T18:00:00+08:00"
WITHDRAW_BY = "2026-12-31T23:59:59+08:00"
ORAL_TEXT = "第一晚我们睡在沙滩上。"
ORAL_TRANSLATION = "On our first night we slept on the sand."
SEG1_TEXT = "我们搬出了大山。"
SEG1_TRANSLATION = "We moved out of the mountains."
NOTE_TEXT = "西海固：宁夏南部曾经的移民迁出区。"


def seed_script(service: ReleaseService) -> None:
    service.register_script("play-1", "闽宁镇之夜")
    service.advance_stage("play-1", "resident")
    service.add_segment("seg-1", "play-1", 1, SEG1_TEXT, kind="dialogue")
    service.add_segment("seg-2", "play-1", 2, ORAL_TEXT, kind="oral_account", involves=["villager-ma"])


def seed_consent(service: ReleaseService, withdraw_by: str = WITHDRAW_BY) -> None:
    service.record_consent("consent-1", "villager-ma", "play-1", withdraw_by)


def sign_all(service: ReleaseService, version_id: str) -> None:
    service.sign_version(version_id, "translator", "dave")
    service.sign_version(version_id, "director", "bob")
    service.sign_version(version_id, "publisher", "carol")


def seed_version(
    service: ReleaseService,
    version_id: str,
    seg1_text: str = SEG1_TRANSLATION,
    seg2_text: str = ORAL_TRANSLATION,
    sign: bool = True,
) -> None:
    service.submit_translation(f"tr-1-{version_id}", "seg-1", "en", seg1_text, "alice")
    service.submit_translation(f"tr-2-{version_id}", "seg-2", "en", seg2_text, "alice")
    service.add_note(f"note-1-{version_id}", "seg-1", "en", NOTE_TEXT, "carol")
    service.submit_version(
        version_id,
        "play-1",
        [
            {
                "segment_ref": "seg-1",
                "language": "en",
                "translation_ref": f"tr-1-{version_id}",
                "note_refs": [f"note-1-{version_id}"],
            },
            {"segment_ref": "seg-2", "language": "en", "translation_ref": f"tr-2-{version_id}"},
        ],
        submitted_by="alice",
    )
    if sign:
        sign_all(service, version_id)
        service.confirm_rehearsal(version_id, "stage-manager")


def seed_run(service: ReleaseService, run_id: str = "run-1") -> None:
    service.schedule_run(run_id, "play-1", RUN_START, RUN_CUTOFF)
    service.request_language(run_id, "en")


def build_locked_fixture(service: ReleaseService) -> None:
    """完整链路：剧目、授权、会签版本、排期并锁定 run-1。"""
    seed_script(service)
    seed_consent(service)
    seed_version(service, "ver-1")
    seed_run(service)
    service.lock_run("run-1", "ver-1")
