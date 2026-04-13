"""Semantic fact data models for business context."""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class FactType(str, Enum):
    DEFINITION = "definition"
    WORKFLOW = "workflow"
    MAPPING = "mapping"
    ENTITY = "entity"
    RULE = "rule"
    CORRECTION = "correction"


class FactSource(str, Enum):
    SCHEMA_ANALYSIS = "schema_analysis"
    SEMANTIC_MODELER = "semantic_modeler"
    CODE_EXTRACTION = "code_extraction"
    USER_CORRECTION = "user_correction"


class SemanticFact(BaseModel):
    id: str
    fact_type: FactType
    concept: str
    claim: str
    tables: list[str] = []
    columns: list[str] = []
    sql_condition: str | None = None
    evidence_path: str | None = None
    evidence_lines: str | None = None
    evidence_commit: str | None = None
    source: FactSource
    confidence: float = 0.8
    created_at: datetime | None = None
    updated_at: datetime | None = None
    valid_from_commit: str | None = None
    valid_to_commit: str | None = None
    is_active: bool = True
