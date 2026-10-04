"""Requests, startup and background work only touch the databases they need.

With a capped, idle-reaped connection pool, a loop over every attached
database opens (and evicts) one connection per database. These tests attach
50 databases and use tests/connection_budget.py to assert which databases
each endpoint opens, reads or writes. Every test uses a fresh instance, so
caches that are only warm after a first request do not hide a sweep.
"""

import json
import os
import shutil
import sqlite3
import time

import pytest

from datasette.app import Datasette
from datasette.utils.catalog import (
    catalog_relationship_counts,
    catalog_summaries,
    catalog_table_details,
)
from datasette.utils.sqlite import (
    sqlite_derived_table_dependencies,
    supports_table_list,
)

from .connection_budget import INTERNAL, connection_budget

N = 50


@pytest.fixture(autouse=True)
def only_default_plugins():
    """Plugins other tests leave registered (tests/plugins/my_plugin.py and
    friends query datasette.get_database() from their hooks) are not part
    of what these tests measure: unregister them for the duration."""
    from datasette.plugins import DEFAULT_PLUGINS, pm

    removed = [
        (name, plugin)
        for name, plugin in pm.list_name_plugin()
        if name not in DEFAULT_PLUGINS
    ]
    for name, _ in removed:
        pm.unregister(name=name)
    try:
        yield
    finally:
        for name, plugin in removed:
            if pm.get_plugin(name) is None:
                pm.register(plugin, name=name)


def _make_db(path, i):
    conn = sqlite3.connect(path)
    conn.executescript("""
        create table t2 (id integer primary key, title text);
        create table t1 (
            id integer primary key, name text, other_id integer references t2(id)
        );
        """)
    conn.executemany(
        "insert into t1 (name, other_id) values (?, ?)",
        [(f"n{j}", j % 3) for j in range(20)],
    )
    conn.executemany(
        "insert into t2 (title) values (?)", [(f"t{j}",) for j in range(3)]
    )
    if i % 5 == 0:
        conn.executescript("""
            create virtual table t1_fts using fts5(
                name, content="t1", content_rowid='id'
            );
            insert into t1_fts(t1_fts) values('rebuild');
            """)
    if i % 3 == 0:
        conn.execute("create view v1 as select * from t1")
    if i % 7 == 0:
        conn.execute("create table _hidden (x)")
    conn.commit()
    conn.close()


@pytest.fixture(scope="module")
def db_files(tmp_path_factory):
    directory = tmp_path_factory.mktemp("budget")
    paths = []
    for i in range(N):
        path = str(directory / f"db{i:03d}.db")
        _make_db(path, i)
        paths.append(path)
    # Older than the SchemaWatcher's racy window, so a sweep is stat-only
    old = time.time() - 60
    for path in paths:
        os.utime(path, (old, old))
    return paths


def _ds(paths, **kwargs):
    settings = {"schema_watch_interval_ms": 0}
    settings.update(kwargs.pop("settings", {}))
    ds = Datasette(paths, settings=settings, **kwargs)
    ds.root_enabled = True
    return ds


def _root(ds):
    return {"ds_actor": ds.client.actor_cookie({"id": "root"})}


async def _get(ds, path, cookies=None):
    async with connection_budget() as budget:
        response = await ds.client.get(path, cookies=cookies or {})
    assert response.status_code == 200, (path, response.status_code, response.text)
    return response, budget


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/.json", "/-/", "/-/.json"])
@pytest.mark.parametrize("as_root", [False, True])
async def test_index_page_opens_no_database(db_files, path, as_root):
    ds = _ds(db_files)
    await ds.invoke_startup()
    response, budget = await _get(ds, path, _root(ds) if as_root else None)
    budget.assert_only()
    budget.assert_no_connections()
    if path.endswith(".json"):
        data = response.json()
        assert len(data["databases"]) == N
        # More databases than COUNT_MAX_DATABASES: no row counts
        assert not any(d["show_table_row_counts"] for d in data["databases"])
        db000 = next(d for d in data["databases"] if d["name"] == "db000")
        assert db000["tables_count"] == 2  # t1, t2
        # _hidden, and t1_fts (an external content FTS table is hidden).
        # Its fts5 shadow tables are not listed at all: they derive from
        # t1_fts, which is itself derived (the one-hop permission policy)
        assert db000["hidden_tables_count"] == 2
        assert db000["views_count"] == 1
        shown = {t["name"]: t for t in db000["tables_and_views_truncated"]}
        assert shown["t1"]["columns"] == ["id", "name", "other_id"]
        assert shown["t1"]["primary_keys"] == ["id"]
        assert shown["t1"]["fts_table"] == "t1_fts"
        # Sorted by foreign key relationships when there are no counts
        assert shown["t1"]["num_relationships_for_sorting"] == 1
    ds.close()


