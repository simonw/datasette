from datasette import hookimpl
from datasette.utils import actor_matches_allow


@hookimpl
def permission_allowed(datasette, actor, action, resource_type, resource_identifier):
    if action == "permissions-debug":
        if actor and actor.get("id") == "root":
            return True
    elif action == "view-instance":
        allow = datasette.metadata("allow")
        if allow is not None:
            return actor_matches_allow(actor, allow)
    elif action == "view-database":
        assert resource_type == "database"
        database_allow = datasette.metadata("allow", database=resource_identifier)
        if database_allow is None:
            return True
        return actor_matches_allow(actor, database_allow)
    elif action == "view-query":
        # Check if this query has a "allow" block in metadata
        assert resource_type == "query"
        database, query_name = resource_identifier
        queries_metadata = datasette.metadata("queries", database=database)
        assert query_name in queries_metadata
        if isinstance(queries_metadata[query_name], str):
            return True
        allow = queries_metadata[query_name].get("allow")
        if allow is None:
            return True
        return actor_matches_allow(actor, allow)
