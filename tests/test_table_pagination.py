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
from datasette.utils import tilde_encode

PAGE_SIZE = 4


@pytest.mark.asyncio
@pytest.mark.parametrize("descending", [False, True])
@pytest.mark.parametrize(
    "declaration,values",
    [
        ("", [-(2**63), -3, 0, 2**53 + 1, 2**63 - 1]),
        ("blob", [1, 2, 3, 4, 5]),
        ("ctas", [1, 2, 3, 4, 5]),
        ("", [-1.5, -0.0, 0.1, 1.0000000000000002, 1e100]),
        ("", [None, -3, 1.25, 2, "", "10", "2", b"", b"\x00", b"\xff"]),
        ("text", ["", "$null", "10", "2", "b:ff", "f:0x1p+2", "i:2", "~3A", "a+b"]),
        ("integer", [1, 2, 3, 4, 5]),
        ("real", [-1.5, 0.1, 1.25, 2.5, 1e100]),
        ("text", [1, 2, 3, 4, 5]),
    ],
)
async def test_sorted_pagination_preserves_storage_classes(
    tmp_path, declaration, values, descending
):
    path = tmp_path / "pages.db"
    with sqlite3.connect(path) as conn:
        if declaration == "ctas":
            conn.execute(
                "create table items as select 1 as score union all select 2 "
                "union all select 3 union all select 4 union all select 5"
            )
            key = "rowid"
        else:
            conn.execute(
                f"create table items (id integer primary key, score {declaration})"
            )
            conn.executemany(
                "insert into items (score) values (?)", [(v,) for v in values]
            )
            key = "id"
        expected = conn.execute(
            f"select {key}, score from items order by score {'desc' if descending else 'asc'}"
        ).fetchall()
    ds = Datasette([path])
    query = urllib.parse.urlencode(
        {"_size": 2, "_sort_desc" if descending else "_sort": "score"}
    )
    url = f"/pages/items.json?{query}"
    seen = []
    try:
        for _ in range(len(values) + 1):
            response = await ds.client.get(url)
            assert response.status_code == 200, response.text
            data = response.json()
            seen.extend(row[key] for row in data["rows"])
            if data["next"] is None:
                break
            assert await count_before(ds, "items", query, data["next"]) == len(seen)
            if len(seen) == 2:
                html = await ds.client.get(
                    "/pages/items?"
                    + query
                    + "&_next="
                    + urllib.parse.quote_plus(data["next"])
                )
                assert html.status_code == 200, html.text
                _, header, _ = bars(html.text)
                after_value = expected[1][1]
                assert (
                    header.select_one(".page-position-text")
                    .get_text(" ", strip=True)
                    .startswith(
                        "Continuing after score = "
                        + ("null" if after_value is None else str(after_value))
                    )
                )
            url = data["next_url"].removeprefix("http://localhost")
        else:
            pytest.fail("Pagination did not finish")
        assert seen == [row[0] for row in expected]
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("descending", [False, True])
@pytest.mark.parametrize("key_type", ["rowid", "integer", "text", "compound"])
@pytest.mark.parametrize("omit_score", [False, True])
async def test_sorted_scalar_cursor_primary_keys(
    tmp_path, descending, key_type, omit_score
):
    path = tmp_path / "pages.db"
    schema = {
        "rowid": "id integer",
        "integer": "id integer primary key",
        "text": "id text primary key",
        "compound": "id text, part integer, primary key (id, part)",
    }[key_type]
    values = [None, -3, 1.25, 2**53 + 1, "", "10", "2", "i:2", b"\x00"]
    with sqlite3.connect(path) as conn:
        conn.execute(f"create table items (score, {schema})")
        for i, value in enumerate(values):
            key = f"key,{i}" if key_type in ("text", "compound") else i
            if key_type == "compound":
                conn.execute("insert into items values (?, ?, 1)", (value, key))
            else:
                conn.execute("insert into items values (?, ?)", (value, key))
        expected = conn.execute(
            f"select id from items order by score {'desc' if descending else 'asc'}"
        ).fetchall()
    ds = Datasette([path])
    args = {"_size": 2, "_sort_desc" if descending else "_sort": "score"}
    if omit_score:
        args["_col"] = "id"
    query = urllib.parse.urlencode(args)
    url = f"/pages/items.json?{query}"
    seen = []
    try:
        for _ in range(len(values) + 1):
            response = await ds.client.get(url)
            assert response.status_code == 200, response.text
            data = response.json()
            seen.extend(row["id"] for row in data["rows"])
            if data["next"] is None:
                break
            assert await count_before(ds, "items", query, data["next"]) == len(seen)
            url = data["next_url"].removeprefix("http://localhost")
        else:
            pytest.fail("Pagination did not finish")
        assert seen == [row[0] for row in expected]
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token", ["i:no", "i:9223372036854775808", "f:no", "f:nan", "f:0x1p+99999", "b:zz"]
)
async def test_invalid_sorted_scalar_cursor(pages_ds, token):
    query = urllib.parse.urlencode({"_sort": "score", "_next": token + ",1"})
    for method, path in [
        ("get", "/pages/items.json"),
        ("post", "/pages/items/-/count"),
    ]:
        response = await getattr(pages_ds.client, method)(
            path + "?" + query + "&_count=before"
        )
        assert response.status_code == 400
        assert response.json()["error"] == "Invalid _next"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2", "2"),
        ("", ""),
        ("$null", None),
        ("i:2", 2),
        ("f:0x1.0000000000001p+0", 1.0000000000000002),
        ("b:00ff", b"\x00\xff"),
    ]
    + [
        (tilde_encode(value), value)
        for value in ["$null", "i:2", "f:0x1p+2", "b:ff", "a+b"]
    ],
)
def test_parse_sorted_scalar_cursor(raw, expected):
    from datasette.views.table import _parse_next

    value, components = _parse_next(raw + ",key~2C1", "score", None)
    assert value == expected
    assert type(value) is type(expected)
    assert components == ["key,1"]


