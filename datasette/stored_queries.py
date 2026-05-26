from __future__ import annotations

import json

from .resources import TableResource
from .utils import named_parameters, sqlite3, tilde_encode, urlsafe_components
from .utils.asgi import Forbidden

UNCHANGED = object()

QUERY_OPTION_FIELDS = (
    "hide_sql",
    "fragment",
    "on_success_message",
    "on_success_message_sql",
    "on_success_redirect",
    "on_error_message",
    "on_error_redirect",
)


async def save_queries_from_config(datasette):
    # Apply configured query entries from datasette.yaml to the internal table.
    await datasette.get_internal_database().execute_write(
        "DELETE FROM queries WHERE source = 'config'"
    )
    for dbname, db_config in ((datasette.config or {}).get("databases") or {}).items():
        for query_name, query_config in (db_config.get("queries") or {}).items():
            if not isinstance(query_config, dict):
                query_config = {"sql": query_config}
            await datasette.add_query(
                dbname,
                query_name,
                query_config["sql"],
                title=query_config.get("title"),
                description=query_config.get("description"),
                description_html=query_config.get("description_html"),
                hide_sql=bool(query_config.get("hide_sql")),
                fragment=query_config.get("fragment"),
                parameters=query_config.get("params"),
                is_write=bool(query_config.get("write")),
                is_private=bool(query_config.get("is_private")),
                is_trusted=bool(query_config.get("is_trusted", True)),
                source="config",
                on_success_message=query_config.get("on_success_message"),
                on_success_message_sql=query_config.get("on_success_message_sql"),
                on_success_redirect=query_config.get("on_success_redirect"),
                on_error_message=query_config.get("on_error_message"),
                on_error_redirect=query_config.get("on_error_redirect"),
            )


def query_row_to_dict(row):
    if row is None:
        return None
    parameters = json.loads(row["parameters"] or "[]")
    options = json.loads(row["options"] or "{}")
    return {
        "database": row["database_name"],
        "name": row["name"],
        "sql": row["sql"],
        "title": row["title"],
        "description": row["description"],
        "description_html": row["description_html"],
        "hide_sql": bool(options.get("hide_sql")),
        "fragment": options.get("fragment"),
        "params": parameters,
        "parameters": parameters,
        "is_write": bool(row["is_write"]),
        "is_private": bool(row["is_private"]),
        "is_trusted": bool(row["is_trusted"]),
        "source": row["source"],
        "owner_id": row["owner_id"],
        "on_success_message": options.get("on_success_message"),
        "on_success_message_sql": options.get("on_success_message_sql"),
        "on_success_redirect": options.get("on_success_redirect"),
        "on_error_message": options.get("on_error_message"),
        "on_error_redirect": options.get("on_error_redirect"),
    }


def query_options_json(options):
    options_dict = {}
    for field in QUERY_OPTION_FIELDS:
        value = options.get(field)
        if field == "hide_sql":
            if value:
                options_dict[field] = True
        elif value is not None:
            options_dict[field] = value
    return json.dumps(options_dict, sort_keys=True)


