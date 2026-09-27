"""Collector tests: bounded windows, tail reads, multi-file merge, missing files."""

from __future__ import annotations

from pathlib import Path

from app.analyzer.collector import collect_from_strings, collect_lines


def _write(path: Path, n: int, prefix: str = "line"):
    path.write_text("\n".join(f"{prefix}-{i}" for i in range(n)) + "\n", encoding="utf-8")
    return path


def test_window_bounds_line_count(tmp_path: Path):
    f = _write(tmp_path / "a.log", 100)
    result = collect_lines([f], window_size=10)
    assert len(result.lines) == 10
    assert result.truncated is True
    # newest lines retained
    assert result.lines[-1] == "line-99"


def test_reads_only_tail_bytes(tmp_path: Path):
    f = _write(tmp_path / "big.log", 5000)
    result = collect_lines([f], window_size=50, max_bytes_per_file=4096)
    assert len(result.lines) <= 50
    assert result.bytes_read <= 4096


def test_merges_multiple_files(tmp_path: Path):
    a = _write(tmp_path / "a.log", 5, "a")
    b = _write(tmp_path / "b.log", 5, "b")
    result = collect_lines([a, b], window_size=100)
    assert len(result.lines) == 10
    assert set(result.files_read) == {str(a), str(b)}


def test_missing_file_is_not_fatal(tmp_path: Path):
    result = collect_lines([tmp_path / "nope.log"], window_size=10)
    assert result.lines == []
    assert result.errors


def test_empty_window_returns_nothing(tmp_path: Path):
    f = _write(tmp_path / "a.log", 10)
    assert collect_lines([f], window_size=0).lines == []


def test_collect_from_strings_bounds():
    result = collect_from_strings([f"l{i}" for i in range(100)], window_size=20)
    assert len(result.lines) == 20
    assert result.truncated is True


def test_skips_blank_lines(tmp_path: Path):
    f = tmp_path / "a.log"
    f.write_text("a\n\n\nb\n", encoding="utf-8")
    result = collect_lines([f], window_size=10)
    assert result.lines == ["a", "b"]