@pytest.mark.parametrize(
    "value",
    [
        None,
        -(2**63),
        2**63 - 1,
        -0.0,
        0.1,
        1e100,
        float.fromhex("0x1p-1074"),
        float.fromhex("0x1.fffffffffffffp+1023"),
        float("inf"),
        float("-inf"),
        b"",
        b"\x00\xff",
        "",
        "$null",
        "i:2",
        "f:0x1p+2",
        "b:ff",
    ],
)
def test_sort_scalar_round_trip(value):
    from datasette.views.table import _encode_sort_value, _parse_next

    decoded, components = _parse_next(_encode_sort_value(value) + ",1", "score", None)
    assert decoded == value
    assert type(decoded) is type(value)
    if isinstance(value, float):
        assert decoded.hex() == value.hex()
    assert components == ["1"]


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
    # The heading keeps the total row count, the bar repeats it
    assert soup.select_one("h3.table-summary").get_text(strip=True) == "19 rows"
    assert header.select_one(".page-position-text").get_text(" ", strip=True) == (
        "Rows 1–4 of 19"
    )
    # The first page already knows its position, so there is nothing to fetch
    assert "data-count-before-url" not in header.attrs
    assert link_text(header) == ["Next page"]
    # Fewer than 5 rows on the page, so no second bar below the table
    assert footer is None
    assert header.select_one("a.page-position-next")["href"] == (
        f"http://localhost/pages/items?_size={PAGE_SIZE}&_next=4"
    )


@pytest.mark.asyncio
async def test_bar_on_later_page(pages_ds):
    response = await pages_ds.client.get(
        f"/pages/items?_size={PAGE_SIZE}&score__gte=2&_sort=score&_next=3,7"
    )
    soup, header, footer = bars(response.text)
    # The heading shows the count and the filter description
    assert soup.select_one("h3.table-summary").get_text(" ", strip=True) == (
        "12 rows where score ≥ 2 sorted by score"
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
    assert footer is None
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
    assert footer is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,expected_links",
    [
        ("_size=5", ["Next page"]),
        ("_size=5&_next=5", ["Back to first page", "Next page"]),
    ],
)
async def test_footer_bar_on_pages_with_five_or_more_rows(
    pages_ds, query, expected_links
):
    response = await pages_ds.client.get(f"/pages/items?{query}")
    _, header, footer = bars(response.text)
    assert link_text(header) == expected_links
    assert link_text(footer) == expected_links
    # The footer has the buttons but not the row count
    assert footer.select_one(".page-position-text") is None


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
