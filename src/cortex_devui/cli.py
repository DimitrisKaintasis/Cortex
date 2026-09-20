from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from cortex import CortexClient
from cortex_devui.connector import DevUIConnector
from cortex_devui.mapper import DevUIConnectorConfig, DevUIMapper
from cortex_devui.models import snapshot_from_mapping


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cortex-devui",
        description="Sync one source-native DevUI snapshot into local Cortex.",
    )
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--source-instance")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    snapshot = snapshot_from_mapping(
        json.loads(args.snapshot.read_text(encoding="utf-8"))
    )
    config = DevUIConnectorConfig(
        organization_id=args.organization_id,
        project_id=args.project_id,
        source_instance=args.source_instance or args.project_id,
        batch_size=args.batch_size,
    )
    with CortexClient(base_url=args.base_url, timeout=args.timeout) as client:
        committed = DevUIConnector(client, DevUIMapper(config)).sync_snapshot(snapshot)
    print(
        json.dumps(
            {
                "request_id": committed.request_id,
                "run_request_id": committed.run_request_id,
                "source_system": committed.source.source_system,
                "source_instance": committed.source.source_instance,
                "committed_cursor": committed.committed_cursor,
                "committed_at": committed.committed_at.isoformat(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
