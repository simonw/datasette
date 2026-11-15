"""
Output renderers can declare the extras they need using "extras" in their
register_output_renderer() dictionary, then receive them in page=
"""

import pytest
import pytest_asyncio

from datasette import hookimpl
from datasette.app import Datasette
from datasette.plugins import pm
from datasette.utils import StartupError
from datasette.utils.asgi import Response


class _ExtrasRendererPlugin:
    __name__ = "ExtrasRendererPlugin"

    @hookimpl
    def register_output_renderer(self):
        def render_declared(page, rows):
            return Response.json({"page": page, "num_rows": len(rows)})

        def render_none_declared(page):
            return Response.json({"page": page})

        def render_without_page_argument():
            return Response.text("ok")

        return [
            {
                "extension": "declared",
                "render": render_declared,
                "extras": ["count", "human_description_en", "primary_keys"],
            },
            {"extension": "nonedeclared", "render": render_none_declared},
            {
                "extension": "noargument",
                "render": render_without_page_argument,
                "extras": ["count"],
            },
        ]


@pytest.fixture
def ds():
    pm.register(_ExtrasRendererPlugin(), name="extras_renderer")
    ds = Datasette(memory=True)
    try:
        yield ds
    finally:
        ds.close()
        pm.unregister(name="extras_renderer")


@pytest_asyncio.fixture
async def db(ds):
    db = ds.add_memory_database("renderer_extras_test", name="data")
    await db.execute_write_script("""
        drop table if exists dogs;
        create table dogs (id integer primary key, name text, age integer);
        insert into dogs (name, age) values ('Cleo', 5), ('Pancakes', 4), ('Fido', 3);
    """)
    return db


@pytest.mark.asyncio
async def test_table_renderer_receives_declared_extras(ds, db):
    response = await ds.client.get("/data/dogs.declared?age__gt=3&_size=1")
    assert response.status_code == 200
    assert response.json() == {
        "page": {
            # Tables always include these two
            "next": "1",
            "next_url": "http://localhost/data/dogs.declared?age__gt=3&_size=1&_next=1",
            # The count is of every matching row, not just this page
            "count": 2,
            "count_truncated": False,
            "human_description_en": "where age > 3",
            "primary_keys": ["id"],
        },
        "num_rows": 1,
    }


@pytest.mark.asyncio
async def test_query_renderer_omits_extras_not_available_for_queries(ds, db):
    # count, human_description_en and primary_keys are all table extras
    response = await ds.client.get("/data/-/query.declared?sql=select+*+from+dogs")
    assert response.status_code == 200
    assert response.json() == {"page": {}, "num_rows": 3}


