import pytest

from datasette.app import Datasette


@pytest.mark.asyncio
async def test_autocomplete_single_pk_exact_match_and_label_order():
    ds = Datasette(memory=True)
    db = ds.add_memory_database("autocomplete_single")
    await db.execute_write_script("""
        create table people (
            id integer primary key,
            name text
        );
        insert into people (id, name) values
            (2, 'Longer non-label pk match'),
            (20, '2'),
            (21, '22'),
            (200, 'A'),
            (3, 'A label containing 2');
        """)

    response = await ds.client.get("/autocomplete_single/people/-/autocomplete?q=2")

    assert response.status_code == 200
    assert response.json() == {
        "rows": [
            {"pks": {"id": 2}, "label": "Longer non-label pk match"},
            {"pks": {"id": 20}, "label": "2"},
            {"pks": {"id": 21}, "label": "22"},
            {"pks": {"id": 3}, "label": "A label containing 2"},
            {"pks": {"id": 200}, "label": "A"},
        ]
    }


@pytest.mark.asyncio
async def test_autocomplete_blank_q_returns_no_results():
    ds = Datasette(memory=True)
    db = ds.add_memory_database("autocomplete_blank")
    await db.execute_write_script("""
        create table people (
            id integer primary key,
            name text
        );
        insert into people (id, name) values
            (1, 'Alice'),
            (2, 'Bob');
        """)

    response = await ds.client.get("/autocomplete_blank/people/-/autocomplete?q=")

    assert response.status_code == 200
    assert response.json() == {"rows": []}

    response = await ds.client.get("/autocomplete_blank/people/-/autocomplete")

    assert response.status_code == 200
    assert response.json() == {"rows": []}


@pytest.mark.asyncio
async def test_autocomplete_initial_returns_latest_rows():
    ds = Datasette(memory=True)
    db = ds.add_memory_database("autocomplete_initial")
    await db.execute_write_script("""
        create table people (
            id integer primary key,
            name text
        );
        insert into people (id, name) values
            (1, 'Alice'),
            (2, 'Bob'),
            (3, 'Cleo');
        """)

    response = await ds.client.get(
        "/autocomplete_initial/people/-/autocomplete?_initial=1"
    )

    assert response.status_code == 200
    assert response.json() == {
        "rows": [
            {"pks": {"id": 3}, "label": "Cleo"},
            {"pks": {"id": 2}, "label": "Bob"},
            {"pks": {"id": 1}, "label": "Alice"},
        ]
    }

    response = await ds.client.get(
        "/autocomplete_initial/people/-/autocomplete?q=&_initial=1"
    )

    assert response.status_code == 200
    assert response.json() == {
        "rows": [
            {"pks": {"id": 3}, "label": "Cleo"},
            {"pks": {"id": 2}, "label": "Bob"},
            {"pks": {"id": 1}, "label": "Alice"},
        ]
    }


@pytest.mark.asyncio
async def test_autocomplete_escapes_like_characters():
    ds = Datasette(memory=True)
    db = ds.add_memory_database("autocomplete_escape")
    await db.execute_write_script("""
        create table tags (
            id integer primary key,
            name text
        );
        insert into tags (id, name) values
            (1, '100% real'),
            (2, '100X real'),
            (3, '100 percent real');
        """)

    response = await ds.client.get("/autocomplete_escape/tags/-/autocomplete?q=100%25")

    assert response.status_code == 200
    assert response.json() == {
        "rows": [
            {"pks": {"id": 1}, "label": "100% real"},
        ]
    }


@pytest.mark.asyncio
async def test_autocomplete_compound_pk_searches_all_pk_columns():
    ds = Datasette(memory=True)
    db = ds.add_memory_database("autocomplete_compound")
    await db.execute_write_script("""
        create table places (
            country text,
            code text,
            name text,
            primary key (country, code)
        );
        insert into places (country, code, name) values
            ('us', 'ca', 'California'),
            ('ca', 'bc', 'British Columbia'),
            ('mx', 'ca', 'Campeche'),
            ('zz', 'zz', 'Nothing');
        """)

    response = await ds.client.get("/autocomplete_compound/places/-/autocomplete?q=ca")

    assert response.status_code == 200
    assert response.json() == {
        "rows": [
            {"pks": {"country": "mx", "code": "ca"}, "label": "Campeche"},
            {"pks": {"country": "us", "code": "ca"}, "label": "California"},
            {"pks": {"country": "ca", "code": "bc"}, "label": "British Columbia"},
        ]
    }


@pytest.mark.asyncio
async def test_autocomplete_primary_key_called_label():
    ds = Datasette(
        memory=True,
        config={
            "databases": {
                "autocomplete_label_pk": {
                    "tables": {"things": {"label_column": "name"}}
                }
            }
        },
    )
    db = ds.add_memory_database("autocomplete_label_pk")
    await db.execute_write_script("""
        create table things (
            label text primary key,
            name text
        );
        insert into things (label, name) values
            ('abc', 'Display value'),
            ('def', 'Other value');
        """)

    response = await ds.client.get("/autocomplete_label_pk/things/-/autocomplete?q=abc")

    assert response.status_code == 200
    assert response.json() == {
        "rows": [
            {"pks": {"label": "abc"}, "label": "Display value"},
        ]
    }


@pytest.mark.asyncio
async def test_autocomplete_timeout_uses_prefix_fallback():
    ds = Datasette(
        memory=True,
        config={
            "databases": {
                "autocomplete_timeout": {"tables": {"things": {"label_column": "name"}}}
            }
        },
        settings={
            "num_sql_threads": 1,
            "sql_time_limit_ms": 1,
        },
    )
    db = ds.add_memory_database("autocomplete_timeout")
    await db.execute_write_script("""
        create table things (
            id text primary key,
            name text
        );
        insert into things (id, name) values
            ('other-000001', 'item-1999 label-only match');
        """)

    def insert_rows(conn):
        conn.executemany(
            "insert into things (id, name) values (?, ?)",
            ((f"item-{i:06d}", f"name {i:06d}") for i in range(200_000)),
        )

    await db.execute_write_fn(insert_rows)

    response = await ds.client.get(
        "/autocomplete_timeout/things/-/autocomplete?q=item-1999"
    )

    assert response.status_code == 200
    data = response.json()
    assert data == {
        "rows": [
            {"pks": {"id": f"item-1999{i:02d}"}, "label": f"name 1999{i:02d}"}
            for i in range(10)
        ]
    }
