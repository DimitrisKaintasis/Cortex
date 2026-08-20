from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import ijson

from data_retrieval.core.identifiers import content_hash, stable_id
from data_retrieval.domain.models import Atom, Document, IngestionBundle
from data_retrieval.storage.repository import Repository

LONGMEMEVAL_DATE_FORMAT = "%Y/%m/%d (%a) %H:%M"


@dataclass(frozen=True, slots=True)
class LongMemEvalTurn:
    role: str
    content: str
    has_answer: bool


@dataclass(frozen=True, slots=True)
class LongMemEvalSession:
    session_id: str
    occurrence: int
    occurred_at: datetime
    raw_date: str
    turns: tuple[LongMemEvalTurn, ...]


@dataclass(frozen=True, slots=True)
class LongMemEvalCase:
    question_id: str
    question_type: str
    question: str
    answer: str
    question_date: datetime
    raw_question_date: str
    answer_session_ids: tuple[str, ...]
    sessions: tuple[LongMemEvalSession, ...]


@dataclass(frozen=True, slots=True)
class ImportedLongMemEvalCase:
    question_id: str
    question_type: str
    namespace: str
    question: str
    answer: str
    question_date: datetime
    answer_session_ids: tuple[str, ...]
    evidence_atom_ids: tuple[str, ...]
    document_ids: tuple[str, ...]
    history_start: datetime
    history_end: datetime
    session_count: int
    atom_count: int


@dataclass(frozen=True, slots=True)
class LongMemEvalImportResult:
    dataset_id: str
    dataset_hash: str
    cases: tuple[ImportedLongMemEvalCase, ...]
    inserted_session_count: int
    reused_session_count: int
    atom_count: int

    @property
    def case_count(self) -> int:
        return len(self.cases)

    @property
    def session_count(self) -> int:
        return self.inserted_session_count + self.reused_session_count


def iter_longmemeval_cases(
    path: Path,
    *,
    timezone_name: str = "UTC",
) -> Iterator[LongMemEvalCase]:
    """Stream validated LongMemEval cases without hydrating the whole JSON array."""

    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"unknown timezone: {timezone_name}") from error

    with path.open("rb") as source:
        for index, raw_case in enumerate(ijson.items(source, "item")):
            if not isinstance(raw_case, Mapping):
                raise ValueError(f"case {index} must be a JSON object")
            yield _parse_case(raw_case, index=index, timezone=timezone)


