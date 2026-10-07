"""跨语种演出版本领域服务。

所有写操作都由调用方给出显式 ``event_id``（重传幂等的锚点），发生时间取自可控时钟。
命令成功后事件先落盘再折叠进内存状态；进程重启时重放日志即可继续未完成会签。
"""

from __future__ import annotations

import threading
from typing import Any

from .clock import Clock, parse_datetime
from .errors import (
    AuthorizationError,
    ConflictError,
    DeadlineError,
    NotFoundError,
    StateError,
)
from .fingerprint import canonical_text, content_fingerprint
from .state import ReleaseVersion, Run, State, apply_event, replay
from .store import EventStore

ROLE_TRANSLATOR = "translator"
ROLE_DIRECTOR = "director"
ROLE_PUBLISHER = "publisher"
SIGN_ROLES = (ROLE_TRANSLATOR, ROLE_DIRECTOR, ROLE_PUBLISHER)
TESTIMONY = "testimony"


class DisputeRaised(ConflictError):
    """回执触发争议；争议事件已落盘，携带其编号。"""

    def __init__(self, dispute_id: str, event: dict) -> None:
        super().__init__(f"回执编号相同但文本指纹不同，已立争议 {dispute_id}")
        self.dispute_id = dispute_id
        self.event = event


class TheatreService:
    def __init__(self, store: EventStore, clock: Clock) -> None:
        self.store = store
        self.clock = clock
        self._lock = threading.RLock()
        self.state: State = replay(store.events)

    # -- 内部件 -----------------------------------------------------------

    def _emit(self, event_type: str, aggregate_type: str, aggregate_id: str, payload: dict, event_id: str) -> dict:
        existing = self.store.get(event_id)
        if existing is not None:
            # 重启后或网络重传后重试同一命令：事件早已落盘，原样幂等返回。
            return existing
        event = {
            "event_id": event_id,
            "event_type": event_type,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "occurred_at": self.clock.now().isoformat(),
            "version": self.store.next_version(aggregate_type, aggregate_id),
            "payload": payload,
        }
        stored = self.store.append(event)
        apply_event(self.state, stored)
        return stored

    def _replayed(self, event_id: str) -> dict | None:
        """同一 event_id 的重传（含重启后）一律幂等返回原事件。"""
        return self.store.get(event_id)

    def _script(self, script_id: str):
        script = self.state.scripts.get(script_id)
        if script is None:
            raise NotFoundError(f"剧目不存在: {script_id}")
        return script

    def _version(self, version_id: str) -> ReleaseVersion:
        version = self.state.versions.get(version_id)
        if version is None:
            raise NotFoundError(f"译文版本不存在: {version_id}")
        return version

    def _run(self, run_id: str) -> Run:
        run = self.state.runs.get(run_id)
        if run is None:
            raise NotFoundError(f"场次不存在: {run_id}")
        return run

    @staticmethod
    def _source_snapshot(script, segment_ids: list[str]) -> list[dict[str, str]]:
        ordered = [s for s in script.segments.values() if s.segment_id in set(segment_ids)]
        return [{"segment_id": s.segment_id, "kind": s.kind, "text": s.text} for s in ordered]

    # -- 剧目与原文 -------------------------------------------------------

    def register_script(
        self,
        script_id: str,
        title: str,
        language: str,
        segments: list[dict[str, Any]],
        event_id: str,
    ) -> dict:
        with self._lock:
            if (prior := self._replayed(event_id)) is not None:
                return prior
            if script_id in self.state.scripts:
                raise ConflictError(f"剧目已注册: {script_id}")
            ids = [s.get("segment_id") for s in segments]
            if not all(ids) or len(set(ids)) != len(ids):
                raise StateError("原文段落编号缺失或重复")
            payload = {"title": title, "language": language, "segments": segments}
            return self._emit("SCRIPT_REGISTERED", "play_script", script_id, payload, event_id)

    # -- 人物原型授权 -----------------------------------------------------

    def record_consent(
        self,
        consent_id: str,
        subject_ref: str,
        scope: dict[str, Any],
        confirmer_id: str,
        event_id: str,
    ) -> dict:
        """登记亲历者授权。scope 形如 {script_id, segment_ids:[...], withdrawal_deadline?}。"""
        with self._lock:
            if (prior := self._replayed(event_id)) is not None:
                return prior
            if consent_id in self.state.consents:
                raise ConflictError(f"授权已登记: {consent_id}")
            if confirmer_id != subject_ref:
                raise AuthorizationError("只有亲历者本人可以登记本人授权")
            if not scope.get("segment_ids"):
                raise StateError("授权范围必须显式列出段落")
            payload = {"subject_ref": subject_ref, "scope": scope}
            return self._emit("CONSENT_RECORDED", "consent_record", consent_id, payload, event_id)

    def withdraw_consent(self, consent_id: str, actor_id: str, event_id: str) -> dict:
        with self._lock:
            if (prior := self._replayed(event_id)) is not None:
                return prior
            consent = self.state.consents.get(consent_id)
            if consent is None:
                raise NotFoundError(f"授权不存在: {consent_id}")
            if actor_id != consent.subject_ref:
                raise AuthorizationError("只有亲历者本人可以撤回本人授权")
            if not consent.active:
                raise StateError("授权已撤回")
            deadline = (consent.scope or {}).get("withdrawal_deadline")
            if deadline is not None and self.clock.now() > parse_datetime(deadline):
                raise DeadlineError(f"已超过授权撤回期限: {deadline}")
            payload = {"consent_id": consent_id, "subject_ref": consent.subject_ref}
            return self._emit("CONSENT_WITHDRAWN", "consent_record", consent_id, payload, event_id)

    # -- 译文候选与三会签 -------------------------------------------------

    def propose_version(
        self,
        version_id: str,
        script_id: str,
        target_language: str,
        proposer_id: str,
        entries: list[dict[str, Any]],
        notes: list[dict[str, Any]] | None,
        event_id: str,
    ) -> dict:
        with self._lock:
            if (prior := self._replayed(event_id)) is not None:
                return prior
            script = self._script(script_id)
            if version_id in self.state.versions:
                raise ConflictError(f"译文版本已存在: {version_id}")
            segment_ids = [e["segment_id"] for e in entries]
            if len(set(segment_ids)) != len(segment_ids):
                raise StateError("同一译文版本中段落重复")
            for sid in segment_ids:
                if sid not in script.segments:
                    raise NotFoundError(f"原文段落不存在: {sid}")
            body = {
                "entries": entries,
                "notes": notes or [],
            }
            payload = {
                "script_id": script_id,
                "target_language": target_language,
                "source_fingerprint": content_fingerprint(self._source_snapshot(script, segment_ids)),
                "content_fingerprint": content_fingerprint(body),
                "proposer_id": proposer_id,
                **body,
            }
            return self._emit("VERSION_PROPOSED", "release_version", version_id, payload, event_id)

    def sign_version(self, version_id: str, role: str, actor_id: str, event_id: str) -> dict:
        with self._lock:
            if (prior := self._replayed(event_id)) is not None:
                return prior
            version = self._version(version_id)
            if version.approved:
                raise StateError("版本已终审，不能补签")
            if role not in SIGN_ROLES:
                raise StateError(f"会签角色必须是 {SIGN_ROLES} 之一")
            if any(sig["actor_id"] == actor_id for sig in version.signatures.values()):
                raise AuthorizationError("同一人不能以不同角色会签")
            if role in version.signatures:
                raise ConflictError(f"角色 {role} 已完成会签")
            payload = {"version_id": version_id, "role": role, "actor_id": actor_id}
            return self._emit("VERSION_SIGNED", "release_version", version_id, payload, event_id)

    def confirm_rehearsal(
        self,
        version_id: str,
        subject_ref: str,
        confirmer_id: str,
        segment_ids: list[str],
        event_id: str,
    ) -> dict:
        """亲历者只确认涉及本人的口述事实；可分批确认，重启后继续累积。"""
        with self._lock:
            if (prior := self._replayed(event_id)) is not None:
                return prior
            version = self._version(version_id)
            script = self._script(version.script_id)
            if version.approved:
                raise StateError("版本已终审，排练确认只能发生在终审前")
            if confirmer_id != subject_ref:
                raise AuthorizationError("亲历者只能确认涉及自己的事实")
            entry_segments = {e["segment_id"] for e in version.entries}
            for sid in segment_ids:
                segment = script.segments.get(sid)
                if segment is None or sid not in entry_segments:
                    raise NotFoundError(f"本版本未收录段落: {sid}")
                if segment.kind != TESTIMONY or segment.subject_ref != subject_ref:
                    raise AuthorizationError(f"段落 {sid} 不属于该亲历者的口述")
            payload = {
                "version_id": version_id,
                "subject_ref": subject_ref,
                "confirmer_id": confirmer_id,
                "segment_ids": segment_ids,
            }
            return self._emit("REHEARSAL_CONFIRMED", "release_version", version_id, payload, event_id)

    def approve_version(self, version_id: str, final_approver_id: str, event_id: str) -> dict:
        """终审：译者/导演/发布三方齐备且互不相同，终审人不得是提交人。"""
        with self._lock:
            if (prior := self._replayed(event_id)) is not None:
                return prior
            version = self._version(version_id)
            script = self._script(version.script_id)
            if version.approved:
                raise StateError("版本已终审")
            missing = [role for role in SIGN_ROLES if role not in version.signatures]
            if missing:
                raise StateError(f"会签未齐备，缺少: {missing}")
            actors = [sig["actor_id"] for sig in version.signatures.values()]
            if len(set(actors)) != 3:
                raise AuthorizationError("三方会签必须由三个不同的人完成")
            publisher = version.signatures[ROLE_PUBLISHER]["actor_id"]
            if final_approver_id != publisher:
                raise AuthorizationError("终审必须由完成发布会签的人执行")
            if final_approver_id == version.proposer_id:
                raise AuthorizationError("提交人与终审人不得为同一人")
            # 涉及真实人物的口述：授权有效且本人已逐条排练确认。
            for entry in version.entries:
                segment = script.segments[entry["segment_id"]]
                if segment.kind != TESTIMONY:
                    continue
                subject = segment.subject_ref
                confirmation = version.confirmations.get(subject)
                if not confirmation or entry["segment_id"] not in confirmation["segment_ids"]:
                    raise AuthorizationError(f"亲历者 {subject} 尚未排练确认段落 {entry['segment_id']}")
                if not self.state.consent_active_for(subject, version.script_id, entry["segment_id"]):
                    deadline_note = ""
                    consent = next(
                        (c for c in self.state.consents.values() if c.subject_ref == subject and not c.active),
                        None,
                    )
                    if consent is not None:
                        deadline_note = "（授权已撤回）"
                    raise AuthorizationError(f"段落 {entry['segment_id']} 缺少有效人物授权{deadline_note}")
            payload = {"version_id": version_id, "final_approver_id": final_approver_id}
            return self._emit("VERSION_APPROVED", "release_version", version_id, payload, event_id)

    # -- 勘误：迟到修订只影响尚未开演的场次 ------------------------------

    def issue_errata(
        self,
        old_version_id: str,
        replacement_version_id: str,
        reason: str,
        actor_id: str,
        event_id: str,
    ) -> dict:
        with self._lock:
            if (prior := self._replayed(event_id)) is not None:
                return prior
            old = self._version(old_version_id)
            replacement = self._version(replacement_version_id)
            if not replacement.approved:
                raise StateError("勘误替换版本必须已经完成终审")
            if replacement.script_id != old.script_id or replacement.target_language != old.target_language:
                raise StateError("勘误只能关联同一剧目、同一目标语种的版本")
            if old.superseded_by is not None:
                raise ConflictError(f"旧版本已被 {old.superseded_by} 勘误")
            if replacement.supersedes is not None:
                raise ConflictError("替换版本不能再次作为勘误挂接其他版本")
            if replacement_version_id == old_version_id:
                raise StateError("勘误版本不能指向自身")
            payload = {
                "supersedes": old_version_id,
                "replacement_version_id": replacement_version_id,
                "reason": reason,
                "actor_id": actor_id,
            }
            return self._emit(
                "ERRATA_ISSUED", "release_version", replacement_version_id, payload, event_id
            )

    # -- 场次：计划、截稿锁场、临场变更、回执 ----------------------------

    def plan_run(
        self,
        run_id: str,
        script_id: str,
        starts_at: str,
        lock_deadline: str,
        audience_languages: list[str],
        event_id: str,
    ) -> dict:
        with self._lock:
            if (prior := self._replayed(event_id)) is not None:
                return prior
            self._script(script_id)
            if run_id in self.state.runs:
                raise ConflictError(f"场次已排定: {run_id}")
            start = parse_datetime(starts_at)
            deadline = parse_datetime(lock_deadline)
            if deadline > start:
                raise StateError("截稿时刻不得晚于开演时刻")
            if not audience_languages or len(set(audience_languages)) != len(audience_languages):
                raise StateError("观众语言需求不能为空且不能重复")
            payload = {
                "script_id": script_id,
                "starts_at": starts_at,
                "lock_deadline": lock_deadline,
                "audience_languages": list(audience_languages),
            }
            return self._emit("RUN_PLANNED", "performance_run", run_id, payload, event_id)

    def lock_run(self, run_id: str, selections: dict[str, str], actor_id: str, event_id: str) -> dict:
        """截稿前锁定当晚每个语种实际采用的译文版本。"""
        with self._lock:
            if (prior := self._replayed(event_id)) is not None:
                return prior
            run = self._run(run_id)
            now = self.clock.now()
            if run.locked:
                raise StateError("场次已锁场")
            if now > parse_datetime(run.lock_deadline):
                raise DeadlineError(f"已过截稿时刻 {run.lock_deadline}，不能再锁场")
            if set(selections) != set(run.audience_languages):
                raise StateError("锁场语种必须与观众语言需求逐一对应")
            script = self._script(run.script_id)
            for language, version_id in selections.items():
                version = self._version(version_id)
                if not version.approved:
                    raise StateError(f"{language} 选用的版本尚未终审: {version_id}")
                if version.script_id != run.script_id or version.target_language != language:
                    raise StateError(f"{language} 选用的版本语种或剧目不匹配")
                if version.superseded_by is not None:
                    raise StateError(f"{language} 不能锁场已被勘误的版本 {version_id}")
            payload = {
                "version_id": ",".join(sorted(selections.values())),
                "script_version": script.version,
                "starts_at": run.starts_at,
                "selections": dict(selections),
                "actor_id": actor_id,
            }
            return self._emit("RUN_LOCKED", "performance_run", run_id, payload, event_id)

    def adjust_run(
        self,
        run_id: str,
        changes: list[dict[str, Any]],
        actor_id: str,
        event_id: str,
        reason: str = "",
    ) -> dict:
        """锁场后、开演前的临场调整；开演后冻结，实际情况以回执为准。"""
        with self._lock:
            if (prior := self._replayed(event_id)) is not None:
                return prior
            run = self._run(run_id)
            if not run.locked:
                raise StateError("场次尚未锁场")
            now = self.clock.now()
            if now >= parse_datetime(run.starts_at):
                raise DeadlineError("已开演，临场调整通道关闭；实际采用内容以回执留存")
            script = self._script(run.script_id)
            for change in changes:
                op = change.get("op")
                if op == "select_version":
                    language = change.get("language")
                    version = self._version(change.get("version_id", ""))
                    if language not in run.audience_languages:
                        raise StateError(f"语种不在当晚需求中: {language}")
                    if not version.approved or version.script_id != run.script_id:
                        raise StateError("临场换用的版本必须已终审且属于同一剧目")
                    if version.target_language != language or version.superseded_by is not None:
                        raise StateError("临场换用的版本语种不符或已被勘误")
                elif op == "redact_segment":
                    language = change.get("language")
                    segment = script.segments.get(change.get("segment_id", ""))
                    if segment is None:
                        raise NotFoundError(f"段落不存在: {change.get('segment_id')}")
                    if language not in run.audience_languages:
                        raise StateError(f"语种不在当晚需求中: {language}")
                else:
                    raise StateError(f"不支持的临场调整操作: {op}")
            payload = {
                "run_id": run_id,
                "actor_id": actor_id,
                "changes": changes,
                "reason": reason,
            }
            return self._emit("RUN_ADJUSTED", "performance_run", run_id, payload, event_id)

    def effective_content(
        self, run_id: str, language: str, *, now: Any = None
    ) -> dict[str, Any]:
        """当晚对外有效内容。未开演场次跟随勘误链头；已开演场次冻结现场快照。"""
        with self._lock:
            run = self._run(run_id)
            if language not in run.audience_languages:
                raise NotFoundError(f"当晚不提供该语种: {language}")
            moment = self.clock.now() if now is None else now
            started = moment >= parse_datetime(run.starts_at)
            selected = run.selections.get(language)
            if selected is None:
                return {
                    "run_id": run_id,
                    "language": language,
                    "status": "planned" if not run.locked else "locked",
                    "started": started,
                    "entries": [],
                    "notes": [],
                }
            # 未开演：跟随勘误链最新头；已开演：冻结开演时刻的链头，迟到勘误不再改写当晚。
            bound = run.starts_at if started else None
            effective_id = self._chain_head_at(selected, bound)
            version = self.state.versions[effective_id]
            script = self.state.scripts[version.script_id]
            redactions = set(run.redactions.get(language, []))
            hidden: list[dict[str, str]] = []
            entries: list[dict[str, Any]] = []
            for entry in version.entries:
                segment = script.segments.get(entry["segment_id"])
                if segment is None:
                    continue
                if entry["segment_id"] in redactions:
                    hidden.append({"segment_id": segment.segment_id, "reason": "临场遮盖"})
                    continue
                if segment.kind == TESTIMONY and not self.state.consent_active_for(
                    segment.subject_ref, version.script_id, segment.segment_id
                ):
                    hidden.append({"segment_id": segment.segment_id, "reason": "未授权口述"})
                    continue
                entries.append(
                    {
                        "segment_id": segment.segment_id,
                        "kind": segment.kind,
                        "source_text": segment.text,
                        "text": entry["text"],
                        "subject_ref": segment.subject_ref,
                    }
                )
            return {
                "run_id": run_id,
                "language": language,
                "status": "performed" if started else "tonight",
                "started": started,
                "starts_at": run.starts_at,
                "selected_version": selected,
                "effective_version": effective_id,
                "errata_followed": effective_id != selected,
                "entries": entries,
                "notes": version.notes,
                "hidden": hidden,
                "content_fingerprint": self._performed_fingerprint(run, language, version),
            }

    def _chain_head_at(self, version_id: str, bound_iso: str | None) -> str:
        """勘误链在某一时间点的链头；bound 为 None 表示取当前最新。"""
        current = version_id
        seen = set()
        while current not in seen:
            seen.add(current)
            successor_id = self.state.versions[current].superseded_by
            if successor_id is None:
                return current
            successor = self.state.versions[successor_id]
            if bound_iso is not None and successor.errata_issued_at is not None:
                if parse_datetime(successor.errata_issued_at) >= parse_datetime(bound_iso):
                    return current
            current = successor_id
        return current

    def _performed_fingerprint(self, run: Run, language: str, version: ReleaseVersion) -> str:
        """当晚实际呈现内容的指纹：排除临场遮盖与未授权口述，与对外内容口径一致。"""
        redactions = set(run.redactions.get(language, []))
        script = self.state.scripts[version.script_id]
        visible_entries = []
        for entry in version.entries:
            segment = script.segments.get(entry["segment_id"])
            if segment is None or entry["segment_id"] in redactions:
                continue
            if segment.kind == TESTIMONY and not self.state.consent_active_for(
                segment.subject_ref, version.script_id, segment.segment_id
            ):
                continue
            visible_entries.append(entry)
        body = {"entries": visible_entries, "notes": version.notes}
        return content_fingerprint(body)

    def record_receipt(
        self,
        run_id: str,
        receipt_id: str,
        language: str,
        observed_fingerprint: str,
        actor_id: str,
        event_id: str,
    ) -> dict:
        """开演后回执：编号+指纹一致幂等；编号相同指纹不同立争议。"""
        with self._lock:
            replay_event = self.store.get(event_id)
            if replay_event is not None:
                # 触发过争议的请求重传也要幂等：还原同一个争议编号。
                if replay_event["event_type"] == "DISPUTE_OPENED":
                    raise DisputeRaised(replay_event["aggregate_id"], replay_event)
                return {"idempotent": True, "receipt_id": replay_event["payload"]["receipt_id"]}
            run = self._run(run_id)
            now = self.clock.now()
            if not run.locked:
                raise StateError("未锁场的场次不能回执")
            if now < parse_datetime(run.starts_at):
                raise DeadlineError("尚未开演，不能提交演出回执")
            if language not in run.audience_languages:
                raise NotFoundError(f"当晚不提供该语种: {language}")
            already_disputed = any(
                d.run_id == run_id and d.receipt_id == receipt_id for d in self.state.disputes.values()
            )
            if already_disputed:
                raise ConflictError(f"回执 {receipt_id} 已进入争议，需人工裁定，不再自动接受")
            existing = run.receipts.get(receipt_id)
            if existing is not None:
                if existing["content_fingerprint"] == observed_fingerprint:
                    return {"idempotent": True, "receipt_id": receipt_id}
                return self._open_dispute(
                    run, receipt_id, language, existing["content_fingerprint"], observed_fingerprint, event_id
                )
            selected = run.selections[language]
            # 回执以开演时刻冻结的勘误链头为准，之后发出的勘误不参与比对。
            effective_id = self._chain_head_at(selected, run.starts_at)
            version = self.state.versions[effective_id]
            expected = self._performed_fingerprint(run, language, version)
            if observed_fingerprint != expected:
                return self._open_dispute(
                    run, receipt_id, language, expected, observed_fingerprint, event_id
                )
            payload = {
                "run_id": run_id,
                "receipt_id": receipt_id,
                "language": language,
                "selected_version": selected,
                "effective_version": effective_id,
                "content_fingerprint": observed_fingerprint,
                "actor_id": actor_id,
            }
            stored = self._emit("RUN_RECEIPTED", "performance_run", run_id, payload, event_id)
            return {"idempotent": False, "event": stored}

    def _open_dispute(
        self,
        run: Run,
        receipt_id: str,
        language: str,
        existing_fingerprint: str,
        conflicting_fingerprint: str,
        event_id: str,
    ) -> dict:
        dispute_id = f"dispute-{run.run_id}-{receipt_id}"
        payload = {
            "receipt_id": receipt_id,
            "run_id": run.run_id,
            "language": language,
            "existing_fingerprint": existing_fingerprint,
            "conflicting_fingerprint": conflicting_fingerprint,
        }
        stored = self._emit("DISPUTE_OPENED", "dispute", dispute_id, payload, event_id)
        raise DisputeRaised(dispute_id, stored)

    # -- 审计 -------------------------------------------------------------

    def audit_translation(
        self, *, text: str | None = None, version_id: str | None = None, segment_id: str | None = None
    ) -> list[dict[str, Any]]:
        """从一句译文（或版本+段落）还原原文、批准链、适用场次与历次更正。"""
        needle = canonical_text(text) if text is not None else None
        findings: list[dict[str, Any]] = []
        if needle is None and segment_id is None:
            raise StateError("审计至少需要一句译文文本或段落编号作为线索")
        for vid, version in self.state.versions.items():
            if version_id is not None and vid != version_id:
                continue
            for entry in version.entries:
                if segment_id is not None and entry["segment_id"] != segment_id:
                    continue
                if needle is not None and canonical_text(entry.get("text", "")) != needle:
                    continue
                script = self.state.scripts.get(version.script_id)
                segment = script.segments.get(entry["segment_id"]) if script else None
                findings.append(
                    {
                        "version_id": vid,
                        "target_language": version.target_language,
                        "segment_id": entry["segment_id"],
                        "translated_text": entry["text"],
                        "source": None
                        if segment is None
                        else {
                            "script_id": version.script_id,
                            "segment_id": segment.segment_id,
                            "kind": segment.kind,
                            "subject_ref": segment.subject_ref,
                            "source_text": segment.text,
                            "source_fingerprint": version.source_fingerprint,
                        },
                        "approval_chain": self._approval_chain(version),
                        "runs": self.runs_using_version(vid),
                        "errata": self._errata_history(vid),
                    }
                )
        return findings

    def _chain_members(self, version_id: str) -> list[str]:
        """勘误链上由旧到新的全部版本号。"""
        root = version_id
        guard = set()
        while self.state.versions[root].supersedes is not None and root not in guard:
            guard.add(root)
            root = self.state.versions[root].supersedes  # type: ignore[assignment]
        members: list[str] = []
        current: str | None = root
        guard = set()
        while current is not None and current not in guard:
            guard.add(current)
            members.append(current)
            current = self.state.versions[current].superseded_by
        return members

    def runs_using_version(self, version_id: str) -> list[dict[str, Any]]:
        """该译文（含勘误链身份）适用的场次，以及在场次各时点的实际效力。"""
        result: list[dict[str, Any]] = []
        now = self.clock.now()
        for run in self.state.runs.values():
            for language, selected in run.selections.items():
                if version_id not in self._chain_members(selected):
                    continue
                head_at_start = self._chain_head_at(selected, run.starts_at)
                head_now = self._chain_head_at(selected, None)
                started = now >= parse_datetime(run.starts_at)
                if started:
                    applicability = "performed" if version_id == head_at_start else "superseded_before_start"
                elif version_id == head_now:
                    applicability = "scheduled_current"
                else:
                    applicability = "superseded"
                result.append(
                    {
                        "run_id": run.run_id,
                        "language": language,
                        "starts_at": run.starts_at,
                        "locked": run.locked,
                        "started": started,
                        "selected_version": selected,
                        "effective_at_start": head_at_start,
                        "effective_now": head_now,
                        "applicability": applicability,
                        "receipt_ids": [
                            rid
                            for rid, receipt in run.receipts.items()
                            if receipt.get("language") == language
                        ],
                    }
                )
        return result

    def _approval_chain(self, version: ReleaseVersion) -> dict[str, Any]:
        return {
            "proposer": {"actor_id": version.proposer_id, "at": version.proposed_at},
            "signatures": [
                {"role": role, "actor_id": sig["actor_id"], "at": sig["at"]}
                for role, sig in sorted(version.signatures.items())
            ],
            "rehearsal_confirmations": [
                {"subject_ref": subject, **record}
                for subject, record in sorted(version.confirmations.items())
            ],
            "approved": version.approved,
            "approved_at": version.approved_at,
            "final_approver_id": version.final_approver_id,
            "separation_of_duty": version.final_approver_id != version.proposer_id
            if version.approved
            else None,
        }

    def _errata_history(self, version_id: str) -> dict[str, Any]:
        # 向前找到被勘误链的根，再依次列到最新。
        root = version_id
        preceding: list[str] = []
        while self.state.versions[root].supersedes is not None:
            root = self.state.versions[root].supersedes  # type: ignore[assignment]
            if root in preceding:
                break
            preceding.append(root)
        chain = []
        current: str | None = root
        while current is not None:
            node = self.state.versions[current]
            chain.append(
                {
                    "version_id": current,
                    "reason": node.errata_reason,
                    "approved_at": node.approved_at,
                }
            )
            current = node.superseded_by
        return {"root_version_id": root, "chain_oldest_first": chain}
