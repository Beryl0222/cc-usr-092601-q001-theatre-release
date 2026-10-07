import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from support import (  # noqa: E402
    SCRIPT_ID,
    SUBJECT_LI,
    SUBJECT_MA,
    at,
    english_entries,
    make_service,
    propose_sign_approve,
    seed_consents,
    seed_script,
)
from theatre_release.clock import SettableClock  # noqa: E402
from theatre_release.errors import (  # noqa: E402
    AuthorizationError,
    ConflictError,
    DeadlineError,
    NotFoundError,
    StateError,
)
from theatre_release.service import DisputeRaised, TheatreService  # noqa: E402
from theatre_release.store import EventStore  # noqa: E402


class ApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service, _ = make_service()
        seed_script(self.service)
        seed_consents(self.service)

    def test_three_distinct_roles_required(self) -> None:
        self.service.propose_version("v1", SCRIPT_ID, "en", "trans-a", english_entries("seg-line"), [], "p1")
        self.service.sign_version("v1", "translator", "trans-a", "s1")
        self.service.sign_version("v1", "director", "dir-b", "s2")
        with self.assertRaises(StateError):  # 缺发布会签
            self.service.approve_version("v1", "pub-c", "a1")

    def test_one_person_cannot_hold_two_roles(self) -> None:
        self.service.propose_version("v1", SCRIPT_ID, "en", "trans-a", english_entries("seg-line"), [], "p1")
        self.service.sign_version("v1", "translator", "trans-a", "s1")
        with self.assertRaises(AuthorizationError):
            self.service.sign_version("v1", "director", "trans-a", "s2")

    def test_proposer_cannot_be_final_approver(self) -> None:
        # 提交人不占译者席位、只完成发布会签：会签可完成，终审被职责分离拦截。
        self.service.propose_version("v1", SCRIPT_ID, "en", "boss-a", english_entries("seg-line"), [], "p1")
        self.service.sign_version("v1", "translator", "trans-a", "s1")
        self.service.sign_version("v1", "director", "dir-b", "s2")
        self.service.sign_version("v1", "publisher", "boss-a", "s3")
        with self.assertRaises(AuthorizationError):
            self.service.approve_version("v1", "boss-a", "a1")

    def test_only_publisher_may_give_final_approval(self) -> None:
        self.service.propose_version("v1", SCRIPT_ID, "en", "trans-a", english_entries("seg-line"), [], "p1")
        self.service.sign_version("v1", "translator", "trans-a", "s1")
        self.service.sign_version("v1", "director", "dir-b", "s2")
        self.service.sign_version("v1", "publisher", "pub-c", "s3")
        with self.assertRaises(AuthorizationError):
            self.service.approve_version("v1", "dir-b", "a1")

    def test_duplicate_signature_is_conflict(self) -> None:
        self.service.propose_version("v1", SCRIPT_ID, "en", "trans-a", english_entries("seg-line"), [], "p1")
        self.service.sign_version("v1", "translator", "trans-a", "s1")
        with self.assertRaises(ConflictError):
            self.service.sign_version("v1", "translator", "trans-other", "s1b")


class ConsentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service, _ = make_service()
        seed_script(self.service)

    def test_subject_must_record_own_consent(self) -> None:
        with self.assertRaises(AuthorizationError):
            self.service.record_consent(
                "c1", SUBJECT_LI, {"script_id": SCRIPT_ID, "segment_ids": ["seg-li"]}, "director", "c"
            )

    def test_testimony_requires_consent_and_rehearsal_confirmation(self) -> None:
        seed_consents(self.service)
        entries = english_entries("seg-li")
        self.service.propose_version("v1", SCRIPT_ID, "en", "trans-a", entries, [], "p")
        self.service.sign_version("v1", "translator", "trans-a", "s1")
        self.service.sign_version("v1", "director", "dir-b", "s2")
        self.service.sign_version("v1", "publisher", "pub-c", "s3")
        with self.assertRaises(AuthorizationError):  # 未做排练确认
            self.service.approve_version("v1", "pub-c", "a")
        self.service.confirm_rehearsal("v1", SUBJECT_LI, SUBJECT_LI, ["seg-li"], "cf")
        self.service.approve_version("v1", "pub-c", "a2")

    def test_subject_cannot_confirm_other_subjects_fact(self) -> None:
        seed_consents(self.service)
        self.service.propose_version(
            "v1", SCRIPT_ID, "en", "trans-a", english_entries("seg-li", "seg-ma"), [], "p"
        )
        with self.assertRaises(AuthorizationError):
            self.service.confirm_rehearsal("v1", SUBJECT_MA, SUBJECT_MA, ["seg-li"], "cf")

    def test_withdrawn_consent_blocks_approval_and_is_hidden(self) -> None:
        self.service.record_consent(
            "consent-li", SUBJECT_LI, {"script_id": SCRIPT_ID, "segment_ids": ["seg-li"]}, SUBJECT_LI, "c1"
        )
        self.service.propose_version("v1", SCRIPT_ID, "en", "trans-a", english_entries("seg-li"), [], "p")
        self.service.confirm_rehearsal("v1", SUBJECT_LI, SUBJECT_LI, ["seg-li"], "cf")
        self.service.withdraw_consent("consent-li", SUBJECT_LI, "w1")
        self.service.sign_version("v1", "translator", "trans-a", "s1")
        self.service.sign_version("v1", "director", "dir-b", "s2")
        self.service.sign_version("v1", "publisher", "pub-c", "s3")
        with self.assertRaises(AuthorizationError):
            self.service.approve_version("v1", "pub-c", "a")

    def test_withdrawal_after_deadline_rejected(self) -> None:
        self.service.record_consent(
            "c1", SUBJECT_LI,
            {"script_id": SCRIPT_ID, "segment_ids": ["seg-li"], "withdrawal_deadline": "2026-10-05T18:00:00+08:00"},
            SUBJECT_LI, "c",
        )
        self.service.clock.set(at(5, 18, 1))
        with self.assertRaises(DeadlineError):
            self.service.withdraw_consent("c1", SUBJECT_LI, "w")


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service, self.tmp = make_service()
        seed_script(self.service)
        seed_consents(self.service)
        propose_sign_approve(self.service, "v1")

    def _plan_lock(self, run_id: str = "run-1001", languages=("en",)):
        self.service.plan_run(
            run_id, SCRIPT_ID, "2026-10-01T20:00:00+08:00", "2026-10-01T18:00:00+08:00",
            list(languages), f"plan-{run_id}",
        )
        self.service.clock.set(at(1, 17))
        self.service.lock_run(run_id, {lang: "v1" for lang in languages}, "sm", f"lock-{run_id}")

    def test_lock_after_deadline_rejected(self) -> None:
        self.service.plan_run(
            "r", SCRIPT_ID, "2026-10-01T20:00:00+08:00", "2026-10-01T18:00:00+08:00", ["en"], "plan"
        )
        self.service.clock.set(at(1, 18, 1))
        with self.assertRaises(DeadlineError):
            self.service.lock_run("r", {"en": "v1"}, "sm", "lock")

    def test_adjustment_window_closes_at_start(self) -> None:
        self._plan_lock()
        self.service.clock.set(at(1, 19))
        self.service.adjust_run(
            "run-1001",
            [{"op": "redact_segment", "language": "en", "segment_id": "seg-ma"}],
            "sm", "adj",
        )
        self.service.clock.set(at(1, 20))
        with self.assertRaises(DeadlineError):
            self.service.adjust_run(
                "run-1001",
                [{"op": "redact_segment", "language": "en", "segment_id": "seg-li"}],
                "sm", "adj2",
            )

    def test_receipt_before_start_rejected(self) -> None:
        self._plan_lock()
        self.service.clock.set(at(1, 19, 59))
        with self.assertRaises(DeadlineError):
            self.service.record_receipt("run-1001", "rc", "en", "fp", "sm", "rc-evt")

    def test_receipt_idempotent_and_dispute(self) -> None:
        self._plan_lock()
        self.service.clock.set(at(1, 20, 30))
        content = self.service.effective_content("run-1001", "en")
        first = self.service.record_receipt("run-1001", "rc-1", "en", content["content_fingerprint"], "sm", "rc1")
        self.assertFalse(first["idempotent"])
        again = self.service.record_receipt("run-1001", "rc-1", "en", content["content_fingerprint"], "sm", "rc1")
        self.assertTrue(again["idempotent"])
        with self.assertRaises(DisputeRaised) as raised:
            self.service.record_receipt("run-1001", "rc-1", "en", "different-fp", "sm", "rc1-bad")
        dispute = self.service.state.disputes[raised.exception.dispute_id]
        self.assertEqual("rc-1", dispute.receipt_id)
        # 争议事件重传（同一 event_id）仍是同一个争议，不产生第二条事件。
        events_before = len(self.service.store.events)
        with self.assertRaises(DisputeRaised):
            self.service.record_receipt("run-1001", "rc-1", "en", "different-fp", "sm", "rc1-bad")
        self.assertEqual(events_before, len(self.service.store.events))

    def test_unknown_language_content_not_found(self) -> None:
        self._plan_lock(languages=("en",))
        with self.assertRaises(NotFoundError):
            self.service.effective_content("run-1001", "ar")


class ErrataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service, self.tmp = make_service()
        seed_script(self.service)
        seed_consents(self.service)
        propose_sign_approve(self.service, "v1")
        self.service.plan_run(
            "run-future", SCRIPT_ID, "2026-10-10T20:00:00+08:00", "2026-10-10T18:00:00+08:00",
            ["en"], "plan-future",
        )
        self.service.clock.set(at(2, 17))
        self.service.lock_run("run-future", {"en": "v1"}, "sm", "lock-future")

    def _v2(self, text: str = "I moved here from Xihaigu in 1997.") -> None:
        entries = english_entries("seg-line", "seg-li", "seg-ma")
        for e in entries:
            if e["segment_id"] == "seg-li":
                e["text"] = text
        propose_sign_approve(self.service, "v2", entries=entries, prefix="v2")

    def test_errata_only_affects_not_yet_started_runs(self) -> None:
        # 已演出场次保留旧文本。
        self.service.plan_run(
            "run-past", SCRIPT_ID, "2026-10-01T20:00:00+08:00", "2026-10-01T18:00:00+08:00",
            ["en"], "plan-past",
        )
        self.service.clock.set(at(1, 17))
        self.service.lock_run("run-past", {"en": "v1"}, "sm", "lock-past")
        self.service.clock.set(at(1, 20, 30))
        past = self.service.effective_content("run-past", "en")
        self.assertEqual("v1", past["effective_version"])

        # 开演后才发勘误。
        self.service.clock.set(at(1, 22))
        self._v2()
        self.service.issue_errata("v1", "v2", "地名表述需更准确", "pub-c", "errata-1")

        self.service.clock.set(at(9, 12))
        future = self.service.effective_content("run-future", "en")
        self.assertEqual("v2", future["effective_version"])
        self.assertTrue(future["errata_followed"])
        past_after = self.service.effective_content("run-past", "en")
        self.assertEqual("v1", past_after["effective_version"])
        self.assertFalse(past_after["errata_followed"])

    def test_errata_issued_after_start_does_not_change_that_night(self) -> None:
        # 另一未开演场次先于勘误锁定 v1。
        self.service.plan_run(
            "run-later", SCRIPT_ID, "2026-10-20T20:00:00+08:00", "2026-10-20T18:00:00+08:00",
            ["en"], "plan-later",
        )
        self.service.clock.set(at(9, 17))
        self.service.lock_run("run-later", {"en": "v1"}, "sm", "lock-later")
        # run-future 开演后（10-10 21:00）才发出 v2 勘误：当晚冻结为 v1。
        self.service.clock.set(at(10, 21))
        self.assertEqual("v1", self.service.effective_content("run-future", "en")["effective_version"])
        self._v2()
        self.service.issue_errata("v1", "v2", "开演后的迟到修订", "pub-c", "errata-late")
        self.assertEqual("v1", self.service.effective_content("run-future", "en")["effective_version"])
        # 尚未开演的 run-later 自动跟随勘误链头 v2。
        self.assertEqual("v2", self.service.effective_content("run-later", "en")["effective_version"])
        # 勘误发出之后排的新场次不能再锁场旧版本。
        self.service.plan_run(
            "run-x", SCRIPT_ID, "2026-10-21T20:00:00+08:00", "2026-10-21T18:00:00+08:00",
            ["en"], "plan-x",
        )
        self.service.clock.set(at(21, 17))
        with self.assertRaises(StateError):
            self.service.lock_run("run-x", {"en": "v1"}, "sm", "lock-x")

    def test_performed_version_kept_through_errata_link(self) -> None:
        self.service.clock.set(at(1, 22))
        self._v2()
        self.service.issue_errata("v1", "v2", "修订", "pub-c", "errata")
        findings = self.service.audit_translation(version_id="v1", segment_id="seg-li")
        self.assertEqual(1, len(findings))
        chain = findings[0]["errata"]["chain_oldest_first"]
        self.assertEqual(["v1", "v2"], [node["version_id"] for node in chain])
        runs = {r["run_id"]: r for r in findings[0]["runs"]}
        self.assertIn("run-future", runs)


