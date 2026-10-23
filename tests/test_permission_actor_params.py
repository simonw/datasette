import pytest
import pytest_asyncio

from datasette import hookimpl
from datasette.app import Datasette
from datasette.permissions import PermissionSQL
from datasette.utils.permissions import gather_permission_sql_from_hooks

# A rule that depends on who is asking, and which passes no params of its own
ALICE_ONLY_RULE = """
select 'data' as parent, 'secret' as child,
       case when json_extract(:actor, '$.id') = 'alice' then 1 else 0 end as allow,
       'alice only' as reason
"""


class ActorAwarePlugin:
    @hookimpl
    def permission_resources_sql(self, datasette, actor, action):
        if action == "view-table":
            return PermissionSQL(sql=ALICE_ONLY_RULE)


@pytest_asyncio.fixture
async def ds_with_actor_aware_rule():
    ds = Datasette(memory=True)
    ds.pm.register(ActorAwarePlugin(), name="actor-aware")
    await ds.invoke_startup()
    db = ds.add_memory_database("data")
    await db.execute_write("create table secret (id integer primary key)")
    await db.execute_write("create table open (id integer primary key)")
    await ds._refresh_schemas()
    try:
        yield ds
    finally:
        ds.pm.unregister(name="actor-aware")


@pytest.mark.asyncio
async def test_gather_permission_sql_from_hooks_attaches_defaults(
    ds_with_actor_aware_rule,
):
    "actor, actor_id and action should be available to rules that pass no params"
    permission_sqls = await gather_permission_sql_from_hooks(
        datasette=ds_with_actor_aware_rule,
        actor={"id": "alice"},
        action="view-table",
    )
    ours = [p for p in permission_sqls if p.sql == ALICE_ONLY_RULE]
    assert len(ours) == 1
    assert ours[0].params == {
        "action": "view-table",
        "actor": '{"id": "alice"}',
        "actor_id": "alice",
    }


@pytest.mark.asyncio
async def test_anonymous_rules_are_evaluated_as_the_anonymous_actor(
    ds_with_actor_aware_rule,
):
    "Rules that reference :actor must see NULL in the anonymous pass, not the current actor"
    ds = ds_with_actor_aware_rule
    resources = await ds.allowed_resources_sql(
        action="view-table", actor={"id": "alice"}, include_is_private=True
    )
    # The anonymous pass renames :actor to :anon_actor so it cannot collide with
    # the parameter used for the current actor
    assert resources.params["anon_actor"] is None
    assert resources.params["anon_action"] == "view-table"

    rows = await ds.get_internal_database().execute(resources.sql, resources.params)
    by_child = {row["child"]: dict(row) for row in rows}
    # alice can view secret, anonymous cannot - so it is a private resource
    assert by_child["secret"]["is_private"] == 1
    # open is visible to everyone
    assert by_child["open"]["is_private"] == 0
