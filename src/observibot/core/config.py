"""Configuration loader with env-var resolution and validation."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATHS = (
    Path("config/observibot.yaml"),
    Path.home() / ".config" / "observibot" / "observibot.yaml",
)

ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


class ConfigError(Exception):
    """Raised when configuration is missing, malformed, or unresolved."""


@dataclass
class LLMConfig:
    """LLM provider config block."""

    provider: str = "mock"
    model: str = "claude-sonnet-4-20250514"
    api_key: str | None = None
    max_tokens_per_cycle: int = 4000
    temperature: float = 0.2
    daily_token_budget: int = 200_000


@dataclass
class ConnectorConfig:
    """A single configured connector."""

    name: str
    type: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class MonitorConfig:
    """Monitor scheduling and anomaly-detection knobs.

    The detection policy is MAD (median absolute deviation) plus a minimum
    absolute-difference gate plus sustained-interval escalation. See
    :class:`observibot.core.anomaly.AnomalyDetector` for the semantics.
    """

    collection_interval_seconds: int = 300
    analysis_interval_seconds: int = 1800
    discovery_interval_seconds: int = 3600
    mad_threshold: float = 3.0
    min_absolute_diff: float = 10.0
    min_relative_diff: float = 0.02
    sustained_intervals_warning: int = 2
    sustained_intervals_critical: int = 3
    baseline_window_hours: int = 24
    min_samples_for_baseline: int = 12
    min_seasonal_weeks: int = 4
    max_seasonal_samples: int = 30
    seasonal_identity_labels: list[str] = field(
        default_factory=lambda: ["instance", "job", "pid", "cpu"]
    )
    # Step 3.3: reserved for Step 3.4 diagnostic queries. READ but not
    # acted on in Step 3.3. See :class:`DiagnosticsConfig` for details.
    diagnostics: DiagnosticsConfig = field(
        default_factory=lambda: DiagnosticsConfig()
    )


@dataclass
class AlertChannelConfig:
    """A single alert channel config."""

    type: str
    options: dict[str, Any] = field(default_factory=dict)
    severity_filter: list[str] = field(
        default_factory=lambda: ["critical", "warning"]
    )


@dataclass
class AlertingConfig:
    """Alert channels + rate-limit policy + aggregation window."""

    channels: list[AlertChannelConfig] = field(default_factory=list)
    max_alerts_per_hour: int = 10
    cooldown_seconds: int = 300
    aggregation_window_seconds: float = 30.0
    aggregation_min_incidents: int = 3


@dataclass
class StoreConfig:
    """Data store config."""

    type: str = "sqlite"
    path: str = "./data/observibot.db"
    metrics_retention_days: int = 30
    events_retention_days: int = 90
    insights_retention_days: int = 90
    max_snapshots: int = 10


@dataclass
class LoggingConfig:
    level: str = "INFO"
    format: str = "text"


@dataclass
class GitHubConfig:
    """GitHub source code connector config."""

    enabled: bool = False
    token: str = ""
    repo: str = ""
    branch: str = "main"
    poll_interval_seconds: int = 900
    local_clone_path: str = ""
    cloud_extraction: bool = False


@dataclass
class ChatConfig:
    """Chat / Q&A settings."""

    enable_app_queries: bool = False
    app_db_max_connections: int = 3
    statement_timeout_ms: int = 3000
    max_result_rows: int = 500
    explain_cost_threshold: float = 100_000


@dataclass
class DiagnosticsConfig:
    """Reserved for Step 3.4. Autonomous diagnostic query generation.

    When enabled, the monitor generates SQL queries against the
    application database to collect evidence about anomalies before
    synthesis. Independent of :class:`ChatConfig`; autonomous use
    requires stricter guardrails than user-initiated chat queries.

    All default values are deliberately scale-invariant (Tier 0):

    - ``explain_cost_threshold``: 10x tighter than chat to bound the
      worst-case query cost per cycle, not calibrated to any customer's
      row counts.
    - ``statement_timeout_ms``: a wall-clock budget the database
      enforces, independent of dataset size.
    - ``max_queries_per_cycle``: bounds LLM/database fan-out per
      analysis cycle, not per customer.
    - ``max_rows_per_query``: evidence surface sent back to the LLM,
      not an absolute row cap on the underlying query (LIMIT injection
      handles that at sandbox-time).
    - ``cooldown_minutes``: rate-limit on diagnostic firings to avoid
      re-querying the same hypothesis before conditions change.
    - ``fail_closed_on_explain_error``: safety posture — on EXPLAIN
      failure, suppress the query rather than run it.

    In Step 3.3 this config is READ (wired through YAML) but not
    ACTED ON. No diagnostic queries are generated or executed.
    """

    enabled: bool = False
    explain_cost_threshold: float = 10_000
    statement_timeout_ms: int = 2000
    max_queries_per_cycle: int = 3
    max_rows_per_query: int = 50
    cooldown_minutes: int = 10
    fail_closed_on_explain_error: bool = True


@dataclass
class ObservibotConfig:
    """Top-level config object."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    connectors: list[ConnectorConfig] = field(default_factory=list)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    alerting: AlertingConfig = field(default_factory=AlertingConfig)
    store: StoreConfig = field(default_factory=StoreConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    chat: ChatConfig = field(default_factory=ChatConfig)
    github: GitHubConfig = field(default_factory=GitHubConfig)
    source_path: Path | None = None


def _resolve_env_vars(value: Any, path: str = "") -> Any:
    """Recursively replace ``${VAR}`` placeholders in strings with env values.

    Supports ``${VAR:-default}`` for fallback values. Raises :class:`ConfigError`
    when a referenced variable is missing and has no default.
    """
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            var = match.group(1)
            default = match.group(2)
            env_val = os.environ.get(var)
            if env_val is not None:
                return env_val
            if default is not None:
                return default
            raise ConfigError(
                f"[env] Missing required environment variable '{var}' "
                f"referenced at {path or '<root>'}. "
                f"Fix: export {var}='...' in your shell or .env file."
            )
        return ENV_VAR_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v, f"{path}.{k}" if path else k) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(item, f"{path}[{i}]") for i, item in enumerate(value)]
    return value


