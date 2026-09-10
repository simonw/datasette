import copy
import sqlite3

import pytest

from datasette import hookimpl
from datasette.app import Datasette
from datasette.plugins import pm


@pytest.fixture
def ds(tmp_path):
    path = tmp_path / "catalog.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            "create table Items (id integer primary key, label text);"
            "insert into Items values (1, 'Example');"
            "create view Report as select id, label from Items;"
            'create table "Ärea" (id integer primary key);'
            'create table "ärea" (id integer primary key);'
        )
    datasette = Datasette([path])
    yield datasette
    datasette.executor.shutdown(wait=True)


@pytest.fixture
def register_plugin():
    plugins = []

    def register(plugin):
        pm.register(plugin)
        plugins.append(plugin)
        return plugin

    yield register
    for plugin in reversed(plugins):
        pm.unregister(plugin)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requested,canonical",
    [
        ("Items", "Items"),
        ("items", "Items"),
        ("ITEMS", "Items"),
        ("ItEmS", "Items"),
        ("Report", "Report"),
        ("report", "Report"),
        ("REPORT", "Report"),
        ("ÄREA", "Ärea"),
        ("äREA", "ärea"),
    ],
)
async def test_table_permission_hook_and_log_use_schema_spelling(
    ds, register_plugin, requested, canonical
):
    calls = []

    class Observer:
        @hookimpl
        def permission_allowed(self, action, resource):
            calls.append((action, resource))

    register_plugin(Observer())
    assert await ds.permission_allowed(
        None, "view-table", ("catalog", requested), default=True
    )
    assert calls == [("view-table", ("catalog", canonical))]
    assert ds._permission_checks[-1]["resource"] == ("catalog", canonical)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["items", "ITEMS", "report", "REPORT"])
async def test_table_allow_and_visibility_use_ascii_identity(ds, name):
    ds._metadata_local = {
        "databases": {
            "catalog": {
                "tables": {
                    "iTeMs": {"allow": {"id": "reader"}},
                    "rEpOrT": {"allow": {"id": "reader"}},
                }
            }
        }
    }
    assert await ds.check_visibility(
        {"id": "reader"}, "view-table", ("catalog", name)
    ) == (True, True)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,actor_id", [("ÄREA", "first"), ("äREA", "second")])
async def test_non_ascii_table_identities_remain_distinct(ds, name, actor_id):
    ds._metadata_local = {
        "databases": {
            "catalog": {
                "tables": {
                    "Ärea": {"allow": {"id": "first"}},
                    "ärea": {"allow": {"id": "second"}},
                }
            }
        }
    }
    assert (
        await ds.permission_allowed(
            {"id": actor_id}, "view-table", ("catalog", name), default=None
        )
        is True
    )
    assert ds._table_permission_allows("catalog", name) == [{"id": actor_id}]


@pytest.mark.parametrize(
    "plugin_name,local_name", [("Items", "ITEMS"), ("ITEMS", "Items")]
)
def test_local_metadata_precedence_across_table_spellings(
    ds, register_plugin, plugin_name, local_name
):
    plugin_metadata = {
        "databases": {
            "catalog": {
                "tables": {
                    plugin_name: {"allow": {"id": "plugin"}, "description": "Plugin"}
                }
            }
        }
    }
    ds._metadata_local = {
        "databases": {"catalog": {"tables": {local_name: {"allow": {"id": "local"}}}}}
    }
    original_plugin = copy.deepcopy(plugin_metadata)
    original_local = copy.deepcopy(ds._metadata_local)
    calls = []

    class Metadata:
        @hookimpl
        def get_metadata(self, key, database, table):
            calls.append((key, database, table))
            return plugin_metadata

    register_plugin(Metadata())
    assert ds._table_permission_allows("catalog", "items") == [{"id": "local"}]
    assert calls == [("tables", "catalog", None)]
    assert plugin_metadata == original_plugin
    assert ds._metadata_local == original_local
    # The ordinary public metadata API keeps the configured spellings.
    public_tables = ds.metadata("tables", database="catalog")
    assert public_tables[plugin_name]["allow"] == {"id": "plugin"}
    assert public_tables[local_name]["allow"] == {"id": "local"}


@pytest.mark.parametrize(
    "local_tables,expected",
    [
        ({}, {"id": "reader"}),
        ({"ITEMS": {"description": "Local"}}, {"id": "reader"}),
        ({"ITEMS": {"allow": None}}, None),
        ({"ITEMS": {"allow": {}}}, {"id": "reader"}),
        (None, None),
    ],
)
def test_legacy_nested_metadata_merge_is_preserved(
    ds, register_plugin, local_tables, expected
):
    class Metadata:
        @hookimpl
        def get_metadata(self):
            return {
                "databases": {
                    "catalog": {"tables": {"Items": {"allow": {"id": "reader"}}}}
                }
            }

    register_plugin(Metadata())
    ds._metadata_local = {"databases": {"catalog": {"tables": local_tables}}}
    assert ds._table_permission_allows("catalog", "Items") == (
        [expected] if expected is not None else []
    )


