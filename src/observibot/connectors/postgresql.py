"""PostgreSQL connector — discovers schema and collects per-table metrics.

Used directly for self-hosted Postgres and as a base for the Supabase connector.
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from observibot.connectors.base import (
    BaseConnector,
    Capability,
    ConnectorCapabilities,
)
from observibot.core.models import (
    ChangeEvent,
    HealthStatus,
    MetricSnapshot,
    Relationship,
    SystemFragment,
    TableInfo,
)

log = logging.getLogger(__name__)

# System schemas we never want to surface to users.
DEFAULT_SYSTEM_SCHEMAS: tuple[str, ...] = (
    "pg_catalog",
    "information_schema",
    "pg_toast",
)


def _quote_ident(name: str) -> str:
    """Quote a Postgres identifier safely for string interpolation.

    asyncpg has no built-in identifier quoting (parameters are for values,
    not names). We need this to splice schema/table/column names into SQL
    we build dynamically for value sampling.
    """
    return '"' + name.replace('"', '""') + '"'


class PostgreSQLConnector(BaseConnector):
    """Generic PostgreSQL connector."""

    type = "postgresql"
    extra_excluded_schemas: tuple[str, ...] = ()

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        super().__init__(name=name, config=config)
        self.connection_string: str | None = config.get("connection_string")
        if not self.connection_string:
            raise ValueError(
                f"Connector '{name}' is missing 'connection_string' "
                f"(set the corresponding environment variable in your config)"
            )
        self.included_schemas: list[str] | None = config.get("schemas")
        self.excluded_tables: set[str] = set(config.get("exclude_tables") or [])
        self._pool: Any = None
        self._previous_table_stats: dict[tuple[str, str], dict[str, int]] = {}

    def get_capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            capabilities=(
                Capability.DISCOVERY
                | Capability.METRICS
                | Capability.CHANGES
                | Capability.HEALTH
            ),
            requires_elevated_role=True,
            notes=[
                "Requires pg_monitor or pg_read_all_stats for full pg_stat_* metrics",
            ],
        )

    # ---------- pool lifecycle ----------

    async def connect(self) -> None:
        """Create the asyncpg pool if it doesn't already exist."""
        await self._ensure_pool()

    async def _ensure_pool(self) -> Any:
        if self._pool is None:
            try:
                import asyncpg  # local import: avoids hard dep at import time
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "asyncpg is required for the PostgreSQL connector"
                ) from exc
            self._pool = await asyncpg.create_pool(
                self.connection_string,
                min_size=1,
                max_size=4,
                command_timeout=30.0,
            )
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            try:
                await self._pool.close()
            finally:
                self._pool = None

    def _excluded_schemas(self) -> set[str]:
        return set(DEFAULT_SYSTEM_SCHEMAS) | set(self.extra_excluded_schemas)

    def _get_schema_list(self) -> list[str]:
        """Return the list of schemas to inspect."""
        return list(self.included_schemas) if self.included_schemas else ["public"]

    def _schema_filter_clause(self, alias: str) -> tuple[str, list[Any]]:
        """Build a SQL clause restricting schema lookups."""
        excluded = sorted(self._excluded_schemas())
        params: list[Any] = []
        clauses: list[str] = []
        if self.included_schemas:
            clauses.append(f"{alias} = ANY($1)")
            params.append(list(self.included_schemas))
        if excluded:
            placeholder = f"${len(params) + 1}"
            clauses.append(f"{alias} <> ALL({placeholder})")
            params.append(excluded)
        if not clauses:
            return "TRUE", []
        return " AND ".join(clauses), params

    # ---------- discovery ----------

    async def discover(self) -> SystemFragment:
        fragment = SystemFragment(connector_name=self.name, connector_type=self.type)
        try:
            pool = await self._ensure_pool()
        except Exception as exc:
            log.warning("Connector %s could not connect: %s", self.name, exc)
            fragment.errors.append(f"connect failed: {exc}")
            return fragment

        async with pool.acquire() as conn:
            try:
                fragment.tables = await self._discover_tables(conn)
            except Exception as exc:
                log.warning("Table discovery failed for %s: %s", self.name, exc)
                fragment.errors.append(f"tables: {exc}")

            try:
                fragment.relationships = await self._discover_relationships(conn)
            except Exception as exc:
                log.warning("Relationship discovery failed for %s: %s", self.name, exc)
                fragment.errors.append(f"relationships: {exc}")

            try:
                await self._enrich_with_indexes(conn, fragment.tables)
            except Exception as exc:
                log.debug("Index discovery failed for %s: %s", self.name, exc)
                fragment.errors.append(f"indexes: {exc}")

        return fragment

    async def _discover_tables(self, conn: Any) -> list[TableInfo]:
        clause, params = self._schema_filter_clause("table_schema")
        rows = await conn.fetch(
            f"""
            SELECT table_schema, table_name, column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE {clause}
            ORDER BY table_schema, table_name, ordinal_position
            """,
            *params,
        )
        tables: dict[tuple[str, str], TableInfo] = {}
        for row in rows:
            key = (row["table_schema"], row["table_name"])
            if row["table_name"] in self.excluded_tables:
                continue
            tbl = tables.get(key)
            if tbl is None:
                tbl = TableInfo(name=row["table_name"], schema=row["table_schema"])
                tables[key] = tbl
            tbl.columns.append(
                {
                    "name": row["column_name"],
                    "type": row["data_type"],
                    "nullable": row["is_nullable"] == "YES",
                    "default": row["column_default"],
                }
            )

        # Primary keys
        try:
            pk_clause, pk_params = self._schema_filter_clause("tc.table_schema")
            pk_rows = await conn.fetch(
                f"""
                SELECT tc.table_schema, tc.table_name, kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                WHERE tc.constraint_type = 'PRIMARY KEY' AND {pk_clause}
                ORDER BY tc.table_schema, tc.table_name, kcu.ordinal_position
                """,
                *pk_params,
            )
            for r in pk_rows:
                key = (r["table_schema"], r["table_name"])
                if key in tables:
                    tables[key].primary_key.append(r["column_name"])
        except Exception as exc:  # pragma: no cover - non-fatal
            log.debug("PK lookup failed: %s", exc)

        # Row count via pg_stat_user_tables when available
        try:
            stat_clause, stat_params = self._schema_filter_clause("schemaname")
            stat_rows = await conn.fetch(
                f"""
                SELECT schemaname, relname, n_live_tup
                FROM pg_stat_user_tables
                WHERE {stat_clause}
                """,
                *stat_params,
            )
            for r in stat_rows:
                key = (r["schemaname"], r["relname"])
                if key in tables:
                    value = r["n_live_tup"]
                    tables[key].row_count = int(value) if value is not None else None
        except Exception as exc:  # pragma: no cover
            log.debug("Row count lookup failed: %s", exc)

        try:
            await self._enrich_with_comments(conn, tables)
        except Exception as exc:
            log.debug("Column comment extraction failed: %s", exc)

        try:
            await self._enrich_with_value_distributions(conn, tables)
        except Exception as exc:
            log.debug("Value distribution sampling failed: %s", exc)

        return list(tables.values())

    async def _discover_relationships(self, conn: Any) -> list[Relationship]:
        clause, params = self._schema_filter_clause("tc.table_schema")
        rows = await conn.fetch(
            f"""
            SELECT
                tc.constraint_name,
                tc.table_schema,
                tc.table_name AS from_table,
                kcu.column_name AS from_column,
                ccu.table_name AS to_table,
                ccu.column_name AS to_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
             AND tc.table_schema = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY' AND {clause}
            """,
            *params,
        )

        # Fallback: information_schema may return 0 rows for non-owner roles
        # (common on managed Supabase). Use pg_constraint as a reliable alternative.
        if not rows:
            log.info(
                "%s: information_schema returned 0 FKs, trying pg_constraint fallback",
                self.name,
            )
            schema_names = self._get_schema_list()
            rows = await conn.fetch(
                """
                SELECT
                    c.conname AS constraint_name,
                    cl.relname AS from_table,
                    a.attname AS from_column,
                    cl2.relname AS to_table,
                    a2.attname AS to_column
                FROM pg_constraint c
                JOIN pg_class cl ON c.conrelid = cl.oid
                JOIN pg_namespace ns ON cl.relnamespace = ns.oid
                JOIN pg_attribute a ON a.attrelid = c.conrelid
                    AND a.attnum = ANY(c.conkey)
                JOIN pg_class cl2 ON c.confrelid = cl2.oid
                JOIN pg_attribute a2 ON a2.attrelid = c.confrelid
                    AND a2.attnum = ANY(c.confkey)
                WHERE c.contype = 'f' AND ns.nspname = ANY($1::text[])
                """,
                schema_names,
            )
        return [
            Relationship(
                from_table=r["from_table"],
                from_column=r["from_column"],
                to_table=r["to_table"],
                to_column=r["to_column"],
                constraint_name=r["constraint_name"],
            )
            for r in rows
        ]

    async def _enrich_with_comments(
        self, conn: Any, tables: dict[tuple[str, str], TableInfo],
    ) -> None:
        schema_list = self._get_schema_list()
        rows = await conn.fetch(
            """
            SELECT
                c.table_schema, c.table_name, c.column_name,
                pgd.description AS column_comment
            FROM information_schema.columns c
            LEFT JOIN pg_catalog.pg_description pgd
                ON pgd.objsubid = c.ordinal_position
                AND pgd.objoid = (
                    SELECT oid FROM pg_catalog.pg_class
                    WHERE relname = c.table_name
                    AND relnamespace = (
                        SELECT oid FROM pg_catalog.pg_namespace
                        WHERE nspname = c.table_schema
                    )
                )
            WHERE c.table_schema = ANY($1::text[])
                AND pgd.description IS NOT NULL
            """,
            schema_list,
        )
        for r in rows:
            key = (r["table_schema"], r["table_name"])
            tbl = tables.get(key)
            if tbl is None:
                continue
            for col in tbl.columns:
                if col.get("name") == r["column_name"]:
                    col["comment"] = r["column_comment"]
                    break

    async def _enrich_with_value_distributions(
        self, conn: Any, tables: dict[tuple[str, str], TableInfo],
    ) -> None:
        """Sample top values for enum-like columns so the LLM doesn't guess.

        The LLM's planning prompt otherwise sees only ``status (text)`` and
        falls back to English-convention guesses like ``'completed'`` —
        catastrophically wrong when the real value is ``'complete'``. We
        issue one bounded ``GROUP BY`` per candidate column, capped by a
        server-side statement timeout so a bad column can never stall
        discovery.
        """
        # Column-name patterns where "which discrete values exist" is the
        # usual question and one GROUP BY per column is cheap enough. Each
        # of these exhibits the same failure mode as status: the LLM guesses
        # the most conventional English values and is confidently wrong.
        enum_bares = {
            "status", "state", "type", "kind", "role", "severity",
            "level", "tier", "phase", "mode", "category",
        }

        def _is_enum_candidate(col: dict) -> bool:
            name = (col.get("name") or "").lower()
            col_type = (col.get("type") or "").lower()
            matches_name = name in enum_bares or any(
                name.endswith(f"_{bare}") for bare in enum_bares
            )
            if not matches_name:
                return False
            # Only text-like columns. Integer status codes don't exhibit
            # the "English past-tense guess" failure mode this targets.
            return any(
                t in col_type
                for t in ("text", "char", "varchar", "citext")
            )

        targets: list[tuple[str, str, str]] = []
        for (schema, table), tbl in tables.items():
            if tbl.row_count == 0:
                continue
            if tbl.row_count is not None and tbl.row_count > 10_000_000:
                # Avoid GROUP BY on very large tables without an index hint.
                continue
            for col in tbl.columns:
                if _is_enum_candidate(col):
                    targets.append((schema, table, col["name"]))

        if not targets:
            return

        try:
            await conn.execute("SET LOCAL statement_timeout = '2s'")
        except Exception as exc:
            log.debug("Could not set statement_timeout for sampling: %s", exc)

        for schema, table, column in targets:
            qschema = _quote_ident(schema)
            qtable = _quote_ident(table)
            qcol = _quote_ident(column)
            try:
                rows = await conn.fetch(
                    f"SELECT {qcol} AS v, COUNT(*) AS n "
                    f"FROM {qschema}.{qtable} "
                    f"GROUP BY 1 ORDER BY n DESC NULLS LAST LIMIT 20"
                )
            except Exception as exc:
                log.debug(
                    "Value sampling failed for %s.%s.%s: %s",
                    schema, table, column, exc,
                )
                continue
            total = sum(int(r["n"]) for r in rows)
            if total <= 0:
                continue
            top_values = [
                {
                    "value": (None if r["v"] is None else str(r["v"])),
                    "count": int(r["n"]),
                    "frequency": round(int(r["n"]) / total, 4),
                }
                for r in rows
            ]
            tbl = tables.get((schema, table))
            if tbl is None:
                continue
            for col in tbl.columns:
                if col.get("name") == column:
                    col["top_values"] = top_values
                    # If LIMIT returned fewer rows than the cap, we saw the
                    # whole distribution — the LLM can trust this is exhaustive.
                    col["values_exhaustive"] = len(rows) < 20
                    break

    async def _enrich_with_indexes(self, conn: Any, tables: list[TableInfo]) -> None:
        clause, params = self._schema_filter_clause("schemaname")
        rows = await conn.fetch(
            f"""
            SELECT schemaname, tablename, indexname, indexdef
            FROM pg_indexes WHERE {clause}
            """,
            *params,
        )
        by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for r in rows:
            key = (r["schemaname"], r["tablename"])
            by_key.setdefault(key, []).append(
                {"name": r["indexname"], "definition": r["indexdef"]}
            )
        for tbl in tables:
            tbl.indexes = by_key.get((tbl.schema, tbl.name), [])

    # ---------- metrics ----------

    async def collect_metrics(self) -> list[MetricSnapshot]:
        metrics: list[MetricSnapshot] = []
        try:
            pool = await self._ensure_pool()
        except Exception as exc:
            log.warning("Connector %s metrics: connect failed: %s", self.name, exc)
            return metrics

        now = datetime.now(UTC)
        async with pool.acquire() as conn:
            await self._collect_table_metrics(conn, metrics, now)
            await self._collect_connection_metrics(conn, metrics, now)
            await self._collect_query_metrics(conn, metrics, now)
            await self._collect_health_ratios(conn, metrics, now)
        return metrics

    def _make_metric(
        self,
        metric_name: str,
        value: float,
        labels: dict[str, str],
        collected_at: datetime,
    ) -> MetricSnapshot:
        return MetricSnapshot(
            connector_name=self.name,
            metric_name=metric_name,
            value=float(value),
            labels=dict(labels),
            collected_at=collected_at,
        )

    async def _collect_table_metrics(
        self, conn: Any, metrics: list[MetricSnapshot], now: datetime
    ) -> None:
        try:
            clause, params = self._schema_filter_clause("schemaname")
            rows = await conn.fetch(
                f"""
                SELECT schemaname, relname, n_live_tup, n_tup_ins, n_tup_upd, n_tup_del,
                       n_dead_tup
                FROM pg_stat_user_tables
                WHERE {clause}
                """,
                *params,
            )
        except Exception as exc:
            log.debug("pg_stat_user_tables not available: %s", exc)
            return

        for r in rows:
            schema = r["schemaname"]
            table = r["relname"]
            key = (schema, table)
            labels = {"schema": schema, "table": table}
            row_count = int(r["n_live_tup"] or 0)
            inserts = int(r["n_tup_ins"] or 0)
            updates = int(r["n_tup_upd"] or 0)
            deletes = int(r["n_tup_del"] or 0)
            dead = int(r["n_dead_tup"] or 0)

            metrics.append(self._make_metric("table_row_count", row_count, labels, now))

            prev = self._previous_table_stats.get(key)
            if prev is not None:
                # Counter-reset detection: pg_stat_* counters are cumulative
                # and reset to 0 on DB restart. If the current value is less
                # than the previous value, treat it as a reset and skip the
                # delta for this cycle instead of emitting a massive negative
                # spike that would trigger a false critical alert.
                if (
                    inserts < prev["ins"]
                    or updates < prev["upd"]
                    or deletes < prev["del"]
                ):
                    log.info(
                        "Counter reset detected on %s.%s — skipping delta",
                        schema,
                        table,
                    )
                else:
                    metrics.append(
                        self._make_metric(
                            "table_inserts", inserts - prev["ins"], labels, now
                        )
                    )
                    metrics.append(
                        self._make_metric(
                            "table_updates", updates - prev["upd"], labels, now
                        )
                    )
                    metrics.append(
                        self._make_metric(
                            "table_deletes", deletes - prev["del"], labels, now
                        )
                    )
            self._previous_table_stats[key] = {
                "ins": inserts,
                "upd": updates,
                "del": deletes,
            }

            total = row_count + dead
            if total > 0:
                metrics.append(
                    self._make_metric(
                        "dead_tuple_ratio", dead / total, labels, now
                    )
                )

    async def _collect_connection_metrics(
        self, conn: Any, metrics: list[MetricSnapshot], now: datetime
    ) -> None:
        try:
            row = await conn.fetchrow(
                "SELECT count(*) AS active FROM pg_stat_activity WHERE state = 'active'"
            )
            if row is not None:
                metrics.append(
                    self._make_metric("active_connections", int(row["active"]), {}, now)
                )
        except Exception as exc:
            log.debug("active_connections unavailable: %s", exc)

        try:
            row = await conn.fetchrow(
                "SELECT count(*) AS blocked FROM pg_stat_activity WHERE wait_event_type = 'Lock'"
            )
            if row is not None:
                metrics.append(
                    self._make_metric("blocked_queries", int(row["blocked"]), {}, now)
                )
        except Exception as exc:
            log.debug("blocked_queries unavailable: %s", exc)

    async def _collect_query_metrics(
        self, conn: Any, metrics: list[MetricSnapshot], now: datetime
    ) -> None:
        try:
            row = await conn.fetchrow(
                """
                SELECT count(*) AS slow FROM pg_stat_activity
                WHERE state = 'active'
                  AND now() - query_start > interval '30 seconds'
                """
            )
            if row is not None:
                metrics.append(
                    self._make_metric(
                        "long_running_queries", int(row["slow"]), {}, now
                    )
                )
        except Exception as exc:
            log.debug("long_running_queries unavailable: %s", exc)

    async def _collect_health_ratios(
        self, conn: Any, metrics: list[MetricSnapshot], now: datetime
    ) -> None:
        try:
            row = await conn.fetchrow(
                """
                SELECT
                    sum(blks_hit)::float AS hits,
                    sum(blks_read)::float AS reads
                FROM pg_stat_database
                """
            )
            if row is not None:
                hits = float(row["hits"] or 0)
                reads = float(row["reads"] or 0)
                if hits + reads > 0:
                    metrics.append(
                        self._make_metric(
                            "cache_hit_ratio", hits / (hits + reads), {}, now
                        )
                    )
        except Exception as exc:
            log.debug("cache_hit_ratio unavailable: %s", exc)

    # ---------- changes ----------

    async def get_recent_changes(self, since: datetime) -> list[ChangeEvent]:
        # Schema diffs are surfaced via the discovery engine, not here.
        return []

    # ---------- health ----------

    async def health_check(self) -> HealthStatus:
        start = time.perf_counter()
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
        except Exception as exc:
            return HealthStatus(
                connector_name=self.name,
                healthy=False,
                message=f"{type(exc).__name__}: {exc}",
            )
        latency = (time.perf_counter() - start) * 1000.0
        return HealthStatus(
            connector_name=self.name,
            healthy=True,
            latency_ms=latency,
            message="ok",
        )

    def required_permissions(self) -> list[str]:
        return [
            "Read access to information_schema",
            "Read access to pg_stat_user_tables, pg_stat_activity, pg_stat_database",
            "Read access to pg_indexes",
        ]
