import copy
import json
import pickle

import pytest

from datasette import Column, Columns, ForeignKey, Row
from datasette.app import Datasette
from datasette.database import Results
from datasette.rows import label_rows
from datasette.utils import CustomJSONEncoder
from datasette.utils.sqlite import supports_returning


@pytest.fixture
def row():
    return Row((1, "Cleo", None), Columns(["id", "name", "owner_id"]))


def test_row_lookup_by_name_and_position(row):
    assert row["id"] == 1
    assert row["name"] == "Cleo"
    assert row[0] == 1
    assert row[-1] is None
    assert row[0:2] == (1, "Cleo")


def test_row_behaves_as_a_sequence_of_values(row):
    assert len(row) == 3
    assert list(row) == [1, "Cleo", None]
    assert tuple(row) == (1, "Cleo", None)
    id_, name, owner_id = row
    assert (id_, name, owner_id) == (1, "Cleo", None)
    # "in" tests values, consistent with iteration
    assert "Cleo" in row
    assert "name" not in row


def test_row_keys_and_dict(row):
    assert row.keys() == ["id", "name", "owner_id"]
    assert dict(row) == {"id": 1, "name": "Cleo", "owner_id": None}


def test_row_missing_column_errors(row):
    with pytest.raises(KeyError):
        row["nope"]
    with pytest.raises(IndexError):
        row[3]
    # Column names are case-sensitive
    with pytest.raises(KeyError):
        row["ID"]


def test_row_accepts_str_subclass_as_key(row):
    class Name(str):
        pass

    assert row[Name("name")] == "Cleo"


def test_row_duplicate_column_names():
    row = Row((1, 2), Columns(["a", "a"]))
    assert row["a"] == 1
    assert tuple(row) == (1, 2)
    assert row[1] == 2
    assert row.keys() == ["a", "a"]


def test_row_equality_and_hash(row):
    same = Row((1, "Cleo", None), Columns(["id", "name", "owner_id"]))
    other_names = Row((1, "Cleo", None), Columns(["id", "title", "owner_id"]))
    other_values = Row((2, "Cleo", None), Columns(["id", "name", "owner_id"]))
    assert row == same
    assert hash(row) == hash(same)
    assert row != other_names
    assert row != other_values
    assert row != (1, "Cleo", None)
    assert len({row, same, other_names}) == 2


def test_row_is_immutable(row):
    with pytest.raises(TypeError):
        row["id"] = 2
    with pytest.raises(AttributeError):
        row.columns = Columns(["a", "b", "c"])
    with pytest.raises(AttributeError):
        row.labels = {}
    with pytest.raises(TypeError):
        row.labels["name"] = "x"
    with pytest.raises(AttributeError):
        row.extra = 1


def test_row_repr(row):
    assert repr(row) == "<Row: id=1, name='Cleo', owner_id=None>"


def test_row_labels():
    columns = Columns(["id", "owner_id"])
    row = Row((1, 5), columns)
    assert row.labels == {}
    assert "owner_id" not in row.labels
    labeled = Row((1, 5), columns, {"owner_id": "Natalie"})
    assert labeled.labels == {"owner_id": "Natalie"}
    assert labeled.labels.get("id") is None
    # The value itself is always the stored value
    assert labeled["owner_id"] == 5
    assert labeled.columns is columns
    # Labels do not affect equality
    assert labeled == row
    # The dictionary that was passed in is copied
    source = {"owner_id": "Natalie"}
    labeled = Row((1, 5), columns, source)
    source["owner_id"] = "changed"
    assert labeled.labels == {"owner_id": "Natalie"}


def test_row_from_dict():
    row = Row.from_dict({"id": 1, "owner_id": 5}, labels={"owner_id": "Natalie"})
    assert row.keys() == ["id", "owner_id"]
    assert row["owner_id"] == 5
    assert row.labels == {"owner_id": "Natalie"}
    assert row.columns.names == ("id", "owner_id")


@pytest.mark.parametrize("duplicate", (pickle.dumps, copy.deepcopy))
def test_row_pickle_and_deepcopy(duplicate):
    row = Row((1, 5), Columns(["id", "owner_id"]), {"owner_id": "Natalie"})
    copied = duplicate(row)
    if isinstance(copied, bytes):
        copied = pickle.loads(copied)
    assert copied == row
    assert copied.labels == {"owner_id": "Natalie"}
    assert copied.columns == row.columns


@pytest.mark.parametrize("duplicate", (pickle.dumps, copy.deepcopy))
@pytest.mark.parametrize(
    "columns",
    (
        Columns(["id", "owner_id"]),
        Columns(["id", Column("owner_id", foreign_key=ForeignKey("owners", "id"))]),
    ),
)
def test_columns_pickle_and_deepcopy(duplicate, columns):
    copied = duplicate(columns)
    if isinstance(copied, bytes):
        copied = pickle.loads(copied)
    assert copied == columns
    assert copied.names == ("id", "owner_id")
    assert copied["owner_id"] == columns["owner_id"]


