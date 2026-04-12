# Observibot Configuration Reference

The full list of YAML keys, environment variables, and tunables.

`observibot` loads configuration in this order:

1. The path passed to `--config` / `-c`.
2. The `OBSERVIBOT_CONFIG` environment variable.
3. `./config/observibot.yaml`.
4. `~/.config/observibot/observibot.yaml`.

If none exists, Observibot starts with a default config that uses the mock LLM
provider and no connectors. `${VAR}` and `${VAR:-default}` placeholders inside
the YAML are resolved against the process environment at load time.

---

## Top-level keys

```yaml
llm: ...
connectors: [...]
monitor: ...
alerting: ...
store: ...
logging: ...
```

---

## `llm`

```yaml
llm:
  provider: anthropic        # mock | anthropic | openai
  model: claude-sonnet-4-20250514
  api_key: ${ANTHROPIC_API_KEY}
  max_tokens_per_cycle: 4000
  temperature: 0.2
  daily_token_budget: 200000
```

| Key | Type | Default | Description |
|---|---|---|---|
| `provider` | string | `mock` | One of `mock`, `anthropic`, `openai`. |
| `model` | string | `claude-sonnet-4-20250514` | Model identifier passed to the SDK. |
| `api_key` | string | `null` | Resolved from env. Required for non-mock providers. |
| `max_tokens_per_cycle` | int | `4000` | Hard cap on completion tokens per call. |
| `temperature` | float | `0.2` | LLM sampling temperature. |
| `daily_token_budget` | int | `200000` | Total tokens/day before BudgetExceededError. |

---

## `connectors`

A list of connectors. Each entry has `name`, `type`, and type-specific options.

### Supabase

```yaml
- name: my-db
  type: supabase
  connection_string: ${SUPABASE_DB_URL}   # use the direct port (5432), NOT pooler (6543)
  schemas: [public]                       # optional, defaults to all non-system
  exclude_tables: []                      # optional, list of bare table names to skip
```

### Generic PostgreSQL

```yaml
- name: my-pg
  type: postgresql
  connection_string: ${POSTGRES_URL}
  schemas: [public, app]
  exclude_tables: [audit_log, debug_events]
```

### Railway

```yaml
- name: my-infra
  type: railway
  api_token: ${RAILWAY_API_TOKEN}
  project_id: ${RAILWAY_PROJECT_ID}
  max_retries: 3                          # optional, default 3
```

> Railway's public GraphQL API does not currently expose CPU/memory resource
> metrics — Observibot's Railway connector is `DISCOVERY | CHANGES | HEALTH`
> only. Deploy events and project topology work fully.

---

## `monitor`

```yaml
monitor:
  collection_interval_seconds: 300
  analysis_interval_seconds: 1800
  discovery_interval_seconds: 3600
  mad_threshold: 3.0
  min_absolute_diff: 10.0
  sustained_intervals_warning: 2
  sustained_intervals_critical: 3
  baseline_window_hours: 24
  min_samples_for_baseline: 12
```

| Key | Default | Description |
|---|---|---|
| `collection_interval_seconds` | `300` | How often to poll connectors for metrics. |
| `analysis_interval_seconds` | `1800` | How often to ask the LLM for insights. |
| `discovery_interval_seconds` | `3600` | How often to re-scan schema/topology. |
| `mad_threshold` | `3.0` | Modified-z (MAD) threshold for "statistically anomalous." |
| `min_absolute_diff` | `10.0` | Minimum absolute deviation from the median to count. Prevents 1→5 false positives. |
| `sustained_intervals_warning` | `2` | Consecutive anomalous readings before "warning". |
| `sustained_intervals_critical` | `3` | Consecutive anomalous readings before "critical". |
| `baseline_window_hours` | `24` | Rolling window used to compute the MAD baseline. |
| `min_samples_for_baseline` | `12` | Minimum samples before any detection runs (cold start). |

### Tuning guidance

- **Noisy production tables (high churn):** raise `min_absolute_diff` so background noise doesn't trip alerts.
- **Slow-moving counters (e.g. `payments`):** lower `min_absolute_diff` so even a small absolute change is meaningful.
- **Want more conservative alerting:** raise `sustained_intervals_warning` to 3 and `sustained_intervals_critical` to 5.

---

## `alerting`

