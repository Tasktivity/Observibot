"""Supabase connector — extends the PostgreSQL connector with Supabase-specific
schema exclusions, Row-Level-Security policy discovery, and Prometheus Metrics
API scraping (~200 metrics covering CPU, memory, disk, WAL, replication, auth,
and the connection pooler).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from observibot.connectors.base import Capability, ConnectorCapabilities
from observibot.connectors.postgresql import PostgreSQLConnector
from observibot.core.models import MetricSnapshot, SystemFragment

log = logging.getLogger(__name__)

SUPABASE_INTERNAL_SCHEMAS: tuple[str, ...] = (
    "auth",
    "storage",
    "vault",
    "extensions",
    "graphql",
    "graphql_public",
    "_analytics",
    "_realtime",
    "_supavisor",
    "supabase_functions",
    "supabase_migrations",
    "realtime",
    "pgsodium",
    "pgsodium_masks",
    "net",
)

DEFAULT_METRICS_INCLUDE = [
    r"node_cpu_.*",
    r"node_memory_.*",
    r"node_disk_.*",
    r"node_filesystem_.*",
    r"process_.*",
    r"supavisor_.*",
    r"replication_.*",
]

DEFAULT_METRICS_EXCLUDE = [
    r"go_memstats_.*",
    r".*_bucket$",
    r"runtime_uptime_.*",
]


class SupabaseConnector(PostgreSQLConnector):
    """Connector for Supabase-hosted PostgreSQL databases."""

    type = "supabase"
    extra_excluded_schemas = SUPABASE_INTERNAL_SCHEMAS

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        super().__init__(name=name, config=config)
        options = config.get("options") or {}
        self._project_ref: str | None = options.get("project_ref")
        self._service_key: str | None = options.get("service_key")
        self._metrics_api_enabled: bool = options.get(
            "metrics_api_enabled",
            bool(self._project_ref and self._service_key),
        )
        self._metrics_include: list[str] = options.get(
            "metrics_api_include", DEFAULT_METRICS_INCLUDE,
        )
        self._metrics_exclude: list[str] = options.get(
            "metrics_api_exclude", DEFAULT_METRICS_EXCLUDE,
        )
        self._http_client: Any = None
        self._previous_prometheus_counters: dict[str, float] = {}
        self._metrics_api_disabled_until: datetime | None = None

    def get_capabilities(self) -> ConnectorCapabilities:
        caps = (
            Capability.DISCOVERY
            | Capability.METRICS
            | Capability.CHANGES
            | Capability.HEALTH
        )
        if self._metrics_api_enabled and self._project_ref and self._service_key:
            caps |= Capability.RESOURCE_METRICS
        return ConnectorCapabilities(
            capabilities=caps,
            requires_elevated_role=True,
            notes=[
                "Requires direct Postgres connection (port 5432) for accurate "
                "pg_stat_activity — Supavisor shows pooler sessions, not app sessions.",
                "Full metric coverage needs pg_monitor or pg_read_all_stats.",
            ],
        )

    async def close(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
        await super().close()

    async def discover(self) -> SystemFragment:
        fragment = await super().discover()
        # Re-enable metrics API on each discovery cycle
        self._metrics_api_disabled_until = None
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                await self._enrich_with_rls(conn, fragment)
        except Exception as exc:
            log.debug("RLS discovery failed: %s", exc)
            fragment.errors.append(f"rls: {exc}")
        return fragment

    async def collect_metrics(self) -> list[MetricSnapshot]:
        metrics = await super().collect_metrics()
        if self._should_scrape_prometheus():
            try:
                prom_metrics = await self._collect_prometheus_metrics()
                metrics.extend(prom_metrics)
            except Exception as exc:
                log.warning(
                    "Supabase Metrics API scrape failed: %s", exc,
                )
        return metrics

    def _should_scrape_prometheus(self) -> bool:
        if not self._metrics_api_enabled:
            return False
        if not self._project_ref or not self._service_key:
            return False
        return not (
            self._metrics_api_disabled_until is not None
            and datetime.now(UTC) < self._metrics_api_disabled_until
        )

    async def _ensure_http_client(self) -> Any:
        if self._http_client is None:
            import httpx
            self._http_client = httpx.AsyncClient(timeout=15.0)
        return self._http_client

    async def _collect_prometheus_metrics(self) -> list[MetricSnapshot]:
        import math
        import re

        from observibot.connectors.prometheus_parser import parse_prometheus_text

        client = await self._ensure_http_client()
        url = (
            f"https://{self._project_ref}.supabase.co"
            f"/customer/v1/privileged/metrics"
        )
        import httpx
        response = await client.get(
            url,
            auth=httpx.BasicAuth("service_role", self._service_key),
        )
        if response.status_code in (403, 404):
            log.warning(
                "Supabase Metrics API returned %d — disabling until next "
                "discovery cycle",
                response.status_code,
            )
            self._metrics_api_disabled_until = datetime(9999, 1, 1, tzinfo=UTC)
            return []
        response.raise_for_status()

        now = datetime.now(UTC)
        raw_metrics = parse_prometheus_text(response.text)

        # Pre-compile filters
        inc = [re.compile(p) for p in self._metrics_include]
        exc = [re.compile(p) for p in self._metrics_exclude]

        # Single-pass: filter, compute counter deltas, build snapshots
        snapshots: list[MetricSnapshot] = []
        for pm in raw_metrics:
            # Skip non-finite values before any caching or emission
            if not math.isfinite(pm.value):
                continue
            # Include filter
            if inc and not any(r.search(pm.name) for r in inc):
                continue
            # Exclude filter
            if exc and any(r.search(pm.name) for r in exc):
                continue

            if pm.metric_type == "counter":
                cache_key = f"{pm.name}|{sorted(pm.labels.items())}"
                prev = self._previous_prometheus_counters.get(cache_key)
                self._previous_prometheus_counters[cache_key] = pm.value
                if prev is None:
                    continue  # First cycle — no delta, do NOT emit
                if pm.value < prev:
                    continue  # Counter reset — do NOT emit
                delta = pm.value - prev
                snapshots.append(MetricSnapshot(
                    connector_name=self.name,
                    metric_name=pm.name,
                    value=delta,
                    labels=dict(pm.labels),
                    collected_at=now,
                ))
            else:
                # Gauge / untyped — emit raw value
                snapshots.append(MetricSnapshot(
                    connector_name=self.name,
                    metric_name=pm.name,
                    value=pm.value,
                    labels=dict(pm.labels),
                    collected_at=now,
                ))

        log.info(
            "Supabase Metrics API: collected %d metrics", len(snapshots),
        )
        return snapshots

    async def _enrich_with_rls(self, conn: Any, fragment: SystemFragment) -> None:
        try:
            rows = await conn.fetch(
                """
                SELECT schemaname, tablename, policyname, permissive, roles, cmd, qual
                FROM pg_policies
                """
            )
        except Exception as exc:
            log.debug("pg_policies unavailable: %s", exc)
            return
        by_table: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for r in rows:
            key = (r["schemaname"], r["tablename"])
            by_table.setdefault(key, []).append(
                {
                    "name": r["policyname"],
                    "permissive": r["permissive"],
                    "roles": list(r["roles"]) if r["roles"] else [],
                    "cmd": r["cmd"],
                    "qual": r["qual"],
                }
            )
        for tbl in fragment.tables:
            tbl.rls_policies = by_table.get((tbl.schema, tbl.name), [])

    def required_permissions(self) -> list[str]:
        return super().required_permissions() + [
            "Read access to pg_policies (Supabase RLS introspection)",
        ]
