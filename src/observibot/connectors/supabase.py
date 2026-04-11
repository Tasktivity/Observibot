"""Supabase connector — extends the PostgreSQL connector with Supabase-specific
schema exclusions and Row-Level-Security policy discovery.
"""
from __future__ import annotations

import logging
from typing import Any

from observibot.connectors.base import Capability, ConnectorCapabilities
from observibot.connectors.postgresql import PostgreSQLConnector
from observibot.core.models import SystemFragment

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


class SupabaseConnector(PostgreSQLConnector):
    """Connector for Supabase-hosted PostgreSQL databases."""

    type = "supabase"
    extra_excluded_schemas = SUPABASE_INTERNAL_SCHEMAS

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        super().__init__(name=name, config=config)

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
                "Requires direct Postgres connection (port 5432) for accurate "
                "pg_stat_activity — Supavisor shows pooler sessions, not app sessions.",
                "Full metric coverage needs pg_monitor or pg_read_all_stats.",
            ],
        )

    async def discover(self) -> SystemFragment:
        fragment = await super().discover()
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                await self._enrich_with_rls(conn, fragment)
        except Exception as exc:
            log.debug("RLS discovery failed: %s", exc)
            fragment.errors.append(f"rls: {exc}")
        return fragment

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
