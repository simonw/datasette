"""
Output renderers that accept a row_stream argument can stream every row
when the request has ?_stream=1. The default CSV and TSV renderers in
datasette/csv_renderer.py are built on this.
"""

import json

import pytest
import pytest_asyncio

from datasette import hookimpl
from datasette.app import Datasette
from datasette.plugins import pm
from datasette.utils.asgi import Response
from datasette.views.base import StreamSizeExceeded


class _StreamingRendererPlugin:
    __name__ = "StreamingRendererPlugin"

    @hookimpl
    def register_output_renderer(self):
        def render_ndjson(columns, row_stream):
            async def body():
                async for row in row_stream:
                    yield json.dumps(dict(zip(columns, row))) + "\n"

            return Response.stream(body(), content_type="application/x-ndjson")

        def render_page_sizes(row_stream):
            async def body():
                async for rows in row_stream.pages():
                    yield f"{len(rows)}\n"

            return Response.stream(body())

        def render_big(row_stream):
            async def body():
                async for _ in row_stream:
                    yield "x" * 1024 * 600

            return Response.stream(body())

        def render_not_streaming(rows):
            return Response.text(str(len(rows)))

        def render_own_disposition():
            return Response.text(
                "x", headers={"Content-Disposition": 'attachment; filename="mine.txt"'}
            )

        return [
            {"extension": "ndjson", "render": render_ndjson},
            {"extension": "pagesizes", "render": render_page_sizes},
            {"extension": "big", "render": render_big},
            {"extension": "notstreaming", "render": render_not_streaming},
            {"extension": "owndisposition", "render": render_own_disposition},
        ]


def _make_datasette(**settings):
    return Datasette(memory=True, settings={"default_page_size": 10, **settings})


@pytest.fixture
def plugin():
    pm.register(_StreamingRendererPlugin(), name="streaming_renderer")
    try:
        yield
    finally:
        pm.unregister(name="streaming_renderer")


async def _add_numbers(ds):
    db = ds.add_memory_database("renderer_streaming_test", name="data")
    await db.execute_write_script("""
        drop view if exists numbers_view;
        drop table if exists numbers;
        create table numbers (id integer primary key, name text);
        insert into numbers (id, name)
            select value, 'n' || (100 - value) from json_each(
                '[' || (
                    with recursive c(x) as (select 1 union all select x + 1 from c where x < 35)
                    select group_concat(x) from c
                ) || ']'
            );
        create view numbers_view as select * from numbers;
    """)
    return db


@pytest_asyncio.fixture
async def ds(plugin):
    ds = _make_datasette()
    await _add_numbers(ds)
    try:
        yield ds
    finally:
        ds.close()


def _ids(response):
    return [json.loads(line)["id"] for line in response.text.splitlines()]


async def test_row_stream_is_the_current_page_by_default(ds):
    response = await ds.client.get("/data/numbers.ndjson")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-ndjson"
    assert _ids(response) == list(range(1, 11))
    # There is a next page
    assert "_next=10" in response.headers["link"]


@pytest.mark.parametrize("table", ("numbers", "numbers_view"))
async def test_row_stream_is_every_row_with_stream(ds, table):
    response = await ds.client.get(f"/data/{table}.ndjson?_stream=1")
    assert response.status_code == 200
    assert _ids(response) == list(range(1, 36))
    # Every row is in the response, so no next page is advertised
    assert "link" not in response.headers


async def test_row_stream_respects_filters_and_sort(ds):
    response = await ds.client.get(
        "/data/numbers.ndjson?_stream=1&id__gt=5&_sort_desc=name"
    )
    ids = _ids(response)
    assert sorted(ids) == list(range(6, 36))
    # name is 'n' || (100 - id) so descending name is ascending id
    assert ids == list(range(6, 36))


async def test_row_stream_pages(ds):
    # Streaming fetches max_returned_rows at a time, not default_page_size
    response = await ds.client.get("/data/numbers.pagesizes?_stream=1")
    assert response.text.split() == ["35"]
    # ... unless ?_size= asks for something else
    response = await ds.client.get("/data/numbers.pagesizes?_stream=1&_size=20")
    assert response.text.split() == ["20", "15"]
    response = await ds.client.get("/data/numbers.pagesizes?_stream=1&_size=10")
    assert response.text.split() == ["10", "10", "10", "5"]
    response = await ds.client.get("/data/numbers.pagesizes")
    assert response.text.split() == ["10"]


@pytest.mark.parametrize(
    "path,expected",
    (
        ("/data/numbers/3.ndjson?_stream=1", [3]),
        (
            "/data/-/query.ndjson?sql=select+id,name+from+numbers+limit+12&_stream=1",
            list(range(1, 13)),
        ),
    ),
)
async def test_row_stream_on_row_and_query_pages(ds, path, expected):
    response = await ds.client.get(path)
    assert response.status_code == 200
    assert _ids(response) == expected


