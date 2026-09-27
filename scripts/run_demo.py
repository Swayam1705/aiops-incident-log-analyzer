#!/usr/bin/env python3
"""Self-contained offline demo of the whole pipeline (no server, no cluster).

Walks the full flow deterministically:

    normal traffic  -> analyze -> no incident
    each of the 5 failures -> analyze -> incident + suspected cause
    evaluation      -> accuracy vs ground truth

Reports are written to the configured incidents directory. This is the fastest way
to see the analyzer work end to end and is safe to run in CI.

    python scripts/run_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analyzer.evaluation import run_evaluation  # noqa: E402
from app.faults import EVALUATION_SCENARIOS, FAULT_SPECS  # noqa: E402
from app.log_generator import normal_log_lines, scenario_log_lines  # noqa: E402
from app.runtime import build_runtime  # noqa: E402


def _rule(title: str) -> None:
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def main(argv: list[str] | None = None) -> int:
    runtime = build_runtime()
    analyzer = runtime.analyzer
    window = runtime.settings.log_window_size

    _rule("STEP 1 - Normal traffic (expect: no critical incident)")
    normal = normal_log_lines(total=window)
    outcome = analyzer.analyze_lines(normal, trigger="demo")
    print(f"error lines: {outcome.correlation.error_lines}, incident created: {outcome.incident_created}")

    for scenario in EVALUATION_SCENARIOS:
        spec = FAULT_SPECS[scenario]
        _rule(f"STEP - Inject '{scenario}' (ground truth: {spec.ground_truth})")
        lines = scenario_log_lines(scenario, total=window)
        outcome = analyzer.analyze_lines(lines, ground_truth=spec.ground_truth, trigger="demo")
        detected = outcome.correlation.suspected_root_cause
        if detected == spec.ground_truth:
            verdict = "MATCH"
        elif detected is None:
            verdict = "UNCERTAIN"
        else:
            verdict = "MISMATCH"
        print(f"detected cause : {detected or 'unknown'}")
        print(f"severity       : {outcome.severity.severity}")
        corr = outcome.correlation
        print(f"error lines    : {corr.error_lines} (noise ignored: {corr.noise_lines})")
        print(f"LLM status     : {outcome.llm.status}")
        if outcome.incident:
            print(f"incident       : {outcome.incident.incident_id}")
            print(f"report         : {', '.join(outcome.stored_paths)}")
        print(f"verdict        : {verdict}")

    _rule("STEP - Evaluation vs ground truth")
    llm_available = None
    if runtime.settings.llm_requested:
        llm_available, _ = runtime.llm.available()
    report = run_evaluation(analyzer, window_size=window, llm_available=llm_available)
    print(report.to_ascii_table())

    _rule("Demo complete")
    print(f"Incident reports stored in: {runtime.store.directory}")
    print("View one:  cat <path>.md")
    print("Browse UI: uvicorn app.main:app --port 8000  ->  http://localhost:8000/ui")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
