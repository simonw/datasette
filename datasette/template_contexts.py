"""
The documented template context for Datasette's core HTML pages.

This module is the source of truth for the template context contract:
the set of variables that custom templates can rely on for each page.
It is consumed by the contract tests in tests/test_template_context.py,
which assert that the real rendered context for each page exactly
matches what is documented here, and by docs/template_context_doc.py
which generates the documentation in docs/template_context.rst.

Documentation for each key comes from one of three places:

- Pages that render a Context dataclass (database, query) use the
  ``help`` metadata on each dataclass field
- Keys provided by registered extras use the ``description`` from the
  Extra class
- Keys added inline by view code are documented in this module
"""

from dataclasses import dataclass

from datasette.extras import ExtraScope
from datasette.views.database import DatabaseContext, QueryContext
from datasette.views.table_extras import table_extra_registry


@dataclass(frozen=True)
class TemplateContextKey:
    name: str
    doc: str


def _keys(**docs):
    return tuple(TemplateContextKey(name, doc) for name, doc in docs.items())


# Added by Datasette.render_template() to the context for every page,
# including pages rendered by plugins
BASE_CONTEXT_KEYS = _keys(
    request="The current Request object, or None",
    crumb_items="Async function returning breadcrumb navigation items for the current page",
    urls="Object with methods for constructing URLs to pages within Datasette - see datasette.urls in the internals documentation",
    actor="The currently authenticated actor dictionary, or None",
    menu_links="Async function returning links for the Datasette application menu, including those added by plugins",
    display_actor="Function returning a display string for an actor dictionary",
    show_logout="True if the logout link should be shown in the navigation menu",
    app_css_hash="Hash of Datasette's app.css contents, used for cache busting",
    zip="Python's zip() builtin, made available to template logic",
    body_scripts="List of script blocks for the page body contributed by plugins",
    format_bytes="Function that formats a number of bytes as a human-readable size",
    show_messages="Function returning any messages set for the current user, clearing them in the process",
    extra_css_urls="List of {url, sri} dictionaries of extra CSS stylesheets to include on the page, from plugins and configuration",
    extra_js_urls="List of {url, sri, module} dictionaries of extra JavaScript URLs to include on the page",
    base_url="The configured base_url setting",
    csrftoken="Function returning the CSRF token for the current request",
    datasette_version="The version of Datasette that is running",
)


@dataclass(frozen=True)
class PageContext:
    # Identifier used in tests and documentation, e.g. "table"
    name: str
    title: str
    description: str
    # The default template used to render this page
    template: str
    # For pages rendered from a Context dataclass
    context_class: type = None
    # For pages whose context includes resolved extras
    extras_scope: ExtraScope = None
    extra_keys: tuple = ()
    # Keys added inline by the view code, documented here
    keys: tuple = ()

    def documented_keys(self):
        "Every page-specific documented key, excluding BASE_CONTEXT_KEYS"
        documented = []
        if self.context_class is not None:
            documented.extend(
                TemplateContextKey(f.name, f.help)
                for f in self.context_class.documented_fields()
            )
        for name in self.extra_keys:
            cls = table_extra_registry.classes_by_name[name]
            documented.append(TemplateContextKey(name, cls.description or ""))
        documented.extend(self.keys)
        return sorted(documented, key=lambda key: key.name)


_SHARED_DOCS = dict(
    ok="True if the data for this page was retrieved without errors",
    rows="The rows for this page, as a list of dictionaries mapping column name to value",
    query_ms="Time taken by the SQL queries for this page, in milliseconds",
    select_templates="List of template names that were considered for this page, the one used marked with an asterisk",
    settings="Dictionary of Datasette's current settings",
    alternate_url_json="URL for the JSON version of this page",
    url_csv="URL for the CSV export of this page",
    url_csv_path="Path portion of the CSV export URL",
    url_csv_hidden_args="(name, value) pairs for hidden form fields used by the CSV export form",
    renderers="Dictionary mapping output format names (e.g. json) to their URLs for this page",
    display_columns="Column objects formatted for the HTML table display",
    display_rows="Row data formatted for the HTML table display",
    custom_table_templates="Custom template names that were considered for displaying this table",
)


