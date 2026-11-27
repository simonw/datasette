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


@pytest.mark.parametrize(
    "actor_id,expected_links,expected_hrefs",
    (
        # secret also links to authors, but anonymous users cannot see it
        (None, "links to authors", ["/books/authors"]),
        ("root", "links to books, secret", ["/books/books", "/books/secret"]),
    ),
)
async def test_homepage_linked_tables(ds, actor_id, expected_links, expected_hrefs):
    response = await ds.client.get(
        "/", cookies=cookies(ds, actor_id) if actor_id else None
    )
    soup = Soup(response.text, "html.parser")

    def links(href):
        item = soup.select_one(f'a.catalog-item-name[href="{href}"]').find_parent("li")
        return item.select_one(".catalog-item-links")

    if actor_id is None:
        linked = links("/books/books")
        assert "secret" not in links("/books/authors").text
    else:
        linked = links("/books/authors")
    assert linked.text == expected_links
    assert [a["href"] for a in linked.select("a")] == expected_hrefs
    # The full list is only needed as a tooltip when it has been cut short
    assert not linked.has_attr("title")
    # Tables with no foreign keys show nothing
    assert links("/music/albums") is None


async def test_homepage_linked_tables_truncated():
    ds = Datasette()
    await ds.invoke_startup()
    db = ds.add_memory_database("linked")
    await db.execute_write("create table hub (id integer primary key)")
    for name in ("a", "b", "c", "d", "e"):
        await db.execute_write(
            f"create table {name} (id integer primary key, "
            "hub_id integer references hub(id))"
        )
    await ds.refresh_schemas()
    soup = Soup((await ds.client.get("/")).text, "html.parser")
    hub = soup.select_one('a.catalog-item-name[href="/linked/hub"]').find_parent("li")
    linked = hub.select_one(".catalog-item-links")
    assert linked.text == "links to a, b, c and 2 more"
    assert linked["title"] == "Linked by foreign keys to: a, b, c, d, e"
    assert [a["href"] for a in linked.select("a")] == [
        "/linked/a",
        "/linked/b",
        "/linked/c",
    ]


@pytest.mark.parametrize("path", ("/", "/wide"))
async def test_more_columns_button_reveals_the_rest(path):
    ds = Datasette()
    await ds.invoke_startup()
    db = ds.add_memory_database("wide")
    names = [f"c{i:02d}" for i in range(11)]
    await db.execute_write(
        "create table t (id integer primary key, {})".format(", ".join(names))
    )
    await db.execute_write("create table narrow (id integer primary key, name text)")
    await ds.refresh_schemas()
    soup = Soup((await ds.client.get(path)).text, "html.parser")
    assert soup.select_one('script[src*="catalog-cards.js"]') is not None
    card = soup.select_one('a.catalog-item-name[href="/wide/t"]').find_parent("li")
    columns = card.select_one(".catalog-item-columns")
    # The first eight columns show, the other four wait behind the button
    rest = columns.select_one(".catalog-columns-rest")
    assert rest.has_attr("hidden")
    assert [c.text for c in rest.select(".catalog-column")] == names[-4:]
    assert [c.text for c in columns.select(".catalog-column")] == ["id"] + names
    button = columns.select_one("button[data-catalog-columns-more]")
    assert button.text == "+4 more"
    assert button["aria-expanded"] == "false"
    # A table with few columns has no button
    narrow = soup.select_one('a.catalog-item-name[href="/wide/narrow"]').find_parent(
        "li"
    )
    assert narrow.select_one("button") is None
    assert narrow.select_one(".catalog-columns-rest") is None


async def test_homepage_private_table_shown_with_permission(ds):
    response = await ds.client.get("/", cookies=cookies(ds, "root"))
    soup = Soup(response.text, "html.parser")
    secret = soup.select_one('a.catalog-item-name[href="/books/secret"]')
    assert secret is not None
    assert "🔒" in secret.find_parent("li").text


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


async def test_homepage_search_never_shows_private_tables(ds):
    response = await ds.client.get("/", params={"q": "password"})
    assert "/books/secret" not in item_links(response.text)
    response = await ds.client.get(
        "/", params={"q": "password"}, cookies=cookies(ds, "root")
    )
    assert item_links(response.text) == ["/books/secret"]


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
    assert ">3 queries →</a>" in response.text


async def test_homepage_queries_include_your_own(ds):
    response = await ds.client.get("/", cookies=cookies(ds, "alice"))
    links = query_links(response.text)
    assert links[-1] == "/books/alices_query"
    soup = Soup(response.text, "html.parser")
    row = soup.select_one('a[href="/books/alices_query"]').find_parent("li")
    assert row.select_one(".type-tag-mine").text == "yours"


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


