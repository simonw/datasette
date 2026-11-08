"""
Tests for the homepage: one catalog of tables, views and queries across every
database, with search, a database filter, pagination and plugin results.
"""

import json

import pytest
import pytest_asyncio
from bs4 import BeautifulSoup as Soup

from datasette import hookimpl
from datasette.app import Datasette
from datasette.jump import JumpSQL


@pytest_asyncio.fixture
async def ds():
    ds = Datasette(
        config={
            "databases": {
                "books": {
                    "tables": {"secret": {"allow": {"id": "root"}}},
                    "queries": {
                        "cheap_books": {
                            "title": "Cheap books",
                            "description": "Books under five pounds",
                            "sql": "select * from books where price < 5",
                        },
                        "by_author": {
                            "sql": "select * from books where author = :author",
                        },
                    },
                }
            }
        },
        metadata={
            "databases": {
                "books": {
                    "tables": {"books": {"description": "Every book in the library"}}
                }
            }
        },
    )
    await ds.invoke_startup()
    books = ds.add_memory_database("books")
    await books.execute_write_script("""
        create table authors (id integer primary key, name text);
        create table books (
            id integer primary key,
            title text,
            price real,
            author_id integer references authors(id)
        );
        create table secret (
            id integer primary key,
            password_hint text,
            author_id integer references authors(id)
        );
        create view expensive_books as select * from books where price > 20;
        insert into authors (name) values ('Ann'), ('Bo');
        insert into books (title, price, author_id) values
            ('One', 3, 1), ('Two', 30, 2), ('Three', 10, 1);
        create virtual table books_fts using fts5(title, content="books");
    """)
    music = ds.add_memory_database("music")
    await music.execute_write(
        "create table albums (id integer primary key, artist text)"
    )
    # Saved queries belonging to individual users
    await ds.add_query(
        "books",
        "alices_query",
        "select title from books where price > 1",
        title="Alice's query",
        source="user",
        owner_id="alice",
    )
    return ds


def cookies(ds, actor_id):
    return {"ds_actor": ds.client.actor_cookie({"id": actor_id})}


def item_links(html):
    soup = Soup(html, "html.parser")
    return [a["href"] for a in soup.select("a.catalog-item-name")]


def query_links(html):
    soup = Soup(html, "html.parser")
    return [a["href"] for a in soup.select("a.query-row-name")]


@pytest.mark.asyncio
async def test_homepage_lists_tables_and_views_from_all_databases(ds):
    response = await ds.client.get("/")
    assert response.status_code == 200
    links = item_links(response.text)
    assert set(links) >= {
        "/books/authors",
        "/books/books",
        "/books/expensive_books",
        "/music/albums",
    }
    # FTS tables are hidden, the private table needs permission
    assert not any("books_fts" in link for link in links)
    assert "/books/secret" not in links
    soup = Soup(response.text, "html.parser")
    books_item = soup.select_one(
        'a.catalog-item-name[href="/books/books"]'
    ).find_parent("li")
    assert "Every book in the library" in books_item.text
    assert "3 rows" in books_item.text
    assert books_item.select_one(".db-tag").text == "books"
    assert "full-text search" in books_item.text
    view_item = soup.select_one(
        'a.catalog-item-name[href="/books/expensive_books"]'
    ).find_parent("li")
    assert view_item.select_one(".type-tag-view").text == "view"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor_id,expected_links,expected_title",
    (
        # secret also links to authors, but anonymous users cannot see it
        (None, "links to 1 table", "authors"),
        ("root", "links to 2 tables", "books, secret"),
    ),
)
async def test_homepage_linked_tables(ds, actor_id, expected_links, expected_title):
    response = await ds.client.get(
        "/", cookies=cookies(ds, actor_id) if actor_id else None
    )
    soup = Soup(response.text, "html.parser")

    def links(href):
        item = soup.select_one(f'a.catalog-item-name[href="{href}"]').find_parent("li")
        return item.select_one(".catalog-item-links")

    if actor_id is None:
        books = links("/books/books")
        assert books.text == expected_links
        assert books["title"] == "Linked by foreign keys to: " + expected_title
        assert "secret" not in links("/books/authors")["title"]
    else:
        authors = links("/books/authors")
        assert authors.text == expected_links
        assert authors["title"] == "Linked by foreign keys to: " + expected_title
    # Tables with no foreign keys show nothing
    assert links("/music/albums") is None