def find_env_var_references(text: str) -> list[tuple[str, str | None]]:
    """Return ``(var_name, default_or_none)`` tuples for every ${VAR} reference.

    Used by ``observibot init`` to surface which environment variables a
    freshly generated config file expects. Lines starting with ``#`` (YAML
    comments) are skipped so example references inside docstrings don't leak
    into the user-facing env table.
    """
    stripped_lines: list[str] = []
    for line in text.splitlines():
        lstripped = line.lstrip()
        if lstripped.startswith("#"):
            continue
        stripped_lines.append(line)
    cleaned = "\n".join(stripped_lines)
    return [
        (match.group(1), match.group(2)) for match in ENV_VAR_PATTERN.finditer(cleaned)
    ]


def patch_config_file(config_path: Path, updates: dict[str, dict[str, Any]]) -> None:
    """Update specific scalar values in a YAML config without rewriting the file.

    ``updates`` is a mapping of ``{section: {key: value}}``, e.g.
    ``{"monitor": {"collection_interval_seconds": 600}}``.

    Uses line-by-line replacement so comments, env-var references, and
    overall structure are preserved.
    """
    text = config_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    current_section: str | None = None

    for i, line in enumerate(lines):
        stripped = line.lstrip()
        # Detect top-level section headers (no leading whitespace, ends with ':')
        if not line[0:1].isspace() and not stripped.startswith("#") and ":" in stripped:
            current_section = stripped.split(":")[0].strip()
            continue
        if current_section in updates:
            section_updates = updates[current_section]
            for key, value in section_updates.items():
                pattern = re.compile(rf"^(\s+{re.escape(key)}\s*:\s*)(\S.*)$")
                m = pattern.match(line)
                if m:
                    lines[i] = f"{m.group(1)}{value}\n"

    config_path.write_text("".join(lines), encoding="utf-8")


def _find_default_config() -> Path | None:
    """Return the first default config path that exists, if any."""
    for candidate in DEFAULT_CONFIG_PATHS:
        if candidate.exists():
            return candidate
    return None


