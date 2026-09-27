# Incident inc-20260926-143935-68e774

- **Timestamp:** 2026-09-26T14:39:35.343Z
- **Service:** checkout-service
- **Severity:** critical
- **Analysis source:** rule-based
- **Status:** open
- **Suspected root cause:** database_timeout
- **Confidence:** 0.95
- **Ground truth (evaluation):** database_timeout
- **Human validation:** pending

## Summary

Detected 59 correlated error line(s) on 'checkout-service' matching 'database_timeout' (burst=True, severity=critical).

## Rule-based analysis

- Suspected cause: `database_timeout`
- Confidence: `0.95`
- Error lines: `59`
- Noise lines ignored: `7`
- Burst detected: `True` (size 59)
- Reasoning:
  - 59 error(s) carry typed error.type=database_timeout
  - pattern 'database_timeout' dominates 100% of errors
  - detected burst of 59 correlated errors

## Evidence lines

```
[2026-09-26T14:38:39.330Z] ERROR checkout-service database query timed out after 5000ms while selecting pending orders pattern=database_timeout
[2026-09-26T14:38:40.030Z] ERROR checkout-service checkout failed: database transaction timed out pattern=database_timeout
[2026-09-26T14:38:40.380Z] ERROR checkout-service database query timed out after 5000ms while selecting pending orders pattern=database_timeout
[2026-09-26T14:38:41.080Z] ERROR checkout-service checkout failed: database transaction timed out pattern=database_timeout
[2026-09-26T14:38:41.430Z] ERROR checkout-service database query timed out after 5000ms while selecting pending orders pattern=database_timeout
[2026-09-26T14:38:42.130Z] ERROR checkout-service checkout failed: database transaction timed out pattern=database_timeout
[2026-09-26T14:38:42.480Z] ERROR checkout-service database query timed out after 5000ms while selecting pending orders pattern=database_timeout
[2026-09-26T14:38:43.180Z] ERROR checkout-service checkout failed: database transaction timed out pattern=database_timeout
[2026-09-26T14:38:43.530Z] ERROR checkout-service database query timed out after 5000ms while selecting pending orders pattern=database_timeout
[2026-09-26T14:38:44.230Z] ERROR checkout-service checkout failed: database transaction timed out pattern=database_timeout
[2026-09-26T14:38:44.580Z] ERROR checkout-service database query timed out after 5000ms while selecting pending orders pattern=database_timeout
[2026-09-26T14:38:45.280Z] ERROR checkout-service checkout failed: database transaction timed out pattern=database_timeout
[2026-09-26T14:38:45.630Z] ERROR checkout-service database query timed out after 5000ms while selecting pending orders pattern=database_timeout
[2026-09-26T14:38:46.330Z] ERROR checkout-service checkout failed: database transaction timed out pattern=database_timeout
[2026-09-26T14:38:46.680Z] ERROR checkout-service database query timed out after 5000ms while selecting pending orders pattern=database_timeout
[2026-09-26T14:38:47.380Z] ERROR checkout-service checkout failed: database transaction timed out pattern=database_timeout
[2026-09-26T14:38:47.730Z] ERROR checkout-service database query timed out after 5000ms while selecting pending orders pattern=database_timeout
[2026-09-26T14:38:48.430Z] ERROR checkout-service checkout failed: database transaction timed out pattern=database_timeout
[2026-09-26T14:38:48.780Z] ERROR checkout-service database query timed out after 5000ms while selecting pending orders pattern=database_timeout
[2026-09-26T14:38:49.480Z] ERROR checkout-service checkout failed: database transaction timed out pattern=database_timeout
```

## AI-assisted analysis (advisory, unvalidated)

- Status: `disabled`
- Model: `n/a`
- _AI analysis unavailable (disabled); the rule-based report above stands on its own._
- Detail: `llm disabled or not configured`

## Recommended action (advisory - requires human validation)

Database timeout: Confirm the database is reachable from the application pods. (advisory - validate before acting)

Runbook: `database_timeout` (see docs/runbooks). No action is executed automatically.

---
_This report was produced by deterministic analysis without LLM assistance. The LLM never executes commands; a human must validate the diagnosis._
