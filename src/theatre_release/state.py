"""事件重放与读模型。

服务每次启动从事件日志重建以下只读状态：

- ``scripts``   原文剧目与段落
- ``consents``  人物原型授权（含撤回终态）
- ``versions``  译文候选、三会签链、排练确认、勘误链
- ``runs``      场次计划、锁场快照、临场变更与回执
- ``disputes``  回执编号相同但指纹不同引发的争议
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Segment:
    segment_id: str
    kind: str
    subject_ref: str | None
    text: str


@dataclass
class Script:
    script_id: str
    title: str
    language: str
    segments: dict[str, Segment]
    version: int
    registered_at: str


@dataclass
class Consent:
    consent_id: str
    subject_ref: str
    scope: dict[str, Any]
    recorded_at: str
    active: bool = True
    withdrawn_at: str | None = None


@dataclass
class ReleaseVersion:
    version_id: str
    script_id: str
    target_language: str
    entries: list[dict[str, Any]]
    notes: list[dict[str, Any]]
    source_fingerprint: str
    content_fingerprint: str
    proposer_id: str
    proposed_at: str
    version: int
    signatures: dict[str, dict[str, str]] = field(default_factory=dict)
    confirmations: dict[str, dict[str, Any]] = field(default_factory=dict)
    approved: bool = False
    approved_at: str | None = None
    final_approver_id: str | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    errata_reason: str | None = None
    errata_issued_at: str | None = None


@dataclass
class Run:
    run_id: str
    script_id: str
    starts_at: str
    lock_deadline: str
    audience_languages: list[str]
    planned_at: str
    version: int
    locked: bool = False
    locked_at: str | None = None
    script_version: int | None = None
    # 每个语言一条采用链：锁场选择 + 后续临场变更
    selections: dict[str, str] = field(default_factory=dict)
    redactions: dict[str, list[str]] = field(default_factory=dict)
    adjustments: list[dict[str, Any]] = field(default_factory=list)
    receipts: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class Dispute:
    dispute_id: str
    receipt_id: str
    run_id: str
    opened_at: str
    fingerprints: list[str] = field(default_factory=list)
    version: int = 1


@dataclass
class State:
    scripts: dict[str, Script] = field(default_factory=dict)
    consents: dict[str, Consent] = field(default_factory=dict)
    versions: dict[str, ReleaseVersion] = field(default_factory=dict)
    runs: dict[str, Run] = field(default_factory=dict)
    disputes: dict[str, Dispute] = field(default_factory=dict)

    def consent_active_for(self, subject_ref: str, script_id: str, segment_id: str) -> bool:
        for consent in self.consents.values():
            if consent.subject_ref != subject_ref or not consent.active:
                continue
            scope = consent.scope or {}
            if scope.get("script_id") not in (None, script_id):
                continue
            segment_ids = scope.get("segment_ids", [])
            if "*" in segment_ids or segment_id in segment_ids:
                return True
        return False


def apply_event(state: State, event: dict[str, Any]) -> None:
    """把单个事件折叠进状态（纯函数式增量，不校验业务规则）。"""
    etype = event["event_type"]
    agg_type = event["aggregate_type"]
    agg_id = event["aggregate_id"]
    at = event["occurred_at"]
    body = event["payload"]

    if etype == "SCRIPT_REGISTERED":
        state.scripts[agg_id] = Script(
            script_id=agg_id,
            title=body["title"],
            language=body["language"],
            segments={
                s["segment_id"]: Segment(
                    segment_id=s["segment_id"],
                    kind=s.get("kind", "line"),
                    subject_ref=s.get("subject_ref"),
                    text=s["text"],
                )
                for s in body["segments"]
            },
            version=event["version"],
            registered_at=at,
        )
        return

    if etype == "CONSENT_RECORDED":
        state.consents[agg_id] = Consent(
            consent_id=agg_id,
            subject_ref=body["subject_ref"],
            scope=body.get("scope", {}),
            recorded_at=at,
        )
        return

    if etype == "CONSENT_WITHDRAWN":
        consent = state.consents[body["consent_id"]]
        consent.active = False
        consent.withdrawn_at = at
        return

    if etype == "VERSION_PROPOSED":
        state.versions[agg_id] = ReleaseVersion(
            version_id=agg_id,
            script_id=body["script_id"],
            target_language=body["target_language"],
            entries=list(body.get("entries", [])),
            notes=list(body.get("notes", [])),
            source_fingerprint=body["source_fingerprint"],
            content_fingerprint=body["content_fingerprint"],
            proposer_id=body["proposer_id"],
            proposed_at=at,
            version=event["version"],
        )
        return

    version = state.versions.get(agg_id)
    if etype == "VERSION_SIGNED" and version is not None:
        version.signatures[body["role"]] = {"actor_id": body["actor_id"], "at": at}
        version.version = event["version"]
        return
    if etype == "VERSION_APPROVED" and version is not None:
        version.approved = True
        version.approved_at = at
        version.final_approver_id = body["final_approver_id"]
        version.version = event["version"]
        return
    if etype == "REHEARSAL_CONFIRMED" and version is not None:
        record = version.confirmations.get(body["subject_ref"])
        segment_ids = list(body["segment_ids"])
        if record is None:
            version.confirmations[body["subject_ref"]] = {
                "confirmer_id": body["confirmer_id"],
                "segment_ids": segment_ids,
                "at": at,
            }
        else:
            merged = sorted(set(record["segment_ids"]) | set(segment_ids))
            record["segment_ids"] = merged
            record["at"] = at
        version.version = event["version"]
        return
    if etype == "ERRATA_ISSUED" and version is not None:
        version.supersedes = body["supersedes"]
        version.errata_reason = body["reason"]
        version.errata_issued_at = at
        previous = state.versions.get(body["supersedes"])
        if previous is not None:
            previous.superseded_by = agg_id
        version.version = event["version"]
        return

    if etype == "DISPUTE_OPENED":
        dispute = state.disputes.get(agg_id)
        if dispute is None:
            state.disputes[agg_id] = Dispute(
                dispute_id=agg_id,
                receipt_id=body["receipt_id"],
                run_id=body["run_id"],
                opened_at=at,
                fingerprints=[body["existing_fingerprint"], body["conflicting_fingerprint"]],
                version=event["version"],
            )
        else:
            if body["conflicting_fingerprint"] not in dispute.fingerprints:
                dispute.fingerprints.append(body["conflicting_fingerprint"])
            dispute.version = event["version"]
        return

    run = state.runs.get(agg_id)
    if etype == "RUN_PLANNED":
        state.runs[agg_id] = Run(
            run_id=agg_id,
            script_id=body["script_id"],
            starts_at=body["starts_at"],
            lock_deadline=body["lock_deadline"],
            audience_languages=list(body["audience_languages"]),
            planned_at=at,
            version=event["version"],
        )
        return
    if run is None:
        return
    if etype == "RUN_LOCKED":
        run.locked = True
        run.locked_at = at
        run.script_version = body["script_version"]
        run.selections = dict(body["selections"])
        run.redactions = {language: [] for language in body["selections"]}
        run.version = event["version"]
        return
    if etype == "RUN_ADJUSTED":
        run.adjustments.append(
            {
                "at": at,
                "actor_id": body["actor_id"],
                "changes": body["changes"],
                "reason": body.get("reason", ""),
            }
        )
        for change in body["changes"]:
            if change.get("op") == "select_version" and change.get("version_id"):
                run.selections[change["language"]] = change["version_id"]
            elif change.get("op") == "redact_segment":
                bucket = run.redactions.setdefault(change["language"], [])
                if change["segment_id"] not in bucket:
                    bucket.append(change["segment_id"])
        run.version = event["version"]
        return
    if etype == "RUN_RECEIPTED":
        run.receipts[body["receipt_id"]] = {
            "at": at,
            "language": body.get("language"),
            "content_fingerprint": body["content_fingerprint"],
            "actor_id": body["actor_id"],
        }
        run.version = event["version"]
        return


def replay(events: list[dict[str, Any]]) -> State:
    state = State()
    for event in events:
        apply_event(state, event)
    return state