async def add_query(
    datasette,
    database,
    name,
    sql,
    *,
    title=None,
    description=None,
    description_html=None,
    hide_sql=False,
    fragment=None,
    parameters=None,
    is_write=False,
    is_private=False,
    is_trusted=False,
    source="plugin",
    owner_id=None,
    on_success_message=None,
    on_success_message_sql=None,
    on_success_redirect=None,
    on_error_message=None,
    on_error_redirect=None,
    replace=True,
):
    parameters_json = json.dumps(list(parameters or []))
    options_json = query_options_json(
        {
            "hide_sql": hide_sql,
            "fragment": fragment,
            "on_success_message": on_success_message,
            "on_success_message_sql": on_success_message_sql,
            "on_success_redirect": on_success_redirect,
            "on_error_message": on_error_message,
            "on_error_redirect": on_error_redirect,
        }
    )
    sql_statement = """
        INSERT INTO queries (
            database_name, name, sql, title, description, description_html,
            options, parameters, is_write, is_private, is_trusted, source, owner_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    if replace:
        sql_statement += """
            ON CONFLICT(database_name, name) DO UPDATE SET
                sql = excluded.sql,
                title = excluded.title,
                description = excluded.description,
                description_html = excluded.description_html,
                options = excluded.options,
                parameters = excluded.parameters,
                is_write = excluded.is_write,
                is_private = excluded.is_private,
                is_trusted = excluded.is_trusted,
                source = excluded.source,
                owner_id = excluded.owner_id,
                updated_at = CURRENT_TIMESTAMP
        """
    await datasette.get_internal_database().execute_write(
        sql_statement,
        [
            database,
            name,
            sql,
            title,
            description,
            description_html,
            options_json,
            parameters_json,
            int(bool(is_write)),
            int(bool(is_private)),
            int(bool(is_trusted)),
            source,
            owner_id,
        ],
    )


async def update_query(
    datasette,
    database,
    name,
    *,
    sql=UNCHANGED,
    title=UNCHANGED,
    description=UNCHANGED,
    description_html=UNCHANGED,
    hide_sql=UNCHANGED,
    fragment=UNCHANGED,
    parameters=UNCHANGED,
    is_write=UNCHANGED,
    is_private=UNCHANGED,
    is_trusted=UNCHANGED,
    source=UNCHANGED,
    owner_id=UNCHANGED,
    on_success_message=UNCHANGED,
    on_success_message_sql=UNCHANGED,
    on_success_redirect=UNCHANGED,
    on_error_message=UNCHANGED,
    on_error_redirect=UNCHANGED,
):
    fields = {
        "sql": sql,
        "title": title,
        "description": description,
        "description_html": description_html,
        "parameters": parameters,
        "is_write": is_write,
        "is_private": is_private,
        "is_trusted": is_trusted,
        "source": source,
        "owner_id": owner_id,
    }
    option_fields = {
        "hide_sql": hide_sql,
        "fragment": fragment,
        "on_success_message": on_success_message,
        "on_success_message_sql": on_success_message_sql,
        "on_success_redirect": on_success_redirect,
        "on_error_message": on_error_message,
        "on_error_redirect": on_error_redirect,
    }
    updates = []
    params = []
    for field, value in fields.items():
        if value is UNCHANGED:
            continue
        if field in {"is_write", "is_private", "is_trusted"}:
            value = int(bool(value))
        elif field == "parameters":
            value = json.dumps(list(value or []))
        updates.append(f"{field} = ?")
        params.append(value)
    changed_options = {
        field: value for field, value in option_fields.items() if value is not UNCHANGED
    }
    if changed_options:
        rows = await datasette.get_internal_database().execute(
            """
            SELECT options FROM queries
            WHERE database_name = ? AND name = ?
            """,
            [database, name],
        )
        row = rows.first()
        options = json.loads(row["options"] or "{}") if row is not None else {}
        for field, value in changed_options.items():
            if field == "hide_sql":
                if value:
                    options[field] = True
                else:
                    options.pop(field, None)
            elif value is None:
                options.pop(field, None)
            else:
                options[field] = value
        updates.append("options = ?")
        params.append(json.dumps(options, sort_keys=True))
    if not updates:
        return
    updates.append("updated_at = CURRENT_TIMESTAMP")
    params.extend([database, name])
    await datasette.get_internal_database().execute_write(
        """
        UPDATE queries
        SET {}
        WHERE database_name = ? AND name = ?
        """.format(", ".join(updates)),
        params,
    )


async def remove_query(datasette, database, name, source=None):
    sql = "DELETE FROM queries WHERE database_name = ? AND name = ?"
    params = [database, name]
    if source is not None:
        sql += " AND source = ?"
        params.append(source)
    await datasette.get_internal_database().execute_write(sql, params)


async def get_query(datasette, database, name):
    rows = await datasette.get_internal_database().execute(
        """
        SELECT * FROM queries
        WHERE database_name = ? AND name = ?
        """,
        [database, name],
    )
    return query_row_to_dict(rows.first())


async def count_queries(
    datasette,
    database=None,
    *,
    actor=None,
    q=None,
    is_write=None,
    is_private=None,
    is_trusted=None,
    source=None,
    owner_id=None,
):
    allowed_sql, allowed_params = await datasette.allowed_resources_sql(
        action="view-query",
        actor=actor,
        parent=database,
    )
    params = dict(allowed_params)
    where_clauses = []
    if database is not None:
        params["query_database"] = database
        where_clauses.append("q.database_name = :query_database")

    if q:
        where_clauses.append("""
            (
                q.name LIKE :query_search
                OR q.title LIKE :query_search
                OR q.description LIKE :query_search
                OR q.sql LIKE :query_search
            )
            """)
        params["query_search"] = "%{}%".format(q)
    if is_write is not None:
        where_clauses.append("q.is_write = :query_is_write")
        params["query_is_write"] = int(bool(is_write))
    if is_private is not None:
        where_clauses.append("q.is_private = :query_is_private")
        params["query_is_private"] = int(bool(is_private))
    if is_trusted is not None:
        where_clauses.append("q.is_trusted = :query_is_trusted")
        params["query_is_trusted"] = int(bool(is_trusted))
    if source is not None:
        where_clauses.append("q.source = :query_source")
        params["query_source"] = source
    if owner_id is not None:
        where_clauses.append("q.owner_id = :query_owner_id")
        params["query_owner_id"] = owner_id

    row = (
        await datasette.get_internal_database().execute(
            """
            SELECT count(*) AS count
            FROM queries q
            JOIN (
                {allowed_sql}
            ) allowed
              ON allowed.parent = q.database_name
             AND allowed.child = q.name
            WHERE {where}
            """.format(
                allowed_sql=allowed_sql,
                where=" AND ".join(where_clauses) or "1 = 1",
            ),
            params,
        )
    ).first()
    return row["count"]


async def list_queries(
    datasette,
    database=None,
    *,
    actor=None,
    limit=50,
    cursor=None,
    q=None,
    is_write=None,
    is_private=None,
    is_trusted=None,
    source=None,
    owner_id=None,
    include_private=False,
):
    limit = min(max(1, int(limit)), 1000)
    allowed_sql, allowed_params = await datasette.allowed_resources_sql(
        action="view-query",
        actor=actor,
        parent=database,
        include_is_private=include_private,
    )
    params = dict(allowed_params)
    params.update({"limit": limit + 1})
    sort_key_sql = "lower(coalesce(nullif(q.title, ''), q.name))"
    where_clauses = []
    order_by = "q.database_name, sort_key, q.name"
    if database is not None:
        params["query_database"] = database
        where_clauses.append("q.database_name = :query_database")
        order_by = "sort_key, q.name"

    if cursor:
        try:
            components = urlsafe_components(cursor)
        except ValueError:
            components = []
        if database is None and len(components) == 3:
            where_clauses.append("""
                (
                    q.database_name > :cursor_database
                    OR (
                        q.database_name = :cursor_database
                        AND (
                            {sort_key_sql} > :cursor_sort_key
                            OR (
                                {sort_key_sql} = :cursor_sort_key
                                AND q.name > :cursor_name
                            )
                        )
                    )
                )
                """.format(sort_key_sql=sort_key_sql))
            params["cursor_database"] = components[0]
            params["cursor_sort_key"] = components[1]
            params["cursor_name"] = components[2]
        elif database is not None and len(components) == 2:
            where_clauses.append("""
                (
                    {sort_key_sql} > :cursor_sort_key
                    OR (
                        {sort_key_sql} = :cursor_sort_key
                        AND q.name > :cursor_name
                    )
                )
                """.format(sort_key_sql=sort_key_sql))
            params["cursor_sort_key"] = components[0]
            params["cursor_name"] = components[1]

    if q:
        where_clauses.append("""
            (
                q.name LIKE :query_search
                OR q.title LIKE :query_search
                OR q.description LIKE :query_search
                OR q.sql LIKE :query_search
            )
            """)
        params["query_search"] = "%{}%".format(q)
    if is_write is not None:
        where_clauses.append("q.is_write = :query_is_write")
        params["query_is_write"] = int(bool(is_write))
    if is_private is not None:
        where_clauses.append("q.is_private = :query_is_private")
        params["query_is_private"] = int(bool(is_private))
    if is_trusted is not None:
        where_clauses.append("q.is_trusted = :query_is_trusted")
        params["query_is_trusted"] = int(bool(is_trusted))
    if source is not None:
        where_clauses.append("q.source = :query_source")
        params["query_source"] = source
    if owner_id is not None:
        where_clauses.append("q.owner_id = :query_owner_id")
        params["query_owner_id"] = owner_id

    private_select = ", allowed.is_private AS private" if include_private else ""
    rows = list(
        (
            await datasette.get_internal_database().execute(
                """
                SELECT q.*, {sort_key_sql} AS sort_key{private_select}
                FROM queries q
                JOIN (
                    {allowed_sql}
                ) allowed
                  ON allowed.parent = q.database_name
                 AND allowed.child = q.name
                WHERE {where}
                ORDER BY {order_by}
                LIMIT :limit
                """.format(
                    allowed_sql=allowed_sql,
                    private_select=private_select,
                    sort_key_sql=sort_key_sql,
                    where=" AND ".join(where_clauses) or "1 = 1",
                    order_by=order_by,
                ),
                params,
            )
        ).rows
    )
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    queries = []
    for row in rows:
        query = query_row_to_dict(row)
        if include_private:
            query["private"] = bool(row["private"])
        queries.append(query)

    next_token = None
    if has_more and rows:
        last_row = rows[-1]
        if database is None:
            next_token = "{},{},{}".format(
                tilde_encode(last_row["database_name"]),
                tilde_encode(last_row["sort_key"]),
                tilde_encode(last_row["name"]),
            )
        else:
            next_token = "{},{}".format(
                tilde_encode(last_row["sort_key"]),
                tilde_encode(last_row["name"]),
            )
    return {
        "queries": queries,
        "next": next_token,
        "has_more": has_more,
        "limit": limit,
    }


async def ensure_query_write_permissions(
    datasette, database, sql, *, actor=None, params=None, analysis=None
):
    write_actions = {
        "insert": "insert-row",
        "update": "update-row",
        "delete": "delete-row",
    }
    db = datasette.get_database(database)
    if analysis is None:
        if params is None:
            params = {name: "" for name in named_parameters(sql)}
        try:
            analysis = await db.analyze_sql(sql, params)
        except sqlite3.DatabaseError as ex:
            raise Forbidden(f"Could not analyze query: {ex}") from ex

    for access in analysis.table_accesses:
        action = write_actions.get(access.operation)
        if action is None:
            continue
        if access.database != database:
            raise Forbidden("Writable queries may not write to attached databases")
        if not await datasette.allowed(
            action=action,
            resource=TableResource(database=access.database, table=access.table),
            actor=actor,
        ):
            raise Forbidden(
                f"Permission denied: need {action} on {access.database}/{access.table}"
            )
    return analysis
