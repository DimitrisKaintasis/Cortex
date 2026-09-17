"""Prove that the default install can ingest and retrieve with SQLite only."""

from __future__ import annotations

import io
import json
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from data_retrieval.cli import main


def run_cli(*arguments: str) -> dict[str, object]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(arguments)
    if exit_code != 0:
        raise RuntimeError(stderr.getvalue() or stdout.getvalue())
    return json.loads(stdout.getvalue())


def main_smoke() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        workspace = Path(temporary_directory)
        source = workspace / "notes.txt"
        database = workspace / "cortex.sqlite3"
        source.write_text(
            "Cortex stores canonical evidence in SQLite.",
            encoding="utf-8",
        )

        ingested = run_cli(
            "ingest",
            str(source),
            "--db",
            str(database),
            "--namespace",
            "smoke",
            "--source",
            "minimal-install-smoke",
            "--tag",
            "storage",
        )
        if ingested["atom_count"] != 1:
            raise AssertionError(f"expected one atom, received {ingested['atom_count']!r}")

        retrieved = run_cli(
            "retrieve",
            "Where is canonical evidence stored?",
            "--db",
            str(database),
            "--namespace",
            "smoke",
            "--tag",
            "storage",
            "--top-k",
            "1",
        )
        items = retrieved["items"]
        if not isinstance(items, list) or len(items) != 1:
            raise AssertionError(f"expected one retrieval item, received {items!r}")
        metadata = items[0]["metadata"]
        if metadata["source_type"] != "text_file":
            raise AssertionError(f"source metadata was not preserved: {metadata!r}")

    print("minimal SQLite install smoke test passed")


if __name__ == "__main__":
    main_smoke()
