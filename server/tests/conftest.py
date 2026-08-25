"""Shared test fixtures.

The MCP session manager refuses to start twice in one process, so exactly one
TestClient is built for the whole session and shared by every test module.
Per-test isolation comes from swapping the singletons in `app.deps` instead.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def _client():
    with TestClient(app) as c:
        yield c
