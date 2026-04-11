"""Railway PaaS connector via the Railway public GraphQL API."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
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


class RailwayConnector(BaseConnector):
    """Read-only Railway connector."""

    type = "railway"

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        super().__init__(name=name, config=config)
        self.api_token: str | None = config.get("api_token")
        self.project_id: str | None = config.get("project_id")
        self._client: httpx.AsyncClient | None = None
        self._max_retries = int(config.get("max_retries", 3))

    def get_capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            capabilities=(
                Capability.DISCOVERY
                | Capability.CHANGES
                | Capability.HEALTH
            ),
            requires_elevated_role=False,
            has_rate_limits=True,
            notes=[
                "Resource metrics (CPU/memory/network) are not exposed by the "
                "Railway public GraphQL API in V1 — use Railway's dashboard for those.",
                "Deploy events and service topology are fully supported.",
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

    async def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        client = await self._ensure_client()
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = await client.post(
                    "", json={"query": query, "variables": variables}
                )
                if resp.status_code == 429:
                    raise httpx.HTTPStatusError(
                        "rate limited", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                payload = resp.json()
                if "errors" in payload and payload["errors"]:
                    raise RuntimeError(f"GraphQL errors: {payload['errors']}")
                return payload.get("data") or {}
            except Exception as exc:
                last_exc = exc
                backoff = 2**attempt
                log.debug("Railway GraphQL attempt %s failed: %s", attempt + 1, exc)
                await asyncio.sleep(backoff)
        assert last_exc is not None
        raise last_exc

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
        services = (data.get("project") or {}).get("services") or {}
        service_count = len(services.get("edges") or [])
        metrics.append(
            MetricSnapshot(
                connector_name=self.name,
                metric_name="service_count",
                value=float(service_count),
                labels={},
                collected_at=now,
            )
        )
        return metrics

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
