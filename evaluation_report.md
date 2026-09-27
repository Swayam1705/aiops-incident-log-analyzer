# Evaluation Report

- Rule-based accuracy: **5/5**
- LLM-assisted accuracy: **n/a (llm unavailable)**
- LLM available during run: `False` (model: `n/a`)
- Log window size: `200`

## Results

| Scenario | Expected | Rule detected | Rule conf. | LLM status | LLM detected | Outcome |
| --- | --- | --- | --- | --- | --- | --- |
| Database timeout | database_timeout | database_timeout | 0.95 | disabled | - | match |
| Memory pressure | memory_pressure | memory_pressure | 0.95 | disabled | - | match |
| HTTP 500 burst | http_500_burst | http_500_burst | 0.95 | disabled | - | match |
| Dependency timeout | dependency_timeout | dependency_timeout | 0.95 | disabled | - | match |
| Invalid config | invalid_configuration | invalid_configuration | 0.95 | disabled | - | match |
| Normal traffic | - | unknown | 0.00 | disabled | - | true_negative |

_Ground truth is fixed by the specification and is never altered to match
analyzer output. 'unknown'/insufficient-evidence outcomes are reported as-is._
