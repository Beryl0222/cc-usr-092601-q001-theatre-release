"""命令行入口。

子命令：

- ``validate``：校验单个领域事件是否符合契约（原有功能）。
- ``serve``：以指定事件日志启动 HTTP 服务；``--clock settable`` 时开放拨钟，便于推演。
- ``audit``：从一句译文（或版本+段落）还原原文、批准链、适用场次和历次更正。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .clock import LOCAL_TZ, SettableClock, SystemClock, parse_datetime
from .contracts import validate_event
from .service import TheatreService
from .store import EventStore


def _load_service(args: argparse.Namespace) -> TheatreService:
    store = EventStore(args.store)
    if args.clock == "settable":
        clock = SettableClock(parse_datetime(args.now) if args.now else SystemClock(LOCAL_TZ).now())
    else:
        clock = SystemClock(LOCAL_TZ)
    return TheatreService(store, clock)


def cmd_validate(args: argparse.Namespace) -> int:
    schema = json.loads(Path(args.schema).read_text(encoding="utf-8"))
    event = json.loads(Path(args.event).read_text(encoding="utf-8"))
    issues = validate_event(event, schema)
    if not issues:
        print("valid")
        return 0
    for issue in issues:
        print(f"{issue.field}	{issue.code}	{issue.message}")
    return 1


def cmd_audit(args: argparse.Namespace) -> int:
    service = _load_service(args)
    findings = service.audit_translation(text=args.text, version_id=args.version_id, segment_id=args.segment_id)
    json.dump({"findings": findings}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if findings else 3


def cmd_serve(args: argparse.Namespace) -> int:
    from .httpapi import build_server

    service = _load_service(args)
    server = build_server(service, clock_settable=args.clock == "settable", host=args.host, port=args.port)
    actual_port = server.server_address[1]
    print(f"serving on http://{args.host}:{actual_port} (store={args.store}, clock={args.clock})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="theatre_release", description="跨语种戏剧演出版本台")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="校验领域事件")
    p_validate.add_argument("schema")
    p_validate.add_argument("event")
    p_validate.set_defaults(func=cmd_validate)

    def add_store(p: argparse.ArgumentParser) -> None:
        p.add_argument("--store", required=True, help="事件日志 JSONL 路径")
        p.add_argument("--clock", choices=("system", "settable"), default="system")
        p.add_argument("--now", help="settable 时钟的初始时间（带时区 ISO 8601）")

    p_audit = sub.add_parser("audit", help="审计一句译文的完整来历")
    add_store(p_audit)
    p_audit.add_argument("--text", help="译文原句")
    p_audit.add_argument("--version-id", dest="version_id")
    p_audit.add_argument("--segment-id", dest="segment_id")
    p_audit.set_defaults(func=cmd_audit)

    p_serve = sub.add_parser("serve", help="启动 HTTP 服务")
    add_store(p_serve)
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8080)
    p_serve.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