```yaml
alerting:
  aggregation_window_seconds: 30
  aggregation_min_incidents: 3
  rate_limit:
    max_alerts_per_hour: 10
    cooldown_seconds: 300
  channels:
    - type: slack
      webhook_url: ${SLACK_WEBHOOK_URL}
      severity_filter: [critical, warning]
    - type: ntfy
      url: ${NTFY_TOPIC_URL}
      severity_filter: [critical, warning, info]
    - type: webhook
      url: https://example.com/observibot
      method: POST
      headers:
        Authorization: Bearer ${MY_WEBHOOK_TOKEN}
      severity_filter: [critical]
```

| Key | Default | Description |
|---|---|---|
| `aggregation_window_seconds` | `30` | Buffer window for incident rollup. Set to `0` to disable. |
| `aggregation_min_incidents` | `3` | Minimum buffered insights before they become a single incident. |
| `rate_limit.max_alerts_per_hour` | `10` | Hard cap across all channels. |
| `rate_limit.cooldown_seconds` | `300` | Per-fingerprint cooldown to prevent dupes. |

### Channel types

| Type | Required options | Notes |
|---|---|---|
| `slack` | `webhook_url` | Sends a Slack Block Kit message. |
| `ntfy` | `url` | Sends to a ntfy.sh topic with priority based on severity. Use a unique topic. |
| `webhook` | `url` | Generic JSON POST. Supports `method`, `headers`. |

---

## `store`

```yaml
store:
  type: sqlite
  path: ./data/observibot.db
  retention:
    metrics_days: 30
    events_days: 90
    insights_days: 90
    max_snapshots: 10
```

The store uses SQLite with WAL mode. The retention job runs once every 24h and
trims rows older than the configured days. `max_snapshots` keeps the N most
recent `system_snapshots` rows and drops the rest.

---

## `chat`

```yaml
chat:
  enable_app_queries: false
  app_db_max_connections: 3
  statement_timeout_ms: 3000
  max_result_rows: 500
```

| Key | Type | Default | Description |
|---|---|---|---|
| `enable_app_queries` | bool | `false` | When `true`, the agentic chat can run read-only SQL against the monitored application database (the same DB configured in `connectors`). Disabled by default — enable only after verifying the database role has the required read access (see below). |
| `app_db_max_connections` | int | `3` | Maximum connections in the chat query pool. Kept small to avoid competing with the application's own traffic. |
| `statement_timeout_ms` | int | `3000` | Per-query timeout. Queries exceeding this are cancelled server-side. |
| `max_result_rows` | int | `500` | Maximum rows returned from a single chat query. |

### Database access requirements for chat queries

When `enable_app_queries` is `true`, Observibot opens a **separate read-only
connection pool** to the application database. The database role used for this
connection must be able to `SELECT` from the tables it needs to query.

On databases that enforce row-level access policies (e.g., PostgreSQL RLS,
Supabase RLS, or platform-specific access controls), a `SELECT` grant alone is
not sufficient — the role must also have a matching read policy on each table.
Without one, queries return zero rows instead of raising an error, which
produces silently wrong answers.

**Before enabling `enable_app_queries`, verify that the database role can
actually read rows** by running a simple `SELECT COUNT(*) FROM <table>` as
that role. If it returns 0 on a table you know has data, your platform's
access policies are likely blocking the role.

See [`architecture/CONNECTORS.md`](architecture/CONNECTORS.md) for
platform-specific setup.

---

## `logging`

```yaml
logging:
  level: INFO       # DEBUG | INFO | WARNING | ERROR
  format: text      # text | json
```

---

## Environment variables Observibot uses directly

| Variable | Used by | Purpose |
|---|---|---|
| `OBSERVIBOT_CONFIG` | config loader | Override config path. |
| `ANTHROPIC_API_KEY` | LLM provider | Anthropic credentials. |
| `OPENAI_API_KEY` | LLM provider | OpenAI credentials. |
| `SUPABASE_DB_URL` | Supabase connector | DB connection string. |
| `RAILWAY_API_TOKEN` | Railway connector | API token. |
| `RAILWAY_PROJECT_ID` | Railway connector | Project ID. |
| `SLACK_WEBHOOK_URL` | Slack channel | Webhook. |
| `NTFY_TOPIC_URL` | ntfy channel | Topic URL. |

Any additional `${VAR}` you reference in your YAML must be set in your shell
or in a `.env` file in the working directory.
