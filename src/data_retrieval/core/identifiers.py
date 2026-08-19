from __future__ import annotations

import hashlib


def content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: str) -> str:
    framed = "".join(f"{len(part)}:{part}" for part in parts)
    return f"{prefix}_{content_hash(framed)[:32]}"
