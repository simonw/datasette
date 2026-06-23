"""
Index of the documented template contexts for Datasette's core HTML pages.

This module deliberately contains no documentation strings of its own -
the documentation lives next to the code it describes:

- Every page renders a Context dataclass defined in its view module
  (DatabaseContext, QueryContext in views/database.py, TableContext in
  views/table.py, RowContext in views/row.py). Fields added by view code
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
from datasette.views.database import DatabaseContext, QueryContext
from datasette.views.row import RowContext
from datasette.views.table import TableContext

PAGES = {
    "database": DatabaseContext,
    "query": QueryContext,
    "table": TableContext,
    "row": RowContext,
}


def documented_context_keys(page_name):
    "Set of every documented key for the named page, including base context keys"
    return set(TEMPLATE_BASE_CONTEXT) | {
        f.name for f in PAGES[page_name].documented_fields()
    }