async def test_homepage_database_filter(ds):
    response = await ds.client.get("/", params={"database": "music"})
    assert item_links(response.text) == ["/music/albums"]
    soup = Soup(response.text, "html.parser")
    selected = soup.select_one("a.homepage-database.selected")
    assert selected["href"] == "/?database=music"
    # An unknown database is ignored
    response = await ds.client.get("/", params={"database": "nope"})
    assert "/books/books" in item_links(response.text)


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


async def test_homepage_results_fragment(ds):
    response = await ds.client.get("/", params={"q": "auth", "_fragment": "results"})
    assert response.status_code == 200
    assert response.headers["x-robots-tag"] == "noindex"
    assert "<html" not in response.text
    soup = Soup(response.text, "html.parser")
    assert soup.select_one("p.homepage-summary") is not None
    results = soup.select_one("div[data-homepage-results]")
    assert results.select_one('a.catalog-item-name[href="/books/authors"]')


async def test_homepage_json(ds):
    response = await ds.client.get("/.json")
    assert response.status_code == 200
    data = response.json()
    assert list(data) == [
        "ok",
        "unstable",
        "databases",
        "items",
        "queries",
        "total",
        "queries_total",
        "totals",
        "q",
        "database_filter",
        "previous_url",
        "next_url",
        "metadata",
        "private",
    ]
    assert [db["name"] for db in data["databases"]] == ["_memory", "books", "music"]
    assert data["databases"][1] == {
        "name": "books",
        "private": False,
        "tables": 2,
        "views": 1,
        "url": "/books",
        "size": None,
        "metadata": {},
    }
    # The same tables and views as the HTML version, in the same order
    html = await ds.client.get("/")
    assert [item["url"] for item in data["items"]] == item_links(html.text)
    assert data["total"] == len(data["items"]) == 4
    assert data["totals"] == {"databases": 3, "tables": 3, "views": 1}
    assert [q["url"] for q in data["queries"]] == query_links(html.text)
    # Counts every query the actor can see, including saved queries
    # belonging to other people which are only listed when searching
    assert data["queries_total"] == 3
    assert data["q"] == ""
    assert data["database_filter"] is None
    assert data["previous_url"] is None
    assert data["next_url"] is None
    assert data["private"] is False
    books = next(i for i in data["items"] if i["url"] == "/books/books")
    assert books == {
        "database": "books",
        "name": "books",
        "type": "table",
        "private": False,
        "title": None,
        "description": "Every book in the library",
        "columns": ["id", "title", "price", "author_id"],
        "matched_columns": [],
        "column_count": 4,
        "linked_tables": ["authors"],
        "has_fts": True,
        "count": 3,
        "count_truncated": False,
        "url": "/books/books",
    }
    # Presentation details of the HTML page are left out
    for collection in ("databases", "items", "queries"):
        assert not any("color" in d for d in data[collection])


async def test_homepage_json_never_shows_private_tables(ds):
    urls = lambda r: [item["url"] for item in r.json()["items"]]
    assert "/books/secret" not in urls(await ds.client.get("/.json"))
    assert "/books/secret" in urls(
        await ds.client.get("/.json", cookies=cookies(ds, "root"))
    )


async def test_homepage_json_search_and_database_filter(ds):
    data = (await ds.client.get("/.json?q=price")).json()
    assert data["q"] == "price"
    assert [(i["url"], i["matched_columns"]) for i in data["items"]] == [
        ("/books/books", ["price"]),
    ]
    assert data["total"] == 1
    # Totals follow the search but not the database filter
    assert data["totals"] == {"databases": 3, "tables": 1, "views": 0}

    data = (await ds.client.get("/.json?database=music")).json()
    assert data["database_filter"] == "music"
    assert [i["url"] for i in data["items"]] == ["/music/albums"]
    assert data["total"] == 1
    assert data["totals"] == {"databases": 3, "tables": 3, "views": 1}
    # A database that does not exist is ignored
    data = (await ds.client.get("/.json?database=nope")).json()
    assert data["database_filter"] is None
    assert data["total"] == 4


async def test_homepage_json_lists_every_column():
    ds = Datasette()
    await ds.invoke_startup()
    db = ds.add_memory_database("wide")
    await db.execute_write(
        "create table wide ({})".format(", ".join(f"c{i}" for i in range(12)))
    )
    item = (await ds.client.get("/.json")).json()["items"][0]
    assert item["columns"] == [f"c{i}" for i in range(12)]
    assert item["column_count"] == 12
    assert "more_columns" not in item


