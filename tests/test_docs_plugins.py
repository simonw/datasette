# fmt: off
# -- start datasette_with_plugin_fixture --
from datasette import hookimpl
from datasette.app import Datasette
from datasette.plugins import pm
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def datasette_with_plugin():
    class TestPlugin:
        __name__ = "TestPlugin"

        @hookimpl
        def register_routes(self):
            return [
                (r"^/error$", lambda: 1 / 0),
            ]

    pm.register(TestPlugin(), name="undo")
    try:
        yield Datasette()
    finally:
        pm.unregister(name="undo")
# -- end datasette_with_plugin_fixture --


# -- start datasette_with_plugin_test --
@pytest.mark.asyncio
async def test_error(datasette_with_plugin):
    response = await datasette_with_plugin.client.get("/error")
    assert response.status_code == 500
# -- end datasette_with_plugin_test --
