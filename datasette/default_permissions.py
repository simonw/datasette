from datasette import hookimpl


@hookimpl
def permission_allowed(actor, action, resource_type, resource_identifier):
    if actor and actor.get("id") == "root" and action == "permissions-debug":
        return True
