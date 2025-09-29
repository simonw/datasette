from datasette import hookimpl
from datasette.utils.permissions import PluginSQL


@hookimpl
def permission_resources_sql(datasette, actor, action):
    if action != "view-table":
        return None

    actor_id = (actor or {}).get("id")

    root_deny = PluginSQL(
        source="example_default_deny",
        sql="""
            SELECT NULL AS parent, NULL AS child, 0 AS allow,
                   'example plugin default deny' AS reason
        """,
        params={},
    )

    pelican_allow = PluginSQL(
        source="pelican_content_tables",
        sql="""
            SELECT
                database_name AS parent,
                table_name AS child,
                1 AS allow,
                'pelican allowed all content tables' AS reason
            FROM catalog_tables
            WHERE database_name = 'content'
              AND :actor_id = 'pelican'
        """,
        params={"actor_id": actor_id},
    )

    violin_allow = PluginSQL(
        source="violin_content_repos",
        sql="""
            SELECT
                'content' AS parent,
                'repos' AS child,
                1 AS allow,
                'violin allowed content/repos' AS reason
            WHERE :actor_id = 'violin'
        """,
        params={"actor_id": actor_id},
    )

    return [root_deny, pelican_allow, violin_allow]