def test_row_json_encoding(row):
    assert json.loads(json.dumps({"row": row}, cls=CustomJSONEncoder)) == {
        "row": [1, "Cleo", None]
    }


def test_columns():
    fk = ForeignKey(table="owners", column="id")
    columns = Columns(["id", Column("owner_id", table="dogs", foreign_key=fk)])
    assert len(columns) == 2
    assert columns.names == ("id", "owner_id")
    assert [column.name for column in columns] == ["id", "owner_id"]
    assert columns[0] == Column("id")
    assert columns["owner_id"].foreign_key == fk
    assert columns["owner_id"].table == "dogs"
    assert columns["id"].type is None
    assert columns[-1] is columns["owner_id"]
    assert "id" in columns
    assert "nope" not in columns
    assert Column("id") in columns
    with pytest.raises(KeyError):
        columns["nope"]
    with pytest.raises(IndexError):
        columns[2]
    assert columns == Columns(["id", Column("owner_id", table="dogs", foreign_key=fk)])
    assert columns != Columns(["id", "owner_id"])
    assert repr(columns) == "<Columns: id, owner_id>"


def test_column_is_immutable():
    column = Column("id")
    with pytest.raises(AttributeError):
        column.name = "other"


def test_results_wrap_tuples_in_rows():
    results = Results([(1, "a"), (2, "b")], False, (("id",), ("name",)))
    assert isinstance(results.columns, Columns)
    assert results.columns.names == ("id", "name")
    assert all(isinstance(row, Row) for row in results.rows)
    # Every row shares the one columns object
    assert all(row.columns is results.columns for row in results.rows)
    assert results.first()["name"] == "a"
    assert results.dicts() == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
    assert [tuple(row) for row in results] == [(1, "a"), (2, "b")]
    assert len(results) == 2


def test_label_rows():
    results = Results(
        [(1, 5, 7), (2, None, 8), (3, 99, 7)],
        True,
        (("id",), ("owner_id",), ("vet_id",)),
    )
    labeled = label_rows(
        results.rows,
        {"owner_id": {5: "Natalie"}, "vet_id": {7: "Dr Paws"}, "nope": {1: "x"}},
    )
    assert [dict(row.labels) for row in labeled] == [
        {"owner_id": "Natalie", "vet_id": "Dr Paws"},
        {},
        {"vet_id": "Dr Paws"},
    ]
    assert all(row.columns is results.columns for row in labeled)
    # Values are untouched, and so are the original rows
    assert [tuple(row) for row in labeled] == [(1, 5, 7), (2, None, 8), (3, 99, 7)]
    assert all(row.labels == {} for row in results.rows)


def test_results_with_columns():
    results = Results([(1, "a")], False, (("id",), ("name",)))
    columns = Columns([Column("id", type="INTEGER"), "name"])
    typed = results.with_columns(columns)
    assert typed.columns is columns
    assert typed.rows[0].columns is columns
    assert typed.rows[0] == results.rows[0]
    assert typed.rows[0].columns["id"].type == "INTEGER"
    # The original results are unchanged
    assert results.columns["id"].type is None
    with pytest.raises(ValueError):
        results.with_columns(Columns(["just_one"]))


@pytest.mark.asyncio
async def test_db_execute_returns_rows():
    ds = Datasette(memory=True)
    db = ds.add_memory_database("test_db_execute_returns_rows")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await db.execute_write("insert into dogs (name) values ('Cleo'), ('Pancakes')")
    results = await db.execute("select id, name from dogs order by id")
    assert isinstance(results.columns, Columns)
    assert results.columns.names == ("id", "name")
    assert all(type(row) is Row for row in results.rows)
    assert all(row.columns is results.columns for row in results.rows)
    assert results.rows[0]["name"] == "Cleo"
    assert results.rows[1][0] == 2
    # Arbitrary SQL against SQLite has no type information
    assert results.columns["id"].type is None
    # Empty results still describe their columns
    empty = await db.execute("select id, name from dogs where id = 99")
    assert empty.rows == []
    assert empty.columns.names == ("id", "name")
    assert empty.first() is None


@pytest.mark.asyncio
@pytest.mark.skipif(
    not supports_returning(), reason="SQLite does not support RETURNING"
)
async def test_db_execute_write_returning_rows():
    ds = Datasette(memory=True)
    db = ds.add_memory_database("test_db_execute_write_returning_rows")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    result = await db.execute_write(
        "insert into dogs (name) values ('Cleo') returning id, name"
    )
    rows = result.fetchall()
    assert type(rows[0]) is Row
    assert rows[0]["name"] == "Cleo"
    assert rows[0].keys() == ["id", "name"]