async def test_stream_ignored_by_renderers_that_do_not_accept_row_stream(ds):
    response = await ds.client.get("/data/numbers.notstreaming?_stream=1")
    assert response.status_code == 200
    assert response.text == "10"
    # ... including the JSON renderer
    response = await ds.client.get("/data/numbers.json?_stream=1")
    assert len(response.json()["rows"]) == 10


async def test_stream_and_next_cannot_be_combined(ds):
    response = await ds.client.get("/data/numbers.ndjson?_stream=1&_next=10")
    assert response.status_code == 400


async def test_streaming_can_be_disabled(plugin):
    ds = _make_datasette(default_allow_stream=False)
    await _add_numbers(ds)
    try:
        for format_ in ("ndjson", "csv", "tsv"):
            response = await ds.client.get(f"/data/numbers.{format_}?_stream=1")
            assert response.status_code == 400, format_
        # Without ?_stream=1 it still works
        response = await ds.client.get("/data/numbers.ndjson")
        assert _ids(response) == list(range(1, 11))
    finally:
        ds.close()


async def test_streamed_response_larger_than_max_stream_mb_is_aborted(plugin):
    ds = _make_datasette(max_stream_mb=1)
    await _add_numbers(ds)
    try:
        # Under the limit: one row is 600KB
        response = await ds.client.get("/data/numbers.big?_size=1")
        assert response.status_code == 200
        assert len(response.content) == 1024 * 600
        # Over the limit: the response has already started, so it cannot
        # become an error page - the connection is dropped instead
        with pytest.raises(StreamSizeExceeded, match="max_stream_mb"):
            await ds.client.get("/data/numbers.big?_size=2")
        with pytest.raises(StreamSizeExceeded):
            await ds.client.get(
                "/data/-/query.csv?sql=select+printf('%.*c',+2000000,+'x')"
            )
    finally:
        ds.close()


async def test_max_stream_mb_zero_disables_the_limit(plugin):
    ds = _make_datasette(max_stream_mb=0)
    await _add_numbers(ds)
    try:
        response = await ds.client.get("/data/numbers.big?_size=3")
        assert len(response.content) == 3 * 1024 * 600
    finally:
        ds.close()


async def test_error_fetching_a_later_page_aborts_the_response(ds, monkeypatch):
    from datasette.views import table

    original = table.table_view_data
    calls = []

    async def broken_on_third_page(*args, **kwargs):
        calls.append(kwargs.get("_next"))
        if len(calls) == 3:
            raise ValueError("database went away")
        return await original(*args, **kwargs)

    monkeypatch.setattr(table, "table_view_data", broken_on_third_page)
    with pytest.raises(ValueError, match="database went away"):
        await ds.client.get("/data/numbers.csv?_stream=1&_size=10")
    assert calls == [None, "10", "20"]


@pytest.mark.parametrize(
    "path,expected",
    (
        ("/data/numbers.ndjson?_dl=1", 'attachment; filename="numbers.ndjson"'),
        ("/data/numbers.json?_dl=1", 'attachment; filename="numbers.json"'),
        ("/data/numbers/3.tsv?_dl=1", 'attachment; filename="numbers.tsv"'),
        (
            "/data/-/query.ndjson?sql=select+1+as+id&_dl=1",
            'attachment; filename="data.ndjson"',
        ),
        # A renderer that sets its own header keeps it
        ("/data/numbers.owndisposition?_dl=1", 'attachment; filename="mine.txt"'),
        ("/data/numbers.ndjson", None),
        # Errors are not downloads
        ("/data/-/query.json?sql=select+nope&_dl=1", None),
    ),
)
async def test_dl_downloads_any_format(ds, path, expected):
    response = await ds.client.get(path)
    assert response.headers.get("content-disposition") == expected


async def test_plugin_can_replace_the_csv_renderer():
    class CsvPlugin:
        __name__ = "CsvPlugin"

        @hookimpl
        def register_output_renderer(self):
            return {
                "extension": "csv",
                "render": lambda: Response.text("from the plugin"),
            }

    pm.register(CsvPlugin(), name="csv_plugin")
    try:
        ds = Datasette(memory=True)
        response = await ds.client.get("/_memory/-/query.csv?sql=select+1")
        assert response.text == "from the plugin"
        # TSV is still the default renderer
        response = await ds.client.get("/_memory/-/query.tsv?sql=select+1+as+one")
        assert response.text == "one\r\n1\r\n"
        ds.close()
    finally:
        pm.unregister(name="csv_plugin")