@pytest.mark.asyncio
async def test_row_renderer_omits_extras_not_available_for_rows(ds, db):
    response = await ds.client.get("/data/dogs/1.declared")
    assert response.status_code == 200
    data = response.json()
    assert data["num_rows"] == 1
    page = data["page"]
    # count and human_description_en are not available for rows
    assert "count" not in page
    assert "human_description_en" not in page
    # Rows always include these
    assert isinstance(page.pop("query_ms"), float)
    assert page == {
        "database": "data",
        "table": "dogs",
        "primary_keys": ["id"],
        "primary_key_values": ["1"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected",
    (
        ("/data/dogs.nonedeclared", {"next": None, "next_url": None}),
        (
            "/data/dogs.nonedeclared?_extra=count",
            {"next": None, "next_url": None, "count": 3, "count_truncated": False},
        ),
        # columns is only included if it was asked for
        ("/data/-/query.nonedeclared?sql=select+1+as+one", {}),
        (
            "/data/-/query.nonedeclared?sql=select+1+as+one&_extra=columns",
            {"columns": ["one"]},
        ),
        # Requested extras are added to the declared ones
        (
            "/data/dogs.declared?_extra=is_view",
            {
                "next": None,
                "next_url": None,
                "count": 3,
                "count_truncated": False,
                "human_description_en": "",
                "primary_keys": ["id"],
                "is_view": False,
            },
        ),
    ),
)
async def test_renderer_receives_extras_requested_in_url(ds, db, path, expected):
    response = await ds.client.get(path)
    assert response.status_code == 200
    assert response.json()["page"] == expected


@pytest.mark.asyncio
async def test_unknown_requested_extra_is_still_an_error(ds, db):
    response = await ds.client.get("/data/dogs.declared?_extra=nope")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_render_function_does_not_have_to_accept_page(ds, db):
    response = await ds.client.get("/data/dogs.noargument")
    assert response.status_code == 200
    assert response.text == "ok"


@pytest.mark.asyncio
async def test_declared_extras_do_not_leak_into_json(ds, db):
    # Declaring an extra for one format must not add it to any other format
    data = (await ds.client.get("/data/dogs.json")).json()
    assert "count" not in data
    assert "human_description_en" not in data


@pytest.mark.parametrize("extras", ("count", [1, 2], [None]))
def test_extras_must_be_a_list_of_strings(extras):
    class BadPlugin:
        __name__ = "BadPlugin"

        @hookimpl
        def register_output_renderer(self):
            return {
                "extension": "bad",
                "render": lambda: Response.text("x"),
                "extras": extras,
            }

    pm.register(BadPlugin(), name="bad_extras_renderer")
    try:
        with pytest.raises(StartupError, match="extras must be a list of strings"):
            Datasette(memory=True)
    finally:
        pm.unregister(name="bad_extras_renderer")


def test_json_renderer_uses_documented_arguments_only(ds):
    # The built-in JSON renderer is called like any plugin renderer, so it
    # must not depend on the undocumented data= and args= arguments
    import inspect

    render, _ = ds.renderers["json"]
    parameters = set(inspect.signature(render).parameters)
    assert not parameters & {"data", "args"}
    assert {"rows", "columns", "page"} <= parameters


def _render_with_data(data, rows):
    return Response.text("ok")


def _render_with_args(args, rows):
    return Response.text("ok")


@pytest.mark.parametrize(
    "render,argument,replacement",
    (
        (
            _render_with_data,
            "data",
            "Declare the extras it needs and use the page argument instead",
        ),
        (_render_with_args, "args", "Use request.args instead"),
    ),
)
def test_render_function_accepting_data_or_args_is_deprecated(
    render, argument, replacement
):
    class DeprecatedPlugin:
        __name__ = "DeprecatedPlugin"

        @hookimpl
        def register_output_renderer(self):
            return {"extension": "deprecated", "render": render}

    pm.register(DeprecatedPlugin(), name="deprecated_renderer")
    try:
        with pytest.warns(DeprecationWarning) as record:
            ds = Datasette(memory=True)
        ds.close()
    finally:
        pm.unregister(name="deprecated_renderer")
    messages = [
        str(warning.message)
        for warning in record
        if "Output renderer 'deprecated'" in str(warning.message)
    ]
    assert len(messages) == 1
    assert messages[0].startswith(
        f"Output renderer 'deprecated' accepts the {argument} argument, which is "
        f"deprecated and will be removed before Datasette 1.0. {replacement}: "
    )


@pytest.mark.asyncio
async def test_deprecated_data_and_args_are_still_passed():
    def render(data, args):
        return Response.json({"ok": data["ok"], "one": args["one"]})

    class DeprecatedPlugin:
        __name__ = "DeprecatedPlugin"

        @hookimpl
        def register_output_renderer(self):
            return {"extension": "deprecated", "render": render}

    pm.register(DeprecatedPlugin(), name="deprecated_renderer")
    try:
        with pytest.warns(DeprecationWarning) as record:
            ds = Datasette(memory=True)
        # The warning points at the render function, not at Datasette
        ours = [w for w in record if "Output renderer 'deprecated'" in str(w.message)]
        assert len(ours) == 2
        assert {w.filename for w in ours} == {__file__}
        assert {w.lineno for w in ours} == {render.__code__.co_firstlineno}
        response = await ds.client.get("/_memory/-/query.deprecated?sql=select+1&one=1")
        assert response.json() == {"ok": True, "one": "1"}
        ds.close()
    finally:
        pm.unregister(name="deprecated_renderer")


def test_no_deprecation_warning_for_other_renderers(ds):
    import warnings

    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        other = Datasette(memory=True)
        other.close()
    # The ds fixture registers renderers that only use page - and the
    # default CSV, TSV, blob and JSON renderers must not warn either
    extensions = ("declared", "nonedeclared", "noargument", "csv", "tsv", "json")
    for warning in record:
        for extension in extensions:
            assert f"Output renderer '{extension}'" not in str(warning.message)