@pytest.mark.asyncio
async def test_index_page_counts_rows_for_few_databases(db_files):
    # At most COUNT_MAX_DATABASES databases: rows are counted, and only the
    # databases listed are touched
    ds = _ds(db_files[:3])
    await ds.invoke_startup()
    response, budget = await _get(ds, "/.json")
    budget.assert_only("db000", "db001", "db002")
    data = response.json()
    assert all(d["show_table_row_counts"] for d in data["databases"])
    assert data["databases"][0]["table_rows_sum"] == 23
    ds.close()


@pytest.mark.asyncio
async def test_index_page_uses_cached_immutable_counts(db_files):
    # Immutable databases' counts are computed once, when first needed, and
    # reused even when there are too many databases to count
    ds = Datasette(
        db_files[3:],
        immutables=db_files[:3],
        settings={"schema_watch_interval_ms": 0},
    )
    await ds.invoke_startup()
    for name in ("db000", "db001", "db002"):
        await ds.get_database(name).table_counts()
    response, budget = await _get(ds, "/.json")
    budget.assert_no_connections()
    budget.assert_only()
    by_name = {d["name"]: d for d in response.json()["databases"]}
    assert by_name["db000"]["show_table_row_counts"]
    assert not by_name["db010"]["show_table_row_counts"]
    ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/-/databases", "/-/databases.json"])
async def test_databases_endpoint(db_files, path):
    ds = _ds(db_files)
    await ds.invoke_startup()
    _, budget = await _get(ds, path)
    budget.assert_only()
    budget.assert_no_connections()
    ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/db001",
        "/db001.json",
        "/db000.json?_search=n",
        "/db001/t1",
        "/db001/t1.json",
        "/db001/t1?_facet=name",
        "/db001/t1.json?_facet=name",
        "/db000/t1_fts.json?_search=n1",
        "/db001/-/query?sql=select+1",
        "/db001/-/query.json?sql=select+1",
        "/db001/-/query",
        "/db001/t1/1",
        "/db001/t1/1.json",
        "/db001/-/schema.json",
        "/db001/t1/-/autocomplete?q=n",
    ],
)
@pytest.mark.parametrize("as_root", [False, True])
async def test_database_scoped_pages_touch_only_their_database(db_files, path, as_root):
    ds = _ds(db_files)
    await ds.invoke_startup()
    database = path.split("/")[1].split(".")[0]
    _, budget = await _get(ds, path, _root(ds) if as_root else None)
    budget.assert_only(database)
    ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,as_root",
    [
        ("/-/versions", False),
        ("/-/versions.json", False),
        ("/-/plugins.json", False),
        ("/-/settings.json", False),
        ("/-/config.json", False),
        ("/-/actor.json", False),
        ("/-/threads", True),
        ("/-/threads.json", True),
        ("/-/tasks.json", True),
        ("/-/actions.json", True),
        ("/-/jump?q=t1", False),
        ("/-/jump.json?q=t1", False),
        ("/-/jump.json", False),
        ("/-/allowed.json?action=view-table", False),
        ("/-/allowed.json?action=view-table", True),
        ("/-/allowed.json?action=view-database", False),
        ("/-/allowed?action=view-table", True),
        ("/-/rules.json?action=view-table", True),
        ("/-/check.json?action=view-table&parent=db001&child=t1", True),
        ("/-/permissions", True),
        ("/-/allow-debug", False),
        ("/-/api", False),
        ("/-/api", True),
        ("/-/create-token", True),
        ("/-/queries.json", False),
        ("/-/patterns", False),
    ],
)
async def test_instance_pages_open_no_user_database(db_files, path, as_root):
    ds = _ds(db_files)
    await ds.invoke_startup()
    # /-/versions probes the SQLite library once per instance
    await ds.client.get("/-/versions.json")
    _, budget = await _get(ds, path, _root(ds) if as_root else None)
    budget.assert_only()
    budget.assert_no_connections()
    ds.close()