@pytest.mark.asyncio
async def test_homepage_private_table_shown_with_permission(ds):
    response = await ds.client.get("/", cookies=cookies(ds, "root"))
    soup = Soup(response.text, "html.parser")
    secret = soup.select_one('a.catalog-item-name[href="/books/secret"]')
    assert secret is not None
    assert "🔒" in secret.find_parent("li").text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "q,expected_link,expected_mark",
    (
        # Table name
        ("auth", "/books/authors", None),
        # Column name - the matching column is highlighted
        ("artist", "/music/albums", "artist"),
        # Description from metadata
        ("library", "/books/books", None),
    ),
)
async def test_homepage_search(ds, q, expected_link, expected_mark):
    response = await ds.client.get("/", params={"q": q})
    links = item_links(response.text)
    assert links[0] == expected_link
    soup = Soup(response.text, "html.parser")
    assert "homepage-searching" in soup.select_one("[data-homepage]")["class"]
    if expected_mark:
        item = soup.select_one(f'a.catalog-item-name[href="{expected_link}"]')
        marks = [m.text for m in item.find_parent("li").select("mark")]
        assert marks == [expected_mark]


@pytest.mark.asyncio
async def test_homepage_search_never_shows_private_tables(ds):
    response = await ds.client.get("/", params={"q": "password"})
    assert "/books/secret" not in item_links(response.text)
    response = await ds.client.get(
        "/", params={"q": "password"}, cookies=cookies(ds, "root")
    )
    assert item_links(response.text) == ["/books/secret"]


@pytest.mark.asyncio
async def test_homepage_queries_default_selection(ds):
    # Without a search: config queries, not other people's saved queries
    response = await ds.client.get("/")
    links = query_links(response.text)
    assert links == ["/books/cheap_books", "/books/by_author"]
    soup = Soup(response.text, "html.parser")
    rows = soup.select("li.query-row")
    assert "Books under five pounds" in rows[0].text
    # Inputs derived from the SQL
    assert "inputs: author" in " ".join(rows[1].text.split())
    assert "All 3 queries" in response.text


@pytest.mark.asyncio
async def test_homepage_queries_include_your_own(ds):
    response = await ds.client.get("/", cookies=cookies(ds, "alice"))
    links = query_links(response.text)
    assert links[-1] == "/books/alices_query"
    soup = Soup(response.text, "html.parser")
    row = soup.select_one('a[href="/books/alices_query"]').find_parent("li")
    assert row.select_one(".type-tag-mine").text == "yours"


@pytest.mark.asyncio
async def test_homepage_search_finds_queries_by_sql(ds):
    # Other people's saved queries show up when they match a search
    response = await ds.client.get("/", params={"q": "price > 1"})
    assert "/books/alices_query" in query_links(response.text)
    assert "Matching queries" in response.text
    # Matching queries come after the matching tables
    soup = Soup(response.text, "html.parser")
    sections = [
        section["class"][0]
        for section in soup.select("[data-homepage-results] > section")
    ]
    assert sections == ["homepage-tables", "homepage-queries"]


@pytest.mark.asyncio
async def test_homepage_database_filter(ds):
    response = await ds.client.get("/", params={"database": "music"})
    assert item_links(response.text) == ["/music/albums"]
    soup = Soup(response.text, "html.parser")
    selected = soup.select_one("a.homepage-database.selected")
    assert selected["href"] == "/?database=music"
    # An unknown database is ignored
    response = await ds.client.get("/", params={"database": "nope"})
    assert "/books/books" in item_links(response.text)


@pytest.mark.asyncio
async def test_homepage_pagination():
    ds = Datasette()
    await ds.invoke_startup()
    db = ds.add_memory_database("many")
    for i in range(65):
        await db.execute_write(f"create table t{i:02d} (id integer primary key)")
    response = await ds.client.get("/")
    soup = Soup(response.text, "html.parser")
    assert len(soup.select("li.catalog-item")) == 30
    nav = soup.select_one("nav.page-position-footer")
    assert " ".join(nav.select_one(".page-position-text").text.split()) == "Page 1 of 3"
    assert nav.select_one("a.page-position-next")["href"] == "/?page=2#tables"
    assert nav.select_one("a.page-position-first") is None
    assert soup.select_one("section#tables") is not None

    response = await ds.client.get("/", params={"page": "3"})
    soup = Soup(response.text, "html.parser")
    assert len(soup.select("li.catalog-item")) == 5
    nav = soup.select_one("nav.page-position-footer")
    assert nav.select_one("a.page-position-first")["href"] == "/?page=2#tables"
    assert nav.select_one("a.page-position-next") is None


