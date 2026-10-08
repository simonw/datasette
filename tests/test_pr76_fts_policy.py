"""Policy and compatibility coverage for PR #76, run against the fixed checkout."""

import uuid

import pytest

from datasette.app import Datasette
from datasette.resources import TableResource
from datasette.utils.sqlite import sqlite3, sqlite_derived_table_dependencies


@pytest.mark.parametrize("vocab_name", ["words", "name USING fts4aux", 'quoted"name'])
@pytest.mark.parametrize(
    "module,arguments",
    [
        ("fts5vocab", "'Search,Index', 'row'"),
        ("fts5vocab", "'SEARCH,INDEX', 'col'"),
        ("fts5vocab", "'Search,Index', 'instance'"),
        ("fts4aux", "'Search,Index'"),
    ],
)
def test_vocabulary_dependency_identity(module, arguments, vocab_name):
    conn = sqlite3.connect(":memory:")
    try:
        fts = "fts5" if module == "fts5vocab" else "fts4"
        conn.execute(f'create virtual table "Search,Index" using {fts}(body)')
        quoted_name = '"' + vocab_name.replace('"', '""') + '"'
        conn.execute(
            f"create virtual table {quoted_name} USING /* module */ {module}({arguments})"
        )
        assert sqlite_derived_table_dependencies(conn)[vocab_name] == "Search,Index"
    finally:
        conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("module", ["fts5", "fts4"])
@pytest.mark.parametrize("external_content", [False, True], ids=["one-hop", "two-hop"])
@pytest.mark.parametrize(
    "source_allowed,vocab_allowed", [(False, True), (True, False), (True, True)]
)
async def test_vocabulary_immediate_source_permissions(
    module, external_content, source_allowed, vocab_allowed
):
    ds = Datasette(
        memory=True,
        config={
            "databases": {
                "data": {
                    "tables": {
                        "search": {
                            "permissions": {
                                "view-table": (
                                    {"id": "reader"} if source_allowed else False
                                )
                            }
                        },
                        "words": {"permissions": {"view-table": vocab_allowed}},
                    }
                }
            }
        },
    )
    db = ds.add_memory_database(uuid.uuid4().hex, name="data")
    await db.execute_write("create table documents(body text)")
    options = "body, content='documents'" if external_content else "body"
    await db.execute_write(f"create virtual table search using {module}({options})")
    definition = (
        "fts5vocab('SEARCH', 'row')" if module == "fts5" else "fts4aux('SEARCH')"
    )
    await db.execute_write(f"create virtual table words using {definition}")
    await ds.invoke_startup()
    try:
        actor = {"id": "reader"}
        expected = source_allowed and vocab_allowed and not external_content
        for name in ("words", "WORDS"):
            assert (
                await ds.allowed(
                    action="view-table",
                    resource=TableResource("data", name),
                    actor=actor,
                )
                is expected
            )
        resources = await ds.allowed_resources(
            "view-table", parent="data", actor=actor, include_is_private=True
        )
        words = [r for r in resources.resources if r.child == "words"]
        assert bool(words) is expected
        if expected:
            assert words[0].private
        assert not await ds.allowed(
            action="view-table", resource=TableResource("data", "words")
        )
        # Dropping the source invalidates dependency metadata and remains denied.
        await db.execute_write("drop table search")
        assert not await ds.allowed(
            action="view-table", resource=TableResource("data", "words"), actor=actor
        )
    finally:
        ds.close()


@pytest.mark.parametrize(
    "module,definition",
    [
        ("fts5", "fts5vocab('main', 'search', 'row')"),
        ("fts4", "fts4aux('main', 'search')"),
    ],
)
def test_cross_schema_vocabulary_is_unresolved(module, definition):
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(f"create virtual table search using {module}(body)")
        conn.execute(f"create virtual table temp.words using {definition}")
        # Cross-schema ownership is not representable by the current map.
        # The source is itself derived, so the immediate-source policy denies it.
        assert (
            sqlite_derived_table_dependencies(conn, schema="temp")["words"] == "words"
        )
    finally:
        conn.close()


@pytest.mark.parametrize(
    "definition",
    [
        """CREATE VIRTUAL TABLE"words"USING"fts5vocab"('search', 'row')""",
        """CREATE VIRTUAL TABLE[words]USING[fts5vocab]('search', 'row')""",
        """CREATE VIRTUAL TABLE`words`USING`fts5vocab`('search', 'row')""",
    ],
)
def test_vocabulary_quoted_token_boundaries(definition):
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("create virtual table search using fts5(body)")
        conn.execute(definition)
        assert sqlite_derived_table_dependencies(conn)["words"] == "search"
    finally:
        conn.close()
