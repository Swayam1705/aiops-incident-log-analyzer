"""Ollama LLM client with strict output validation.

Safety contract (enforced here and by the surrounding pipeline):

* The model receives a **bounded**, structured prompt: metadata + rule-based
  evidence + a capped set of evidence lines. Never raw unbounded logs.
* The model is **advisory**. It returns JSON. It cannot execute commands, call
  tools, touch the filesystem, run kubectl/docker, or trigger remediation. There is
  no code path that interprets its output as an instruction.
* Raw output is **never trusted**. We parse, schema-check, bound and sanitize it.
  Any failure degrades to an explicit ``unavailable``/``validation_error``/
  ``insufficient_evidence`` status - the deterministic report still stands.

Only the standard library is used (urllib), so no extra runtime dependency and no
surprise network behaviour.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

REQUIRED_FIELDS = ("summary", "suspected_root_cause", "evidence_lines", "confidence", "recommended_action")

# Canonical cause vocabulary the model is constrained to. 'unknown' is always allowed
# so the model can (and is instructed to) abstain rather than fabricate a cause.
ALLOWED_CAUSES = {
    "database_timeout",
    "memory_pressure",
    "http_500_burst",
    "dependency_timeout",
    "invalid_configuration",
    "liveness_probe_failure",
    "unknown",
    "insufficient_evidence",
}

PROMPT_TEMPLATE = """You are an advisory incident-analysis assistant for an SRE team.
You do NOT have tools. You MUST NOT execute commands or propose running them.
You only analyze the evidence below and return STRICT JSON.

Rules:
- Return ONLY a single JSON object. No prose, no markdown fences.
- Use exactly these keys: {required}.
- "suspected_root_cause" must be one of: {causes}.
- If the evidence is insufficient, set "suspected_root_cause" to "unknown" and
  "confidence" to a low value. NEVER invent a root cause.
- "evidence_lines" must be copied verbatim from the EVIDENCE section (max {max_ev}).
- "confidence" is a number between 0.0 and 1.0.
- "recommended_action" is advisory guidance for a human; do not include shell commands.

CONTEXT (structured, from the deterministic analyzer):
{context}

EVIDENCE (recent correlated log lines, bounded):
{evidence}

Return the JSON object now."""


@dataclass
class LLMResult:
    available: bool
    # ok | insufficient_evidence | validation_error | unavailable | timeout | disabled | model_not_found
    status: str
    model: str | None = None
    summary: str | None = None
    suspected_root_cause: str | None = None
    evidence_lines: list[str] | None = None
    confidence: float | None = None
    recommended_action: str | None = None
    error: str | None = None
    raw_excerpt: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "status": self.status,
            "model": self.model,
            "summary": self.summary,
            "suspected_root_cause": self.suspected_root_cause,
            "evidence_lines": self.evidence_lines or [],
            "confidence": self.confidence,
            "recommended_action": self.recommended_action,
            "error": self.error,
            "raw_excerpt": self.raw_excerpt,
        }


def disabled_result(reason: str = "llm disabled by configuration") -> LLMResult:
    return LLMResult(available=False, status="disabled", error=reason)


def build_prompt(
    context: dict[str, Any],
    evidence_lines: list[str],
    max_evidence: int,
) -> str:
    """Build the bounded prompt sent to the model."""
    bounded_evidence = evidence_lines[:max_evidence]
    return PROMPT_TEMPLATE.format(
        required=", ".join(REQUIRED_FIELDS),
        causes=", ".join(sorted(ALLOWED_CAUSES)),
        max_ev=max_evidence,
        context=json.dumps(context, indent=2, default=str),
        evidence="\n".join(f"- {line}" for line in bounded_evidence) or "(no evidence lines)",
    )


def extract_json(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from model output.

    Tolerates: bare JSON, code fences, leading/trailing prose, and trailing commas.
    Returns None when no JSON object can be recovered.
    """
    if not text:
        return None
    candidate = text.strip()
    # Strip common code fences.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.DOTALL)
    if fence:
        candidate = fence.group(1)
    else:
        # Fall back to the first {...} span (greedy to the last brace).
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = candidate[start : end + 1]
    candidate = candidate.strip()
    # Remove trailing commas before } or ] which some small models emit.
    candidate = re.sub(r",(\s*[}\]])", r"\1", candidate)
    try:
        loaded = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def validate_llm_output(
    payload: dict[str, Any],
    max_evidence: int,
    max_response_chars: int,
) -> tuple[LLMResult | None, str | None]:
    """Validate and normalize a decoded JSON payload.

    Returns ``(result, None)`` on success, or ``(None, error_message)`` on failure.
    Unknown extra fields are ignored (not fatal). Missing/invalid required fields
    are fatal because we must never present partial model output as trustworthy.
    """
    if not isinstance(payload, dict):
        return None, "output is not a JSON object"

    missing = [field for field in REQUIRED_FIELDS if field not in payload]
    if missing:
        return None, f"missing required fields: {', '.join(missing)}"

    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None, "'summary' must be a non-empty string"
    if len(summary) > max_response_chars:
        summary = summary[:max_response_chars]

    cause_raw = payload.get("suspected_root_cause")
    if not isinstance(cause_raw, str):
        return None, "'suspected_root_cause' must be a string"
    cause = cause_raw.strip().lower()
    if cause in {"", "none", "n/a", "null", "insufficient", "insufficient evidence"}:
        cause = "insufficient_evidence"
    if cause not in ALLOWED_CAUSES:
        # Do not fabricate: coerce an out-of-vocabulary cause to unknown.
        cause = "unknown"

    evidence_raw = payload.get("evidence_lines")
    if not isinstance(evidence_raw, list):
        return None, "'evidence_lines' must be a list"
    evidence: list[str] = []
    for item in evidence_raw[:max_evidence]:
        if isinstance(item, str) and item.strip():
            evidence.append(item.strip()[:500])

    confidence_raw = payload.get("confidence")
    if isinstance(confidence_raw, bool):
        return None, "'confidence' must be a number, not a boolean"
    if isinstance(confidence_raw, (int, float)):
        confidence = float(confidence_raw)
    elif isinstance(confidence_raw, str):
        try:
            confidence = float(confidence_raw.strip())
        except ValueError:
            return None, "'confidence' must be a number between 0 and 1"
    else:
        return None, "'confidence' must be a number between 0 and 1"
    if not 0.0 <= confidence <= 1.0:
        return None, f"'confidence' out of range [0,1]: {confidence}"

    action_raw = payload.get("recommended_action")
    if action_raw is None:
        action = None
    elif isinstance(action_raw, str):
        action = action_raw.strip()[:max_response_chars]
    elif isinstance(action_raw, list):
        action = "; ".join(str(x) for x in action_raw)[:max_response_chars]
    else:
        return None, "'recommended_action' must be a string"

    status = "insufficient_evidence" if cause in {"unknown", "insufficient_evidence"} else "ok"
    result = LLMResult(
        available=True,
        status=status,
        summary=summary.strip(),
        suspected_root_cause=cause,
        evidence_lines=evidence,
        confidence=round(confidence, 3),
        recommended_action=action,
    )
    return result, None


