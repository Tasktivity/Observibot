from __future__ import annotations

import pytest

from observibot.connectors import UnknownConnectorError, get_connector


def test_get_connector_supabase() -> None:
    conn = get_connector(
        "sb", "supabase", {"connection_string": "postgres://u:p@h/db"}
    )
    assert conn.type == "supabase"
    assert conn.name == "sb"


def test_get_connector_railway_requires_lazy_ctor() -> None:
    # Railway connector won't validate token until use
    conn = get_connector(
        "rw", "railway", {"api_token": "t", "project_id": "p"}
    )
    assert conn.type == "railway"


def test_unknown_connector_raises() -> None:
    with pytest.raises(UnknownConnectorError):
        get_connector("x", "mysql", {})


def test_postgres_alias() -> None:
    conn = get_connector("pg", "postgres", {"connection_string": "postgres://x"})
    assert conn.type == "postgresql"
