# Evaluation

The evaluation harness (`app/analyzer/evaluation.py`, CLI `scripts/run_evaluation.py`,
API `POST /evaluation/run`) runs each known failure scenario through the **real**
pipeline and scores the result against fixed ground truth.

## Ground-truth scenarios

| Scenario | Ground-truth label | Expected analyzer result |
| --- | --- | --- |
| Database timeout | `database_timeout` | incident created, cause matched |
| Memory pressure | `memory_pressure` | incident created, cause matched |
| HTTP 500 burst | `http_500_burst` | incident created, cause matched |
| Dependency timeout | `dependency_timeout` | incident created, cause matched |
| Invalid configuration | `invalid_configuration` | incident created, cause matched |
| Normal traffic (control) | _(none)_ | **no** incident (true negative) |

Ground-truth labels are defined in `app/faults.py` and are **never** derived from or
adjusted to match analyzer output.

## Process

For each scenario the harness:

1. Generates a deterministic bounded log window (failure records + normal traffic +
   irrelevant noise + a few unparseable lines).
2. Runs collect -> parse -> correlate -> severity -> (advisory LLM) -> report.
3. Compares the detected cause to the ground truth.

## Outcomes

- `match` - detected cause equals ground truth.
- `mismatch` - a different specific cause was detected.
- `uncertain` - the analyzer abstained (`unknown` / insufficient evidence).
- `true_negative` - the normal-traffic control produced no incident.
- `false_positive` - the normal-traffic control produced an incident (a real defect).

## Accuracy calculation

- **Rule-based accuracy** = matched scenarios / scored scenarios (the five failures).
- **LLM accuracy** = scenarios where the LLM's `suspected_root_cause` equals ground
  truth / scored scenarios. Reported **separately** and may legitimately be lower, or
  `n/a (llm unavailable)` when Ollama is not reachable.

## Observed results (this environment, Ollama not installed)

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
Rule-based accuracy: 5/5    LLM accuracy: n/a (llm unavailable)
```

The rule-based analyzer scored **5/5** with **0 false positives** on the control. The LLM
column reports `n/a` here because Ollama was not installed in this sandbox; that is an
honest reflection of the run, not a hidden failure. On a machine with Ollama + a model,
re-run `python scripts/run_evaluation.py` to populate the LLM column with real numbers.

## Limitations

- The five failure log shapes are known to the parser's pattern rules, so rule-based
  detection is expected to be strong on exactly these scenarios. This measures the
  deterministic layer honestly; it is not a claim of general-purpose RCA ability.
- LLM accuracy varies by model, prompt, and machine. It is reported as observed and is
  allowed to abstain. Do not treat a single run as a stable benchmark.
- Windows are bounded; a cause whose signal falls outside the window may be missed
  (reported as `uncertain`, never fabricated).

## Reproduce

```bash
python scripts/run_evaluation.py --window-size 200 --out evaluation_report.md
# or against a running service:
curl -X POST "http://localhost:8000/evaluation/run?window_size=200"
```
