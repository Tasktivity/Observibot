from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from observibot.cli import app

runner = CliRunner()


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "observibot" in result.output.lower()


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_cli_init_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "obs.yaml"
    result = runner.invoke(app, ["init", "--target", str(target)])
    assert result.exit_code == 0
    assert target.exists()


def test_cli_init_refuses_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "obs.yaml"
    target.write_text("existing: 1\n")
    result = runner.invoke(app, ["init", "--target", str(target)])
    assert result.exit_code != 0


def test_cli_init_force_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "obs.yaml"
    target.write_text("existing: 1\n")
    result = runner.invoke(app, ["init", "--target", str(target), "--force"])
    assert result.exit_code == 0
    assert "existing: 1" not in target.read_text()


def test_cli_health_with_no_connectors(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text("llm: {provider: mock}\nconnectors: []\n")
    result = runner.invoke(app, ["--config", str(cfg), "health"])
    assert result.exit_code == 0
    assert "no connectors" in result.output.lower() or "getting started" in result.output.lower()


def test_cli_discover_with_no_connectors(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text("llm: {provider: mock}\nconnectors: []\n")
    result = runner.invoke(app, ["--config", str(cfg), "discover"])
    assert result.exit_code == 0
    assert "no connectors" in result.output.lower() or "getting started" in result.output.lower()


def test_cli_missing_config_file_reports_error(tmp_path: Path) -> None:
    cfg = tmp_path / "missing.yaml"
    result = runner.invoke(app, ["--config", str(cfg), "health"])
    assert result.exit_code != 0
    assert "error" in result.output.lower()


def test_cli_show_model_no_snapshot(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "llm: {provider: mock}\nconnectors: []\nstore:\n  path: %s\n"
        % (tmp_path / "store.db")
    )
    result = runner.invoke(app, ["--config", str(cfg), "show-model"])
    assert result.exit_code == 0
    assert "no system snapshot" in result.output.lower() or "run" in result.output.lower()


def test_cli_status_empty(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "llm: {provider: mock}\nconnectors: []\nstore:\n  path: %s\n"
        % (tmp_path / "store.db")
    )
    result = runner.invoke(app, ["--config", str(cfg), "status"])
    assert result.exit_code == 0


def test_cli_root_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_cli_analyze_no_snapshot(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "llm: {provider: mock}\nconnectors: []\nstore:\n  path: %s\n"
        % (tmp_path / "store.db")
    )
    result = runner.invoke(app, ["--config", str(cfg), "analyze"])
    assert result.exit_code == 0
    assert "no system snapshot" in result.output.lower()


def test_cli_analyze_no_metrics(tmp_path: Path) -> None:
    """With a snapshot but zero metrics, analyze must exit cleanly with a hint."""
    import asyncio

    from observibot.core.models import SystemModel, TableInfo
    from observibot.core.store import Store

    db_path = tmp_path / "store.db"
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        f"llm: {{provider: mock}}\nconnectors: []\nstore:\n  path: {db_path}\n"
    )

    async def _seed() -> None:
        async with Store(db_path) as store:
            model = SystemModel(tables=[TableInfo(name="t")])
            model.compute_fingerprint()
            await store.save_system_snapshot(model)

    asyncio.run(_seed())
    result = runner.invoke(app, ["--config", str(cfg), "analyze"])
    assert result.exit_code == 0
    assert "no metrics" in result.output.lower()


def test_cli_test_alert_no_channels(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "llm: {provider: mock}\nconnectors: []\nstore:\n  path: %s\n"
        % (tmp_path / "store.db")
    )
    result = runner.invoke(app, ["--config", str(cfg), "test-alert"])
    assert result.exit_code == 0
    assert "no alert channels" in result.output.lower()


def test_cli_test_alert_with_webhook_channel(
    tmp_path: Path, monkeypatch
) -> None:
    """A configured webhook channel should receive the synthetic insight."""
    import httpx

    captured: dict = {}

    class FakeResp:
        status_code = 200
        text = "ok"

    class FakeClient:
        def __init__(self, *a, **kw): ...
        async def request(self, method, url, json, headers):
            captured["url"] = url
            captured["payload"] = json
            return FakeResp()
        async def aclose(self): ...

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "llm:\n  provider: mock\n"
        "connectors: []\n"
        "store:\n  path: %s\n"
        "alerting:\n"
        "  channels:\n"
        "    - type: webhook\n"
        "      url: https://example.com/hook\n"
        "      severity_filter: [info, warning, critical]\n"
        % (tmp_path / "store.db")
    )
    result = runner.invoke(app, ["--config", str(cfg), "test-alert"])
    assert result.exit_code == 0
    # Output table should report success
    assert "yes" in result.output.lower()
    assert captured["url"] == "https://example.com/hook"
    assert captured["payload"]["title"] == "Observibot test alert"
