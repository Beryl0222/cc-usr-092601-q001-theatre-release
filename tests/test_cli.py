import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from support import (  # noqa: E402
    SCRIPT_ID,
    at,
    english_entries,
    make_service,
    propose_sign_approve,
    seed_consents,
    seed_script,
)
from theatre_release.cli import main  # noqa: E402


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="theatre-cli-")
        self.store_path = str(Path(self.tmp) / "events.jsonl")
        service, _ = make_service()
        seed_script(service)
        seed_consents(service)
        propose_sign_approve(service, "v1")
        service.plan_run(
            "run-1", SCRIPT_ID, "2026-10-10T20:00:00+08:00", "2026-10-10T18:00:00+08:00",
            ["en"], "plan-1",
        )
        service.clock.set(at(10, 17))
        service.lock_run("run-1", {"en": "v1"}, "sm", "lock-1")
        # 把已构建的日志复制到 CLI 将打开的路径。
        Path(self.store_path).write_bytes(Path(service.store.path).read_bytes())

    def test_audit_reconstructs_line(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            code = main([
                "audit", "--store", self.store_path, "--clock", "settable",
                "--now", "2026-10-05T09:00:00+08:00",
                "--text", "I moved down from Xihaigu in 1997.",
            ])
        self.assertEqual(0, code)
        report = json.loads(out.getvalue())
        self.assertEqual(1, len(report["findings"]))
        finding = report["findings"][0]
        self.assertEqual("seg-li", finding["source"]["segment_id"])
        self.assertIn("西海固", finding["source"]["source_text"])
        roles = {s["role"] for s in finding["approval_chain"]["signatures"]}
        self.assertEqual({"translator", "director", "publisher"}, roles)
        self.assertTrue(finding["approval_chain"]["separation_of_duty"])
        run_ids = {r["run_id"] for r in finding["runs"]}
        self.assertIn("run-1", run_ids)

    def test_audit_miss_returns_three(self) -> None:
        with redirect_stdout(io.StringIO()) as out:
            code = main([
                "audit", "--store", self.store_path, "--clock", "settable",
                "--now", "2026-10-05T09:00:00+08:00",
                "--text", "no such translation",
            ])
        self.assertEqual(3, code)
        self.assertEqual({"findings": []}, json.loads(out.getvalue()))

    def test_validate_sample_command(self) -> None:
        with redirect_stdout(io.StringIO()) as out:
            code = main(["validate", str(ROOT / "contracts/domain.schema.json"), str(ROOT / "data/sample.json")])
        self.assertEqual(0, code)
        self.assertEqual("valid\n", out.getvalue())


if __name__ == "__main__":
    unittest.main()
