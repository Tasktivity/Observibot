"""Step 3.4 Tier 3 live verification harness.

Forces one diagnostic cycle end-to-end against the live Store, live
AppDatabasePool, and live Anthropic provider. Uses a synthetic critical
anomaly on a real monitored table so Call A picks tables the LLM
knows exist and Call B runs real SELECT queries against the app DB.

This does NOT replace the naturally-fired detector path — it exercises
the identical analyzer + monitor code with a controlled trigger so
Tier 3 evidence can be collected without waiting for the detector's
sustained-interval state to rebuild after a restart.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from observibot.agent.analyzer import Analyzer
from observibot.agent.llm_provider import AnthropicProvider
from observibot.alerting.base import AlertManager
from observibot.core.anomaly import Anomaly
from observibot.core.app_db import AppDatabasePool
from observibot.core.config import load_config
from observibot.core.monitor import build_monitor_loop
from observibot.core.store import Store


async def main() -> None:
    cfg = load_config()
    print(f"config: diagnostics.enabled={cfg.monitor.diagnostics.enabled}")

    async with Store(cfg.store.path) as store:
        app_db = None
        for cc in cfg.connectors:
            if cc.type in ("supabase", "postgresql"):
                dsn = cc.options.get("connection_string")
                if dsn:
                    app_db = AppDatabasePool(
                        dsn=dsn,
                        max_size=cfg.chat.app_db_max_connections,
                        statement_timeout_ms=cfg.chat.statement_timeout_ms,
                    )
                    await app_db.connect()
                    break

        if app_db is None:
            print("FATAL: no application DB configured")
            return

        provider = AnthropicProvider(
            model=cfg.llm.model,
            api_key=cfg.llm.api_key or os.getenv("OBSERVIBOT_ANTHROPIC_API_KEY", ""),
            max_tokens_per_cycle=cfg.llm.max_tokens_per_cycle,
            temperature=cfg.llm.temperature,
            daily_token_budget=cfg.llm.daily_token_budget,
        )
        analyzer = Analyzer(provider=provider, store=store)

        loop = build_monitor_loop(
            config=cfg, connectors=[], store=store,
            analyzer=analyzer, alert_manager=AlertManager(channels=[]),
            lockfile_path=None, health_host=None,
        )
        loop._cached_model = await store.get_latest_system_snapshot()
        loop._app_db = app_db

        if loop._cached_model is None:
            print("FATAL: no system model snapshot on file")
            return

        tables = {t.name for t in loop._cached_model.tables}
        print(f"system model: {len(loop._cached_model.tables)} tables")
        target_table = "ai_query_history" if "ai_query_history" in tables else sorted(tables)[0]
        print(f"target table: {target_table}")

        # A critical rolling-baseline anomaly → passes the cold-start gate.
        anomaly = Anomaly(
            metric_name="table_row_count",
            connector_name="taskgator-db",
            labels={"schema": "public", "table": target_table},
            value=9999.0,
            median=8000.0,
            mad=5.0,
            modified_z=15.0,
            absolute_diff=1999.0,
            severity="critical",
            direction="spike",
            consecutive_count=5,
            detected_at=datetime.now(UTC),
            sample_count=48,
            baseline_source="rolling",
        )

        print("triggering analysis with synthetic critical anomaly...")
        start = datetime.now(UTC)
        insights = await loop.trigger_analysis([anomaly])
        elapsed = (datetime.now(UTC) - start).total_seconds()
        print(f"trigger_analysis returned {len(insights)} insight(s) in {elapsed:.1f}s")

        for ins in insights:
            print("---INSIGHT---")
            print(f"  id={ins.id} severity={ins.severity} title={ins.title!r}")
            ev = ins.evidence or {}
            diags = ev.get("diagnostics") or []
            print(f"  diagnostics: {len(diags)}")
            for d in diags:
                print(
                    f"    - error={d.get('error')!r} row_count={d.get('row_count')} "
                    f"hypothesis={d.get('hypothesis')[:80]!r}"
                )
                print(f"      sql: {d.get('sql')[:140]}")

        diag_events = await store.get_events(event_type="diagnostic_run", limit=5)
        skipped_events = await store.get_events(event_type="diagnostic_skipped", limit=5)
        timeout_events = await store.get_events(event_type="diagnostic_timeout", limit=5)
        print("\n---EVENTS---")
        print(f"diagnostic_run: {len(diag_events)}")
        for e in diag_events[:3]:
            print(f"  {e['occurred_at']} run_id={e['run_id']}: {e['summary']}")
        print(f"diagnostic_skipped: {len(skipped_events)}")
        for e in skipped_events[:3]:
            print(f"  {e['occurred_at']} run_id={e['run_id']}: {e['summary']}")
        print(f"diagnostic_timeout: {len(timeout_events)}")
        for e in timeout_events[:3]:
            print(f"  {e['occurred_at']} run_id={e['run_id']}: {e['summary']}")

        # Now re-trigger with the identical anomaly — cooldown cache must
        # prevent a second Call A on the running instance.
        print("\n--- second trigger (cooldown test) ---")
        start = datetime.now(UTC)
        await loop.trigger_analysis([
            Anomaly(**{**anomaly.__dict__, "detected_at": datetime.now(UTC)})
        ])
        elapsed = (datetime.now(UTC) - start).total_seconds()
        print(f"second trigger completed in {elapsed:.1f}s")
        skipped2 = await store.get_events(event_type="diagnostic_skipped", limit=5)
        print(f"diagnostic_skipped after 2nd trigger: {len(skipped2)}")
        for e in skipped2[:3]:
            print(f"  {e['occurred_at']} run_id={e['run_id']}: {e['summary']}")

        await app_db.close()
        print("\nTier 3 verification harness complete.")


if __name__ == "__main__":
    asyncio.run(main())
