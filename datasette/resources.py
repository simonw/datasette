"""Core resource types for Datasette's permission system."""

from datasette.permissions import Resource


class DatabaseResource(Resource):
    """A database in Datasette."""

    name = "database"
    parent_class = None  # Top of the resource hierarchy

    def __init__(self, database: str):
        super().__init__(parent=database, child=None)

    @classmethod
    async def resources_sql(cls, datasette, actor=None) -> str:
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
    async def resources_sql(cls, datasette, actor=None) -> str:
        return """
            SELECT database_name AS parent, table_name AS child
            FROM catalog_tables
            UNION ALL
            SELECT database_name AS parent, view_name AS child
            FROM catalog_views
        """


class QueryResource(Resource):
    """A saved query in a database."""

    name = "query"
    parent_class = DatabaseResource

    def __init__(self, database: str, query: str):
        super().__init__(parent=database, child=query)

    @classmethod
    async def resources_sql(cls, datasette, actor=None) -> str:
        return """
            SELECT q.database_name AS parent, q.name AS child
            FROM queries q
            JOIN catalog_databases cd ON cd.database_name = q.database_name
        """
