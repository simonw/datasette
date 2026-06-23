import pytest
from bs4 import BeautifulSoup as Soup

from datasette.app import Datasette
from datasette.database import Database


@pytest.mark.asyncio
async def test_debug_autocomplete_for_table():
    ds = Datasette(memory=True)
    db = ds.add_database(
        Database(ds, memory_name="test_debug_autocomplete_for_table"), name="data"
    )
    await db.execute_write_script("""
        create table authors (
            id integer primary key,
            name text
        );
        insert into authors (id, name) values
            (1, 'Ada Lovelace'),
            (2, 'Grace Hopper');
    """)

    response = await ds.client.get("/-/debug/autocomplete?database=data&table=authors")

    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    assert soup.select_one("h1").text == "Debug autocomplete"
    assert any(
        "autocomplete.js" in (script.get("src") or "")
        for script in soup.find_all("script")
    )
    autocomplete = soup.select_one("datasette-autocomplete")
    assert autocomplete is not None
    assert autocomplete["src"] == "/data/authors/-/autocomplete"
    assert soup.select_one("input#debug-autocomplete-input") is not None
    assert "Label column:" in response.text
    assert "<code>name</code>" in response.text


@pytest.mark.asyncio
async def test_debug_autocomplete_suggests_label_column_tables():
    ds = Datasette(memory=True)
    db = ds.add_database(
        Database(ds, memory_name="test_debug_autocomplete_suggests"), name="data"
    )
    await db.execute_write_script("""
        create table authors (
            id integer primary key,
            name text
        );
        create table releases (
            id integer primary key,
            title text
        );
    """)

    response = await ds.client.get("/-/debug/autocomplete")

    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    links = {a.text: a["href"] for a in soup.select("table.rows-and-columns a")}
    assert links == {
        "authors": "/-/debug/autocomplete?database=data&table=authors",
        "releases": "/-/debug/autocomplete?database=data&table=releases",
    }
    assert [code.text for code in soup.select("table.rows-and-columns code")] == [
        "name",
        "title",
    ]


@pytest.mark.asyncio
async def test_debug_autocomplete_scan_limit():
    ds = Datasette(memory=True)
    db = ds.add_database(
        Database(ds, memory_name="test_debug_autocomplete_scan_limit"), name="data"
    )
    await db.execute_write_script(
        "\n".join(
            f"create table t{i:03d} (id integer primary key);" for i in range(100)
        )
        + "\ncreate table z_has_label (id integer primary key, name text);"
    )

    response = await ds.client.get("/-/debug/autocomplete")

    assert response.status_code == 200
    assert "No tables with detected label columns found." in response.text
    assert "Scanned 100 tables; stopped at the 100 table scan limit." in response.text
    assert "z_has_label" not in response.text
