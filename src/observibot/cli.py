"""Observibot command-line interface."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import time
from pathlib import Path

from dotenv import load_dotenv

# Load .env file before anything else touches os.environ
load_dotenv()

from datetime import UTC, datetime, timedelta

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from observibot import __version__
from observibot.agent.analyzer import Analyzer, summarize_system
from observibot.agent.llm_provider import LLMError, build_provider
from observibot.agent.semantic_modeler import SemanticModeler
from observibot.alerting.base import AlertManager
from observibot.alerting.webhook import build_channels
from observibot.connectors import UnknownConnectorError, get_connector
from observibot.connectors.base import BaseConnector
from observibot.core.anomaly import build_detector_from_config
from observibot.core.config import (
    ConfigError,
    ObservibotConfig,
    find_env_var_references,
    load_config,
    write_example_config,
)
from observibot.core.discovery import DiscoveryEngine, diff_models
from observibot.core.models import Insight
from observibot.core.monitor import LockfileError, build_monitor_loop
from observibot.core.store import Store

app = typer.Typer(
    help="Observibot — autonomous AI SRE agent for everyone on PaaS stacks.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


# ---------- helpers ----------


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _safe_load_config(path: Path | None) -> ObservibotConfig:
    try:
        return load_config(path)
    except ConfigError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        message = str(exc)
        if "environment variable" in message.lower():
            console.print(
                "[dim]Hint: set the variable in your shell or add it to "
                "a .env file loaded by your environment.[/dim]"
            )
        raise typer.Exit(code=2) from exc


def _instantiate_connectors(cfg: ObservibotConfig) -> list[BaseConnector]:
    connectors: list[BaseConnector] = []
    for c in cfg.connectors:
        try:
            connectors.append(get_connector(c.name, c.type, c.options))
        except (UnknownConnectorError, ValueError) as exc:
            console.print(f"[yellow]Skipping connector {c.name}:[/yellow] {exc}")
    if cfg.github.enabled and cfg.github.token:
        try:
            from observibot.connectors.github import GitHubConnector
            gh = GitHubConnector(
                name="github",
                config={
                    "token": cfg.github.token,
                    "repo": cfg.github.repo,
                    "branch": cfg.github.branch,
                    "poll_interval_seconds": cfg.github.poll_interval_seconds,
                    "local_clone_path": cfg.github.local_clone_path,
                    "cloud_extraction": cfg.github.cloud_extraction,
                },
            )
            connectors.append(gh)
        except Exception as exc:
            console.print(f"[yellow]Skipping GitHub connector:[/yellow] {exc}")
    return connectors


async def _close_connectors(connectors: list[BaseConnector]) -> None:
    for c in connectors:
        with contextlib.suppress(Exception):
            await c.close()


def _no_connectors_message() -> None:
    console.print(
        Panel.fit(
            "[bold]No connectors configured.[/bold]\n\n"
            "Run [cyan]observibot init[/cyan] to create a starter config, then\n"
            "edit [cyan]config/observibot.yaml[/cyan] and add at least one connector\n"
            "(supabase, postgresql, or railway).",
            title="Getting started",
            border_style="cyan",
        )
    )


# ---------- commands ----------


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"observibot {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    config: Path | None = typer.Option(
        None, "--config", "-c", help="Path to observibot.yaml"
    ),
    log_level: str = typer.Option("INFO", "--log-level", "-l", help="Log level"),
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Observibot — set --config, --log-level, or --version globally."""
    _setup_logging(log_level)
    if config is not None:
        import os
        os.environ["OBSERVIBOT_CONFIG"] = str(config)


@app.command()
def version() -> None:
    """Print Observibot version."""
    console.print(f"observibot {__version__}")


