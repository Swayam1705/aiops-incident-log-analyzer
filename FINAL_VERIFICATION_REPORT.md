# Final Verification Report

**Project 5 — AIOps Incident & Log Analyzer**

This report records only results that were **actually executed and observed** in the
build environment. Where a step could not be run here (Docker/kind daemon, a real Ollama
model), that is stated plainly rather than claimed.

## Environment used for verification

- Python 3.13.14, pip with network access.
- Real binaries obtained and used: `helm v3.16.3`, `kubectl v1.31.3`, `kind v0.25.0`,
  plus a real `kube-apiserver v1.31.3` + `etcd v3.5.16` started locally for schema validation.
- **Docker daemon was NOT available** in this sandbox, so image build / `kind create cluster`
  / container run could not be executed here. They are provided and documented, and the
  manifests they deploy were validated against a real API server.
- **Ollama was NOT installed** here, so live model inference was not run. The LLM client
  was instead exercised against a real local HTTP server (see tests).

## Requirement → Implementation → Verification → Result → Evidence

| Requirement | Implementation | Verification | Result | Evidence |
| --- | --- | --- | --- | --- |
| Application generates structured JSON logs | `app/log_generator.py`, `app/logging_config.py` | unit tests + live run | PASS | JSON records observed on stdout/file |
| Normal behavior + deterministic failures | `app/simulator.py`, `app/faults.py` | route + correlate tests | PASS | 5 scenarios detected |
| 5 failure scenarios w/ ground truth | `app/faults.py` (`*_timeout`, etc.) | `test_correlate.py`, evaluation | PASS | 5/5 |
| Failure injection mechanism | `scripts/inject_errors.py`, `POST /admin/faults` | live curl + tests | PASS | dependency-timeout injected live |
| Log collector (bounded) | `app/analyzer/collector.py` | `test_collector.py` | PASS | window/tail tests |
| Log windowing (configurable) | `LOG_WINDOW_SIZE`, `LLM_MAX_CONTEXT_LINES` | config + collector tests | PASS | default 2000 / 60 |
| Log parser (resilient) | `app/analyzer/parser.py` | `test_parser.py` | PASS | malformed→noise |
| Rule-based correlation | `app/analyzer/correlate.py` | `test_correlate.py` | PASS | bursts/dominant pattern |
| Baseline non-AI analysis | pipeline runs rules before LLM | demo with LLM disabled | PASS | 5/5 with no LLM |
| Severity (deterministic) | `app/analyzer/severity.py` | `test_severity.py` | PASS | auditable factors |
| Ollama integration (configurable model) | `app/analyzer/llm_client.py` | live HTTP server tests | PASS | ok/timeout/unavailable/etc. |
| Strict JSON AI output + validation | `validate_llm_output`, `extract_json` | `test_llm_output_validation.py` | PASS | malformed/invalid rejected |
| unknown / insufficient-evidence | coerced + validated | tests | PASS | never fabricated |
| LLM cannot execute commands | no subprocess/exec anywhere | grep audit | PASS | see Security |
| Incident report (JSON+MD) | `app/analyzer/report.py` | `test_reports.py` | PASS | both artifacts written |
| Local persistence + retention | `ReportStore` | `test_reports.py` | PASS | pruning verified |
| Prometheus metrics (real) | `app/metrics.py`, `/metrics` | `test_metrics.py`, live | PASS | counters move with traffic |
| Grafana dashboard (as code) | `observability/grafana/*` | JSON/YAML parse | PASS | 8 panels provisioned |
| Notification (optional webhook) | `app/analyzer/notify.py` | `test_notify.py` | PASS | graceful when unconfigured |
| Human validation workflow | `validation` field + `/validate` | route tests | PASS | pending→confirmed |
| Runbook / advisory remediation | `app/analyzer/runbook.py` | `test_incident_routes.py` | PASS | auto_executed=false |
| Kubernetes manifests | `k8s/*.yaml` | **server-side strict validation** | PASS | all created (dry-run) |
| Helm chart | `helm/` | `helm lint` + `template` + schema validation | PASS | lint 0 failed |
| Liveness/readiness probes | deployment + `/health/*` | schema + route tests | PASS | fail under faults |
| 2+ replicas | `replicas: 2` | manifest + helm `--set replicaCount=3` | PASS | validated |
| Evaluation harness | `app/analyzer/evaluation.py` | `test_evaluation.py`, live | PASS | 5/5, 0 FP |
| Tests (pytest) | `tests/` (13 files) | `pytest` | PASS | 115 passed |
| CI | `.github/workflows/ci.yml` | YAML review (not run on GH here) | PROVIDED | lint+test+docker jobs |
| Security review | this report + code audit | grep + redaction test | PASS | see Security |
| Custom UI | `app/ui/*`, `/ui` | live 200s + tests | PASS | all pages render |
| Docs | `docs/*.md`, README | written | PASS | 5 docs + README |
| ZIP deliverable | `aiops-incident-log-analyzer.zip` | built + contents checked | PASS | excludes caches/secrets/weights |

