"""Configuration tests: env parsing, defaults, safe fallbacks, secret handling."""

from __future__ import annotations

from pathlib import Path

from app.config import load_settings


def test_defaults_applied():
    settings = load_settings(env={}, base_dir="/tmp")
    assert settings.log_window_size == 2000
    assert settings.analyze_min_error_lines == 5
    assert settings.ollama_base_url.endswith("11434")


def test_invalid_int_falls_back():
    settings = load_settings(env={"LOG_WINDOW_SIZE": "not-a-number"}, base_dir="/tmp")
    assert settings.log_window_size == 2000


def test_bool_parsing():
    assert load_settings(env={"LLM_ENABLED": "false"}, base_dir="/tmp").llm_enabled is False
    assert load_settings(env={"LLM_ENABLED": "yes"}, base_dir="/tmp").llm_enabled is True
    assert load_settings(env={"LLM_ENABLED": "garbage"}, base_dir="/tmp").llm_enabled is True


def test_llm_requested_requires_model():
    settings = load_settings(env={"LLM_ENABLED": "true", "OLLAMA_MODEL": ""}, base_dir="/tmp")
    assert settings.llm_requested is False


def test_public_dict_has_no_secrets():
    env = {"ADMIN_TOKEN": "secret123", "NOTIFICATION_WEBHOOK_URL": "http://x?t=abc"}
    settings = load_settings(env=env, base_dir="/tmp")
    public = settings.to_public_dict()
    blob = str(public).lower()
    assert "secret123" not in blob
    assert "admin_token" not in blob
    # webhook URL itself is not exposed, only whether it is configured
    assert "notification_enabled" in public


def test_log_files_includes_primary(tmp_path: Path):
    log = tmp_path / "logs" / "app.log"
    log.parent.mkdir(parents=True)
    log.write_text("x")
    settings = load_settings(
        env={"LOG_FILE": str(log), "COLLECTOR_FILE_GLOB": str(tmp_path / "logs" / "*.log")},
        base_dir=tmp_path,
    )
    files = settings.log_files()
    assert settings.resolve(log) in files


def test_resolve_relative_against_base(tmp_path: Path):
    settings = load_settings(env={"INCIDENTS_DIR": "incidents/generated"}, base_dir=tmp_path)
    assert settings.resolve(settings.incidents_dir) == (tmp_path / "incidents" / "generated").resolve()