class LongMemEvalIngestService:
    """Import LongMemEval as isolated question namespaces and session documents."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def ingest_path(
        self,
        *,
        path: Path,
        namespace_prefix: str = "longmemeval",
        dataset_id: str | None = None,
        timezone_name: str = "UTC",
        max_cases: int | None = None,
    ) -> LongMemEvalImportResult:
        if not path.is_file():
            raise ValueError(f"input file does not exist: {path}")
        namespace_prefix = namespace_prefix.strip()
        resolved_dataset_id = (dataset_id or path.stem).strip()
        if not namespace_prefix:
            raise ValueError("namespace_prefix cannot be empty")
        if not resolved_dataset_id:
            raise ValueError("dataset_id cannot be empty")
        if max_cases is not None and max_cases <= 0:
            raise ValueError("max_cases must be positive")

        dataset_hash = _file_hash(path)
        imported_cases: list[ImportedLongMemEvalCase] = []
        inserted_session_count = 0
        reused_session_count = 0
        atom_count = 0

        cases = iter_longmemeval_cases(path, timezone_name=timezone_name)
        try:
            for case_index, case in enumerate(cases):
                if max_cases is not None and case_index >= max_cases:
                    break
                imported, inserted, reused = self._ingest_case(
                    case=case,
                    namespace_prefix=namespace_prefix,
                    dataset_id=resolved_dataset_id,
                    dataset_hash=dataset_hash,
                )
                imported_cases.append(imported)
                inserted_session_count += inserted
                reused_session_count += reused
                atom_count += imported.atom_count
        except ijson.JSONError as error:
            raise ValueError(f"invalid LongMemEval JSON: {error}") from error

        return LongMemEvalImportResult(
            dataset_id=resolved_dataset_id,
            dataset_hash=dataset_hash,
            cases=tuple(imported_cases),
            inserted_session_count=inserted_session_count,
            reused_session_count=reused_session_count,
            atom_count=atom_count,
        )

    def _ingest_case(
        self,
        *,
        case: LongMemEvalCase,
        namespace_prefix: str,
        dataset_id: str,
        dataset_hash: str,
    ) -> tuple[ImportedLongMemEvalCase, int, int]:
        namespace = (
            f"{namespace_prefix}:{dataset_id}:{dataset_hash[:12]}:{case.question_id}"
        )
        evidence_atom_ids: list[str] = []
        document_ids: list[str] = []
        inserted = 0
        reused = 0
        case_atom_count = 0

        prepared_sessions = [
            (
                session,
                *_session_bundle(
                    case=case,
                    session=session,
                    session_index=session_index,
                    namespace=namespace,
                    dataset_id=dataset_id,
                    dataset_hash=dataset_hash,
                ),
            )
            for session_index, session in enumerate(case.sessions)
        ]
        existing_document_ids = {
            document.document_id
            for document in self.repository.get_documents(
                tuple(document.document_id for _, document, _ in prepared_sessions)
            )
        }

        for session, document, atoms in prepared_sessions:
            document_ids.append(document.document_id)
            case_atom_count += len(atoms)
            evidence_atom_ids.extend(
                atom.atom_id
                for atom, turn in zip(atoms, session.turns, strict=True)
                if turn.has_answer
            )
            if document.document_id in existing_document_ids:
                reused += 1
                continue
            self.repository.persist_ingestion(
                IngestionBundle(
                    document=document,
                    atoms=atoms,
                    tags=(),
                    atom_tags=(),
                )
            )
            inserted += 1

        return (
            ImportedLongMemEvalCase(
                question_id=case.question_id,
                question_type=case.question_type,
                namespace=namespace,
                question=case.question,
                answer=case.answer,
                question_date=case.question_date,
                answer_session_ids=case.answer_session_ids,
                evidence_atom_ids=tuple(evidence_atom_ids),
                document_ids=tuple(document_ids),
                history_start=min(session.occurred_at for session in case.sessions),
                history_end=max(session.occurred_at for session in case.sessions),
                session_count=len(case.sessions),
                atom_count=case_atom_count,
            ),
            inserted,
            reused,
        )


def _parse_case(
    raw: Mapping[str, Any],
    *,
    index: int,
    timezone: ZoneInfo,
) -> LongMemEvalCase:
    question_id = _required_string(raw, "question_id", context=f"case {index}")
    context = f"case {question_id}"
    question_type = _required_string(raw, "question_type", context=context)
    question = _required_string(raw, "question", context=context)
    answer = _answer_value(raw, context=context)
    raw_question_date = _required_string(raw, "question_date", context=context)
    question_date = _parse_date(raw_question_date, timezone=timezone, context=context)
    session_ids = _string_sequence(raw.get("haystack_session_ids"), "haystack_session_ids", context)
    session_dates = _string_sequence(raw.get("haystack_dates"), "haystack_dates", context)
    raw_sessions = raw.get("haystack_sessions")
    if not isinstance(raw_sessions, Sequence) or isinstance(raw_sessions, (str, bytes)):
        raise ValueError(f"{context}: haystack_sessions must be an array")
    if not (len(session_ids) == len(session_dates) == len(raw_sessions)):
        raise ValueError(f"{context}: haystack session arrays must have equal lengths")
    occurrences: dict[str, int] = {}
    sessions: list[LongMemEvalSession] = []
    for session_id, raw_date, raw_turns in zip(
        session_ids, session_dates, raw_sessions, strict=True
    ):
        occurrence = occurrences.get(session_id, 0) + 1
        occurrences[session_id] = occurrence
        sessions.append(
            _parse_session(
                session_id=session_id,
                occurrence=occurrence,
                raw_date=raw_date,
                raw_turns=raw_turns,
                timezone=timezone,
                context=context,
            )
        )
    answer_session_ids = _string_sequence(
        raw.get("answer_session_ids"), "answer_session_ids", context
    )
    unknown_evidence = set(answer_session_ids) - set(session_ids)
    if unknown_evidence:
        raise ValueError(
            f"{context}: answer_session_ids are absent from the haystack: "
            f"{sorted(unknown_evidence)}"
        )

    return LongMemEvalCase(
        question_id=question_id,
        question_type=question_type,
        question=question,
        answer=answer,
        question_date=question_date,
        raw_question_date=raw_question_date,
        answer_session_ids=answer_session_ids,
        sessions=tuple(sessions),
    )


def _parse_session(
    *,
    session_id: str,
    occurrence: int,
    raw_date: str,
    raw_turns: Any,
    timezone: ZoneInfo,
    context: str,
) -> LongMemEvalSession:
    session_context = f"{context}, session {session_id}"
    if not isinstance(raw_turns, Sequence) or isinstance(raw_turns, (str, bytes)):
        raise ValueError(f"{session_context}: session must be an array of turns")
    turns: list[LongMemEvalTurn] = []
    for turn_index, raw_turn in enumerate(raw_turns):
        if not isinstance(raw_turn, Mapping):
            raise ValueError(f"{session_context}, turn {turn_index}: turn must be an object")
        role = _required_string(raw_turn, "role", context=session_context)
        if role not in {"user", "assistant"}:
            raise ValueError(f"{session_context}, turn {turn_index}: unsupported role {role!r}")
        content = _string_value(raw_turn, "content", context=session_context)
        has_answer = raw_turn.get("has_answer", False)
        if not isinstance(has_answer, bool):
            raise ValueError(
                f"{session_context}, turn {turn_index}: has_answer must be boolean"
            )
        turns.append(LongMemEvalTurn(role=role, content=content, has_answer=has_answer))
    if not turns:
        raise ValueError(f"{session_context}: session cannot be empty")
    return LongMemEvalSession(
        session_id=session_id,
        occurrence=occurrence,
        occurred_at=_parse_date(raw_date, timezone=timezone, context=session_context),
        raw_date=raw_date,
        turns=tuple(turns),
    )


def _session_bundle(
    *,
    case: LongMemEvalCase,
    session: LongMemEvalSession,
    session_index: int,
    namespace: str,
    dataset_id: str,
    dataset_hash: str,
) -> tuple[Document, tuple[Atom, ...]]:
    canonical_session = json.dumps(
        {
            "date": session.raw_date,
            "turns": [
                {"role": turn.role, "content": turn.content} for turn in session.turns
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    session_hash = content_hash(canonical_session)
    source = f"longmemeval/{session.session_id}"
    if session.occurrence > 1:
        source = f"{source}#{session.occurrence}"
    document_id = stable_id("doc", namespace, source, session_hash)
    shared_metadata = {
        "source_type": "longmemeval_session",
        "benchmark": "longmemeval",
        "dataset_id": dataset_id,
        "dataset_hash": dataset_hash,
        "question_id": case.question_id,
        "question_type": case.question_type,
        "question_date": case.raw_question_date,
        "timeline_id": case.question_id,
        "session_id": session.session_id,
        "session_occurrence": session.occurrence,
        "session_index": session_index,
        "session_date": session.raw_date,
    }
    document = Document(
        document_id=document_id,
        namespace=namespace,
        source=source,
        content_hash=session_hash,
        metadata=shared_metadata,
    )
    atoms = tuple(
        Atom(
            atom_id=stable_id(
                "atom", document_id, str(turn_index), content_hash(turn.content)
            ),
            document_id=document_id,
            namespace=namespace,
            position=turn_index,
            char_start=0,
            char_end=len(turn.content),
            content=turn.content,
            content_hash=content_hash(turn.content),
            occurred_at=session.occurred_at,
            metadata={
                **shared_metadata,
                "source": source,
                "role": turn.role,
                "turn_index": turn_index,
            },
        )
        for turn_index, turn in enumerate(session.turns)
    )
    return document, atoms


def _parse_date(value: str, *, timezone: ZoneInfo, context: str) -> datetime:
    try:
        parsed = datetime.strptime(value, LONGMEMEVAL_DATE_FORMAT)
    except ValueError as error:
        raise ValueError(
            f"{context}: expected date format YYYY/MM/DD (Day) HH:MM, got {value!r}"
        ) from error
    return parsed.replace(tzinfo=timezone)


def _required_string(raw: Mapping[str, Any], key: str, *, context: str) -> str:
    value = _string_value(raw, key, context=context)
    if not value.strip():
        raise ValueError(f"{context}: {key} must be a non-empty string")
    return value


def _string_value(raw: Mapping[str, Any], key: str, *, context: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{context}: {key} must be a string")
    return value


def _answer_value(raw: Mapping[str, Any], *, context: str) -> str:
    value = raw.get("answer")
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"{context}: answer must be a string or integer")
    return str(value)


def _string_sequence(value: Any, name: str, context: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{context}: {name} must be an array")
    if not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{context}: {name} must contain only non-empty strings")
    return tuple(value)


def _file_hash(path: Path, *, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(block_size):
            digest.update(block)
    return digest.hexdigest()