def load_config(path: str | Path | None = None) -> ObservibotConfig:
    """Load Observibot config from YAML.

    Resolution order:

    1. ``path`` argument if provided.
    2. ``OBSERVIBOT_CONFIG`` environment variable.
    3. ``./config/observibot.yaml``.
    4. ``~/.config/observibot/observibot.yaml``.

    If none of the above exist, return a default :class:`ObservibotConfig`
    using the mock LLM provider and no connectors.

    Raises:
        ConfigError: If the file is unreadable, malformed, or has unresolved
            ``${ENV_VAR}`` references with no defaults.
    """
    config_path: Path | None
    if path is not None:
        config_path = Path(path)
    elif env_path := os.environ.get("OBSERVIBOT_CONFIG"):
        config_path = Path(env_path)
    else:
        config_path = _find_default_config()

    if config_path is None:
        return ObservibotConfig()

    if not config_path.exists():
        raise ConfigError(
            f"[config] Config file not found: {config_path}. "
            f"Fix: run 'observibot init' to create a starter config."
        )

    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"[config] Cannot read config file {config_path}: {exc}. "
            f"Fix: check file permissions."
        ) from exc

    try:
        raw = yaml.safe_load(raw_text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"[config] Invalid YAML in {config_path}: {exc}. "
            f"Fix: run the file through a YAML linter."
        ) from exc

    if not isinstance(raw, dict):
        raise ConfigError(
            f"[config] Root of {config_path} must be a mapping, got {type(raw).__name__}. "
            f"Fix: wrap contents under top-level keys like 'llm:', 'connectors:', etc."
        )

    resolved = _resolve_env_vars(raw)
    cfg = _build_config(resolved)
    cfg.source_path = config_path
    return cfg


