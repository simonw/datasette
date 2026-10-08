"""Schema facts read from the ``_internal`` catalog instead of the databases.

Pages that summarise many databases at once (the index page, permission
listings) must not open every database: with a capped connection pool a loop
over thousands of databases opens and evicts a connection per database at a
0% hit rate. The SchemaWatcher keeps the ``catalog_*`` tables current (owned
databases from the write path, external ones by polling), so these helpers
answer the same questions as the live introspection methods on
:class:`~datasette.database.Database` with a handful of ``_internal`` queries
that cover any number of databases.

They describe whatever the catalog currently holds - exactly what
``allowed_resources()`` lists - so a listing and the facts used to filter or
decorate it always come from the same snapshot.
"""

import json

from .sqlite import (
    _VIRTUAL_TABLE_SHADOW_SUFFIXES,
    SQLITE_STAT_TABLES,
    _is_fts_content_virtual_table,
    _virtual_table_module,
    derived_table_dependencies_from_rows,
)

# Tables hidden in databases that contain a geometry_columns table, matching
# Database.hidden_table_names()
SPATIALITE_HIDDEN_TABLES = (
    "ElementaryGeometries",
    "SpatialIndex",
    "geometry_columns",
    "spatial_ref_sys",
    "spatialite_history",
    "sql_statements_log",
    "sqlite_sequence",
    "views_geometry_columns",
    "virts_geometry_columns",
    "data_licenses",
    "KNN",
    "KNN2",
)


def _names_param(names):
    return json.dumps(list(names))


def derived_cache_key(state):
    """Identifies one version of a database's catalog rows. A replaced file
    can keep its schema_version, so the scan count is part of the key."""
    return ("catalog", state.catalog_version, state.stats["scans"])


def remember_derived_table_dependencies(state, table_rows):
    """Called by the SchemaWatcher with the ``(name, sql)`` table rows it has
    just written to the catalog, so permission checks never need to read
    them back."""
    state.db._cached_derived_table_dependencies = (
        derived_cache_key(state),
        derived_table_dependencies_from_rows(table_rows),
    )


async def catalog_derived_table_dependencies(datasette, databases):
    """``{name: {implementation table: source table}}`` for the given watched
    ``Database`` objects, from the rows in the catalog.

    Each database's map is cached against its catalog rows (normally filled
    in by the SchemaWatcher when it scans the database); the ones missing -
    databases restored from a persisted catalog - are read back in one
    ``_internal`` query, never from the databases themselves.
    """
    result = {}
    missing = []
    for db in databases:
        state = db._watch_state
        if state.missing or state.catalog_version is None:
            # No catalog rows, so nothing listed that could need filtering
            result[db.name] = {}
            continue
        key = derived_cache_key(state)
        cached = db._cached_derived_table_dependencies
        if cached is not None and cached[0] == key:
            result[db.name] = cached[1]
        else:
            missing.append((db, key))
    if missing:
        # Only databases with a virtual table can have dependencies
        rows = await datasette.get_internal_database().execute(
            """
            select database_name, table_name, sql from catalog_tables
            where database_name in (
              select database_name from catalog_tables
              where coalesce(rootpage, 0) = 0
                and database_name in (select value from json_each(:names))
            )
            order by rowid
            """,
            {"names": _names_param(db.name for db, _ in missing)},
        )
        by_database = {}
        for database_name, table_name, sql in rows.rows:
            by_database.setdefault(database_name, []).append((table_name, sql))
        for db, key in missing:
            deps = derived_table_dependencies_from_rows(by_database.get(db.name, ()))
            db._cached_derived_table_dependencies = (key, deps)
            result[db.name] = deps
    return result


async def all_derived_table_dependencies(datasette):
    """``{database: dependencies}`` for every watched database, cached until
    the catalog next changes (``SchemaWatcher.catalog_generation``), so each
    page of ``allowed_resources()`` costs a dictionary lookup instead of a
    query against every attached database."""
    generation = datasette._schema_watcher.catalog_generation
    cached = datasette._catalog_derived_cache
    if cached is not None and cached[0] == generation:
        return cached[1]
    watched = [db for db in datasette.databases.values() if db._watch_state is not None]
    result = await catalog_derived_table_dependencies(datasette, watched)
    result = {name: deps for name, deps in result.items() if deps}
    datasette._catalog_derived_cache = (generation, result)
    return result


