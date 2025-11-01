"""Core resource types for Datasette's permission system."""

from datasette.permissions import Resource


class DatabaseResource(Resource):
    """A database in Datasette."""

    name = "database"
    parent_class = None  # Top of the resource hierarchy

    def __init__(self, database: str):
        super().__init__(parent=database, child=None)

    @classmethod
    async def resources_sql(cls, datasette) -> str:
        return """
            SELECT database_name AS parent, NULL AS child
            FROM catalog_databases
        """


class TableResource(Resource):
    """A table in a database."""

    name = "table"
    parent_class = DatabaseResource

    def __init__(self, database: str, table: str):
        super().__init__(parent=database, child=table)

    @classmethod
    async def resources_sql(cls, datasette) -> str:
        return """
            SELECT database_name AS parent, table_name AS child
            FROM catalog_tables
            UNION ALL
            SELECT database_name AS parent, view_name AS child
            FROM catalog_views
        """


class QueryResource(Resource):
    """A canned query in a database."""

    name = "query"
    parent_class = DatabaseResource

    def __init__(self, database: str, query: str):
        super().__init__(parent=database, child=query)

    @classmethod
    async def resources_sql(cls, datasette) -> str:
        from datasette.plugins import pm
        from datasette.utils import await_me_maybe

        # Get all databases from catalog
        db = datasette.get_internal_database()
        result = await db.execute("SELECT database_name FROM catalog_databases")
        databases = [row[0] for row in result.rows]

        # Gather all canned queries from all databases
        query_pairs = []
        for database_name in databases:
            # Call the hook to get queries (including from config via default plugin)
            for queries_result in pm.hook.canned_queries(
                datasette=datasette,
                database=database_name,
                actor=None,  # Get ALL queries for resource enumeration
            ):
                queries = await await_me_maybe(queries_result)
                if queries:
                    for query_name in queries.keys():
                        query_pairs.append((database_name, query_name))

        # Build SQL
        if not query_pairs:
            return "SELECT NULL AS parent, NULL AS child WHERE 0"

        # Generate UNION ALL query
        selects = []
        for db_name, query_name in query_pairs:
            # Escape single quotes by doubling them
            db_escaped = db_name.replace("'", "''")
            query_escaped = query_name.replace("'", "''")
            selects.append(
                f"SELECT '{db_escaped}' AS parent, '{query_escaped}' AS child"
            )

        return " UNION ALL ".join(selects)