@pytest.mark.parametrize(
    "database_metadata,expected",
    [
        ({}, {"id": "fallback"}),
        ({"tables": {}}, None),
        ({"tables": None}, None),
        ({"tables": {"items": {"allow": {"id": "database"}}}}, {"id": "database"}),
    ],
)
def test_existing_instance_table_metadata_fallback(ds, database_metadata, expected):
    ds._metadata_local = {
        "tables": {"ITEMS": {"allow": {"id": "fallback"}}},
        "databases": {"catalog": database_metadata},
    }
    assert ds._table_permission_allows("catalog", "Items") == (
        [expected] if expected is not None else []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("source", ["local", "plugin"])
@pytest.mark.parametrize("requested", ["Items", "ITEMS"])
async def test_same_source_case_variant_rules_use_deny_precedence(
    ds, register_plugin, reverse, source, requested
):
    entries = [("Items", {"allow": {"id": "reader"}}), ("ITEMS", {"allow": {}})]
    if reverse:
        entries.reverse()
    metadata = {"databases": {"catalog": {"tables": dict(entries)}}}
    if source == "local":
        ds._metadata_local = metadata
    else:

        class Metadata:
            @hookimpl
            def get_metadata(self):
                return metadata

        register_plugin(Metadata())
    original = copy.deepcopy(metadata)
    assert (
        await ds.permission_allowed(
            {"id": "reader"}, "view-table", ("catalog", requested), default=None
        )
        is False
    )
    assert metadata == original


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_local_allow_overlays_same_source_plugin_aliases(
    ds, register_plugin, reverse
):
    entries = [
        ("Items", {"allow": {"id": "first"}}),
        ("ITEMS", {"allow": {"id": "second"}}),
    ]
    if reverse:
        entries.reverse()

    class Metadata:
        @hookimpl
        def get_metadata(self):
            return {"databases": {"catalog": {"tables": dict(entries)}}}

    register_plugin(Metadata())
    ds._metadata_local = {
        "databases": {"catalog": {"tables": {"items": {"allow": {"id": "local"}}}}}
    }
    assert ds._table_permission_allows("catalog", "Items") == [{"id": "local"}]
    assert (
        await ds.permission_allowed(
            {"id": "local"}, "view-table", ("catalog", "Items"), default=None
        )
        is True
    )


@pytest.mark.asyncio
async def test_plugin_permission_verdict_still_overrides_metadata(ds, register_plugin):
    ds._metadata_local = {
        "databases": {"catalog": {"tables": {"Items": {"allow": {"id": "reader"}}}}}
    }

    class Policy:
        @hookimpl
        def permission_allowed(self, action, resource):
            if action == "view-table":
                assert resource == ("catalog", "Items")
                return False

    register_plugin(Policy())
    assert (
        await ds.permission_allowed(
            {"id": "reader"}, "view-table", ("catalog", "Items")
        )
        is False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,resource",
    [
        ("view-database", "Catalog"),
        ("execute-sql", "Catalog"),
        ("no-match", ("catalog", "ITEMS")),
        ("view-table", ("missing", "ITEMS")),
        ("view-table", ("catalog", "FutureTable")),
    ],
)
async def test_other_and_hypothetical_resources_keep_identity(
    ds, register_plugin, action, resource
):
    calls = []

    class Observer:
        @hookimpl
        def permission_allowed(self, action, resource):
            calls.append((action, resource))

    register_plugin(Observer())
    assert await ds.permission_allowed(None, action, resource, default=True)
    assert calls == [(action, resource)]


@pytest.mark.asyncio
async def test_canned_query_names_remain_case_sensitive(ds, register_plugin):
    ds._metadata_local = {
        "databases": {
            "catalog": {
                "queries": {
                    "Report": {"sql": "select 1", "allow": {"id": "first"}},
                    "report": {"sql": "select 2", "allow": {"id": "second"}},
                }
            }
        }
    }
    for name, actor_id in (("Report", "first"), ("report", "second")):
        assert (
            await ds.permission_allowed(
                {"id": actor_id}, "view-query", ("catalog", name), default=None
            )
            is True
        )
        assert ds._permission_checks[-1]["resource"] == ("catalog", name)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/catalog",
        "/catalog/Items.json",
        "/catalog/Report.json",
        "/catalog/Items/1.json",
    ],
)
async def test_ordinary_requests_retain_table_permission_hooks(
    ds, register_plugin, path
):
    calls = []

    class Observer:
        @hookimpl
        def permission_allowed(self, action, resource):
            if action == "view-table":
                calls.append(resource)

    register_plugin(Observer())
    response = await ds.client.get(path)
    assert response.status_code == 200
    assert calls
    assert all(
        database == "catalog" and name in {"Items", "Report", "Ärea", "ärea"}
        for database, name in calls
    )
