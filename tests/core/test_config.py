from __future__ import annotations

from pathlib import Path

import pytest

from observibot.core.config import ConfigError, load_config


def test_default_config_when_nothing_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OBSERVIBOT_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    cfg = load_config()
    # With no file anywhere, we get a default (mock) config instead of crashing.
    assert cfg.llm.provider == "mock"
    assert cfg.connectors == []
    assert cfg.source_path is None


def test_explicit_missing_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(ConfigError):
        load_config(missing)


def test_load_minimal_config(tmp_path: Path) -> None:
    cfg_path = tmp_path / "c.yaml"
    cfg_path.write_text(
        "llm:\n  provider: mock\n  model: mock-model\nconnectors: []\n"
    )
    cfg = load_config(cfg_path)
    assert cfg.llm.provider == "mock"
    assert cfg.connectors == []
    assert cfg.source_path == cfg_path


def test_env_var_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_CONN", "postgres://u:p@h/d")
    cfg_path = tmp_path / "c.yaml"
    cfg_path.write_text(
        "llm: {provider: mock}\n"
        "connectors:\n"
        "  - name: db\n"
        "    type: postgresql\n"
        "    connection_string: ${TEST_CONN}\n"
    )
    cfg = load_config(cfg_path)
    assert cfg.connectors[0].options["connection_string"] == "postgres://u:p@h/d"


def test_env_var_missing_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_VAR", raising=False)
    cfg_path = tmp_path / "c.yaml"
    cfg_path.write_text(
        "llm: {provider: mock}\n"
        "connectors:\n"
        "  - name: db\n"
        "    type: postgresql\n"
        "    connection_string: ${MISSING_VAR}\n"
    )
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_env_var_default_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_VAR", raising=False)
    cfg_path = tmp_path / "c.yaml"
    cfg_path.write_text(
        "llm:\n  provider: mock\n  api_key: ${MISSING_VAR:-fallback-key}\n"
        "connectors: []\n"
    )
    cfg = load_config(cfg_path)
    assert cfg.llm.api_key == "fallback-key"


def test_invalid_yaml(tmp_path: Path) -> None:
    cfg_path = tmp_path / "bad.yaml"
    cfg_path.write_text("llm: [unclosed\n  oops: )(\n")
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_non_mapping_root_raises(tmp_path: Path) -> None:
    cfg_path = tmp_path / "list.yaml"
    cfg_path.write_text("- item1\n- item2\n")
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_connector_missing_name_raises(tmp_path: Path) -> None:
    cfg_path = tmp_path / "c.yaml"
    cfg_path.write_text(
        "connectors:\n  - type: postgresql\n    connection_string: x\n"
    )
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_observibot_config_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "env.yaml"
    cfg_path.write_text("llm: {provider: mock}\nconnectors: []\n")
    monkeypatch.setenv("OBSERVIBOT_CONFIG", str(cfg_path))
    cfg = load_config()
    assert cfg.source_path == cfg_path


def test_alerting_config_defaults(tmp_path: Path) -> None:
    cfg_path = tmp_path / "c.yaml"
    cfg_path.write_text(
        "alerting:\n"
        "  channels:\n"
        "    - type: slack\n"
        "      webhook_url: https://example.com/hook\n"
        "      severity_filter: [critical]\n"
    )
    cfg = load_config(cfg_path)
    assert cfg.alerting.channels[0].type == "slack"
    assert cfg.alerting.channels[0].options["webhook_url"] == "https://example.com/hook"
    assert cfg.alerting.channels[0].severity_filter == ["critical"]
