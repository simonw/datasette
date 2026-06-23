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


class InternalOnlyExtra(Extra):
    description = "Internal extra for HTML templates only"
    scopes = {ExtraScope.TABLE}
    public = False

    async def resolve(self, context):
        return "internal"


def test_internal_classes_for_scope():
    registry = ExtraRegistry([SlowValueExtra, DependentExtra, InternalOnlyExtra])
    assert registry.internal_classes_for_scope(ExtraScope.TABLE) == [InternalOnlyExtra]
    assert registry.public_classes_for_scope(ExtraScope.TABLE) == [
        SlowValueExtra,
        DependentExtra,
    ]


def _registered_extra_classes():
    # Plain Providers are internal dependency plumbing, only Extra
    # subclasses surface as documented JSON/template keys
    from datasette.views.table_extras import table_extra_registry

    return [cls for cls in table_extra_registry.classes if issubclass(cls, Extra)]


@pytest.mark.parametrize("cls", _registered_extra_classes(), ids=lambda cls: cls.key())
def test_registered_extras_have_descriptions(cls):
    # Every registered extra is part of the documented template/JSON contract
    assert cls.description, "{} is missing a description".format(cls.__name__)


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