@pytest.mark.asyncio
async def test_versions_probes_sqlite_once(db_files):
    ds = _ds(db_files[:12], crossdb=True)
    await ds.invoke_startup()
    async with connection_budget() as budget:
        for _ in range(3):
            assert (await ds.client.get("/-/versions.json")).status_code == 200
    # One :memory: connection for the first request, none after; it is not
    # given the --crossdb ATTACHes
    assert sum(budget.probes.values()) == 1
    assert not budget.attached
    budget.assert_only(probes=1)
    ds.close()


@pytest.mark.asyncio
async def test_detect_json1_is_cached(db_files):
    ds = _ds(db_files[:2])
    await ds.invoke_startup()
    await ds.client.get("/db001/t1")
    _, budget = await _get(ds, "/db001/t1?_facet=name")
    assert not budget.probes
    ds.close()


@pytest.mark.asyncio
async def test_instance_schema_reads_each_visible_database_once(db_files):
    # /-/schema dumps every visible database's sqlite_master: one read each,
    # nothing for databases the actor cannot see
    ds = _ds(
        db_files[:10],
        config={"databases": {"db003": {"allow": False}}},
    )
    await ds.invoke_startup()
    _, budget = await _get(ds, "/-/schema.json")
    expected = {f"db{i:03d}" for i in range(10)} - {"db003"}
    assert budget.touched() - {INTERNAL} == expected
    assert all(budget.reads[name] == 1 for name in expected)
    ds.close()


@pytest.mark.asyncio
async def test_autocomplete_debug_is_bounded(db_files):
    ds = _ds(db_files)
    await ds.invoke_startup()
    _, budget = await _get(ds, "/-/debug/autocomplete")
    # Only databases holding one of the (at most 5) suggestions
    budget.assert_at_most(5)
    ds.close()


@pytest.mark.asyncio
async def test_execute_write_touches_only_its_database(db_files, tmp_path):
    paths = []
    for path in db_files[:20]:
        target = tmp_path / os.path.basename(path)
        shutil.copy(path, target)
        paths.append(str(target))
    ds = _ds(paths)
    await ds.invoke_startup()
    async with connection_budget() as budget:
        response = await ds.client.post(
            "/db004/-/execute-write",
            actor={"id": "root"},
            data={"sql": "create table new_table (id integer primary key)"},
        )
    assert response.status_code == 200, response.text
    assert "Query executed" in response.text
    budget.assert_only("db004")
    # The catalog already has the new table
    rows = await ds.get_internal_database().execute(
        "select 1 from catalog_tables where database_name = 'db004' and table_name = 'new_table'"
    )
    assert rows.rows
    ds.close()


@pytest.mark.asyncio
async def test_telemetry_metric_callbacks_open_nothing(db_files):
    from datasette import telemetry

    ds = _ds(db_files)
    await ds.invoke_startup()
    telemetry.register_datasette(ds)
    try:
        with connection_budget() as budget:
            observations = 0
            for callback in (
                telemetry.observe_sql_thread_limit,
                telemetry.observe_sql_thread_queue_depth,
                telemetry.observe_pending_queries,
                telemetry.observe_write_queue_depth,
                telemetry.observe_open_connections,
            ):
                observations += len(list(callback()))
        assert observations
        budget.assert_only(internal=False)
        budget.assert_no_connections(internal=False)
    finally:
        telemetry.unregister_datasette(ds)
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("num_sql_threads", [0, 3])
async def test_startup_opens_each_database_at_most_once(db_files, num_sql_threads):
    imm = db_files[:5]
    with connection_budget() as budget:
        ds = Datasette(
            db_files[5:],
            immutables=imm,
            settings={
                "schema_watch_interval_ms": 0,
                "num_sql_threads": num_sql_threads,
            },
        )
        await ds._startup_sequence()
    # One catalog scan connection per database, no pooled reads or writes
    # (immutable table counts are no longer precomputed at startup)
    assert set(budget.opened) - {INTERNAL, "_memory"} == {
        f"db{i:03d}" for i in range(N)
    }
    assert all(
        count == 1 for name, count in budget.opened.items() if name != INTERNAL
    ), budget.opened
    assert set(budget.reads) <= {INTERNAL}, budget.reads
    assert set(budget.writes) <= {INTERNAL}, budget.writes
    assert not budget.attached
    ds.close()


