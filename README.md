# AIOps Incident & Log Analyzer

**Local AI-assisted incident analysis on Kubernetes — zero cost.**

An operational assistant that collects application logs, runs **deterministic
(non-AI) analysis first**, optionally consults a **local LLM through Ollama** as an
advisory second opinion, stores a structured incident report (JSON + Markdown), exposes
Prometheus metrics, renders a Grafana dashboard and a custom UI, sends an optional
notification, and **requires human validation** before any remediation.

> The LLM is **advisory**. It never executes commands, never runs kubectl/docker, and
> never performs remediation. If Ollama is unavailable, the rule-based incident report
> still stands on its own.

---

## Project overview

This is **Project 5: AIOps Incident & Log Analyzer**. It implements the full flow:

```text
app logs -> collect (bounded window) -> parse -> correlate (rules) -> severity
        -> Ollama (advisory) -> incident report (JSON+MD) -> Prometheus -> Grafana
        -> notification -> human validation -> runbook/remediation (advisory)
```

## Real-world problem

On-call engineers drown in logs during incidents. They need to know *what broke*, *how
bad*, and *what to check next* — fast — without an LLM confidently inventing a root
cause. This project pairs a **trustworthy deterministic analyzer** with an **optional
local LLM** that can only advise, and forces a **human** to validate the diagnosis before
acting. Everything runs locally for free.

## Complete architecture

See [`docs/architecture.md`](docs/architecture.md) for the full diagram. In short:

- A FastAPI service (`checkout-service`) emits structured JSON logs and can inject five
  deterministic failure scenarios.
- The analyzer collects a bounded log window, parses it, correlates errors, scores
  severity, optionally asks Ollama, and writes a report.
- Prometheus scrapes `/metrics`; Grafana is provisioned from code; a custom UI shows
  incidents, evidence, AI analysis, evaluation, and health.

## Features

- Structured JSON logging with secret redaction and request IDs
- Five deterministic failure scenarios with fixed ground-truth labels
- Bounded log windowing (never sends unlimited logs to the LLM)
- Resilient parser (malformed lines become noise, never crash the run)
- Rule-based correlation + deterministic severity (works with no LLM)
- Ollama integration with strict JSON validation and explicit `unknown` support
- Incident reports as JSON + Markdown with human-validation workflow
- Advisory runbooks (never auto-executed)
- Prometheus metrics (request/error/latency/incident) + Grafana dashboard as code
- Custom responsive UI, optional webhook notification
- Kubernetes (kind) + Helm deployment, liveness/readiness/startup probes, 2 replicas
- Ground-truth evaluation harness with honest accuracy reporting
- 115 pytest tests + ruff lint + GitHub Actions CI

## Technology stack

Python 3.10+ · FastAPI · Uvicorn · Pydantic · Jinja2 · Docker · kind · kubectl · Helm ·
Ollama (local model) · Prometheus · Grafana OSS · pytest · ruff · GitHub Actions.
OpenTelemetry is optional and **not** enabled (see `docs/assumptions.md`).

## Free / zero-cost strategy

No paid services anywhere. The AI model runs locally through Ollama; observability uses
Prometheus + Grafana OSS; persistence is local JSON/Markdown files; CI runs on
GitHub-hosted runners with no cluster and no model. There are no API keys.

## Prerequisites

- Python 3.10+
- Docker (for image build + kind)
- kind, kubectl, helm (for the Kubernetes demo)
- Ollama (for the advisory LLM; optional — the project works without it)

