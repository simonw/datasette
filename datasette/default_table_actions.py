from datasette import hookimpl
from datasette.resources import TableResource


@hookimpl
def table_actions(datasette, actor, database, table, request):
    async def inner():
        db = datasette.get_database(database)
        actions = []
        if db.is_mutable and await datasette.allowed(
            action="alter-table",
            resource=TableResource(database=database, table=table),
            actor=actor,
        ):
            actions.append(
                {
                    "type": "button",
                    "label": "Alter table",
                    "description": "Change columns and primary key for this table.",
                    "attrs": {
                        "aria-label": "Alter table {}".format(table),
                        "data-table-action": "alter-table",
                    },
                }
            )
        # Not gated on db.is_mutable - label configuration is a display
        # preference stored in the internal DB, not a schema change.
        if await datasette.allowed(
            action="set-label-columns",
            resource=TableResource(database=database, table=table),
            actor=actor,
        ):
            actions.append(
                {
                    "type": "button",
                    "label": "Set label column(s)",
                    "description": "Choose which column(s) are used to label this table's rows.",
                    "attrs": {
                        "aria-label": "Set label columns for {}".format(table),
                        "data-table-action": "set-label-columns",
                    },
                }
            )
        return actions

    return inner
