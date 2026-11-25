"""
Tests for the documented template context - the contract that custom
template authors can rely on for Datasette 1.0.
"""

import html
import json
import pathlib
from dataclasses import dataclass, field

import pytest

from datasette.app import TEMPLATE_BASE_CONTEXT, Datasette
from datasette.extras import ExtraScope
from datasette.fixtures import write_fixture_database
from datasette.stored_queries import add_query
from datasette.template_contexts import PAGES, documented_context_keys
from datasette.views import Context


def test_documented_fields():
    @dataclass
    class DemoNested:
        name: str

    @dataclass
    class DemoContext(Context):
        name: str = field(metadata={"help": "The name"})
        _internal: str = field()
        count: int = field(metadata={"help": "How many there are"})
        items: list[DemoNested] = field(metadata={"help": "Nested items"})

    fields = DemoContext.documented_fields()
    assert [(f.name, f.type_name, f.help) for f in fields] == [
        ("name", "str", "The name"),
        ("count", "int", "How many there are"),
        ("items", "list[DemoNested]", "Nested items"),
    ]


def test_page_title():
    @dataclass
    class QueryListContext(Context):
        pass

    @dataclass
    class ApiExplorerContext(Context):
        documented_title = "API explorer"

    assert QueryListContext.page_title() == "Query list page"
    assert ApiExplorerContext.page_title() == "API explorer"
    assert [klass.page_title() for klass in list(PAGES.values())[:5]] == [
        "Index page",
        "Database page",
        "Query page",
        "Table page",
        "Row page",
    ]


@pytest.mark.parametrize("klass", PAGES.values(), ids=lambda klass: klass.__name__)
def test_context_class_fields_all_have_help(klass):
    for context_field in klass.documented_fields():
        assert (
            context_field.help
        ), f"{klass.__name__}.{context_field.name} is missing documentation"


@pytest.mark.parametrize("klass", PAGES.values(), ids=lambda klass: klass.__name__)
def test_context_class_has_docstring_and_documented_template(klass):
    assert klass.__doc__, f"{klass.__name__} is missing a docstring"
    assert (
        klass.documented_template
    ), f"{klass.__name__} is missing a documented_template"


def test_from_extra_documentation_comes_from_the_extra_class():
    from datasette.views import from_extra
    from datasette.views.table_extras import CountExtra

    @dataclass
    class DemoContext(Context):
        extras_scope = ExtraScope.TABLE

        count: int = from_extra()
        name: str = field(metadata={"help": "The name"})

    fields = {f.name: f for f in DemoContext.documented_fields()}
    assert fields["count"].help == CountExtra.description
    assert fields["count"].from_extra
    assert fields["name"].help == "The name"
    assert not fields["name"].from_extra


def test_from_extra_must_match_a_registered_extra():
    from datasette.views import from_extra

    @dataclass
    class BadContext(Context):
        extras_scope = ExtraScope.TABLE

        not_a_real_extra: str = from_extra()

    with pytest.raises(KeyError):
        BadContext.documented_fields()


def test_from_extra_must_be_available_for_the_scope():
    from datasette.views import from_extra

    @dataclass
    class WrongScopeContext(Context):
        extras_scope = ExtraScope.ROW

        # count is a TABLE-scope extra, not available for ROW
        count: int = from_extra()

    with pytest.raises(ValueError):
        WrongScopeContext.documented_fields()


@pytest.fixture
def isolate_extra_template_vars_plugins():
    # Datasette instances created with plugins_dir (e.g. the session-scoped
    # ds_client fixture) register their plugins on the global plugin manager
    # for the rest of the process. The contract documents plugin-free
    # Datasette core, so unregister any non-default plugin that adds
    # template variables via the extra_template_vars hook
    from datasette.plugins import DEFAULT_PLUGINS, pm

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
    # The pages that need permissions are requested as the root actor
    ds.root_enabled = True
    yield ds
    for db in ds.databases.values():
        if not db.is_memory:
            db.close()


async def get_template_context(ds, path, actor_id=None, expected_status=200):
    sep = "&" if "?" in path else "?"
    cookies = {}
    if actor_id:
        cookies["ds_actor"] = ds.client.actor_cookie({"id": actor_id})
    response = await ds.client.get(path + sep + "_context_json=1", cookies=cookies)
    assert response.status_code == expected_status, path
    body = html.unescape(response.text.removeprefix("<pre>").removesuffix("</pre>"))
    return json.loads(body)


