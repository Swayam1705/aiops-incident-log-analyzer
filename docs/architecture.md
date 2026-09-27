# Architecture

AIOps Incident & Log Analyzer is a local, zero-cost operational assistant. It collects
application logs, runs **deterministic (non-AI) analysis first**, optionally consults a
**local LLM through Ollama as an advisory second opinion**, stores a structured incident
report, exposes Prometheus metrics, and renders Grafana + a custom UI. A human validates
every diagnosis before any remediation.

The single most important design rule: **the deterministic pipeline never depends on the
LLM.** If Ollama is down, slow, or returns garbage, the rule-based incident report still
stands on its own.

## End-to-end flow

```text
Application (checkout-service) generates normal + failure logs
        |  structured JSON -> stdout + rotating file (logs/app.log)
        v
Collector  (bounded tail: most recent LOG_WINDOW_SIZE lines)
        v
Parser     (JSON-first, malformed lines -> noise, never crashes)
        v
Correlate  (rule-based: bursts, dominant error pattern, per-service counts)
        v
Severity   (deterministic: incident type + volume + burst + CRITICAL lines)
        v
Runbook    (advisory guidance selected by root cause)
        v
Ollama LLM (optional, advisory; strict JSON; validated; may abstain)
        v
Incident report (JSON + Markdown) -> incidents/generated/
        v
Prometheus metrics -> Grafana dashboard
        v
Notification (optional webhook)
        v
Human validates diagnosis  ->  Runbook / remediation (never auto-executed)
```

## Component diagram

```text
                    +--------------------------------------------------+
                    |                FastAPI application               |
                    |                                                  |
   HTTP traffic --> | /api/*  business endpoints (workload under obs.) |
                    | /metrics Prometheus exposition                   |
                    | /analyze /incidents /logs /evaluation /runbooks  |
                    | /admin/faults  deterministic failure injection   |
                    | /ui  server-rendered dashboard (Jinja2)          |
                    +-----------------+--------------------------------+
                                      |
              +-----------------------+------------------------+
              |                       |                        |
      +-------v-------+      +--------v--------+      +--------v--------+
      | Workload      |      | Analyzer        |      | Metrics registry|
      | simulator     |      | pipeline        |      | (stdlib)        |
      | (logs+faults) |      | collect->parse  |      +--------+--------+
      +-------+-------+      | ->correlate     |               |
              |              | ->severity      |               |
              | writes       | ->runbook       |               |
              v              | ->LLM(advisory) |               |
        logs/app.log         | ->report        |               |
              ^              +--------+--------+               |
              |                       |                        |
              |                +------v-------+                |
              +--- collector --| Report store |                |
                               | JSON + MD    |                |
                               +------+-------+                |
                                      |                        |
                               +------v-------+        +-------v-------+
                               | Notifier     |        | Prometheus    |
                               | (webhook)    |        | scrape /metrics
                               +--------------+        +-------+-------+
                                                               |
                                                        +------v-------+
                                                        | Grafana OSS  |
                                                        +--------------+
```

## The application (`app/`)

- `main.py` - FastAPI app factory + lifespan (starts/stops the simulator and scheduler).
- `runtime.py` - single wiring point shared by the web app and the CLI scripts.
- `routes.py` - JSON API (health, metrics, analyze, incidents, logs, evaluation, runbooks, admin).
- `ui_routes.py` - server-rendered UI pages.
- `middleware.py` - request metrics, request IDs, security headers.
- `simulator.py` - background workload + failure simulator (the thing under observation).
- `scheduler.py` - optional scheduled analysis trigger.
- `log_generator.py` - deterministic structured-log records for normal traffic and each failure.
- `faults.py` - the fault registry with fixed ground-truth labels.
- `logging_config.py` - structured JSON logging + secret redaction + rotating file sink.
- `metrics.py` - a small, dependency-free Prometheus registry.
- `config.py` - typed environment configuration.

## The analyzer (`app/analyzer/`)

- `collector.py` - bounded log windowing (tail bytes, merge files, never unbounded).
- `parser.py` - resilient parsing; extracts timestamp/service/severity/request_id/error pattern.
- `correlate.py` - deterministic correlation and root-cause inference.
- `severity.py` - deterministic severity scoring.
- `runbook.py` - advisory remediation guidance (never executed).
- `llm_client.py` - Ollama client with strict JSON validation and abstain support.
- `report.py` - incident model, JSON/Markdown rendering, bounded storage.
- `notify.py` - best-effort webhook notification.
- `pipeline.py` - orchestration with per-stage failure isolation.
- `evaluation.py` - ground-truth accuracy harness.

## Logging

Every record is one JSON object per line with `timestamp`, `level`, `service`, `logger`,
`event`, `message`, `request_id`, and error/typed fields. A `RedactingFilter` masks
secret-looking keys/values before formatting. Logs go to stdout and a rotating file that
the collector reads.

## Non-AI analysis first

`correlate.py` groups error lines, finds the dominant normalized error pattern, detects
bursts, and computes a deterministic confidence. This produces a usable incident with no
model involved. The LLM stage only *adds* an advisory opinion and an agreement flag.

## Ollama (advisory)

The client posts a bounded prompt (metadata + capped evidence lines) to
`/api/generate` with `format=json`, then validates the response: required fields present,
confidence in range, cause within a fixed vocabulary (out-of-vocabulary coerced to
`unknown`), evidence bounded. Any failure -> explicit non-fatal status. The model has no
tools and cannot execute anything.

## Kubernetes & Helm

`k8s/` holds raw manifests (namespace, deployment, service, configmap, secret example,
probes). `helm/` is the same deployment as a chart. Both run as non-root, with resource
limits, liveness/readiness/startup probes, and 2 replicas. Logs/incidents use `emptyDir`
(documented limitation for cross-replica aggregation).

## Observability

Prometheus scrapes `/metrics` (pod-annotation discovery in kind; static target in compose).
Grafana is provisioned from `observability/grafana/` (datasource + dashboard as code).

## Notifications & human validation

If `NOTIFICATION_WEBHOOK_URL` is set, a JSON summary is POSTed on incident creation.
Delivery is best-effort. Every incident starts in `validation: pending`; a human confirms
or rejects it via the API/UI. Remediation is documentation only.
