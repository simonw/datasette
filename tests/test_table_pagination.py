"""
Tests for the table page pagination bar and the ?_count=before option of the
/-/count endpoint, which tells pages after the first which rows they show.
"""

import sqlite3
import urllib.parse

import pytest
import pytest_asyncio
from bs4 import BeautifulSoup

from datasette.app import Datasette

PAGE_SIZE = 4


@pytest_asyncio.fixture
async def pages_ds(tmp_path):
    ds = Datasette([tmp_path / "pages.db"])
    db = ds.get_database()
    # score has NULLs and repeated values so sorting by it exercises both the
    # null handling and the primary key tie-breaker in the _next cursor
    await db.execute_write_script("""
        create table items (id integer primary key, score integer, name text);
        insert into items values
            (1, 5, 'a'), (2, null, 'b'), (3, 3, 'c'), (4, 5, 'd'),
            (5, null, 'e'), (6, 1, 'f'), (7, 3, 'g'), (8, 5, 'h'),
            (9, null, 'i'), (10, 2, 'j'), (11, 3, 'k'), (12, 4, 'l'),
            (13, null, 'm'), (14, 5, 'n'), (15, 1, 'o'), (16, 2, 'p'),
            (17, 3, 'q'), (18, null, 'r'), (19, 4, 's');
        create table compound (pk1 text, pk2 integer, value text,
            primary key (pk1, pk2));
        insert into compound values
            ('a', 1, 'x'), ('a', 2, 'y'), ('a', 3, null), ('b', 1, 'x'),
            ('b', 2, null), ('c', 1, 'z'), ('c', 2, 'x'), ('c', 3, 'y'),
            ('d', 1, 'x'), ('d', 2, 'z');
        create table no_pk (name text, score integer);
        insert into no_pk values
            ('a', 3), ('b', null), ('c', 1), ('d', 3), ('e', 2), ('f', null),
            ('g', 5), ('h', 4), ('i', 1), ('j', 2), ('k', null);
        create view items_view as select id, score from items;
        create table small (id integer primary key);
        insert into small values (1), (2);
    """)
    yield ds
    ds.close()


async def count_before(ds, table, query, next_value):
    args = dict(urllib.parse.parse_qsl(query))
    args.update({"_next": next_value, "_count": "before"})
    response = await ds.client.post(
        f"/pages/{table}/-/count?" + urllib.parse.urlencode(args)
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()["count"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table,query",
    [
        ("items", ""),
        ("items", "_sort=score"),
        ("items", "_sort_desc=score"),
        ("items", "_sort=name"),
        ("items", "_sort_desc=name"),
        ("items", "score__gte=2&_sort=score"),
        ("items", "score__notnull=1&_sort_desc=score"),
        ("compound", ""),
        ("compound", "_sort=value"),
        ("compound", "_sort_desc=value"),
        ("no_pk", ""),
        ("no_pk", "_sort=score"),
        ("no_pk", "_sort_desc=score"),
        ("items_view", ""),
        ("items_view", "_sort_desc=score"),
    ],
)
async def test_count_before_matches_every_page(pages_ds, table, query):
    # Walk every page through the JSON API, checking that each page's
    # count-before is the number of rows on all of the pages before it
    seen = 0
    next_value = None
    pages = 0
    while True:
        args = dict(urllib.parse.parse_qsl(query))
        args["_size"] = PAGE_SIZE
        if next_value:
            args["_next"] = next_value
            assert await count_before(pages_ds, table, query, next_value) == seen
        response = await pages_ds.client.get(
            f"/pages/{table}.json?" + urllib.parse.urlencode(args)
        )
        assert response.status_code == 200, response.text
        data = response.json()
        seen += len(data["rows"])
        pages += 1
        next_value = data["next"]
        if not next_value:
            break
    assert pages > 2
    total = (
        await pages_ds.client.post(
            f"/pages/{table}/-/count?"
            + urllib.parse.urlencode(dict(urllib.parse.parse_qsl(query)))
        )
    ).json()["count"]
    assert seen == total


@pytest.mark.asyncio
async def test_count_without_count_before_ignores_next(pages_ds):
    # The "count all" button posts the page's own query string, which may
    # include _next - that must still count every matching row
    response = await pages_ds.client.post("/pages/items/-/count?_next=10")
    assert response.json() == {"ok": True, "count": 19}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        # A cursor with the wrong number of primary key components
        "/pages/compound/-/count?_next=a&_count=before",
        # Views use integer offsets
        "/pages/items_view/-/count?_next=abc&_count=before",
    ],
)
async def test_count_before_invalid_next(pages_ds, path):
    response = await pages_ds.client.post(path)
    assert response.status_code == 400
    assert response.json()["ok"] is False


def bars(html):
    soup = BeautifulSoup(html, "html.parser")
    header = soup.select_one("nav.page-position:not(.page-position-footer)")
    footer = soup.select_one("nav.page-position-footer")
    return soup, header, footer


def link_text(nav):
    return [a.get_text(" ", strip=True) for a in nav.select("a")]


@pytest.mark.asyncio
async def test_no_bar_when_results_fit_on_one_page(pages_ds):
    response = await pages_ds.client.get("/pages/small")
    soup, header, footer = bars(response.text)
    assert header is None
    assert footer is None
    assert soup.select_one("h3.table-summary").get_text(strip=True) == "2 rows"


