import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from support import (  # noqa: E402
    SCRIPT_ID,
    SUBJECT_LI,
    at,
    english_entries,
    make_service,
    seed_script,
)
from theatre_release.httpapi import build_server  # noqa: E402


class HttpFixture:
    def __init__(self) -> None:
        self.service, _ = make_service()
        self.server = build_server(self.service, clock_settable=True, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class HttpApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = HttpFixture()
        self.service = self.fx.service

    def tearDown(self) -> None:
        self.fx.stop()

    def post(self, path: str, body: dict):
        request = urllib.request.Request(
            self.fx.base + path,
            json.dumps(body).encode("utf-8"),
            {"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def get(self, path: str):
        try:
            with urllib.request.urlopen(self.fx.base + path) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def _approved_version(self) -> None:
        seed_script(self.service)
        self.post("/consents", {
            "consent_id": "c-li", "subject_ref": SUBJECT_LI, "actor_id": SUBJECT_LI,
            "scope": {"script_id": SCRIPT_ID, "segment_ids": ["seg-li"]}, "event_id": "c1",
        })
        entries = [english_entries("seg-line")[0], english_entries("seg-li")[0]]
        status, _ = self.post("/versions", {
            "version_id": "v1", "script_id": SCRIPT_ID, "target_language": "en",
            "actor_id": "trans-a", "entries": entries, "event_id": "p1",
        })
        self.assertEqual(200, status)
        self.post("/versions/v1/confirm-rehearsal", {
            "subject_ref": SUBJECT_LI, "actor_id": SUBJECT_LI,
            "segment_ids": ["seg-li"], "event_id": "cf1",
        })
        self.post("/versions/v1/sign", {"role": "translator", "actor_id": "trans-a", "event_id": "s1"})
        self.post("/versions/v1/sign", {"role": "director", "actor_id": "dir-b", "event_id": "s2"})
        self.post("/versions/v1/sign", {"role": "publisher", "actor_id": "pub-c", "event_id": "s3"})
        status, body = self.post("/versions/v1/approve", {"actor_id": "pub-c", "event_id": "a1"})
        self.assertEqual(200, status)

    def test_forbidden_and_not_found_mapping(self) -> None:
        seed_script(self.service)
        status, body = self.post("/consents", {
            "consent_id": "c1", "subject_ref": SUBJECT_LI, "actor_id": "someone-else",
            "scope": {"script_id": SCRIPT_ID, "segment_ids": ["seg-li"]}, "event_id": "e",
        })
        self.assertEqual(403, status)
        self.assertEqual("forbidden", body["error"])
        status, _ = self.get("/runs/nope/tonight?language=en")
        self.assertEqual(404, status)

    def test_tonight_content_hides_unauthorized_testimony(self) -> None:
        self._approved_version()
        # seg-li 已授权；用临时刻掉授权的方式验证隐藏：先排场锁场，再撤回授权。
        self.post("/runs", {
            "run_id": "run-1", "script_id": SCRIPT_ID,
            "starts_at": "2026-10-01T20:00:00+08:00",
            "lock_deadline": "2026-10-01T18:00:00+08:00",
            "audience_languages": ["en"], "event_id": "plan",
        })
        self.post("/admin/clock", {"now": "2026-10-01T17:00:00+08:00"})
        self.post("/runs/run-1/lock", {"selections": {"en": "v1"}, "actor_id": "sm", "event_id": "lock"})
        status, content = self.get("/runs/run-1/tonight?language=en")
        self.assertEqual(200, status)
        visible = {e["segment_id"] for e in content["entries"]}
        self.assertEqual({"seg-line", "seg-li"}, visible)

        self.post("/consents/c-li/withdraw", {"actor_id": SUBJECT_LI, "event_id": "wd"})
        status, content = self.get("/runs/run-1/tonight?language=en")
        self.assertEqual(200, status)
        visible = {e["segment_id"] for e in content["entries"]}
        self.assertEqual({"seg-line"}, visible)
        hidden = {(h["segment_id"], h["reason"]) for h in content["hidden"]}
        self.assertIn(("seg-li", "未授权口述"), hidden)

    def test_receipt_dispute_end_to_end(self) -> None:
        self._approved_version()
        self.post("/runs", {
            "run_id": "run-1", "script_id": SCRIPT_ID,
            "starts_at": "2026-10-01T20:00:00+08:00",
            "lock_deadline": "2026-10-01T18:00:00+08:00",
            "audience_languages": ["en"], "event_id": "plan",
        })
        self.post("/admin/clock", {"now": "2026-10-01T17:00:00+08:00"})
        self.post("/runs/run-1/lock", {"selections": {"en": "v1"}, "actor_id": "sm", "event_id": "lock"})
        self.post("/admin/clock", {"now": "2026-10-01T20:30:00+08:00"})
        _, content = self.get("/runs/run-1/tonight?language=en")
        status, body = self.post("/runs/run-1/receipts", {
            "receipt_id": "rc-1", "language": "en",
            "content_fingerprint": content["content_fingerprint"],
            "actor_id": "sm", "event_id": "rc1",
        })
        self.assertEqual(200, status)
        status, body = self.post("/runs/run-1/receipts", {
            "receipt_id": "rc-1", "language": "en",
            "content_fingerprint": "mismatch",
            "actor_id": "sm", "event_id": "rc1-bad",
        })
        self.assertEqual(409, status)
        self.assertEqual("dispute", body["error"])
        status, disputes = self.get("/disputes")
        self.assertEqual(200, status)
        self.assertEqual(1, len(disputes["disputes"]))


if __name__ == "__main__":
    unittest.main()
