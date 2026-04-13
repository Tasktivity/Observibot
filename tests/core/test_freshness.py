"""Tests for code intelligence freshness tracking."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from observibot.core.code_intelligence.service import CodeKnowledgeService
from observibot.core.store import Store


@pytest.fixture
async def fresh_store(tmp_path: Path):
    path = tmp_path / "fresh_store.db"
    async with Store(path) as store:
        yield store


class TestFreshnessStatus:
    async def test_unavailable_when_never_indexed(self, fresh_store: Store):
        service = CodeKnowledgeService(fresh_store)
        status = await service.get_freshness_status()
        assert status["status"] == "unavailable"
        assert status["last_indexed_commit"] is None

    async def test_current_when_recently_indexed(self, fresh_store: Store):
        now = datetime.now(UTC)
        await fresh_store.set_code_intelligence_meta(
            "last_indexed_commit", "abc123",
        )
        await fresh_store.set_code_intelligence_meta(
            "last_index_time", now.isoformat(),
        )
        service = CodeKnowledgeService(fresh_store)
        status = await service.get_freshness_status()
        assert status["status"] == "current"
        assert status["last_indexed_commit"] == "abc123"

    async def test_stale_when_old(self, fresh_store: Store):
        old_time = datetime.now(UTC) - timedelta(hours=48)
        await fresh_store.set_code_intelligence_meta(
            "last_indexed_commit", "old123",
        )
        await fresh_store.set_code_intelligence_meta(
            "last_index_time", old_time.isoformat(),
        )
        service = CodeKnowledgeService(fresh_store)
        status = await service.get_freshness_status()
        assert status["status"] == "stale"

    async def test_error_when_error_recorded(self, fresh_store: Store):
        now = datetime.now(UTC)
        await fresh_store.set_code_intelligence_meta(
            "last_index_time", now.isoformat(),
        )
        await fresh_store.set_code_intelligence_meta(
            "index_error", "GitHub API rate limited",
        )
        service = CodeKnowledgeService(fresh_store)
        status = await service.get_freshness_status()
        assert status["status"] == "error"
        assert status["error_message"] == "GitHub API rate limited"

    async def test_custom_threshold(self, fresh_store: Store):
        recent = datetime.now(UTC) - timedelta(hours=2)
        await fresh_store.set_code_intelligence_meta(
            "last_index_time", recent.isoformat(),
        )
        service = CodeKnowledgeService(fresh_store)

        status_strict = await service.get_freshness_status(stale_threshold_hours=1)
        assert status_strict["status"] == "stale"

        status_lenient = await service.get_freshness_status(stale_threshold_hours=24)
        assert status_lenient["status"] == "current"


class TestFreshnessWarning:
    async def test_no_warning_when_current(self, fresh_store: Store):
        now = datetime.now(UTC)
        await fresh_store.set_code_intelligence_meta(
            "last_index_time", now.isoformat(),
        )
        service = CodeKnowledgeService(fresh_store)
        warning = await service.get_freshness_warning()
        assert warning is None

    async def test_warning_when_stale(self, fresh_store: Store):
        old = datetime.now(UTC) - timedelta(hours=48)
        await fresh_store.set_code_intelligence_meta(
            "last_index_time", old.isoformat(),
        )
        service = CodeKnowledgeService(fresh_store)
        warning = await service.get_freshness_warning()
        assert warning is not None
        assert "outdated" in warning.lower()

    async def test_no_warning_when_unavailable(self, fresh_store: Store):
        service = CodeKnowledgeService(fresh_store)
        warning = await service.get_freshness_warning()
        assert warning is None


class TestFreshnessAPIResponse:
    def test_response_model_structure(self):
        from observibot.api.schemas import CodeIntelligenceStatusResponse
        resp = CodeIntelligenceStatusResponse(
            status="current",
            last_indexed_commit="abc123",
            last_index_time="2026-04-12T10:00:00+00:00",
        )
        assert resp.status == "current"
        assert resp.error_message is None