async def test_homepage_json_pagination():
    ds = Datasette()
    await ds.invoke_startup()
    db = ds.add_memory_database("many")
    await db.execute_write_script(
        "".join(f"create table t{i:03d} (id integer primary key);" for i in range(65))
    )
    seen = []
    url = "/.json?database=many"
    previous_urls = []
    while url:
        data = (await ds.client.get(url)).json()
        assert data["total"] == 65
        seen.extend(item["name"] for item in data["items"])
        previous_urls.append(data["previous_url"])
        url = (data["next_url"] or "").removeprefix("http://localhost")
    assert sorted(seen) == [f"t{i:03d}" for i in range(65)]
    assert previous_urls == [
        None,
        "http://localhost/.json?database=many",
        "http://localhost/.json?database=many&page=2",
    ]


async def test_homepage_json_alternate_keeps_query_string(ds):
    response = await ds.client.get("/?q=price&database=books")
    alternate = response.headers["link"].split(">")[0].lstrip("<")
    assert alternate == "http://localhost/.json?q=price&database=books"
    data = (await ds.client.get(alternate.removeprefix("http://localhost"))).json()
    assert [i["url"] for i in data["items"]] == item_links(response.text)


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


async def test_recent_context_in_base_template(ds):
    response = await ds.client.get("/", cookies=cookies(ds, "alice"))
    assert (
        'window.datasetteRecentContext = {"actor": "alice", "base": "/"};'
        in response.text
        or 'window.datasetteRecentContext = {"base": "/", "actor": "alice"};'
        in response.text
    )


async def test_homepage_database_metadata_in_template_and_json(tmp_path):
    (tmp_path / "index.html").write_text(
        "{% for db in databases %}"
        "<div data-db='{{ db.name }}'>"
        "<span class='title'>{{ db.metadata.title }}</span>"
        "<span class='desc'>{{ db.metadata.description }}</span>"
        "<span class='source'>{{ db.metadata.source }}</span>"
        "<span class='source-url'>{{ db.metadata.source_url }}</span>"
        "</div>"
        "{% endfor %}"
    )
    ds = Datasette(
        template_dir=str(tmp_path),
        metadata={
            "databases": {
                "books": {
                    "title": "Library Books",
                    "description": "Catalog of all library holdings",
                    "source": "City Library",
                    "source_url": "https://example.com/library",
                }
            }
        },
    )
    await ds.invoke_startup()
    ds.add_memory_database("books")
    ds.add_memory_database("music")

    html_response = await ds.client.get("/")
    assert html_response.status_code == 200
    soup = Soup(html_response.text, "html.parser")
    books_div = soup.select_one('div[data-db="books"]')
    assert books_div.select_one(".title").text == "Library Books"
    assert books_div.select_one(".desc").text == "Catalog of all library holdings"
    assert books_div.select_one(".source").text == "City Library"
    assert books_div.select_one(".source-url").text == "https://example.com/library"
    music_div = soup.select_one('div[data-db="music"]')
    assert music_div.select_one(".title").text == ""

    json_response = await ds.client.get("/.json")
    assert json_response.status_code == 200
    by_name = {d["name"]: d for d in json_response.json()["databases"]}
    assert by_name["books"]["metadata"] == {
        "title": "Library Books",
        "description": "Catalog of all library holdings",
        "source": "City Library",
        "source_url": "https://example.com/library",
    }
    assert by_name["music"]["metadata"] == {}


async def test_homepage_database_metadata_permissions_and_runtime_update():
    ds = Datasette(
        config={"databases": {"secret_db": {"allow": {"id": "root"}}}},
        metadata={
            "databases": {
                "secret_db": {
                    "title": "Classified Records",
                    "source": "Internal Archive",
                }
            }
        },
    )
    await ds.invoke_startup()
    ds.add_memory_database("secret_db")

    anon_resp = await ds.client.get("/.json")
    assert anon_resp.status_code == 200
    assert "Classified Records" not in anon_resp.text
    assert all(d["name"] != "secret_db" for d in anon_resp.json()["databases"])

    root_resp = await ds.client.get("/.json", cookies=cookies(ds, "root"))
    assert root_resp.status_code == 200
    by_name = {d["name"]: d for d in root_resp.json()["databases"]}
    assert by_name["secret_db"]["metadata"] == {
        "title": "Classified Records",
        "source": "Internal Archive",
    }

    await ds.set_database_metadata(
        "secret_db", "source_url", "https://example.com/internal"
    )
    updated_resp = await ds.client.get("/.json", cookies=cookies(ds, "root"))
    updated_by_name = {d["name"]: d for d in updated_resp.json()["databases"]}
    assert (
        updated_by_name["secret_db"]["metadata"]["source_url"]
        == "https://example.com/internal"
    )
