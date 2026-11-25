"""
SQL parameters returned by permission_resources_sql() plugins must not be
able to collide with parameters used by Datasette itself, or with the
parameters of other plugins.
"""

import pytest

from datasette import hookimpl
from datasette.app import Datasette
from datasette.permissions import PermissionSQL
from datasette.plugins import pm
from datasette.resources import DatabaseResource, TableResource


class RulesPlugin:
    def __init__(self, name, rules):
        self.__name__ = name
        self.rules = rules

    @hookimpl
    def permission_resources_sql(self, datasette, actor, action):
        return self.rules(actor, action)


@pytest.fixture
def register():
    registered = []

    def _register(name, rules):
        plugin = RulesPlugin(name, rules)
        pm.register(plugin, name=name)
        registered.append(name)

    yield _register
    for name in registered:
        pm.unregister(name=name)


async def make_ds(name):
    ds = Datasette(default_deny=True)
    for database, table in (("pub", "pub_table"), ("secret", "secret_table")):
        db = ds.add_memory_database(f"{name}_{database}", name=database)
        await db.execute_write(
            f"create table if not exists {table} (id integer primary key, v text)"
        )
        await db.execute_write(f"insert or replace into {table} values (1, 'one')")
    await ds.invoke_startup()
    return ds


def view_everything_but_tables(actor, action):
    if action in ("view-instance", "view-database"):
        return PermissionSQL.allow("public instance")


TABLE_IN_NAMED_DATABASE = (
    "select :{param} as parent, null as child, 1 as allow, 'pub only' as reason"
)
NOTHING_UNLESS_BIG = (
    "select null as parent, null as child, 1 as allow, 'big' as reason "
    "where :{param} > 100"
)


@pytest.mark.parametrize(
    "param,sql,value,paths",
    (
        # The control: a name Datasette does not use itself
        (
            "myplugin_db",
            TABLE_IN_NAMED_DATABASE,
            "pub",
            ("/", "/?q=secret", "/.json", "/secret", "/secret.json"),
        ),
        # The homepage binds :database, :q, :pattern and :allowed_dbs
        ("database", TABLE_IN_NAMED_DATABASE, "pub", ("/", "/?q=secret", "/.json")),
        ("q", TABLE_IN_NAMED_DATABASE, "pub", ("/?q=secret", "/.json?q=secret")),
        ("pattern", TABLE_IN_NAMED_DATABASE, "pub", ("/?q=secret",)),
        ("allowed_dbs", TABLE_IN_NAMED_DATABASE, "pub", ("/", "/?q=secret")),
        # Datasette.allowed_resources() binds :limit for pagination
        ("limit", NOTHING_UNLESS_BIG, 1, ("/secret", "/secret.json", "/.json")),
    ),
)
async def test_plugin_param_cannot_be_overwritten_by_core(
    register, param, sql, value, paths
):
    def rules(actor, action):
        if action == "view-table":
            return PermissionSQL(sql=sql.format(param=param), params={param: value})
        return view_everything_but_tables(actor, action)

    register("test_param_collision", rules)
    ds = await make_ds(f"collide_{param}")
    # The private table is private, however it is asked about
    response = await ds.client.get("/secret/secret_table.json")
    assert response.status_code == 403
    for path in paths:
        response = await ds.client.get(path)
        assert response.status_code == 200, path
        assert "secret_table" not in response.text, path
    if sql is TABLE_IN_NAMED_DATABASE:
        # ... and the rule still allows what it is meant to allow
        assert "pub_table" in (await ds.client.get("/")).text
        assert (await ds.client.get("/pub/pub_table.json")).status_code == 200


async def test_two_plugins_can_use_the_same_param_name(register):
    def rules_for(database):
        def rules(actor, action):
            if action == "view-table":
                return PermissionSQL(
                    sql=TABLE_IN_NAMED_DATABASE.format(param="db"),
                    params={"db": database},
                )
            return view_everything_but_tables(actor, action)

        return rules

    register("test_same_param_one", rules_for("pub"))
    register("test_same_param_two", rules_for("secret"))
    ds = await make_ds("same_param")
    # Listing every allowed table
    page = await ds.allowed_resources("view-table", None)
    assert {(r.parent, r.child) for r in page.resources} == {
        ("pub", "pub_table"),
        ("secret", "secret_table"),
    }
    # Checking one table at a time
    for database, table in (("pub", "pub_table"), ("secret", "secret_table")):
        assert await ds.allowed(
            action="view-table", resource=TableResource(database, table), actor=None
        )
    assert "pub_table" in (await ds.client.get("/")).text
    assert "secret_table" in (await ds.client.get("/")).text