@pytest.fixture
async def ds_with_dogs():
    ds = Datasette(memory=True)
    db = ds.add_memory_database("row_types")
    await db.execute_write_script("""
        create table owners (id integer primary key, name text);
        insert into owners values (1, 'Natalie');
        create table breeds (id integer primary key, weight real, height real);
        insert into breeds values (3, 30.5, 55.0);
        create table dogs (
            -- Types that are asserted on are in upper case, because older
            -- versions of SQLite report a declared type of "integer" as
            -- written and newer versions report "INTEGER"
            id INTEGER primary key,
            name varchar(30),
            born DATE,
            owner_id integer references owners(id),
            breed integer references breeds,
            photo BLOB,
            anything
        );
        insert into dogs values (1, 'Cleo', '2020-01-01', 1, 3, null, 3);
        insert into dogs values (2, 'Pancakes', null, 99, 3, null, null);
        create table no_pk (name text, owner_id integer references owners(id));
        insert into no_pk values ('x', 1);
        create view dogs_view as select id, name, owner_id from dogs;
        """)
    await ds.invoke_startup()
    return ds


@pytest.mark.asyncio
async def test_describe_columns(ds_with_dogs):
    db = ds_with_dogs.get_database("row_types")
    columns = await db.describe_columns("dogs")
    assert columns.names == (
        "id",
        "name",
        "born",
        "owner_id",
        "breed",
        "photo",
        "anything",
    )
    assert all(column.table == "dogs" for column in columns)
    # The type is the declared type, exactly as SQLite reports it
    assert columns["id"].type == "INTEGER"
    assert columns["name"].type == "varchar(30)"
    assert columns["born"].type == "DATE"
    assert columns["photo"].type == "BLOB"
    # A column declared without a type has none
    assert columns["anything"].type is None
    assert columns["owner_id"].foreign_key == ForeignKey("owners", "id")
    # This one was declared without naming the column it refers to
    assert columns["breed"].foreign_key == ForeignKey("breeds", None)
    assert columns["name"].foreign_key is None


@pytest.mark.asyncio
async def test_describe_columns_for_names(ds_with_dogs):
    db = ds_with_dogs.get_database("row_types")
    columns = await db.describe_columns("no_pk", ["rowid", "owner_id", "nope"])
    assert columns.names == ("rowid", "owner_id", "nope")
    assert columns["rowid"].type == "INTEGER"
    assert columns["rowid"].table == "no_pk"
    assert columns["owner_id"].foreign_key == ForeignKey("owners", "id")
    assert columns["nope"] == Column("nope")


@pytest.fixture
def render_cell_calls():
    from datasette import hookimpl
    from datasette.plugins import pm

    calls = []

    class Plugin:
        __name__ = "RecordRenderCellRows"

        @hookimpl
        def render_cell(self, row, value, column, table, database):
            if database == "row_types":
                calls.append((table, column, value, row))

    pm.register(Plugin(), name="undo_record_render_cell_rows")
    try:
        yield calls
    finally:
        pm.unregister(name="undo_record_render_cell_rows")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_table",
    (
        ("/row_types/dogs", "dogs"),
        ("/row_types/dogs?_labels=off", "dogs"),
        ("/row_types/dogs/1", "dogs"),
        ("/row_types/dogs/1?_labels=off", "dogs"),
        ("/row_types/dogs_view", "dogs_view"),
        ("/row_types/-/query?sql=select+*+from+dogs", None),
        ("/row_types/dogs.json?_extra=render_cell", "dogs"),
        ("/row_types/dogs.json?_extra=render_cell&_labels=on", "dogs"),
        ("/row_types/dogs/1.json?_extra=render_cell", "dogs"),
        ("/row_types/-/query.json?sql=select+*+from+dogs&_extra=render_cell", None),
    ),
)
async def test_render_cell_always_gets_row_and_stored_value(
    ds_with_dogs, render_cell_calls, path, expected_table
):
    response = await ds_with_dogs.client.get(path)
    assert response.status_code == 200
    assert render_cell_calls
    for table, column, value, row in render_cell_calls:
        assert table == expected_table
        assert type(row) is Row
        # The value is what is stored in the database, never a label
        assert value is None or isinstance(value, (str, int, float, bytes))
        assert row[column] == value
    owner_ids = {
        row["id"]: value
        for _, column, value, row in render_cell_calls
        if column == "owner_id"
    }
    assert owner_ids[1] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expect_labels",
    (
        ("/row_types/dogs", True),
        ("/row_types/dogs?_labels=off", False),
        ("/row_types/dogs/1", True),
        ("/row_types/dogs_view", False),
        ("/row_types/-/query?sql=select+*+from+dogs", False),
        ("/row_types/dogs.json?_extra=render_cell", False),
        ("/row_types/dogs.json?_extra=render_cell&_labels=on", True),
    ),
)
async def test_render_cell_row_labels(
    ds_with_dogs, render_cell_calls, path, expect_labels
):
    await ds_with_dogs.client.get(path)
    rows = {row["id"]: row for _, _, _, row in render_cell_calls}
    if expect_labels:
        # breeds has no label column, so breed never gets a label
        assert rows[1].labels == {"owner_id": "Natalie"}
    else:
        assert rows[1].labels == {}
    if 2 in rows:
        # owner_id 99 does not match a row in owners
        assert rows[2].labels == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,typed",
    (
        ("/row_types/dogs", True),
        ("/row_types/dogs/1", True),
        ("/row_types/dogs.json?_extra=render_cell", True),
        ("/row_types/-/query?sql=select+*+from+dogs", False),
    ),
)
async def test_render_cell_row_columns_are_described(
    ds_with_dogs, render_cell_calls, path, typed
):
    await ds_with_dogs.client.get(path)
    row = render_cell_calls[0][3]
    # Rows keep the order of the query on every page
    assert row.columns.names == (
        "id",
        "name",
        "born",
        "owner_id",
        "breed",
        "photo",
        "anything",
    )
    if typed:
        assert row.columns["born"].type == "DATE"
        assert row.columns["owner_id"].foreign_key == ForeignKey("owners", "id")
        assert row.columns["id"].table == "dogs"
    else:
        # SQLite cannot describe the columns of an arbitrary query
        assert all(column == Column(column.name) for column in row.columns)


