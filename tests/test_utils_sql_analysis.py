import pytest

from datasette.utils.sql_analysis import analyze_sql_tables
from datasette.utils.sqlite import sqlite3


@pytest.fixture
def conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        create table dogs (id integer primary key, name text, age integer);
        create table cats (id integer primary key, name text);
        create table log (message text);
        create view dog_names as select id, name from dogs;
        create trigger dogs_after_insert after insert on dogs begin
            update cats set name = new.name where id = new.id;
            insert into log (message) values (new.name);
        end;
        create trigger dog_names_instead_of_update instead of update on dog_names begin
            update dogs set name = new.name where id = old.id;
        end;
        """)
    try:
        yield conn
    finally:
        conn.close()


def table_operation_tuples(analysis):
    return [
        (
            operation.operation,
            operation.database,
            operation.sqlite_schema,
            operation.table,
            operation.columns,
            operation.source,
        )
        for operation in analysis.operations
        if operation.target_type == "table"
        and operation.operation in {"read", "insert", "update", "delete"}
        and not operation.internal
    ]


def test_analyze_select_tables(conn):
    analysis = analyze_sql_tables(
        conn,
        "select dogs.name, cats.name from dogs join cats on dogs.id = cats.id where dogs.age > ?",
        (2,),
        database_name="data",
    )

    assert set(table_operation_tuples(analysis)) == {
        ("read", "data", "main", "cats", ("id", "name"), None),
        ("read", "data", "main", "dogs", ("age", "id", "name"), None),
    }


def test_analyze_uses_sqlite_schema_as_default_database(conn):
    analysis = analyze_sql_tables(conn, "select name from dogs")

    assert set(table_operation_tuples(analysis)) == {
        ("read", "main", "main", "dogs", ("name",), None),
    }


def test_analyze_user_schema_table_read_is_not_internal(conn):
    analysis = analyze_sql_tables(
        conn,
        "insert into log select sql from sqlite_master where name = 'dogs'",
        database_name="data",
    )

    assert {
        "operation": "read",
        "target_type": "schema",
        "database": "data",
        "sqlite_schema": "main",
        "table": None,
        "target": "sqlite_master",
        "columns": ("name", "sql"),
        "source": None,
        "internal": False,
    } in [operation_dict(operation) for operation in analysis.operations]


def operation_dict(operation):
    return {
        "operation": operation.operation,
        "target_type": operation.target_type,
        "database": operation.database,
        "sqlite_schema": operation.sqlite_schema,
        "table": operation.table,
        "target": operation.target,
        "columns": operation.columns,
        "source": operation.source,
        "internal": operation.internal,
    }


def test_analyze_create_table_operation():
    conn = sqlite3.connect(":memory:")
    try:
        analysis = analyze_sql_tables(
            conn,
            "create table foobar (id integer primary key, name text)",
            database_name="data",
        )
    finally:
        conn.close()

    assert {
        "operation": "create",
        "target_type": "table",
        "database": "data",
        "sqlite_schema": "main",
        "table": "foobar",
        "target": "foobar",
        "columns": (),
        "source": None,
        "internal": False,
    } in [operation_dict(operation) for operation in analysis.operations]
    assert not [
        operation
        for operation in analysis.operations
        if operation.table in {"sqlite_master", "sqlite_schema"}
        and not operation.internal
    ]


def test_analyze_vacuum_operation():
    conn = sqlite3.connect(":memory:")
    try:
        analysis = analyze_sql_tables(conn, "vacuum", database_name="data")
    finally:
        conn.close()

    assert [operation_dict(operation) for operation in analysis.operations] == [
        {
            "operation": "vacuum",
            "target_type": "database",
            "database": "data",
            "sqlite_schema": "main",
            "table": None,
            "target": "data",
            "columns": (),
            "source": None,
            "internal": False,
        }
    ]


def test_analyze_statement_with_no_authorizer_callbacks_is_unknown():
    conn = sqlite3.connect(":memory:")
    try:
        analysis = analyze_sql_tables(conn, "reindex", database_name="data")
    finally:
        conn.close()

    assert [operation_dict(operation) for operation in analysis.operations] == [
        {
            "operation": "unknown",
            "target_type": "statement",
            "database": "data",
            "sqlite_schema": None,
            "table": None,
            "target": None,
            "columns": (),
            "source": None,
            "internal": False,
        }
    ]


def test_analyze_transaction_operation(conn):
    analysis = analyze_sql_tables(conn, "commit", database_name="data")

    assert [operation_dict(operation) for operation in analysis.operations] == [
        {
            "operation": "commit",
            "target_type": "transaction",
            "database": None,
            "sqlite_schema": None,
            "table": None,
            "target": "COMMIT",
            "columns": (),
            "source": None,
            "internal": False,
        }
    ]


def test_analyze_savepoint_operation(conn):
    analysis = analyze_sql_tables(conn, "savepoint s", database_name="data")

    assert [operation_dict(operation) for operation in analysis.operations] == [
        {
            "operation": "savepoint",
            "target_type": "transaction",
            "database": None,
            "sqlite_schema": None,
            "table": None,
            "target": "BEGIN s",
            "columns": (),
            "source": None,
            "internal": False,
        }
    ]


def test_analyze_function_operation(conn):
    analysis = analyze_sql_tables(
        conn,
        "insert into dogs (name) values (upper(:name))",
        {"name": "Cleo"},
        database_name="data",
    )

    assert {
        (
            operation.operation,
            operation.target_type,
            operation.target,
            operation.database,
            operation.table,
        )
        for operation in analysis.operations
    } == {
        ("insert", "table", "dogs", "data", "dogs"),
        ("function", "function", "upper", None, None),
        ("read", "table", "dogs", "data", "dogs"),
        ("update", "table", "cats", "data", "cats"),
        ("read", "table", "cats", "data", "cats"),
        ("insert", "table", "log", "data", "log"),
    }


def test_analyze_create_virtual_table_operation():
    conn = sqlite3.connect(":memory:")
    try:
        analysis = analyze_sql_tables(
            conn,
            "create virtual table docs using fts5(body)",
            database_name="data",
        )
    finally:
        conn.close()

    assert {
        "operation": "create",
        "target_type": "virtual-table",
        "database": "data",
        "sqlite_schema": "main",
        "table": "docs",
        "target": "docs",
        "columns": (),
        "source": None,
        "internal": False,
    } in [operation_dict(operation) for operation in analysis.operations]


def test_analyze_table_kind_for_regular_virtual_and_shadow_tables():
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript("""
            create table dogs (id integer primary key, name text);
            create virtual table docs using fts5(title, body, content='');
        """)

        regular_analysis = analyze_sql_tables(
            conn,
            "insert into dogs (name) values ('Cleo')",
            database_name="data",
        )
        virtual_analysis = analyze_sql_tables(
            conn,
            "insert into docs(docs) values('delete-all')",
            database_name="data",
        )
        shadow_analysis = analyze_sql_tables(
            conn,
            "insert into docs_config(k, v) values ('x', 1)",
            database_name="data",
        )
    finally:
        conn.close()

    regular_insert = next(
        operation
        for operation in regular_analysis.operations
        if operation.operation == "insert" and operation.table == "dogs"
    )
    virtual_insert = next(
        operation
        for operation in virtual_analysis.operations
        if operation.operation == "insert" and operation.table == "docs"
    )
    shadow_insert = next(
        operation
        for operation in shadow_analysis.operations
        if operation.operation == "insert" and operation.table == "docs_config"
    )

    assert regular_insert.table_kind == "table"
    assert virtual_insert.table_kind == "virtual"
    assert shadow_insert.table_kind == "shadow"


def test_analyze_create_table_as_select_function_is_not_internal():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("create table secret(value text)")
        analysis = analyze_sql_tables(
            conn,
            "create table copied as select substr(value, 1, 1) from secret",
            database_name="data",
        )
    finally:
        conn.close()

    assert {
        "operation": "function",
        "target_type": "function",
        "database": None,
        "sqlite_schema": None,
        "table": None,
        "target": "substr",
        "columns": (),
        "source": None,
        "internal": False,
    } in [operation_dict(operation) for operation in analysis.operations]


def test_analyze_insert_tables(conn):
    analysis = analyze_sql_tables(
        conn,
        "insert into dogs (name, age) values (:name, :age)",
        {"name": "Cleo", "age": 4},
        database_name="data",
    )

    assert set(table_operation_tuples(analysis)) == {
        ("insert", "data", "main", "dogs", (), None),
        ("read", "data", "main", "dogs", ("id", "name"), "dogs_after_insert"),
        ("update", "data", "main", "cats", ("name",), "dogs_after_insert"),
        ("read", "data", "main", "cats", ("id",), "dogs_after_insert"),
        ("insert", "data", "main", "log", (), "dogs_after_insert"),
    }


def test_analyze_update_tables(conn):
    analysis = analyze_sql_tables(
        conn,
        "update dogs set age = age + 1 where name = ?",
        ("Cleo",),
        database_name="data",
    )

    assert set(table_operation_tuples(analysis)) == {
        ("update", "data", "main", "dogs", ("age",), None),
        ("read", "data", "main", "dogs", ("age", "name"), None),
    }


def test_analyze_delete_tables(conn):
    analysis = analyze_sql_tables(
        conn,
        "delete from dogs where name = ?",
        ("Cleo",),
        database_name="data",
    )

    assert set(table_operation_tuples(analysis)) == {
        ("delete", "data", "main", "dogs", (), None),
        ("read", "data", "main", "dogs", ("name",), None),
    }


def test_analyze_insert_select_with_cte(conn):
    analysis = analyze_sql_tables(
        conn,
        """
        with old_dogs as (
            select name from dogs where age > :age
        )
        insert into cats (name)
        select name from old_dogs
        """,
        {"age": 10},
        database_name="data",
    )

    assert set(table_operation_tuples(analysis)) == {
        ("insert", "data", "main", "cats", (), None),
        ("read", "data", "main", "dogs", ("age", "name"), "old_dogs"),
    }


def test_analyze_view_with_instead_of_trigger(conn):
    analysis = analyze_sql_tables(
        conn,
        "update dog_names set name = :name where id = :id",
        {"name": "Zelda", "id": 1},
        database_name="data",
    )

    assert set(table_operation_tuples(analysis)) == {
        ("update", "data", "main", "dog_names", ("name",), None),
        ("read", "data", "main", "dogs", ("id", "name"), "dog_names"),
        ("read", "data", "main", "dog_names", ("id", "name"), "dog_names"),
        (
            "read",
            "data",
            "main",
            "dog_names",
            ("id", "name"),
            "dog_names_instead_of_update",
        ),
        ("update", "data", "main", "dogs", ("name",), "dog_names_instead_of_update"),
        ("read", "data", "main", "dogs", ("id",), "dog_names_instead_of_update"),
    }


def test_analyze_attached_database_tables(conn):
    conn.execute("attach database ':memory:' as extra")
    conn.execute("create table extra.people (id integer primary key, name text)")

    analysis = analyze_sql_tables(
        conn,
        "insert into extra.people (name) select name from dogs",
        database_name="data",
        schema_to_database={"extra": "extra_db"},
    )

    assert set(table_operation_tuples(analysis)) == {
        ("insert", "extra_db", "extra", "people", (), None),
        ("read", "data", "main", "dogs", ("name",), None),
    }


def test_analyze_disables_authorizer_on_error():
    class FakeConnection:
        def __init__(self):
            self.authorizers = []

        def set_authorizer(self, authorizer):
            self.authorizers.append(authorizer)

        def execute(self, sql, params):
            raise sqlite3.OperationalError("bad SQL")

    conn = FakeConnection()

    with pytest.raises(sqlite3.OperationalError):
        analyze_sql_tables(conn, "bad SQL")

    assert conn.authorizers[-1] is None


# The schema sqlite-utils creates for enable_fts(..., create_triggers=True)
FTS_WITH_TRIGGERS_SCHEMA = """
    create table repos (id integer primary key, name text, description text);
    create table log (message text);
    create virtual table [repos_fts] using fts5 (
        [name], [description], content=[repos], content_rowid=[id]
    );
    create trigger [repos_ai] after insert on [repos] begin
      insert into [repos_fts] (rowid, [name], [description])
      values (new.rowid, new.[name], new.[description]);
    end;
    create trigger [repos_ad] after delete on [repos] begin
      insert into [repos_fts] ([repos_fts], rowid, [name], [description])
      values('delete', old.rowid, old.[name], old.[description]);
    end;
    create trigger [repos_au] after update on [repos] begin
      insert into [repos_fts] ([repos_fts], rowid, [name], [description])
      values('delete', old.rowid, old.[name], old.[description]);
      insert into [repos_fts] (rowid, [name], [description])
      values (new.rowid, new.[name], new.[description]);
    end;
