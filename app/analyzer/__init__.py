"""Non-AI-first incident analysis package.

Submodules:
    collector  - bounded log windowing from files / memory
    parser     - resilient structured-log parsing
    correlate  - deterministic rule-based correlation + root-cause inference
    severity   - deterministic severity scoring
    llm_client - advisory Ollama client with strict output validation
    report     - incident model, JSON/Markdown rendering, bounded storage
    runbook    - advisory remediation guidance (never auto-executed)
    notify     - best-effort webhook notification
    pipeline   - orchestration tying the stages together
    evaluation - ground-truth accuracy harness
"""

from app.analyzer.collector import CollectionResult, collect_from_glob, collect_lines
from app.analyzer.correlate import CorrelationResult, correlate
from app.analyzer.evaluation import EvaluationReport, run_evaluation
from app.analyzer.llm_client import LLMResult, OllamaClient, validate_llm_output
from app.analyzer.notify import NotificationResult, Notifier
from app.analyzer.parser import ParsedRecord, parse_line, parse_lines
from app.analyzer.pipeline import AnalysisOutcome, Analyzer
from app.analyzer.report import (
    AIAnalysis,
    IncidentReport,
    ReportStore,
    RuleAnalysis,
    build_report,
)
from app.analyzer.runbook import RUNBOOKS, get_runbook, list_runbooks
from app.analyzer.severity import SeverityDecision, compute_severity

__all__ = [
    "AIAnalysis",
    "AnalysisOutcome",
    "Analyzer",
    "CollectionResult",
    "CorrelationResult",
    "EvaluationReport",
    "IncidentReport",
    "LLMResult",
    "NotificationResult",
    "Notifier",
    "OllamaClient",
    "ParsedRecord",
    "ReportStore",
    "RuleAnalysis",
    "RUNBOOKS",
    "SeverityDecision",
    "build_report",
    "collect_from_glob",
    "collect_lines",
    "compute_severity",
    "correlate",
    "get_runbook",
    "list_runbooks",
    "parse_line",
    "parse_lines",
    "run_evaluation",
    "validate_llm_output",
]