@pytest.mark.parametrize(
    "page_name,path",
    (
        ("index", "/"),
        ("index", "/?q=facet"),
        ("database", "/fixtures"),
        ("table", "/fixtures/facetable"),
        ("table", "/fixtures/facetable?_city_id__exact=1"),
        ("row", "/fixtures/facetable/1"),
        ("query", "/fixtures/-/query?sql=select+*+from+facetable"),
        ("query", "/fixtures/neighborhood_search?text=cork"),
        ("query_list", "/-/queries"),
        ("query_list", "/fixtures/-/queries?q=search"),
        ("query_create", "/fixtures/-/queries/store as root"),
        (
            "query_create",
            "/fixtures/-/queries/store?sql=select+*+from+facetable+where+pk=:pk as root",
        ),
        ("query_edit", "/fixtures/saved_by_root/-/edit as root"),
        ("query_delete", "/fixtures/saved_by_root/-/delete as root"),
        ("execute_write", "/fixtures/-/execute-write as root"),
        (
            "execute_write",
            "/fixtures/-/execute-write?sql=delete+from+facetable+where+pk=:pk as root",
        ),
        ("schema", "/-/schema"),
        ("schema", "/fixtures/-/schema"),
        ("schema", "/fixtures/facetable/-/schema"),
        ("create_token", "/-/create-token as root"),
        ("api_explorer", "/-/api"),
        ("api_explorer", "/-/api as root"),
        ("logout", "/-/logout as root"),
        ("json_data", "/-/versions"),
        ("json_data", "/-/settings"),
        ("json_data", "/-/actions as root"),
        ("autocomplete_debug", "/-/debug/autocomplete"),
        (
            "autocomplete_debug",
            "/-/debug/autocomplete?database=fixtures&table=facetable",
        ),
        ("permissions_debug", "/-/permissions as root"),
        ("allowed_resources", "/-/allowed"),
        ("allowed_resources", "/-/allowed as root"),
        ("permission_rules", "/-/rules as root"),
        ("permission_check", "/-/check as root"),
        ("allow_debug", "/-/allow-debug"),
        ("allow_debug", "/-/allow-debug?actor=nope"),
        ("messages_debug", "/-/messages"),
        ("error", "/-/permissions"),
    ),
)
async def test_template_context_matches_documented_contract(
    context_ds, isolate_extra_template_vars_plugins, page_name, path
):
    # The full contract: every key in the rendered template context is
    # documented, and every documented key is present in the context.
    # A path can be followed by " as <actor_id>" to sign in as that actor
    path, _, actor_id = path.partition(" as ")
    if "saved_by_root" in path:
        # A query saved by a user, which can be edited and deleted
        await context_ds.invoke_startup()
        await add_query(
            context_ds,
            "fixtures",
            "saved_by_root",
            "select * from facetable where pk = :pk",
            source="user",
            owner_id="root",
        )
    documented = documented_context_keys(page_name)
    actual = {
        key
        for key in await get_template_context(
            context_ds,
            path,
            actor_id,
            # The error page is tested using a page that denies permission
            expected_status=403 if page_name == "error" else 200,
        )
        if not key.startswith("_")
    }
    undocumented = actual - documented
    no_longer_present = documented - actual
    assert not undocumented, (
        f"Undocumented keys in {page_name} template context: {sorted(undocumented)} - add them to the "
        "page's Context class"
    )
    assert not no_longer_present, (
        f"Documented keys missing from {page_name} template context: {sorted(no_longer_present)} - this would "
        "break custom templates"
    )


async def test_csrf_error_context_matches_documented_contract(context_ds):
    # This page is rendered without a request, so ?_context_json=1 cannot
    # be used to inspect it
    await context_ds.invoke_startup()
    rendered = {}

    async def render_template(templates, context=None, **kwargs):
        rendered["templates"] = templates
        rendered["context"] = context
        return ""

    original = context_ds.render_template
    context_ds.render_template = render_template
    try:
        response = await context_ds.client.post(
            "/-/messages",
            data={"message": "hi"},
            headers={"origin": "https://evil.example.com"},
        )
    finally:
        context_ds.render_template = original
    assert response.status_code == 403
    assert rendered["templates"] == PAGES["csrf_error"].documented_template
    assert isinstance(rendered["context"], PAGES["csrf_error"])
    assert "origin" in rendered["context"].reason.lower()


