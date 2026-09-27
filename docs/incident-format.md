# Incident Report Format

An incident report is the normalized artifact produced by the pipeline and stored as
both JSON (`incidents/generated/<id>.json`) and Markdown (`<id>.md`). The schema version
is `1.0`.

## Fields

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `schema_version` | string | yes | Schema version (`1.0`). |
| `incident_id` | string | yes | Unique id `inc-YYYYMMDD-HHMMSS-<hex>`. |
| `timestamp` | string (ISO-8601 UTC) | yes | When the report was created. |
| `service` | string | yes | Service the incident was detected on. |
| `severity` | enum | yes | `critical` \| `high` \| `medium` \| `low` \| `info`. |
| `severity_decision` | object | yes | `{severity, score, factors[]}` - the deterministic reasoning. |
| `summary` | string | yes | One-line human summary. |
| `suspected_root_cause` | string \| null | yes | Normalized cause, or `null` when unknown. |
| `evidence_lines` | string[] | yes | Real correlated log lines (bounded). |
| `confidence` | number 0..1 | yes | Rule-based confidence in the suspected cause. |
| `recommended_action` | string | yes | Advisory next step (never executed). |
| `analysis_source` | enum | yes | `rule-based` \| `llm-assisted`. |
| `status` | string | yes | Lifecycle status (`open`). |
| `validation` | object | yes | Human-in-the-loop decision (see below). |
| `rule_analysis` | object | yes | Deterministic analysis detail. |
| `ai_analysis` | object | yes | Advisory LLM output + availability/status. |
| `ground_truth` | string \| null | optional | Injected scenario label (evaluation mode only). |
| `window` | object | yes | `{size, start, end, log_window_size}` analyzed. |
| `runbook_id` | string \| null | optional | Matching runbook for the suspected cause. |

### `rule_analysis`

`{suspected_root_cause, confidence, evidence_lines[], burst_detected, burst_size,
error_lines, noise_lines, reasons[], pattern_counts{}, numeric_signals{}}`

### `ai_analysis`

`{available, model, summary, suspected_root_cause, evidence_lines[], confidence,
recommended_action, status, error, raw_excerpt, agreed_with_rules}`

`status` is one of: `ok`, `insufficient_evidence`, `validation_error`, `unavailable`,
`timeout`, `disabled`, `model_not_found`.

### `validation`

`{status, validated_by, validated_at, note}` where `status` is `pending` (default),
`confirmed`, `false_positive`, or `rejected`.

## Severity semantics (deterministic)

Severity is **never** set by the LLM. It is computed from objective signals:

1. Base rank from the recognized incident type
   (`invalid_configuration`/`liveness_probe_failure` = critical; timeouts/500-burst/memory = high).
2. `+1` when a correlated burst is detected.
3. `+1` when error volume is high (`>= 20` error lines in the window).
4. A `CRITICAL` evidence line raises severity to at least `high`.
5. No suspected cause -> `info` (nothing actionable detected).

The `severity_decision.factors[]` array records exactly which rules fired, so the score is
auditable.

## Uncertainty handling

When evidence is insufficient, `suspected_root_cause` is `null` and `confidence` is `0`.
The LLM is instructed (and validated) to return `unknown`/`insufficient_evidence` rather
than invent a cause. Reports never fabricate a root cause to look decisive.

## Example (abridged JSON)

```json
{
  "incident_id": "inc-20260926-143935-68e774",
  "timestamp": "2026-09-26T14:39:35.343Z",
  "service": "checkout-service",
  "severity": "critical",
  "severity_decision": {"severity": "critical", "score": 4, "factors": ["..."]},
  "summary": "Detected 59 correlated error line(s) ... matching 'database_timeout'.",
  "suspected_root_cause": "database_timeout",
  "evidence_lines": ["[ts] ERROR checkout-service database query timed out ..."],
  "confidence": 0.95,
  "recommended_action": "Database timeout: Confirm the database is reachable ... (advisory)",
  "analysis_source": "rule-based",
  "validation": {"status": "pending", "validated_by": null, "validated_at": null, "note": null},
  "rule_analysis": {"error_lines": 59, "burst_detected": true, "burst_size": 59, "noise_lines": 7},
  "ai_analysis": {"available": false, "status": "disabled", "agreed_with_rules": null},
  "ground_truth": "database_timeout",
  "runbook_id": "database_timeout"
}
```

Full worked examples live in `incidents/examples/`.
