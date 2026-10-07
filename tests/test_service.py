import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from helpers import (  # noqa: E402
    ORAL_TRANSLATION,
    RUN_CUTOFF,
    RUN_START,
    SEG1_TRANSLATION,
    build_locked_fixture,
    seed_consent,
    seed_run,
    seed_script,
    seed_version,
    sign_all,
)
from theatre_release import JsonlStore, ManualClock, ReleaseService, ServiceError, validate_event  # noqa: E402

START = "2026-10-01T09:00:00+08:00"


class ServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = ManualClock(START)
        self.store = JsonlStore(self.tmp.name)
        self.service = ReleaseService(self.store, self.clock)

    def fixture(self) -> ReleaseService:
        build_locked_fixture(self.service)
        return self.service

    def reopen(self) -> ReleaseService:
        """模拟重启：同一日志目录重新构建服务。"""
        self.service = ReleaseService(JsonlStore(self.tmp.name), self.clock)
        return self.service

    def assert_error(self, code: str, fn, *args, **kwargs) -> None:
        with self.assertRaises(ServiceError) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(code, ctx.exception.code)


class ScriptAndSegmentTests(ServiceTestCase):
    def test_stage_only_moves_forward(self) -> None:
        self.service.register_script("play-1", "闽宁镇之夜")
        self.assert_error("stage_not_forward", self.service.advance_stage, "play-1", "draft")
        self.service.advance_stage("play-1", "resident")
        self.assert_error("stage_not_forward", self.service.advance_stage, "play-1", "rehearsal")

    def test_schedule_requires_resident_stage(self) -> None:
        self.service.register_script("play-1", "闽宁镇之夜")
        self.assert_error(
            "script_not_resident",
            self.service.schedule_run,
            "run-1",
            "play-1",
            RUN_START,
            RUN_CUTOFF,
        )

    def test_schedule_requires_cutoff_before_start(self) -> None:
        seed_script(self.service)
        self.assert_error(
            "invalid_schedule",
            self.service.schedule_run,
            "run-1",
            "play-1",
            RUN_START,
            "2026-10-08T00:00:00+08:00",
        )

    def test_witness_can_only_confirm_own_facts(self) -> None:
        seed_script(self.service)
        self.assert_error("witness_not_involved", self.service.confirm_facts, "seg-2", "outsider")
        event = self.service.confirm_facts("seg-2", "villager-ma")
        self.assertEqual("FACT_CONFIRMED", event["event_type"])
        self.assertEqual(["villager-ma"], self.service.world.segments["seg-2"].confirmations)


class ConsentTests(ServiceTestCase):
    def test_withdrawal_before_deadline(self) -> None:
        seed_script(self.service)
        seed_consent(self.service, withdraw_by="2026-10-03T00:00:00+08:00")
        self.clock.set("2026-10-02T12:00:00+08:00")
        self.service.withdraw_consent("consent-1", "家属要求")
        self.assertEqual("withdrawn", self.service.world.consents["consent-1"].status)

    def test_withdrawal_after_deadline_rejected(self) -> None:
        seed_script(self.service)
        seed_consent(self.service, withdraw_by="2026-10-03T00:00:00+08:00")
        self.clock.set("2026-10-04T00:00:00+08:00")
        self.assert_error("withdrawal_deadline_passed", self.service.withdraw_consent, "consent-1", "太迟了")


