"""
Tests for the datasette.app.Datasette class
"""
from .fixtures import app_client
import pytest


@pytest.fixture
def datasette(app_client):
    return app_client.ds


def test_get_database(datasette):
    db = datasette.get_database("fixtures")
    assert "fixtures" == db.name
    with pytest.raises(KeyError):
        datasette.get_database("missing")


def test_get_database_no_argument(datasette):
    # Returns the first available database:
    db = datasette.get_database()
    assert "fixtures" == db.name
