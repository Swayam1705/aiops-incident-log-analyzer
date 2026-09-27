#!/usr/bin/env python3
"""Run the ground-truth evaluation across all five failure scenarios.

Generates a deterministic log window per scenario (plus a normal-traffic control),
runs the real pipeline, and scores the result honestly against fixed ground truth.
Optionally writes a Markdown report.

    python scripts/run_evaluation.py
    python scripts/run_evaluation.py --window-size 300 --out evaluation_report.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analyzer.evaluation import run_evaluation  # noqa: E402
from app.runtime import build_runtime  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the analyzer against ground truth.")
    parser.add_argument("--window-size", type=int, default=200)
    parser.add_argument("--out", default=None, help="write a Markdown report to this path")
    parser.add_argument("--json", action="store_true", help="print JSON instead of the ASCII table")
    args = parser.parse_args(argv)

    runtime = build_runtime()
    llm_available = None
    if runtime.llm is not None and runtime.settings.llm_requested:
        llm_available, _ = runtime.llm.available()
    report = run_evaluation(runtime.analyzer, window_size=args.window_size, llm_available=llm_available)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        print(report.to_ascii_table())

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report.to_markdown(), encoding="utf-8")
        print(f"\nMarkdown report written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
