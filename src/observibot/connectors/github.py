"""GitHub connector — optional source code access and change detection.

Strictly optional: Observibot must boot cleanly when this connector is absent
or disabled. Uses httpx for GitHub REST API with conditional requests and
rate-limit backoff.
"""
from __future__ import annotations

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
    SystemFragment,
)

log = logging.getLogger(__name__)

API_BASE = "https://api.github.com"
TIMEOUT_SECONDS = 10.0
MAX_CONSECUTIVE_FAILURES = 3
BACKOFF_INTERVAL_SECONDS = 3600


class GitHubConnector(BaseConnector):
    """Read-only connector for GitHub repositories."""

    type = "github"

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        super().__init__(name=name, config=config)
        self.token: str = config.get("token", "")
        self.repo: str = config.get("repo", "")
        self.branch: str = config.get("branch", "main")
        self.poll_interval: int = int(config.get("poll_interval_seconds", 900))
        self.local_clone_path: str = config.get("local_clone_path", "")
        self.cloud_extraction: bool = bool(config.get("cloud_extraction", False))

        self._client: httpx.AsyncClient | None = None
        self._etags: dict[str, str] = {}
        self._last_known_sha: str | None = None
        self._consecutive_failures: int = 0
        self._backed_off_until: datetime | None = None

    def get_capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            capabilities=(
                Capability.DISCOVERY
                | Capability.CHANGES
                | Capability.HEALTH
                | Capability.CODE_ACCESS
                | Capability.CODE_CHANGES
            ),
            has_rate_limits=True,
            notes=["GitHub API rate limit: 5000 req/hour with PAT"],
        )

    async def connect(self) -> None:
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            headers=headers,
            timeout=TIMEOUT_SECONDS,
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _should_back_off(self) -> bool:
        if self._backed_off_until is None:
            return False
        if datetime.now(UTC) < self._backed_off_until:
            return True
        self._backed_off_until = None
        self._consecutive_failures = 0
        return False

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self._backed_off_until = datetime.now(UTC) + timedelta(
                seconds=BACKOFF_INTERVAL_SECONDS
            )
            log.warning(
                "GitHub connector: %d consecutive failures, "
                "backing off until %s",
                self._consecutive_failures,
                self._backed_off_until.isoformat(),
            )

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._backed_off_until = None

    async def _api_get(self, path: str) -> httpx.Response | None:
        """GET with ETag conditional requests and rate-limit handling."""
        if self._client is None:
            await self.connect()

        if self._should_back_off():
            return None

        headers: dict[str, str] = {}
        if path in self._etags:
            headers["If-None-Match"] = self._etags[path]

        try:
            assert self._client is not None
            resp = await self._client.get(path, headers=headers)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            log.warning("GitHub API timeout/connect error: %s", exc)
            self._record_failure()
            return None

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "60"))
            self._backed_off_until = datetime.now(UTC) + timedelta(seconds=retry_after)
            log.warning("GitHub rate limited, backing off %ds", retry_after)
            return None

        if resp.status_code == 304:
            self._record_success()
            return resp

        if resp.status_code >= 400:
            log.warning("GitHub API error %d on %s", resp.status_code, path)
            self._record_failure()
            return None

        if "ETag" in resp.headers:
            self._etags[path] = resp.headers["ETag"]

        self._record_success()
        return resp

    async def discover(self) -> SystemFragment:
        fragment = SystemFragment(
            connector_name=self.name,
            connector_type=self.type,
        )
        if not self.repo:
            fragment.errors.append("No repository configured")
            return fragment

        resp = await self._api_get(f"/repos/{self.repo}")
        if resp is None or resp.status_code == 304:
            return fragment

        try:
            repo_data = resp.json()
            fragment.services = []

            from observibot.core.models import ServiceInfo
            fragment.services.append(ServiceInfo(
                name=f"github:{self.repo}",
                type="repository",
                environment=self.branch,
                status="active" if not repo_data.get("archived") else "archived",
                metadata={
                    "default_branch": repo_data.get("default_branch", "main"),
                    "language": repo_data.get("language"),
                    "size_kb": repo_data.get("size"),
                    "open_issues": repo_data.get("open_issues_count", 0),
                },
            ))
        except Exception as exc:
            fragment.errors.append(f"repo metadata: {exc}")

        return fragment

    async def collect_metrics(self) -> list[MetricSnapshot]:
        return []

    async def get_recent_changes(self, since: datetime) -> list[ChangeEvent]:
        if not self.repo:
            return []

        since_str = since.isoformat().replace("+00:00", "Z")
        resp = await self._api_get(
            f"/repos/{self.repo}/commits?sha={self.branch}&since={since_str}&per_page=30"
        )
        if resp is None or resp.status_code == 304:
            return []

        events: list[ChangeEvent] = []
        try:
            commits = resp.json()
            for commit in commits:
                sha = commit.get("sha", "")[:8]
                msg = (commit.get("commit", {}).get("message", "") or "").split("\n")[0]
                author = commit.get("commit", {}).get("author", {}).get("name", "unknown")
                date_str = (
                    commit.get("commit", {}).get("author", {}).get("date")
                    or datetime.now(UTC).isoformat()
                )
                events.append(ChangeEvent(
                    connector_name=self.name,
                    event_type="commit",
                    summary=f"{sha} {msg}",
                    details={
                        "sha": commit.get("sha", ""),
                        "author": author,
                        "message": msg,
                        "url": commit.get("html_url", ""),
                    },
                    occurred_at=datetime.fromisoformat(
                        date_str.replace("Z", "+00:00")
                    ),
                ))
                if not self._last_known_sha:
                    self._last_known_sha = commit.get("sha", "")
        except Exception as exc:
            log.warning("Failed to parse GitHub commits: %s", exc)

        return events

    async def health_check(self) -> HealthStatus:
        if not self.repo:
            return HealthStatus(
                connector_name=self.name,
                healthy=False,
                message="No repository configured",
            )

        start = time.perf_counter()
        resp = await self._api_get(f"/repos/{self.repo}")
        latency = (time.perf_counter() - start) * 1000

        if resp is None:
            status = "degraded" if self._consecutive_failures > 0 else "unavailable"
            return HealthStatus(
                connector_name=self.name,
                healthy=False,
                latency_ms=latency,
                message=f"GitHub: {status}",
            )

        return HealthStatus(
            connector_name=self.name,
            healthy=True,
            latency_ms=latency,
            message="ok",
        )

    def required_permissions(self) -> list[str]:
        return [
            "Read access to repository contents",
            "Read access to commits and pull requests",
        ]

    async def get_file_content(
        self, path: str, ref: str | None = None,
    ) -> str:
        """Retrieve a single file's content from the repository."""
        ref = ref or self.branch
        resp = await self._api_get(
            f"/repos/{self.repo}/contents/{path}?ref={ref}"
        )
        if resp is None or resp.status_code in (304, 404):
            return ""
        import base64
        data = resp.json()
        content = data.get("content", "")
        encoding = data.get("encoding", "")
        if encoding == "base64":
            return base64.b64decode(content).decode("utf-8", errors="replace")
        return content

    async def get_changed_files(self, since_sha: str) -> list[dict]:
        """Get files changed between since_sha and current branch HEAD."""
        resp = await self._api_get(
            f"/repos/{self.repo}/compare/{since_sha}...{self.branch}"
        )
        if resp is None or resp.status_code == 304:
            return []
        data = resp.json()
        return [
            {
                "filename": f.get("filename", ""),
                "status": f.get("status", ""),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
            }
            for f in data.get("files", [])
        ]

    async def list_files(
        self, path: str = "", ref: str | None = None,
    ) -> list[str]:
        """List files in a directory of the repository."""
        ref = ref or self.branch
        resp = await self._api_get(
            f"/repos/{self.repo}/contents/{path}?ref={ref}"
        )
        if resp is None or resp.status_code == 304:
            return []
        data = resp.json()
        if not isinstance(data, list):
            return []
        return [item.get("path", "") for item in data if item.get("type") == "file"]
