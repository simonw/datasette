from datasette import hookimpl
from datasette.resources import DatabaseResource


@hookimpl
def database_actions(datasette, actor, database, request):
    async def inner():
        if not datasette.get_database(database).is_mutable:
            return []
        if not await datasette.allowed(
            action="execute-write-sql",
            resource=DatabaseResource(database),
            actor=actor,
        ):
            return []
        return [
            {
                "href": datasette.urls.database(database) + "/-/execute-write",
                "label": "Execute write SQL",
                "description": "Run writable SQL with table permission checks.",
            }
        ]

    return inner
