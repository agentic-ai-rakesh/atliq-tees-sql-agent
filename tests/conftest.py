from __future__ import annotations

import pytest

from tshirt_agent.config import Settings
from tshirt_agent.database import create_readonly_engine
from tshirt_agent.seed_data import build_sqlite


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    return build_sqlite(tmp_path_factory.mktemp("db") / "atliq_tshirts.db")


@pytest.fixture
def settings(db_path, tmp_path) -> Settings:
    return Settings(database_url=f"sqlite:///{db_path}", log_dir=tmp_path / "logs")


@pytest.fixture
def engine(settings):
    engine = create_readonly_engine(settings.database_url)
    yield engine
    engine.dispose()
