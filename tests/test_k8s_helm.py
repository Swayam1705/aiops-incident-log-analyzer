"""Kubernetes/Helm artifact tests.

These validate the YAML parses and (when the helm binary is present) that the chart
lints and renders. They do not require a cluster, so they run on GitHub-hosted CI.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
K8S_DIR = ROOT / "k8s"
HELM_DIR = ROOT / "helm"


@pytest.mark.parametrize(
    "name",
    [
        "namespace.yaml", "configmap.yaml", "service.yaml",
        "deployment.yaml", "secret.example.yaml", "probes.yaml",
    ],
)
def test_k8s_manifest_parses(name):
    docs = list(yaml.safe_load_all((K8S_DIR / name).read_text()))
    assert docs and docs[0] is not None


def test_deployment_has_probes_and_resources():
    doc = yaml.safe_load((K8S_DIR / "deployment.yaml").read_text())
    container = doc["spec"]["template"]["spec"]["containers"][0]
    assert "livenessProbe" in container
    assert "readinessProbe" in container
    assert "resources" in container
    assert doc["spec"]["replicas"] >= 2  # multi-replica requirement
    assert container["securityContext"]["allowPrivilegeEscalation"] is False


def test_deployment_runs_as_non_root():
    doc = yaml.safe_load((K8S_DIR / "deployment.yaml").read_text())
    pod_sec = doc["spec"]["template"]["spec"]["securityContext"]
    assert pod_sec["runAsNonRoot"] is True


def test_service_is_cluster_ip():
    doc = yaml.safe_load((K8S_DIR / "service.yaml").read_text())
    assert doc["spec"]["type"] == "ClusterIP"


def test_no_real_secret_values_committed():
    text = (K8S_DIR / "secret.example.yaml").read_text()
    assert "REPLACE_ME" in text  # placeholders only


def test_helm_chart_files_present():
    assert (HELM_DIR / "Chart.yaml").exists()
    assert (HELM_DIR / "values.yaml").exists()
    assert (HELM_DIR / "templates" / "deployment.yaml").exists()


HELM = shutil.which("helm")


@pytest.mark.skipif(HELM is None, reason="helm binary not installed")
def test_helm_lint():
    result = subprocess.run([HELM, "lint", str(HELM_DIR)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(HELM is None, reason="helm binary not installed")
def test_helm_template_renders():
    result = subprocess.run(
        [HELM, "template", "t", str(HELM_DIR), "--namespace", "aiops", "--set", "secret.create=true"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    kinds = [d.get("kind") for d in yaml.safe_load_all(result.stdout) if d]
    for expected in ["Deployment", "Service", "ConfigMap", "Secret"]:
        assert expected in kinds
