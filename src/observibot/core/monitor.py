"""Monitor loop — APScheduler-based daemon orchestrating discovery, metrics,
analysis, retention, and alerting.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from observibot.agent.analyzer import Analyzer
from observibot.agent.llm_provider import LLMHardError
from observibot.alerting.base import AlertManager
from observibot.connectors.base import BaseConnector, Capability
from observibot.core.anomaly import (
    Anomaly,
    build_detector_from_config,
    compute_anomaly_signature,
)
from observibot.core.code_intelligence.schema_analyzer import analyze_schema_for_facts
from observibot.core.config import ObservibotConfig
from observibot.core.discovery import DiscoveryEngine, diff_models
from observibot.core.evidence import DiagnosticEvidence, EvidenceBundle
from observibot.core.models import Insight, SystemModel
from observibot.core.seasonal import compute_seasonal_updates, hour_of_week
from observibot.core.store import Store

log = logging.getLogger(__name__)


class LockfileError(RuntimeError):
    """Raised when another Observibot process already holds the lockfile."""


@dataclass
class CircuitBreakerState:
    """Current state of the LLM circuit breaker."""

    soft_failures: int = 0
    hard_failures: int = 0
    opened_at: datetime | None = None
    cooldown: timedelta = timedelta(minutes=10)


class CircuitBreaker:
    """Two-mode circuit breaker for the LLM analyzer.

    Soft failures (bad JSON, transient timeouts) follow the original 3-strike
    / 10-minute policy. Hard failures (401 auth, quota) escalate the cooldown
    geometrically (5 → 15 → 60 min) because there is no point retrying.
    """

    SOFT_THRESHOLD = 3
    SOFT_COOLDOWN = timedelta(minutes=10)
    HARD_COOLDOWNS = (
        timedelta(minutes=5),
        timedelta(minutes=15),
        timedelta(hours=1),
    )

    def __init__(self) -> None:
        self.state = CircuitBreakerState()

    def record_success(self) -> None:
        self.state = CircuitBreakerState()

    def record_soft_failure(self) -> None:
        self.state.soft_failures += 1
        if self.state.soft_failures >= self.SOFT_THRESHOLD and self.state.opened_at is None:
            self.state.opened_at = datetime.now(UTC)
            self.state.cooldown = self.SOFT_COOLDOWN
            log.warning(
                "Circuit breaker opened (soft) after %s failures",
                self.state.soft_failures,
            )

    def record_hard_failure(self) -> None:
        idx = min(self.state.hard_failures, len(self.HARD_COOLDOWNS) - 1)
        self.state.hard_failures += 1
        self.state.opened_at = datetime.now(UTC)
        self.state.cooldown = self.HARD_COOLDOWNS[idx]
        log.warning(
            "Circuit breaker opened (hard) with cooldown=%s after %s hard failures",
            self.state.cooldown,
            self.state.hard_failures,
        )

    def is_open(self) -> bool:
        if self.state.opened_at is None:
            return False
        if datetime.now(UTC) - self.state.opened_at >= self.state.cooldown:
            log.info("Circuit breaker cooldown elapsed; closing")
            self.state = CircuitBreakerState()
            return False
        return True


# ---------- lockfile helpers ----------


def _is_pid_alive(pid: int) -> bool:
    """Return True if a process with ``pid`` currently exists."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but belongs to someone else; treat as alive.
        return True
    return True


