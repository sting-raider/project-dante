"""Shared test fixtures: isolated in-memory store + event log per test."""

from __future__ import annotations

import os

os.environ["DANTE_STORE_PATH"] = os.environ.get("DANTE_STORE_PATH", ".dante-test-store.json")

import pytest  # noqa: E402
from project_dante.db.store import STORE  # noqa: E402
from project_dante.domain.events import LOG  # noqa: E402


@pytest.fixture(autouse=True)
def clean_store():
    STORE.reset()
    LOG.reset()
    yield
    STORE.reset()
    LOG.reset()
