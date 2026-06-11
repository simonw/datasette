"""
Tests for the documented template context - the contract that custom
template authors can rely on for Datasette 1.0.
"""

import html
import json
import pathlib
from dataclasses import dataclass, field

import pytest

from datasette.app import Datasette
from datasette.extras import ExtraScope
from datasette.fixtures import write_fixture_database
from datasette.template_contexts import (
    BASE_CONTEXT_KEYS,
    PAGES,
    documented_context_keys,
)
from datasette.views import Context
from datasette.views.database import DatabaseContext, QueryContext
from datasette.views.table_extras import table_extra_registry


def test_documented_fields():
    @dataclass
    class DemoContext(Context):
        name: str = field(metadata={"help": "The name"})
        count: int = field(metadata={"help": "How many there are"})

    fields = DemoContext.documented_fields()
    assert [(f.name, f.type_name, f.help) for f in fields] == [
        ("name", "str", "The name"),
        ("count", "int", "How many there are"),
    ]


@pytest.mark.parametrize("klass", (DatabaseContext, QueryContext))
def test_context_dataclass_fields_all_have_help(klass):
    for context_field in klass.documented_fields():
        assert context_field.help, "{}.{} is missing help metadata".format(
            klass.__name__, context_field.name
        )


def test_extra_field_documentation_comes_from_the_extra_class():
    from datasette.views import extra_field
    from datasette.views.table_extras import CountExtra

    @dataclass
    class DemoContext(Context):
        extras_scope = ExtraScope.TABLE

        count: int = extra_field()
        name: str = field(metadata={"help": "The name"})

    fields = {f.name: f for f in DemoContext.documented_fields()}
    assert fields["count"].help == CountExtra.description
    assert fields["count"].from_extra
    assert fields["name"].help == "The name"
    assert not fields["name"].from_extra


def test_extra_field_must_match_a_registered_extra():
    from datasette.views import extra_field

    @dataclass
    class BadContext(Context):
        extras_scope = ExtraScope.TABLE

        not_a_real_extra: str = extra_field()

    with pytest.raises(KeyError):
        BadContext.documented_fields()


def test_extra_field_must_be_available_for_the_scope():
    from datasette.views import extra_field

    @dataclass
    class WrongScopeContext(Context):
        extras_scope = ExtraScope.ROW

        # count is a TABLE-scope extra, not available for ROW
        count: int = extra_field()

    with pytest.raises(ValueError):
        WrongScopeContext.documented_fields()


@pytest.fixture
def isolate_extra_template_vars_plugins():
    # Datasette instances created with plugins_dir (e.g. the session-scoped
    # ds_client fixture) register their plugins on the global plugin manager
    # for the rest of the process. The contract documents plugin-free
    # Datasette core, so unregister any non-default plugin that adds
    # template variables via the extra_template_vars hook
    from datasette.plugins import pm, DEFAULT_PLUGINS

    hook_plugins = {impl.plugin for impl in pm.hook.extra_template_vars.get_hookimpls()}
    removed = []
    for plugin in list(pm.get_plugins()):
        name = pm.get_name(plugin)
        if name not in DEFAULT_PLUGINS and plugin in hook_plugins:
            pm.unregister(plugin)
            removed.append((plugin, name))
    yield
    for plugin, name in removed:
        pm.register(plugin, name)


@pytest.fixture(scope="module")
def context_ds(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("template-context") / "fixtures.db"
    write_fixture_database(db_path)
    ds = Datasette(
        [str(db_path)],
        settings={"num_sql_threads": 1, "template_debug": True},
        config={
            "databases": {
                "fixtures": {
                    "queries": {
                        "neighborhood_search": {
                            "sql": (
                                "select _neighborhood from facetable "
                                "where _neighborhood like '%' || :text || '%'"
                            ),
                            "title": "Search neighborhoods",
                        }
                    }
                }
            }
        },
    )
    yield ds
    for db in ds.databases.values():
        if not db.is_memory:
            db.close()


async def get_template_context(ds, path):
    sep = "&" if "?" in path else "?"
    response = await ds.client.get(path + sep + "_context=1")
    assert response.status_code == 200, path
    body = html.unescape(response.text.removeprefix("<pre>").removesuffix("</pre>"))
    return json.loads(body)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "page_name,path",
    (
        ("database", "/fixtures"),
        ("table", "/fixtures/facetable"),
        ("table", "/fixtures/facetable?_city_id__exact=1"),
        ("row", "/fixtures/facetable/1"),
        ("query", "/fixtures/-/query?sql=select+*+from+facetable"),
        ("query", "/fixtures/neighborhood_search?text=cork"),
    ),
)
async def test_template_context_matches_documented_contract(
    context_ds, isolate_extra_template_vars_plugins, page_name, path
):
    # The full contract: every key in the rendered template context is
    # documented, and every documented key is present in the context
    documented = documented_context_keys(page_name)
    actual = set(await get_template_context(context_ds, path))
    undocumented = actual - documented
    no_longer_present = documented - actual
    assert not undocumented, (
        "Undocumented keys in {} template context: {} - document them in "
        "datasette/template_contexts.py".format(page_name, sorted(undocumented))
    )
    assert not no_longer_present, (
        "Documented keys missing from {} template context: {} - this would "
        "break custom templates".format(page_name, sorted(no_longer_present))
    )


def test_base_context_keys_all_have_docs():
    for key in BASE_CONTEXT_KEYS:
        assert key.doc, "Base context key {} is missing docs".format(key.name)


@pytest.mark.parametrize("page", PAGES.values(), ids=lambda page: page.name)
def test_page_documented_keys_all_have_docs(page):
    for key in page.documented_keys():
        assert key.doc, "{} page key {} is missing docs".format(page.name, key.name)


def test_template_context_docs_cover_every_documented_key():
    docs_path = pathlib.Path(__file__).parent.parent / "docs" / "template_context.rst"
    assert docs_path.exists(), "docs/template_context.rst is missing"
    docs = docs_path.read_text()
    for key in BASE_CONTEXT_KEYS:
        assert "``{}``".format(key.name) in docs, key.name
    for page in PAGES.values():
        assert page.title in docs, page.title
        for key in page.documented_keys():
            assert "``{}``".format(key.name) in docs, "{} ({} page)".format(
                key.name, page.name
            )


@pytest.mark.parametrize("page", PAGES.values(), ids=lambda page: page.name)
def test_page_extra_keys_are_registered_extras(page):
    for name in page.extra_keys:
        cls = table_extra_registry.classes_by_name.get(name)
        assert cls is not None, "{} is not a registered extra".format(name)
        assert page.extras_scope is not None
        assert cls.available_for(
            page.extras_scope
        ), "{} extra is not available for scope {}".format(name, page.extras_scope)