class RestartTests(unittest.TestCase):
    def test_unfinished_countersign_resumes_after_restart(self) -> None:
        service, tmp = make_service()
        seed_script(service)
        seed_consents(service)
        service.propose_version(
            "v1", SCRIPT_ID, "en", "trans-a", english_entries("seg-line", "seg-li", "seg-ma"), [], "p"
        )
        service.confirm_rehearsal("v1", SUBJECT_LI, SUBJECT_LI, ["seg-li"], "cf1")
        service.sign_version("v1", "translator", "trans-a", "s1")
        log_path = service.store.path

        # 重启：新服务从日志重放，时钟重新设定为稍后时刻。
        restarted = TheatreService(EventStore(log_path), SettableClock(at(1, 15)))
        restarted.confirm_rehearsal("v1", SUBJECT_MA, SUBJECT_MA, ["seg-ma"], "cf2")
        restarted.sign_version("v1", "director", "dir-b", "s2")
        restarted.sign_version("v1", "publisher", "pub-c", "s3")
        restarted.approve_version("v1", "pub-c", "a1")
        self.assertTrue(restarted.state.versions["v1"].approved)

        # 再重启一次，事件总数不变，状态一致。
        again = TheatreService(EventStore(log_path), SettableClock(at(1, 16)))
        self.assertEqual(len(again.store.events), len(restarted.store.events))
        self.assertEqual({"seg-li"}, set(again.state.versions["v1"].confirmations[SUBJECT_LI]["segment_ids"]))

    def test_command_replay_is_idempotent_after_restart(self) -> None:
        service, tmp = make_service()
        seed_script(service)
        service.propose_version("v1", SCRIPT_ID, "en", "ta", english_entries("seg-line"), [], "p")
        service.sign_version("v1", "translator", "ta", "sig-t")
        log_path = service.store.path
        events_after_sign = len(service.store.events)

        restarted = TheatreService(EventStore(log_path), SettableClock(at(1, 12)))
        # 同一会签命令（相同 event_id）重传：原样返回，不新增事件、不报"已会签"。
        echoed = restarted.sign_version("v1", "translator", "ta", "sig-t")
        self.assertEqual("VERSION_SIGNED", echoed["event_type"])
        self.assertEqual(events_after_sign, len(restarted.store.events))
        # 会签照常继续。
        restarted.sign_version("v1", "director", "db", "sig-d")
        restarted.sign_version("v1", "publisher", "pc", "sig-p")
        restarted.approve_version("v1", "pc", "appr")
        self.assertTrue(restarted.state.versions["v1"].approved)


if __name__ == "__main__":
    unittest.main()
