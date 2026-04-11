"""Tests for the minimal health endpoint."""
from __future__ import annotations

from fastapi.testclient import TestClient

from observibot import __version__
from observibot.health import health_app


def test_health_endpoint_returns_ok() -> None:
    client = TestClient(health_app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__


def test_root_returns_index() -> None:
    client = TestClient(health_app)
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "observibot"
    assert body["health"] == "/health"
    assert body["version"] == __version__


def test_no_docs_endpoint() -> None:
    client = TestClient(health_app)
    # We disable /docs to keep the surface area minimal.
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
