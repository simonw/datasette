import pytest
import pytest_asyncio

from datasette import hookimpl
from datasette.app import Datasette
from datasette.database import QueryInterrupted
from datasette.filters import FilterArguments
from datasette.plugins import pm
from datasette.utils import tilde_encode


@pytest_asyncio.fixture
async def count_ds(tmp_path):
    ds = Datasette(
        [tmp_path / "counts.db"],
        config={"permissions": {"execute-sql": {"id": "sql-user"}}},
    )
    db = ds.get_database()
    await db.execute_write_script("""
        create table numbers (id integer primary key, name text);
        insert into numbers values (1, 'one'), (2, 'two'), (3, 'three'),
            (4, 'four'), (5, 'five'), (6, 'six'), (7, 'seven');
        create view number_view as select id + 0 as number from numbers;
        create table "a/b.c" (id integer);
        insert into "a/b.c" values (1);
        create virtual table numbers_fts using fts5(name, content="numbers");
        insert into numbers_fts(numbers_fts) values ('rebuild');
    """)
    db.count_limit = 2
    yield ds
    ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table,query,count",
    [
        ("numbers", "", 7),
        ("numbers", "?id__gt=1", 6),
        ("numbers", "?id__gt=1&id__lt=5", 3),
        ("numbers", "?id__gt=1&id__gt=3", 4),
        ("numbers", "?id__gt=100", 0),
        ("numbers", "?name__contains=o", 3),
        ("numbers", "?_search=two", 1),
        ("numbers", "?_search_name=three", 1),
        ("numbers", "?id__gt=1&_next=5&_size=1&_sort_desc=id&_nocount=1", 6),
        # A computed column without affinity catches string/integer casting bugs.
        ("number_view", "?number__gt=1", 6),
        ("a/b.c", "", 1),
    ],
)
async def test_count(count_ds, table, query, count):
    response = await count_ds.client.post(
        f"/counts/{tilde_encode(table)}/-/count{query}"
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "count": count}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get", "head", "put", "delete"])
async def test_count_post_only(count_ds, method):
    response = await count_ds.client.request(method, "/counts/numbers/-/count")
    assert response.status_code == 405


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,status",
    [
        ("/missing/numbers/-/count", 404),
        ("/counts/missing/-/count", 404),
        ("/counts/numbers/-/count?_fts_table=invalid", 400),
        ("/counts/numbers/-/count?_where=id>1", 403),
    ],
)
async def test_count_errors(count_ds, path, status):
    response = await count_ds.client.post(
        path, json={}, headers={"Accept": "application/json"}
    )
    assert response.status_code == status, response.text
    assert response.json()["ok"] is False


@pytest.mark.asyncio
async def test_count_permissions(tmp_path):
    ds = Datasette(
        [tmp_path / "counts.db"], config={"permissions": {"view-table": False}}
    )
    await ds.get_database().execute_write("create table secret (id integer)")
    try:
        response = await ds.client.post(
            "/counts/secret/-/count", json={}, headers={"Accept": "application/json"}
        )
        assert response.status_code == 403
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_count_plugin_filter(count_ds):
    class Plugin:
        @hookimpl
        def filters_from_request(self, request):
            if request.args.get("_custom"):
                return FilterArguments(["id > :custom"], params={"custom": 4})

    plugin = Plugin()
    pm.register(plugin)
    try:
        response = await count_ds.client.post(
            "/counts/numbers/-/count?_custom=1",
            json={},
            headers={"Accept": "application/json"},
        )
        assert response.json() == {"ok": True, "count": 3}
    finally:
        pm.unregister(plugin)


@pytest.mark.asyncio
async def test_count_timeout(count_ds, monkeypatch):
    db = count_ds.get_database()
    original = db.execute

    async def execute(sql, *args, **kwargs):
        if sql.startswith("select count(*)"):
            raise QueryInterrupted(Exception("interrupted"), sql, {})
        return await original(sql, *args, **kwargs)

    monkeypatch.setattr(db, "execute", execute)
    response = await count_ds.client.post(
        "/counts/numbers/-/count", json={}, headers={"Accept": "application/json"}
    )
    assert response.status_code == 400
    assert response.json()["errors"] == ["Count query timed out"]


@pytest.mark.asyncio
async def test_count_button_without_execute_sql(count_ds):
    from bs4 import BeautifulSoup

    response = await count_ds.client.get("/counts/numbers?id__gt=1")
    soup = BeautifulSoup(response.text, "html.parser")
    button = soup.select_one("button.count-all")
    assert button["data-count-url"] == "/counts/numbers/-/count"
    assert soup.select_one(".table-count").text == "2+ rows"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,status,count",
    [
        ("?_where=id>2&_where=id<6", 200, 3),
        ("?_where=invalid_sql(", 400, None),
    ],
)
async def test_count_where(count_ds, query, status, count):
    response = await count_ds.client.post(
        "/counts/numbers/-/count" + query,
        json={},
        actor={"id": "sql-user"},
    )
    assert response.status_code == status
    if status == 200:
        assert response.json() == {"ok": True, "count": count}
    else:
        assert response.json()["ok"] is False


@pytest.mark.asyncio
async def test_count_base_url(tmp_path):
    from bs4 import BeautifulSoup

    ds = Datasette([tmp_path / "counts.db"], settings={"base_url": "/prefix/"})
    db = ds.get_database()
    await db.execute_write_script(
        "create table numbers (id integer); insert into numbers values (1), (2)"
    )
    db.count_limit = 1
    try:
        response = await ds.client.get("/counts/numbers")
        button = BeautifulSoup(response.text, "html.parser").select_one(".count-all")
        assert button["data-count-url"] == "/prefix/counts/numbers/-/count"
        response = await ds.client.post(
            ds.urls.path("/counts/numbers/-/count"), json={}
        )
        assert response.json() == {"ok": True, "count": 2}
    finally:
        ds.close()