def _shared(*names):
    return tuple(TemplateContextKey(name, _SHARED_DOCS[name]) for name in names)


PAGES = {
    page.name: page
    for page in (
        PageContext(
            name="database",
            title="Database",
            description="The page listing the tables, views and queries in a database, e.g. /fixtures",
            template="database.html",
            context_class=DatabaseContext,
        ),
        PageContext(
            name="query",
            title="Query",
            description="The page for arbitrary SQL queries (/database/-/query?sql=...) and stored queries (/database/query-name)",
            template="query.html",
            context_class=QueryContext,
        ),
        PageContext(
            name="table",
            title="Table",
            description="The page showing the rows in a table or SQL view, e.g. /fixtures/facetable",
            template="table.html",
            extras_scope=ExtraScope.TABLE,
            extra_keys=(
                "actions",
                "all_columns",
                "columns",
                "count",
                "count_sql",
                "custom_table_templates",
                "database",
                "database_color",
                "display_columns",
                "display_rows",
                "expandable_columns",
                "facet_results",
                "facets_timed_out",
                "filters",
                "form_hidden_args",
                "human_description_en",
                "is_view",
                "metadata",
                "next_url",
                "primary_keys",
                "private",
                "query",
                "renderers",
                "set_column_type_ui",
                "sorted_facet_results",
                "suggested_facets",
                "table",
                "table_definition",
                "view_definition",
            ),
            keys=_shared(
                "ok",
                "rows",
                "query_ms",
                "select_templates",
                "settings",
                "alternate_url_json",
                "url_csv",
                "url_csv_path",
                "url_csv_hidden_args",
            )
            + _keys(
                allow_execute_sql="True if the current actor can execute custom SQL against this database",
                append_querystring="Function that appends additional querystring arguments to a URL",
                count_limit="The maximum number of rows Datasette will count before showing an approximation",
                datasette_allow_facet='The string "true" or "false" reflecting the allow_facet setting',
                extra_wheres_for_ui="Extra where clauses from ?_where=, with links to remove them",
                filter_columns="List of columns offered by the filter interface",
                fix_path="Function that applies the base_url prefix to a path",
                is_sortable="True if any of the displayed columns can be used to sort",
                next="Pagination token for the next page, or None",
                path_with_replaced_args="Function for building the current path with modified querystring arguments",
                sort="Column the page is sorted by, or None",
                sort_desc="Column the page is sorted by in descending order, or None",
                supports_search="True if this table has full-text search configured",
                top_table="Async function rendering the top_table plugin slot",
            ),
        ),
        PageContext(
            name="row",
            title="Row",
            description="The page showing an individual row, e.g. /fixtures/facetable/1",
            template="row.html",
            extras_scope=ExtraScope.ROW,
            extra_keys=(
                "columns",
                "database",
                "database_color",
                "foreign_key_tables",
                "metadata",
                "primary_keys",
                "private",
                "table",
            ),
            keys=_shared(
                "ok",
                "rows",
                "query_ms",
                "select_templates",
                "settings",
                "alternate_url_json",
                "url_csv",
                "url_csv_path",
                "url_csv_hidden_args",
                "renderers",
                "display_columns",
                "display_rows",
                "custom_table_templates",
            )
            + _keys(
                primary_key_values="Values of the primary keys for this row, from the URL",
                row_actions="Row actions made available by plugin hooks",
                top_row="Async function rendering the top_row plugin slot",
            ),
        ),
    )
}


def documented_context_keys(page_name):
    "Set of every documented key for the named page, including base context keys"
    page = PAGES[page_name]
    return {key.name for key in BASE_CONTEXT_KEYS} | {
        key.name for key in page.documented_keys()
    }
