"""
Builds the data for the homepage catalog: one list of every table and view the
actor can see, across all attached databases, plus stored queries.

Everything is driven by SQL against the internal database's catalog_* tables,
so searching and paginating stays cheap even with thousands of tables.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from datasette.database import QueryInterrupted
from datasette.utils import escape_sqlite, named_parameters

PAGE_SIZE = 30
QUERIES_LIMIT = 5
COLUMNS_PREVIEW = 8
# Time budget, in ms, for counting rows in each listed table
COUNT_TIME_LIMIT_MS = 15
# Overall budget, in seconds, for all row counts on one page
COUNT_TOTAL_BUDGET_S = 0.3


@dataclass
class Catalog:
    items: list = field(default_factory=list)
    total: int = 0
    queries: list = field(default_factory=list)
    queries_total: int = 0
    databases: list = field(default_factory=list)
    totals: dict = field(default_factory=dict)


def search_pattern(q):
    terms = q.split()
    return "%" + "%".join(terms) + "%" if terms else "%"


async def _count_rows(datasette, items):
    """Count rows for the given items, within a per-table and total time budget.

    Counts are capped at the database's count_limit; an item whose count is
    above that gets count_truncated = True. Items that time out keep count=None.
    """
    import time

    start = time.monotonic()
    for item in items:
        db = datasette.databases.get(item["database"])
        if db is None:
            continue
        if (
            not db.is_mutable
            and db.cached_table_counts is not None
            and item["name"] in db.cached_table_counts
        ):
            item["count"] = db.cached_table_counts[item["name"]]
            continue
        if time.monotonic() - start > COUNT_TOTAL_BUDGET_S:
            break
        try:
            result = await db.execute(
                "select count(*) from (select * from {} limit {})".format(
                    escape_sqlite(item["name"]), db.count_limit + 1
                ),
                custom_time_limit=COUNT_TIME_LIMIT_MS,
            )
            count = result.rows[0][0]
        except (QueryInterrupted, sqlite3.OperationalError, sqlite3.DatabaseError):
            continue
        if count > db.count_limit:
            item["count"] = db.count_limit
            item["count_truncated"] = True
        else:
            item["count"] = count


async def build_catalog(
    datasette, actor, *, q="", database=None, page=1, page_size=PAGE_SIZE
):
    internal = datasette.get_internal_database()
    q = (q or "").strip()
    pattern = search_pattern(q)

    db_sql, db_params = await datasette.allowed_resources_sql(
        action="view-database", actor=actor, include_is_private=True
    )
    database_rows = (await internal.execute(db_sql, db_params)).rows
    allowed_databases = {
        row["parent"]: bool(row["is_private"]) for row in database_rows
    }
    # Keep attach order, which is how Datasette lists databases elsewhere
    database_names = [n for n in datasette.databases if n in allowed_databases]
    if database not in allowed_databases:
        database = None

    table_sql, table_params = await datasette.allowed_resources_sql(
        action="view-table", actor=actor, include_is_private=True
    )
    params = dict(table_params)
    params.update(
        {
            "allowed_dbs": json.dumps(database_names),
            "q": q,
            "pattern": pattern,
            "database": database,
        }
    )

    items_cte = f"""
    WITH allowed AS (
        {table_sql}
    ),
    items AS (
        SELECT
            allowed.parent AS database_name,
            allowed.child AS name,
            CASE WHEN catalog_views.view_name IS NULL THEN 'table' ELSE 'view' END AS type,
            allowed.is_private AS private,
            (SELECT value FROM metadata_resources m
              WHERE m.database_name = allowed.parent
                AND m.resource_name = allowed.child AND m.key = 'title') AS title,
            (SELECT value FROM metadata_resources m
              WHERE m.database_name = allowed.parent
                AND m.resource_name = allowed.child AND m.key = 'description') AS description
        FROM allowed
        LEFT JOIN catalog_views
            ON catalog_views.database_name = allowed.parent
           AND catalog_views.view_name = allowed.child
        LEFT JOIN catalog_tables
            ON catalog_tables.database_name = allowed.parent
           AND catalog_tables.table_name = allowed.child
        WHERE allowed.parent IN (SELECT value FROM json_each(:allowed_dbs))
          -- FTS shadow tables, SpatiaLite internals and tables hidden in config
          AND COALESCE(catalog_tables.hidden, 0) = 0
    ),
    matching AS (
        SELECT * FROM items
        WHERE :q = ''
           OR name LIKE :pattern
           OR title LIKE :pattern
           OR description LIKE :pattern
           OR EXISTS (
                SELECT 1 FROM catalog_columns c
                WHERE c.database_name = items.database_name
                  AND c.table_name = items.name
                  AND c.name LIKE :pattern
           )
    )
    """

    # Per-database totals for the summary line and the database filter
    summary_rows = (
        await internal.execute(
            items_cte + """
        SELECT database_name, type, count(*) AS n FROM matching
        GROUP BY database_name, type
        """,
            params,
        )
    ).rows
    per_db = {name: {"table": 0, "view": 0} for name in database_names}
    for row in summary_rows:
        per_db[row["database_name"]][row["type"]] = row["n"]

    filtered_params = dict(params)
    where_db = "WHERE (:database IS NULL OR database_name = :database)"
    total = (
        await internal.execute(
            items_cte + f"SELECT count(*) FROM matching {where_db}", filtered_params
        )
    ).first()[0]

    offset = max(page - 1, 0) * page_size
    filtered_params.update({"limit": page_size, "offset": offset})
    rows = (
        await internal.execute(
            items_cte + f"""
        SELECT
            matching.*,
            (SELECT count(*) FROM catalog_columns c
              WHERE c.database_name = matching.database_name
                AND c.table_name = matching.name) AS column_count,
            (SELECT json_group_array(name) FROM (
                SELECT name FROM catalog_columns c
                WHERE c.database_name = matching.database_name
                  AND c.table_name = matching.name
                ORDER BY cid LIMIT {COLUMNS_PREVIEW}
            )) AS columns,
            (SELECT json_group_array(name) FROM (
                SELECT name FROM catalog_columns c
                WHERE :q != ''
                  AND c.database_name = matching.database_name
                  AND c.table_name = matching.name
                  AND c.name LIKE :pattern
                ORDER BY cid LIMIT {COLUMNS_PREVIEW}
            )) AS matched_columns,
            (SELECT count(*) FROM (
                SELECT DISTINCT table_name, id FROM catalog_foreign_keys f
                WHERE f.database_name = matching.database_name
                  AND (f.table_name = matching.name OR f."table" = matching.name)
            )) AS relationships,
            EXISTS (
                SELECT 1 FROM catalog_tables t
                WHERE t.database_name = matching.database_name
                  AND t.table_name = matching.name || '_fts'
            ) AS has_fts,
            CASE
                WHEN :q = '' THEN 0
                WHEN lower(name) = lower(:q) THEN 0
                WHEN name LIKE :q || '%' THEN 1
                WHEN name LIKE :pattern OR title LIKE :pattern THEN 2
                WHEN description LIKE :pattern THEN 3
                ELSE 4
            END AS relevance
        FROM matching
        {where_db}
        ORDER BY relevance, relationships DESC, column_count DESC, name
        LIMIT :limit OFFSET :offset
        """,
            filtered_params,
        )
    ).rows

    items = []
    for row in rows:
        columns = json.loads(row["columns"] or "[]")
        column_count = row["column_count"]
        if row["type"] == "view":
            # Views are not in catalog_columns; only look these up for this page
            db = datasette.databases[row["database_name"]]
            try:
                view_columns = await db.table_columns(row["name"])
            except sqlite3.DatabaseError:
                # A broken view, for example one referencing a missing table
                view_columns = []
            column_count = len(view_columns)
            columns = view_columns[:COLUMNS_PREVIEW]
        matched_columns = json.loads(row["matched_columns"] or "[]")
        if matched_columns:
            # Show the columns that matched the search first
            columns = matched_columns + [c for c in columns if c not in matched_columns]
            columns = columns[: max(COLUMNS_PREVIEW, len(matched_columns))]
        items.append(
            {
                "database": row["database_name"],
                "name": row["name"],
                "type": row["type"],
                "private": bool(row["private"]),
                "title": row["title"],
                "description": row["description"],
                "columns": columns,
                "matched_columns": matched_columns,
                "column_count": column_count,
                "relationships": row["relationships"],
                "has_fts": bool(row["has_fts"]),
                "count": None,
                "count_truncated": False,
                "url": datasette.urls.table(row["database_name"], row["name"]),
                "color": datasette.databases[row["database_name"]].color,
            }
        )
    await _count_rows(datasette, items)

    # Stored queries: canned queries from config plus saved user queries
    query_sql, query_params = await datasette.allowed_resources_sql(
        action="view-query", actor=actor, include_is_private=True
    )
    qparams = dict(query_params)
    qparams.update(
        {
            "q": q,
            "pattern": pattern,
            "database": database,
            "allowed_dbs": json.dumps(database_names),
        }
    )
    queries_cte = f"""
    WITH allowed AS ({query_sql}),
    matching AS (
        SELECT queries.rowid AS rowid, queries.*, allowed.is_private AS allowed_private
        FROM allowed
        JOIN queries
          ON queries.database_name = allowed.parent
         AND queries.name = allowed.child
        WHERE allowed.parent IN (SELECT value FROM json_each(:allowed_dbs))
          AND (:database IS NULL OR queries.database_name = :database)
          AND (
            :q = ''
            OR queries.name LIKE :pattern
            OR queries.title LIKE :pattern
            OR queries.description LIKE :pattern
            OR queries.sql LIKE :pattern
          )
    )
    """
    queries_total = (
        await internal.execute(queries_cte + "SELECT count(*) FROM matching", qparams)
    ).first()[0]
    # Without a search, only show queries someone chose to put there: ones
    # from config or plugins, in the order they were defined, then the
    # current actor's own saved queries. Other people's saved queries can
    # number in the hundreds, so they only appear in search results.
    qparams["actor_id"] = (actor or {}).get("id")
    query_rows = (
        await internal.execute(
            queries_cte + f"""
        SELECT * FROM matching
        WHERE :q != ''
           OR source != 'user'
           OR (owner_id IS NOT NULL AND owner_id = :actor_id)
        ORDER BY
            CASE
                WHEN :q = '' THEN 0
                WHEN name LIKE :pattern OR title LIKE :pattern THEN 0
                WHEN description LIKE :pattern THEN 1
                ELSE 2
            END,
            CASE WHEN source != 'user' THEN 0 ELSE 1 END,
            CASE WHEN source != 'user' THEN rowid END,
            updated_at DESC,
            name
        LIMIT {QUERIES_LIMIT}
        """,
            qparams,
        )
    ).rows
    queries = []
    for row in query_rows:
        try:
            parameters = json.loads(row["parameters"] or "[]")
        except ValueError:
            parameters = []
        if not parameters:
            parameters = named_parameters(row["sql"])
        queries.append(
            {
                "database": row["database_name"],
                "name": row["name"],
                "title": row["title"],
                "description": row["description"],
                "sql": row["sql"],
                "is_write": bool(row["is_write"]),
                "private": bool(row["allowed_private"]),
                "parameters": parameters,
                "url": datasette.urls.query(row["database_name"], row["name"]),
                "color": datasette.databases[row["database_name"]].color,
                "mine": row["source"] == "user"
                and row["owner_id"] is not None
                and row["owner_id"] == qparams["actor_id"],
            }
        )

    databases = []
    for name in database_names:
        db = datasette.databases[name]
        databases.append(
            {
                "name": name,
                "color": db.color,
                "private": allowed_databases[name],
                "tables": per_db[name]["table"],
                "views": per_db[name]["view"],
                "url": datasette.urls.database(name),
                "size": None if db.is_memory else db.size,
            }
        )

    return Catalog(
        items=items,
        total=total,
        queries=queries,
        queries_total=queries_total,
        databases=databases,
        totals={
            "databases": len(databases),
            "tables": sum(d["tables"] for d in databases),
            "views": sum(d["views"] for d in databases),
        },
    )