def acquire_lockfile(path: Path) -> None:
    """Create a PID lockfile, raising :class:`LockfileError` if already held.

    Stale lockfiles (PID no longer alive) are silently cleaned up.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            existing_pid = int(path.read_text().strip() or "0")
        except ValueError:
            existing_pid = 0
        if existing_pid and _is_pid_alive(existing_pid):
            raise LockfileError(
                f"Another Observibot instance is already running (PID {existing_pid}). "
                f"Stop it first or remove {path} if stale."
            )
        log.info("Removing stale lockfile %s (PID %s)", path, existing_pid)
        path.unlink(missing_ok=True)
    path.write_text(str(os.getpid()))


def release_lockfile(path: Path) -> None:
    """Remove the lockfile if it belongs to the current process."""
    if not path.exists():
        return
    try:
        pid = int(path.read_text().strip() or "0")
    except ValueError:
        pid = 0
    if pid == 0 or pid == os.getpid():
        path.unlink(missing_ok=True)


class MonitorLoop:
    """Coordinator for scheduled discovery, collection, analysis, and retention."""

    def __init__(
        self,
        config: ObservibotConfig,
        connectors: list[BaseConnector],
        store: Store,
        analyzer: Analyzer,
        alert_manager: AlertManager,
        lockfile_path: Path | None = None,
        health_host: str | None = "0.0.0.0",
        health_port: int = 8080,
    ) -> None:
        self.config = config
        self.connectors = connectors
        self.store = store
        self.analyzer = analyzer
        self.alert_manager = alert_manager
        self.discovery_engine = DiscoveryEngine(connectors)
        self.detector = build_detector_from_config(config.monitor)
        self.scheduler: AsyncIOScheduler | None = None
        self.circuit_breaker = CircuitBreaker()
        self._stop_event = asyncio.Event()
        self._collection_lock = asyncio.Lock()
        self._analysis_lock = asyncio.Lock()
        self._discovery_lock = asyncio.Lock()
        self._cached_model: SystemModel | None = None
        self._pending_anomalies: list[Anomaly] = []
        self._lockfile_path = lockfile_path
        self._lock_held = False
        self._health_host = health_host
        self._health_port = health_port
        self._health_task: asyncio.Task[None] | None = None
        self._app_db: object | None = None
        # Diagnostic hypothesis-test cooldown cache. Key = anomaly
        # signature (stable fingerprint of metric+labels+direction per
        # Step 3.2); value = (cached_at, evidence_list). Prevents a
        # sustained incident from firing the LLM + sandbox on every
        # analysis cycle.
        self._diagnostic_cache: dict[
            str, tuple[datetime, list[DiagnosticEvidence]]
        ] = {}

    # ---------- event emission ----------

    async def _emit(
        self, event_type: str, subject: str, ref_table: str, ref_id: str,
        *, severity: str | None = None, summary: str | None = None,
        source: str = "monitor_loop", run_id: str | None = None,
    ) -> None:
        """Fire-and-forget event emission. Never blocks the caller."""
        try:
            await self.store.emit_event(
                event_type=event_type, source=source, subject=subject,
                ref_table=ref_table, ref_id=ref_id, severity=severity,
                summary=summary, agent="sre", run_id=run_id,
            )
        except Exception as exc:
            log.debug("Event emission failed: %s", exc)

    # ---------- runtime config ----------

    def reschedule(self, job_id: str, seconds: int) -> None:
        """Reschedule an APScheduler job, update in-memory config, and persist to YAML."""
        if self.scheduler is None:
            raise RuntimeError("Scheduler not started")
        self.scheduler.reschedule_job(job_id, trigger="interval", seconds=seconds)
        key: str | None = None
        if job_id == "collect":
            self.config.monitor.collection_interval_seconds = seconds
            key = "collection_interval_seconds"
        elif job_id == "analyze":
            self.config.monitor.analysis_interval_seconds = seconds
            key = "analysis_interval_seconds"
        if key and self.config.source_path:
            try:
                from observibot.core.config import patch_config_file
                patch_config_file(self.config.source_path, {"monitor": {key: seconds}})
                log.info("Persisted %s=%d to %s", key, seconds, self.config.source_path)
            except Exception as exc:
                log.warning("Failed to persist config change: %s", exc)

    # ---------- capability helpers ----------

    def _connectors_with(self, capability: Capability) -> list[BaseConnector]:
        return [
            c for c in self.connectors
            if c.get_capabilities().supports(capability)
        ]

    # ---------- lifecycle ----------

    async def start(self) -> None:
        """Connect all connectors, run one initial discovery/collection pass,
        then start the scheduler for future intervals.
        """
        log.info("Monitor loop starting")
        if self._lockfile_path is not None:
            acquire_lockfile(self._lockfile_path)
            self._lock_held = True

        # Connect every connector exactly once.
        for connector in self.connectors:
            try:
                await connector.connect()
            except Exception as exc:
                log.warning(
                    "Connector %s failed to connect: %s", connector.name, exc
                )

        # Initialize app database pool for chat queries (opt-in)
        if self.config.chat.enable_app_queries:
            app_dsn = None
            for cc in self.config.connectors:
                if cc.type in ("supabase", "postgresql"):
                    app_dsn = cc.options.get("connection_string")
                    break
            if app_dsn:
                from observibot.core.app_db import AppDatabasePool
                self._app_db = AppDatabasePool(
                    dsn=app_dsn,
                    max_size=self.config.chat.app_db_max_connections,
                    statement_timeout_ms=self.config.chat.statement_timeout_ms,
                )
                try:
                    await self._app_db.connect()
                    log.info("App database pool connected for chat queries")
                except Exception as exc:
                    log.warning("Failed to connect app DB pool: %s", exc)
                    self._app_db = None
            else:
                log.warning(
                    "chat.enable_app_queries is true but no supabase/postgresql "
                    "connector found — app queries will be unavailable"
                )

        # Start the web UI + API + health endpoint as a background task.
        if self._health_host is not None:
            try:
                from observibot.api.deps import (
                    set_analyzer,
                    set_app_db,
                    set_chat_config,
                    set_monitor_loop,
                    set_store,
                )
                set_store(self.store)
                set_analyzer(self.analyzer)
                set_monitor_loop(self)
                set_chat_config(self.config.chat)
                if self._app_db is not None:
                    set_app_db(self._app_db)

                from observibot.health import serve_health

                self._health_task = asyncio.create_task(
                    serve_health(host=self._health_host, port=self._health_port),
                    name="observibot-health",
                )
            except Exception as exc:
                log.warning("Health endpoint failed to start: %s", exc)

        # Clean up stale monitor runs from prior crashes
        try:
            stale_count = await self.store.mark_stale_runs()
            if stale_count:
                log.info("Cleaned up %d stale monitor run records", stale_count)
        except Exception as exc:
            log.debug("Stale run cleanup failed: %s", exc)

        self._cached_model = await self.store.get_latest_system_snapshot()

        # Initial blocking work happens BEFORE the scheduler starts so users
        # see the startup banner and get a first snapshot before the background
        # loop takes over.
        try:
            await self.run_discovery_cycle()
        except Exception as exc:
            log.warning("Initial discovery failed: %s", exc)
        try:
            await self.run_collection_cycle()
        except Exception as exc:
            log.warning("Initial collection failed: %s", exc)

        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduler.add_job(
            self._safe_collect,
            "interval",
            seconds=self.config.monitor.collection_interval_seconds,
            id="collect",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )
        scheduler.add_job(
            self._safe_analyze,
            "interval",
            seconds=self.config.monitor.analysis_interval_seconds,
            id="analyze",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )
        scheduler.add_job(
            self._safe_discover,
            "interval",
            seconds=self.config.monitor.discovery_interval_seconds,
            id="discover",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )
        scheduler.add_job(
            self._safe_retention,
            "interval",
            hours=24,
            id="retention",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )
        scheduler.start()
        self.scheduler = scheduler

    async def stop(self) -> None:
        log.info("Monitor loop stopping")
        if self.scheduler is not None:
            with contextlib.suppress(Exception):
                self.scheduler.shutdown(wait=False)
            self.scheduler = None
        if self._health_task is not None and not self._health_task.done():
            self._health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._health_task
            self._health_task = None
        for connector in self.connectors:
            with contextlib.suppress(Exception):
                await connector.close()
        if self._app_db is not None:
            with contextlib.suppress(Exception):
                await self._app_db.close()
            self._app_db = None
        await self.alert_manager.close()
        if self._lock_held and self._lockfile_path is not None:
            release_lockfile(self._lockfile_path)
            self._lock_held = False
        self._stop_event.set()

    async def run_forever(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):  # pragma: no cover
                loop.add_signal_handler(sig, self._stop_event.set)
        try:
            await self.start()
            await self._stop_event.wait()
        finally:
            await self.stop()

    # ---------- jobs (scheduler-safe wrappers) ----------

    async def _safe_collect(self) -> None:
        async with self._collection_lock:
            try:
                await self.run_collection_cycle()
            except Exception as exc:
                log.exception("Collection cycle failed: %s", exc)

    async def _safe_analyze(self) -> list[Insight]:
        async with self._analysis_lock:
            try:
                return await self.run_analysis_cycle()
            except Exception as exc:
                log.exception("Analysis cycle failed: %s", exc)
                return []

    async def _safe_discover(self) -> None:
        async with self._discovery_lock:
            try:
                await self.run_discovery_cycle()
            except Exception as exc:
                log.exception("Discovery cycle failed: %s", exc)

    async def _safe_retention(self) -> None:
        try:
            result = await self.store.apply_retention(
                metrics_days=self.config.store.metrics_retention_days,
                events_days=self.config.store.events_retention_days,
                insights_days=self.config.store.insights_retention_days,
                max_snapshots=self.config.store.max_snapshots,
            )
            log.info("Retention cleanup: %s", result)
        except Exception as exc:
            log.exception("Retention job failed: %s", exc)

    # ---------- cycles ----------

    async def _run_source_extraction(self, system_model: SystemModel) -> None:
        """Run source code extraction if GitHub + local clone is configured."""
        gh = self.config.github
        if not (gh.enabled and gh.cloud_extraction and gh.local_clone_path):
            return

        clone_path = Path(gh.local_clone_path)
        if not clone_path.is_dir():
            log.debug("Local clone path %s not found, skipping extraction", clone_path)
            return

        try:
            head_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(clone_path), capture_output=True, text=True, timeout=10,
            ).stdout.strip()
        except Exception as exc:
            log.debug("Cannot read git HEAD at %s: %s", clone_path, exc)
            return

        last_sha = await self.store.get_code_intelligence_meta("last_indexed_commit")

        from observibot.core.code_intelligence.extractor import SemanticExtractor
        from observibot.core.code_intelligence.tree_sitter_index import TreeSitterIndex

        idx = TreeSitterIndex()
        extractor = SemanticExtractor(
            code_index=idx,
            llm_provider=self.analyzer.provider,
            store=self.store,
            cloud_extraction_allowed=gh.cloud_extraction,
        )

        if last_sha and last_sha == head_sha:
            # Same commit — check if there's a pending batch to continue
            batch_idx_str = await self.store.get_code_intelligence_meta(
                "last_extracted_file_index",
            )
            if batch_idx_str is None or batch_idx_str == "-1":
                log.debug(
                    "Source code unchanged (HEAD=%s), extraction complete",
                    head_sha[:8],
                )
                return
            # Continue batched extraction from where we left off
            start_index = int(batch_idx_str)
        else:
            # New commit — save SHA and start fresh
            await self.store.set_code_intelligence_meta(
                "last_indexed_commit", head_sha,
            )
            start_index = 0

            if last_sha and last_sha != head_sha:
                # Try incremental extraction for changed files
                try:
                    diff_result = subprocess.run(
                        ["git", "diff", "--name-only", last_sha, head_sha],
                        cwd=str(clone_path), capture_output=True, text=True,
                        timeout=10,
                    )
                    changed = [
                        str(clone_path / f)
                        for f in diff_result.stdout.strip().splitlines()
                        if f.strip()
                    ]
                except Exception:
                    changed = []

                if changed:
                    log.info(
                        "Running incremental extraction on %d changed files",
                        len(changed),
                    )
                    facts = await extractor.run_incremental_extraction(
                        str(clone_path), changed, system_model=system_model,
                    )
                    await self.store.set_code_intelligence_meta(
                        "last_extracted_file_index", "-1",
                    )
                    await self.store.set_code_intelligence_meta(
                        "last_extraction_at", datetime.now(UTC).isoformat(),
                    )
                    log.info(
                        "Extracted %d semantic facts from source code",
                        len(facts),
                    )
                    return

        # Batched full extraction: process 3 files per discovery cycle
        # (3 files × ~3 chunks × ~6s/LLM call ≈ 54s, within 120s timeout)
        batch_size = 3
        log.info(
            "Running batched extraction (files %d+, batch=%d)",
            start_index, batch_size,
        )

        # Index the repo once (fast) before the LLM-heavy extraction
        await idx.index_directory(str(clone_path))
        high_signal = await idx.get_high_signal_files()
        total = len(high_signal)

        # Pre-compute next_index so we can save it even if extraction times out
        end_index = min(start_index + batch_size, total)
        next_index = end_index if end_index < total else -1
        await self.store.set_code_intelligence_meta(
            "last_extracted_file_index", str(next_index),
        )

        facts, _ = await extractor.run_full_extraction(
            str(clone_path), system_model=system_model,
            start_index=start_index, batch_size=batch_size,
        )

        await self.store.set_code_intelligence_meta(
            "last_extraction_at", datetime.now(UTC).isoformat(),
        )
        if next_index == -1:
            log.info(
                "Extraction complete: %d facts from final batch", len(facts),
            )
        else:
            log.info(
                "Extracted %d facts, next batch starts at index %d of %d",
                len(facts), next_index, total,
            )

    async def run_discovery_cycle(self) -> SystemModel:
        log.info("Running discovery cycle")
        new_model = await self.discovery_engine.run()
        old_model = self._cached_model
        diff = diff_models(old_model, new_model)
        await self.store.save_system_snapshot(new_model)
        self._cached_model = new_model
        try:
            await analyze_schema_for_facts(new_model, self.store)
        except Exception as exc:
            log.debug("Schema fact seeding skipped: %s", exc)
        try:
            await asyncio.wait_for(
                self._run_source_extraction(new_model), timeout=120,
            )
        except TimeoutError:
            log.warning("Source code extraction timed out after 120s")
        except Exception as exc:
            log.warning("Source code extraction failed: %s", exc)
        if diff.has_changes and old_model is not None:
            await self._emit(
                "drift", "system_topology",
                "system_snapshots", new_model.id,
                severity="info", source="discovery",
                summary=(
                    f"Schema/topology change: +{len(diff.added_tables)} tables, "
                    f"-{len(diff.removed_tables)} tables"
                ),
            )
            insight = Insight(
                title="System architecture changed",
                severity="info",
                summary=(
                    f"Detected schema/service changes: "
                    f"+{len(diff.added_tables)} tables, "
                    f"-{len(diff.removed_tables)} tables, "
                    f"~{len(diff.changed_tables)} altered tables, "
                    f"+{len(diff.added_services)} services, "
                    f"-{len(diff.removed_services)} services."
                ),
                details=diff.to_human_readable(),
                related_tables=diff.added_tables + diff.removed_tables,
                source="drift",
                confidence=0.9,
            )
            insight.fingerprint = insight.compute_fingerprint()
            stored = await self.store.save_insight(insight)
            if stored:
                await self._emit(
                    "insight",
                    insight.related_metrics[0]
                    if insight.related_metrics else "system",
                    "insights", insight.id,
                    severity=insight.severity, summary=insight.title,
                )
                await self.alert_manager.dispatch(insight)
        return new_model

    async def run_collection_cycle(self) -> int:
        log.info("Running collection cycle")
        run_id = uuid.uuid4().hex[:12]
        started_at = datetime.now(UTC)
        try:
            await self.store.create_monitor_run(run_id, started_at)
        except Exception as exc:
            log.warning("Failed to create monitor run record: %s", exc)

        all_metrics = []
        anomaly_count = 0
        insight_count = 0
        llm_used = False
        cutoff = datetime.now(UTC) - timedelta(
            seconds=self.config.monitor.collection_interval_seconds * 2
        )

        metric_connectors = self._connectors_with(Capability.METRICS)
        change_connectors = self._connectors_with(Capability.CHANGES)

        try:
            for connector in metric_connectors:
                try:
                    metrics = await connector.collect_metrics()
                    all_metrics.extend(metrics)
                except Exception as exc:
                    log.warning(
                        "Connector %s metric collection failed: %s",
                        connector.name, exc,
                    )

            for connector in change_connectors:
                try:
                    changes = await connector.get_recent_changes(cutoff)
                    for change in changes:
                        await self.store.save_change_event(change)
                        await self._emit(
                            "deploy",
                            change.details.get("service", connector.name)
                            if isinstance(change.details, dict) else connector.name,
                            "change_events", change.id,
                            severity="info", summary=change.summary,
                            run_id=run_id,
                        )
                except Exception as exc:
                    log.debug(
                        "Connector %s change polling failed: %s",
                        connector.name, exc,
                    )

            # Load baseline history BEFORE saving the current batch, otherwise
            # each new value contaminates its own baseline (median/MAD) and
            # biases the detector toward false negatives.
            baseline_window = timedelta(hours=self.config.monitor.baseline_window_hours)
            history = await self.store.get_metrics(
                since=datetime.now(UTC) - baseline_window
            )

            # Isolated seasonal-lookup fetch: a transient read failure must
            # NOT drop the whole cycle. An empty seasonal_lookup makes
            # evaluate_seasonal() delegate to evaluate() unchanged.
            try:
                seasonal_lookup = await self.store.get_seasonal_baselines_for_hour(
                    hour_of_week(started_at),
                    min_weeks_observed=self.config.monitor.min_seasonal_weeks,
                )
            except Exception as exc:
                log.warning(
                    "Seasonal baseline fetch failed, falling back to rolling: %s",
                    exc,
                )
                seasonal_lookup = {}

            strip_set = frozenset(self.config.monitor.seasonal_identity_labels)
            anomalies = self.detector.evaluate_seasonal(
                history=history,
                latest=all_metrics,
                seasonal_lookup=seasonal_lookup,
                identity_strip_set=strip_set,
            )

            if all_metrics:
                await self.store.save_metrics(all_metrics)
                try:
                    n_updated = await compute_seasonal_updates(
                        store=self.store,
                        metrics=all_metrics,
                        identity_strip_set=strip_set,
                        max_samples=self.config.monitor.max_seasonal_samples,
                    )
                    if n_updated:
                        await self._emit(
                            "seasonal_update",
                            "collection_cycle",
                            "monitor_runs",
                            run_id,
                            severity="info",
                            summary=f"Updated {n_updated} seasonal buckets",
                            run_id=run_id,
                        )
                except Exception as exc:
                    log.warning(
                        "Seasonal baseline update failed (non-fatal): %s", exc
                    )
                    await self._emit(
                        "seasonal_update",
                        "collection_cycle",
                        "monitor_runs",
                        run_id,
                        severity="warning",
                        summary=f"Seasonal update failed: {exc}",
                        run_id=run_id,
                    )
            anomaly_count = len(anomalies)
            if anomalies:
                log.info("Detected %s sustained anomalies", len(anomalies))
                for anomaly in anomalies:
                    await self._emit(
                        "anomaly", anomaly.metric_name,
                        "metric_snapshots", anomaly.metric_name,
                        severity=anomaly.severity,
                        summary=(
                            f"{anomaly.metric_name} exceeded threshold: "
                            f"{anomaly.value}"
                        ),
                        run_id=run_id,
                    )
                self._pending_anomalies.extend(anomalies)
                llm_used = True  # Analysis attempted (input), not output
                analysis_results = await self._safe_analyze()
                insight_count = len(analysis_results)

            log.info(
                "Collection cycle completed: %d metrics from %d connectors. "
                "Next cycle in %ds.",
                len(all_metrics),
                len(metric_connectors),
                self.config.monitor.collection_interval_seconds,
            )

            await self._emit(
                "metric_collection", "collection_cycle",
                "monitor_runs", run_id,
                summary=(
                    f"Collected {len(all_metrics)} metrics "
                    f"from {len(metric_connectors)} connectors"
                ),
                run_id=run_id,
            )

            try:
                await self.store.complete_monitor_run(
                    run_id,
                    datetime.now(UTC),
                    {
                        "metric_count": len(all_metrics),
                        "anomaly_count": anomaly_count,
                        "insight_count": insight_count,
                        "llm_used": llm_used,
                        "system_snapshot_id": (
                            self._cached_model.id if self._cached_model else None
                        ),
                    },
                )
            except Exception as exc:
                log.debug("Failed to complete monitor run record: %s", exc)

            return len(all_metrics)

        except Exception as exc:
            try:
                await self.store.fail_monitor_run(run_id, str(exc))
            except Exception as store_exc:
                log.debug("Failed to record monitor run failure: %s", store_exc)
            raise

    async def run_analysis_cycle(self) -> list[Insight]:
        if self.circuit_breaker.is_open():
            log.info("Circuit breaker open; skipping analysis")
            return []
        anomalies = self._pending_anomalies
        self._pending_anomalies = []
        if not anomalies:
            return []
        recent_changes = await self.store.get_recent_change_events(
            since=datetime.now(UTC) - timedelta(hours=2)
        )
        business_context = await self.store.get_all_business_context()

        # Batch recurrence lookup BEFORE the LLM call so the prompt can
        # include historical context ("seen N times at this hour"). Per-subject
        # queries would be N+1; the batch variant runs as a single WHERE IN.
        anomaly_subjects = sorted({a.metric_name for a in anomalies})
        try:
            recurrence_map = await self.store.get_event_recurrence_summaries(
                subjects=anomaly_subjects,
                event_type="anomaly",
                days=30,
            )
        except Exception as exc:
            log.debug("Recurrence lookup failed (non-fatal): %s", exc)
            recurrence_map = {}

        # Step 3.3: build a unified EvidenceBundle for this cycle. Today it
        # carries recurrence only; Step 3.4 populates diagnostics via the
        # hypothesis-test loop below.
        pending_bundle = EvidenceBundle.from_recurrence_map(recurrence_map)
        # TODO(future-step): invoke CorrelationDetector here and extend
        # pending_bundle.correlations before the analyzer call.

        await self._maybe_run_diagnostics(anomalies, pending_bundle)

        try:
            insights = await self.analyzer.analyze_anomalies(
                anomalies=anomalies,
                system_model=self._cached_model,
                recent_changes=recent_changes,
                business_context=business_context,
                recurrence_context=recurrence_map,
                evidence=pending_bundle,
            )
            self.circuit_breaker.record_success()
        except LLMHardError as exc:
            log.warning("LLM hard failure: %s", exc)
            self.circuit_breaker.record_hard_failure()
            return []
        except Exception as exc:
            log.warning("Analyzer soft failure: %s", exc)
            self.circuit_breaker.record_soft_failure()
            return []

        bundle_dict = pending_bundle.to_dict() if not pending_bundle.is_empty() else None

        saved_insights: list[Insight] = []
        for insight in insights:
            # Legacy single-metric recurrence_context is still populated so
            # older consumers (dashboard "seen N times" badge) continue to
            # render during the one-release backcompat window.
            if recurrence_map:
                for metric in insight.related_metrics:
                    rec = recurrence_map.get(metric)
                    if rec and rec.get("count", 0) > 1:
                        insight.recurrence_context = rec
                        break
            # Unified bundle — preserves cross-metric and (future) diagnostic
            # evidence the legacy single-metric field can't express.
            insight.evidence = bundle_dict

            stored = await self.store.save_insight(insight)
            if not stored:
                continue

            saved_insights.append(insight)
            await self._emit(
                "insight",
                insight.related_metrics[0] if insight.related_metrics else "system",
                "insights", insight.id,
                severity=insight.severity, summary=insight.title,
            )
            await self.alert_manager.dispatch(insight)
        return saved_insights

    async def _maybe_run_diagnostics(
        self,
        anomalies: list[Anomaly],
        pending_bundle: EvidenceBundle,
    ) -> None:
        """Hypothesis-test loop: generate SQL queries against the app DB
        and attach evidence to ``pending_bundle.diagnostics`` when it's
        safe to do so.

        The whole phase degrades to "no diagnostics" on any failure —
        never raises. Hard ceilings bound wall-clock; cooldown cache
        suppresses repeat firings of the same anomaly signature.
        """
        diag_cfg = self.config.monitor.diagnostics
        if not diag_cfg.enabled:
            return
        if self._app_db is None or not getattr(self._app_db, "is_connected", False):
            return
        if self._cached_model is None or not anomalies:
            return

        # Gate 1 (Gemini-4.2 / ChatGPT-6): skip cold-start rolling
        # anomalies unless severity is critical. A seasonal anomaly
        # (weeks_observed >= 4) has enough history to trust.
        diag_candidates = [
            a for a in anomalies
            if a.baseline_source == "seasonal" or a.severity == "critical"
        ]
        if not diag_candidates:
            return

        # Gate 2 (Gemini-7.1): if the circuit breaker is open, do not
        # spend an LLM call on diagnostics either.
        if self.circuit_breaker.is_open():
            return

        sig = compute_anomaly_signature(diag_candidates)
        now = datetime.now(UTC)
        cooldown = timedelta(minutes=diag_cfg.cooldown_minutes)
        run_id = uuid.uuid4().hex[:12]

        cached = self._diagnostic_cache.get(sig)
        if cached and (now - cached[0]) < cooldown:
            pending_bundle.diagnostics = list(cached[1])
            await self._emit(
                "diagnostic_skipped", "analysis_cycle",
                "monitor_runs", run_id,
                severity="info",
                summary=f"diagnostic cooldown active (sig={sig[:8]})",
                run_id=run_id,
            )
        else:
            try:
                diag_queries = await asyncio.wait_for(
                    self.analyzer.generate_diagnostic_queries(
                        anomalies=diag_candidates,
                        system_model=self._cached_model,
                        recent_changes=None,
                        recurrence=pending_bundle.recurrence,
                    ),
                    timeout=diag_cfg.hypothesis_timeout_s,
                )
                evidence_list = await asyncio.wait_for(
                    self.analyzer.execute_diagnostics(
                        queries=diag_queries,
                        app_db=self._app_db,
                        system_model=self._cached_model,
                        cfg=diag_cfg,
                    ),
                    timeout=diag_cfg.execution_timeout_s,
                )
                pending_bundle.diagnostics = evidence_list
                self._diagnostic_cache[sig] = (now, evidence_list)
                ok = sum(1 for e in evidence_list if not e.error)
                rej = sum(1 for e in evidence_list if e.error)
                await self._emit(
                    "diagnostic_run", "analysis_cycle",
                    "monitor_runs", run_id,
                    severity="info",
                    summary=(
                        f"{len(evidence_list)} diagnostic(s): "
                        f"{ok} succeeded, {rej} rejected/errored"
                    ),
                    run_id=run_id,
                )
            except TimeoutError:
                log.warning(
                    "Diagnostic phase exceeded hard ceiling; proceeding "
                    "without evidence"
                )
                await self._emit(
                    "diagnostic_timeout", "analysis_cycle",
                    "monitor_runs", run_id,
                    severity="warning",
                    summary="diagnostic phase timed out",
                    run_id=run_id,
                )
            except Exception as exc:
                log.warning(
                    "Diagnostic phase failed: %s — proceeding without", exc
                )

        # Evict entries older than the current cooldown so the cache
        # stays bounded without a separate timer task.
        self._diagnostic_cache = {
            k: v for k, v in self._diagnostic_cache.items()
            if (now - v[0]) < cooldown
        }

    async def trigger_analysis(self, anomalies: list[Anomaly]) -> list[Insight]:
        """Force an analysis pass with the supplied anomalies."""
        self._pending_anomalies.extend(anomalies)
        return await self.run_analysis_cycle()


def build_monitor_loop(
    config: ObservibotConfig,
    connectors: list[BaseConnector],
    store: Store,
    analyzer: Analyzer,
    alert_manager: AlertManager,
    lockfile_path: Path | None = None,
    health_host: str | None = "0.0.0.0",
    health_port: int = 8080,
) -> MonitorLoop:
    """Convenience factory used by the CLI."""
    return MonitorLoop(
        config=config,
        connectors=connectors,
        store=store,
        analyzer=analyzer,
        alert_manager=alert_manager,
        lockfile_path=lockfile_path,
        health_host=health_host,
        health_port=health_port,
    )


__all__ = [
    "CircuitBreaker",
    "LockfileError",
    "MonitorLoop",
    "acquire_lockfile",
    "build_monitor_loop",
    "release_lockfile",
]
