"""Tests for the Agent Memory Inspector API + Store methods."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from observibot.api.app import create_app
from observibot.api.deps import set_store
from observibot.core.code_intelligence.models import (
    FactSource,
    FactType,
    SemanticFact,
)
from observibot.core.models import Insight
from observibot.core.store import Store

pytestmark = pytest.mark.asyncio


def _fact(
    *,
    concept: str,
    claim: str,
    source: FactSource = FactSource.CODE_EXTRACTION,
    fact_type: FactType = FactType.DEFINITION,
    confidence: float = 0.85,
    tables: list[str] | None = None,
) -> SemanticFact:
    return SemanticFact(
        id=uuid.uuid4().hex[:12],
        fact_type=fact_type,
        concept=concept,
        claim=claim,
        tables=tables or [],
        columns=[],
        sql_condition=None,
        source=source,
        confidence=confidence,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        is_active=True,
    )


@pytest.fixture
async def app_client(tmp_path: Path):
    db_path = tmp_path / "knowledge_test.db"
    async with Store(db_path) as store:
        set_store(store)
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, store


async def _register(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register",
        json={"email": "admin@test.com", "password": "testpass123"},
    )
    assert resp.status_code == 200


# ---------- Store methods ----------


async def test_store_filtered_facts_by_source(tmp_path: Path):
    async with Store(tmp_path / "kb.db") as store:
        await store.save_semantic_fact(_fact(
            concept="active user", claim="last_login_at within 30 days",
            source=FactSource.CODE_EXTRACTION,
        ))
        await store.save_semantic_fact(_fact(
            concept="user", claim="users table",
            source=FactSource.SCHEMA_ANALYSIS,
        ))
        code_facts = await store.get_semantic_facts_filtered(
            source="code_extraction",
        )
        assert len(code_facts) == 1
        assert code_facts[0]["concept"] == "active user"
        all_facts = await store.get_semantic_facts_filtered()
        assert len(all_facts) == 2


async def test_store_filtered_facts_by_type(tmp_path: Path):
    async with Store(tmp_path / "kb2.db") as store:
        await store.save_semantic_fact(_fact(
            concept="onboarded", claim="onboarded_at IS NOT NULL",
            fact_type=FactType.DEFINITION,
        ))
        await store.save_semantic_fact(_fact(
            concept="user flow", claim="signup -> verify -> onboarded",
            fact_type=FactType.WORKFLOW,
        ))
        defs = await store.get_semantic_facts_filtered(fact_type="definition")
        assert len(defs) == 1
        assert defs[0]["concept"] == "onboarded"


async def test_store_search_hits_claim_or_concept(tmp_path: Path):
    async with Store(tmp_path / "kb3.db") as store:
        await store.save_semantic_fact(_fact(
            concept="onboarded user",
            claim="A user is onboarded once they complete the signup flow.",
        ))
        await store.save_semantic_fact(_fact(
            concept="admin",
            claim="row with role = 'admin'",
        ))
        hits = await store.get_semantic_facts_filtered(search="onboarded")
        assert any("onboarded" in f["concept"] for f in hits)


async def test_store_update_fact_deactivate(tmp_path: Path):
    async with Store(tmp_path / "kb4.db") as store:
        fact = _fact(concept="c", claim="original claim")
        await store.save_semantic_fact(fact)

        # Find the fact ID via active filter
        rows = await store.get_semantic_facts_filtered()
        fid = rows[0]["id"]

        updated = await store.update_semantic_fact(fid, is_active=False)
        assert updated is not None
        assert updated["is_active"] is False

        active = await store.get_semantic_facts_filtered(active_only=True)
        assert len(active) == 0
        all_including = await store.get_semantic_facts_filtered(active_only=False)
        assert len(all_including) == 1


async def test_store_update_fact_edit_claim(tmp_path: Path):
    async with Store(tmp_path / "kb5.db") as store:
        fact = _fact(concept="c", claim="wrong claim")
        await store.save_semantic_fact(fact)
        fid = (await store.get_semantic_facts_filtered())[0]["id"]

        updated = await store.update_semantic_fact(fid, claim="correct claim")
        assert updated is not None
        assert updated["claim"] == "correct claim"


async def test_store_update_fact_confidence(tmp_path: Path):
    async with Store(tmp_path / "kb6.db") as store:
        await store.save_semantic_fact(_fact(concept="c", claim="x"))
        fid = (await store.get_semantic_facts_filtered())[0]["id"]
        updated = await store.update_semantic_fact(fid, confidence=0.1)
        assert updated is not None
        assert updated["confidence"] == pytest.approx(0.1)


async def test_store_update_fact_missing_returns_none(tmp_path: Path):
    async with Store(tmp_path / "kb7.db") as store:
        out = await store.update_semantic_fact("does-not-exist", claim="x")
        assert out is None


async def test_store_delete_fact(tmp_path: Path):
    async with Store(tmp_path / "kb8.db") as store:
        await store.save_semantic_fact(_fact(concept="gone", claim="will be deleted"))
        fid = (await store.get_semantic_facts_filtered())[0]["id"]
        ok = await store.delete_semantic_fact(fid)
        assert ok is True
        rows = await store.get_semantic_facts_filtered(active_only=False)
        assert len(rows) == 0


async def test_store_delete_missing_returns_false(tmp_path: Path):
    async with Store(tmp_path / "kb9.db") as store:
        ok = await store.delete_semantic_fact("does-not-exist")
        assert ok is False


async def test_store_knowledge_stats(tmp_path: Path):
    async with Store(tmp_path / "kb10.db") as store:
        await store.save_semantic_fact(_fact(
            concept="a", claim="x",
            source=FactSource.CODE_EXTRACTION,
            fact_type=FactType.DEFINITION,
        ))
        await store.save_semantic_fact(_fact(
            concept="b", claim="y",
            source=FactSource.SCHEMA_ANALYSIS,
            fact_type=FactType.ENTITY,
        ))
        stats = await store.get_knowledge_stats()
        assert stats["total_facts"] == 2
        assert stats["active_facts"] == 2
        assert stats["inactive_facts"] == 0
        assert stats["facts_by_source"]["code_extraction"] == 1
        assert stats["facts_by_source"]["schema_analysis"] == 1
        assert stats["facts_by_type"]["definition"] == 1
        assert stats["facts_by_type"]["entity"] == 1
        assert stats["total_events"] == 0
        assert stats["total_feedback"] == 0


# ---------- API endpoints ----------


async def test_api_list_facts(app_client):
    client, store = app_client
    await _register(client)
    await store.save_semantic_fact(_fact(concept="c1", claim="v1"))
    await store.save_semantic_fact(_fact(concept="c2", claim="v2"))

    resp = await client.get("/api/knowledge/facts")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert {f["concept"] for f in data} == {"c1", "c2"}


async def test_api_list_facts_filter_by_source(app_client):
    client, store = app_client
    await _register(client)
    await store.save_semantic_fact(_fact(
        concept="c1", claim="v1", source=FactSource.CODE_EXTRACTION,
    ))
    await store.save_semantic_fact(_fact(
        concept="c2", claim="v2", source=FactSource.SCHEMA_ANALYSIS,
    ))

    resp = await client.get("/api/knowledge/facts?source=code_extraction")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["source"] == "code_extraction"


async def test_api_list_facts_search(app_client):
    client, store = app_client
    await _register(client)
    await store.save_semantic_fact(_fact(
        concept="onboarded user",
        claim="A user is onboarded once they confirm their email",
    ))
    await store.save_semantic_fact(_fact(
        concept="admin", claim="user with role=admin",
    ))

    resp = await client.get("/api/knowledge/facts?search=onboarded")
    assert resp.status_code == 200
    data = resp.json()
    assert any("onboarded" in f["concept"] for f in data)


async def test_api_patch_deactivates_fact(app_client):
    client, store = app_client
    await _register(client)
    await store.save_semantic_fact(_fact(concept="c1", claim="v1"))
    facts = await store.get_semantic_facts_filtered()
    fid = facts[0]["id"]

    resp = await client.patch(
        f"/api/knowledge/facts/{fid}", json={"is_active": False},
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    # Default (active_only=True) should now exclude it
    resp2 = await client.get("/api/knowledge/facts")
    assert len(resp2.json()) == 0

    # But it remains discoverable with active_only=false
    resp3 = await client.get("/api/knowledge/facts?active_only=false")
    assert len(resp3.json()) == 1


async def test_api_patch_edits_claim(app_client):
    client, store = app_client
    await _register(client)
    await store.save_semantic_fact(_fact(concept="c", claim="old"))
    fid = (await store.get_semantic_facts_filtered())[0]["id"]

    resp = await client.patch(
        f"/api/knowledge/facts/{fid}", json={"claim": "new claim"},
    )
    assert resp.status_code == 200
    assert resp.json()["claim"] == "new claim"


async def test_api_patch_missing_returns_404(app_client):
    client, _ = app_client
    await _register(client)
    resp = await client.patch(
        "/api/knowledge/facts/does-not-exist", json={"is_active": False},
    )
    assert resp.status_code == 404


async def test_api_delete_fact(app_client):
    client, store = app_client
    await _register(client)
    await store.save_semantic_fact(_fact(concept="c", claim="x"))
    fid = (await store.get_semantic_facts_filtered())[0]["id"]

    resp = await client.delete(f"/api/knowledge/facts/{fid}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    rows = await store.get_semantic_facts_filtered(active_only=False)
    assert rows == []


async def test_api_delete_missing_returns_404(app_client):
    client, _ = app_client
    await _register(client)
    resp = await client.delete("/api/knowledge/facts/does-not-exist")
    assert resp.status_code == 404


async def test_api_reactivate_fact(app_client):
    client, store = app_client
    await _register(client)
    await store.save_semantic_fact(_fact(concept="c", claim="x"))
    fid = (await store.get_semantic_facts_filtered())[0]["id"]

    await client.patch(f"/api/knowledge/facts/{fid}", json={"is_active": False})
    resp = await client.patch(
        f"/api/knowledge/facts/{fid}", json={"is_active": True},
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True


async def test_api_stats_counts_by_source_and_type(app_client):
    client, store = app_client
    await _register(client)
    await store.save_semantic_fact(_fact(
        concept="a", claim="x",
        source=FactSource.CODE_EXTRACTION, fact_type=FactType.DEFINITION,
    ))
    await store.save_semantic_fact(_fact(
        concept="b", claim="y",
        source=FactSource.SCHEMA_ANALYSIS, fact_type=FactType.ENTITY,
    ))
    await store.save_semantic_fact(_fact(
        concept="c", claim="z",
        source=FactSource.USER_CORRECTION, fact_type=FactType.CORRECTION,
        confidence=1.0,
    ))

    resp = await client.get("/api/knowledge/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_facts"] == 3
    assert data["active_facts"] == 3
    assert data["facts_by_source"]["code_extraction"] == 1
    assert data["facts_by_source"]["schema_analysis"] == 1
    assert data["facts_by_source"]["user_correction"] == 1
    assert data["code_intelligence_status"] in {"unavailable", "stale", "current", "error"}


async def test_api_feedback_summary(app_client):
    client, store = app_client
    await _register(client)

    # Seed an insight + feedback
    insight = Insight(
        id="ins-1",
        severity="warning",
        title="Test insight",
        summary="x",
        source="anomaly",
        confidence=0.5,
        created_at=datetime.now(UTC),
    )
    await store.save_insight(insight)
    await store.record_insight_feedback(
        insight_id="ins-1", user_id="u1", outcome="actionable",
    )
    await store.record_insight_feedback(
        insight_id="ins-1", user_id="u1", outcome="noise",
    )

    resp = await client.get("/api/knowledge/feedback-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert data["by_outcome"]["actionable"] == 1
    assert data["by_outcome"]["noise"] == 1
    assert len(data["recent"]) == 2
    # insight title is joined in
    assert any(r["insight_title"] == "Test insight" for r in data["recent"])


async def test_api_business_context_list(app_client):
    client, store = app_client
    await _register(client)
    await store.set_business_context("app_type", "SaaS B2B")
    await store.set_business_context("critical_tables", ["users", "orders"])

    resp = await client.get("/api/knowledge/context")
    assert resp.status_code == 200
    data = resp.json()
    keys = {e["key"] for e in data}
    assert keys == {"app_type", "critical_tables"}


async def test_api_endpoints_require_auth(app_client):
    client, _ = app_client
    # No registration → no cookie
    resp = await client.get("/api/knowledge/facts")
    assert resp.status_code == 401
    resp = await client.get("/api/knowledge/stats")
    assert resp.status_code == 401


# ---------- Admin gate on mutations (H4) ----------


def _non_admin_cookie() -> dict[str, str]:
    """Build a JWT cookie for a non-admin user for sandbox testing."""
    from observibot.api.deps import create_access_token

    token = create_access_token(
        {"sub": "non-admin-id", "email": "viewer@test.com", "is_admin": False}
    )
    return {"access_token": token}


async def test_non_admin_patch_rejected(app_client):
    client, store = app_client
    await _register(client)  # admin user, sets cookie
    await store.save_semantic_fact(_fact(concept="c", claim="v"))
    fid = (await store.get_semantic_facts_filtered())[0]["id"]

    # Swap in non-admin cookie
    resp = await client.patch(
        f"/api/knowledge/facts/{fid}",
        json={"is_active": False},
        cookies=_non_admin_cookie(),
    )
    assert resp.status_code == 403
    assert "admin" in resp.json()["detail"].lower()


async def test_non_admin_delete_rejected(app_client):
    client, store = app_client
    await _register(client)
    await store.save_semantic_fact(_fact(concept="c", claim="v"))
    fid = (await store.get_semantic_facts_filtered())[0]["id"]

    resp = await client.delete(
        f"/api/knowledge/facts/{fid}",
        cookies=_non_admin_cookie(),
    )
    assert resp.status_code == 403


async def test_non_admin_read_still_allowed(app_client):
    """GET endpoints remain open to any authenticated user."""
    client, store = app_client
    await _register(client)
    await store.save_semantic_fact(_fact(concept="c", claim="v"))

    resp = await client.get("/api/knowledge/facts", cookies=_non_admin_cookie())
    assert resp.status_code == 200
    resp = await client.get("/api/knowledge/stats", cookies=_non_admin_cookie())
    assert resp.status_code == 200


async def test_admin_patch_emits_knowledge_event(app_client):
    client, store = app_client
    await _register(client)  # admin
    await store.save_semantic_fact(_fact(concept="c", claim="v"))
    fid = (await store.get_semantic_facts_filtered())[0]["id"]

    resp = await client.patch(
        f"/api/knowledge/facts/{fid}", json={"is_active": False},
    )
    assert resp.status_code == 200

    events = await store.get_events(limit=20)
    assert any(
        e["event_type"] == "knowledge_edit" and fid in (e.get("subject") or "")
        for e in events
    ), f"expected knowledge_edit event for {fid}, got {events}"


async def test_admin_delete_emits_knowledge_event(app_client):
    client, store = app_client
    await _register(client)
    await store.save_semantic_fact(_fact(concept="c", claim="v"))
    fid = (await store.get_semantic_facts_filtered())[0]["id"]

    resp = await client.delete(f"/api/knowledge/facts/{fid}")
    assert resp.status_code == 200

    events = await store.get_events(limit=20)
    assert any(
        e["event_type"] == "knowledge_edit"
        and "deleted" in (e.get("summary") or "")
        for e in events
    )
