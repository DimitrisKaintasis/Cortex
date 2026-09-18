from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import NormalizedEvent

MENTION_RE = re.compile(r"<@(U[A-Z0-9]+)(?:\|[^>]+)?>")
CODE_RE = re.compile(r"(```.*?```|`[^`\n]*`)", re.DOTALL)


def sanitized_user(member: dict[str, Any]) -> dict[str, Any]:
    profile = member.get("profile") or {}
    return {
        "id": member["id"],
        "name": member.get("name") or None,
        "display_name": profile.get("display_name") or None,
        "real_name": profile.get("real_name") or member.get("real_name") or None,
        "deleted": bool(member.get("deleted")),
        "is_bot": bool(member.get("is_bot")),
        "is_app_user": bool(member.get("is_app_user")),
        "updated": member.get("updated"),
    }


def fetch_slack_directory(
    output_path: Path, token: str | None = None
) -> dict[str, Any]:
    token = token or os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        raise ValueError("SLACK_BOT_TOKEN is required when refreshing Slack users")
    members: list[dict[str, Any]] = []
    cursor = ""
    while True:
        query = urllib.parse.urlencode(
            {"limit": 200, **({"cursor": cursor} if cursor else {})}
        )
        request = urllib.request.Request(
            f"https://slack.com/api/users.list?{query}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("ok"):
            raise RuntimeError(
                f"Slack users.list failed: {payload.get('error', 'unknown_error')}"
            )
        members.extend(sanitized_user(member) for member in payload.get("members", []))
        cursor = payload.get("response_metadata", {}).get("next_cursor", "").strip()
        if not cursor:
            break
    snapshot = {
        "format_version": 1,
        "fetched_at": datetime.now(UTC).isoformat(),
        "source": "slack_api",
        "user_count": len(members),
        "users": sorted(members, key=lambda item: item["id"]),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return snapshot


def load_slack_directory(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "format_version": 1,
            "fetched_at": None,
            "source": "unavailable",
            "user_count": 0,
            "users": [],
        }
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    for user in snapshot.get("users", []):
        if "email" in user:
            raise ValueError("Slack directory cache must not contain emails")
    return snapshot


def preferred_name(user: dict[str, Any] | None, actor_id: str | None) -> str | None:
    if not actor_id:
        return None
    if not user:
        return actor_id
    return (
        user.get("display_name")
        or user.get("real_name")
        or user.get("name")
        or actor_id
    )


def resolve_mentions(text: str, users: dict[str, dict[str, Any]]) -> str:
    pieces = CODE_RE.split(text)
    resolved: list[str] = []
    for index, piece in enumerate(pieces):
        if index % 2:
            resolved.append(piece)
            continue

        def replacement(match: re.Match[str]) -> str:
            user_id = match.group(1)
            user = users.get(user_id)
            if not user:
                return match.group(0)
            return f"@{preferred_name(user, user_id)}"

        resolved.append(MENTION_RE.sub(replacement, piece))
    return "".join(resolved)


def classify_actor(
    actor_id: str | None,
    user: dict[str, Any] | None,
    source_metadata: dict[str, Any],
) -> str:
    role = source_metadata.get("role")
    if role in {"system", "tool"} and not actor_id:
        return "system"
    if source_metadata.get("agent_id") or source_metadata.get("public_agent_id"):
        return "agent"
    if user and user.get("is_app_user"):
        return "app"
    if user and user.get("is_bot"):
        return "bot"
    if actor_id and actor_id.startswith("U"):
        return "human"
    return "unknown"


def enrich_events_with_slack_users(
    events: list[NormalizedEvent], directory_path: Path
) -> tuple[list[NormalizedEvent], dict[str, int]]:
    snapshot = load_slack_directory(directory_path)
    users = {user["id"]: user for user in snapshot.get("users", [])}
    fetched_at = snapshot.get("fetched_at")
    resolution_timestamp = (
        datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        if fetched_at
        else None
    )
    resolved_count = 0
    unresolved_ids: set[str] = set()
    enriched: list[NormalizedEvent] = []
    for event in events:
        user = users.get(event.actor_id or "")
        metadata = dict(event.source_metadata)
        actor_type = classify_actor(event.actor_id, user, metadata)
        name = preferred_name(user, event.actor_id)
        source = "cache" if user else "unresolved"
        if not user and actor_type == "agent" and event.actor_id:
            name = f"Agent {event.actor_id[:8]}"
            source = "export"
        elif not user and actor_type == "system":
            name = "System"
            source = "export"
        if user:
            resolved_count += 1
        elif source == "export":
            resolved_count += 1
        elif event.actor_id:
            unresolved_ids.add(event.actor_id)
        enriched.append(
            event.model_copy(
                update={
                    "actor_display_name": name,
                    "actor_type": actor_type,
                    "actor_resolution_source": source,
                    "actor_resolution_timestamp": resolution_timestamp,
                    "actor_is_active": None
                    if not user
                    else not bool(user.get("deleted")),
                    "display_content": resolve_mentions(event.raw_content, users),
                }
            )
        )
    return enriched, {
        "directory_users": len(users),
        "resolved_events": resolved_count,
        "unresolved_events": len(events) - resolved_count,
        "unresolved_actor_ids": len(unresolved_ids),
    }
