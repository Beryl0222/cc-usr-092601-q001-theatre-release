"""审计命令：从一句译文还原原文、批准链、适用场次和历次更正。

用法：``python -m theatre_release.audit <store_dir> <translation_id>``
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .clock import SystemClock
from .errors import ServiceError
from .service import ReleaseService
from .store import JsonlStore


def audit_translation(service: ReleaseService, translation_id: str) -> dict[str, Any]:
    world = service.world
    translation = world.translations.get(translation_id)
    if translation is None:
        raise ServiceError("translation_not_found", f"译文候选不存在: {translation_id}")
    segment = world.segments[translation.segment_id]

    versions = []
    for version in world.versions.values():
        if not any(item.translation_id == translation_id for item in version.items):
            continue
        versions.append(
            {
                "version_id": version.version_id,
                "number": version.number,
                "status": version.status,
                "submitted_by": version.submitted_by,
                "submitted_at": version.submitted_at.isoformat(),
                "approvals": [
                    {"role": sig.role, "signer": sig.signer, "at": sig.at.isoformat()}
                    for sig in version.signatures
                ],
                "approved_at": version.approved_at.isoformat() if version.approved_at else None,
                "rehearsal": (
                    {
                        "confirmer": version.rehearsal_confirmer,
                        "at": version.rehearsal_confirmed_at.isoformat(),
                    }
                    if version.rehearsal_confirmer
                    else None
                ),
            }
        )
    versions.sort(key=lambda item: item["number"])

    version_ids = {item["version_id"] for item in versions}
    runs = [
        {
            "run_id": run.run_id,
            "starts_at": run.starts_at.isoformat(),
            "status": run.status,
            "locked_version_id": run.locked_version_id,
            "locked_number": run.locked_number,
            "fingerprint": run.fingerprint,
        }
        for run in world.runs.values()
        if run.locked_version_id in version_ids
    ]
    runs.sort(key=lambda item: item["starts_at"])

    errata = [
        {
            "errata_id": item.errata_id,
            "run_id": item.run_id,
            "version_id": item.version_id,
            "supersedes": item.supersedes,
            "reason": item.reason,
            "correction": item.correction,
            "issued_at": item.issued_at.isoformat(),
        }
        for item in world.errata.values()
        if item.version_id in version_ids
    ]
    errata.sort(key=lambda item: item["issued_at"])

    return {
        "translation": {
            "translation_id": translation.translation_id,
            "language": translation.language,
            "text": translation.text,
            "fingerprint": translation.fingerprint,
            "translator": translation.translator,
            "submitted_at": translation.submitted_at.isoformat(),
        },
        "source_segment": {
            "segment_id": segment.segment_id,
            "script_id": segment.script_id,
            "order": segment.order,
            "kind": segment.kind,
            "text": segment.text,
            "involves": list(segment.involves),
            "confirmations": list(segment.confirmations),
        },
        "versions": versions,
        "runs": runs,
        "errata": errata,
    }


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description="从一句译文还原原文、批准链、适用场次和历次更正")
    parser.add_argument("store_dir", help="事件日志目录")
    parser.add_argument("translation_id", help="译文候选编号")
    args = parser.parse_args(argv)
    service = ReleaseService(JsonlStore(args.store_dir), SystemClock())
    try:
        report = audit_translation(service, args.translation_id)
    except ServiceError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