class CatalogSummary:
    """What the index page needs to know about one database's tables."""

    def __init__(self, name):
        self.name = name
        self.views = set()
        # (table, sql, type) for virtual tables and tables typed as shadow
        self.special_rows = []
        self.has_spatialite = False
        self._shadow = None
        self._content_fts = None

    def _classify(self):
        virtual = [(t, sql) for t, sql, _ in self.special_rows if sql is not None]
        typed_shadow = {t for t, _, type_ in self.special_rows if type_ == "shadow"}
        untyped = any(type_ is None for _, _, type_ in self.special_rows)
        shadow = set(typed_shadow)
        if untyped:
            # Rows written without PRAGMA table_list types: fall back to the
            # DDL-based rule for SQLite's built-in modules. Candidate names
            # that do not exist are harmless - only existing tables are
            # looked up.
            for virtual_table, sql in virtual:
                module = _virtual_table_module(sql)
                for suffix in _VIRTUAL_TABLE_SHADOW_SUFFIXES.get(module, ()):
                    shadow.add(virtual_table + suffix)
        self._shadow = shadow
        self._content_fts = {
            t for t, sql in virtual if _is_fts_content_virtual_table(sql)
        }

    def is_hidden(self, table, config_hidden=()):
        """Matches ``table in await db.hidden_table_names()`` for an
        existing table."""
        if self._shadow is None:
            self._classify()
        if table in config_hidden:
            return True
        if table in SQLITE_STAT_TABLES or table.startswith("_"):
            return True
        if table in self._shadow or table in self._content_fts:
            return True
        return self.has_spatialite and (
            table in SPATIALITE_HIDDEN_TABLES
            # sqlite_master "name like 'idx_%'": case-insensitive, "_" is
            # a single-character wildcard
            or (len(table) >= 4 and table[:3].lower() == "idx")
        )


async def catalog_summaries(datasette, database_names):
    """``{database: CatalogSummary}`` for the named databases, in two
    ``_internal`` queries whatever the number of databases."""
    internal = datasette.get_internal_database()
    params = {"names": _names_param(database_names)}
    summaries = {name: CatalogSummary(name) for name in database_names}
    views = await internal.execute(
        """
        select database_name, view_name from catalog_views
        where database_name in (select value from json_each(:names))
        """,
        params,
    )
    for database_name, view_name in views.rows:
        summary = summaries.get(database_name)
        if summary is not None:
            summary.views.add(view_name)
            if view_name == "geometry_columns":
                summary.has_spatialite = True
    special = await internal.execute(
        """
        select database_name, table_name,
          case when coalesce(rootpage, 0) = 0 then sql end as sql, type
        from catalog_tables
        where database_name in (select value from json_each(:names))
          and (
            coalesce(rootpage, 0) = 0
            or type is null
            or type = 'shadow'
            or table_name = 'geometry_columns'
          )
        order by rowid
        """,
        params,
    )
    for database_name, table_name, sql, type_ in special.rows:
        summary = summaries.get(database_name)
        if summary is None:
            continue
        if table_name == "geometry_columns":
            summary.has_spatialite = True
        if sql is not None and _virtual_table_module(sql) is None:
            sql = None
        if sql is not None or type_ in (None, "shadow"):
            summary.special_rows.append((table_name, sql, type_))
    return summaries


