from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class ContextAuthorization(ABC):
    @abstractmethod
    def can_view_channel(self, actor_id: str, channel_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def can_view_thread(self, actor_id: str, channel_id: str, thread_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def can_view_attachment(self, actor_id: str, attachment_id: str) -> bool:
        raise NotImplementedError


class AllowAllAuthorization(ContextAuthorization):
    def can_view_channel(self, actor_id: str, channel_id: str) -> bool:
        return True

    def can_view_thread(self, actor_id: str, channel_id: str, thread_id: str) -> bool:
        return True

    def can_view_attachment(self, actor_id: str, attachment_id: str) -> bool:
        return True


class StaticFixtureAuthorization(ContextAuthorization):
    def __init__(self, grants: dict[str, Any]) -> None:
        self.grants = grants

    @classmethod
    def from_path(cls, path: Path) -> StaticFixtureAuthorization:
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def _actor(self, actor_id: str) -> dict[str, Any]:
        return self.grants.get("actors", {}).get(actor_id, {})

    def can_view_channel(self, actor_id: str, channel_id: str) -> bool:
        return channel_id in self._actor(actor_id).get("channels", [])

    def can_view_thread(self, actor_id: str, channel_id: str, thread_id: str) -> bool:
        actor = self._actor(actor_id)
        return self.can_view_channel(actor_id, channel_id) and thread_id in actor.get(
            "threads", []
        )

    def can_view_attachment(self, actor_id: str, attachment_id: str) -> bool:
        return attachment_id in self._actor(actor_id).get("attachments", [])
