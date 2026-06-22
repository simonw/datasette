from datasette import hookimpl
from datasette.resources import TableResource


@hookimpl
def table_actions(datasette, actor, database, table, request):
    async def inner():
        db = datasette.get_database(database)
        if not db.is_mutable:
            return []
        if not await datasette.allowed(
            action="alter-table",
            resource=TableResource(database=database, table=table),
            actor=actor,
        ):
            return []
        return [
            {
                "type": "button",
                "label": "Alter table",
                "description": "Change columns and primary key for this table.",
                "attrs": {
                    "aria-label": "Alter table {}".format(table),
                    "data-table-action": "alter-table",
                },
            }
        ]

    return inner