async def catalog_table_details(datasette, pairs):
    """``{(database, table): {"columns": [...], "primary_keys": [...],
    "fts_table": str | None}}`` for the given pairs, matching
    ``table_columns()``, ``primary_keys()`` and ``fts_table()``."""
    from . import detect_fts_sql

    pairs = list(pairs)
    details = {
        pair: {"columns": [], "primary_keys": [], "fts_table": None} for pair in pairs
    }
    if not pairs:
        return details
    internal = datasette.get_internal_database()
    columns = await internal.execute(
        """
        select database_name, table_name, name, is_pk from catalog_columns
        where (database_name, table_name) in (
          select json_extract(value, '$[0]'), json_extract(value, '$[1]')
          from json_each(:pairs)
        )
        order by database_name, table_name, cid
        """,
        {"pairs": json.dumps(pairs)},
    )
    pks = {}
    for database_name, table_name, name, is_pk in columns.rows:
        entry = details.get((database_name, table_name))
        if entry is None:
            continue
        entry["columns"].append(name)
        if is_pk:
            pks.setdefault((database_name, table_name), []).append((is_pk, name))
    for pair, pk_columns in pks.items():
        # Stable sort by position in the primary key, like detect_primary_keys()
        pk_columns.sort(key=lambda item: item[0])
        details[pair]["primary_keys"] = [name for _, name in pk_columns]
    # detect_fts() against the catalog: the same LIKE patterns, so the same
    # matching rules; first match in sqlite_master (= catalog rowid) order
    fts_args = []
    for database_name, table in pairs:
        _, fts_params = detect_fts_sql(table)
        fts_args.append(
            [
                database_name,
                table,
                fts_params["fts_double_quoted"],
                fts_params["fts_bracket_quoted"],
            ]
        )
    fts = await internal.execute(
        r"""
        select p.db, p.tbl, (
          select c.table_name from catalog_tables c
          where c.database_name = p.db
            and coalesce(c.rootpage, 0) = 0
            and (
              c.sql like p.dq escape char(92)
              or c.sql like p.bq escape char(92)
              or (c.table_name = p.tbl and c.sql like '%VIRTUAL TABLE%USING FTS%')
            )
          order by c.rowid limit 1
        )
        from (
          select json_extract(value, '$[0]') as db, json_extract(value, '$[1]') as tbl,
            json_extract(value, '$[2]') as dq, json_extract(value, '$[3]') as bq
          from json_each(:args)
        ) p
        """,
        {"args": json.dumps(fts_args)},
    )
    for database_name, table, fts_table in fts.rows:
        entry = details.get((database_name, table))
        if entry is not None:
            entry["fts_table"] = fts_table
    return details


async def catalog_relationship_counts(datasette, database_names):
    """``{database: {table: incoming + outgoing foreign keys}}``, counted the
    way ``get_all_foreign_keys()`` builds its lists: compound foreign keys
    are skipped, and so are references to tables that do not exist."""
    internal = datasette.get_internal_database()
    rows = await internal.execute(
        """
        with fk as (
          select database_name, table_name, id, max("table") as other
          from catalog_foreign_keys
          where database_name in (select value from json_each(:names))
          group by database_name, table_name, id
          having count(*) = 1
        ),
        valid as (
          select fk.database_name, fk.table_name, fk.other from fk
          where exists (
            select 1 from catalog_tables t
            where t.database_name = fk.database_name and t.table_name = fk.other
          )
        )
        select database_name, table_name, count(*) from (
          select database_name, table_name from valid
          union all
          select database_name, other as table_name from valid
        )
        group by database_name, table_name
        """,
        {"names": _names_param(database_names)},
    )
    counts = {}
    for database_name, table_name, n in rows.rows:
        counts.setdefault(database_name, {})[table_name] = n
    return counts


async def catalog_all_foreign_keys(datasette, database_name):
    """``get_all_foreign_keys()`` for one database, from the catalog:
    ``{table: {"incoming": [...], "outgoing": [...]}}`` for every table."""
    internal = datasette.get_internal_database()
    tables = await internal.execute(
        "select table_name from catalog_tables where database_name = ? order by table_name",
        [database_name],
    )
    table_to_foreign_keys = {
        row[0]: {"incoming": [], "outgoing": []} for row in tables.rows
    }
    rows = await internal.execute(
        """
        select table_name, id, "table", "from", "to" from catalog_foreign_keys
        where database_name = ?
        order by table_name, id, seq
        """,
        [database_name],
    )
    by_table = {}
    for table_name, fk_id, other_table, from_, to_ in rows.rows:
        by_table.setdefault(table_name, {}).setdefault(fk_id, []).append(
            (other_table, from_, to_)
        )
    for table, fks in by_table.items():
        if table not in table_to_foreign_keys:
            continue
        for parts in fks.values():
            if len(parts) != 1:
                # Compound foreign keys are left out, as get_outbound_foreign_keys() does
                continue
            other_table, from_, to_ = parts[0]
            if other_table not in table_to_foreign_keys:
                # Weird edge case where something refers to a table that does
                # not actually exist
                continue
            table_to_foreign_keys[other_table]["incoming"].append(
                {"other_table": table, "column": to_, "other_column": from_}
            )
            table_to_foreign_keys[table]["outgoing"].append(
                {"other_table": other_table, "column": from_, "other_column": to_}
            )
    for foreign_keys in table_to_foreign_keys.values():
        foreign_keys["incoming"].sort(
            key=lambda fk: (fk["other_table"], fk["column"], fk["other_column"])
        )
        foreign_keys["outgoing"].sort(
            key=lambda fk: (fk["other_table"], fk["column"], fk["other_column"])
        )
    return table_to_foreign_keys
