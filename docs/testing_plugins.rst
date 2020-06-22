.. _testing_plugins:

Testing plugins
===============

We recommend using `pytest <https://docs.pytest.org/>`__ to write automated tests for your plugins.

If you use the template described in :ref:`writing_plugins_cookiecutter` your plugin will start with a single test in your ``tests/`` directory that looks like this:

.. code-block:: python

    from datasette.app import Datasette
    import pytest
    import httpx

    @pytest.mark.asyncio
    async def test_plugin_is_installed():
        app = Datasette([], memory=True).app()
        async with httpx.AsyncClient(app=app) as client:
            response = await client.get("http://localhost/-/plugins.json")
            assert 200 == response.status_code
            installed_plugins = {p["name"] for p in response.json()}
            assert "datasette-plugin-template-demo" in installed_plugins

This test uses the `HTTPX <https://www.python-httpx.org/>`__ Python library to run mock HTTP requests through a fresh instance of Datasette. This is the recommended way to write tests against a Datasette instance.

It also uses the `pytest-asyncio <https://pypi.org/project/pytest-asyncio/>`__ package to add support for ``async def`` test functions running under pytest.

You can install these packages like so::

    pip install pytest pytest-asyncio httpx

If you are building an installable package you can add them as test dependencies to your ``setup.py`` module like this:

.. code-block:: python

    setup(
        name="datasette-my-plugin",
        # ...
        extras_require={
            "test": ["pytest", "pytest-asyncio", "httpx"]
        },
        tests_require=["datasette-my-plugin[test]"],
    )

You can then install the test dependencies like so::

    pip install -e '.[test]'

Then run the tests using pytest like so::

    pytest

.. _testing_plugins_fixtures:

Using pytest fixtures
---------------------

`Pytest fixtures <https://docs.pytest.org/en/stable/fixture.html>`__ can be used to create initial testable objects which can then be used by multiple tests.

A common pattern for Datasette plugins is to create a fixture which sets up a temporary test database and wraps it in a Datasette instance.

Here's an example that uses the `sqlite-utils library <https://sqlite-utils.readthedocs.io/en/stable/python-api.html>`__ to populate a temporary test database. It also sets the title of that table using a simulated ``metadata.json`` congiguration:

.. code-block:: python

    from datasette.app import Datasette
    import httpx
    import pytest
    import sqlite_utils

    @pytest.fixture(scope="session")
    def ds(tmp_path_factory):
        db_directory = tmp_path_factory.mktemp("dbs")
        db_path = db_directory / "test.db"
        db = sqlite_utils.Database(db_path)
        db["dogs"].insert_all([
            {"id": 1, "name": "Cleo", "age": 5},
            {"id": 2, "name": "Pancakes", "age": 4}
        ], pk="id")
        ds = Datasette(
            [db_path],
            metadata={
                "databases": {
                    "test": {
                        "tables": {
                            "dogs": {
                                "title": "Some dogs"
                            }
                        }
                    }
                }
            }
        )
        return ds

    @pytest.mark.asyncio
    async def test_example_table_json(ds):
        async with httpx.AsyncClient(app=ds.app()) as client:
            response = await client.get("http://localhost/test/dogs.json?_shape=array")
            assert 200 == response.status_code
            assert [
                {"id": 1, "name": "Cleo", "age": 5},
                {"id": 2, "name": "Pancakes", "age": 4},
            ] == response.json()

    @pytest.mark.asyncio
        async def test_example_table_html(ds):
            async with httpx.AsyncClient(app=ds.app()) as client:
                response = await client.get("http://localhost/test/dogs")
                assert ">Some dogs</h1>" in response.text

Here the ``ds()`` function defines the fixture, which is than automatically passed to the two test functions based on pytest automatically matching their ``ds`` function parameters.

The ``@pytest.fixture(scope="session")`` line here ensures the fixture is reused for the full ``pytest`` execution session. This means that the temporary database file will be created once and reused for each test.

If you want to create that test database repeatedly for every individual test function, write the fixture function like this instead. You may want to do this if your plugin modifies the database contents in some way:

.. code-block:: python

    @pytest.fixture
    def ds(tmp_path_factory):
        # ...
