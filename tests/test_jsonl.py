"""Tests for append-only JSONL storage and multiprocessing concurrency."""

from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

import flab.jsonl as jsonl_mod
from flab.jsonl import IncompleteLineWarning, JsonlParseError, append_record, read_records


def _worker_append(path_str: str, worker_id: int, count: int) -> None:
    payload = "x" * 512
    for i in range(count):
        append_record(
            path_str,
            {"worker": worker_id, "seq": i, "payload": payload},
        )


def test_append_record_preserves_history_and_has_no_delete_or_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "records.jsonl"
    append_record(target, {"seq": 1, "msg": "first"})
    append_record(target, {"seq": 2, "msg": "second"})
    append_record(target, {"seq": 3, "msg": "third"})

    records = read_records(target, strict=True)
    assert records == [
        {"seq": 1, "msg": "first"},
        {"seq": 2, "msg": "second"},
        {"seq": 3, "msg": "third"},
    ]

    # Verify module exposes no overwrite/delete/truncate functions
    public_names = [name for name in dir(jsonl_mod) if not name.startswith("_")]
    for forbidden in ("overwrite", "delete", "remove", "truncate", "clear"):
        assert not any(forbidden in name.lower() for name in public_names)


def test_concurrent_processes_append_complete_lines(tmp_path: Path) -> None:
    target = tmp_path / "concurrent.jsonl"
    per_proc = 40
    ctx = multiprocessing.get_context("spawn")
    p1 = ctx.Process(target=_worker_append, args=(str(target), 1, per_proc))
    p2 = ctx.Process(target=_worker_append, args=(str(target), 2, per_proc))

    p1.start()
    p2.start()
    p1.join(timeout=15)
    p2.join(timeout=15)

    assert p1.exitcode == 0
    assert p2.exitcode == 0

    records = read_records(target, strict=True)
    assert len(records) == per_proc * 2
    w1_seqs = sorted(r["seq"] for r in records if r["worker"] == 1)
    w2_seqs = sorted(r["seq"] for r in records if r["worker"] == 2)
    assert w1_seqs == list(range(per_proc))
    assert w2_seqs == list(range(per_proc))


def test_incomplete_last_line_strict_vs_non_strict(tmp_path: Path) -> None:
    target = tmp_path / "interrupted.jsonl"
    append_record(target, {"id": 1})
    append_record(target, {"id": 2})

    # Simulate an interrupted write at the end of the file
    with open(target, "ab") as f:
        f.write(b'{"id": 3, "incomplete": ')

    with pytest.raises(JsonlParseError) as exc_info:
        read_records(target, strict=True)
    assert exc_info.value.lineno == 3
    assert "3" in str(exc_info.value)

    reported: list[tuple[int, str]] = []
    with pytest.warns(IncompleteLineWarning, match="line 3"):
        records = read_records(
            target,
            strict=False,
            on_incomplete=lambda lineno, line: reported.append((lineno, line)),
        )

    assert records == [{"id": 1}, {"id": 2}]
    assert records.incomplete_lineno == 3
    assert records.incomplete_line == '{"id": 3, "incomplete": '
    assert reported == [(3, '{"id": 3, "incomplete": ')]


def test_corrupted_middle_line_raises_even_when_non_strict(tmp_path: Path) -> None:
    target = tmp_path / "corrupt_mid.jsonl"
    target.write_text('{"id": 1}\n{bad_json_line_2\n{"id": 3}\n', encoding="utf-8")

    with pytest.raises(JsonlParseError) as exc_info:
        read_records(target, strict=False)
    assert exc_info.value.lineno == 2
    assert "2" in str(exc_info.value)
