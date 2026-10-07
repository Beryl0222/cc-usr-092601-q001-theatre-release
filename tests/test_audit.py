import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from helpers import ORAL_TEXT, SEG1_TEXT, build_locked_fixture  # noqa: E402
from theatre_release import JsonlStore, ManualClock, ReleaseService  # noqa: E402
from theatre_release.audit import audit_translation, main as audit_main  # noqa: E402


class AuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = ManualClock("2026-10-01T09:00:00+08:00")
        self.service = ReleaseService(JsonlStore(self.tmp.name), self.clock)
        build_locked_fixture(self.service)

    def perform_and_correct(self) -> None:
        fingerprint = self.service.world.runs["run-1"].fingerprint
        self.service.record_receipt("run-1", "rcpt-1", 1, fingerprint)
        self.service.issue_errata("errata-1", "run-1", "人名译写有误", correction="马得福 -> 马德福")

    def test_audit_reconstructs_source_approvals_runs_and_errata(self) -> None:
        self.perform_and_correct()
        report = audit_translation(self.service, "tr-1-ver-1")

        self.assertEqual(SEG1_TEXT, report["source_segment"]["text"])
        self.assertEqual("seg-1", report["source_segment"]["segment_id"])
        self.assertEqual("We moved out of the mountains.", report["translation"]["text"])

        self.assertEqual(1, len(report["versions"]))
        version = report["versions"][0]
        self.assertEqual("ver-1", version["version_id"])
        self.assertEqual("approved", version["status"])
        self.assertEqual("alice", version["submitted_by"])
        self.assertEqual(
            {("translator", "dave"), ("director", "bob"), ("publisher", "carol")},
            {(item["role"], item["signer"]) for item in version["approvals"]},
        )
        self.assertEqual("stage-manager", version["rehearsal"]["confirmer"])

        self.assertEqual(1, len(report["runs"]))
        self.assertEqual("run-1", report["runs"][0]["run_id"])
        self.assertEqual("performed", report["runs"][0]["status"])

        self.assertEqual(1, len(report["errata"]))
        errata = report["errata"][0]
        self.assertEqual("errata-1", errata["errata_id"])
        self.assertEqual("ver-1", errata["supersedes"])
        self.assertEqual("run-1", errata["run_id"])

    def test_audit_oral_segment_shows_witness_confirmation(self) -> None:
        self.service.confirm_facts("seg-2", "villager-ma")
        report = audit_translation(self.service, "tr-2-ver-1")
        self.assertEqual(ORAL_TEXT, report["source_segment"]["text"])
        self.assertEqual(["villager-ma"], report["source_segment"]["confirmations"])
        self.assertEqual(["villager-ma"], report["source_segment"]["involves"])

    def test_audit_cli_outputs_json(self) -> None:
        self.perform_and_correct()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = audit_main([self.tmp.name, "tr-1-ver-1"])
        self.assertEqual(0, code)
        report = json.loads(buffer.getvalue())
        self.assertEqual("tr-1-ver-1", report["translation"]["translation_id"])
        self.assertEqual(1, len(report["errata"]))

    def test_audit_cli_unknown_translation_fails(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            code = audit_main([self.tmp.name, "no-such-translation"])
        self.assertEqual(1, code)
        self.assertIn("translation_not_found", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