## Python setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install pytest ruff PyYAML httpx   # dev/test tools
cp .env.example .env               # then edit as needed
```

## Docker setup

```bash
docker build -f docker/Dockerfile -t aiops-incident-log-analyzer:local .
# Local non-Kubernetes mode (app + Prometheus + Grafana):
docker compose -f docker/compose.yaml up --build
```

## Ollama setup

```bash
# Install Ollama (see https://ollama.com), then:
ollama serve            # starts http://localhost:11434
```

## Local model setup

Choose a model that fits your machine's RAM. Smaller = lighter:

| Model | Approx. RAM | Good for |
| --- | --- | --- |
| `llama3.2:1b` | ~1.3 GB | very low-resource laptops |
| `qwen2.5:1.5b` | ~1.5 GB | default, laptop-friendly |
| `llama3.1:8b` | ~6 GB | better reasoning, more RAM |

```bash
ollama pull qwen2.5:1.5b
# Point the app at it (already the default in .env.example):
#   OLLAMA_BASE_URL=http://localhost:11434
#   OLLAMA_MODEL=qwen2.5:1.5b
# Disable the LLM entirely (rule-based only) by setting OLLAMA_MODEL= (empty)
```

> Model weights are **not** included in this repository or the ZIP. Pull them yourself.

## Kubernetes / kind setup

```bash
docker build -f docker/Dockerfile -t aiops-incident-log-analyzer:local .
kind create cluster --name aiops
kind load docker-image aiops-incident-log-analyzer:local --name aiops
```

## kubectl setup

```bash
kubectl cluster-info --context kind-aiops
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/deployment.yaml
# Optional secret (notifications/admin token):
#   kubectl -n aiops create secret generic aiops-analyzer-secret \
#     --from-literal=NOTIFICATION_WEBHOOK_URL='https://your-webhook' \
#     --from-literal=ADMIN_TOKEN='a-long-random-token'
```

## Helm setup

```bash
helm install aiops helm/ --namespace aiops --create-namespace \
  --set image.repository=aiops-incident-log-analyzer --set image.tag=local
helm upgrade aiops helm/ --namespace aiops --set replicaCount=3
helm uninstall aiops --namespace aiops
```

## Prometheus setup

```bash
docker run -d --name prometheus -p 9090:9090 \
  -v "$(pwd)/observability/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
  -v "$(pwd)/observability/alerts.yml:/etc/prometheus/alerts.yml:ro" \
  prom/prometheus:v2.54.1
# In kind, port-forward the app so Prometheus can scrape it, or deploy Prometheus
# in-cluster with the pod-annotation discovery already configured.
```

## Grafana setup

```bash
docker run -d --name grafana -p 3000:3000 \
  -v "$(pwd)/observability/grafana/provisioning:/etc/grafana/provisioning:ro" \
  -v "$(pwd)/observability/grafana/dashboards:/var/lib/grafana/dashboards:ro" \
  grafana/grafana-oss:11.2.0
# Login admin/admin. The "AIOps Incident & Log Analyzer" dashboard is auto-provisioned.
```

## Local application startup

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
# UI: http://localhost:8000/ui   API docs: http://localhost:8000/docs
```

## Failure injection (exact commands)

Against a running service:

```bash
python scripts/inject_errors.py database-timeout
python scripts/inject_errors.py memory-pressure
python scripts/inject_errors.py http-500-burst
python scripts/inject_errors.py dependency-timeout
python scripts/inject_errors.py invalid-configuration
python scripts/inject_errors.py list          # show scenarios
python scripts/inject_errors.py clear         # stop the active fault
```

Offline (no server) — writes a deterministic log file for the analyzer:

```bash
python scripts/inject_errors.py http-500-burst --offline --out logs/app.log
```

All scenarios are simulations. Nothing destructive is executed.

## Analyzer execution (exact commands)

```bash
python scripts/run_analyzer.py
python scripts/run_analyzer.py --window-size 500 --ground-truth database_timeout
python scripts/run_analyzer.py --json
# Or via the API:
curl -X POST http://localhost:8000/analyze -H 'Content-Type: application/json' -d '{}'
```

## Report viewing (exact commands / location)

```bash
ls incidents/generated/
cat incidents/generated/<incident-id>.md
cat incidents/generated/<incident-id>.json
curl http://localhost:8000/incidents
curl http://localhost:8000/incidents/<incident-id>?format=md
```

## Grafana dashboard (exact access)

Open `http://localhost:3000` (admin/admin), Dashboards → **AIOps** folder →
**AIOps Incident & Log Analyzer**. Panels: Request Rate, Error Rate, Latency (p50/p95/p99),
Incidents Created, Total Incidents, Analyzer Runs, Log Lines Collected/Parsed/Noise,
Recent Incident Activity.

## Kubernetes deployment (exact commands)

