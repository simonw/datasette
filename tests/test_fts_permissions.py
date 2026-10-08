import pytest

from datasette import hookimpl
from datasette.app import Datasette
from datasette.permissions import Action, PermissionSQL, _permission_check_cache
from datasette.resources import DatabaseResource, TableResource
from datasette.utils.sqlite import sqlite3, sqlite_derived_table_dependencies


@pytest.mark.asyncio
@pytest.mark.parametrize("fts_module", ["fts4", "fts5"])
@pytest.mark.parametrize("actor", [None, {"id": "root"}], ids=["anonymous", "root"])
async def test_derived_permissions_allow_one_hop_but_deny_nested_sources(
    fts_module, actor
):
    class InspectPlugin:
        @hookimpl
        def register_actions(self):
            return [
                Action(
                    name="inspect-derived",
                    description="Inspect a table",
                    resource_class=TableResource,
                    also_requires="view-table",
                )
            ]

        @hookimpl
        def permission_resources_sql(self, action):
            if action == "inspect-derived":
                return PermissionSQL(
                    sql="SELECT NULL AS parent, NULL AS child, 1 AS allow, 'inspect allowed' AS reason"
                )

    ds = Datasette(memory=True)
    ds.pm.register(InspectPlugin(), name="inspect-derived-test")
    db = ds.add_memory_database(
        f"derived_one_hop_{fts_module}_{actor is not None}", name="data"
    )
    await db.execute_write("create table Documents (body text)")
    await db.execute_write(
        f"create virtual table Search using {fts_module}(body, content='Documents')"
    )
    await db.execute_write(
        f"create virtual table Nested using {fts_module}(body, content='sEaRcH')"
    )
    await ds.invoke_startup()
    token = _permission_check_cache.set({})
    try:
        # Both direct permissions are allowed, but a derived source makes its
        # dependent unavailable even to an actor who can view the whole chain.
        # Check and cache Search first so its cached grant cannot grant Nested.
        for table, expected in (
            ("Documents", True),
            ("Search", True),
            ("Nested", False),
            ("Search_docsize", False),
        ):
            for spelling in (table, table.upper(), table.lower()):
                assert await ds.allowed_many(
                    actions=["view-table", "inspect-derived"],
                    resource=TableResource("data", spelling),
                    actor=actor,
                ) == {"view-table": expected, "inspect-derived": expected}

        page = await ds.allowed_resources(
            "view-table", actor, parent="data", include_is_private=True, limit=1000
        )
        allowed = {resource.child for resource in page.resources}
        assert {"Documents", "Search"}.issubset(allowed)
        assert "Nested" not in allowed
        assert "Search_docsize" not in allowed
    finally:
        _permission_check_cache.reset(token)
        ds.pm.unregister(name="inspect-derived-test")
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("listing", [False, True], ids=["individual", "listing"])
async def test_derived_permission_discovery_error_is_retried(monkeypatch, listing):
    ds = Datasette(memory=True)
    db = ds.add_memory_database(f"derived_discovery_error_{listing}", name="data")
    await db.execute_write("create table documents (id integer primary key)")
    await ds.invoke_startup()

    class UnavailableSchema:
        def execute(self, sql):
            raise sqlite3.DatabaseError("schema temporarily unavailable")

    async def check():
        if listing:
            return await ds.allowed_resources("view-table", parent="data")
        return await ds.allowed(
            action="view-table", resource=TableResource("data", "documents")
        )

    token = _permission_check_cache.set({})
    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                "datasette.database.sqlite_derived_table_dependencies",
                lambda conn: sqlite_derived_table_dependencies(UnavailableSchema()),
            )
            with pytest.raises(sqlite3.DatabaseError, match="schema temporarily"):
                await check()

        # Failed discovery must not cache an empty map or a permission grant.
        assert db._cached_derived_table_dependencies is None
        assert not _permission_check_cache.get()
        result = await check()
        if listing:
            assert [resource.child for resource in result.resources] == ["documents"]
        else:
            assert result is True
        assert db._cached_derived_table_dependencies is not None
    finally:
        _permission_check_cache.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("fts_module", ("fts4", "fts5"))
