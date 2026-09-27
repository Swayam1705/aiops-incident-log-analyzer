# Testing

Tests use `pytest` and run with **no cluster, no Docker, and no Ollama**. The LLM
boundary is exercised against a real local HTTP server (stdlib `http.server`) so the
actual client request/parse/validate path runs - the client itself is never mocked.

## Run

```bash
pip install -r requirements.txt
pip install pytest ruff PyYAML httpx
ruff check .          # lint
pytest -q             # unit + integration
```

Current status: **115 passed** (2 Helm tests auto-skip when the `helm` binary is absent
and pass when it is present).

## Coverage by file

| Test file | What it verifies |
| --- | --- |
| `test_parser.py` | valid logs, malformed lines, noise, severity normalization, pattern extraction, bounded evidence |
| `test_correlate.py` | all five failure patterns, burst detection, noise separation, below-threshold -> unknown |
| `test_severity.py` | deterministic severity, volume/burst escalation, info when no incident, auditable factors |
| `test_reports.py` | JSON+Markdown generation, required fields, persistence, validation roundtrip, retention pruning, no secrets |
| `test_llm_output_validation.py` | malformed JSON, missing/invalid fields, out-of-range/boolean confidence, extra fields, bounded evidence, timeout, unavailable, model-not-found, disabled, unknown/insufficient-evidence |
| `test_incident_routes.py` | health/live/ready, metrics movement, full inject->analyze->validate flow, invalid scenario/validation rejection, 404s, evaluation endpoint, logs, runbooks, security headers, config hides secrets, UI pages, probe behaviour under faults |
| `test_metrics.py` | counter/gauge/histogram behaviour, Prometheus text format, no fake constant series |
| `test_notify.py` | unconfigured skip, successful delivery, failed/unreachable delivery without raising, no secret leakage, URL token redaction |
| `test_evaluation.py` | 5/5 rule-based, true-negative control, honest LLM-unavailable reporting, evidence correctness, Markdown/ASCII render |
| `test_config.py` | defaults, invalid-int fallback, bool parsing, empty model disables LLM, public dict hides secrets, log-file resolution |
| `test_collector.py` | bounded windows, tail-only byte reads, multi-file merge, missing files non-fatal, blank-line skipping |
| `test_k8s_helm.py` | manifests parse, deployment has probes/resources/non-root/2 replicas, ClusterIP service, placeholder-only secret, Helm lint + template render (skips without helm) |

## Required-scenario mapping

- **Normal traffic -> no critical incident**: `test_correlate.py`, `test_evaluation.py`,
  `test_incident_routes.py::test_analyze_normal_traffic_creates_no_incident`.
- **HTTP 500 burst -> incident created**: `test_incident_routes.py::test_full_incident_flow`.
- **Dependency timeout -> correct evidence lines**: `test_correlate.py::test_evidence_lines_are_real_log_content`.
- **Noisy logs -> irrelevant entries ignored**: `test_parser.py`, `test_correlate.py::test_unparseable_lines_counted_as_noise`.
- **Insufficient AI evidence -> unknown**: `test_llm_output_validation.py`, `test_correlate.py::test_below_threshold_reports_unknown`.
- **Unhealthy pod -> probe behaviour**: `test_incident_routes.py::test_liveness_fails_under_liveness_fault`,
  `test_readiness_fails_under_invalid_configuration`.
- **Prometheus -> incident metric changes**: `test_metrics.py`, `test_incident_routes.py::test_metrics_change_with_traffic`.

## Kubernetes / cluster tests (manual, not in core CI)

`tests/test_k8s_helm.py` validates manifests offline. Full cluster behaviour (kind deploy,
scaling, probe restarts) is verified manually via the commands in the README and recorded
in `FINAL_VERIFICATION_REPORT.md`. In this project's build sandbox, manifests and the
Helm chart were validated against a real `kube-apiserver` with server-side strict schema
validation.

## AI evaluation

`scripts/run_evaluation.py` and `POST /evaluation/run` produce the ground-truth scorecard
described in `docs/evaluation.md`.
