from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Iterable

from .utils import tilde_encode, urlsafe_components

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


@dataclass
class StoredQuery:
    database: str
    name: str
    sql: str
    title: str | None
    description: str | None
    description_html: str | None
    hide_sql: bool
    fragment: str | None
    parameters: list[str]
    is_write: bool
    is_private: bool
    is_trusted: bool
    source: str
    owner_id: str | None
    on_success_message: str | None
    on_success_message_sql: str | None
    on_success_redirect: str | None
    on_error_message: str | None
    on_error_redirect: str | None
    private: bool | None = None


@dataclass
class StoredQueryPage:
    queries: list[StoredQuery]
    next: str | None
    has_more: bool
    limit: int


def stored_query_to_dict(query: StoredQuery) -> dict[str, Any]:
    data = {
        "database": query.database,
        "name": query.name,
        "sql": query.sql,
        "title": query.title,
        "description": query.description,
        "description_html": query.description_html,
        "hide_sql": query.hide_sql,
        "fragment": query.fragment,
        "parameters": list(query.parameters),
        "is_write": query.is_write,
        "is_private": query.is_private,
        "is_trusted": query.is_trusted,
        "source": query.source,
        "owner_id": query.owner_id,
        "on_success_message": query.on_success_message,
        "on_success_message_sql": query.on_success_message_sql,
        "on_success_redirect": query.on_success_redirect,
        "on_error_message": query.on_error_message,
        "on_error_redirect": query.on_error_redirect,
    }
    if query.private is not None:
        data["private"] = query.private
    return data


def stored_query_page_to_dict(page: StoredQueryPage) -> dict[str, Any]:
    return {
        "queries": [stored_query_to_dict(query) for query in page.queries],
        "next": page.next,
        "limit": page.limit,
    }


async def save_queries_from_config(datasette: Any) -> None:
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
                is_trusted=bool(query_config.get("is_trusted", True)),
                source="config",
                on_success_message=query_config.get("on_success_message"),
                on_success_message_sql=query_config.get("on_success_message_sql"),
                on_success_redirect=query_config.get("on_success_redirect"),
                on_error_message=query_config.get("on_error_message"),
                on_error_redirect=query_config.get("on_error_redirect"),
            )


def query_row_to_stored_query(
    row: Any, private: bool | None = None
) -> StoredQuery | None:
    if row is None:
        return None
    parameters = json.loads(row["parameters"] or "[]")
    options = json.loads(row["options"] or "{}")
    return StoredQuery(
        database=row["database_name"],
        name=row["name"],
        sql=row["sql"],
        title=row["title"],
        description=row["description"],
        description_html=row["description_html"],
        hide_sql=bool(options.get("hide_sql")),
        fragment=options.get("fragment"),
        parameters=parameters,
        is_write=bool(row["is_write"]),
        is_private=bool(row["is_private"]),
        is_trusted=bool(row["is_trusted"]),
        source=row["source"],
        owner_id=row["owner_id"],
        on_success_message=options.get("on_success_message"),
        on_success_message_sql=options.get("on_success_message_sql"),
        on_success_redirect=options.get("on_success_redirect"),
        on_error_message=options.get("on_error_message"),
        on_error_redirect=options.get("on_error_redirect"),
        private=private,
    )


def query_options_json(options: dict[str, Any]) -> str:
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
    datasette: Any,
    database: str,
    name: str,
    sql: str,
    *,
    title: str | None = None,
    description: str | None = None,
    description_html: str | None = None,
    hide_sql: bool = False,
    fragment: str | None = None,
    parameters: Iterable[str] | None = None,
    is_write: bool = False,
    is_private: bool = False,
    is_trusted: bool = False,
    source: str = "plugin",
    owner_id: str | None = None,
    on_success_message: str | None = None,
    on_success_message_sql: str | None = None,
    on_success_redirect: str | None = None,
    on_error_message: str | None = None,
    on_error_redirect: str | None = None,
    replace: bool = True,
) -> None:
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
    datasette: Any,
    database: str,
    name: str,
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
) -> None:
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


async def remove_query(
    datasette: Any, database: str, name: str, source: str | None = None
) -> None:
    sql = "DELETE FROM queries WHERE database_name = ? AND name = ?"
    params = [database, name]
    if source is not None:
        sql += " AND source = ?"
        params.append(source)
    await datasette.get_internal_database().execute_write(sql, params)


async def get_query(datasette: Any, database: str, name: str) -> StoredQuery | None:
    rows = await datasette.get_internal_database().execute(
        """
        SELECT * FROM queries
        WHERE database_name = ? AND name = ?
        """,
        [database, name],
    )
    return query_row_to_stored_query(rows.first())


async def count_queries(
    datasette: Any,
    database: str | None = None,
    *,
    actor: dict[str, Any] | None = None,
    q: str | None = None,
    is_write: bool | None = None,
    is_private: bool | None = None,
    is_trusted: bool | None = None,
    source: str | None = None,
    owner_id: str | None = None,
) -> int:
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
    datasette: Any,
    database: str | None = None,
    *,
    actor: dict[str, Any] | None = None,
    limit: int = 50,
    cursor: str | None = None,
    q: str | None = None,
    is_write: bool | None = None,
    is_private: bool | None = None,
    is_trusted: bool | None = None,
    source: str | None = None,
    owner_id: str | None = None,
    include_private: bool = False,
) -> StoredQueryPage:
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
        query = query_row_to_stored_query(
            row, private=bool(row["private"]) if include_private else None
        )
        assert query is not None
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
    return StoredQueryPage(
        queries=queries,
        next=next_token,
        has_more=has_more,
        limit=limit,
    )
