"""Metrics tests: counters/gauges/histogram behave and render valid Prometheus text."""

from __future__ import annotations

from app.metrics import MetricsRegistry


def test_counter_increments_and_renders():
    reg = MetricsRegistry()
    reg.request_total.inc(method="GET", path="/x", status="200", outcome="ok")
    reg.request_total.inc(method="GET", path="/x", status="200", outcome="ok")
    out = reg.render()
    assert 'http_requests_total{method="GET",outcome="ok",path="/x",status="200"} 2' in out


def test_counter_rejects_negative():
    reg = MetricsRegistry()
    try:
        reg.request_total.inc(-1, method="GET", path="/x", status="200", outcome="ok")
        assert False
    except ValueError:
        pass


def test_histogram_buckets_and_sum():
    reg = MetricsRegistry()
    reg.request_latency.observe(0.05, method="GET", path="/x", outcome="ok")
    reg.request_latency.observe(2.0, method="GET", path="/x", outcome="ok")
    out = reg.render()
    assert "http_request_duration_seconds_bucket" in out
    assert "http_request_duration_seconds_count" in out
    assert reg.request_latency.count(method="GET", path="/x", outcome="ok") == 2


def test_gauge_set_and_render():
    reg = MetricsRegistry()
    reg.log_lines_collected.set(42)
    assert "analyzer_log_lines_collected 42" in reg.render()


def test_incidents_metric_labels():
    reg = MetricsRegistry()
    reg.incidents_total.inc(severity="high", root_cause="database_timeout", analysis_source="rule-based")
    out = reg.render()
    expected = (
        'incidents_total{analysis_source="rule-based",'
        'root_cause="database_timeout",severity="high"} 1'
    )
    assert expected in out


def test_no_constant_fake_metrics():
    # A fresh registry should not pre-populate request/error counters with fake values.
    reg = MetricsRegistry()
    out = reg.render()
    assert "http_requests_total{" not in out  # no series until real traffic occurs


def test_metric_help_and_type_present():
    reg = MetricsRegistry()
    reg.request_total.inc(method="GET", path="/x", status="200", outcome="ok")
    out = reg.render()
    assert "# TYPE http_requests_total counter" in out
    assert "# HELP http_requests_total" in out
