.. _testing_plugins:

Testing plugins
===============

We recommend using `pytest <https://docs.pytest.org/>`__ to write automated tests for your plugins.

If you use the template described in :ref:`writing_plugins_cookiecutter` your plugin will start with a single test in your ``tests/`` directory that looks like this:

.. code-block:: python

    from datasette.app import Datasette
    import pytest


    @pytest.mark.asyncio
    async def test_plugin_is_installed():
        datasette = Datasette(memory=True)
        response = await datasette.client.get("/-/plugins.json")
        assert response.status_code == 200
        installed_plugins = {p["name"] for p in response.json()}
        assert (
            "datasette-plugin-template-demo"
            in installed_plugins
        )


This test uses the :ref:`internals_datasette_client` object to exercise a test instance of Datasette. ``datasette.client`` is a wrapper around the `HTTPX <https://www.python-httpx.org/>`__ Python library which can imitate HTTP requests using ASGI. This is the recommended way to write tests against a Datasette instance.

This test also uses the `pytest-asyncio <https://pypi.org/project/pytest-asyncio/>`__ package to add support for ``async def`` test functions running under pytest.

You can install these packages like so::

    pip install pytest pytest-asyncio

If you are building an installable package you can add them as test dependencies to your ``setup.py`` module like this:

.. code-block:: python

    setup(
        name="datasette-my-plugin",
        # ...
        extras_require={"test": ["pytest", "pytest-asyncio"]},
        tests_require=["datasette-my-plugin[test]"],
    )

You can then install the test dependencies like so::

    pip install -e '.[test]'

Then run the tests using pytest like so::

    pytest

.. _testing_plugins_datasette_test_instance:

Setting up a Datasette test instance
------------------------------------

The above example shows the easiest way to start writing tests against a Datasette instance:

.. code-block:: python

    from datasette.app import Datasette
    import pytest


    @pytest.mark.asyncio
    async def test_plugin_is_installed():
        datasette = Datasette(memory=True)
        response = await datasette.client.get("/-/plugins.json")
        assert response.status_code == 200

Creating a ``Datasette()`` instance like this as useful shortcut in tests, but there is one detail you need to be aware of. It's important to ensure that the async method ``.invoke_startup()`` is called on that instance. You can do that like this:

.. code-block:: python

    datasette = Datasette(memory=True)
    await datasette.invoke_startup()

This method registers any :ref:`plugin_hook_startup` or :ref:`plugin_hook_prepare_jinja2_environment` plugins that might themselves need to make async calls.

If you are using ``await datasette.client.get()`` and similar methods then you don't need to worry about this - Datasette automatically calls ``invoke_startup()`` the first time it handles a request.

.. _testing_plugins_pdb:

Using pdb for errors thrown inside Datasette
--------------------------------------------

If an exception occurs within Datasette itself during a test, the response returned to your plugin will have a ``response.status_code`` value of 500.

You can add ``pdb=True`` to the ``Datasette`` constructor to drop into a Python debugger session inside your test run instead of getting back a 500 response code. This is equivalent to running the ``datasette`` command-line tool with the ``--pdb`` option.

Here's what that looks like in a test function:

.. code-block:: python

    def test_that_opens_the_debugger_or_errors():
        ds = Datasette([db_path], pdb=True)
        response = await ds.client.get("/")

If you use this pattern you will need to run ``pytest`` with the ``-s`` option to avoid capturing stdin/stdout in order to interact with the debugger prompt.

.. _testing_plugins_fixtures:

Using pytest fixtures
---------------------

`Pytest fixtures <https://docs.pytest.org/en/stable/fixture.html>`__ can be used to create initial testable objects which can then be used by multiple tests.

A common pattern for Datasette plugins is to create a fixture which sets up a temporary test database and wraps it in a Datasette instance.

Here's an example that uses the `sqlite-utils library <https://sqlite-utils.datasette.io/en/stable/python-api.html>`__ to populate a temporary test database. It also sets the title of that table using a simulated ``metadata.json`` configuration:

