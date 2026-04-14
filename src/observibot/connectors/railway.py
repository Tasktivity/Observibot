"""Railway PaaS connector via the Railway public GraphQL API.

Collects service topology, deployment events, and (when available) per-service
resource metrics (CPU, memory, disk, network) via the GraphQL ``metrics`` query
or an optional user-deployed Prometheus exporter.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from observibot.connectors.base import (
    BaseConnector,
    Capability,
    ConnectorCapabilities,
)
from observibot.core.models import (
    ChangeEvent,
    HealthStatus,
    MetricSnapshot,
    ServiceInfo,
    SystemFragment,
)

log = logging.getLogger(__name__)

RAILWAY_GRAPHQL_URL = "https://backboard.railway.com/graphql/v2"


class PermanentGraphQLError(Exception):
    """Non-retryable GraphQL failure.

    Raised for HTTP 4xx (except 429) and for 200-OK responses containing
    ``errors`` that match schema-mismatch keywords. These will never succeed
    on retry with the same payload, so ``_graphql`` must raise immediately
    rather than burning the exponential-backoff budget.
    """

PROJECT_QUERY = """
query Project($id: String!) {
  project(id: $id) {
    id
    name
    services { edges { node { id name } } }
    environments { edges { node { id name } } }
  }
}
"""

DEPLOYMENTS_QUERY = """
query Deployments($projectId: String!, $limit: Int!) {
  deployments(input: {projectId: $projectId}, first: $limit) {
    edges {
      node {
        id
        status
        createdAt
        staticUrl
        service { id name }
        environment { id name }
      }
    }
  }
}
"""

# Railway V2 GraphQL metrics query for per-service resource usage.
# Returns time-series samples with { ts, value } for each measurement.
# `metrics` is a top-level query returning [MetricsResult!]! directly — there is
# no outer `measurements { ... }` wrapper. Each MetricsResult has a `measurement`
# (singular) field naming the metric. `startDate` is DateTime! (non-null) — passing
# `null` produces an HTTP 400 at parse time before GraphQL validation runs.
SERVICE_METRICS_QUERY = """
query ServiceMetrics(
  $serviceId: String!
  $environmentId: String!
  $startDate: DateTime!
) {
  metrics(
    serviceId: $serviceId
    environmentId: $environmentId
    measurements: [CPU_USAGE, MEMORY_USAGE_GB, DISK_USAGE_GB, NETWORK_RX_GB, NETWORK_TX_GB]
    startDate: $startDate
    sampleRateSeconds: 60
  ) {
    measurement
    values { ts value }
  }
}
"""


class RailwayConnector(BaseConnector):
    """Read-only Railway connector."""

    type = "railway"

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        super().__init__(name=name, config=config)
        self.api_token: str | None = config.get("api_token")
        self.project_id: str | None = config.get("project_id")
        options = config.get("options") or {}
        self._prometheus_endpoint: str | None = options.get("prometheus_endpoint")
        self._client: httpx.AsyncClient | None = None
        self._prom_client: httpx.AsyncClient | None = None
        self._max_retries = int(config.get("max_retries", 3))
        self._service_cache: dict[str, str] = {}  # id -> name
        self._environment_cache: dict[str, str] = {}  # id -> name
        self._graphql_metrics_available: bool | None = None

    def get_capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            capabilities=(
                Capability.DISCOVERY
                | Capability.METRICS
                | Capability.RESOURCE_METRICS
                | Capability.CHANGES
                | Capability.HEALTH
            ),
            requires_elevated_role=False,
            has_rate_limits=True,
            notes=[
                "Resource metrics via GraphQL API (CPU, memory, disk, network).",
                "Deploy events and service topology fully supported.",
                *(
                    ["Optional Prometheus exporter scraping configured."]
                    if self._prometheus_endpoint else []
                ),
            ],
        )

    async def connect(self) -> None:
        """Create the httpx client if credentials are present."""
        try:
            self._ensure_credentials()
        except ValueError as exc:
            log.info("Railway connector %s not connecting: %s", self.name, exc)
            return
        await self._ensure_client()

    def _ensure_credentials(self) -> tuple[str, str]:
        if not self.api_token:
            raise ValueError(
                f"Railway connector '{self.name}' is missing 'api_token' "
                "(set RAILWAY_API_TOKEN in your environment)"
            )
        if not self.project_id:
            raise ValueError(
                f"Railway connector '{self.name}' is missing 'project_id'"
            )
        return self.api_token, self.project_id

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            token, _ = self._ensure_credentials()
            self._client = httpx.AsyncClient(
                base_url=RAILWAY_GRAPHQL_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(20.0),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            finally:
                self._client = None
        if self._prom_client is not None:
            try:
                await self._prom_client.aclose()
            finally:
                self._prom_client = None

    async def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        client = await self._ensure_client()
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = await client.post(
                    "", json={"query": query, "variables": variables}
                )
                # 4xx other than 429 is permanent — malformed query, missing
                # auth, bad variable shape. Retrying the identical payload
                # wastes API budget. Surface the response body at INFO so
                # schema drift is diagnosable from a single log line.
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    body = self._safe_body(resp)
                    log.info(
                        "Railway GraphQL %s (permanent): %s",
                        resp.status_code, body,
                    )
                    raise PermanentGraphQLError(
                        f"HTTP {resp.status_code}: {body}"
                    )
                if resp.status_code == 429:
                    # Retryable — let the backoff loop handle it.
                    raise httpx.HTTPStatusError(
                        "rate limited", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                payload = resp.json()
                if "errors" in payload and payload["errors"]:
                    err_msg = f"GraphQL errors: {payload['errors']}"
                    if any(kw in err_msg for kw in self._SCHEMA_ERROR_KEYWORDS):
                        log.info(
                            "Railway GraphQL schema error (permanent): %s",
                            payload["errors"],
                        )
                        raise PermanentGraphQLError(err_msg)
                    raise RuntimeError(err_msg)
                return payload.get("data") or {}
            except PermanentGraphQLError:
                # Do not retry — caller handles the disable logic.
                raise
            except Exception as exc:
                last_exc = exc
                backoff = 2**attempt
                log.debug("Railway GraphQL attempt %s failed: %s", attempt + 1, exc)
                await asyncio.sleep(backoff)
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _safe_body(resp: httpx.Response) -> str:
        """Extract a compact response body for diagnostic logs.

        Truncates to 500 chars and strips whitespace. Never raises — falls
        back to ``<unreadable>`` if the body can't be decoded.
        """
        try:
            text = resp.text.strip()
            if len(text) > 500:
                return text[:500] + "..."
            return text
        except Exception:
            return "<unreadable>"

    async def discover(self) -> SystemFragment:
        fragment = SystemFragment(connector_name=self.name, connector_type=self.type)
        try:
            self._ensure_credentials()
        except Exception as exc:
            fragment.errors.append(str(exc))
            return fragment

        try:
            data = await self._graphql(PROJECT_QUERY, {"id": self.project_id})
        except Exception as exc:
            log.warning("Railway discovery failed: %s", exc)
            fragment.errors.append(f"project query: {exc}")
            return fragment

        project = data.get("project") or {}
        env_names = {
            edge["node"]["id"]: edge["node"]["name"]
            for edge in (project.get("environments") or {}).get("edges", [])
        }

        services: dict[str, ServiceInfo] = {}
        for edge in (project.get("services") or {}).get("edges", []):
            node = edge["node"]
            services[node["id"]] = ServiceInfo(
                name=node["name"],
                type="web",
                metadata={"id": node["id"]},
            )

        try:
            deploys = await self._graphql(
                DEPLOYMENTS_QUERY, {"projectId": self.project_id, "limit": 20}
            )
            for edge in (deploys.get("deployments") or {}).get("edges", []):
                node = edge["node"]
                service_node = node.get("service") or {}
                env_node = node.get("environment") or {}
                svc = services.get(service_node.get("id"))
                if svc is None:
                    continue
                if not svc.last_deploy_at:
                    svc.last_deploy_at = self._parse_timestamp(node.get("createdAt"))
                    svc.last_deploy_id = node.get("id")
                    svc.status = node.get("status")
                    svc.environment = env_node.get("name") or env_names.get(env_node.get("id"))
        except Exception as exc:
            log.debug("Railway deployments query failed: %s", exc)
            fragment.errors.append(f"deployments: {exc}")

        fragment.services = list(services.values())
        return fragment

    def _parse_timestamp(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            cleaned = value.replace("Z", "+00:00")
            return datetime.fromisoformat(cleaned)
        except ValueError:
            return None

    async def collect_metrics(self) -> list[MetricSnapshot]:
        metrics: list[MetricSnapshot] = []
        try:
            self._ensure_credentials()
        except Exception:
            return metrics
        try:
            data = await self._graphql(PROJECT_QUERY, {"id": self.project_id})
        except Exception as exc:
            log.debug("Railway metric query failed: %s", exc)
            return metrics

        now = datetime.now(UTC)
        project = data.get("project") or {}
        services_data = project.get("services") or {}
        service_edges = services_data.get("edges") or []
        metrics.append(
            MetricSnapshot(
                connector_name=self.name,
                metric_name="service_count",
                value=float(len(service_edges)),
                labels={},
                collected_at=now,
            )
        )

        # Cache service/environment mappings for resource metrics
        for edge in service_edges:
            node = edge["node"]
            self._service_cache[node["id"]] = node["name"]
        env_edges = (project.get("environments") or {}).get("edges", [])
        for edge in env_edges:
            node = edge["node"]
            self._environment_cache[node["id"]] = node["name"]

        # Path A: GraphQL resource metrics per service
        graphql_metrics = await self._collect_graphql_resource_metrics(now)
        metrics.extend(graphql_metrics)

        # Path B: Optional Prometheus exporter scraping
        if self._prometheus_endpoint:
            prom_metrics = await self._collect_prometheus_metrics(now)
            # Deduplicate: Prometheus metrics override GraphQL if present
            prom_names = {
                (m.metric_name, tuple(sorted(m.labels.items())))
                for m in prom_metrics
            }
            metrics = [
                m for m in metrics
                if (m.metric_name, tuple(sorted(m.labels.items())))
                not in prom_names
            ]
            metrics.extend(prom_metrics)

        return metrics

    # Railway GraphQL metric name → our normalized metric name
    _METRIC_NAME_MAP: dict[str, str] = {
        "CPU_USAGE": "service_cpu_usage",
        "MEMORY_USAGE_GB": "service_memory_usage_gb",
        "DISK_USAGE_GB": "service_disk_usage_gb",
        "NETWORK_RX_GB": "service_network_rx_gb",
        "NETWORK_TX_GB": "service_network_tx_gb",
    }

    # Error messages that indicate the API schema doesn't support metrics
    _SCHEMA_ERROR_KEYWORDS = ("Cannot query field", "Unknown field", "is not defined")

    async def _collect_graphql_resource_metrics(
        self, now: datetime,
    ) -> list[MetricSnapshot]:
        if self._graphql_metrics_available is False:
            return []

        # Refresh service + environment caches each cycle
        try:
            data = await self._graphql(PROJECT_QUERY, {"id": self.project_id})
            project = data.get("project") or {}
            self._service_cache = {
                e["node"]["id"]: e["node"]["name"]
                for e in (project.get("services") or {}).get("edges", [])
            }
            self._environment_cache = {
                e["node"]["id"]: e["node"]["name"]
                for e in (project.get("environments") or {}).get("edges", [])
            }
        except Exception as exc:
            log.debug("Failed to refresh Railway caches: %s", exc)
            if not self._service_cache:
                return []

        # Prefer "production" environment, fall back to first
        metrics: list[MetricSnapshot] = []
        target_env_id = None
        for env_id, env_name in self._environment_cache.items():
            if env_name.lower() == "production":
                target_env_id = env_id
                break
        if target_env_id is None:
            target_env_id = next(iter(self._environment_cache), None)
        if not target_env_id:
            return metrics

        # Pull 1 hour of history at 60s sample rate — matches collection interval
        # and provides a small rolling window for the anomaly detector's baseline.
        start_date = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

        for svc_id, svc_name in self._service_cache.items():
            try:
                data = await self._graphql(
                    SERVICE_METRICS_QUERY,
                    {
                        "serviceId": svc_id,
                        "environmentId": target_env_id,
                        "startDate": start_date,
                    },
                )
            except PermanentGraphQLError as exc:
                # 4xx or GraphQL schema error: Railway API has drifted from our
                # query shape. Disable metrics for the remaining lifetime of this
                # connector instance so we don't burn API budget on every cycle.
                log.info(
                    "Railway GraphQL metrics unsupported (%s). "
                    "Disabling for this connector instance.", exc,
                )
                self._graphql_metrics_available = False
                return metrics
            except Exception as exc:
                # Transient error (5xx, timeout, network) — skip this service,
                # continue to next. Do NOT disable: next cycle may succeed.
                log.debug(
                    "Railway resource metrics failed for %s: %s",
                    svc_name, exc,
                )
                continue

            self._graphql_metrics_available = True
            # `metrics` is now a flat list of MetricsResult — no outer wrapper.
            measurements = data.get("metrics") or []
            for m in measurements:
                raw_name = m.get("measurement", "")
                metric_name = self._METRIC_NAME_MAP.get(raw_name, raw_name)
                values = m.get("values") or []
                if not values:
                    continue
                # Take the most recent sample
                latest = values[-1]
                val = latest.get("value")
                if val is None:
                    continue
                metrics.append(
                    MetricSnapshot(
                        connector_name=self.name,
                        metric_name=metric_name,
                        value=float(val),
                        labels={"service": svc_name},
                        collected_at=now,
                    )
                )

        return metrics

    async def _collect_prometheus_metrics(
        self, now: datetime,
    ) -> list[MetricSnapshot]:
        if not self._prometheus_endpoint:
            return []

        from observibot.connectors.prometheus_parser import (
            prometheus_to_snapshots,
        )

        if self._prom_client is None:
            self._prom_client = httpx.AsyncClient(timeout=15.0)

        try:
            resp = await self._prom_client.get(self._prometheus_endpoint)
            resp.raise_for_status()
        except Exception as exc:
            log.warning(
                "Railway Prometheus endpoint unreachable: %s", exc,
            )
            return []

        return prometheus_to_snapshots(
            resp.text,
            connector_name=self.name,
            collected_at=now,
        )

    async def get_recent_changes(self, since: datetime) -> list[ChangeEvent]:
        events: list[ChangeEvent] = []
        try:
            self._ensure_credentials()
        except Exception:
            return events
        try:
            data = await self._graphql(
                DEPLOYMENTS_QUERY, {"projectId": self.project_id, "limit": 20}
            )
        except Exception as exc:
            log.debug("Railway changes query failed: %s", exc)
            return events

        for edge in (data.get("deployments") or {}).get("edges", []):
            node = edge["node"]
            ts = self._parse_timestamp(node.get("createdAt"))
            if ts is None or ts < since:
                continue
            svc = (node.get("service") or {}).get("name", "?")
            events.append(
                ChangeEvent(
                    connector_name=self.name,
                    event_type="deploy",
                    summary=f"{svc} deploy {node.get('status', 'UNKNOWN')}",
                    details={
                        "deployment_id": node.get("id"),
                        "status": node.get("status"),
                        "service": svc,
                    },
                    occurred_at=ts,
                )
            )
        return events

    async def health_check(self) -> HealthStatus:
        start = time.perf_counter()
        try:
            self._ensure_credentials()
            await self._graphql(PROJECT_QUERY, {"id": self.project_id})
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
            "Railway API token with read access to the project",
            "Read access to project services and deployments",
        ]
