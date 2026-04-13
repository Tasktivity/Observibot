"""Tests for semantic extraction pipeline and secret scanner."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from observibot.core.code_intelligence.extractor import SemanticExtractor
from observibot.core.code_intelligence.secret_scanner import has_secrets, scan_and_redact
from observibot.core.code_intelligence.tree_sitter_index import TreeSitterIndex
from observibot.core.models import SystemModel, TableInfo
from observibot.core.store import Store


SAMPLE_CODE = '''\
class UserService:
    def get_onboarded_users(self):
        """Return users who have completed onboarding."""
        return db.query(User).filter(User.completed_onboarding_at.isnot(None))

    def get_active_users(self):
        return db.query(User).filter(User.is_active == True, User.last_login_at > threshold)
'''


@pytest.fixture
async def ext_store(tmp_path: Path):
    path = tmp_path / "ext_store.db"
    async with Store(path) as store:
        yield store


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "user_service.py").write_text(SAMPLE_CODE)
    return tmp_path


def _mock_provider(facts_response: dict | None = None) -> MagicMock:
    provider = MagicMock()
    provider.name = "mock"
    provider.analyze = AsyncMock(return_value=MagicMock(
        data=facts_response or {
            "facts": [
                {
                    "fact_type": "mapping",
                    "concept": "onboarded",
                    "claim": "User has completed onboarding when completed_onboarding_at IS NOT NULL",
                    "tables": ["users"],
                    "columns": ["users.completed_onboarding_at"],
                    "sql_condition": "completed_onboarding_at IS NOT NULL",
                    "confidence": 0.85,
                },
            ]
        }
    ))
    return provider


def _sample_model() -> SystemModel:
    return SystemModel(
        tables=[
            TableInfo(name="users", schema="public", columns=[
                {"name": "id", "type": "uuid"},
                {"name": "completed_onboarding_at", "type": "timestamptz"},
                {"name": "is_active", "type": "boolean"},
            ]),
        ],
    )


class TestSemanticExtractor:
    async def test_full_extraction_pipeline(self, ext_store: Store, sample_repo: Path):
        idx = TreeSitterIndex()
        provider = _mock_provider()
        extractor = SemanticExtractor(
            code_index=idx, llm_provider=provider,
            store=ext_store, cloud_extraction_allowed=False,
        )
        facts = await extractor.run_full_extraction(
            str(sample_repo), system_model=_sample_model(),
        )
        assert len(facts) >= 1
        assert facts[0].concept == "onboarded"
        assert facts[0].source.value == "code_extraction"

    async def test_validation_lowers_confidence_for_unknown_tables(
        self, ext_store: Store, sample_repo: Path,
    ):
        provider = _mock_provider({
            "facts": [{
                "fact_type": "definition",
                "concept": "thing",
                "claim": "test claim",
                "tables": ["nonexistent_table"],
                "columns": [],
                "sql_condition": None,
                "confidence": 0.9,
            }]
        })
        idx = TreeSitterIndex()
        extractor = SemanticExtractor(
            code_index=idx, llm_provider=provider,
            store=ext_store, cloud_extraction_allowed=False,
        )
        facts = await extractor.run_full_extraction(
            str(sample_repo), system_model=_sample_model(),
        )
        if facts:
            assert facts[0].confidence <= 0.3

    async def test_cloud_extraction_blocked_without_opt_in(
        self, ext_store: Store, sample_repo: Path,
    ):
        provider = MagicMock()
        provider.name = "anthropic"
        idx = TreeSitterIndex()
        extractor = SemanticExtractor(
            code_index=idx, llm_provider=provider,
            store=ext_store, cloud_extraction_allowed=False,
        )
        facts = await extractor.run_full_extraction(str(sample_repo))
        assert facts == []

    async def test_cloud_extraction_allowed_with_opt_in(
        self, ext_store: Store, sample_repo: Path,
    ):
        provider = _mock_provider()
        provider.name = "anthropic"
        idx = TreeSitterIndex()
        extractor = SemanticExtractor(
            code_index=idx, llm_provider=provider,
            store=ext_store, cloud_extraction_allowed=True,
        )
        facts = await extractor.run_full_extraction(str(sample_repo))
        assert len(facts) >= 1

    async def test_incremental_extraction(
        self, ext_store: Store, sample_repo: Path,
    ):
        idx = TreeSitterIndex()
        provider = _mock_provider()
        extractor = SemanticExtractor(
            code_index=idx, llm_provider=provider,
            store=ext_store, cloud_extraction_allowed=False,
        )
        changed = [str(sample_repo / "services" / "user_service.py")]
        facts = await extractor.run_incremental_extraction(
            str(sample_repo), changed, system_model=_sample_model(),
        )
        assert len(facts) >= 1

    async def test_extraction_failure_doesnt_crash(
        self, ext_store: Store, sample_repo: Path,
    ):
        provider = MagicMock()
        provider.name = "mock"
        provider.analyze = AsyncMock(side_effect=Exception("LLM down"))
        idx = TreeSitterIndex()
        extractor = SemanticExtractor(
            code_index=idx, llm_provider=provider,
            store=ext_store, cloud_extraction_allowed=False,
        )
        facts = await extractor.run_full_extraction(str(sample_repo))
        assert facts == []

    async def test_facts_persisted_to_store(
        self, ext_store: Store, sample_repo: Path,
    ):
        idx = TreeSitterIndex()
        provider = _mock_provider()
        extractor = SemanticExtractor(
            code_index=idx, llm_provider=provider,
            store=ext_store, cloud_extraction_allowed=False,
        )
        await extractor.run_full_extraction(
            str(sample_repo), system_model=_sample_model(),
        )
        stored = await ext_store.get_semantic_facts()
        assert len(stored) >= 1


class TestSecretScanner:
    def test_detects_github_pat(self):
        content = 'TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"'
        assert has_secrets(content) is True
        redacted, warnings = scan_and_redact(content)
        assert "ghp_" not in redacted
        assert "[REDACTED:" in redacted
        assert len(warnings) >= 1

    def test_detects_aws_key(self):
        content = "aws_key = AKIAIOSFODNN7EXAMPLE"
        redacted, warnings = scan_and_redact(content)
        assert "AKIA" not in redacted

    def test_detects_connection_string(self):
        content = 'DATABASE_URL = "postgres://user:pass@host:5432/db"'
        assert has_secrets(content) is True
        redacted, _ = scan_and_redact(content)
        assert "postgres://" not in redacted

    def test_detects_private_key(self):
        content = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow..."
        assert has_secrets(content) is True

    def test_no_false_positive_on_clean_code(self):
        content = """\
class UserService:
    def get_users(self):
        return db.query(User).all()
"""
        assert has_secrets(content) is False
        redacted, warnings = scan_and_redact(content)
        assert redacted == content
        assert warnings == []

    def test_detects_jwt(self):
        content = 'token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"'
        assert has_secrets(content) is True

    def test_redaction_preserves_structure(self):
        content = 'config = {\n    "key": "ghp_abcdefghijklmnopqrstuvwxyz0123456789",\n    "name": "test"\n}'
        redacted, _ = scan_and_redact(content)
        assert '"name": "test"' in redacted
        assert "ghp_" not in redacted

    def test_detects_bearer_token(self):
        content = 'headers = {"Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.abcdef.ghijkl"}'
        assert has_secrets(content) is True
