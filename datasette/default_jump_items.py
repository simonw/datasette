from datasette import hookimpl
from datasette.jump import JumpSQL


@hookimpl
def jump_items_sql(datasette, actor, request):
    async def inner():
        database_sql, database_params = await datasette.allowed_resources_sql(
            action="view-database", actor=actor
        )
        table_sql, table_params = await datasette.allowed_resources_sql(
            action="view-table", actor=actor
        )
        query_sql, query_params = await datasette.allowed_resources_sql(
            action="view-query", actor=actor
        )
        return [
            JumpSQL(
                sql=f"""
                WITH allowed_databases AS (
                    {database_sql}
                )
                SELECT
                    'database' AS type,
                    parent AS label,
                    NULL AS description,
                    json_object(
                        'method', 'database',
                        'database', parent
                    ) AS url,
                    parent AS search_text,
                    NULL AS display_name
                FROM allowed_databases
                """,
                params=database_params,
            ),
            JumpSQL(
                sql=f"""
                WITH allowed_tables AS (
                    {table_sql}
                )
                SELECT
                    CASE WHEN catalog_views.view_name IS NULL THEN 'table' ELSE 'view' END AS type,
                    allowed_tables.parent || ': ' || allowed_tables.child AS label,
                    NULL AS description,
                    json_object(
                        'method', 'table',
                        'database', allowed_tables.parent,
                        'table', allowed_tables.child
                    ) AS url,
                    allowed_tables.parent || ' ' || allowed_tables.child AS search_text,
                    NULL AS display_name
                FROM allowed_tables
                LEFT JOIN catalog_views
                    ON catalog_views.database_name = allowed_tables.parent
                   AND catalog_views.view_name = allowed_tables.child
                """,
                params=table_params,
            ),
            JumpSQL(
                sql=f"""
                WITH allowed_queries AS (
                    {query_sql}
                )
                SELECT
                    'query' AS type,
                    allowed_queries.parent || ': ' || allowed_queries.child AS label,
                    NULL AS description,
                    json_object(
                        'method', 'query',
                        'database', allowed_queries.parent,
                        'query', allowed_queries.child
                    ) AS url,
                    allowed_queries.parent || ' ' || allowed_queries.child AS search_text,
                    NULL AS display_name
                FROM allowed_queries
                """,
                params=query_params,
            ),
        ]

    return inner
