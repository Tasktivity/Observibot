# Observibot Quickstart

Get Observibot connected to your systems and the first agent running in
five minutes.

> New here? [VISION.md](VISION.md) explains what Observibot is at a
> conceptual level. This doc gets you running the SRE agent (the first
> agent shipped with the platform).

## 1. Prerequisites

- **Python 3.11+** (3.12 recommended), OR
- **Docker** + Docker Compose

You'll also need credentials for at least one connector. The most common
combo is Supabase + Railway:

| Credential | Where to find it |
|---|---|
| `SUPABASE_DB_URL` | Supabase Dashboard → Project Settings → Database → Connection string (use the **direct** connection on port `5432`, not the `6543` Supavisor port). |
| `RAILWAY_API_TOKEN` | Railway Dashboard → Account Settings → Tokens → Create Token. |
| `RAILWAY_PROJECT_ID` | Railway Dashboard → Project → Settings → General → Project ID. |
| `ANTHROPIC_API_KEY` *(or `OPENAI_API_KEY`)* | https://console.anthropic.com/ — required only if you want LLM-powered insights. |
| `NTFY_TOPIC_URL` *(optional)* | A unique ntfy topic, e.g. `https://ntfy.sh/your-private-topic`. |
| `SLACK_WEBHOOK_URL` *(optional)* | A Slack incoming webhook URL. |

Support for additional platforms (AWS, GCP, Azure, GitLab, Fly.io,
Render, and others) is planned — see
[phases/ROADMAP.md](phases/ROADMAP.md).

## 2. Install

### Python (recommended for local dev)

```bash
git clone https://github.com/YOUR_USERNAME/Observibot.git
cd Observibot
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Docker

```bash
git clone https://github.com/YOUR_USERNAME/Observibot.git
cd Observibot
cp .env.example .env  # then edit with real credentials (see below)
docker compose up --build
```

## 3. Create a `.env` file

```bash
ANTHROPIC_API_KEY=sk-ant-...
SUPABASE_DB_URL=postgresql://postgres:PASSWORD@db.PROJECT.supabase.co:5432/postgres
RAILWAY_API_TOKEN=...
RAILWAY_PROJECT_ID=...
NTFY_TOPIC_URL=https://ntfy.sh/your-private-topic
```

`observibot` automatically loads `.env` from the current working directory at startup.

## 4. Initialize the config

```bash
observibot init
```

This creates `config/observibot.yaml` from the bundled template and prints a
table of every `${ENV_VAR}` reference along with whether each is currently set.

## 5. Verify connectivity

```bash
observibot health
```

You should see a green "YES" row per connector. Any red row tells you exactly
which credential or permission is missing.

## 6. Run a one-shot discovery

```bash
observibot discover
```

This scans every connector, builds a unified `SystemModel`, and stores a
snapshot. On subsequent runs you'll see a "Changes since last discovery" panel.

## 7. Test your alert channels

```bash
observibot test-alert
```

Sends a synthetic info-level alert through every configured channel and reports
per-channel success or failure. Run this **before** starting the daemon —
debugging a misconfigured webhook at 3am is no fun.

## 8. Start continuous monitoring

```bash
observibot run
```

Observibot will:

- Run an initial discovery + collection
- Schedule collection every 5 minutes, analysis every 30 minutes, discovery every hour
- Expose `http://localhost:8080/health` for orchestrators
- Detect anomalies using time-aware MAD-based statistics
- Aggregate burst incidents into a single alert
- Fall back to deterministic alerts if the LLM is unavailable

Stop with `Ctrl-C`. The daemon handles `SIGTERM` cleanly so it's safe to run
under systemd, Docker, or other process supervisors.

## 9. Optional: Enable agentic chat queries against your database

The web dashboard includes an agentic chat that can answer questions about your
application's data (e.g., "How many users are there?") by running read-only SQL
against your production database. This is disabled by default.

To enable it, set `chat.enable_app_queries: true` in your config. Before
enabling, verify that the database role can actually read rows from your tables
— some platforms enforce row-level access policies that silently return empty
results even when schema-level `SELECT` is granted. See
[`CONFIGURATION.md`](CONFIGURATION.md) for details and
[`architecture/CONNECTORS.md`](architecture/CONNECTORS.md) for
platform-specific setup.

## 10. Optional: ntfy push notifications

ntfy.sh is a convenient way to get push notifications on your phone for free.

1. Install the ntfy app on your phone (iOS/Android).
2. Subscribe to a unique topic name like `obs-yourname-prod-q3a2`.
3. Add to `.env`: `NTFY_TOPIC_URL=https://ntfy.sh/obs-yourname-prod-q3a2`
4. Add to `config/observibot.yaml` under `alerting.channels`:
   ```yaml
   - type: ntfy
     url: ${NTFY_TOPIC_URL}
     severity_filter: [critical, warning]
   ```
5. Run `observibot test-alert` to verify.

## Next steps

- Read [`CONFIGURATION.md`](CONFIGURATION.md) for the full list of tunables.
- Read [`DEPLOYMENT.md`](DEPLOYMENT.md) for Docker / production setup.
- Read [`architecture/CONNECTORS.md`](architecture/CONNECTORS.md) for
  connector permission requirements.
- Read [`VISION.md`](VISION.md) for where the platform is heading beyond
  the first SRE agent.
