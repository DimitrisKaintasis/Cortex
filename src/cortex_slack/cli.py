from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from cortex import CortexClient
from cortex_slack.connector import SlackConnector
from cortex_slack.mapper import SlackConnectorConfig, SlackMapper
from cortex_slack.models import event_pages_from_mapping


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cortex-slack",
        description="Sync source-native Slack event pages into local Cortex.",
    )
    parser.add_argument("event_pages", type=Path)
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument(
        "--channel-project",
        action="append",
        required=True,
        metavar="CHANNEL=PROJECT",
        help="allowlist one Slack channel into one Cortex project scope",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


def channel_projects(values: Sequence[str]) -> dict[str, str]:
    projects: dict[str, str] = {}
    for value in values:
        channel_id, separator, project_id = value.partition("=")
        if not separator or not channel_id.strip() or not project_id.strip():
            raise ValueError("--channel-project must use non-empty CHANNEL=PROJECT")
        if channel_id in projects and projects[channel_id] != project_id:
            raise ValueError(f"Slack channel has conflicting project mappings: {channel_id}")
        projects[channel_id] = project_id
    return projects


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    pages = event_pages_from_mapping(
        json.loads(args.event_pages.read_text(encoding="utf-8"))
    )
    config = SlackConnectorConfig(
        organization_id=args.organization_id,
        workspace_id=args.workspace_id,
        channel_projects=channel_projects(args.channel_project),
        batch_size=args.batch_size,
    )
    with CortexClient(base_url=args.base_url, timeout=args.timeout) as client:
        committed = SlackConnector(client, SlackMapper(config)).sync_pages(pages)
    print(
        json.dumps(
            [
                {
                    "request_id": item.request_id,
                    "run_request_id": item.run_request_id,
                    "committed_cursor": item.committed_cursor,
                    "committed_at": item.committed_at.isoformat(),
                }
                for item in committed
            ],
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