class SigningTests(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        seed_script(self.service)
        seed_version(self.service, "ver-1", sign=False)

    def test_submitter_cannot_approve(self) -> None:
        self.assert_error("submitter_cannot_approve", self.service.sign_version, "ver-1", "translator", "alice")

    def test_signer_cannot_take_two_roles(self) -> None:
        self.service.sign_version("ver-1", "director", "bob")
        self.assert_error("signer_conflict", self.service.sign_version, "ver-1", "translator", "bob")

    def test_role_signed_only_once(self) -> None:
        self.service.sign_version("ver-1", "director", "bob")
        self.assert_error("role_already_signed", self.service.sign_version, "ver-1", "director", "eve")

    def test_three_distinct_roles_complete_approval(self) -> None:
        self.service.sign_version("ver-1", "translator", "dave")
        self.service.sign_version("ver-1", "director", "bob")
        self.assertEqual("in_review", self.service.world.versions["ver-1"].status)
        self.service.sign_version("ver-1", "publisher", "carol")
        version = self.service.world.versions["ver-1"]
        self.assertEqual("approved", version.status)
        self.assertIsNotNone(version.approved_at)
        self.assert_error("version_already_approved", self.service.sign_version, "ver-1", "translator", "eve")

    def test_restart_resumes_unfinished_cosigning(self) -> None:
        self.service.sign_version("ver-1", "translator", "dave")
        service = self.reopen()
        self.assertEqual("in_review", service.world.versions["ver-1"].status)
        service.sign_version("ver-1", "director", "bob")
        service.sign_version("ver-1", "publisher", "carol")
        self.assertEqual("approved", service.world.versions["ver-1"].status)
        approved = [e for e in self.store.load() if e["event_type"] == "VERSION_APPROVED"]
        self.assertEqual(1, len(approved))


class LockTests(ServiceTestCase):
    def test_lock_requires_approval(self) -> None:
        seed_script(self.service)
        seed_version(self.service, "ver-1", sign=False)
        seed_run(self.service)
        self.assert_error("version_not_approved", self.service.lock_run, "run-1", "ver-1")

    def test_lock_requires_rehearsal_confirmation(self) -> None:
        seed_script(self.service)
        seed_version(self.service, "ver-1", sign=False)
        sign_all(self.service, "ver-1")
        seed_run(self.service)
        self.assert_error("rehearsal_not_confirmed", self.service.lock_run, "run-1", "ver-1")

    def test_lock_rejects_version_submitted_after_cutoff(self) -> None:
        seed_script(self.service)
        seed_run(self.service)
        self.clock.set("2026-10-06T09:00:00+08:00")  # 已过 10-05 截稿
        seed_version(self.service, "ver-late")
        self.assert_error("submitted_after_cutoff", self.service.lock_run, "run-1", "ver-late")

    def test_lock_rejected_after_start(self) -> None:
        self.fixture()
        run2 = "run-2"
        self.service.schedule_run(run2, "play-1", "2026-10-02T19:30:00+08:00", "2026-10-01T12:00:00+08:00")
        self.clock.set("2026-10-02T20:00:00+08:00")
        self.assert_error("run_already_started", self.service.lock_run, run2, "ver-1")


class RevisionAndErrataTests(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.fixture()

    def make_v2(self) -> None:
        seed_version(self.service, "ver-2", seg1_text="We left the mountains behind.")

    def test_late_revision_applies_before_start(self) -> None:
        self.make_v2()
        self.service.revise_run("run-1", "ver-2")
        run = self.service.world.runs["run-1"]
        self.assertEqual(2, run.locked_number)
        self.assertEqual("ver-2", run.locked_version_id)

    def test_late_revision_rejected_after_start(self) -> None:
        self.make_v2()
        self.clock.set("2026-10-07T20:00:00+08:00")
        self.assert_error("run_already_started", self.service.revise_run, "run-1", "ver-2")

    def test_performed_run_requires_errata(self) -> None:
        self.make_v2()
        fingerprint = self.service.world.runs["run-1"].fingerprint
        self.service.record_receipt("run-1", "rcpt-1", 1, fingerprint)
        self.assert_error("performed_run_requires_errata", self.service.revise_run, "run-1", "ver-2")

    def test_revision_requires_newer_number(self) -> None:
        self.assert_error("not_a_newer_revision", self.service.revise_run, "run-1", "ver-1")

    def test_errata_chain_preserves_performed_version(self) -> None:
        fingerprint = self.service.world.runs["run-1"].fingerprint
        self.service.record_receipt("run-1", "rcpt-1", 1, fingerprint)
        self.service.issue_errata("errata-1", "run-1", "人名译写有误", correction="马得福 -> 马德福")
        self.service.issue_errata("errata-2", "run-1", "补充年代说明")
        first = self.service.world.errata["errata-1"]
        second = self.service.world.errata["errata-2"]
        self.assertEqual("ver-1", first.supersedes)
        self.assertEqual("errata-1", second.supersedes)
        self.assertEqual("performed", self.service.world.runs["run-1"].status)

    def test_errata_rejected_for_unperformed_run(self) -> None:
        self.assert_error("run_not_performed", self.service.issue_errata, "errata-1", "run-1", "太早")


class ReceiptTests(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.fixture()

    def test_receipt_retransmission_is_idempotent(self) -> None:
        fingerprint = self.service.world.runs["run-1"].fingerprint
        first = self.service.record_receipt("run-1", "rcpt-1", 1, fingerprint)
        self.assertEqual("accepted", first["outcome"])
        self.assertFalse(first["idempotent_replay"])
        journal_len = len(self.store.load())
        second = self.service.record_receipt("run-1", "rcpt-1", 1, fingerprint)
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(journal_len, len(self.store.load()))
        self.assertEqual("performed", self.service.world.runs["run-1"].status)

    def test_same_number_different_fingerprint_raises_dispute(self) -> None:
        result = self.service.record_receipt("run-1", "rcpt-9", 1, "0" * 64)
        self.assertEqual("disputed", result["outcome"])
        run = self.service.world.runs["run-1"]
        self.assertEqual("disputed", run.status)
        dispute = self.service.world.disputes["run-1:rcpt-9"]
        self.assertEqual(run.fingerprint, dispute.expected_fingerprint)
        self.assertEqual("0" * 64, dispute.actual_fingerprint)

    def test_receipt_with_wrong_number_rejected(self) -> None:
        self.assert_error("receipt_version_mismatch", self.service.record_receipt, "run-1", "rcpt-1", 99, "x")

    def test_receipt_requires_lock(self) -> None:
        self.service.schedule_run("run-2", "play-1", "2026-10-08T19:30:00+08:00", "2026-10-06T18:00:00+08:00")
        self.assert_error("run_not_locked", self.service.record_receipt, "run-2", "rcpt-1", 1, "x")


class LiveChangeTests(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.fixture()

    def test_live_change_updates_fingerprint_and_content(self) -> None:
        before = self.service.world.runs["run-1"].fingerprint
        self.service.record_live_change(
            "lc-1", "run-1", "seg-1", "en", "We left the mountains tonight.", "演员临场改词"
        )
        run = self.service.world.runs["run-1"]
        self.assertNotEqual(before, run.fingerprint)
        content = self.service.run_content("run-1", "en")
        first = content["segments"][0]
        self.assertEqual("We left the mountains tonight.", first["translation"])
        self.assertTrue(first["live_changed"])

    def test_live_change_rejected_for_unlocked_run(self) -> None:
        self.service.schedule_run("run-2", "play-1", "2026-10-08T19:30:00+08:00", "2026-10-06T18:00:00+08:00")
        self.assert_error(
            "run_not_locked", self.service.record_live_change, "lc-1", "run-2", "seg-1", "en", "x", "y"
        )


class IdempotencyTests(ServiceTestCase):
    def test_event_id_replay_is_noop(self) -> None:
        seed_script(self.service)
        first = self.service.submit_translation(
            "tr-1", "seg-1", "en", SEG1_TRANSLATION, "alice", event_id="cmd-001"
        )
        self.assertIsNotNone(first)
        journal_len = len(self.store.load())
        second = self.service.submit_translation(
            "tr-1", "seg-1", "en", SEG1_TRANSLATION, "alice", event_id="cmd-001"
        )
        self.assertIsNone(second)
        self.assertEqual(journal_len, len(self.store.load()))


class ContractConformanceTests(ServiceTestCase):
    def test_journal_events_conform_to_contract(self) -> None:
        self.fixture()
        self.service.record_live_change("lc-1", "run-1", "seg-1", "en", "Ad lib line.", "临场")
        fingerprint = self.service.world.runs["run-1"].fingerprint
        self.service.record_receipt("run-1", "rcpt-1", 1, fingerprint)
        self.service.record_receipt("run-1", "rcpt-2", 1, "0" * 64)
        self.service.issue_errata("errata-1", "run-1", "更正")
        schema = json.loads((ROOT / "contracts/domain.schema.json").read_text(encoding="utf-8"))
        events = self.store.load()
        self.assertGreater(len(events), 0)
        for event in events:
            self.assertEqual([], validate_event(event, schema), msg=event["event_type"])

    def test_restart_replays_full_state(self) -> None:
        self.fixture()
        service = self.reopen()
        run = service.world.runs["run-1"]
        self.assertEqual("locked", run.status)
        self.assertEqual("ver-1", run.locked_version_id)
        content = service.run_content("run-1", "en")
        self.assertEqual(SEG1_TRANSLATION, content["segments"][0]["translation"])
        self.assertEqual(ORAL_TRANSLATION, content["segments"][1]["translation"])


if __name__ == "__main__":
    unittest.main()
