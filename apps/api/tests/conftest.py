"""Shared test fixtures: isolated in-memory store + event log per test."""

from __future__ import annotations

import os

os.environ["DANTE_STORE_PATH"] = os.environ.get("DANTE_STORE_PATH", ".dante-test-store.json")

import pytest  # noqa: E402
from project_dante.db.store import STORE  # noqa: E402
from project_dante.domain.events import LOG  # noqa: E402
from project_dante.settings import Settings  # noqa: E402

# A developer may intentionally keep an ignored repo-root .env populated for
# a real Test Mode smoke run.  Automated tests must remain hermetic: explicit
# process environment variables and monkeypatches still apply, but local
# env-files must not silently turn sandbox fixtures into live-test-mode ones.
Settings.model_config["env_file"] = None


@pytest.fixture(autouse=True)
def clean_store():
    STORE.reset()
    LOG.reset()
    yield
    STORE.reset()
    LOG.reset()
