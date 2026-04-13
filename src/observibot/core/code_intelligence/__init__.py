"""Shared knowledge layer for business context and code intelligence."""
from observibot.core.code_intelligence.models import (
    FactSource,
    FactType,
    SemanticFact,
)
from observibot.core.code_intelligence.service import CodeKnowledgeService

__all__ = [
    "CodeKnowledgeService",
    "FactSource",
    "FactType",
    "SemanticFact",
]
