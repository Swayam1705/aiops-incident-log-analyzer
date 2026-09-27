#!/usr/bin/env python3
"""Deterministic failure injection.

Default behaviour talks to a *running* instance of the service and activates a
simulated fault through its admin API::

    python scripts/inject_errors.py database-timeout
    python scripts/inject_errors.py http-500-burst --duration 30
    python scripts/inject_errors.py clear
    python scripts/inject_errors.py list

With ``--offline`` it instead writes a deterministic scenario log file that the
analyzer can read without a running server (useful in CI / air-gapped demos)::

    python scripts/inject_errors.py memory-pressure --offline --out logs/app.log

All scenarios are simulations. Nothing destructive is executed.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Allow running as a plain script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.faults import FAULT_SPECS, SCENARIO_NAMES, normalize_scenario  # noqa: E402
from app.log_generator import scenario_log_lines  # noqa: E402


def _post(url: str, payload: dict | None, token: str | None) -> dict:
    data = json.dumps(payload or {}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Admin-Token"] = token
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _get(url: str, token: str | None) -> dict:
    headers = {"X-Admin-Token": token} if token else {}
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inject a deterministic simulated failure.")
    parser.add_argument(
        "scenario", nargs="?", help="one of: " + ", ".join(SCENARIO_NAMES) + " | clear | list"
    )
    parser.add_argument("--duration", type=float, default=None, help="fault duration in seconds")
    parser.add_argument("--base-url", default="http://localhost:8000", help="running service base URL")
    parser.add_argument("--token", default=None, help="admin token (if ADMIN_TOKEN is set)")
    parser.add_argument(
        "--offline", action="store_true", help="write a scenario log file instead of calling the API"
    )
    parser.add_argument("--out", default="logs/app.log", help="offline output log file")
    parser.add_argument("--lines", type=int, default=160, help="offline: number of log lines to generate")
    args = parser.parse_args(argv)

    if not args.scenario or args.scenario == "list":
        print("Known failure scenarios (ground truth):")
        for name in SCENARIO_NAMES:
            spec = FAULT_SPECS[name]
            print(f"  {name:<22} -> {spec.ground_truth}")
        return 0

    if args.scenario == "clear":
        if args.offline:
            print("offline mode has no active fault to clear")
            return 0
        try:
            result = _post(f"{args.base_url.rstrip('/')}/admin/faults/clear", {}, args.token)
            print(json.dumps(result, indent=2))
            return 0
        except (urllib.error.URLError, OSError) as exc:
            print(f"could not reach service at {args.base_url}: {exc}", file=sys.stderr)
            return 2

    try:
        scenario = normalize_scenario(args.scenario)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.offline:
        lines = scenario_log_lines(scenario, total=args.lines)
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        print(f"wrote {len(lines)} deterministic log lines for '{scenario}' -> {out_path}")
        print(f"ground truth: {FAULT_SPECS[scenario].ground_truth}")
        print("now run: python scripts/run_analyzer.py")
        return 0

    payload: dict[str, object] = {"scenario": scenario}
    if args.duration is not None:
        payload["duration_seconds"] = args.duration
    try:
        result = _post(f"{args.base_url.rstrip('/')}/admin/faults", payload, args.token)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"injection failed (HTTP {exc.code}): {detail}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError) as exc:
        print(
            f"could not reach service at {args.base_url}: {exc}\n"
            "Start it first: uvicorn app.main:app --host 0.0.0.0 --port 8000\n"
            "Or use --offline to generate a log file for the analyzer.",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, indent=2))
    print(f"\nground truth: {result.get('ground_truth')}")
    print("next: python scripts/run_analyzer.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
