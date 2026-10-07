"""HTTP API（标准库实现，无第三方依赖）。

写接口要求 JSON 体携带操作者 ``actor_id`` 与幂等 ``event_id``；读接口按观众语言
返回当晚有效内容并隐藏未授权口述。错误码映射：

- 404 引用不存在
- 403 无权操作（职责分离、亲历者范围）
- 409 冲突（重复编号内容不同、争议）
- 422 状态不允许或越过截稿/开演/撤回期限
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .clock import parse_datetime
from .errors import (
    AuthorizationError,
    ConflictError,
    DeadlineError,
    NotFoundError,
    StateError,
    TheatreError,
)
from .service import DisputeRaised, TheatreService


def build_server(service: TheatreService, *, clock_settable: bool, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    handler = _make_handler(service, clock_settable)
    server = ThreadingHTTPServer((host, port), handler)
    return server


def _make_handler(service: TheatreService, clock_settable: bool) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "TheatreRelease/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # 安静：测试不输出噪声
            return

        # -- 基础收发 -----------------------------------------------------

        def _send_json(self, status: int, body: Any) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise StateError("请求体必须是 UTF-8 JSON 对象")
            if not isinstance(body, dict):
                raise StateError("请求体必须是 JSON 对象")
            return body

        def _field(self, body: dict[str, Any], name: str) -> Any:
            if name not in body:
                raise StateError(f"缺少字段: {name}")
            return body[name]

        def _guard(self, fn: Callable[[], Any], success: int = 200) -> None:
            try:
                result = fn()
            except DisputeRaised as exc:
                self._send_json(409, {"error": "dispute", "message": str(exc), "dispute_id": exc.dispute_id})
            except NotFoundError as exc:
                self._send_json(404, {"error": "not_found", "message": str(exc)})
            except AuthorizationError as exc:
                self._send_json(403, {"error": "forbidden", "message": str(exc)})
            except (DeadlineError, StateError) as exc:
                code = "deadline_passed" if isinstance(exc, DeadlineError) else "invalid_state"
                self._send_json(422, {"error": code, "message": str(exc)})
            except ConflictError as exc:
                self._send_json(409, {"error": "conflict", "message": str(exc)})
            except TheatreError as exc:
                self._send_json(400, {"error": "bad_request", "message": str(exc)})
            else:
                self._send_json(success, result if result is not None else {"ok": True})

        # -- 路由 ---------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            self._guard(lambda: self._route_get(path, query))

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            self._guard(lambda: self._route_post(path))

        # -- 读 -----------------------------------------------------------

        def _route_get(self, path: str, query: dict[str, str]) -> Any:
            if path == "/health":
                return {"ok": True, "now": service.clock.now().isoformat()}

            match = re.fullmatch(r"/runs/([^/]+)/tonight", path)
            if match:
                language = query.get("language")
                if not language:
                    raise StateError("查询参数 language 必填")
                return service.effective_content(match.group(1), language)

            match = re.fullmatch(r"/runs/([^/]+)", path)
            if match:
                run = service.state.runs.get(match.group(1))
                if run is None:
                    raise NotFoundError(f"场次不存在: {match.group(1)}")
                return _run_view(run)

            match = re.fullmatch(r"/versions/([^/]+)", path)
            if match:
                version = service.state.versions.get(match.group(1))
                if version is None:
                    raise NotFoundError(f"译文版本不存在: {match.group(1)}")
                return _version_view(version)

            if path == "/audit":
                return {
                    "findings": service.audit_translation(
                        text=query.get("text"),
                        version_id=query.get("version_id"),
                        segment_id=query.get("segment_id"),
                    )
                }

            if path == "/disputes":
                return {"disputes": [_dispute_view(d) for d in service.state.disputes.values()]}

            raise NotFoundError(f"未知路径: {path}")

        # -- 写 -----------------------------------------------------------

        def _route_post(self, path: str) -> Any:
            body = self._read_json()

            if path == "/admin/clock":
                if not clock_settable:
                    raise AuthorizationError("该实例使用系统时钟，不支持拨钟")
                service.clock.set(parse_datetime(self._field(body, "now")))
                return {"now": service.clock.now().isoformat()}

            if path == "/scripts":
                return service.register_script(
                    script_id=self._field(body, "script_id"),
                    title=self._field(body, "title"),
                    language=self._field(body, "language"),
                    segments=self._field(body, "segments"),
                    event_id=self._field(body, "event_id"),
                )

            if path == "/consents":
                return service.record_consent(
                    consent_id=self._field(body, "consent_id"),
                    subject_ref=self._field(body, "subject_ref"),
                    scope=self._field(body, "scope"),
                    confirmer_id=self._field(body, "actor_id"),
                    event_id=self._field(body, "event_id"),
                )

            match = re.fullmatch(r"/consents/([^/]+)/withdraw", path)
            if match:
                return service.withdraw_consent(
                    consent_id=match.group(1),
                    actor_id=self._field(body, "actor_id"),
                    event_id=self._field(body, "event_id"),
                )

            if path == "/versions":
                return service.propose_version(
                    version_id=self._field(body, "version_id"),
                    script_id=self._field(body, "script_id"),
                    target_language=self._field(body, "target_language"),
                    proposer_id=self._field(body, "actor_id"),
                    entries=self._field(body, "entries"),
                    notes=body.get("notes", []),
                    event_id=self._field(body, "event_id"),
                )

            match = re.fullmatch(r"/versions/([^/]+)/sign", path)
            if match:
                return service.sign_version(
                    version_id=match.group(1),
                    role=self._field(body, "role"),
                    actor_id=self._field(body, "actor_id"),
                    event_id=self._field(body, "event_id"),
                )

            match = re.fullmatch(r"/versions/([^/]+)/confirm-rehearsal", path)
            if match:
                return service.confirm_rehearsal(
                    version_id=match.group(1),
                    subject_ref=self._field(body, "subject_ref"),
                    confirmer_id=self._field(body, "actor_id"),
                    segment_ids=self._field(body, "segment_ids"),
                    event_id=self._field(body, "event_id"),
                )

            match = re.fullmatch(r"/versions/([^/]+)/approve", path)
            if match:
                return service.approve_version(
                    version_id=match.group(1),
                    final_approver_id=self._field(body, "actor_id"),
                    event_id=self._field(body, "event_id"),
                )

            if path == "/errata":
                return service.issue_errata(
                    old_version_id=self._field(body, "old_version_id"),
                    replacement_version_id=self._field(body, "replacement_version_id"),
                    reason=self._field(body, "reason"),
                    actor_id=self._field(body, "actor_id"),
                    event_id=self._field(body, "event_id"),
                )

            if path == "/runs":
                return service.plan_run(
                    run_id=self._field(body, "run_id"),
                    script_id=self._field(body, "script_id"),
                    starts_at=self._field(body, "starts_at"),
                    lock_deadline=self._field(body, "lock_deadline"),
                    audience_languages=self._field(body, "audience_languages"),
                    event_id=self._field(body, "event_id"),
                )

            match = re.fullmatch(r"/runs/([^/]+)/lock", path)
            if match:
                return service.lock_run(
                    run_id=match.group(1),
                    selections=self._field(body, "selections"),
                    actor_id=self._field(body, "actor_id"),
                    event_id=self._field(body, "event_id"),
                )

            match = re.fullmatch(r"/runs/([^/]+)/adjust", path)
            if match:
                return service.adjust_run(
                    run_id=match.group(1),
                    changes=self._field(body, "changes"),
                    actor_id=self._field(body, "actor_id"),
                    event_id=self._field(body, "event_id"),
                    reason=body.get("reason", ""),
                )

            match = re.fullmatch(r"/runs/([^/]+)/receipts", path)
            if match:
                result = service.record_receipt(
                    run_id=match.group(1),
                    receipt_id=self._field(body, "receipt_id"),
                    language=self._field(body, "language"),
                    observed_fingerprint=self._field(body, "content_fingerprint"),
                    actor_id=self._field(body, "actor_id"),
                    event_id=self._field(body, "event_id"),
                )
                return result

            raise NotFoundError(f"未知路径: {path}")

    return Handler


def _run_view(run: Any) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(run)


def _version_view(version: Any) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(version)


def _dispute_view(dispute: Any) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(dispute)
