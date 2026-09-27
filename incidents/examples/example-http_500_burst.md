# Incident inc-20260926-143935-bfc03b

- **Timestamp:** 2026-09-26T14:39:35.357Z
- **Service:** checkout-service
- **Severity:** critical
- **Analysis source:** rule-based
- **Status:** open
- **Suspected root cause:** http_500_burst
- **Confidence:** 0.95
- **Ground truth (evaluation):** http_500_burst
- **Human validation:** pending

## Summary

Detected 88 correlated error line(s) on 'checkout-service' matching 'http_500_burst' (burst=True, severity=critical).

## Rule-based analysis

- Suspected cause: `http_500_burst`
- Confidence: `0.95`
- Error lines: `88`
- Noise lines ignored: `7`
- Burst detected: `True` (size 88)
- Reasoning:
  - 88 error(s) carry typed error.type=http_500_burst
  - pattern 'http_500_burst' dominates 100% of errors
  - detected burst of 88 correlated errors

## Evidence lines

```
[2026-09-26T14:38:39.345Z] ERROR checkout-service unhandled exception while processing request, returning HTTP 500 pattern=http_500_burst
[2026-09-26T14:38:39.695Z] ERROR checkout-service request failed with HTTP 500 internal server error pattern=http_500_burst
[2026-09-26T14:38:40.045Z] ERROR checkout-service unhandled exception while processing request, returning HTTP 500 pattern=http_500_burst
[2026-09-26T14:38:40.395Z] ERROR checkout-service request failed with HTTP 500 internal server error pattern=http_500_burst
[2026-09-26T14:38:40.745Z] ERROR checkout-service unhandled exception while processing request, returning HTTP 500 pattern=http_500_burst
[2026-09-26T14:38:41.095Z] ERROR checkout-service request failed with HTTP 500 internal server error pattern=http_500_burst
[2026-09-26T14:38:41.445Z] ERROR checkout-service unhandled exception while processing request, returning HTTP 500 pattern=http_500_burst
[2026-09-26T14:38:41.795Z] ERROR checkout-service request failed with HTTP 500 internal server error pattern=http_500_burst
[2026-09-26T14:38:42.145Z] ERROR checkout-service unhandled exception while processing request, returning HTTP 500 pattern=http_500_burst
[2026-09-26T14:38:42.495Z] ERROR checkout-service request failed with HTTP 500 internal server error pattern=http_500_burst
[2026-09-26T14:38:42.845Z] ERROR checkout-service unhandled exception while processing request, returning HTTP 500 pattern=http_500_burst
[2026-09-26T14:38:43.195Z] ERROR checkout-service request failed with HTTP 500 internal server error pattern=http_500_burst
[2026-09-26T14:38:43.545Z] ERROR checkout-service unhandled exception while processing request, returning HTTP 500 pattern=http_500_burst
[2026-09-26T14:38:43.895Z] ERROR checkout-service request failed with HTTP 500 internal server error pattern=http_500_burst
[2026-09-26T14:38:44.245Z] ERROR checkout-service unhandled exception while processing request, returning HTTP 500 pattern=http_500_burst
[2026-09-26T14:38:44.595Z] ERROR checkout-service request failed with HTTP 500 internal server error pattern=http_500_burst
[2026-09-26T14:38:44.945Z] ERROR checkout-service unhandled exception while processing request, returning HTTP 500 pattern=http_500_burst
[2026-09-26T14:38:45.295Z] ERROR checkout-service request failed with HTTP 500 internal server error pattern=http_500_burst
[2026-09-26T14:38:45.645Z] ERROR checkout-service unhandled exception while processing request, returning HTTP 500 pattern=http_500_burst
[2026-09-26T14:38:45.995Z] ERROR checkout-service request failed with HTTP 500 internal server error pattern=http_500_burst
```

## AI-assisted analysis (advisory, unvalidated)

- Status: `disabled`
- Model: `n/a`
- _AI analysis unavailable (disabled); the rule-based report above stands on its own._
- Detail: `llm disabled or not configured`

## Recommended action (advisory - requires human validation)

HTTP 500 burst: Read recent application logs for the exception type and stack trace. (advisory - validate before acting)

Runbook: `http_500_burst` (see docs/runbooks). No action is executed automatically.

---
_This report was produced by deterministic analysis without LLM assistance. The LLM never executes commands; a human must validate the diagnosis._
