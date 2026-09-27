# Assumptions

These are **implementation assumptions** made where the specification left a detail open.
They are not source requirements. Each was chosen to be the smallest reasonable decision
that yields a working, maintainable, testable project.

1. **Web framework: FastAPI.** The spec allowed FastAPI or Flask; FastAPI was chosen for
   its typed requests, automatic `/docs`, and async middleware. Used consistently.

2. **Prometheus client: hand-rolled.** Metrics use a small stdlib registry rather than
   `prometheus_client`, to keep the runtime image minimal and the metric names/labels
   explicit. Output is valid Prometheus text exposition format v0.0.4.

3. **The application is also the workload generator.** A single service
   (`checkout-service`) both serves demo business endpoints and runs a background
   simulator that emits normal + failure logs. This keeps the project self-contained;
   in production the collector would read other services' logs.

4. **Log collection is a bounded file reader.** A Promtail/Loki pipeline is optional and
   not required. The collector tails the most recent bytes of `logs/*.log`. The bounded
   window contract is identical to what a Promtail deployment would honour.

5. **Default log window is 2000 lines** (`LOG_WINDOW_SIZE`), configurable. The LLM sees a
   further-bounded subset (`LLM_MAX_CONTEXT_LINES`, default 60 evidence lines).

6. **Incident storage is JSON + Markdown files** under `incidents/generated/`, capped by
   `REPORT_RETENTION_LIMIT` (default 200). SQLite was judged unnecessary.

7. **Multi-replica log/incident sharing uses `emptyDir`.** kind has no ReadWriteMany
   storage class by default, so each replica analyzes its own log window. A RWX PVC is the
   documented production swap-in. This is called out as a limitation.

8. **Ollama default model is `qwen2.5:1.5b`** (laptop-friendly). The model is fully
   configurable; an empty `OLLAMA_MODEL` disables the LLM stage gracefully.

9. **Severity vocabulary is `critical|high|medium|low|info`** with the deterministic rules
   documented in `docs/incident-format.md`.

10. **Root-cause vocabulary is fixed** to the five ground-truth labels plus
    `unknown`/`insufficient_evidence`. Out-of-vocabulary LLM output is coerced to `unknown`
    rather than trusted.

11. **Notification is a generic JSON webhook.** Any free/local receiver works
    (Slack/Discord/ntfy/webhook.site). No provider is hard-coded and no credential is
    stored in the repo.

12. **Admin auth is optional.** When `ADMIN_TOKEN` is set, `/admin/*` requires it; when
    empty (local default) the routes are open. This is documented as a limitation, not a
    claim of production authorization.

13. **OpenTelemetry is not implemented.** The spec made it optional; it was omitted to
    avoid visual/complexity overhead that does not improve the core observability story.
    Metrics + logs + Grafana already satisfy the observability requirement.

14. **The `liveness-failure` scenario is a probe demonstration**, not one of the five
    scored evaluation scenarios. It exists to make liveness/readiness behaviour visible.

15. **Evaluation uses generated fixtures with the same shapes the running app emits.**
    This is deterministic and offline-friendly; a live end-to-end run against the running
    service is also supported via the API and `scripts/run_demo.py`.
