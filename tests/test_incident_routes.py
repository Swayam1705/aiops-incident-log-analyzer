"""API route tests against the real FastAPI app (in-process, no external services)."""

from __future__ import annotations

import time


def test_health_live_and_ready(client):
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200


def test_metrics_endpoint_exposes_real_families(client):
    body = client.get("/metrics").text
    for metric in [
        "http_requests_total",
        "http_request_duration_seconds",
        "app_errors_total",
        "incidents_total",
    ]:
        assert metric in body


def test_metrics_change_with_traffic(client):
    before = client.get("/metrics").text
    for _ in range(5):
        client.get("/api/products")
    after = client.get("/metrics").text
    # request counter for /api/products must have moved
    assert after != before
    assert 'path="/api/products"' in after


def test_analyze_normal_traffic_creates_no_incident(client, runtime):
    # No fault injected and no error logs -> no incident.
    result = client.post("/analyze", json={}).json()
    assert result["incident_created"] is False


def test_full_incident_flow(client):
    client.post("/admin/faults", json={"scenario": "http-500-burst", "duration_seconds": 3})
    time.sleep(0.6)  # let the simulator emit failure logs
    result = client.post("/analyze", json={}).json()
    assert result["incident_created"] is True
    assert result["suspected_root_cause"] == "http_500_burst"
    incident_id = result["incident"]["incident_id"]

    assert client.get(f"/incidents/{incident_id}").status_code == 200
    assert client.get(f"/incidents/{incident_id}?format=md").status_code == 200

    validated = client.post(
        f"/incidents/{incident_id}/validate", json={"status": "confirmed", "validated_by": "qa"}
    ).json()
    assert validated["validation"]["status"] == "confirmed"
    client.post("/admin/faults/clear")


def test_invalid_scenario_rejected(client):
    response = client.post("/admin/faults", json={"scenario": "rm-rf-everything"})
    assert response.status_code == 400


def test_invalid_validation_status_rejected(client):
    client.post("/admin/faults", json={"scenario": "database-timeout", "duration_seconds": 2})
    time.sleep(0.5)
    incident_id = client.post("/analyze", json={}).json()["incident"]["incident_id"]
    response = client.post(f"/incidents/{incident_id}/validate", json={"status": "not-a-status"})
    assert response.status_code == 400
    client.post("/admin/faults/clear")


def test_missing_incident_404(client):
    assert client.get("/incidents/does-not-exist").status_code == 404


def test_evaluation_endpoint(client):
    result = client.post("/evaluation/run?window_size=120").json()
    assert result["rule_accuracy"].endswith("/5")
    assert result["false_positives"] == 0


def test_logs_endpoint_returns_records(client):
    result = client.get("/logs?limit=10").json()
    assert "records" in result
    assert isinstance(result["records"], list)


def test_runbooks_endpoint(client):
    result = client.get("/runbooks").json()
    assert result["advisory"] is True
    assert result["auto_executed"] is False
    assert result["count"] >= 5


def test_security_headers_present(client):
    headers = client.get("/health/live").headers
    assert headers.get("x-content-type-options") == "nosniff"
    assert "content-security-policy" in {k.lower() for k in headers}


def test_config_endpoint_hides_secrets(client):
    body = client.get("/config").json()
    blob = str(body).lower()
    assert "admin_token" not in blob
    assert "webhook_url" not in blob


def test_ui_pages_render(client):
    for path in ["/ui/", "/ui/incidents", "/ui/evaluation", "/ui/health"]:
        assert client.get(path).status_code == 200


def test_liveness_fails_under_liveness_fault(client):
    client.post("/admin/faults", json={"scenario": "liveness-failure", "duration_seconds": 3})
    assert client.get("/health/live").status_code == 503
    assert client.get("/health/ready").status_code == 503
    client.post("/admin/faults/clear")
    assert client.get("/health/live").status_code == 200


def test_readiness_fails_under_invalid_configuration(client):
    client.post("/admin/faults", json={"scenario": "invalid-configuration", "duration_seconds": 3})
    assert client.get("/health/ready").status_code == 503
    # liveness still passes: k8s would remove it from endpoints but not restart
    assert client.get("/health/live").status_code == 200
    client.post("/admin/faults/clear")
