import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from helpers import (  # noqa: E402
    NOTE_TEXT,
    ORAL_TEXT,
    ORAL_TRANSLATION,
    SEG1_TRANSLATION,
    build_locked_fixture,
)
from theatre_release import JsonlStore, ManualClock, ReleaseService  # noqa: E402
from theatre_release.api import make_server  # noqa: E402


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = ManualClock("2026-10-01T09:00:00+08:00")
        self.service = ReleaseService(JsonlStore(self.tmp.name), self.clock)
        build_locked_fixture(self.service)
        self.clock.set("2026-10-07T10:00:00+08:00")  # 开演当天白天
        self.server = make_server(self.service, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)

    def _stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def get(self, path: str):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path)
        response = conn.getresponse()
        raw = response.read().decode("utf-8")
        conn.close()
        return response.status, json.loads(raw), raw

    def test_tonight_returns_effective_content_per_language(self) -> None:
        status, body, _ = self.get("/tonight?lang=en")
        self.assertEqual(200, status)
        self.assertEqual("run-1", body["run_id"])
        self.assertEqual(1, body["version"])
        self.assertEqual("locked", body["status"])
        first, second = body["segments"]
        self.assertEqual(SEG1_TRANSLATION, first["translation"])
        self.assertEqual([NOTE_TEXT], first["notes"])
        self.assertEqual(ORAL_TRANSLATION, second["translation"])
        self.assertFalse(second["hidden"])

    def test_tonight_with_explicit_date(self) -> None:
        status, body, _ = self.get("/tonight?lang=en&date=2026-10-07")
        self.assertEqual(200, status)
        self.assertEqual("run-1", body["run_id"])
        status, body, _ = self.get("/tonight?lang=en&date=2026-10-08")
        self.assertEqual(404, status)
        self.assertEqual("no_performance_tonight", body["error"]["code"])

    def test_unauthorized_oral_account_is_hidden(self) -> None:
        self.service.withdraw_consent("consent-1", "原型人物撤回授权")
        status, body, raw = self.get("/runs/run-1/content?lang=en")
        self.assertEqual(200, status)
        second = body["segments"][1]
        self.assertTrue(second["hidden"])
        self.assertEqual("consent_missing", second["reason"])
        self.assertNotIn("source", second)
        self.assertNotIn("translation", second)
        self.assertNotIn(ORAL_TEXT, raw)
        self.assertNotIn(ORAL_TRANSLATION, raw)

    def test_missing_language_is_rejected(self) -> None:
        status, body, _ = self.get("/tonight")
        self.assertEqual(400, status)
        self.assertEqual("language_required", body["error"]["code"])

    def test_unknown_run_returns_404(self) -> None:
        status, body, _ = self.get("/runs/no-such-run/content?lang=en")
        self.assertEqual(404, status)
        self.assertEqual("run_not_found", body["error"]["code"])

    def test_unlocked_run_returns_409(self) -> None:
        self.service.schedule_run(
            "run-2", "play-1", "2026-10-08T19:30:00+08:00", "2026-10-06T18:00:00+08:00"
        )
        status, body, _ = self.get("/runs/run-2/content?lang=en")
        self.assertEqual(409, status)
        self.assertEqual("run_not_locked", body["error"]["code"])

    def test_unknown_language_has_no_translation(self) -> None:
        status, body, _ = self.get("/runs/run-1/content?lang=fr")
        self.assertEqual(200, status)
        self.assertIsNone(body["segments"][0]["translation"])

    def test_live_change_is_served(self) -> None:
        self.service.record_live_change(
            "lc-1", "run-1", "seg-1", "en", "We left the mountains tonight.", "临场改词"
        )
        status, body, _ = self.get("/runs/run-1/content?lang=en")
        self.assertEqual(200, status)
        first = body["segments"][0]
        self.assertEqual("We left the mountains tonight.", first["translation"])
        self.assertTrue(first["live_changed"])


if __name__ == "__main__":
    unittest.main()
