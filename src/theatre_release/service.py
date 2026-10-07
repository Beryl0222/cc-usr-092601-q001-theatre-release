"""跨语种演出版本服务：在基础事件契约之上落实业务规则。

规则要点：
- 亲历者只能确认涉及自己的事实；
- 译者、导演、发布人员会签，提交人不得终审自己的版本，三者必须互不同人；
- 场次锁定实际采用的版本与文本指纹，迟到修订只影响尚未开演的场次；
- 已经演出的场次只能通过勘误关联更正；
- 回执按 receipt_id 幂等，编号相同而指纹不同进入争议；
- 截稿、开演、撤回期限全部经由注入的时钟判定，重启后重放日志继续会签。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from .clock import ensure_aware
from .errors import ServiceError
from .events import (
    SCRIPT_STAGES,
    SEGMENT_KINDS,
    SIGNING_ROLES,
    fingerprint_content,
    fingerprint_text,
)
from .state import VersionState, World
from .store import JsonlStore


class ReleaseService:
    """演出版本服务入口；构造时重放事件日志，因而重启后可继续未完成会签。"""

    def __init__(self, store: JsonlStore, clock: Any) -> None:
        self._store = store
        self._clock = clock
        self._world = World()
        self._event_ids: set[str] = set()
        self._aggregate_versions: dict[str, int] = {}
        for event in store.load():
            self._replay(event)

    # ------------------------------------------------------------------
    # 基础设施
    # ------------------------------------------------------------------
    @property
    def world(self) -> World:
        return self._world

    def _replay(self, event: dict[str, Any]) -> None:
        self._event_ids.add(event["event_id"])
        self._aggregate_versions[event["aggregate_id"]] = event["version"]
        self._world.apply(event)

    def _already(self, event_id: Optional[str]) -> bool:
        return event_id is not None and event_id in self._event_ids

    def _emit(
        self,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        event_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        if self._already(event_id):
            return None
        version = self._aggregate_versions.get(aggregate_id, 0) + 1
        if event_id is None:
            event_id = f"evt-{self._clock.now():%Y%m%dT%H%M%S%f}-{len(self._event_ids) + 1:06d}"
        event = {
            "event_id": event_id,
            "event_type": event_type,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "occurred_at": self._clock.now().isoformat(),
            "version": version,
            "payload": payload,
        }
        self._store.append(event)
        self._replay(event)
        return event

    def _script(self, script_id: str):
        script = self._world.scripts.get(script_id)
        if script is None:
            raise ServiceError("script_not_found", f"剧目不存在: {script_id}")
        return script

    def _segment(self, segment_id: str):
        segment = self._world.segments.get(segment_id)
        if segment is None:
            raise ServiceError("segment_not_found", f"原文段落不存在: {segment_id}")
        return segment

    def _version(self, version_id: str) -> VersionState:
        version = self._world.versions.get(version_id)
        if version is None:
            raise ServiceError("version_not_found", f"版本不存在: {version_id}")
        return version

    def _run(self, run_id: str):
        run = self._world.runs.get(run_id)
        if run is None:
            raise ServiceError("run_not_found", f"场次不存在: {run_id}")
        return run

    # ------------------------------------------------------------------
    # 剧目阶段与原文段落
    # ------------------------------------------------------------------
    def register_script(
        self, script_id: str, title: str, stage: str = "draft", event_id: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        if self._already(event_id):
            return None
        if script_id in self._world.scripts:
            raise ServiceError("duplicate_script", f"剧目已存在: {script_id}")
        if stage not in SCRIPT_STAGES:
            raise ServiceError("invalid_stage", f"未知剧目阶段: {stage}")
        return self._emit(
            "SCRIPT_REGISTERED", "play_script", script_id, {"title": title, "stage": stage}, event_id
        )

    def advance_stage(self, script_id: str, to_stage: str, event_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        if self._already(event_id):
            return None
        script = self._script(script_id)
        if to_stage not in SCRIPT_STAGES:
            raise ServiceError("invalid_stage", f"未知剧目阶段: {to_stage}")
        if SCRIPT_STAGES.index(to_stage) <= SCRIPT_STAGES.index(script.stage):
            raise ServiceError("stage_not_forward", f"剧目阶段只能向前推进: {script.stage} -> {to_stage}")
        return self._emit(
            "STAGE_ADVANCED",
            "play_script",
            script_id,
            {"from_stage": script.stage, "to_stage": to_stage},
            event_id,
        )

    def add_segment(
        self,
        segment_id: str,
        script_id: str,
        order: int,
        text: str,
        kind: str = "dialogue",
        involves: Optional[list[str]] = None,
        event_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        if self._already(event_id):
            return None
        self._script(script_id)
        if segment_id in self._world.segments:
            raise ServiceError("duplicate_segment", f"原文段落已存在: {segment_id}")
        if kind not in SEGMENT_KINDS:
            raise ServiceError("invalid_segment_kind", f"未知段落类型: {kind}")
        if not isinstance(order, int) or isinstance(order, bool) or order < 1:
            raise ServiceError("invalid_order", "段落顺序必须是正整数")
        return self._emit(
            "SEGMENT_ADDED",
            "source_segment",
            segment_id,
            {
                "script_ref": script_id,
                "order": order,
                "text": text,
                "kind": kind,
                "involves": list(involves or []),
            },
            event_id,
        )

    def confirm_facts(self, segment_id: str, witness: str, event_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        """亲历者确认事实；只能确认涉及自己的段落。"""
        if self._already(event_id):
            return None
        segment = self._segment(segment_id)
        if witness not in segment.involves:
            raise ServiceError("witness_not_involved", "亲历者只能确认涉及自己的事实")
        return self._emit(
            "FACT_CONFIRMED",
            "source_segment",
            segment_id,
            {"segment_ref": segment_id, "witness": witness},
            event_id,
        )

    # ------------------------------------------------------------------
    # 人物原型授权
    # ------------------------------------------------------------------
    def record_consent(
        self,
        consent_id: str,
        subject_ref: str,
        script_id: str,
        withdraw_by: "str | datetime",
        segments: Optional[list[str]] = None,
        event_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        if self._already(event_id):
            return None
        self._script(script_id)
        if consent_id in self._world.consents:
            raise ServiceError("duplicate_consent", f"授权记录已存在: {consent_id}")
        deadline = ensure_aware(withdraw_by)
        scope = {"script_ref": script_id, "segments": list(segments) if segments else ["*"]}
        return self._emit(
            "CONSENT_RECORDED",
            "consent_record",
            consent_id,
            {"subject_ref": subject_ref, "scope": scope, "withdraw_by": deadline.isoformat()},
            event_id,
        )

    def withdraw_consent(self, consent_id: str, reason: str, event_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        if self._already(event_id):
            return None
        consent = self._world.consents.get(consent_id)
        if consent is None:
            raise ServiceError("consent_not_found", f"授权记录不存在: {consent_id}")
        if consent.status != "granted":
            raise ServiceError("consent_not_active", f"授权已撤回: {consent_id}")
        if self._clock.now() > consent.withdraw_by:
            raise ServiceError("withdrawal_deadline_passed", "已过撤回期限，授权不可撤回")
        return self._emit("CONSENT_WITHDRAWN", "consent_record", consent_id, {"reason": reason}, event_id)

    # ------------------------------------------------------------------
    # 译文候选与文化注释
    # ------------------------------------------------------------------
    def submit_translation(
        self,
        translation_id: str,
        segment_id: str,
        language: str,
        text: str,
        translator: str,
        event_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        if self._already(event_id):
            return None
        self._segment(segment_id)
        if translation_id in self._world.translations:
            raise ServiceError("duplicate_translation", f"译文候选已存在: {translation_id}")
        return self._emit(
            "TRANSLATION_SUBMITTED",
            "translation_candidate",
            translation_id,
            {
                "segment_ref": segment_id,
                "language": language,
                "text": text,
                "translator": translator,
                "fingerprint": fingerprint_text(text),
            },
            event_id,
        )

    def add_note(
        self,
        note_id: str,
        segment_id: str,
        language: str,
        text: str,
        author: str = "",
        event_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        if self._already(event_id):
            return None
        self._segment(segment_id)
        if note_id in self._world.notes:
            raise ServiceError("duplicate_note", f"文化注释已存在: {note_id}")
        return self._emit(
            "NOTE_ADDED",
            "cultural_note",
            note_id,
            {"segment_ref": segment_id, "language": language, "text": text, "author": author},
            event_id,
        )

    # ------------------------------------------------------------------
    # 版本会签
    # ------------------------------------------------------------------
    def submit_version(
        self,
        version_id: str,
        script_id: str,
        items: list[dict[str, Any]],
        submitted_by: str,
        event_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        if self._already(event_id):
            return None
        self._script(script_id)
        if version_id in self._world.versions:
            raise ServiceError("duplicate_version", f"版本已存在: {version_id}")
        if not items:
            raise ServiceError("empty_items", "版本至少要包含一个条目")
        normalized = []
        for item in items:
            segment = self._segment(item.get("segment_ref", ""))
            if segment.script_id != script_id:
                raise ServiceError("segment_script_mismatch", f"段落 {segment.segment_id} 不属于剧目 {script_id}")
            translation = self._world.translations.get(item.get("translation_ref", ""))
            if translation is None:
                raise ServiceError("translation_not_found", f"译文候选不存在: {item.get('translation_ref')}")
            if translation.segment_id != segment.segment_id:
                raise ServiceError("translation_segment_mismatch", "译文与段落不对应")
            if translation.language != item.get("language"):
                raise ServiceError("translation_language_mismatch", "译文语种与条目语种不一致")
            note_ids = list(item.get("note_refs", []))
            for note_id in note_ids:
                note = self._world.notes.get(note_id)
                if note is None:
                    raise ServiceError("note_not_found", f"文化注释不存在: {note_id}")
                if note.segment_id != segment.segment_id:
                    raise ServiceError("note_segment_mismatch", "注释与段落不对应")
            normalized.append(
                {
                    "segment_ref": segment.segment_id,
                    "language": translation.language,
                    "translation_ref": translation.translation_id,
                    "note_refs": note_ids,
                }
            )
        number = 1 + max(
            (v.number for v in self._world.versions.values() if v.script_id == script_id), default=0
        )
        return self._emit(
            "VERSION_SUBMITTED",
            "release_version",
            version_id,
            {"script_ref": script_id, "number": number, "items": normalized, "submitted_by": submitted_by},
            event_id,
        )

    def sign_version(
        self, version_id: str, role: str, signer: str, event_id: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        """会签：三种角色缺一不可，且任何人不得终审自己提交的版本。"""
        if self._already(event_id):
            return None
        version = self._version(version_id)
        if version.status != "in_review":
            raise ServiceError("version_already_approved", f"版本已终审: {version_id}")
        if role not in SIGNING_ROLES:
            raise ServiceError("invalid_role", f"未知会签角色: {role}")
        if signer == version.submitted_by:
            raise ServiceError("submitter_cannot_approve", "提交人不得独自完成同一版本的提交与终审")
        if any(sig.role == role for sig in version.signatures):
            raise ServiceError("role_already_signed", f"角色已会签: {role}")
        if any(sig.signer == signer for sig in version.signatures):
            raise ServiceError("signer_conflict", "同一人员不得以多个角色会签同一版本")
        event = self._emit(
            "VERSION_SIGNED", "release_version", version_id, {"role": role, "signer": signer}, event_id
        )
        signed_roles = {sig.role for sig in version.signatures}
        if signed_roles == set(SIGNING_ROLES):
            self._emit(
                "VERSION_APPROVED",
                "release_version",
                version_id,
                {
                    "number": version.number,
                    "script_ref": version.script_id,
                    "signers": [
                        {"role": sig.role, "signer": sig.signer, "at": sig.at.isoformat()}
                        for sig in version.signatures
                    ],
                },
            )
        return event

    def confirm_rehearsal(
        self, version_id: str, confirmer: str, event_id: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        if self._already(event_id):
            return None
        version = self._version(version_id)
        if version.rehearsal_confirmer is not None:
            raise ServiceError("rehearsal_already_confirmed", f"版本已完成排练确认: {version_id}")
        return self._emit(
            "REHEARSAL_CONFIRMED", "release_version", version_id, {"confirmer": confirmer}, event_id
        )

    # ------------------------------------------------------------------
    # 场次计划与锁定
    # ------------------------------------------------------------------
    def schedule_run(
        self,
        run_id: str,
        script_id: str,
        starts_at: "str | datetime",
        submission_cutoff: "str | datetime",
        event_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        if self._already(event_id):
            return None
        script = self._script(script_id)
        if run_id in self._world.runs:
            raise ServiceError("duplicate_run", f"场次已存在: {run_id}")
        if script.stage != "resident":
            raise ServiceError("script_not_resident", "只有驻演阶段的剧目可以排期")
        start = ensure_aware(starts_at)
        cutoff = ensure_aware(submission_cutoff)
        if cutoff >= start:
            raise ServiceError("invalid_schedule", "截稿时间必须早于开演时间")
        return self._emit(
            "RUN_SCHEDULED",
            "performance_run",
            run_id,
            {
                "script_ref": script_id,
                "starts_at": start.isoformat(),
                "submission_cutoff": cutoff.isoformat(),
            },
            event_id,
        )

    def request_language(self, run_id: str, language: str, event_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        if self._already(event_id):
            return None
        self._run(run_id)
        return self._emit("LANGUAGE_REQUESTED", "performance_run", run_id, {"language": language}, event_id)

    def _content_fingerprint(
        self,
        version: VersionState,
        live_change_ids: Optional[list[str]] = None,
        pending_change: Optional[tuple[str, str, str]] = None,
    ) -> str:
        overrides: dict[tuple[str, str], str] = {}
        for change_id in live_change_ids or []:
            change = self._world.live_changes[change_id]
            overrides[(change.segment_id, change.language)] = change.text
        if pending_change is not None:
            segment_id, language, text = pending_change
            overrides[(segment_id, language)] = text
        rows = []
        for item in version.items:
            translation = self._world.translations[item.translation_id]
            text = overrides.get((item.segment_id, item.language), translation.text)
            notes = sorted(self._world.notes[note_id].text for note_id in item.note_ids)
            rows.append([item.segment_id, item.language, text, notes])
        rows.sort(key=lambda row: (self._world.segments[row[0]].order, row[1]))
        return fingerprint_content(rows)

    def lock_run(self, run_id: str, version_id: str, event_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        if self._already(event_id):
            return None
        run = self._run(run_id)
        version = self._version(version_id)
        if run.status != "scheduled":
            raise ServiceError("run_not_scheduled", f"场次不在待锁定状态: {run.status}")
        if self._clock.now() >= run.starts_at:
            raise ServiceError("run_already_started", "场次已开演，不能再锁定")
        if version.script_id != run.script_id:
            raise ServiceError("version_script_mismatch", "版本与场次剧目不一致")
        if version.status != "approved":
            raise ServiceError("version_not_approved", "版本尚未完成会签终审")
        if version.rehearsal_confirmer is None:
            raise ServiceError("rehearsal_not_confirmed", "版本尚未完成排练确认")
        if version.submitted_at > run.submission_cutoff:
            raise ServiceError("submitted_after_cutoff", "版本提交晚于该场次截稿时间")
        fingerprint = self._content_fingerprint(version)
        return self._emit(
            "RUN_LOCKED",
            "performance_run",
            run_id,
            {
                "script_version": version.number,
                "starts_at": run.starts_at.isoformat(),
                "version_ref": version_id,
                "fingerprint": fingerprint,
            },
            event_id,
        )

    def revise_run(self, run_id: str, version_id: str, event_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        """迟到修订：只影响尚未开演的场次；已演出的场次只能走勘误。"""
        if self._already(event_id):
            return None
        run = self._run(run_id)
        version = self._version(version_id)
        if run.status in ("performed", "disputed"):
            raise ServiceError("performed_run_requires_errata", "场次已经演出，请通过勘误关联更正")
        if run.status != "locked":
            raise ServiceError("run_not_locked", f"场次尚未锁定: {run.status}")
        if self._clock.now() >= run.starts_at:
            raise ServiceError("run_already_started", "场次已开演，迟到修订不再生效")
        if version.script_id != run.script_id:
            raise ServiceError("version_script_mismatch", "版本与场次剧目不一致")
        if version.status != "approved":
            raise ServiceError("version_not_approved", "版本尚未完成会签终审")
        if version.number <= (run.locked_number or 0):
            raise ServiceError("not_a_newer_revision", "迟到修订必须采用更新的版本编号")
        fingerprint = self._content_fingerprint(version, run.live_change_ids)
        return self._emit(
            "RUN_REVISED",
            "performance_run",
            run_id,
            {
                "version_ref": version_id,
                "script_version": version.number,
                "fingerprint": fingerprint,
            },
            event_id,
        )

    def record_live_change(
        self,
        change_id: str,
        run_id: str,
        segment_id: str,
        language: str,
        text: str,
        reason: str,
        event_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        if self._already(event_id):
            return None
        run = self._run(run_id)
        if run.status != "locked":
            raise ServiceError("run_not_locked", "只有已锁定且未开演的场次可以登记临场变更")
        segment = self._segment(segment_id)
        if segment.script_id != run.script_id:
            raise ServiceError("segment_script_mismatch", "段落不属于该场次剧目")
        version = self._version(run.locked_version_id)
        # 指纹按“变更已生效”计算后随事件落盘，保证重放结果一致
        fingerprint = self._content_fingerprint(
            version, run.live_change_ids, pending_change=(segment_id, language, text)
        )
        payload = {
            "run_ref": run_id,
            "segment_ref": segment_id,
            "language": language,
            "text": text,
            "reason": reason,
            "fingerprint": fingerprint,
        }
        return self._emit("LIVE_CHANGE_RECORDED", "live_change", change_id, payload, event_id)

    # ------------------------------------------------------------------
    # 回执、争议与勘误
    # ------------------------------------------------------------------
    def record_receipt(
        self,
        run_id: str,
        receipt_id: str,
        version_number: int,
        fingerprint: str,
        event_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """场次回执：同一 receipt_id 重传幂等；编号相同而指纹不同进入争议。"""
        if self._already(event_id):
            run = self._run(run_id)
            receipt = run.receipts.get(receipt_id)
            return self._receipt_result(run_id, receipt_id, receipt.outcome if receipt else None, idempotent=True)
        run = self._run(run_id)
        if run.locked_version_id is None:
            raise ServiceError("run_not_locked", "场次尚未锁定，无法接收回执")
        existing = run.receipts.get(receipt_id)
        if existing is not None:
            return self._receipt_result(run_id, receipt_id, existing.outcome, idempotent=True)
        if version_number != run.locked_number:
            raise ServiceError(
                "receipt_version_mismatch",
                f"回执编号 {version_number} 与锁定编号 {run.locked_number} 不一致",
            )
        outcome = "accepted" if fingerprint == run.fingerprint else "disputed"
        self._emit(
            "RECEIPT_RECORDED",
            "performance_run",
            run_id,
            {
                "receipt_id": receipt_id,
                "version_number": version_number,
                "fingerprint": fingerprint,
                "outcome": outcome,
            },
            event_id,
        )
        if outcome == "disputed":
            self._emit(
                "DISPUTE_RAISED",
                "dispute_case",
                f"{run_id}:{receipt_id}",
                {
                    "run_ref": run_id,
                    "receipt_id": receipt_id,
                    "expected_fingerprint": run.fingerprint,
                    "actual_fingerprint": fingerprint,
                },
            )
        return self._receipt_result(run_id, receipt_id, outcome, idempotent=False)

    @staticmethod
    def _receipt_result(run_id: str, receipt_id: str, outcome: Optional[str], idempotent: bool) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "receipt_id": receipt_id,
            "outcome": outcome,
            "idempotent_replay": idempotent,
        }

    def issue_errata(
        self,
        errata_id: str,
        run_id: str,
        reason: str,
        correction: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """勘误：只面向已经演出的场次，沿版本链关联保留。"""
        if self._already(event_id):
            return None
        run = self._run(run_id)
        if run.status not in ("performed", "disputed"):
            raise ServiceError("run_not_performed", "只有已经演出的场次才能登记勘误")
        if errata_id in self._world.errata:
            raise ServiceError("duplicate_errata", f"勘误已存在: {errata_id}")
        previous = [e for e in self._world.errata.values() if e.run_id == run_id]
        supersedes = max(previous, key=lambda e: e.issued_at).errata_id if previous else run.locked_version_id
        return self._emit(
            "ERRATA_ISSUED",
            "release_version",
            run.locked_version_id,
            {
                "errata_id": errata_id,
                "run_ref": run_id,
                "supersedes": supersedes,
                "reason": reason,
                "correction": correction,
            },
            event_id,
        )

    # ------------------------------------------------------------------
    # 面向观众的当晚内容
    # ------------------------------------------------------------------
    def _consent_granted(self, segment) -> bool:
        if segment.kind != "oral_account":
            return True
        for person in segment.involves:
            granted = any(
                consent.status == "granted"
                and consent.subject_ref == person
                and consent.script_id == segment.script_id
                and consent.covers(segment.segment_id)
                for consent in self._world.consents.values()
            )
            if not granted:
                return False
        return True

    def run_content(self, run_id: str, language: str) -> dict[str, Any]:
        """按观众语言给出该场次的有效内容；未授权口述整段隐藏。"""
        run = self._run(run_id)
        if run.locked_version_id is None:
            raise ServiceError("run_not_locked", "场次尚未锁定，暂无有效内容")
        version = self._version(run.locked_version_id)
        items = {(item.segment_id, item.language): item for item in version.items}
        overrides: dict[tuple[str, str], str] = {}
        for change_id in run.live_change_ids:
            change = self._world.live_changes[change_id]
            overrides[(change.segment_id, change.language)] = change.text
        segments = sorted(
            (s for s in self._world.segments.values() if s.script_id == run.script_id),
            key=lambda s: s.order,
        )
        rendered = []
        for segment in segments:
            if not self._consent_granted(segment):
                rendered.append(
                    {
                        "segment_id": segment.segment_id,
                        "order": segment.order,
                        "kind": segment.kind,
                        "hidden": True,
                        "reason": "consent_missing",
                    }
                )
                continue
            item = items.get((segment.segment_id, language))
            translation_text = None
            notes: list[str] = []
            if item is not None:
                translation_text = self._world.translations[item.translation_id].text
                notes = [self._world.notes[note_id].text for note_id in item.note_ids]
            live = overrides.get((segment.segment_id, language))
            entry = {
                "segment_id": segment.segment_id,
                "order": segment.order,
                "kind": segment.kind,
                "hidden": False,
                "source": segment.text,
                "translation": translation_text,
                "notes": notes,
                "live_changed": live is not None,
            }
            if live is not None:
                entry["translation"] = live
            rendered.append(entry)
        return {
            "run_id": run.run_id,
            "script_id": run.script_id,
            "starts_at": run.starts_at.isoformat(),
            "status": run.status,
            "language": language,
            "version": run.locked_number,
            "fingerprint": run.fingerprint,
            "segments": rendered,
        }

    def tonight(self, language: str, on_date: Optional["str | date"] = None) -> dict[str, Any]:
        """当晚有效内容：取指定日期（默认时钟当天）最早一场已锁定场次。"""
        if on_date is None:
            day = self._clock.now().date()
        elif isinstance(on_date, str):
            day = date.fromisoformat(on_date)
        else:
            day = on_date
        candidates = [
            run
            for run in self._world.runs.values()
            if run.starts_at.date() == day and run.locked_version_id is not None
        ]
        if not candidates:
            raise ServiceError("no_performance_tonight", f"{day.isoformat()} 当晚没有已锁定场次")
        run = min(candidates, key=lambda r: r.starts_at)
        return self.run_content(run.run_id, language)