def test_debugging_tools_are_not_part_of_the_stable_contract():
    assert {key for key, klass in PAGES.items() if not klass.stable} == {
        "autocomplete_debug",
        "permissions_debug",
        "allowed_resources",
        "permission_rules",
        "permission_check",
        "allow_debug",
        "messages_debug",
    }
    # They are documented in their own section, after every stable page
    docs = (
        pathlib.Path(__file__).parent.parent / "docs" / "template_context.rst"
    ).read_text()
    stable_docs, debugging_docs = docs.split("\nDebugging tools\n")
    assert "not** part of the stable contract" in debugging_docs
    for klass in PAGES.values():
        heading = f"\n{klass.page_title()}\n"
        assert (heading in stable_docs) is klass.stable, klass.__name__
        assert (heading in debugging_docs) is not klass.stable, klass.__name__


def test_base_context_keys_all_have_docs():
    for name, doc in TEMPLATE_BASE_CONTEXT.items():
        assert doc, f"Base context key {name} is missing docs"


def test_template_context_docs_cover_every_documented_key():
    docs_path = pathlib.Path(__file__).parent.parent / "docs" / "template_context.rst"
    assert docs_path.exists(), "docs/template_context.rst is missing"
    docs = docs_path.read_text()
    for name in TEMPLATE_BASE_CONTEXT:
        assert f"``{name}``" in docs, name
    for page_name, klass in PAGES.items():
        title = klass.page_title()
        assert title in docs, title
        for context_field in klass.documented_fields():
            assert (
                f"``{context_field.name}``" in docs
            ), f"{context_field.name} ({page_name} page)"
            assert (
                f"``{context_field.name}`` - ``{context_field.type_name}``" in docs
            ), f"{context_field.name} type ({page_name} page)"


# Documented variables that none of Datasette's default templates use.
# Everything documented is frozen until Datasette 2.0, so a variable should
# only be listed here on purpose: because custom templates or plugins need
# it. See https://github.com/simonw/datasette/issues/3088
UNUSED_BY_DEFAULT_TEMPLATES = {
    # url_csv: custom templates based on older default templates link to it
    "query": {"url_csv"},
    # columns: passed to the extra_body_script, extra_template_vars,
    # extra_css_urls and extra_js_urls plugin hooks
    # rows: the raw values, display_rows only has the rendered cells
    # next: the pagination token, for templates that build their own links
    # count_sql: matches ?_extra=count_sql
    "table": {"columns", "count_sql", "next", "rows", "url_csv"},
    "row": {"columns", "rows", "url_csv"},
    "query_list": {"next"},
}
# base_url: used by templates in plugins
UNUSED_BASE_CONTEXT = {"base_url"}

# Templates that jinja2.meta cannot discover from the documented template:
# {% include custom_table_templates %} and views sharing a Context class
ADDITIONAL_TEMPLATES = {
    "table": ["_table.html"],
    "row": ["_row_fields.html"],
    "json_data": ["debug_actions.html"],
}


def _variables_used_by(environment, template_name, seen=None):
    "Undeclared variables in a template and every template it references"
    from jinja2 import meta

    seen = set() if seen is None else seen
    if template_name in seen:
        return set()
    seen.add(template_name)
    source = environment.loader.get_source(environment, template_name)[0]
    ast = environment.parse(source)
    used = set(meta.find_undeclared_variables(ast))
    for referenced in meta.find_referenced_templates(ast):
        if referenced is not None:
            used |= _variables_used_by(environment, referenced, seen)
    return used


def _variables_used_by_page(environment, page_name):
    templates = [PAGES[page_name].documented_template] + ADDITIONAL_TEMPLATES.get(
        page_name, []
    )
    used = set()
    for template_name in templates:
        used |= _variables_used_by(environment, template_name)
    return used


@pytest.mark.parametrize("page_name", PAGES)
def test_documented_variables_are_used_by_default_templates(page_name):
    environment = Datasette(memory=True).get_jinja_environment()
    used = _variables_used_by_page(environment, page_name)
    documented = {f.name for f in PAGES[page_name].documented_fields()}
    assert documented - used == UNUSED_BY_DEFAULT_TEMPLATES.get(page_name, set()), (
        f"{page_name}: variables documented but not used by any default template "
        "must match UNUSED_BY_DEFAULT_TEMPLATES - remove the variable, or list it "
        "there if it is kept on purpose"
    )


def test_base_context_variables_are_used_by_default_templates():
    environment = Datasette(memory=True).get_jinja_environment()
    used = set()
    for page_name in PAGES:
        used |= _variables_used_by_page(environment, page_name)
    assert set(TEMPLATE_BASE_CONTEXT) - used == UNUSED_BASE_CONTEXT
