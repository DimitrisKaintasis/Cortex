# LongMemEval ingestion

## Representation

The adapter follows the official cleaned LongMemEval structure without flattening the
dataset archive:

- one isolated namespace per evaluation question;
- one canonical document per history session;
- one source atom per ordered user or assistant turn;
- the session timestamp on every turn atom;
- the question ID as `timeline_id` for later Temporal projection;
- roles, dataset identity, session identity, occurrence, and original date strings as
  metadata.

The expected answer, `answer_session_ids`, and turn-level `has_answer` flags are held only
in the adapter's evaluation case object. They are deliberately excluded from canonical
document and atom content and metadata so retrieval and enrichment cannot see ground truth.

LongMemEval uses parallel arrays for session IDs, dates, and contents. The adapter validates
their lengths and streams one question at a time with `ijson`, so memory use is bounded by
one evaluation case instead of the complete JSON file. Thirteen cleaned LongMemEval-S cases
repeat a session ID for different content; later occurrences receive a stable occurrence
suffix instead of being collapsed.

The namespace format is:

```text
<prefix>:<dataset-id>:<first-12-characters-of-SHA256>:<question-id>
```

Including the file hash prevents two dataset revisions from silently sharing memory.

## Import

The CLI reads the PostgreSQL DSN from the process environment. The local ignored `.env`
contains the generated development DSN, but the application intentionally does not load
secret files automatically. In PowerShell:

```powershell
$dsnLine = Get-Content .env |
  Where-Object { $_ -like 'DATA_RETRIEVAL_POSTGRES_DSN=*' } |
  Select-Object -First 1
$env:DATA_RETRIEVAL_POSTGRES_DSN = `
  $dsnLine.Substring('DATA_RETRIEVAL_POSTGRES_DSN='.Length)

python -m data_retrieval ingest-longmemeval `
  .\data\benchmarks\longmemeval\longmemeval_s_cleaned.json `
  --dataset-id cleaned-s-2025-09
```

`--max-cases` supports small smoke runs. Repeating the same command bulk-checks existing
documents once per question and inserts only missing sessions. Each new session is one short
transaction, so an interrupted import can be safely repeated.

The official dates do not carry a UTC offset. The adapter interprets them as UTC by default
to preserve ordering reproducibly. Use `--timezone` only when a benchmark variant documents
a different source timezone.

## 2026-08-20 capacity run

The official cleaned datasets were downloaded from the
[LongMemEval Hugging Face release](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned),
which labels the dataset MIT. The upstream repository also includes an
[MIT license](https://github.com/xiaowu0162/LongMemEval/blob/main/LICENSE).
Raw files remain under ignored `data/`; canonical state remains in the laptop PostgreSQL
Docker volume.

| Dataset | Raw bytes | SHA-256 | Cases | Sessions | Atoms | Initial import |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| Oracle | 15,388,478 | `821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c` | 500 | 948 | 10,960 | 8.05 s |
| S cleaned | 277,383,467 | `d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442` | 500 | 23,867 | 246,750 | 218.31 s |

After both imports the PostgreSQL database occupied 1,002,780,339 bytes. A complete
LongMemEval-S idempotence rerun reused all 23,867 documents, inserted zero, and completed in
6.55 seconds. A direct eight-word GIN full-text lookup returned its target at rank 1 in
2.89 ms while the database contained the full dataset.

These are engineering measurements on this laptop, not retrieval-quality benchmark scores.
A natural-language question with no shared lexical terms returned no raw-only results. The
next quality stage requires semantic embeddings and/or tag enrichment, followed by an
official recall evaluation. LongMemEval-M is 2,737,100,077 raw bytes and was deliberately
not downloaded yet; its embedding runtime and projected storage should be estimated first.
