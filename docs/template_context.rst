.. _template_context:

Template context
================

This page describes the variables made available to templates used by Datasette to render different pages of the application.


.. [[[cog
    from datasette.context import rst_docs_for_dataclass, Table
    cog.out(rst_docs_for_dataclass(Table))
.. ]]]
Table
-----

A table is a useful thing

Fields
~~~~~~

:name - ``str``: The name of the table
:columns - ``List[str]``: List of column names in the table
:primary_keys - ``List[str]``: List of column names that are primary keys
:count - ``int``: Number of rows in the table
:hidden - ``bool``: Should this table default to being hidden in the main database UI?
:fts_table - ``Optional[str]``: If this table has FTS support, the accompanying FTS table name
:foreign_keys - ``ForeignKey``: List of foreign keys for this table
:private - ``bool``: Private tables are not visible to signed-out anonymous users
.. [[[end]]]
