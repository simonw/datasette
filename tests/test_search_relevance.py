import csv
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
import pytest_asyncio
from bs4 import BeautifulSoup

from datasette.app import Datasette


@pytest_asyncio.fixture
async def search_ds(tmp_path):
    ds = Datasette(
        [tmp_path / "search.db"],
        config={
            "databases": {
                "search": {
                    "tables": {
                        "manuals": {
                            "fts_table": "MANUALS INDEX",
                            "fts_pk": "id",
                            "sort_desc": "title",
                        },
                        "notes_view": {"fts_table": "notes_fts"},
                    }
                }
            }
        },
    )
    try:
        await ds.get_database().execute_write_script("""
            create table manuals (
                title text primary key, id integer unique, body text, category text,
                _SEARCH_RANK text, _search_rowid text
            );
            insert into manuals values
                ('A', 101, 'a long guide to waterproof fabrics and liners', 'guide', 'a', 'keep'),
                ('B', 102, 'waterproof waterproof', 'guide', 'b', 'keep'),
                ('C', 103, 'waterproof waterproof', 'guide', 'c', 'keep'),
                ('D', 104, 'waterproof', 'guide', 'd', 'skip'),
                ('E', 105, 'a manual for packing rope', 'guide', 'e', 'keep'),
                ('F', 106, 'waterproof', 'checklist', 'f', 'keep'),
                ('G', 100, 'waterproof', 'guide', 'g', 'keep');
            create virtual table "manuals index" using fts5(body);
            insert into "manuals index"(rowid, body) select id, body from manuals;
            create table notes (body text);
            insert into notes values ('a long note about waterproof jackets'),
                ('waterproof waterproof');
            create virtual table notes_fts using fts5(body, content="notes");
            insert into notes_fts(notes_fts) values ('rebuild');
            create view notes_view as select rowid, body from notes;
            create table legacy (body text);
            insert into legacy select body from notes;
            create virtual table legacy_fts using fts4(body, content="legacy");
            insert into legacy_fts(legacy_fts) values ('rebuild');
        """)
        yield ds
    finally:
        ds.close()


async def test_search_relevance_table_link_and_column_sort(search_ds):
    path = "/search/manuals?_search=waterproof&category=guide"
    page = await search_ds.client.get(path)
    assert page.status_code == 200
    soup = BeautifulSoup(page.text, "html.parser")
    link = soup.select_one(".search-sort a")
    assert link is not None
    assert link.get_text(strip=True) == "Sort by relevance"
    assert parse_qs(urlsplit(link["href"]).query) == {
        "_search": ["waterproof"],
        "category": ["guide"],
        "_searchsort": ["relevance"],
    }
    ranked = await search_ds.client.get(link["href"])
    assert ranked.status_code == 200
    soup = BeautifulSoup(ranked.text, "html.parser")
    result_titles = [
        cell.get_text(strip=True) for cell in soup.select("tbody td.col-title")
    ]
    assert result_titles == ["B", "C", "D", "G", "A"]
    default = soup.select_one(".search-sort a")
    assert default.get_text(strip=True) == "Use default sort"
    assert default["href"] == path
    default_json = await search_ds.client.get(path.replace("manuals?", "manuals.json?"))
    assert [row["title"] for row in default_json.json()["rows"]] == [
        "G",
        "D",
        "C",
        "B",
        "A",
    ]

    header = soup.select_one("th.col-title a")
    column_sorted = await search_ds.client.get(header["href"])
    assert column_sorted.status_code == 200
    soup = BeautifulSoup(column_sorted.text, "html.parser")
    assert [
        cell.get_text(strip=True) for cell in soup.select("tbody td.col-title")
    ] == ["A", "B", "C", "D", "G"]


async def test_search_relevance_pagination_filters_facets_and_csv(search_ds):
    query = (
        "_search=water*&_searchmode=raw&_searchsort=relevance&category=guide"
        "&_search_rowid__exact=keep&_where=manuals.id>100&_size=1"
        "&_extra=count&_facet=title"
    )
    url = f"/search/manuals.json?{query}"
    titles = []
    for offset in range(1, 4):
        response = await search_ds.client.get(url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["count"] == 3
        assert {
            facet["value"]: facet["count"]
            for facet in data["facet_results"]["results"]["title"]["results"]
        } == {"A": 1, "B": 1, "C": 1}
        row = data["rows"][0]
        titles.append(row["title"])
        assert set(row) == {
            "title",
            "id",
            "body",
            "category",
            "_SEARCH_RANK",
            "_search_rowid",
        }
        assert row["_SEARCH_RANK"] == row["title"].lower()
        if offset < 3:
            assert data["next"] == str(offset)
            url = data["next_url"].removeprefix("http://localhost")
            next_query = urlsplit(url).query
            count = await search_ds.client.post(
                f"/search/manuals/-/count?{next_query}&_count=before"
            )
            assert count.json() == {"ok": True, "count": offset}
        else:
            assert data["next_url"] is None
    assert titles == ["B", "C", "A"]

    exported = await search_ds.client.get(f"/search/manuals.csv?{query}&_stream=1")
    assert exported.status_code == 200
    assert [
        row["title"] for row in csv.DictReader(exported.text.splitlines())
    ] == titles


async def test_search_relevance_agrees_with_count_and_facets_for_or_filter(search_ds):
    query = urlencode(
        {
            "_search": "waterproof",
            "_searchsort": "relevance",
            "_where": "title in ('A', 'E') or title in ('B', 'E')",
            "_extra": "count",
            "_facet": "category",
        }
    )
    response = await search_ds.client.get(f"/search/manuals.json?{query}")
    assert response.status_code == 200, response.text
    data = response.json()
    assert [row["title"] for row in data["rows"]] == ["B", "A"]
    assert data["count"] == 2
    categories = data["facet_results"]["results"]["category"]["results"]
    assert [(category["value"], category["count"]) for category in categories] == [
        ("guide", 2)
    ]
    count = await search_ds.client.post(f"/search/manuals/-/count?{query}")
    assert count.json() == {"ok": True, "count": 2}


@pytest.mark.parametrize(
    "table,query,rows,show_link",
    [
        ("notes", "_search=waterproof", [2, 1], True),
        ("notes", "_search_body=waterproof", [1, 2], False),
        ("notes", "", [1, 2], False),
        ("notes_view", "_search=waterproof", [1, 2], False),
        ("legacy", "_search=waterproof", [1, 2], False),
    ],
)
async def test_search_relevance_availability(search_ds, table, query, rows, show_link):
    query = f"_searchsort=relevance&{query}"
    response = await search_ds.client.get(f"/search/{table}.json?{query}")
    assert response.status_code == 200, response.text
    assert [row["rowid"] for row in response.json()["rows"]] == rows
    page = await search_ds.client.get(f"/search/{table}?{query}")
    assert page.status_code == 200
    assert (
        bool(BeautifulSoup(page.text, "html.parser").select_one(".search-sort a"))
        == show_link
    )


@pytest.mark.parametrize("token", ["invalid", "-1"])
async def test_search_relevance_invalid_page(search_ds, token):
    response = await search_ds.client.get(
        f"/search/notes.json?_search=waterproof&_searchsort=relevance&_next={token}"
    )
    assert response.status_code == 400
