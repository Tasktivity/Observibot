from __future__ import annotations

from observibot.connectors.supabase import SupabaseConnector


def test_supabase_inherits_connection_validation() -> None:
    import pytest

    with pytest.raises(ValueError):
        SupabaseConnector(name="x", config={})


def test_supabase_required_permissions_includes_rls() -> None:
    conn = SupabaseConnector(
        name="x", config={"connection_string": "postgres://u:p@h/db"}
    )
    perms = conn.required_permissions()
    assert any("pg_policies" in p or "RLS" in p for p in perms)
