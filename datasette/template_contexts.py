"""
Index of the documented template contexts for Datasette's core HTML pages.

This module deliberately contains no documentation strings of its own -
the documentation lives next to the code it describes:

- Every page renders a Context dataclass defined in its view module,
  for example DatabaseContext and QueryContext in views/database.py and
  TableContext in views/table.py. Fields added by view code
  carry ``help`` metadata; fields declared with from_extra() take their
  documentation from the description on the matching Extra class in
  views/table_extras.py.
- The keys render_template() adds to every page are documented in
  TEMPLATE_BASE_CONTEXT in datasette/app.py, next to the code that adds
  them.

The contract tests in tests/test_template_context.py assert that the real
rendered context for each page exactly matches what is documented, and
docs/template_context_doc.py generates docs/template_context.rst from the
same classes.
"""

from datasette.app import TEMPLATE_BASE_CONTEXT
from datasette.views.base import ErrorContext
from datasette.views.database import DatabaseContext, QueryContext
from datasette.views.execute_write import ExecuteWriteContext
from datasette.views.index import IndexContext
from datasette.views.row import RowContext
from datasette.views.special import (
    AllowDebugContext,
    AllowedResourcesContext,
    ApiExplorerContext,
    AutocompleteDebugContext,
    CreateTokenContext,
    JsonDataContext,
    LogoutContext,
    MessagesDebugContext,
    PermissionCheckContext,
    PermissionRulesContext,
    PermissionsDebugContext,
    SchemaContext,
)
from datasette.views.stored_queries import (
    QueryCreateContext,
    QueryDeleteContext,
    QueryEditContext,
    QueryListContext,
)
from datasette.views.table import TableContext

PAGES = {
    "index": IndexContext,
    "database": DatabaseContext,
    "query": QueryContext,
    "table": TableContext,
    "row": RowContext,
    "query_list": QueryListContext,
    "query_create": QueryCreateContext,
    "query_edit": QueryEditContext,
    "query_delete": QueryDeleteContext,
    "execute_write": ExecuteWriteContext,
    "schema": SchemaContext,
    "create_token": CreateTokenContext,
    "api_explorer": ApiExplorerContext,
    "logout": LogoutContext,
    "json_data": JsonDataContext,
    "autocomplete_debug": AutocompleteDebugContext,
    "permissions_debug": PermissionsDebugContext,
    "allowed_resources": AllowedResourcesContext,
    "permission_rules": PermissionRulesContext,
    "permission_check": PermissionCheckContext,
    "allow_debug": AllowDebugContext,
    "messages_debug": MessagesDebugContext,
    "error": ErrorContext,
}


def documented_context_keys(page_name):
    "Set of every documented key for the named page, including base context keys"
    return set(TEMPLATE_BASE_CONTEXT) | {
        f.name for f in PAGES[page_name].documented_fields()
    }
