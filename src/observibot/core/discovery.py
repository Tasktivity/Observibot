"""Discovery engine — orchestrates connectors and merges fragments.

The :class:`ModelDiff` returned by :func:`diff_models` is powered by
`DeepDiff` when available so ordering changes and non-structural noise do
not trigger false drift alerts.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from deepdiff import DeepDiff

from observibot.connectors.base import BaseConnector
from observibot.core.models import (
    Relationship,
    SystemFragment,
    SystemModel,
    TableInfo,
)

log = logging.getLogger(__name__)

# Paths we deliberately ignore when comparing two SystemModels — these
# change on every discovery cycle without being "drift."
_IGNORED_DIFF_PATHS = (
    "root['id']",
    "root['created_at']",
    "root['fingerprint']",
    "root['fragments']",
)


@dataclass
class ModelDiff:
    """Human-readable diff between two SystemModels.

    Populated by :func:`diff_models`. The high-level fields (``added_tables``,
    etc.) are derived from a DeepDiff pass and remain backward compatible
    with earlier versions of this class. ``raw_diff`` exposes the full
    DeepDiff payload for richer LLM prompts.
    """

    added_tables: list[str] = field(default_factory=list)
    removed_tables: list[str] = field(default_factory=list)
    changed_tables: list[dict[str, list[str]]] = field(default_factory=list)
    added_services: list[str] = field(default_factory=list)
    removed_services: list[str] = field(default_factory=list)
    fingerprint_changed: bool = False
    raw_diff: dict[str, Any] = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        return bool(
            self.added_tables
            or self.removed_tables
            or self.changed_tables
            or self.added_services
            or self.removed_services
            or self.fingerprint_changed
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "added_tables": list(self.added_tables),
            "removed_tables": list(self.removed_tables),
            "changed_tables": list(self.changed_tables),
            "added_services": list(self.added_services),
            "removed_services": list(self.removed_services),
            "fingerprint_changed": self.fingerprint_changed,
            "raw_diff": self.raw_diff,
        }

    def to_human_readable(self) -> str:
        """Render a terminal-friendly summary suitable for CLI panels."""
        if not self.has_changes:
            return "No structural changes detected."
        lines: list[str] = []
        if self.added_tables:
            lines.append(f"Added tables ({len(self.added_tables)}):")
            for name in self.added_tables:
                lines.append(f"  + {name}")
        if self.removed_tables:
            lines.append(f"Removed tables ({len(self.removed_tables)}):")
            for name in self.removed_tables:
                lines.append(f"  - {name}")
        if self.changed_tables:
            lines.append(f"Changed tables ({len(self.changed_tables)}):")
            for entry in self.changed_tables:
                table = entry.get("table", ["?"])[0]
                added = entry.get("added_columns") or []
                removed = entry.get("removed_columns") or []
                if added:
                    lines.append(f"  ~ {table}: +cols {', '.join(added)}")
                if removed:
                    lines.append(f"  ~ {table}: -cols {', '.join(removed)}")
        if self.added_services:
            lines.append(f"Added services ({len(self.added_services)}):")
            for s in self.added_services:
                lines.append(f"  + {s}")
        if self.removed_services:
            lines.append(f"Removed services ({len(self.removed_services)}):")
            for s in self.removed_services:
                lines.append(f"  - {s}")
        return "\n".join(lines)


class DiscoveryEngine:
    """Runs all connectors and merges their fragments into a SystemModel."""

    def __init__(self, connectors: Iterable[BaseConnector]) -> None:
        self.connectors = list(connectors)

    async def run(self) -> SystemModel:
        """Discover all connectors in parallel and build a merged SystemModel."""
        if not self.connectors:
            model = SystemModel()
            model.compute_fingerprint()
            return model

        results = await asyncio.gather(
            *(self._safe_discover(c) for c in self.connectors),
            return_exceptions=False,
        )
        return self.merge_fragments(results)

    async def _safe_discover(self, connector: BaseConnector) -> SystemFragment:
        try:
            return await connector.discover()
        except Exception as exc:
            log.warning("Connector %s failed: %s", connector.name, exc)
            return SystemFragment(
                connector_name=connector.name,
                connector_type=connector.type,
                errors=[str(exc)],
            )

    def merge_fragments(self, fragments: Iterable[SystemFragment]) -> SystemModel:
        """Merge a sequence of SystemFragments into a single SystemModel."""
        merged = SystemModel(fragments=list(fragments))
        seen_tables: dict[tuple[str, str, str], TableInfo] = {}
        for frag in merged.fragments:
            for tbl in frag.tables:
                key = (frag.connector_name, tbl.schema, tbl.name)
                seen_tables[key] = tbl
            for rel in frag.relationships:
                merged.relationships.append(rel)
            merged.services.extend(frag.services)
        merged.tables = list(seen_tables.values())
        # Stable de-dup of relationships
        rel_keys: set[tuple[str, str, str, str]] = set()
        unique_rels: list[Relationship] = []
        for rel in merged.relationships:
            key = (rel.from_table, rel.from_column, rel.to_table, rel.to_column)
            if key in rel_keys:
                continue
            rel_keys.add(key)
            unique_rels.append(rel)
        merged.relationships = unique_rels
        merged.compute_fingerprint()
        return merged


def diff_models(old: SystemModel | None, new: SystemModel) -> ModelDiff:
    """Compute a structured diff between two SystemModels using DeepDiff.

    ``ignore_order=True`` means dict/list re-ordering does not create false
    drift. The high-level ``added_tables`` / ``removed_tables`` /
    ``changed_tables`` fields are computed by set-diffing by fully-qualified
    name so the CLI output stays meaningful to humans; the full DeepDiff
    payload is also stored in ``raw_diff`` for LLM prompts.
    """
    diff = ModelDiff()
    if old is None:
        diff.added_tables = sorted(t.fqn for t in new.tables)
        diff.added_services = sorted(s.name for s in new.services)
        diff.fingerprint_changed = bool(new.fingerprint)
        return diff

    old_tables = {t.fqn: t for t in old.tables}
    new_tables = {t.fqn: t for t in new.tables}
    diff.added_tables = sorted(set(new_tables) - set(old_tables))
    diff.removed_tables = sorted(set(old_tables) - set(new_tables))

    for name in sorted(set(old_tables) & set(new_tables)):
        old_cols = {c["name"]: c for c in old_tables[name].columns}
        new_cols = {c["name"]: c for c in new_tables[name].columns}
        added = sorted(set(new_cols) - set(old_cols))
        removed = sorted(set(old_cols) - set(new_cols))
        if added or removed:
            diff.changed_tables.append(
                {"table": [name], "added_columns": added, "removed_columns": removed}
            )

    old_services = {s.name for s in old.services}
    new_services = {s.name for s in new.services}
    diff.added_services = sorted(new_services - old_services)
    diff.removed_services = sorted(old_services - new_services)
    diff.fingerprint_changed = old.fingerprint != new.fingerprint

    try:
        raw = DeepDiff(
            old.to_dict(),
            new.to_dict(),
            ignore_order=True,
            exclude_paths=list(_IGNORED_DIFF_PATHS),
        )
        diff.raw_diff = raw.to_dict() if hasattr(raw, "to_dict") else dict(raw)
    except Exception as exc:  # pragma: no cover — DeepDiff is quite forgiving
        log.debug("DeepDiff failed: %s", exc)
        diff.raw_diff = {}

    return diff
