"""Semantic extraction pipeline — tree-sitter chunks + LLM reasoning."""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from observibot.core.code_intelligence.code_index import CodeChunk, CodeIndex
from observibot.core.code_intelligence.models import FactSource, FactType, SemanticFact
from observibot.core.code_intelligence.secret_scanner import scan_and_redact
from observibot.core.models import SystemModel
from observibot.core.store import Store

log = logging.getLogger(__name__)

EXTRACTION_PROMPT = """\
You are analyzing source code for an application monitored by Observibot.
The application's database has these tables: {table_names}

Analyze this code chunk and extract business rules, entity definitions,
workflows, and domain vocabulary. For each finding, you MUST specify
which database tables and columns it relates to.

File: {file_path} (lines {start_line}-{end_line})
Language: {language}

```{language}
{code_content}
```

Respond with VALID JSON ONLY:
{{
  "facts": [
    {{
      "fact_type": "definition|workflow|mapping|entity|rule",
      "concept": "short concept name",
      "claim": "one-sentence definition or rule",
      "tables": ["table_name"],
      "columns": ["table_name.column_name"],
      "sql_condition": "SQL WHERE fragment or null",
      "confidence": 0.0-1.0
    }}
  ]
}}

Rules:
- Every fact MUST reference at least one database table
- If you cannot map a finding to a known table, set confidence < 0.3
- Do NOT fabricate table or column names not in the provided list
- Be specific about SQL conditions
"""


class SemanticExtractor:
    """Extracts business semantics from source code using structural analysis + LLM."""

    def __init__(
        self,
        code_index: CodeIndex,
        llm_provider,
        store: Store,
        cloud_extraction_allowed: bool = False,
    ) -> None:
        self.code_index = code_index
        self.llm_provider = llm_provider
        self.store = store
        self.cloud_extraction_allowed = cloud_extraction_allowed

    async def run_full_extraction(
        self, repo_path: str, system_model: SystemModel | None = None,
        start_index: int = 0, batch_size: int = 0,
    ) -> tuple[list[SemanticFact], int]:
        """Full extraction pipeline: index -> identify -> extract -> validate -> store.

        When ``batch_size`` > 0, only processes files from ``start_index`` to
        ``start_index + batch_size``. Returns (facts, next_start_index) where
        next_start_index is -1 if all files have been processed.
        """
        if not self.cloud_extraction_allowed and not self._is_local_provider():
            log.warning(
                "Source code extraction disabled (cloud_extraction=false and "
                "no local LLM configured). Enable cloud_extraction or configure Ollama."
            )
            return [], -1

        file_count = await self.code_index.index_directory(repo_path)
        if file_count == 0:
            log.info("No files indexed in %s", repo_path)
            return [], -1

        high_signal = await self.code_index.get_high_signal_files()
        if not high_signal:
            all_symbols = await self.code_index.get_symbols()
            high_signal = list({s.file_path for s in all_symbols})[:20]

        total_files = len(high_signal)
        if batch_size > 0:
            end_index = min(start_index + batch_size, total_files)
            batch = high_signal[start_index:end_index]
            next_index = end_index if end_index < total_files else -1
        else:
            batch = high_signal[:30]
            next_index = -1

        table_names = self._get_table_names(system_model)
        all_facts: list[SemanticFact] = []

        for file_path in batch:
            chunks = await self.code_index.get_chunks_for_file(file_path)
            for chunk in chunks:
                try:
                    facts = await self._extract_from_chunk(chunk, table_names)
                    validated = self._validate_facts(facts, table_names)
                    # Save facts incrementally so partial results persist on timeout
                    for fact in validated:
                        await self.store.save_semantic_fact(fact)
                    all_facts.extend(validated)
                except Exception as exc:
                    log.debug("Extraction failed for %s: %s", file_path, exc)

        log.info(
            "Extracted %d semantic facts from %d files (batch %d-%d of %d)",
            len(all_facts), len(batch), start_index,
            start_index + len(batch), total_files,
        )
        return all_facts, next_index

    async def run_incremental_extraction(
        self, repo_path: str, changed_files: list[str],
        system_model: SystemModel | None = None,
    ) -> list[SemanticFact]:
        """Re-extract only changed files."""
        if not self.cloud_extraction_allowed and not self._is_local_provider():
            return []

        await self.code_index.index_directory(repo_path)
        table_names = self._get_table_names(system_model)
        all_facts: list[SemanticFact] = []

        for file_path in changed_files:
            chunks = await self.code_index.get_chunks_for_file(file_path)
            for chunk in chunks:
                try:
                    facts = await self._extract_from_chunk(chunk, table_names)
                    validated = self._validate_facts(facts, table_names)
                    for fact in validated:
                        await self.store.save_semantic_fact(fact)
                    all_facts.extend(validated)
                except Exception as exc:
                    log.debug("Incremental extraction failed for %s: %s", file_path, exc)

        return all_facts

    async def _extract_from_chunk(
        self, chunk: CodeChunk, table_names: list[str],
    ) -> list[SemanticFact]:
        redacted_content, warnings = scan_and_redact(chunk.content)
        if warnings:
            log.info("Redacted secrets in %s: %s", chunk.file_path, warnings)

        prompt = EXTRACTION_PROMPT.format(
            table_names=", ".join(table_names) if table_names else "(none discovered)",
            file_path=chunk.file_path,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            language=chunk.language,
            code_content=redacted_content,
        )

        response = await self.llm_provider.analyze(
            system_prompt="You are a code analyzer. Output only valid JSON.",
            user_prompt=prompt,
        )

        return self._parse_extraction_response(
            response.data, chunk.file_path, chunk.start_line, chunk.end_line,
        )

    def _parse_extraction_response(
        self, data: dict, file_path: str, start_line: int, end_line: int,
    ) -> list[SemanticFact]:
        facts: list[SemanticFact] = []
        for raw in data.get("facts", []):
            fact_type_str = raw.get("fact_type", "definition")
            try:
                fact_type = FactType(fact_type_str)
            except ValueError:
                fact_type = FactType.DEFINITION

            facts.append(SemanticFact(
                id=uuid.uuid4().hex[:12],
                fact_type=fact_type,
                concept=raw.get("concept", "unknown"),
                claim=raw.get("claim", ""),
                tables=raw.get("tables", []),
                columns=raw.get("columns", []),
                sql_condition=raw.get("sql_condition"),
                evidence_path=file_path,
                evidence_lines=f"{start_line}-{end_line}",
                source=FactSource.CODE_EXTRACTION,
                confidence=float(raw.get("confidence", 0.5)),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ))
        return facts

    def _validate_facts(
        self, facts: list[SemanticFact], known_tables: list[str],
    ) -> list[SemanticFact]:
        """Validate facts: lower confidence for unrecognized tables."""
        table_set = set(known_tables)
        validated: list[SemanticFact] = []
        for fact in facts:
            if not fact.claim:
                continue
            if fact.tables and not any(t in table_set for t in fact.tables):
                fact.confidence = min(fact.confidence, 0.3)
            validated.append(fact)
        return validated

    def _get_table_names(self, model: SystemModel | None) -> list[str]:
        if model is None:
            return []
        return [t.name for t in model.tables]

    def _is_local_provider(self) -> bool:
        name = getattr(self.llm_provider, "name", "")
        return name.lower() in ("mock", "ollama", "local")
