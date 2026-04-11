# Observibot — Data Store Schema

SQLite for v1 (zero external deps), upgradeable to PostgreSQL.

## Tables

- **system_snapshots** — versioned SystemModel JSON + fingerprint
- **metric_snapshots** — time-series metric data, indexed on (connector, metric_name, collected_at)
- **change_events** — deploys, migrations, config changes
- **insights** — LLM-generated findings with severity, evidence, correlation
- **alert_history** — delivery log for all alerts sent
- **business_context** — user-provided context from onboarding interview
- **llm_usage** — token tracking for cost transparency
- **metric_baselines** — rolling mean/stddev for anomaly detection

## Retention Defaults

| Table | TTL |
|-------|-----|
| metric_snapshots | 30 days |
| change_events | 90 days |
| insights | 90 days |
| alert_history | 90 days |
| system_snapshots | last 10 |
| metric_baselines | overwritten in place |

Cleanup runs hourly as part of the monitor loop.