"""


@pytest.fixture
def fts_db_path(tmp_path):
    # A file database, so each test can open a connection that has not yet
    # connected to the FTS virtual table
    path = str(tmp_path / "fts.db")
    conn = sqlite3.connect(path)
    conn.executescript(FTS_WITH_TRIGGERS_SCHEMA)
    conn.close()
    return path


def all_table_operation_tuples(analysis):
    return {
        (operation.operation, operation.table, operation.source, operation.internal)
        for operation in analysis.operations
        if operation.target_type == "table"
    }


def test_analyze_ignores_virtual_table_connect_reads(fts_db_path):
    # FTS5 reads its <name>_config shadow table the first time a connection
    # uses the virtual table. That read belongs to the module, not to the
    # analyzed SQL, and must not depend on the connection's history.
    conn = sqlite3.connect(fts_db_path)
    try:
        first = analyze_sql_tables(
            conn, "delete from repos where id = 1", database_name="data"
        )
        second = analyze_sql_tables(
            conn, "delete from repos where id = 1", database_name="data"
        )
    finally:
        conn.close()

    assert "repos_fts_config" not in {operation.table for operation in first.operations}
    assert all_table_operation_tuples(first) == all_table_operation_tuples(second)


def test_analyze_reports_explicit_shadow_table_reads_on_fresh_connection(
    fts_db_path,
):
    conn = sqlite3.connect(fts_db_path)
    try:
        analysis = analyze_sql_tables(
            conn,
            "insert into log select v from repos_fts_config",
            database_name="data",
        )
    finally:
        conn.close()

    assert ("read", "data", "main", "repos_fts_config", ("v",), None) in set(
        table_operation_tuples(analysis)
    )


@pytest.mark.parametrize(
    "sql,trigger,expected",
    (
        (
            "insert into repos (name) values ('datasette')",
            "repos_ai",
            {("insert", "data", "main", "repos", (), None)},
        ),
        (
            "update repos set name = 'datasette' where id = 1",
            "repos_au",
            {
                ("update", "data", "main", "repos", ("name",), None),
                ("read", "data", "main", "repos", ("id",), None),
            },
        ),
        (
            "delete from repos where id = 1",
            "repos_ad",
            {
                ("delete", "data", "main", "repos", (), None),
                ("read", "data", "main", "repos", ("id",), None),
            },
        ),
    ),
    ids=("insert", "update", "delete"),
)
def test_analyze_fts_index_maintenance_triggers_are_internal(
    fts_db_path, sql, trigger, expected
):
    conn = sqlite3.connect(fts_db_path)
    try:
        analysis = analyze_sql_tables(conn, sql, database_name="data")
    finally:
        conn.close()

    assert set(table_operation_tuples(analysis)) == expected
    trigger_operations = [
        operation for operation in analysis.operations if operation.source == trigger
    ]
    assert ("insert", "repos_fts") in {
        (operation.operation, operation.table) for operation in trigger_operations
    }
    assert all(operation.internal for operation in trigger_operations)


@pytest.mark.parametrize(
    "extra_schema,sql,trigger",
    (
        # The trigger does more than maintain the FTS index
        (
            """
            create trigger repos_ad_log after delete on repos begin
              insert into repos_fts (repos_fts, rowid, name, description)
              values('delete', old.rowid, old.name, old.description);
              insert into log (message) values (old.name);
            end;
            """,
            "delete from repos where id = 1",
            "repos_ad_log",
        ),
        # The trigger writes an FTS index that belongs to a different table
        (
            """
            create table other (id integer primary key, name text);
            create trigger other_ai after insert on other begin
              insert into repos_fts (repos_fts, rowid, name, description)
              values('delete-all', null, null, null);
            end;
            """,
            "insert into other (name) values ('x')",
            "other_ai",
        ),
        # The FTS table stores its own content, so it is not derived from
        # the table the trigger is attached to
        (
            """
            create virtual table own_fts using fts5(name);
            create trigger repos_ai_own after insert on repos begin
              insert into own_fts (rowid, name) values (new.id, new.name);
            end;
            """,
            "insert into repos (name) values ('x')",
            "repos_ai_own",
        ),
    ),
    ids=("extra-write", "other-table", "contentful-fts"),
)
def test_analyze_other_triggers_writing_fts_are_not_internal(
    fts_db_path, extra_schema, sql, trigger
):
    conn = sqlite3.connect(fts_db_path)
    try:
        conn.executescript(extra_schema)
        analysis = analyze_sql_tables(conn, sql, database_name="data")
    finally:
        conn.close()

    trigger_operations = [
        operation for operation in analysis.operations if operation.source == trigger
    ]
    assert trigger_operations
    assert not any(operation.internal for operation in trigger_operations)


def test_analyze_view_sharing_name_with_fts_trigger_is_not_internal(fts_db_path):
    # SQLite reports the innermost trigger *or view* as the source, and a view
    # may share a name with a trigger
    conn = sqlite3.connect(fts_db_path)
    try:
        conn.execute("create view repos_ad as select id, name from repos")
        analysis = analyze_sql_tables(
            conn,
            "delete from repos where id in (select id from repos_ad)",
            database_name="data",
        )
    finally:
        conn.close()

    source_operations = [
        operation for operation in analysis.operations if operation.source == "repos_ad"
    ]
    assert ("read", "repos") in {
        (operation.operation, operation.table) for operation in source_operations
    }
    assert not any(operation.internal for operation in source_operations)