@pytest.mark.asyncio
async def test_bar_on_first_page(pages_ds):
    response = await pages_ds.client.get(f"/pages/items?_size={PAGE_SIZE}")
    soup, header, footer = bars(response.text)
    # The row count moves from the heading into the bar
    assert soup.select_one("h3.table-summary") is None
    assert header.select_one(".page-position-text").get_text(" ", strip=True) == (
        "Rows 1–4 of 19"
    )
    # The first page already knows its position, so there is nothing to fetch
    assert "data-count-before-url" not in header.attrs
    assert link_text(header) == ["Next page"]
    assert link_text(footer) == ["Next page"]
    assert header.select_one("a.page-position-next")["href"] == (
        f"http://localhost/pages/items?_size={PAGE_SIZE}&_next=4"
    )
    # The footer has the buttons but not the row count
    assert footer.select_one(".page-position-text") is None


@pytest.mark.asyncio
async def test_bar_on_later_page(pages_ds):
    response = await pages_ds.client.get(
        f"/pages/items?_size={PAGE_SIZE}&score__gte=2&_sort=score&_next=3,7"
    )
    soup, header, footer = bars(response.text)
    # The heading keeps the filter description but loses the count
    assert soup.select_one("h3.table-summary").get_text(" ", strip=True) == (
        "where score ≥ 2 sorted by score"
    )
    # Fallback text shown until JavaScript fills in the row numbers
    assert header.select_one(".page-position-text").get_text(" ", strip=True) == (
        "Continuing after score = 3 · 12 rows"
    )
    assert header["data-page-rows"] == "4"
    assert header["data-total"] == "12"
    assert header["data-count-before-url"] == (
        "/pages/items/-/count?_size=4&score__gte=2&_sort=score&_next=3%2C7"
        "&_count=before"
    )
    assert link_text(header) == ["Back to first page", "Next page"]
    assert link_text(footer) == ["Back to first page", "Next page"]
    # Back to first page keeps the filters and sort but drops the cursor
    assert header.select_one("a.page-position-first")["href"] == (
        "/pages/items?_size=4&score__gte=2&_sort=score"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,expected",
    [
        ("_next=8", "Continuing after id = 8"),
        ("_sort=score&_next=$null,9", "Continuing after score = null"),
    ],
)
async def test_bar_fallback_describes_cursor(pages_ds, query, expected):
    response = await pages_ds.client.get(f"/pages/items?_size={PAGE_SIZE}&{query}")
    _, header, _ = bars(response.text)
    assert (
        header.select_one(".page-position-text")
        .get_text(" ", strip=True)
        .startswith(expected)
    )


@pytest.mark.asyncio
async def test_bar_on_last_page(pages_ds):
    response = await pages_ds.client.get(f"/pages/items?_size={PAGE_SIZE}&_next=16")
    _, header, footer = bars(response.text)
    assert link_text(header) == ["Back to first page"]
    assert link_text(footer) == ["Back to first page"]


@pytest.mark.asyncio
async def test_bar_keeps_truncated_count_in_heading(pages_ds):
    # When the count is truncated the heading keeps its "count all" button
    # and the bar does not claim a total
    pages_ds.get_database("pages").count_limit = 5
    response = await pages_ds.client.get(f"/pages/items?_size={PAGE_SIZE}&_next=4")
    soup, header, _ = bars(response.text)
    assert soup.select_one("button.count-all") is not None
    assert "data-total" not in header.attrs
    assert "rows" not in header.select_one(".page-position-text").get_text()


@pytest.mark.asyncio
async def test_bar_base_url(tmp_path):
    ds = Datasette([tmp_path / "pages.db"], settings={"base_url": "/prefix/"})
    db = ds.get_database()
    await db.execute_write_script(
        "create table t (id integer primary key);"
        "insert into t values (1), (2), (3), (4), (5);"
    )
    try:
        response = await ds.client.get("/pages/t?_size=2&_next=2")
        _, header, _ = bars(response.text)
        assert header["data-count-before-url"] == (
            "/prefix/pages/t/-/count?_size=2&_next=2&_count=before"
        )
        assert header.select_one("a.page-position-first")["href"] == (
            "/prefix/pages/t?_size=2"
        )
        response = await ds.client.post(
            header["data-count-before-url"].removeprefix("/prefix"), json={}
        )
        assert response.json() == {"ok": True, "count": 2}
    finally:
        ds.close()


@pytest.mark.parametrize(
    "pks,use_rowid,next_value",
    [
        (["pk1", "pk2"], False, "b,2"),
        (["id"], False, "10"),
        ([], True, "10"),
    ],
)
def test_count_before_uses_primary_key_index(pks, use_rowid, next_value):
    # The count of earlier rows should be an index range search, not a scan
    # of the whole table - so its cost grows with how far into the table the
    # page is rather than with the size of the table
    from datasette.views.table import _at_or_before_next_where_clause

    conn = sqlite3.connect(":memory:")
    if use_rowid:
        conn.execute("create table t (name text)")
    else:
        conn.execute(
            "create table t ({}, value text, primary key ({}))".format(
                ", ".join(f"{pk} text" for pk in pks), ", ".join(pks)
            )
        )
    params = {}
    clause = _at_or_before_next_where_clause(
        next_value, None, None, pks, use_rowid, params
    )
    plan = " ".join(
        row[-1]
        for row in conn.execute(
            f"explain query plan select count(*) from t where {clause}", params
        )
    )
    assert plan.startswith("SEARCH"), plan
