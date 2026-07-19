from pathlib import Path

import pytest

from path_network import create_app


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def app(tmp_path):
    instance_path = tmp_path / "instance"
    application = create_app(
        {
            "TESTING": True,
            "DATA_DIR": str(tmp_path / "data"),
            "DATABASE": str(tmp_path / "path-network.sqlite"),
        },
        instance_path=instance_path,
    )
    yield application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def fixture_path():
    def get_fixture(name: str) -> Path:
        return FIXTURES_DIR / name

    return get_fixture


@pytest.fixture
def sample_gpx_bytes(fixture_path):
    return fixture_path("morning-ride.gpx").read_bytes()
