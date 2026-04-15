"""In-memory chat session store with TTL expiry.

For v1, sessions live in a process-local dict. This is sufficient for
single-instance deployment. DB-backed sessions can replace this later.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field


@dataclass
class ChatSession:
    """A multi-turn chat session."""

    session_id: str
    user_id: str
    created_at: float
    last_active: float
    turns: list[dict] = field(default_factory=list)
    state: dict = field(default_factory=dict)


DEFAULT_TTL_SECONDS = 1800  # 30 minutes
# One turn is now a full exchange (question + answer + structured entities).
# Phase 4.5 Step 2 rewrote _record_turn() to store a single record per exchange
# instead of two (user + assistant) separate rows, so 5 turns = 5 exchanges.
MAX_TURNS = 5


class SessionStore:
    """In-memory session store with TTL-based expiry."""

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._sessions: dict[str, ChatSession] = {}
        self._ttl = ttl_seconds

    def create_session(self, user_id: str) -> ChatSession:
        session_id = uuid.uuid4().hex[:16]
        now = time.time()
        session = ChatSession(
            session_id=session_id,
            user_id=user_id,
            created_at=now,
            last_active=now,
        )
        self._sessions[session_id] = session
        return session

    def get_session(
        self, session_id: str, user_id: str | None = None,
    ) -> ChatSession | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if time.time() - session.last_active > self._ttl:
            del self._sessions[session_id]
            return None
        if user_id is not None and session.user_id != user_id:
            return None
        session.last_active = time.time()
        return session

    def add_turn(self, session_id: str, turn: dict) -> None:
        session = self.get_session(session_id)
        if session is None:
            return
        session.turns.append(turn)
        if len(session.turns) > MAX_TURNS:
            session.turns = session.turns[-MAX_TURNS:]

    def get_context(self, session_id: str) -> list[dict]:
        session = self.get_session(session_id)
        if session is None:
            return []
        return list(session.turns)

    def cleanup_expired(self) -> int:
        now = time.time()
        expired = [
            sid for sid, s in self._sessions.items()
            if now - s.last_active > self._ttl
        ]
        for sid in expired:
            del self._sessions[sid]
        return len(expired)

    @property
    def active_count(self) -> int:
        return len(self._sessions)


# Module-level singleton
_global_session_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    """Return the global session store, creating it if needed."""
    global _global_session_store
    if _global_session_store is None:
        _global_session_store = SessionStore()
    return _global_session_store