@pytest.mark.asyncio
async def test_startup_with_crossdb_attaches_nothing(db_files):
    with connection_budget() as budget:
        ds = Datasette(
            db_files[:12],
            settings={"schema_watch_interval_ms": 0},
            crossdb=True,
        )
        await ds._startup_sequence()
    assert not budget.attached
    # A crossdb query still sees the attached databases
    response = await ds.client.get(
        "/_memory/-/query.json?sql=select+count(*)+from+db001.t1&_shape=array"
    )
    assert response.status_code == 200
    ds.close()


@pytest.mark.asyncio
async def test_restart_with_persisted_catalog_opens_nothing(
    db_files, tmp_path, monkeypatch
):
    from datasette import schema_watcher

    # The files' ctimes are recent (os.utime() in the fixture): without this
    # their stored fingerprints would be racy, and so rescanned
    monkeypatch.setattr(schema_watcher, "RACY_WINDOW_NS", 0)
    internal = str(tmp_path / "internal.db")
    ds = Datasette(db_files, internal=internal)
    await ds.invoke_startup()
    ds.close()
    with connection_budget() as budget:
        ds = Datasette(
            db_files, internal=internal, settings={"schema_watch_interval_ms": 0}
        )
        await ds._startup_sequence()
        response = await ds.client.get("/.json")
    assert response.status_code == 200
    budget.assert_only()
    ds.close()


@pytest.mark.asyncio
async def test_idle_sweeps_open_nothing(db_files):
    ds = _ds(db_files)
    await ds.invoke_startup()
    with connection_budget() as budget:
        for _ in range(3):
            await ds._refresh_schemas(background=True)
        await ds.refresh_schemas(force=True)
    budget.assert_only(internal=False)
    budget.assert_no_connections(internal=False)
    ds.close()


@pytest.mark.asyncio
async def test_failed_scan_is_not_retried_by_idle_sweeps(
    db_files, tmp_path, monkeypatch
):
    from datasette import schema_watcher

    # The file was just written: do not wait out the racy window
    monkeypatch.setattr(schema_watcher, "RACY_WINDOW_NS", 0)
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a SQLite database" * 10)
    old = time.time() - 60
    os.utime(bad, (old, old))
    ds = _ds(db_files[:3] + [str(bad)])
    await ds.invoke_startup()
    assert ds.get_database("bad")._watch_state.error is not None
    with connection_budget() as budget:
        for _ in range(3):
            await ds._refresh_schemas(background=True)
    assert "bad" not in budget.touched()
    # ...until the file changes
    good = sqlite3.connect(tmp_path / "good.db")
    good.execute("create table fixed (id integer primary key)")
    good.commit()
    good.close()
    os.replace(tmp_path / "good.db", bad)
    await ds._refresh_schemas(background=True)
    rows = await ds.get_internal_database().execute(
        "select table_name from catalog_tables where database_name = 'bad'"
    )
    assert [r[0] for r in rows.rows] == ["fixed"]
    ds.close()


@pytest.mark.asyncio
async def test_check_databases_reuses_catalog_scan(db_files, tmp_path):
    from datasette.cli import check_databases
    from datasette.utils import ConnectionProblem

    ds = _ds(db_files)
    with connection_budget() as budget:
        await check_databases(ds)
    # The catalog scan opened each database once; no second pass
    assert all(count == 1 for name, count in budget.opened.items() if name != INTERNAL)
    assert set(budget.reads) <= {INTERNAL, "_memory"}, budget.reads
    ds.close()

    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a SQLite database" * 10)
    ds = _ds(db_files[:3] + [str(bad)])
    with pytest.raises(Exception) as excinfo:
        await check_databases(ds)
    assert "bad.db" in str(excinfo.value)
    assert "file is not a database" in str(excinfo.value)
    assert not isinstance(excinfo.value, ConnectionProblem)
    ds.close()


# Parity: the catalog helpers answer exactly what the live helpers do


