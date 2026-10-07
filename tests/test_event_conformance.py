import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from support import at, make_service, propose_sign_approve, seed_consents, seed_script  # noqa: E402
from theatre_release.cli import main  # noqa: E402
from theatre_release.contracts import validate_event  # noqa: E402
from theatre_release.service import DisputeRaised  # noqa: E402


class EventContractConformanceTests(unittest.TestCase):
    def test_every_emitted_event_matches_schema(self) -> None:
        schema = json.loads((ROOT / "contracts/domain.schema.json").read_text(encoding="utf-8"))
        service, _ = make_service()
        seed_script(service)
        seed_consents(service)
        propose_sign_approve(service, "v1")
        service.plan_run(
            "run-1", "script-minning", "2026-10-01T20:00:00+08:00", "2026-10-01T18:00:00+08:00",
            ["en"], "plan",
        )
        service.clock.set(at(1, 17))
        service.lock_run("run-1", {"en": "v1"}, "sm", "lock")
        service.clock.set(at(1, 19))
        service.adjust_run(
            "run-1", [{"op": "redact_segment", "language": "en", "segment_id": "seg-ma"}],
            "sm", "adj",
        )
        service.clock.set(at(1, 20, 30))
        content = service.effective_content("run-1", "en")
        service.record_receipt("run-1", "rc", "en", content["content_fingerprint"], "sm", "rc-ok")
        with self.assertRaises(DisputeRaised):
            service.record_receipt("run-1", "rc2", "en", "wrong", "sm", "rc-bad")
        propose_sign_approve(service, "v2", prefix="v2")
        service.issue_errata("v1", "v2", "地名表述修订", "pub-c", "errata")
        service.withdraw_consent("consent-ma", "ma-defu", "withdraw-ma")

        event_types = set()
        for event in service.store.events:
            issues = validate_event(event, schema)
            self.assertEqual([], [(i.field, i.code, i.message) for i in issues], msg=event["event_id"])
            event_types.add(event["event_type"])
        self.assertEqual(
            {
                "SCRIPT_REGISTERED", "CONSENT_RECORDED", "VERSION_PROPOSED",
                "REHEARSAL_CONFIRMED", "VERSION_SIGNED", "VERSION_APPROVED",
                "RUN_PLANNED", "RUN_LOCKED", "RUN_ADJUSTED", "RUN_RECEIPTED",
                "DISPUTE_OPENED", "ERRATA_ISSUED", "CONSENT_WITHDRAWN",
            },
            event_types,
        )


if __name__ == "__main__":
    unittest.main()