@app.command()
def init(
    target: Path = typer.Option(
        Path("config/observibot.yaml"),
        "--target",
        "-t",
        help="Where to write the new config file.",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite if exists"),
) -> None:
    """Create a starter config file with example settings."""
    if target.exists() and not force:
        console.print(
            f"[yellow]{target} already exists.[/yellow] Use --force to overwrite."
        )
        raise typer.Exit(code=1)
    try:
        path = write_example_config(target)
    except OSError as exc:
        console.print(f"[red]Failed to write config:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(
        Panel.fit(
            f"[green]Created[/green] {path}\n\n"
            "Next steps:\n"
            "  1. Edit the file and set the environment variables it references.\n"
            "  2. Run [cyan]observibot health[/cyan] to verify connectors.\n"
            "  3. Run [cyan]observibot discover[/cyan] for a first scan.\n"
            "  4. Run [cyan]observibot run[/cyan] to start continuous monitoring.",
            title="observibot init",
            border_style="green",
        )
    )
    # Surface the env vars the new config expects so users don't have to
    # open the file to find out.
    import os
    refs = find_env_var_references(path.read_text(encoding="utf-8"))
    if refs:
        env_table = Table(title="Environment variables referenced", show_lines=False)
        env_table.add_column("Variable")
        env_table.add_column("Default")
        env_table.add_column("Status")
        seen: set[str] = set()
        for var, default in refs:
            if var in seen:
                continue
            seen.add(var)
            is_set = var in os.environ
            status = "[green]set[/green]" if is_set else "[yellow]unset[/yellow]"
            env_table.add_row(var, default or "-", status)
        console.print(env_table)


@app.command()
def discover(
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Run a one-shot discovery cycle and print a summary."""
    cfg = _safe_load_config(config)
    if not cfg.connectors:
        _no_connectors_message()
        return
    connectors = _instantiate_connectors(cfg)
    if not connectors:
        _no_connectors_message()
        return

    async def _run() -> None:
        async with Store(cfg.store.path) as store:
            engine = DiscoveryEngine(connectors)
            previous = await store.get_latest_system_snapshot()
            # connect() every capable connector once before running discovery.
            for c in connectors:
                try:
                    await c.connect()
                except Exception as exc:
                    console.print(
                        f"[yellow]{c.name} failed to connect: {exc}[/yellow]"
                    )
            with console.status("Discovering systems..."):
                model = await engine.run()
            await store.save_system_snapshot(model)
            await _close_connectors(connectors)

            table = Table(title="Discovered Systems", show_lines=False)
            table.add_column("Connector")
            table.add_column("Tables", justify="right")
            table.add_column("Relationships", justify="right")
            table.add_column("Services", justify="right")
            table.add_column("Errors", justify="right")
            for frag in model.fragments:
                table.add_row(
                    f"{frag.connector_name} ({frag.connector_type})",
                    str(len(frag.tables)),
                    str(len(frag.relationships)),
                    str(len(frag.services)),
                    str(len(frag.errors)),
                )
            console.print(table)
            console.print(
                f"\n[bold]Fingerprint:[/bold] {model.fingerprint}\n"
                f"[bold]Tables:[/bold] {len(model.tables)}  "
                f"[bold]Relationships:[/bold] {len(model.relationships)}  "
                f"[bold]Services:[/bold] {len(model.services)}"
            )
            for frag in model.fragments:
                if frag.errors:
                    console.print(f"[yellow]{frag.connector_name} errors:[/yellow]")
                    for err in frag.errors:
                        console.print(f"  - {err}")

            if previous is not None:
                diff = diff_models(previous, model)
                console.print(
                    Panel(
                        diff.to_human_readable(),
                        title="Changes since last discovery",
                        border_style="cyan" if diff.has_changes else "green",
                    )
                )

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted.[/yellow]")


@app.command()
def health(config: Path | None = typer.Option(None, "--config", "-c")) -> None:
    """Check connector health and report status."""
    cfg = _safe_load_config(config)
    if not cfg.connectors:
        _no_connectors_message()
        return
    connectors = _instantiate_connectors(cfg)
    if not connectors:
        _no_connectors_message()
        return

    async def _run() -> None:
        results = []
        for c in connectors:
            try:
                results.append(await c.health_check())
            except Exception as exc:
                from observibot.core.models import HealthStatus
                results.append(
                    HealthStatus(connector_name=c.name, healthy=False, message=str(exc))
                )
        await _close_connectors(connectors)
        table = Table(title="Connector Health")
        table.add_column("Connector")
        table.add_column("Healthy")
        table.add_column("Latency (ms)", justify="right")
        table.add_column("Message")
        for r in results:
            color = "green" if r.healthy else "red"
            table.add_row(
                r.connector_name,
                f"[{color}]{'YES' if r.healthy else 'NO'}[/{color}]",
                f"{r.latency_ms:.1f}" if r.latency_ms is not None else "-",
                r.message or "",
            )
        console.print(table)

    asyncio.run(_run())


@app.command("show-model")
def show_model(config: Path | None = typer.Option(None, "--config", "-c")) -> None:
    """Show the most recent SystemModel snapshot."""
    cfg = _safe_load_config(config)

    async def _run() -> None:
        async with Store(cfg.store.path) as store:
            model = await store.get_latest_system_snapshot()
        if model is None:
            console.print("[yellow]No system snapshot yet. Run 'observibot discover'.[/yellow]")
            return
        console.print(Panel(summarize_system(model), title="System Model", border_style="cyan"))
        console.print(f"[bold]Fingerprint:[/bold] {model.fingerprint}")
        console.print(f"[bold]Created:[/bold] {model.created_at.isoformat()}")

    asyncio.run(_run())


@app.command()
def onboard(config: Path | None = typer.Option(None, "--config", "-c")) -> None:
    """Run the semantic modeler against the latest snapshot."""
    cfg = _safe_load_config(config)

    async def _run() -> None:
        async with Store(cfg.store.path) as store:
            model = await store.get_latest_system_snapshot()
            if model is None:
                console.print(
                    "[yellow]No snapshot found. Run 'observibot discover' first.[/yellow]"
                )
                return
            try:
                provider = build_provider(
                    cfg.llm.provider,
                    cfg.llm.model,
                    cfg.llm.api_key,
                    max_tokens_per_cycle=cfg.llm.max_tokens_per_cycle,
                    temperature=cfg.llm.temperature,
                    daily_token_budget=cfg.llm.daily_token_budget,
                )
            except LLMError as exc:
                console.print(f"[red]LLM provider error:[/red] {exc}")
                return
            analyzer = Analyzer(provider=provider, store=store)
            modeler = SemanticModeler(analyzer=analyzer, store=store)
            with console.status("Running semantic modeler..."):
                result = await modeler.run(model)
            app_type = result.get("app_type", "?")
            summary = result.get("summary") or result.get("app_description", "")
            critical = result.get("critical_tables") or []
            metrics = result.get("key_metrics") or []
            risks = result.get("risks") or []
            console.print(
                Panel(
                    f"[bold]App type:[/bold] {app_type}\n"
                    f"[bold]Summary:[/bold] {summary}\n"
                    f"[bold]Critical:[/bold] {', '.join(critical)}\n"
                    f"[bold]Key metrics:[/bold] {', '.join(metrics)}\n"
                    f"[bold]Risks:[/bold] {', '.join(risks)}",
                    title="Business Context",
                    border_style="green",
                )
            )

    asyncio.run(_run())


@app.command()
def run(config: Path | None = typer.Option(None, "--config", "-c")) -> None:
    """Start the continuous monitor daemon."""
    cfg = _safe_load_config(config)
    if not cfg.connectors:
        _no_connectors_message()
        return
    connectors = _instantiate_connectors(cfg)
    if not connectors:
        _no_connectors_message()
        return

    async def _run() -> None:
        async with Store(cfg.store.path) as store:
            try:
                provider = build_provider(
                    cfg.llm.provider,
                    cfg.llm.model,
                    cfg.llm.api_key,
                    max_tokens_per_cycle=cfg.llm.max_tokens_per_cycle,
                    temperature=cfg.llm.temperature,
                    daily_token_budget=cfg.llm.daily_token_budget,
                )
            except LLMError as exc:
                console.print(f"[red]LLM provider error:[/red] {exc}")
                return
            analyzer = Analyzer(provider=provider, store=store)
            channels = build_channels(cfg.alerting.channels)
            alert_manager = AlertManager(
                channels=channels,
                max_alerts_per_hour=cfg.alerting.max_alerts_per_hour,
                cooldown_seconds=cfg.alerting.cooldown_seconds,
                aggregation_window_seconds=cfg.alerting.aggregation_window_seconds,
                aggregation_min_incidents=cfg.alerting.aggregation_min_incidents,
            )
            lockfile_path = Path(cfg.store.path).parent / "observibot.lock"
            loop = build_monitor_loop(
                config=cfg,
                connectors=connectors,
                store=store,
                analyzer=analyzer,
                alert_manager=alert_manager,
                lockfile_path=lockfile_path,
            )
            console.print(
                Panel.fit(
                    "[bold green]Observibot is now running.[/bold green]\n"
                    f"Connectors: {', '.join(c.name for c in connectors)}\n"
                    f"LLM provider: {cfg.llm.provider}\n"
                    f"Collection: every {cfg.monitor.collection_interval_seconds}s\n"
                    f"Analysis: every {cfg.monitor.analysis_interval_seconds}s\n"
                    f"Discovery: every {cfg.monitor.discovery_interval_seconds}s\n"
                    "Press Ctrl-C to stop.",
                    title="observibot run",
                    border_style="green",
                )
            )
            try:
                await loop.run_forever()
            except LockfileError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=3) from exc
            except KeyboardInterrupt:
                console.print("[yellow]Stopping...[/yellow]")

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("[yellow]Stopped.[/yellow]")


def _resolve_lockfile(config: Path | None) -> Path:
    """Return the lockfile path derived from the store config."""
    cfg = _safe_load_config(config)
    return Path(cfg.store.path).parent / "observibot.lock"


def _stop_daemon(lockfile: Path, timeout: int = 10) -> bool:
    """Send SIGTERM to the running daemon and wait for exit.

    Returns True if the process was stopped (or wasn't running).
    """
    if not lockfile.exists():
        console.print("[yellow]No running instance found (no lockfile).[/yellow]")
        return True
    try:
        pid = int(lockfile.read_text().strip() or "0")
    except ValueError:
        pid = 0
    if pid == 0:
        lockfile.unlink(missing_ok=True)
        console.print("[yellow]Stale lockfile removed.[/yellow]")
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        lockfile.unlink(missing_ok=True)
        console.print(f"[yellow]PID {pid} not running; stale lockfile removed.[/yellow]")
        return True
    except PermissionError:
        console.print(
            f"[red]PID {pid} exists but is owned by another user. "
            "Cannot send signal.[/red]"
        )
        return False

    console.print(f"Sending SIGTERM to PID {pid}...")
    os.kill(pid, signal.SIGTERM)

    for _ in range(timeout * 10):
        time.sleep(0.1)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            lockfile.unlink(missing_ok=True)
            console.print("[green]Stopped.[/green]")
            return True
    console.print(
        f"[red]PID {pid} did not exit within {timeout}s. "
        "You may need to kill it manually.[/red]"
    )
    return False


@app.command()
def stop(config: Path | None = typer.Option(None, "--config", "-c")) -> None:
    """Gracefully stop a running Observibot daemon."""
    lockfile = _resolve_lockfile(config)
    if not _stop_daemon(lockfile):
        raise typer.Exit(code=1)


@app.command()
def restart(config: Path | None = typer.Option(None, "--config", "-c")) -> None:
    """Restart the Observibot daemon (stop then run)."""
    lockfile = _resolve_lockfile(config)
    if lockfile.exists() and not _stop_daemon(lockfile):
        raise typer.Exit(code=1)
    # Delegate to the run command
    run(config=config)


@app.command()
def status(config: Path | None = typer.Option(None, "--config", "-c")) -> None:
    """Show recent metrics and insights."""
    cfg = _safe_load_config(config)

    async def _run() -> None:
        async with Store(cfg.store.path) as store:
            metrics = await store.get_metrics(limit=20)
            insights = await store.get_recent_insights(limit=10)
            llm_summary = await store.get_llm_usage_summary()

        if metrics:
            mtable = Table(title="Recent metrics")
            mtable.add_column("metric")
            mtable.add_column("value", justify="right")
            mtable.add_column("labels")
            mtable.add_column("collected_at")
            for m in metrics:
                mtable.add_row(
                    m.metric_name,
                    f"{m.value:g}",
                    ",".join(f"{k}={v}" for k, v in m.labels.items()),
                    m.collected_at.isoformat(),
                )
            console.print(mtable)
        else:
            console.print("[dim]No metrics yet.[/dim]")

        if insights:
            itable = Table(title="Recent insights")
            itable.add_column("severity")
            itable.add_column("title")
            itable.add_column("confidence", justify="right")
            itable.add_column("source")
            itable.add_column("created_at")
            for i in insights:
                color = {"critical": "red", "warning": "yellow", "info": "cyan"}.get(
                    i.severity, "white"
                )
                itable.add_row(
                    f"[{color}]{i.severity}[/{color}]",
                    i.display_title(),
                    f"{i.confidence:.2f}",
                    i.source,
                    i.created_at.isoformat(),
                )
            console.print(itable)
        else:
            console.print("[dim]No insights yet.[/dim]")

        console.print(
            f"[bold]LLM usage (24h):[/bold] {llm_summary['calls']} calls, "
            f"{llm_summary['total_tokens']} tokens, ${llm_summary['cost_usd']:.4f}"
        )

    asyncio.run(_run())


@app.command()
def ask(
    question: str = typer.Argument(..., help="Question to ask Observibot"),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Ask Observibot a question about your system."""
    cfg = _safe_load_config(config)

    async def _run() -> None:
        async with Store(cfg.store.path) as store:
            try:
                provider = build_provider(
                    cfg.llm.provider,
                    cfg.llm.model,
                    cfg.llm.api_key,
                    max_tokens_per_cycle=cfg.llm.max_tokens_per_cycle,
                    temperature=cfg.llm.temperature,
                    daily_token_budget=cfg.llm.daily_token_budget,
                )
            except LLMError as exc:
                console.print(f"[red]LLM provider error:[/red] {exc}")
                return
            analyzer = Analyzer(provider=provider, store=store)
            model = await store.get_latest_system_snapshot()
            metrics = await store.get_metrics(limit=50)
            insights = await store.get_recent_insights(limit=10)

            business_context = ""
            try:
                from observibot.core.code_intelligence.service import (
                    CodeKnowledgeService,
                )
                knowledge = CodeKnowledgeService(store)
                if await knowledge.should_inject_context(question):
                    facts = await knowledge.get_context_for_question(question)
                    business_context = await knowledge.format_context_for_prompt(
                        facts,
                    )
            except Exception:
                pass  # business context is best-effort

            try:
                result = await analyzer.answer_question(
                    question=question,
                    system_model=model,
                    recent_metrics=metrics,
                    recent_insights=insights,
                    business_context=business_context,
                )
            except LLMError as exc:
                console.print(f"[red]LLM error:[/red] {exc}")
                return
            console.print(
                Panel(
                    f"[bold]Answer:[/bold] {result.answer}\n\n"
                    f"[bold]Evidence:[/bold] {', '.join(result.evidence)}\n"
                    f"[bold]Follow-ups:[/bold] {', '.join(result.follow_ups)}",
                    title="observibot ask",
                    border_style="cyan",
                )
            )

    asyncio.run(_run())


@app.command()
def cost(config: Path | None = typer.Option(None, "--config", "-c")) -> None:
    """Show LLM usage and cost summary."""
    cfg = _safe_load_config(config)

    async def _run() -> None:
        async with Store(cfg.store.path) as store:
            summary = await store.get_llm_usage_summary()
        console.print(
            Panel(
                f"[bold]Calls:[/bold] {summary['calls']}\n"
                f"[bold]Tokens:[/bold] {summary['total_tokens']}\n"
                f"[bold]Cost (USD):[/bold] ${summary['cost_usd']:.4f}\n"
                f"[bold]Since:[/bold] {summary['since']}",
                title="LLM Usage",
                border_style="cyan",
            )
        )

    asyncio.run(_run())


@app.command()
def analyze(config: Path | None = typer.Option(None, "--config", "-c")) -> None:
    """Run a one-shot LLM analysis of recent metrics and changes.

    Useful for ad-hoc investigation: pulls the last two hours of metrics and
    change events, runs the anomaly detector, and asks the LLM for insights
    without starting the full monitor daemon.
    """
    cfg = _safe_load_config(config)

    async def _run() -> None:
        async with Store(cfg.store.path) as store:
            try:
                provider = build_provider(
                    cfg.llm.provider,
                    cfg.llm.model,
                    cfg.llm.api_key,
                    max_tokens_per_cycle=cfg.llm.max_tokens_per_cycle,
                    temperature=cfg.llm.temperature,
                    daily_token_budget=cfg.llm.daily_token_budget,
                )
            except LLMError as exc:
                console.print(f"[red]LLM provider error:[/red] {exc}")
                raise typer.Exit(code=2) from exc

            analyzer_obj = Analyzer(provider=provider, store=store)
            model = await store.get_latest_system_snapshot()
            if model is None:
                console.print(
                    "[yellow]No system snapshot yet.[/yellow] "
                    "Run [cyan]observibot discover[/cyan] first."
                )
                return

            since = datetime.now(UTC) - timedelta(hours=2)
            metrics = await store.get_metrics(since=since)
            if not metrics:
                console.print(
                    "[yellow]No metrics in the last 2 hours.[/yellow] "
                    "Run [cyan]observibot run[/cyan] for at least one collection cycle."
                )
                return
            changes = await store.get_recent_change_events(since=since, limit=50)
            business_context = await store.get_all_business_context()

            detector = build_detector_from_config(cfg.monitor)
            history = await store.get_metrics(
                since=datetime.now(UTC)
                - timedelta(hours=cfg.monitor.baseline_window_hours)
            )
            anomalies = detector.evaluate(history=history, latest=metrics)

            if anomalies:
                console.print(
                    f"[bold]Detected {len(anomalies)} sustained anomalies. "
                    "Asking the LLM for analysis...[/bold]"
                )
            else:
                console.print(
                    "[dim]No sustained anomalies. Asking the LLM for a "
                    "general health check...[/dim]"
                )

            try:
                insights = await analyzer_obj.analyze_anomalies(
                    anomalies=anomalies,
                    system_model=model,
                    recent_changes=changes,
                    business_context=business_context,
                )
            except LLMError as exc:
                console.print(f"[red]LLM analysis failed:[/red] {exc}")
                raise typer.Exit(code=2) from exc

            if not insights:
                console.print(
                    Panel.fit(
                        "[green]No new insights produced.[/green]\n"
                        "Either everything looks normal or the LLM had nothing "
                        "new to say versus recent insights.",
                        title="observibot analyze",
                        border_style="green",
                    )
                )
                return

            for insight in insights:
                color = {
                    "critical": "red",
                    "warning": "yellow",
                    "info": "cyan",
                }.get(insight.severity, "white")
                actions = (
                    "\n".join(f"  • {a}" for a in insight.recommended_actions)
                    or "  (none)"
                )
                uncertainty = (
                    f"\n[bold]Uncertainty:[/bold] {insight.uncertainty_reason}"
                    if insight.uncertainty_reason
                    else ""
                )
                console.print(
                    Panel(
                        f"[bold]Severity:[/bold] [{color}]{insight.severity}[/{color}]\n"
                        f"[bold]Confidence:[/bold] {insight.confidence:.2f}\n"
                        f"[bold]Source:[/bold] {insight.source}\n\n"
                        f"[bold]Summary:[/bold] {insight.summary}\n\n"
                        f"[bold]Recommended actions:[/bold]\n{actions}"
                        f"{uncertainty}",
                        title=insight.display_title(),
                        border_style=color,
                    )
                )

    asyncio.run(_run())


@app.command("test-alert")
def test_alert(
    config: Path | None = typer.Option(None, "--config", "-c"),
    severity: str = typer.Option(
        "info", "--severity", "-s", help="Severity of the test alert"
    ),
) -> None:
    """Send a test alert through every configured channel.

    Lets you verify Slack/ntfy/webhook setup without waiting for a real
    anomaly. Reports per-channel success or failure.
    """
    cfg = _safe_load_config(config)
    if not cfg.alerting.channels:
        console.print(
            Panel.fit(
                "[yellow]No alert channels configured.[/yellow]\n"
                "Add at least one channel to your config under "
                "[cyan]alerting.channels[/cyan] (slack, ntfy, or webhook).",
                title="observibot test-alert",
                border_style="yellow",
            )
        )
        return

    channels = build_channels(cfg.alerting.channels)
    if not channels:
        console.print(
            "[red]Channel build failed — check 'alerting.channels' types in "
            "your config.[/red]"
        )
        raise typer.Exit(code=2)

    # Aggregation intentionally disabled for the test path so the user gets
    # immediate feedback per channel.
    manager = AlertManager(
        channels=channels,
        max_alerts_per_hour=cfg.alerting.max_alerts_per_hour,
        cooldown_seconds=0,
        aggregation_window_seconds=0,
    )
    insight = Insight(
        title="Observibot test alert",
        severity=severity,
        summary="If you see this, alerts are working!",
        details=(
            "This is a synthetic insight produced by 'observibot test-alert'. "
            "It does not reflect any real issue with your system."
        ),
        recommended_actions=["No action required — this is a test."],
        confidence=1.0,
        source="rule",
    )

    async def _run() -> list:
        try:
            return await manager.dispatch(insight)
        finally:
            await manager.close()

    results = asyncio.run(_run())

    table = Table(title="Test alert results")
    table.add_column("Channel")
    table.add_column("Severity")
    table.add_column("Success")
    table.add_column("Message")
    if not results:
        table.add_row("(none)", severity, "—", "no channels accepted this severity")
    for r in results:
        color = "green" if r.success else "red"
        table.add_row(
            r.channel,
            r.severity,
            f"[{color}]{'YES' if r.success else 'NO'}[/{color}]",
            r.message or "",
        )
    console.print(table)


def _exit_with_error(exc: BaseException) -> None:
    console.print(f"[red]Error:[/red] {exc}")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
