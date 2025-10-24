"""Core resource types for Datasette's permission system."""

from datasette.permissions import Resource


class InstanceResource(Resource):
    """The Datasette instance itself."""

    name = "instance"
    parent_name = None

    def __init__(self):
        super().__init__(parent=None, child=None)

    @classmethod
    def resources_sql(cls) -> str:
        return "SELECT NULL AS parent, NULL AS child"


class DatabaseResource(Resource):
    """A database in Datasette."""

    name = "database"
    parent_name = "instance"

    def __init__(self, database: str):
        super().__init__(parent=database, child=None)

    @classmethod
    def resources_sql(cls) -> str:
        return """
            SELECT database_name AS parent, NULL AS child
            FROM catalog_databases
        """


class TableResource(Resource):
    """A table in a database."""

    name = "table"
    parent_name = "database"

    def __init__(self, database: str, table: str):
        super().__init__(parent=database, child=table)

    @classmethod
    def resources_sql(cls) -> str:
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
    parent_name = "database"

    def __init__(self, database: str, query: str):
        super().__init__(parent=database, child=query)

    @classmethod
    def resources_sql(cls) -> str:
        # TODO: Need catalog for queries
        return "SELECT NULL AS parent, NULL AS child WHERE 0"
