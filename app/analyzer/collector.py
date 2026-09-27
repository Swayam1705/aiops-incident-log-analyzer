"""Bounded log collection.

The collector reads the most recent bytes from one or more log files and returns a
bounded window of raw lines. It never loads whole files into memory and never sends
unbounded history downstream to the LLM.

This is intentionally a plain file reader (not a required Promtail/Loki pipeline) so
the project runs anywhere with zero cost. The bounded-window contract is the same
one a Promtail-based deployment would honour.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CollectionResult:
    lines: list[str]
    files_read: list[str] = field(default_factory=list)
    bytes_read: int = 0
    truncated: bool = False
    errors: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.lines)


def _tail_bytes(path: Path, max_bytes: int) -> tuple[bytes, bool]:
    """Read at most ``max_bytes`` from the end of ``path``.

    Returns ``(data, truncated)``. Never raises: unreadable files yield empty data.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return b"", False
    try:
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
                data = handle.read(max_bytes)
                return data, True
            return handle.read(), False
    except OSError:
        return b"", False


def collect_lines(
    paths: Iterable[Path | str],
    window_size: int,
    max_bytes_per_file: int = 4 * 1024 * 1024,
) -> CollectionResult:
    """Collect the most recent ``window_size`` non-empty lines across ``paths``.

    Files are read tail-first and merged; the final list is trimmed to the newest
    ``window_size`` lines while preserving chronological order (oldest first).
    """
    result = CollectionResult(lines=[])
    if window_size <= 0:
        return result

    collected: list[tuple[str, str]] = []  # (sort_key, line)
    for raw_path in paths:
        path = Path(raw_path)
        name = str(path)
        data, truncated = _tail_bytes(path, max_bytes_per_file)
        if truncated:
            result.truncated = True
        if not data:
            if not path.exists():
                result.errors.append(f"missing: {name}")
            continue
        result.files_read.append(name)
        result.bytes_read += len(data)
        # Decode defensively; drop a possibly-split leading partial line when truncated.
        text = data.decode("utf-8", errors="replace")
        raw_lines = text.splitlines()
        if truncated and raw_lines:
            raw_lines = raw_lines[1:]
        for line in raw_lines:
            if line.strip():
                collected.append((name, line))

    # Preserve file order then line order; caller decides windowing.
    all_lines = [line for _, line in collected]
    if len(all_lines) > window_size:
        all_lines = all_lines[-window_size:]
        result.truncated = True
    result.lines = all_lines
    return result


def collect_from_glob(
    base_dir: Path | str,
    glob: str,
    window_size: int,
    max_bytes_per_file: int = 4 * 1024 * 1024,
) -> CollectionResult:
    """Expand a glob under ``base_dir`` and collect a bounded window from matches."""
    base = Path(base_dir)
    pattern = base / glob
    parent = pattern.parent
    matches: list[Path] = []
    try:
        parent.mkdir(parents=True, exist_ok=True)
        matches = sorted(p for p in parent.glob(pattern.name) if p.is_file())
    except OSError as exc:  # pragma: no cover - defensive
        result = CollectionResult(lines=[])
        result.errors.append(f"glob error: {exc}")
        return result
    return collect_lines(matches, window_size=window_size, max_bytes_per_file=max_bytes_per_file)


def collect_from_strings(lines: Iterable[str], window_size: int) -> CollectionResult:
    """Collect a bounded window from an in-memory iterable (used by tests/evaluation)."""
    materialized = [line for line in lines if line and line.strip()]
    truncated = len(materialized) > window_size
    if truncated:
        materialized = materialized[-window_size:]
    return CollectionResult(lines=materialized, files_read=["<memory>"], truncated=truncated)


def ensure_dir(path: Path | str) -> Path:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def line_count(path: Path | str) -> int:
    try:
        with open(path, "rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def file_mtime(path: Path | str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0
