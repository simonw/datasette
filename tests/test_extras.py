import asyncio

import pytest

from datasette.extras import Extra, ExtraRegistry, ExtraScope


class SlowValueExtra(Extra):
    description = "Returns context['value'], optionally slowly"
    scopes = {ExtraScope.TABLE}

    async def resolve(self, context):
        if context["slow"]:
            await asyncio.sleep(0.05)
        return context["value"]


class DependentExtra(Extra):
    description = "Depends on slow_value"
    scopes = {ExtraScope.TABLE}

    async def resolve(self, context, slow_value):
        return slow_value + 1


def test_registry_is_built_once_per_scope():
    registry = ExtraRegistry([SlowValueExtra, DependentExtra])
    first = registry._registry_for_scope(ExtraScope.TABLE)
    second = registry._registry_for_scope(ExtraScope.TABLE)
    assert first is second


@pytest.mark.asyncio
async def test_concurrent_resolves_do_not_share_state():
    # The asyncinject registry is shared across requests - resolved values
    # must not leak between concurrent resolve() calls with different contexts
    registry = ExtraRegistry([SlowValueExtra, DependentExtra])
    slow, fast = await asyncio.gather(
        registry.resolve(
            {"slow_value", "dependent"},
            {"value": 100, "slow": True},
            ExtraScope.TABLE,
        ),
        registry.resolve(
            {"slow_value", "dependent"},
            {"value": 200, "slow": False},
            ExtraScope.TABLE,
        ),
    )
    assert slow == {"slow_value": 100, "dependent": 101}
    assert fast == {"slow_value": 200, "dependent": 201}


@pytest.mark.asyncio
async def test_table_row_and_query_scopes_use_separate_registries():
    from datasette.views.table_extras import table_extra_registry

    registries = {
        scope: table_extra_registry._registry_for_scope(scope) for scope in ExtraScope
    }
    assert len(set(map(id, registries.values()))) == 3
    # Scope-specific extras only registered where they belong
    assert "count" in registries[ExtraScope.TABLE]._registry
    assert "count" not in registries[ExtraScope.QUERY]._registry
    assert "foreign_key_tables" in registries[ExtraScope.ROW]._registry
