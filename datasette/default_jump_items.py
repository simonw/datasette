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
                    'Database' AS description,
                    NULL AS url,
                    parent AS database_name,
                    NULL AS resource_name,
                    parent AS search_text,
                    10 AS sort_key,
                    'datasette' AS source,
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
                    CASE WHEN catalog_views.view_name IS NULL THEN 'Table' ELSE 'View' END AS description,
                    NULL AS url,
                    allowed_tables.parent AS database_name,
                    allowed_tables.child AS resource_name,
                    allowed_tables.parent || ' ' || allowed_tables.child AS search_text,
                    CASE WHEN catalog_views.view_name IS NULL THEN 20 ELSE 25 END AS sort_key,
                    'datasette' AS source,
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
                    'Canned query' AS description,
                    NULL AS url,
                    allowed_queries.parent AS database_name,
                    allowed_queries.child AS resource_name,
                    allowed_queries.parent || ' ' || allowed_queries.child AS search_text,
                    30 AS sort_key,
                    'datasette' AS source,
                    NULL AS display_name
                FROM allowed_queries
                """,
                params=query_params,
            ),
        ]

    return inner
