import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import create_app  # noqa: E402


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "test.db"), load_history=False, start_monitor=False)


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def repo(app):
    return app.state.repo


@pytest.fixture()
def coordinator(app):
    return app.state.coordinator
