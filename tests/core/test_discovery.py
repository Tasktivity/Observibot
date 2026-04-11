from __future__ import annotations

import pytest

from observibot.core.discovery import DiscoveryEngine, diff_models
from observibot.core.models import SystemModel, TableInfo


@pytest.mark.asyncio
async def test_engine_runs_with_no_connectors() -> None:
    engine = DiscoveryEngine(connectors=[])
    model = await engine.run()
    assert isinstance(model, SystemModel)
    assert model.fingerprint != ""


@pytest.mark.asyncio
async def test_engine_merges_fragments(
    mock_supabase_connector, mock_railway_connector
) -> None:
    engine = DiscoveryEngine(connectors=[mock_supabase_connector, mock_railway_connector])
    model = await engine.run()
    assert len(model.tables) == 6
    assert len(model.services) == 2
    assert len(model.relationships) == 5


@pytest.mark.asyncio
async def test_engine_survives_connector_failure(mock_supabase_connector) -> None:
    class BrokenConnector(type(mock_supabase_connector)):
        async def discover(self):  # type: ignore[override]
            raise RuntimeError("boom")

    engine = DiscoveryEngine(
        connectors=[mock_supabase_connector, BrokenConnector(name="broken")]
    )
    model = await engine.run()
    # Healthy connector contributed tables
    assert len(model.tables) == 6
    # Broken connector recorded an error fragment
    assert any(frag.errors for frag in model.fragments)


def test_diff_reports_added_table(sample_system_model: SystemModel) -> None:
    old = sample_system_model
    new = SystemModel.from_dict(old.to_dict())
    new.tables.append(TableInfo(name="new_table", schema="public", columns=[]))
    new.compute_fingerprint()
    diff = diff_models(old, new)
    assert "public.new_table" in diff.added_tables
    assert diff.fingerprint_changed


def test_diff_reports_column_changes(sample_system_model: SystemModel) -> None:
    old = sample_system_model
    new = SystemModel.from_dict(old.to_dict())
    for tbl in new.tables:
        if tbl.name == "tasks":
            tbl.columns.append({"name": "priority", "type": "int"})
    new.compute_fingerprint()
    diff = diff_models(old, new)
    changed_names = [c["table"][0] for c in diff.changed_tables]
    assert "public.tasks" in changed_names


def test_diff_first_run_reports_everything_as_added(sample_system_model: SystemModel) -> None:
    diff = diff_models(None, sample_system_model)
    assert len(diff.added_tables) == len(sample_system_model.tables)
    assert len(diff.added_services) == len(sample_system_model.services)


def test_diff_ignores_reordering(sample_system_model: SystemModel) -> None:
    """Rearranging the columns list must NOT trigger drift — DeepDiff with
    ignore_order=True should see them as equal."""
    old = sample_system_model
    new = SystemModel.from_dict(old.to_dict())
    for tbl in new.tables:
        tbl.columns.reverse()
    new.compute_fingerprint()
    diff = diff_models(old, new)
    assert not diff.added_tables
    assert not diff.removed_tables
    assert not diff.changed_tables


def test_diff_human_readable_has_bullets(sample_system_model: SystemModel) -> None:
    old = sample_system_model
    new = SystemModel.from_dict(old.to_dict())
    new.tables.append(TableInfo(name="new_table", schema="public", columns=[]))
    new.compute_fingerprint()
    text = diff_models(old, new).to_human_readable()
    assert "public.new_table" in text
    assert "Added tables" in text