@pytest.mark.asyncio
async def test_row_page_displays_primary_keys_first_but_keeps_row_order(
    render_cell_calls,
):
    ds = Datasette(memory=True)
    db = ds.add_memory_database("row_types")
    await db.execute_write_script("""
        create table pk_last (t text, n integer, id integer primary key);
        insert into pk_last values ('x', 5, 1);
        """)
    for path in ("/row_types/pk_last", "/row_types/pk_last/1"):
        render_cell_calls.clear()
        response = await ds.client.get(path)
        assert response.status_code == 200
        row = render_cell_calls[0][3]
        assert row.keys() == ["t", "n", "id"]
        assert row[0] == "x"
    # The row page calls the hook for the primary key first
    assert [call[1] for call in render_cell_calls] == ["id", "t", "n"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_cells",
    (
        (
            "/row_types/dogs",
            [
                '<a href="/row_types/owners/1">Natalie</a>\xa0<em>1</em>',
                # No label column on breeds, so the link shows the value
                '<a href="/row_types/breeds/3">3</a>',
                # 99 is not a row in owners
                "99",
                '<a href="/row_types/breeds/3">3</a>',
            ],
        ),
        ("/row_types/dogs?_labels=off", ["1", "3", "99", "3"]),
        (
            "/row_types/dogs/1",
            [
                '<a href="/row_types/owners/1">Natalie</a>\xa0<em>1</em>',
                '<a href="/row_types/breeds/3">3</a>',
            ],
        ),
    ),
)
async def test_foreign_key_links_in_html(ds_with_dogs, path, expected_cells):
    from bs4 import BeautifulSoup as Soup

    response = await ds_with_dogs.client.get(path)
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    # The table page uses <td> cells, the row page a list of fields
    cells = [
        el.decode_contents().strip()
        for el in soup.select(
            "td.col-owner_id, td.col-breed, "
            ".col-owner_id .row-field-value, .col-breed .row-field-value"
        )
    ]
    assert cells == expected_cells


@pytest.mark.asyncio
async def test_labels_in_json_and_csv(ds_with_dogs):
    response = await ds_with_dogs.client.get(
        "/row_types/dogs.json?_labels=on&_extra=expanded_columns&_col=owner_id&_col=breed"
    )
    data = response.json()
    # breeds has no label column, so breed is not expanded
    assert data["expanded_columns"] == ["owner_id"]
    assert data["rows"] == [
        {"id": 1, "owner_id": {"value": 1, "label": "Natalie"}, "breed": 3},
        {"id": 2, "owner_id": 99, "breed": 3},
    ]
    response = await ds_with_dogs.client.get(
        "/row_types/dogs.csv?_labels=on&_col=owner_id&_col=breed"
    )
    assert response.text.splitlines() == [
        "id,owner_id,owner_id_label,breed",
        "1,1,Natalie,3",
        "2,99,,3",
    ]