```bash
docker build -f docker/Dockerfile -t aiops-incident-log-analyzer:local .
kind create cluster --name aiops
kind load docker-image aiops-incident-log-analyzer:local --name aiops
kubectl apply -f k8s/
kubectl -n aiops get pods -w
kubectl -n aiops get svc aiops-analyzer
kubectl -n aiops port-forward svc/aiops-analyzer 8000:80
kubectl -n aiops logs deploy/aiops-analyzer --tail=50
```

> `k8s/probes.yaml` is a strategic-merge **patch** (not a standalone resource). Apply it
> with `kubectl -n aiops patch deployment aiops-analyzer --patch-file k8s/probes.yaml`.
> `k8s/secret.example.yaml` is a template — copy and fill it; never commit a real Secret.

## Helm deployment (exact commands)

```bash
helm lint helm/
helm install aiops helm/ -n aiops --create-namespace --set image.tag=local
helm upgrade aiops helm/ -n aiops --set replicaCount=3
helm test aiops -n aiops
helm uninstall aiops -n aiops
```

## Scaling (exact commands)

```bash
kubectl -n aiops scale deployment aiops-analyzer --replicas=3
kubectl -n aiops get pods -l app.kubernetes.io/name=aiops-analyzer
# Verify the service still balances:
kubectl -n aiops port-forward svc/aiops-analyzer 8000:80 &
curl -s http://localhost:8000/health/ready
```

## Testing (exact commands)

```bash
ruff check .
pytest -q
python scripts/run_demo.py                 # offline end-to-end demo
python scripts/run_evaluation.py --window-size 200 --out evaluation_report.md
```

## Troubleshooting

- **Docker unavailable** — install Docker or use the local `uvicorn` mode; kind needs Docker.
- **kind unavailable** — `kind` requires Docker running; the local mode still works without it.
- **kubectl issues** — check `kubectl config current-context` is `kind-aiops`.
- **Ollama unavailable** — the app logs `llm unavailable`; rule-based analysis continues.
  Set `OLLAMA_MODEL=` to disable the LLM stage cleanly.
- **Model missing** — `ollama pull <model>`; see "Local model setup".
- **Memory limitations** — use a smaller model (`llama3.2:1b`) or disable the LLM.
- **Port conflicts** — change `APP_PORT`, or the `-p` host port for Prometheus/Grafana.
- **Malformed logs** — handled by design; they are counted as noise, not fatal.
- **Analyzer errors** — each stage is isolated; check `/health` and the report's `errors`.
- **Grafana problems** — confirm the Prometheus datasource URL matches your deployment.
- **Prometheus problems** — confirm the scrape target matches the app's `/metrics`.
- **Pod failures** — `kubectl -n aiops describe pod <name>` and `kubectl -n aiops get events`.

## Security

See the security review in `FINAL_VERIFICATION_REPORT.md`. Summary: non-root containers,
dropped capabilities, resource limits, ClusterIP-only service, secret redaction in logs,
strict LLM output validation, no command execution anywhere, security headers, and an
optional admin token. **This project is not production-hardened and makes no claim of
complete security.** Remaining risks are documented.

## Limitations

- Multi-replica log/incident aggregation uses `emptyDir` (per-pod), not a shared store.
- Rule-based detection is tuned to the five known scenarios; it is not general-purpose RCA.
- The LLM is advisory only and its accuracy varies by model and machine.
- No authentication on the UI/read APIs by default; admin routes are optionally token-gated.
- OpenTelemetry tracing is not implemented (optional per spec).

## Cleanup (exact commands)

```bash
docker compose -f docker/compose.yaml down -v      # if using compose
kind delete cluster --name aiops                    # delete the kind cluster
docker rmi aiops-incident-log-analyzer:local        # remove the image
rm -rf incidents/generated/* logs/*                 # clear local artifacts
```

## GitHub

Create a repository manually on github.com (New repository), then:

```powershell
git config --global user.name "YOUR_NAME"        # replace YOUR_NAME
git config --global user.email "YOUR_EMAIL"      # replace YOUR_EMAIL

git init
git add .
git commit -m "Initial AIOps Incident and Log Analyzer implementation"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git   # replace both
git push -u origin main
```

Replace `YOUR_NAME`, `YOUR_EMAIL`, `YOUR_USERNAME`, and `YOUR_REPOSITORY` with your own
values. Nothing is pushed automatically and no credentials are requested by this project.

## License

MIT — see [`LICENSE`](LICENSE).