@pytest.mark.asyncio
async def test_homepage_single_database_links_to_it():
    ds = Datasette()
    await ds.invoke_startup()
    ds.remove_database("_memory")
    db = ds.add_memory_database("only")
    await db.execute_write("create table t (id integer primary key)")
    response = await ds.client.get("/")
    soup = Soup(response.text, "html.parser")
    link = soup.select_one(".homepage-summary-database a")
    assert link["href"] == "/only"
    # No database filter or database tags with one database
    assert soup.select_one("nav.homepage-databases") is None
    assert soup.select_one("li.catalog-item .db-tag") is None


@pytest.mark.asyncio
async def test_homepage_results_fragment(ds):
    response = await ds.client.get("/", params={"q": "auth", "_fragment": "results"})
    assert response.status_code == 200
    assert response.headers["x-robots-tag"] == "noindex"
    assert "<html" not in response.text
    soup = Soup(response.text, "html.parser")
    assert soup.select_one("p.homepage-summary") is not None
    results = soup.select_one("div[data-homepage-results]")
    assert results.select_one('a.catalog-item-name[href="/books/authors"]')


@pytest.mark.asyncio
async def test_homepage_json_unchanged(ds):
    response = await ds.client.get("/.json")
    data = response.json()
    assert set(data) == {"ok", "unstable", "databases", "metadata"}
    assert {db["name"] for db in data["databases"]} >= {"books", "music"}


class JumpItemsPlugin:
    __name__ = "JumpItemsPlugin"

    @hookimpl
    def jump_items_sql(self, datasette, actor, request):
        return JumpSQL.menu_item(
            label="Releases dashboard",
            url="/-/apps/releases",
            description="An app for browsing releases",
            item_type="app",
        )


@pytest.mark.asyncio
async def test_homepage_search_includes_plugin_jump_items(ds):
    ds.pm.register(JumpItemsPlugin(), name="JumpItemsPlugin")
    try:
        response = await ds.client.get("/", params={"q": "releases"})
        soup = Soup(response.text, "html.parser")
        other = soup.select_one("section.homepage-other")
        assert other is not None
        link = other.select_one("a.other-result-name")
        assert link["href"] == "/-/apps/releases"
        assert link.text == "Releases dashboard"
        row = link.find_parent("li")
        assert row.select_one(".type-tag-other").text == "app"
        assert "An app for browsing releases" in row.text
        # Tables, views, queries and databases are not repeated there
        response = await ds.client.get("/", params={"q": "books"})
        assert "homepage-other" not in response.text
        # Nothing from plugins when filtered to a database or not searching
        response = await ds.client.get(
            "/", params={"q": "releases", "database": "books"}
        )
        assert "homepage-other" not in response.text
        response = await ds.client.get("/")
        assert "homepage-other" not in response.text
    finally:
        ds.pm.unregister(name="JumpItemsPlugin")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected",
    (
        (
            "/books/books",
            {
                "type": "table",
                "database": "books",
                "name": "books",
                "url": "/books/books",
            },
        ),
        (
            "/books/expensive_books",
            {
                "type": "view",
                "database": "books",
                "name": "expensive_books",
                "url": "/books/expensive_books",
            },
        ),
        (
            "/books/cheap_books",
            {
                "type": "query",
                "database": "books",
                "name": "cheap_books",
                "title": "Cheap books",
                "url": "/books/cheap_books",
            },
        ),
        (
            "/books",
            {"type": "database", "database": "books", "name": "books", "url": "/books"},
        ),
        # Arbitrary SQL is not remembered
        ("/books/-/query?sql=select+1", None),
    ),
)
async def test_recent_item_marker(ds, path, expected):
    response = await ds.client.get(path)
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    marker = soup.select_one("script[data-datasette-recent-item]")
    if expected is None:
        assert marker is None
        return
    data = json.loads(marker.string)
    for key, value in expected.items():
        assert data[key] == value
    assert data["color"] == ds.get_database("books").color


@pytest.mark.asyncio
async def test_recent_context_in_base_template(ds):
    response = await ds.client.get("/", cookies=cookies(ds, "alice"))
    assert (
        'window.datasetteRecentContext = {"actor": "alice", "base": "/"};'
        in response.text
        or 'window.datasetteRecentContext = {"base": "/", "actor": "alice"};'
        in response.text
    )
