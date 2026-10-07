"""由事件日志重放出的领域状态。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .clock import ensure_aware


@dataclass
class ScriptState:
    script_id: str
    title: str
    stage: str


@dataclass
class SegmentState:
    segment_id: str
    script_id: str
    order: int
    text: str
    kind: str
    involves: list[str]
    confirmations: list[str] = field(default_factory=list)


@dataclass
class ConsentState:
    consent_id: str
    subject_ref: str
    script_id: str
    scope_all: bool
    segment_ids: list[str]
    withdraw_by: datetime
    recorded_at: datetime
    status: str = "granted"  # granted | withdrawn
    withdrawn_at: Optional[datetime] = None
    reason: Optional[str] = None

    def covers(self, segment_id: str) -> bool:
        return self.scope_all or segment_id in self.segment_ids


@dataclass
class TranslationState:
    translation_id: str
    segment_id: str
    language: str
    text: str
    fingerprint: str
    translator: str
    submitted_at: datetime


@dataclass
class NoteState:
    note_id: str
    segment_id: str
    language: str
    text: str
    author: str


@dataclass
class SignatureState:
    role: str
    signer: str
    at: datetime


@dataclass
class VersionItem:
    segment_id: str
    language: str
    translation_id: str
    note_ids: list[str]


@dataclass
class VersionState:
    version_id: str
    script_id: str
    number: int
    items: list[VersionItem]
    submitted_by: str
    submitted_at: datetime
    status: str = "in_review"  # in_review | approved
    signatures: list[SignatureState] = field(default_factory=list)
    approved_at: Optional[datetime] = None
    rehearsal_confirmer: Optional[str] = None
    rehearsal_confirmed_at: Optional[datetime] = None


@dataclass
class LiveChangeState:
    change_id: str
    run_id: str
    segment_id: str
    language: str
    text: str
    reason: str
    at: datetime


@dataclass
class ReceiptState:
    receipt_id: str
    version_number: int
    fingerprint: str
    outcome: str  # accepted | disputed
    at: datetime


@dataclass
class RunState:
    run_id: str
    script_id: str
    starts_at: datetime
    submission_cutoff: datetime
    status: str = "scheduled"  # scheduled | locked | performed | disputed
    languages: list[str] = field(default_factory=list)
    locked_version_id: Optional[str] = None
    locked_number: Optional[int] = None
    fingerprint: Optional[str] = None
    live_change_ids: list[str] = field(default_factory=list)
    receipts: dict[str, ReceiptState] = field(default_factory=dict)


@dataclass
class ErrataState:
    errata_id: str
    run_id: str
    version_id: str
    supersedes: str
    reason: str
    correction: Optional[str]
    issued_at: datetime


@dataclass
class DisputeState:
    dispute_id: str
    run_id: str
    receipt_id: str
    expected_fingerprint: str
    actual_fingerprint: str
    at: datetime


class World:
    """全部聚合的内存视图；只通过 apply 由事件推进。"""

    def __init__(self) -> None:
        self.scripts: dict[str, ScriptState] = {}
        self.segments: dict[str, SegmentState] = {}
        self.consents: dict[str, ConsentState] = {}
        self.translations: dict[str, TranslationState] = {}
        self.notes: dict[str, NoteState] = {}
        self.versions: dict[str, VersionState] = {}
        self.runs: dict[str, RunState] = {}
        self.live_changes: dict[str, LiveChangeState] = {}
        self.errata: dict[str, ErrataState] = {}
        self.disputes: dict[str, DisputeState] = {}

    def apply(self, event: dict[str, Any]) -> None:
        handler = getattr(self, f"_on_{event['event_type'].lower()}", None)
        if handler is None:
            raise ValueError(f"未登记的事件类型: {event['event_type']}")
        handler(event)

    # -- 剧目 ---------------------------------------------------------
    def _on_script_registered(self, event: dict[str, Any]) -> None:
        payload = event["payload"]
        self.scripts[event["aggregate_id"]] = ScriptState(
            script_id=event["aggregate_id"],
            title=payload["title"],
            stage=payload["stage"],
        )

    def _on_stage_advanced(self, event: dict[str, Any]) -> None:
        self.scripts[event["aggregate_id"]].stage = event["payload"]["to_stage"]

    def _on_segment_added(self, event: dict[str, Any]) -> None:
        payload = event["payload"]
        self.segments[event["aggregate_id"]] = SegmentState(
            segment_id=event["aggregate_id"],
            script_id=payload["script_ref"],
            order=payload["order"],
            text=payload["text"],
            kind=payload["kind"],
            involves=list(payload.get("involves", [])),
        )

    def _on_fact_confirmed(self, event: dict[str, Any]) -> None:
        segment = self.segments[event["aggregate_id"]]
        witness = event["payload"]["witness"]
        if witness not in segment.confirmations:
            segment.confirmations.append(witness)

    # -- 授权 ---------------------------------------------------------
    def _on_consent_recorded(self, event: dict[str, Any]) -> None:
        payload = event["payload"]
        scope = payload["scope"]
        segment_ids = list(scope.get("segments", ["*"]))
        self.consents[event["aggregate_id"]] = ConsentState(
            consent_id=event["aggregate_id"],
            subject_ref=payload["subject_ref"],
            script_id=scope["script_ref"],
            scope_all="*" in segment_ids,
            segment_ids=segment_ids,
            withdraw_by=ensure_aware(payload["withdraw_by"]),
            recorded_at=ensure_aware(event["occurred_at"]),
        )

    def _on_consent_withdrawn(self, event: dict[str, Any]) -> None:
        consent = self.consents[event["aggregate_id"]]
        consent.status = "withdrawn"
        consent.withdrawn_at = ensure_aware(event["occurred_at"])
        consent.reason = event["payload"]["reason"]

    # -- 译文与注释 ----------------------------------------------------
    def _on_translation_submitted(self, event: dict[str, Any]) -> None:
        payload = event["payload"]
        self.translations[event["aggregate_id"]] = TranslationState(
            translation_id=event["aggregate_id"],
            segment_id=payload["segment_ref"],
            language=payload["language"],
            text=payload["text"],
            fingerprint=payload["fingerprint"],
            translator=payload["translator"],
            submitted_at=ensure_aware(event["occurred_at"]),
        )

    def _on_note_added(self, event: dict[str, Any]) -> None:
        payload = event["payload"]
        self.notes[event["aggregate_id"]] = NoteState(
            note_id=event["aggregate_id"],
            segment_id=payload["segment_ref"],
            language=payload["language"],
            text=payload["text"],
            author=payload.get("author", ""),
        )

    # -- 版本会签 ------------------------------------------------------
    def _on_version_submitted(self, event: dict[str, Any]) -> None:
        payload = event["payload"]
        items = [
            VersionItem(
                segment_id=item["segment_ref"],
                language=item["language"],
                translation_id=item["translation_ref"],
                note_ids=list(item.get("note_refs", [])),
            )
            for item in payload["items"]
        ]
        self.versions[event["aggregate_id"]] = VersionState(
            version_id=event["aggregate_id"],
            script_id=payload["script_ref"],
            number=payload["number"],
            items=items,
            submitted_by=payload["submitted_by"],
            submitted_at=ensure_aware(event["occurred_at"]),
        )

    def _on_version_signed(self, event: dict[str, Any]) -> None:
        payload = event["payload"]
        self.versions[event["aggregate_id"]].signatures.append(
            SignatureState(
                role=payload["role"],
                signer=payload["signer"],
                at=ensure_aware(event["occurred_at"]),
            )
        )

    def _on_version_approved(self, event: dict[str, Any]) -> None:
        version = self.versions[event["aggregate_id"]]
        version.status = "approved"
        version.approved_at = ensure_aware(event["occurred_at"])

    def _on_rehearsal_confirmed(self, event: dict[str, Any]) -> None:
        version = self.versions[event["aggregate_id"]]
        version.rehearsal_confirmer = event["payload"]["confirmer"]
        version.rehearsal_confirmed_at = ensure_aware(event["occurred_at"])

    # -- 场次 ---------------------------------------------------------
    def _on_run_scheduled(self, event: dict[str, Any]) -> None:
        payload = event["payload"]
        self.runs[event["aggregate_id"]] = RunState(
            run_id=event["aggregate_id"],
            script_id=payload["script_ref"],
            starts_at=ensure_aware(payload["starts_at"]),
            submission_cutoff=ensure_aware(payload["submission_cutoff"]),
        )

    def _on_language_requested(self, event: dict[str, Any]) -> None:
        run = self.runs[event["aggregate_id"]]
        language = event["payload"]["language"]
        if language not in run.languages:
            run.languages.append(language)

    def _on_run_locked(self, event: dict[str, Any]) -> None:
        payload = event["payload"]
        run = self.runs[event["aggregate_id"]]
        run.status = "locked"
        run.locked_version_id = payload["version_ref"]
        run.locked_number = payload["script_version"]
        run.fingerprint = payload["fingerprint"]

    def _on_run_revised(self, event: dict[str, Any]) -> None:
        payload = event["payload"]
        run = self.runs[event["aggregate_id"]]
        run.locked_version_id = payload["version_ref"]
        run.locked_number = payload["script_version"]
        run.fingerprint = payload["fingerprint"]

    def _on_live_change_recorded(self, event: dict[str, Any]) -> None:
        payload = event["payload"]
        change = LiveChangeState(
            change_id=event["aggregate_id"],
            run_id=payload["run_ref"],
            segment_id=payload["segment_ref"],
            language=payload["language"],
            text=payload["text"],
            reason=payload["reason"],
            at=ensure_aware(event["occurred_at"]),
        )
        self.live_changes[change.change_id] = change
        run = self.runs[change.run_id]
        run.live_change_ids.append(change.change_id)
        run.fingerprint = payload["fingerprint"]

    def _on_receipt_recorded(self, event: dict[str, Any]) -> None:
        payload = event["payload"]
        run = self.runs[event["aggregate_id"]]
        receipt = ReceiptState(
            receipt_id=payload["receipt_id"],
            version_number=payload["version_number"],
            fingerprint=payload["fingerprint"],
            outcome=payload["outcome"],
            at=ensure_aware(event["occurred_at"]),
        )
        run.receipts[receipt.receipt_id] = receipt
        if receipt.outcome == "accepted":
            run.status = "performed"

    def _on_dispute_raised(self, event: dict[str, Any]) -> None:
        payload = event["payload"]
        dispute = DisputeState(
            dispute_id=event["aggregate_id"],
            run_id=payload["run_ref"],
            receipt_id=payload["receipt_id"],
            expected_fingerprint=payload["expected_fingerprint"],
            actual_fingerprint=payload["actual_fingerprint"],
            at=ensure_aware(event["occurred_at"]),
        )
        self.disputes[dispute.dispute_id] = dispute
        self.runs[dispute.run_id].status = "disputed"

    # -- 勘误 ---------------------------------------------------------
    def _on_errata_issued(self, event: dict[str, Any]) -> None:
        payload = event["payload"]
        errata = ErrataState(
            errata_id=payload["errata_id"],
            run_id=payload["run_ref"],
            version_id=event["aggregate_id"],
            supersedes=payload["supersedes"],
            reason=payload["reason"],
            correction=payload.get("correction"),
            issued_at=ensure_aware(event["occurred_at"]),
        )
        self.errata[errata.errata_id] = errata