async def test_reserved_params_without_a_params_dictionary(register):
    def rules(actor, action):
        if action == "view-table":
            # No params= at all: :actor_id and :action are still available
            return PermissionSQL(
                sql=(
                    "select 'pub' as parent, null as child, 1 as allow, 'alice' as reason "
                    "where :actor_id = 'alice' and :action = 'view-table'"
                )
            )
        return view_everything_but_tables(actor, action)

    register("test_reserved_params", rules)
    ds = await make_ds("reserved_params")
    resource = TableResource("pub", "pub_table")
    assert await ds.allowed(
        action="view-table", resource=resource, actor={"id": "alice"}
    )
    assert not await ds.allowed(
        action="view-table", resource=resource, actor={"id": "bob"}
    )
    page = await ds.allowed_resources("view-table", {"id": "alice"})
    assert [(r.parent, r.child) for r in page.resources] == [("pub", "pub_table")]
    page = await ds.allowed_resources("view-table", {"id": "bob"})
    assert page.resources == []


async def test_action_param_with_also_requires(register):
    # execute-sql also requires view-database. Each action's rules must
    # see their own :action, including when the two are combined to list
    # every database the actor can run SQL against.
    def rules(actor, action):
        if action == "view-instance":
            return PermissionSQL.allow("public instance")
        if action in ("view-database", "execute-sql"):
            return PermissionSQL(
                sql=(
                    "select 'pub' as parent, null as child, 1 as allow, "
                    "'action is ' || :action as reason "
                    "where :action = :expected"
                ),
                params={"expected": action},
            )

    register("test_action_param", rules)
    ds = await make_ds("action_param")
    assert await ds.allowed(
        action="execute-sql", resource=DatabaseResource("pub"), actor=None
    )
    page = await ds.allowed_resources("execute-sql", None)
    assert [r.parent for r in page.resources] == ["pub"]


def test_namespace_permission_sql_params():
    from datasette.utils.permissions import namespace_permission_sql_params

    original = PermissionSQL(
        sql="select :actor_id, :actor, :a, :ab, ':actor' || :actor_idx",
        restriction_sql="select :a as parent, :ab as child",
        params={"actor": "A", "actor_id": "B", "a": 1, "ab": 2},
        source="plugin",
    )
    renamed = namespace_permission_sql_params(original, "p_")
    # :actor is not renamed inside :actor_id, and :actor_idx is not a param
    assert renamed.sql == (
        "select :p_actor_id, :p_actor, :p_a, :p_ab, ':p_actor' || :actor_idx"
    )
    assert renamed.restriction_sql == "select :p_a as parent, :p_ab as child"
    assert renamed.params == {"p_actor": "A", "p_actor_id": "B", "p_a": 1, "p_ab": 2}
    assert renamed.source == "plugin"
    # The original is left alone, so a plugin can return the same object twice
    assert original.sql == "select :actor_id, :actor, :a, :ab, ':actor' || :actor_idx"
    assert original.params == {"actor": "A", "actor_id": "B", "a": 1, "ab": 2}
    # Renaming twice gives each name both prefixes, once
    twice = namespace_permission_sql_params(renamed, "anon_")
    assert twice.sql.startswith("select :anon_p_actor_id, :anon_p_actor,")
    # Nothing to rename
    restriction_only = namespace_permission_sql_params(
        PermissionSQL(restriction_sql="select 'pub' as parent, null as child"), "p_"
    )
    assert restriction_only.sql is None
    assert restriction_only.params == {}


async def test_plugin_can_return_the_same_permission_sql_object_every_time(register):
    shared = PermissionSQL(
        sql="select :db as parent, null as child, 1 as allow, 'pub only' as reason",
        params={"db": "pub"},
    )

    def rules(actor, action):
        if action == "view-table":
            return shared
        return view_everything_but_tables(actor, action)

    register("test_shared_object", rules)
    ds = await make_ds("shared_object")
    for _ in range(3):
        assert (await ds.client.get("/pub/pub_table.json")).status_code == 200
        assert (await ds.client.get("/secret/secret_table.json")).status_code == 403
        assert "secret_table" not in (await ds.client.get("/")).text
    assert shared.params == {"db": "pub"}
    assert ":db " in shared.sql


async def test_permission_check_explanation_still_works(register):
    def rules(actor, action):
        if action == "view-table":
            return PermissionSQL(
                sql=TABLE_IN_NAMED_DATABASE.format(param="limit"),
                params={"limit": "pub"},
            )
        return view_everything_but_tables(actor, action)

    register("test_explain", rules)
    ds = await make_ds("explain")
    ds.root_enabled = True
    for table, expected in (("pub/pub_table", True), ("secret/secret_table", False)):
        database, name = table.split("/")
        response = await ds.client.get(
            "/-/check.json",
            params={
                "action": "view-table",
                "parent": database,
                "child": name,
                # Explain the decision for another actor, not for root
                "actor": '{"id": "alice"}',
            },
            actor={"id": "root"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["allowed"] is expected
    # The rules debug page pages through the same rules with :limit and :offset
    response = await ds.client.get(
        "/-/rules.json", params={"action": "view-table"}, actor={"id": "root"}
    )
    assert response.status_code == 200, response.text
    assert "pub only" in response.text
