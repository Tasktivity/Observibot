# Observibot Deployment Guide

How to run Observibot in production. Three options, in increasing order of
"hands off":

1. **Local Python** — best for development and dogfooding.
2. **Docker / Docker Compose** — best for self-hosted servers.
3. **Railway** — best for "I just want it to run forever and update itself."

---

## 1. Local Python

```bash
git clone https://github.com/YOUR_USERNAME/Observibot.git
cd Observibot
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cp .env.example .env       # fill in real credentials
observibot init
observibot health
observibot run
```

Use `systemd`, `launchd`, or `pm2` to keep it alive across reboots. Example
systemd unit:

```ini
# /etc/systemd/system/observibot.service
[Unit]
Description=Observibot SRE Agent
After=network.target

[Service]
Type=simple
User=observibot
WorkingDirectory=/opt/observibot
EnvironmentFile=/opt/observibot/.env
ExecStart=/opt/observibot/.venv/bin/observibot run
Restart=on-failure
RestartSec=10
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now observibot
sudo journalctl -u observibot -f
```

The lockfile (`data/observibot.lock`) prevents two daemons from running at
once even if systemd misbehaves.

---

## 2. Docker / Docker Compose

The repository ships with a production-ready `Dockerfile` and `docker-compose.yaml`.

```bash
cp .env.example .env       # fill in real credentials
docker compose build
docker compose up -d
docker compose logs -f observibot
```

Key facts:

- Base image: `python:3.12-slim`.
- Entrypoint runs through `tini` so `SIGTERM` is forwarded cleanly to
  Observibot's signal handlers.
- Persistent volume `observibot-data` is mounted at `/app/data` and contains
  the SQLite database and the lockfile.
- Container exposes `:8080/health` for the compose healthcheck.
- All credentials come from environment variables — never bake secrets into
  the image.

### Environment variables for Docker

Put these in `.env` (referenced by `env_file:` in compose):

```bash
ANTHROPIC_API_KEY=sk-ant-...
SUPABASE_DB_URL=postgresql://postgres:PASSWORD@db.PROJECT.supabase.co:5432/postgres
RAILWAY_API_TOKEN=...
RAILWAY_PROJECT_ID=...
NTFY_TOPIC_URL=https://ntfy.sh/your-private-topic
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...   # optional
```

### Updating

```bash
git pull
docker compose build --no-cache
docker compose up -d
```

The lockfile is on the persistent volume, so the new container will see and
remove the stale one automatically.

---

## 3. Railway

Observibot ships a `railway.toml` and a `Dockerfile` so you can deploy it as a
Railway service in minutes.

### One-time setup

1. **Fork/clone** the Observibot repo to your own GitHub account.
2. In Railway, create a new project → "Deploy from GitHub repo" → pick your fork.
3. Railway detects `Dockerfile` automatically.
4. Add a **Volume** mounted at `/app/data` (Railway → Service → Settings → Volumes).
5. Add the environment variables under Service → Variables:
   - `ANTHROPIC_API_KEY`
   - `SUPABASE_DB_URL`
   - `RAILWAY_API_TOKEN`
   - `RAILWAY_PROJECT_ID`
   - `NTFY_TOPIC_URL` *(optional)*
   - `SLACK_WEBHOOK_URL` *(optional)*
6. Click **Deploy**.

Railway will:

- Build the Docker image.
- Start `observibot run`.
- Probe `/health` every 10s (configured in `railway.toml`).
- Restart on failure up to 3 times.
- Send `SIGTERM` on redeploy (handled cleanly).

### Verifying the deploy

In the Railway dashboard, open the service's **Deployments → Logs** tab. You
should see:

```
Monitor loop starting
Health endpoint listening on http://0.0.0.0:8080/health
Running discovery cycle
Running collection cycle
Collection cycle completed: ... metrics from ... connectors. Next cycle in 300s.
```

The first ntfy alert (or test alert via `observibot test-alert` from the
Railway shell) confirms the alerting path works.

---

## Persistent storage

Observibot writes:

- `data/observibot.db` — SQLite store (metrics, insights, snapshots).
- `data/observibot.db-wal` / `-shm` — WAL and shared-memory files.
- `data/observibot.lock` — single-writer lockfile.

In Docker and Railway, mount a volume at `/app/data`. Backing it up is as
simple as copying the directory while the daemon is stopped (or using
`sqlite3 .backup`).

---

## Health checks

`GET /health` returns:

```json
{"status": "ok", "version": "0.1.0"}
```

Use it for:

- Docker `HEALTHCHECK` (already in compose).
- Railway healthcheck (already in `railway.toml`).
- Kubernetes liveness/readiness probe.
- External uptime monitoring (UptimeRobot, Better Stack, etc.).

`GET /` returns a tiny JSON index pointing at `/health`.

---

## Logs

By default Observibot logs to stdout in plain text. Set
`logging.format: json` in `config/observibot.yaml` for structured logging
suitable for log aggregators (Datadog, Grafana Loki, etc.).

---

## Common issues

| Symptom | Likely cause | Fix |
|---|---|---|
| `Another Observibot instance is already running (PID …)` | Stale lockfile after a hard kill | `rm data/observibot.lock` then restart. |
| Health check 502 from Railway | Container not yet finished initial discovery | Increase `healthcheckTimeout` in `railway.toml`. |
| `Missing required environment variable 'SUPABASE_DB_URL'` | `.env` not loaded or var not set in Railway Variables | `observibot health` will tell you the exact var. |
| Slack alerts never arrive | Webhook URL wrong or channel rejecting messages | `observibot test-alert` to verify. |
| ntfy alerts arrive but Slack doesn't | Severity filter on the Slack channel excludes the test severity | `observibot test-alert --severity critical`. |
