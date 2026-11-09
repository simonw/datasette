.. _database_errors:

Database execution errors
=========================

The :ref:`database_execute` and :ref:`database_execute_write` methods raise exceptions when SQL cannot be executed. Plugin code can catch these exceptions around the ``await`` expression.

SQLite errors
-------------

Import exception classes from Datasette's selected SQLite backend, rather than directly from Python's ``sqlite3`` module. Datasette may use ``pysqlite3`` instead of the standard library module::

    from datasette.utils.sqlite import sqlite3

Common errors include:

* ``sqlite3.OperationalError`` for invalid SQL or a missing table or column.
* ``sqlite3.IntegrityError`` for a write that violates a constraint, such as a duplicate primary key.
* ``sqlite3.ProgrammingError`` for an incorrect number of bound parameters.

These are all subclasses of ``sqlite3.DatabaseError``. Catch a specific subclass when you need to distinguish the cause, or ``sqlite3.DatabaseError`` to handle database errors together. These examples are not an exhaustive list of exceptions that can propagate from the database API or plugin callbacks.

The :ref:`database_execute_write_many` and :ref:`database_execute_write_script` methods also propagate SQLite errors. For writes, use the default ``block=True`` if the caller needs to catch execution errors: with ``block=False`` the write is queued and awaiting the method does not wait for its execution to finish.

Interrupted queries
-------------------

``datasette.database.QueryInterrupted`` is raised when a query executed by ``db.execute()`` exceeds its time limit. The limit comes from :ref:`setting_sql_time_limit_ms`; ``custom_time_limit`` can reduce it for an individual query. ``db.execute_write()`` also raises ``QueryInterrupted`` when its ``time_limit_ms`` is exceeded.

``QueryInterrupted`` is **not** a subclass of ``sqlite3.DatabaseError``, so catch it separately. It exposes ``sql`` and ``params`` for the interrupted statement and ``e`` for the underlying SQLite exception.

For example, to handle interruptions separately from other database errors::

    from datasette.database import QueryInterrupted
    from datasette.utils.sqlite import sqlite3

    try:
        results = await db.execute(
            "select * from items where id = :id",
            {"id": item_id},
            custom_time_limit=100,
        )
    except QueryInterrupted:
        # The query was interrupted, for example by its time limit.
        ...
    except sqlite3.DatabaseError:
        # SQL execution failed for another database-related reason.
        ...
    else:
        rows = results.rows

``log_sql_errors=False`` on ``db.execute()`` suppresses console logging of SQL errors, not the exceptions themselves. Catching an exception does not retry the query or turn it into an empty result.
