"""Tests for the in-memory chat session store."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from observibot.api.app import create_app
from observibot.api.deps import set_store
from observibot.api.session_store import MAX_TURNS, SessionStore

# MAX_TURNS = 5 (each turn is now a full exchange — Step 2 structured turns)
from observibot.core.store import Store

pytestmark = pytest.mark.asyncio


# ---------- unit tests ----------


def test_create_session():
    store = SessionStore()
    session = store.create_session("user-1")
    assert session.session_id
    assert session.user_id == "user-1"
    assert session.turns == []
    assert store.active_count == 1


def test_get_existing_session():
    store = SessionStore()
    session = store.create_session("user-1")
    fetched = store.get_session(session.session_id)
    assert fetched is not None
    assert fetched.session_id == session.session_id


def test_get_nonexistent_session():
    store = SessionStore()
    assert store.get_session("nonexistent") is None


def test_expired_session_returns_none():
    store = SessionStore(ttl_seconds=0)
    session = store.create_session("user-1")
    # Sleep briefly to ensure TTL expires
    time.sleep(0.01)
    assert store.get_session(session.session_id) is None


def test_add_turn_and_get_context():
    store = SessionStore()
    session = store.create_session("user-1")
    store.add_turn(session.session_id, {"role": "user", "summary": "q1"})
    store.add_turn(session.session_id, {"role": "assistant", "summary": "a1"})

    context = store.get_context(session.session_id)
    assert len(context) == 2
    assert context[0]["summary"] == "q1"
    assert context[1]["summary"] == "a1"


def test_max_turns_enforced():
    store = SessionStore()
    session = store.create_session("user-1")

    for i in range(MAX_TURNS + 3):
        store.add_turn(session.session_id, {"role": "user", "summary": f"turn-{i}"})

    context = store.get_context(session.session_id)
    assert len(context) == MAX_TURNS
    # Oldest turns are dropped
    assert context[0]["summary"] == f"turn-{3}"


def test_cleanup_expired():
    store = SessionStore(ttl_seconds=0)
    store.create_session("user-1")
    store.create_session("user-2")
    time.sleep(0.01)
    removed = store.cleanup_expired()
    assert removed == 2
    assert store.active_count == 0


def test_add_turn_to_nonexistent_session():
    store = SessionStore()
    # Should not raise
    store.add_turn("nonexistent", {"role": "user", "summary": "hi"})


def test_five_full_exchanges_preserved():
    """Step 2 structured turns: 5 exchanges = 5 records, not 10."""
    store = SessionStore()
    session = store.create_session("user-1")
    for i in range(5):
        store.add_turn(session.session_id, {
            "question_summary": f"q{i}",
            "answer_summary": f"a{i}",
            "entities": {"domain": "observability"},
        })
    context = store.get_context(session.session_id)
    assert len(context) == 5
    assert [t["question_summary"] for t in context] == ["q0", "q1", "q2", "q3", "q4"]
    assert [t["answer_summary"] for t in context] == ["a0", "a1", "a2", "a3", "a4"]


def test_session_ownership_enforced():
    store = SessionStore()
    session = store.create_session("user-1")
    # Correct user can access
    assert store.get_session(session.session_id, "user-1") is not None
    # Different user cannot access
    assert store.get_session(session.session_id, "user-2") is None
    # Without user_id check, session is still accessible (backward compat)
    assert store.get_session(session.session_id) is not None


def test_expired_session_does_not_leak_to_other_user():
    store = SessionStore(ttl_seconds=0)
    session = store.create_session("user-1")
    time.sleep(0.01)
    # Expired session returns None for any user
    assert store.get_session(session.session_id, "user-1") is None
    assert store.get_session(session.session_id, "user-2") is None


# ---------- API integration tests ----------


@pytest.fixture
async def chat_client(tmp_path: Path):
    db_path = tmp_path / "session_test.db"
    async with Store(db_path) as store:
        set_store(store)
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            yield client, store


async def _register(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register",
        json={"email": "admin@test.com", "password": "testpass123"},
    )
    assert resp.status_code == 200


async def test_chat_returns_session_id(chat_client):
    client, _ = chat_client
    await _register(client)

    resp = await client.post(
        "/api/chat/query",
        json={"question": "What metrics are available?"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert len(data["session_id"]) > 0


async def test_session_id_persists_across_turns(chat_client):
    client, _ = chat_client
    await _register(client)

    resp1 = await client.post(
        "/api/chat/query",
        json={"question": "Show metrics"},
    )
    session_id = resp1.json()["session_id"]

    resp2 = await client.post(
        "/api/chat/query",
        json={"question": "Break that down", "session_id": session_id},
    )
    assert resp2.status_code == 200
    assert resp2.json()["session_id"] == session_id


async def test_chat_works_without_session_id(chat_client):
    client, _ = chat_client
    await _register(client)

    resp = await client.post(
        "/api/chat/query",
        json={"question": "Recent alerts"},
    )
    assert resp.status_code == 200
    assert resp.json()["answer"]
