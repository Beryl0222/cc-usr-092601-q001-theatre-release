"""面向观众的 HTTP API：按语言给出当晚有效内容，隐藏未授权口述。

路由：
- ``GET /tonight?lang=<语言>[&date=YYYY-MM-DD]``：当晚最早一场已锁定场次的有效内容；
- ``GET /runs/<run_id>/content?lang=<语言>``：指定场次的有效内容。

运行：``python -m theatre_release.api <store_dir> [--host 127.0.0.1] [--port 8000]``
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .clock import SystemClock
from .errors import ServiceError
from .service import ReleaseService
from .store import JsonlStore

_STATUS_BY_CODE = {
    "run_not_found": 404,
    "no_performance_tonight": 404,
    "run_not_locked": 409,
}


class ReleaseHandler(BaseHTTPRequestHandler):
    """无状态查询处理器；service 由 make_server 注入。"""

    service: ReleaseService

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 保持安静
        pass

    def do_GET(self) -> None:  # noqa: N802 - stdlib 约定
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        try:
            language = self._required_language(params)
            parts = [part for part in parsed.path.split("/") if part]
            if parts == ["tonight"]:
                on_date = params.get("date", [None])[0]
                body = self.service.tonight(language, on_date)
            elif len(parts) == 3 and parts[0] == "runs" and parts[2] == "content":
                body = self.service.run_content(parts[1], language)
            else:
                raise ServiceError("route_not_found", f"未知路由: {parsed.path}")
        except ServiceError as exc:
            self._send(_STATUS_BY_CODE.get(exc.code, 400), {"error": {"code": exc.code, "message": exc.message}})
        except Exception as exc:  # pragma: no cover - 兜底
            self._send(500, {"error": {"code": "internal_error", "message": str(exc)}})
        else:
            self._send(200, body)

    @staticmethod
    def _required_language(params: dict[str, list[str]]) -> str:
        values = params.get("lang")
        if not values or not values[0].strip():
            raise ServiceError("language_required", "缺少查询参数 lang")
        return values[0].strip()

    def _send(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def make_server(service: ReleaseService, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    handler = type("BoundReleaseHandler", (ReleaseHandler,), {"service": service})
    return ThreadingHTTPServer((host, port), handler)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description="跨语种演出版本服务 HTTP API")
    parser.add_argument("store_dir", help="事件日志目录")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    service = ReleaseService(JsonlStore(args.store_dir), SystemClock())
    server = make_server(service, args.host, args.port)
    print(f"listening on http://{args.host}:{args.port} (store: {args.store_dir})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