.. code-block:: python

    from datasette.app import Datasette
    import pytest
    import sqlite_utils


    @pytest.fixture(scope="session")
    def datasette(tmp_path_factory):
        db_directory = tmp_path_factory.mktemp("dbs")
        db_path = db_directory / "test.db"
        db = sqlite_utils.Database(db_path)
        db["dogs"].insert_all(
            [
                {"id": 1, "name": "Cleo", "age": 5},
                {"id": 2, "name": "Pancakes", "age": 4},
            ],
            pk="id",
        )
        datasette = Datasette(
            [db_path],
            metadata={
                "databases": {
                    "test": {
                        "tables": {
                            "dogs": {"title": "Some dogs"}
                        }
                    }
                }
            },
        )
        return datasette


    @pytest.mark.asyncio
    async def test_example_table_json(datasette):
        response = await datasette.client.get(
            "/test/dogs.json?_shape=array"
        )
        assert response.status_code == 200
        assert response.json() == [
            {"id": 1, "name": "Cleo", "age": 5},
            {"id": 2, "name": "Pancakes", "age": 4},
        ]


    @pytest.mark.asyncio
    async def test_example_table_html(datasette):
        response = await datasette.client.get("/test/dogs")
        assert ">Some dogs</h1>" in response.text

Here the ``datasette()`` function defines the fixture, which is than automatically passed to the two test functions based on pytest automatically matching their ``datasette`` function parameters.

The ``@pytest.fixture(scope="session")`` line here ensures the fixture is reused for the full ``pytest`` execution session. This means that the temporary database file will be created once and reused for each test.

If you want to create that test database repeatedly for every individual test function, write the fixture function like this instead. You may want to do this if your plugin modifies the database contents in some way:

.. code-block:: python

    @pytest.fixture
    def datasette(tmp_path_factory):
        # This fixture will be executed repeatedly for every test
        ...

.. _testing_plugins_pytest_httpx:

Testing outbound HTTP calls with pytest-httpx
---------------------------------------------

If your plugin makes outbound HTTP calls - for example datasette-auth-github or datasette-import-table - you may need to mock those HTTP requests in your tests.

The `pytest-httpx <https://pypi.org/project/pytest-httpx/>`__ package is a useful library for mocking calls. It can be tricky to use with Datasette though since it mocks all HTTPX requests, and Datasette's own testing mechanism uses HTTPX internally.

To avoid breaking your tests, you can return ``["localhost"]`` from the ``non_mocked_hosts()`` fixture.

As an example, here's a very simple plugin which executes an HTTP response and returns the resulting content:

.. code-block:: python

    from datasette import hookimpl
    from datasette.utils.asgi import Response
    import httpx


    @hookimpl
    def register_routes():
        return [
            (r"^/-/fetch-url$", fetch_url),
        ]


    async def fetch_url(datasette, request):
        if request.method == "GET":
            return Response.html(
                """
                <form action="/-/fetch-url" method="post">
                <input type="hidden" name="csrftoken" value="{}">
                <input name="url"><input type="submit">
            </form>""".format(
                    request.scope["csrftoken"]()
                )
            )
        vars = await request.post_vars()
        url = vars["url"]
        return Response.text(httpx.get(url).text)

Here's a test for that plugin that mocks the HTTPX outbound request:

.. code-block:: python

    from datasette.app import Datasette
    import pytest


    @pytest.fixture
    def non_mocked_hosts():
        # This ensures httpx-mock will not affect Datasette's own
        # httpx calls made in the tests by datasette.client:
        return ["localhost"]


    async def test_outbound_http_call(httpx_mock):
        httpx_mock.add_response(
            url="https://www.example.com/",
            text="Hello world",
        )
        datasette = Datasette([], memory=True)
        response = await datasette.client.post(
            "/-/fetch-url",
            data={"url": "https://www.example.com/"},
        )
        assert response.text == "Hello world"

        outbound_request = httpx_mock.get_request()
        assert (
            outbound_request.url == "https://www.example.com/"
        )

.. _testing_plugins_register_in_test:

Registering a plugin for the duration of a test
-----------------------------------------------

When writing tests for plugins you may find it useful to register a test plugin just for the duration of a single test. You can do this using ``pm.register()`` and ``pm.unregister()`` like this:

.. code-block:: python

    from datasette import hookimpl
    from datasette.app import Datasette
    from datasette.plugins import pm
    import pytest


    @pytest.mark.asyncio
    async def test_using_test_plugin():
        class TestPlugin:
            __name__ = "TestPlugin"

            # Use hookimpl and method names to register hooks
            @hookimpl
            def register_routes(self):
                return [
                    (r"^/error$", lambda: 1 / 0),
                ]

        pm.register(TestPlugin(), name="undo")
        try:
            # The test implementation goes here
            datasette = Datasette()
            response = await datasette.client.get("/error")
            assert response.status_code == 500
        finally:
            pm.unregister(name="undo")
