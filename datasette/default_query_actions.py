from datasette import hookimpl
from datasette.resources import QueryResource


@hookimpl
def query_actions(datasette, actor, database, query_name, request):
    # Only stored queries (with a name) can be edited or deleted
    if not query_name:
        return None

    async def inner():
        query = await datasette.get_query(database, query_name)
        if query is None:
            return []
        # Config-defined and trusted queries are managed outside the UI
        if query.source == "config" or query.is_trusted:
            return []

        links = []
        if await datasette.allowed(
            action="update-query",
            resource=QueryResource(database, query_name),
            actor=actor,
        ):
            links.append(
                {
                    "href": datasette.urls.table(database, query_name) + "/-/edit",
                    "label": "Edit this query",
                    "description": (
                        "Change the title, description, SQL or visibility."
                    ),
                }
            )
        if await datasette.allowed(
            action="delete-query",
            resource=QueryResource(database, query_name),
            actor=actor,
        ):
            links.append(
                {
                    "href": datasette.urls.table(database, query_name) + "/-/delete",
                    "label": "Delete this query",
                    "description": "Permanently remove this saved query.",
                }
            )
        return links

    return inner
