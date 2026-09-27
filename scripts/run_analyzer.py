#!/usr/bin/env python3
"""Run the analyzer over the latest bounded log window (offline, no server needed).

Collects the configured log files, runs the deterministic pipeline (and the advisory
LLM stage if Ollama is reachable), stores the JSON + Markdown report and prints a
summary::

    python scripts/run_analyzer.py
    python scripts/run_analyzer.py --window-size 500 --ground-truth database_timeout
    python scripts/run_analyzer.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.runtime import build_runtime  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze the latest bounded log window.")
    parser.add_argument("--window-size", type=int, default=None, help="override LOG_WINDOW_SIZE")
    parser.add_argument("--ground-truth", default=None, help="optional ground-truth label (evaluation)")
    parser.add_argument("--trigger", default="manual")
    parser.add_argument(
        "--force-incident", action="store_true", help="create an incident even below threshold"
    )
    parser.add_argument("--json", action="store_true", help="print the full outcome as JSON")
    args = parser.parse_args(argv)

    runtime = build_runtime()
    if args.window_size:
        runtime.settings.log_window_size = args.window_size

    outcome = runtime.analyzer.analyze_files(
        trigger=args.trigger,
        ground_truth=args.ground_truth,
        force_incident=args.force_incident,
    )

    if args.json:
        payload = outcome.summary_dict()
        if outcome.incident is not None:
            payload["incident"] = outcome.incident.to_dict()
        print(json.dumps(payload, indent=2, default=str))
        return 0

    corr = outcome.correlation
    print("Analyzer run summary")
    print("=" * 60)
    print(f"window size          : {outcome.window_size} lines")
    print(f"parsed / noise       : {corr.parsed_lines} / {corr.noise_lines}")
    print(f"error / warning lines: {corr.error_lines} / {corr.warning_lines}")
    print(f"burst detected       : {corr.burst_detected} (size {corr.burst_size})")
    print(f"suspected root cause : {corr.suspected_root_cause or 'unknown / insufficient evidence'}")
    print(f"rule confidence      : {corr.rule_confidence:.2f}")
    print(f"severity             : {outcome.severity.severity}")
    print(f"LLM status           : {outcome.llm.status} (available={outcome.llm.available})")
    if outcome.llm.available:
        print(f"LLM suspected cause  : {outcome.llm.suspected_root_cause}")
    print(f"incident created     : {outcome.incident_created}")
    if outcome.incident is not None:
        print(f"incident id          : {outcome.incident.incident_id}")
        print(f"stored at            : {', '.join(outcome.stored_paths)}")
        if outcome.notification:
            print(f"notification         : {outcome.notification.to_dict()}")
    if outcome.errors:
        print("non-fatal errors     :")
        for err in outcome.errors:
            print(f"  - {err}")
    if not outcome.incident_created:
        print("\nNo incident: normal traffic or insufficient correlated evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