async def test_csv_and_tsv_are_registered_renderers(ds):
    assert {"csv", "tsv", "json"} <= set(ds.renderers)
    data = (await ds.client.get("/data/numbers.json?_extra=renderers")).json()
    assert {"csv", "tsv", "json"} <= set(data["renderers"])


async def test_expanded_columns_extra(ds_client):
    data = (
        await ds_client.get(
            "/fixtures/facetable.json?_labels=on&_extra=expanded_columns&_size=1"
        )
    ).json()
    assert data["expanded_columns"] == ["_city_id"]
    assert data["rows"][0]["_city_id"] == {"value": 1, "label": "San Francisco"}
    data = (
        await ds_client.get("/fixtures/facetable.json?_extra=expanded_columns&_size=1")
    ).json()
    assert data["expanded_columns"] == []


@pytest.mark.asyncio
async def test_row_stream_pages_are_max_returned_rows(plugin):
    ds = _make_datasette(max_returned_rows=15)
    await _add_numbers(ds)
    try:
        response = await ds.client.get("/data/numbers.pagesizes?_stream=1")
        assert response.text.split() == ["15", "15", "5"]
        # A size set in the table configuration does not apply to streaming
        ds.config = {"databases": {"data": {"tables": {"numbers": {"size": 5}}}}}
        response = await ds.client.get("/data/numbers.pagesizes")
        assert response.text.split() == ["5"]
        response = await ds.client.get("/data/numbers.pagesizes?_stream=1")
        assert response.text.split() == ["15", "15", "5"]
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_export_dialog_groups_formats_that_can_stream(ds):
    from bs4 import BeautifulSoup

    # A later page, so "Include all rows" is offered for this small table
    response = await ds.client.get("/data/numbers?_next=10")
    dialog = BeautifulSoup(response.text, "html.parser").select_one("#export-dialog")
    form = dialog.select_one("form.export-csv-form")
    # Formats that accept row_stream share the form with TSV and CSV, so
    # "Include all rows" applies to them
    formats = [row["data-export-format"] for row in form.select(".export-option")]
    assert formats[:2] == ["tsv", "csv"]
    assert sorted(formats[2:]) == ["big", "ndjson", "pagesizes"]
    assert form.select_one("input[name=_stream]") is not None
    ndjson = form.select_one('[data-export-format="ndjson"]')
    assert {
        button["data-export-action"]: (button["type"], button.get("formaction"))
        for button in ndjson.select("button")
    } == {
        "copy": ("button", None),
        "view": ("submit", "/data/numbers.ndjson"),
        "download": ("submit", "/data/numbers.ndjson"),
        "copy-url": ("button", None),
    }
    # They return up to max_returned_rows rows, like CSV
    assert ndjson.select_one(".export-rows")["data-export-rows"] == "csv"
    # Formats that cannot stream stay in the list below, as links
    other = dialog.select_one("ul.export-options")
    assert other.select_one('a[href^="/data/numbers.notstreaming"]') is not None
    assert other.select_one('a[href^="/data/numbers.ndjson"]') is None
    assert form.select_one('[formaction="/data/numbers.notstreaming"]') is None


@pytest.mark.asyncio
async def test_export_dialog_binary_format_that_can_stream(ds):
    from bs4 import BeautifulSoup

    def render_binary(row_stream):
        async def body():
            async for row in row_stream:
                yield bytes([row["id"]])

        return Response.stream(body(), content_type="application/octet-stream")

    class BinaryPlugin:
        __name__ = "BinaryPlugin"

        @hookimpl
        def register_output_renderer(self):
            return {
                "extension": "bin",
                "render": render_binary,
                "label": "Binary",
                "is_binary": True,
            }

    pm.register(BinaryPlugin(), name="binary_renderer")
    try:
        binary_ds = _make_datasette()
        await _add_numbers(binary_ds)
        response = await binary_ds.client.get("/data/numbers?_next=10")
        binary_ds.close()
    finally:
        pm.unregister(name="binary_renderer")
    dialog = BeautifulSoup(response.text, "html.parser").select_one("#export-dialog")
    row = dialog.select_one('form.export-csv-form [data-export-format="bin"]')
    assert row["data-export-kind"] == "binary"
    # Binary formats offer Download and Copy URL, not Copy or View
    assert [
        (button["data-export-action"], button.get("formaction"))
        for button in row.select("button")
    ] == [("download", "/data/numbers.bin"), ("copy-url", None)]
    assert row.select_one(".export-option-name").text == "Binary"
    note = dialog.select_one("#export-page-note").text
    assert "to export every row as TSV, CSV, " in note
    assert "Binary" in note