MISC_SQL = """
    create table docs (id integer primary key, title text, body text);
    create virtual table docs_fts using fts4(title, body, content="docs");
    create virtual table docs_vocab using fts4aux(docs_fts);
    create virtual table plain using fts5(x);
    create virtual table geo using rtree(id, minx, maxx);
    create table _private (x);
    create table geometry_columns (f_table_name text);
    create table idx_foo_bar (x);
    create table IDXA (x);
    create table parent (id integer primary key);
    create table child (
        id integer primary key,
        p integer references parent(id),
        q integer references missing(id)
    );
    create table comp (a, b, foreign key (a, b) references parent(id, id));
    create table self_ref (id integer primary key, up integer references self_ref(id));
    create table wide (b text, a integer, c, primary key (a, b));
    create view v_docs as select * from docs;
"""


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["misc", "fixtures"])
async def test_catalog_helpers_match_live_introspection(tmp_path, name):
    from datasette.fixtures import write_fixture_database
    from datasette.utils.catalog import (
        all_derived_table_dependencies,
        catalog_all_foreign_keys,
    )

    path = str(tmp_path / f"{name}.db")
    if name == "fixtures":
        write_fixture_database(path)
        config_hidden = set()
        config = {}
    else:
        conn = sqlite3.connect(path)
        conn.executescript(MISC_SQL)
        conn.close()
        config_hidden = {"docs"}
        config = {"databases": {"misc": {"tables": {"docs": {"hidden": True}}}}}
    ds = Datasette([path], config=config, settings={"schema_watch_interval_ms": 0})
    await ds.invoke_startup()
    db = ds.get_database(name)
    tables = await db.table_names()
    summary = (await catalog_summaries(ds, [name]))[name]
    # hidden_table_names() also lists SpatiaLite names that do not exist
    hidden = set(await db.hidden_table_names()) & set(tables)
    assert {t for t in tables if summary.is_hidden(t, config_hidden)} == hidden
    assert summary.views == set(await db.view_names())

    details = await catalog_table_details(ds, [(name, t) for t in tables])
    for table in tables:
        assert details[(name, table)]["columns"] == await db.table_columns(table)
        assert details[(name, table)]["primary_keys"] == await db.primary_keys(table)
        assert details[(name, table)]["fts_table"] == await db.fts_table(table)

    all_foreign_keys = await db.get_all_foreign_keys()
    assert await catalog_all_foreign_keys(ds, name) == all_foreign_keys
    relationships = (await catalog_relationship_counts(ds, [name])).get(name, {})
    for table, fks in all_foreign_keys.items():
        assert relationships.get(table, 0) == len(fks["incoming"] + fks["outgoing"])

    derived = await all_derived_table_dependencies(ds)
    live = await db.execute_fn(sqlite_derived_table_dependencies)
    assert derived.get(name, {}) == live
    # Restored from the catalog rather than remembered from the scan
    db._cached_derived_table_dependencies = None
    assert await db.derived_table_dependencies() == live
    ds.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not supports_table_list(), reason="needs PRAGMA table_list")
async def test_catalog_records_table_types(tmp_path):
    path = str(tmp_path / "types.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        create table plain (id integer primary key);
        create virtual table f using fts5(x);
        """)
    conn.close()
    ds = Datasette([path], settings={"schema_watch_interval_ms": 0})
    await ds.invoke_startup()
    rows = await ds.get_internal_database().execute(
        "select table_name, type from catalog_tables where database_name = 'types'"
    )
    types = dict(rows.rows)
    assert types["plain"] == "table"
    assert types["f"] == "virtual"
    assert types["f_data"] == "shadow"
    ds.close()


@pytest.mark.asyncio
async def test_catalog_derived_dependencies_follow_catalog_changes(tmp_path):
    path = str(tmp_path / "live.db")
    sqlite3.connect(path).execute("create table docs (body text)").connection.close()
    ds = Datasette([path], settings={"schema_watch_interval_ms": 0})
    await ds.invoke_startup()
    from datasette.utils.catalog import all_derived_table_dependencies

    assert "live" not in await all_derived_table_dependencies(ds)
    db = ds.get_database("live")
    await db.execute_write(
        "create virtual table docs_fts using fts5(body, content='docs')"
    )
    derived = await all_derived_table_dependencies(ds)
    assert derived["live"]["docs_fts"] == "docs"
    assert derived["live"]["docs_fts_data"] == "docs_fts"
    ds.close()


def test_connection_budget_helper_counts(db_files):
    # The helper itself: a direct execute_fn is a read, connect() an open
    import asyncio

    async def run():
        ds = _ds(db_files[:2])
        await ds.invoke_startup()
        db = ds.get_database("db001")
        async with connection_budget() as budget:
            await db.execute("select 1")
            conn = sqlite3.connect(":memory:")
            conn.close()
        assert budget.reads["db001"] == 1
        assert budget.touched() == {"db001"}
        with pytest.raises(AssertionError, match="db001"):
            budget.assert_only("db000")
        ds.close()
        return budget

    budget = asyncio.run(run())
    assert sum(budget.probes.values()) == 1
    assert json.dumps(budget.summary())