## Verification commands actually run (and results)

```text
ruff check .                     -> All checks passed!
pytest                           -> 115 passed, 2 skipped (helm not on PATH in that shell)
pytest (helm on PATH)            -> 117 passed, 0 skipped
helm lint helm/                  -> 1 chart(s) linted, 0 chart(s) failed
helm template ... | kubectl apply --dry-run=server --validate=strict
                                 -> all resources created (server dry run)
kubectl apply --dry-run=server --validate=strict -f k8s/*.yaml
                                 -> namespace/configmap/service/deployment/secret created
kubectl patch --patch-file k8s/probes.yaml --dry-run=server
                                 -> deployment patched
python scripts/run_evaluation.py -> Rule-based accuracy: 5/5, LLM: n/a (unavailable), FP: 0
python scripts/run_demo.py       -> all 5 scenarios MATCH, normal traffic true_negative
clean checkout: venv + pip install + ruff + pytest + run_demo
                                 -> All checks passed; 115 passed; demo 5/5
live server (clean checkout, port 8055):
  /health/live, /health/ready, /ui/  -> 200
  /metrics                           -> http_requests_total present
  POST /admin/faults dependency-timeout -> ground_truth dependency_timeout
  POST /analyze                      -> incident_created True, cause dependency_timeout
  POST /evaluation/run               -> rule 5/5, llm n/a, fp 0
```

## Evaluation results (observed)

```text
Scenario            Expected              Detected              Result
--------------------------------------------------------------------------------
Database timeout    database_timeout      database_timeout      match
Memory pressure     memory_pressure       memory_pressure       match
HTTP 500 burst      http_500_burst        http_500_burst        match
Dependency timeout  dependency_timeout    dependency_timeout    match
Invalid config      invalid_configuration invalid_configuration match
Normal traffic      -                     unknown               true_negative
--------------------------------------------------------------------------------
Rule-based accuracy: 5/5    LLM accuracy: n/a (Ollama not installed here)
False positives: 0
```

The LLM column is `n/a` because Ollama was not installed in this sandbox. This is an
honest reflection of the run — it is **not** a hidden failure and the ground truth was
**not** altered. On a machine with Ollama + a pulled model, `python scripts/run_evaluation.py`
populates the LLM column with real numbers.

## Security checks performed

| Check | Method | Finding |
| --- | --- | --- |
| Command execution (subprocess/os.system/eval/exec/shell=True) | grep over `app/`, `scripts/` | **None found** |
| LLM `recommended_action` executed? | grep | **Never executed** (advisory only) |
| Hard-coded secrets/keys/tokens | grep over source + manifests | **None** (placeholders only) |
| `.env` committed? | filesystem check | **Absent**; `.env.example` present and git-ignored target |
| Secret redaction in logs | runtime test | `password=`/`token=`/`api_key` → `***REDACTED***` |
| Container user | Dockerfile review | non-root `appuser` (uid via useradd), `USER appuser` |
| K8s security context | manifest review | runAsNonRoot, drop ALL caps, no priv-escalation, limits |
| Service exposure | manifest review | ClusterIP only; Ollama referenced in-cluster |
| LLM output handling | code + tests | strict schema validation; out-of-vocab coerced to unknown |
| Security headers | middleware + live check | CSP, nosniff, frame-deny, referrer-policy present |
| Dependency review | `pip check` | No broken requirements |
| Webhook token leakage | `test_notify.py` | token-bearing URL redacted from error detail |

**This project is NOT production-hardened and does NOT claim complete security.**

### Remaining risks / limitations

- No authentication on UI/read APIs by default; `/admin/*` is only protected when
  `ADMIN_TOKEN` is set. Intended for local/demo use.
- Multi-replica aggregation uses `emptyDir` (per-pod logs/incidents), not a shared store.
- Rule-based detection is tuned to the five known scenarios; not general-purpose RCA.
- `readOnlyRootFilesystem` is false (the app writes logs/incidents); tighten with volumes
  for production.
- Dependency versions are pinned but not vulnerability-scanned in this report; add
  `pip-audit`/Dependabot for continuous scanning.
- Docker image build and kind cluster run were not executed here (no Docker daemon);
  they are provided, documented, and their manifests schema-validated.

## Known limitations (summary)

See README "Limitations" and `docs/assumptions.md`. Key points: OpenTelemetry not
implemented (optional per spec); LLM accuracy varies by model/machine; bounded windows can
miss out-of-window causes (reported as `uncertain`, never fabricated).

## Assumptions

See `docs/assumptions.md` for the full list (framework choice, hand-rolled metrics,
emptyDir storage, default model, fixed cause vocabulary, optional admin auth, etc.).