def _build_config(data: dict[str, Any]) -> ObservibotConfig:
    """Translate a resolved YAML dict into an :class:`ObservibotConfig`."""
    llm_raw = data.get("llm") or {}
    llm = LLMConfig(
        provider=llm_raw.get("provider", "mock"),
        model=llm_raw.get("model", LLMConfig.model),
        api_key=llm_raw.get("api_key"),
        max_tokens_per_cycle=int(llm_raw.get("max_tokens_per_cycle", 4000)),
        temperature=float(llm_raw.get("temperature", 0.2)),
        daily_token_budget=int(llm_raw.get("daily_token_budget", 200_000)),
    )

    connectors: list[ConnectorConfig] = []
    for i, conn in enumerate(data.get("connectors") or []):
        if not isinstance(conn, dict):
            raise ConfigError(
                f"[config] connectors[{i}] must be a mapping. "
                f"Fix: use 'name:', 'type:', and type-specific options."
            )
        name = conn.get("name")
        ctype = conn.get("type")
        if not name:
            raise ConfigError(
                f"[config] connectors[{i}].name is required. "
                f"Fix: add 'name: my-db' to this connector block."
            )
        if not ctype:
            raise ConfigError(
                f"[config] connectors[{i}].type is required. "
                f"Fix: set 'type:' to one of supabase, postgresql, railway."
            )
        opts = {k: v for k, v in conn.items() if k not in {"name", "type"}}
        connectors.append(ConnectorConfig(name=name, type=ctype, options=opts))

    mon_raw = data.get("monitor") or {}
    id_labels = mon_raw.get("seasonal_identity_labels")
    if id_labels is None:
        id_labels = ["instance", "job", "pid", "cpu"]
    diag_raw = mon_raw.get("diagnostics") or {}
    diagnostics = DiagnosticsConfig(
        enabled=bool(diag_raw.get("enabled", False)),
        explain_cost_threshold=float(
            diag_raw.get("explain_cost_threshold", 10_000)
        ),
        statement_timeout_ms=int(diag_raw.get("statement_timeout_ms", 2000)),
        max_queries_per_cycle=int(diag_raw.get("max_queries_per_cycle", 3)),
        max_rows_per_query=int(diag_raw.get("max_rows_per_query", 50)),
        cooldown_minutes=int(diag_raw.get("cooldown_minutes", 10)),
        fail_closed_on_explain_error=bool(
            diag_raw.get("fail_closed_on_explain_error", True)
        ),
    )
    monitor = MonitorConfig(
        collection_interval_seconds=int(mon_raw.get("collection_interval_seconds", 300)),
        analysis_interval_seconds=int(mon_raw.get("analysis_interval_seconds", 1800)),
        discovery_interval_seconds=int(mon_raw.get("discovery_interval_seconds", 3600)),
        mad_threshold=float(mon_raw.get("mad_threshold", 3.0)),
        min_absolute_diff=float(mon_raw.get("min_absolute_diff", 10.0)),
        min_relative_diff=float(mon_raw.get("min_relative_diff", 0.02)),
        sustained_intervals_warning=int(mon_raw.get("sustained_intervals_warning", 2)),
        sustained_intervals_critical=int(mon_raw.get("sustained_intervals_critical", 3)),
        baseline_window_hours=int(mon_raw.get("baseline_window_hours", 24)),
        min_samples_for_baseline=int(mon_raw.get("min_samples_for_baseline", 12)),
        min_seasonal_weeks=int(mon_raw.get("min_seasonal_weeks", 4)),
        max_seasonal_samples=int(mon_raw.get("max_seasonal_samples", 30)),
        seasonal_identity_labels=[str(x) for x in id_labels],
        diagnostics=diagnostics,
    )

    alert_raw = data.get("alerting") or {}
    rate = alert_raw.get("rate_limit") or {}
    channels: list[AlertChannelConfig] = []
    for i, ch in enumerate(alert_raw.get("channels") or []):
        if not isinstance(ch, dict):
            raise ConfigError(
                f"[config] alerting.channels[{i}] must be a mapping. "
                f"Fix: use 'type: slack|webhook' and channel-specific options."
            )
        ctype = ch.get("type")
        if not ctype:
            raise ConfigError(
                f"[config] alerting.channels[{i}].type is required. "
                f"Fix: set 'type:' to slack or webhook."
            )
        sev = ch.get("severity_filter") or ["critical", "warning"]
        opts = {k: v for k, v in ch.items() if k not in {"type", "severity_filter"}}
        channels.append(
            AlertChannelConfig(type=ctype, options=opts, severity_filter=list(sev))
        )
    alerting = AlertingConfig(
        channels=channels,
        max_alerts_per_hour=int(rate.get("max_alerts_per_hour", 10)),
        cooldown_seconds=int(rate.get("cooldown_seconds", 300)),
        aggregation_window_seconds=float(alert_raw.get("aggregation_window_seconds", 30.0)),
        aggregation_min_incidents=int(alert_raw.get("aggregation_min_incidents", 3)),
    )

    store_raw = data.get("store") or {}
    retention = store_raw.get("retention") or {}
    store = StoreConfig(
        type=store_raw.get("type", "sqlite"),
        path=store_raw.get("path", "./data/observibot.db"),
        metrics_retention_days=int(retention.get("metrics_days", 30)),
        events_retention_days=int(retention.get("events_days", 90)),
        insights_retention_days=int(retention.get("insights_days", 90)),
        max_snapshots=int(retention.get("max_snapshots", 10)),
    )

    log_raw = data.get("logging") or {}
    logging_cfg = LoggingConfig(
        level=str(log_raw.get("level", "INFO")).upper(),
        format=log_raw.get("format", "text"),
    )

    chat_raw = data.get("chat") or {}
    chat = ChatConfig(
        enable_app_queries=bool(chat_raw.get("enable_app_queries", False)),
        app_db_max_connections=int(chat_raw.get("app_db_max_connections", 3)),
        statement_timeout_ms=int(chat_raw.get("statement_timeout_ms", 3000)),
        max_result_rows=int(chat_raw.get("max_result_rows", 500)),
        explain_cost_threshold=float(
            chat_raw.get("explain_cost_threshold", 100_000)
        ),
    )

    gh_raw = data.get("github") or {}
    github = GitHubConfig(
        enabled=bool(gh_raw.get("enabled", False)),
        token=str(gh_raw.get("token", "")),
        repo=str(gh_raw.get("repo", "")),
        branch=str(gh_raw.get("branch", "main")),
        poll_interval_seconds=int(gh_raw.get("poll_interval_seconds", 900)),
        local_clone_path=str(gh_raw.get("local_clone_path", "")),
        cloud_extraction=bool(gh_raw.get("cloud_extraction", False)),
    )

    return ObservibotConfig(
        llm=llm,
        connectors=connectors,
        monitor=monitor,
        alerting=alerting,
        store=store,
        logging=logging_cfg,
        chat=chat,
        github=github,
    )


def write_example_config(target: str | Path) -> Path:
    """Write the bundled example config to ``target``."""
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    example = Path(__file__).resolve().parents[3] / "config" / "observibot.example.yaml"
    if example.exists():
        target_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        target_path.write_text(_FALLBACK_EXAMPLE, encoding="utf-8")
    return target_path


_FALLBACK_EXAMPLE = """\
llm:
  provider: mock
  model: claude-sonnet-4-20250514
  api_key: ${ANTHROPIC_API_KEY:-}

connectors: []

monitor:
  collection_interval_seconds: 300
  analysis_interval_seconds: 1800
  discovery_interval_seconds: 3600

alerting:
  channels: []

store:
  type: sqlite
  path: ./data/observibot.db
"""
