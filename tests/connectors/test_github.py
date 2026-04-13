"""Tests for GitHubConnector with mocked httpx responses."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from observibot.connectors.base import Capability
from observibot.connectors.github import GitHubConnector


def _make_connector(**kwargs) -> GitHubConnector:
    config = {
        "token": "ghp_testtoken123",
        "repo": "owner/testrepo",
        "branch": "main",
        "poll_interval_seconds": 900,
        **kwargs,
    }
    return GitHubConnector(name="test-github", config=config)


def _mock_response(
    status_code: int = 200,
    json_data: dict | list | None = None,
    headers: dict | None = None,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.json.return_value = json_data or {}
    return resp


class TestCapabilities:
    def test_declares_correct_capabilities(self):
        conn = _make_connector()
        caps = conn.get_capabilities()
        assert caps.supports(Capability.DISCOVERY)
        assert caps.supports(Capability.CHANGES)
        assert caps.supports(Capability.HEALTH)
        assert caps.supports(Capability.CODE_ACCESS)
        assert caps.supports(Capability.CODE_CHANGES)
        assert not caps.supports(Capability.METRICS)
        assert caps.has_rate_limits is True


class TestDiscover:
    async def test_discover_returns_repo_metadata(self):
        conn = _make_connector()
        mock_client = AsyncMock()
        mock_client.get.return_value = _mock_response(
            json_data={
                "default_branch": "main",
                "language": "Python",
                "size": 5000,
                "open_issues_count": 3,
                "archived": False,
            },
            headers={"ETag": '"abc123"'},
        )
        conn._client = mock_client

        fragment = await conn.discover()
        assert len(fragment.services) == 1
        assert fragment.services[0].name == "github:owner/testrepo"
        assert fragment.services[0].status == "active"
        assert fragment.services[0].metadata["language"] == "Python"

    async def test_discover_handles_no_repo(self):
        conn = _make_connector(repo="")
        fragment = await conn.discover()
        assert "No repository configured" in fragment.errors

    async def test_discover_handles_api_failure(self):
        conn = _make_connector()
        mock_client = AsyncMock()
        mock_client.get.return_value = _mock_response(status_code=500)
        conn._client = mock_client

        fragment = await conn.discover()
        assert fragment.services == []


class TestRecentChanges:
    async def test_returns_commits(self):
        conn = _make_connector()
        mock_client = AsyncMock()
        mock_client.get.return_value = _mock_response(
            json_data=[
                {
                    "sha": "abc12345deadbeef",
                    "commit": {
                        "message": "Fix user query",
                        "author": {
                            "name": "Dev",
                            "date": "2026-04-12T10:00:00Z",
                        },
                    },
                    "html_url": "https://github.com/owner/repo/commit/abc123",
                },
            ],
            headers={"ETag": '"xyz789"'},
        )
        conn._client = mock_client

        since = datetime.now(UTC) - timedelta(hours=24)
        events = await conn.get_recent_changes(since)
        assert len(events) == 1
        assert events[0].event_type == "commit"
        assert "Fix user query" in events[0].summary
        assert events[0].details["author"] == "Dev"

    async def test_returns_empty_for_no_repo(self):
        conn = _make_connector(repo="")
        events = await conn.get_recent_changes(datetime.now(UTC))
        assert events == []


class TestConditionalRequests:
    async def test_etag_caching(self):
        conn = _make_connector()
        mock_client = AsyncMock()
        conn._client = mock_client

        mock_client.get.return_value = _mock_response(
            json_data={"default_branch": "main"},
            headers={"ETag": '"etag123"'},
        )
        await conn.discover()
        assert "/repos/owner/testrepo" in conn._etags

        mock_client.get.return_value = _mock_response(status_code=304)
        fragment = await conn.discover()
        call_headers = mock_client.get.call_args_list[-1].kwargs.get("headers", {})
        assert call_headers.get("If-None-Match") == '"etag123"'


class TestRateLimitBackoff:
    async def test_429_triggers_backoff(self):
        conn = _make_connector()
        mock_client = AsyncMock()
        mock_client.get.return_value = _mock_response(
            status_code=429, headers={"Retry-After": "60"},
        )
        conn._client = mock_client

        fragment = await conn.discover()
        assert conn._backed_off_until is not None

    async def test_consecutive_failures_trigger_backoff(self):
        conn = _make_connector()
        mock_client = AsyncMock()
        mock_client.get.return_value = _mock_response(status_code=500)
        conn._client = mock_client

        for _ in range(3):
            await conn.discover()

        assert conn._consecutive_failures >= 3
        assert conn._backed_off_until is not None


class TestHealthCheck:
    async def test_healthy_check(self):
        conn = _make_connector()
        mock_client = AsyncMock()
        mock_client.get.return_value = _mock_response(
            json_data={"id": 1}, headers={"ETag": '"h1"'},
        )
        conn._client = mock_client

        status = await conn.health_check()
        assert status.healthy is True
        assert status.latency_ms is not None

    async def test_unhealthy_no_repo(self):
        conn = _make_connector(repo="")
        status = await conn.health_check()
        assert status.healthy is False


class TestMonitorUnaffected:
    async def test_no_metrics(self):
        conn = _make_connector()
        metrics = await conn.collect_metrics()
        assert metrics == []


class TestConnectorRegistration:
    def test_github_in_registry(self):
        from observibot.connectors import get_connector
        conn = get_connector(
            name="test-gh", type="github",
            config={"token": "test", "repo": "owner/repo"},
        )
        assert conn.type == "github"

    def test_system_boots_without_github(self):
        """Config with no github section should work fine."""
        from observibot.core.config import load_config
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("llm:\n  provider: mock\nconnectors: []\n")
            f.flush()
            try:
                cfg = load_config(f.name)
                assert cfg.github.enabled is False
            finally:
                os.unlink(f.name)