async def test_external_content_fts_inherits_content_table_view_permission(fts_module):
    actor = {"id": "reader"}
    secret_marker = "ISSUE_17_EXTERNAL_CONTENT_FTS_SECRET"
    ds = Datasette(
        memory=True,
        config={
            "permissions": {
                "view-instance": {"id": "reader"},
                "view-database": {"id": "reader"},
                "view-table": {"id": "reader"},
                "execute-sql": {"id": "nobody"},
            },
            "databases": {
                "data": {
                    "tables": {
                        "secret": {"permissions": {"view-table": False}},
                    }
                }
            },
        },
    )
    db = ds.add_memory_database(f"issue_17_{fts_module}_permissions", name="data")
    await db.execute_write("create table secret (id integer primary key, body text)")
    await db.execute_write(
        "insert into secret (body) values (?)",
        [secret_marker],
    )
    fts_options = "body, content='secret'"
    if fts_module == "fts5":
        fts_options += ", content_rowid='id'"
    await db.execute_write(
        f"create virtual table secret_fts using {fts_module}({fts_options})"
    )
    await db.execute_write("insert into secret_fts(secret_fts) values ('rebuild')")
    await ds.invoke_startup()

    try:
        assert "secret_fts" in await db.hidden_table_names()
        assert (
            await ds.allowed(
                action="execute-sql",
                resource=DatabaseResource("data"),
                actor=actor,
            )
            is False
        )

        direct = await ds.client.get("/data/secret.json", actor=actor)
        assert direct.status_code == 403

        companion = await ds.client.get(
            "/data/secret_fts.json?_shape=array",
            actor=actor,
        )
        assert companion.status_code in (403, 404), (
            "An automatically hidden external-content FTS table must inherit "
            "the content table's view denial or be unavailable: "
            f"{companion.text}"
        )
        assert secret_marker not in companion.text
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fts_module", ("fts4", "fts5"))
@pytest.mark.parametrize("contentless", (False, True), ids=("internal", "contentless"))
async def test_fts_shadow_tables_inherit_logical_table_view_permission(
    fts_module, contentless
):
    table_config = {
        "secret_fts": {"permissions": {"view-table": False}},
        # An explicit allow on one implementation table must not override
        # the logical FTS table's denial.
        "secret_fts_docsize": {"permissions": {"view-table": True}},
    }
    ds = Datasette(
        memory=True,
        config={
            "permissions": {
                "view-instance": True,
                "view-database": True,
                "view-table": True,
                "execute-sql": False,
            },
            "databases": {"data": {"tables": table_config}},
        },
    )
    db = ds.add_memory_database(
        f"issue_17_{fts_module}_{'contentless' if contentless else 'internal'}",
        name="data",
    )
    options = "body, content=''" if contentless else "body"
    await db.execute_write(
        f"create virtual table secret_fts using {fts_module}({options})"
    )
    await db.execute_write(
        "insert into secret_fts(rowid, body) values (1, 'ISSUE_17_SHADOW_SECRET')"
    )
    await ds.invoke_startup()

    try:
        dependencies = await db.derived_table_dependencies()
        shadow_tables = sorted(
            table for table, source in dependencies.items() if source == "secret_fts"
        )
        assert shadow_tables
        assert "secret_fts_docsize" in shadow_tables

        for shadow_table in shadow_tables:
            assert (
                await ds.allowed(
                    action="view-table",
                    resource=TableResource("data", shadow_table),
                )
                is False
            )
            response = await ds.client.get(f"/data/{shadow_table}.json?_shape=array")
            assert response.status_code == 403
            assert "ISSUE_17_SHADOW_SECRET" not in response.text

        allowed = await ds.allowed_resources("view-table", parent="data", limit=1000)
        allowed_names = {resource.child for resource in allowed.resources}
        assert not set(shadow_tables).intersection(allowed_names)

        database_json = await ds.client.get("/data.json")
        assert database_json.status_code == 200
        for shadow_table in shadow_tables:
            assert shadow_table not in database_json.text

        schema_json = await ds.client.get("/data/-/schema.json")
        assert schema_json.status_code == 200
        for shadow_table in shadow_tables:
            assert shadow_table not in schema_json.text
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_allowed,companion_allowed,expected",
    (
        (False, True, False),
        (True, False, False),
        (True, True, True),
    ),
)
async def test_external_content_and_companion_permissions_are_both_required(
    content_allowed, companion_allowed, expected
):
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "permissions": {
                "view-instance": True,
                "view-database": True,
            },
            "databases": {
                "data": {
                    "tables": {
                        "secret": {"permissions": {"view-table": content_allowed}},
                        "secret_fts": {
                            "permissions": {"view-table": companion_allowed}
                        },
                    }
                }
            },
        },
    )
    db = ds.add_memory_database(
        f"issue_17_explicit_{int(content_allowed)}_{int(companion_allowed)}",
        name="data",
    )
    await db.execute_write("create table secret(id integer primary key, body text)")
    await db.execute_write("insert into secret(body) values ('ISSUE_17_MATRIX_SECRET')")
    await db.execute_write(
        "create virtual table secret_fts using fts5("
        "body, content='secret', content_rowid='id')"
    )
    await db.execute_write("insert into secret_fts(secret_fts) values ('rebuild')")
    await ds.invoke_startup()

    try:
        assert (
            await ds.allowed(
                action="view-table",
                resource=TableResource("data", "secret_fts"),
            )
            is expected
        )
        response = await ds.client.get("/data/secret_fts.json?_shape=array")
        assert response.status_code == (200 if expected else 403)
        if not expected:
            assert "ISSUE_17_MATRIX_SECRET" not in response.text
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_derived_tables_propagate_private_flag_and_route_permissions():
    actor = {"id": "reader"}
    ds = Datasette(
        memory=True,
        config={
            "permissions": {
                "view-instance": True,
                "view-database": True,
                "view-table": True,
            },
            "databases": {
                "data": {
                    "tables": {
                        "secret": {"permissions": {"view-table": {"id": "reader"}}}
                    }
                }
            },
        },
    )
    db = ds.add_memory_database("issue_17_private_flag", name="data")
    await db.execute_write("create table secret(id integer primary key, body text)")
    await db.execute_write("insert into secret(body) values ('PRIVATE')")
    await db.execute_write(
        "create virtual table secret_fts using fts5("
        "body, content='secret', content_rowid='id')"
    )
    await db.execute_write("insert into secret_fts(secret_fts) values ('rebuild')")
    await ds.invoke_startup()

    try:
        actor_page = await ds.allowed_resources(
            "view-table", actor, parent="data", include_is_private=True, limit=1000
        )
        actor_resources = {
            resource.child: resource for resource in actor_page.resources
        }
        derived_names = set(await db.derived_table_dependencies())
        assert "secret_fts" in actor_resources
        assert actor_resources["secret_fts"].private
        # Shadow tables depend on the already-derived external-content FTS
        # table, so they remain unavailable even to the permitted reader.
        assert not (derived_names - {"secret_fts"}).intersection(actor_resources)

        anonymous_page = await ds.allowed_resources(
            "view-table", parent="data", limit=1000
        )
        anonymous_names = {resource.child for resource in anonymous_page.resources}
        assert not derived_names.intersection(anonymous_names)

        for path in (
            "/data/secret_fts.json?_facet=body",
            "/data/secret_fts.csv",
            "/data/secret_fts/-/autocomplete?q=PRIVATE",
            "/data/secret_fts/-/schema.json",
        ):
            denied = await ds.client.get(path)
            assert denied.status_code == 403
            allowed = await ds.client.get(path, actor=actor)
            assert allowed.status_code == 200
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_cyclic_derived_table_dependencies_fail_closed():
    ds = Datasette(memory=True)
    db = ds.add_memory_database("issue_17_cycle", name="data")
    await db.execute_write(
        "create virtual table first_fts using fts5(body, content='second_fts')"
    )
    await db.execute_write(
        "create virtual table second_fts using fts5(body, content='first_fts')"
    )
    await ds.invoke_startup()

    try:
        for table in ("first_fts", "second_fts"):
            assert (
                await ds.allowed(
                    action="view-table", resource=TableResource("data", table)
                )
                is False
            )

        page = await ds.allowed_resources("view-table", parent="data", limit=1000)
        allowed_names = {resource.child for resource in page.resources}
        assert "first_fts" not in allowed_names
        assert "second_fts" not in allowed_names
    finally:
        ds.close()