class OllamaClient:
    """Minimal, dependency-free Ollama client used for advisory analysis."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:1.5b",
        timeout_seconds: float = 45.0,
        max_context_lines: int = 60,
        max_response_chars: int = 8000,
        temperature: float = 0.0,
        enabled: bool = True,
    ) -> None:
        self.base_url = (base_url or "http://localhost:11434").rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_context_lines = max_context_lines
        self.max_response_chars = max_response_chars
        self.temperature = temperature
        self.enabled = enabled

    # -- connectivity ----------------------------------------------------
    def available(self) -> tuple[bool, str | None]:
        """Check whether the Ollama server responds. Never raises."""
        if not self.enabled:
            return False, "llm disabled by configuration"
        if not self.model:
            return False, "no model configured"
        try:
            request = urllib.request.Request(f"{self.base_url}/api/tags", method="GET")
            with urllib.request.urlopen(request, timeout=min(5.0, self.timeout_seconds)) as response:
                if response.status != 200:
                    return False, f"ollama returned HTTP {response.status}"
                body = response.read(65536).decode("utf-8", errors="replace")
            try:
                models = [m.get("name") for m in json.loads(body).get("models", [])]
            except (json.JSONDecodeError, ValueError):
                models = []
            if models and not any(self.model == m or self.model in (m or "") for m in models):
                available_list = ", ".join(str(m) for m in models)
                return False, f"model '{self.model}' not found (available: {available_list})"
            return True, None
        except urllib.error.URLError as exc:
            return False, f"ollama unreachable: {exc.reason}"
        except TimeoutError:
            return False, "ollama connection timed out"
        except (OSError, ValueError) as exc:
            return False, f"ollama check failed: {exc}"

    # -- analysis --------------------------------------------------------
    def analyze(self, context: dict[str, Any], evidence_lines: list[str]) -> LLMResult:
        """Run advisory analysis. Returns a validated result or an explicit failure."""
        if not self.enabled:
            return disabled_result()
        if not self.model:
            return LLMResult(available=False, status="disabled", error="no model configured")

        prompt = build_prompt(context, evidence_lines, self.max_context_lines)
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": self.temperature},
        }
        data = json.dumps(payload).encode("utf-8")
        try:
            request = urllib.request.Request(
                f"{self.base_url}/api/generate",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(self.max_response_chars + 4096).decode("utf-8", errors="replace")
        except TimeoutError:
            return LLMResult(available=False, status="timeout", model=self.model, error="request timed out")
        except urllib.error.HTTPError as exc:
            detail = exc.read(512).decode("utf-8", errors="replace") if exc.fp else ""
            status = "model_not_found" if exc.code == 404 else "unavailable"
            return LLMResult(
                available=False,
                status=status,
                model=self.model,
                error=f"HTTP {exc.code}: {detail[:200]}",
            )
        except urllib.error.URLError as exc:
            return LLMResult(
                available=False,
                status="unavailable",
                model=self.model,
                error=f"ollama unreachable: {exc.reason}",
            )
        except (OSError, ValueError) as exc:
            return LLMResult(
                available=False, status="unavailable", model=self.model, error=f"request failed: {exc}"
            )

        # Ollama /api/generate returns {"response": "...", ...}
        try:
            envelope = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return LLMResult(
                available=False,
                status="validation_error",
                model=self.model,
                error="ollama response was not valid JSON envelope",
                raw_excerpt=raw[:300],
            )
        text = envelope.get("response") if isinstance(envelope, dict) else None
        if not isinstance(text, str) or not text.strip():
            return LLMResult(
                available=False,
                status="validation_error",
                model=self.model,
                error="empty model response",
                raw_excerpt=raw[:300],
            )

        decoded = extract_json(text)
        if decoded is None:
            return LLMResult(
                available=False,
                status="validation_error",
                model=self.model,
                error="model did not return parseable JSON",
                raw_excerpt=text[:300],
            )

        result, error = validate_llm_output(
            decoded, max_evidence=self.max_context_lines, max_response_chars=self.max_response_chars
        )
        if result is None:
            return LLMResult(
                available=False,
                status="validation_error",
                model=self.model,
                error=error,
                raw_excerpt=text[:300],
            )
        result.model = self.model
        result.raw_excerpt = text[:300]
        return result
