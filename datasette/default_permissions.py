from datasette import hookimpl
from datasette.utils import actor_matches_allow
import click
import itsdangerous
import json
import time


@hookimpl(tryfirst=True, specname="permission_allowed")
def permission_allowed_default(datasette, actor, action, resource):
    async def inner():
        if action in (
            "permissions-debug",
            "debug-menu",
            "insert-row",
            "create-table",
            "drop-table",
            "delete-row",
            "update-row",
        ):
            if actor and actor.get("id") == "root":
                return True
        elif action == "view-instance":
            allow = datasette.metadata("allow")
            if allow is not None:
                return actor_matches_allow(actor, allow)
        elif action == "view-database":
            if resource == "_internal" and (actor is None or actor.get("id") != "root"):
                return False
            database_allow = datasette.metadata("allow", database=resource)
            if database_allow is None:
                return None
            return actor_matches_allow(actor, database_allow)
        elif action == "view-table":
            database, table = resource
            tables = datasette.metadata("tables", database=database) or {}
            table_allow = (tables.get(table) or {}).get("allow")
            if table_allow is None:
                return None
            return actor_matches_allow(actor, table_allow)
        elif action == "view-query":
            # Check if this query has a "allow" block in metadata
            database, query_name = resource
            query = await datasette.get_canned_query(database, query_name, actor)
            assert query is not None
            allow = query.get("allow")
            if allow is None:
                return None
            return actor_matches_allow(actor, allow)
        elif action == "execute-sql":
            # Use allow_sql block from database block, or from top-level
            database_allow_sql = datasette.metadata("allow_sql", database=resource)
            if database_allow_sql is None:
                database_allow_sql = datasette.metadata("allow_sql")
            if database_allow_sql is None:
                return None
            return actor_matches_allow(actor, database_allow_sql)

    return inner


@hookimpl(specname="permission_allowed")
def permission_allowed_actor_restrictions(actor, action, resource):
    if actor is None:
        return None
    if "_r" not in actor:
        # No restrictions, so we have no opinion
        return None
    _r = actor.get("_r")
    action_initials = "".join([word[0] for word in action.split("-")])
    # If _r is defined then we use those to further restrict the actor
    # Crucially, we only use this to say NO (return False) - we never
    # use it to return YES (True) because that might over-ride other
    # restrictions placed on this actor
    all_allowed = _r.get("a")
    if all_allowed is not None:
        assert isinstance(all_allowed, list)
        if action_initials in all_allowed:
            return None
    # How about for the current database?
    if action in ("view-database", "view-database-download", "execute-sql"):
        database_allowed = _r.get("d", {}).get(resource)
        if database_allowed is not None:
            assert isinstance(database_allowed, list)
            if action_initials in database_allowed:
                return None
    # Or the current table? That's any time the resource is (database, table)
    if not isinstance(resource, str) and len(resource) == 2:
        database, table = resource
        table_allowed = _r.get("t", {}).get(database, {}).get(table)
        # TODO: What should this do for canned queries?
        if table_allowed is not None:
            assert isinstance(table_allowed, list)
            if action_initials in table_allowed:
                return None
    # This action is not specifically allowed, so reject it
    return False


@hookimpl
def actor_from_request(datasette, request):
    prefix = "dstok_"
    if not datasette.setting("allow_signed_tokens"):
        return None
    max_signed_tokens_ttl = datasette.setting("max_signed_tokens_ttl")
    authorization = request.headers.get("authorization")
    if not authorization:
        return None
    if not authorization.startswith("Bearer "):
        return None
    token = authorization[len("Bearer ") :]
    if not token.startswith(prefix):
        return None
    token = token[len(prefix) :]
    try:
        decoded = datasette.unsign(token, namespace="token")
    except itsdangerous.BadSignature:
        return None
    if "t" not in decoded:
        # Missing timestamp
        return None
    created = decoded["t"]
    if not isinstance(created, int):
        # Invalid timestamp
        return None
    duration = decoded.get("d")
    if duration is not None and not isinstance(duration, int):
        # Invalid duration
        return None
    if (duration is None and max_signed_tokens_ttl) or (
        duration is not None
        and max_signed_tokens_ttl
        and duration > max_signed_tokens_ttl
    ):
        duration = max_signed_tokens_ttl
    if duration:
        if time.time() - created > duration:
            # Expired
            return None
    actor = {"id": decoded["a"], "token": "dstok"}
    if duration:
        actor["token_expires"] = created + duration
    return actor


@hookimpl
def register_commands(cli):
    from datasette.app import Datasette

    @cli.command()
    @click.argument("id")
    @click.option(
        "--secret",
        help="Secret used for signing the API tokens",
        envvar="DATASETTE_SECRET",
        required=True,
    )
    @click.option(
        "-e",
        "--expires-after",
        help="Token should expire after this many seconds",
        type=int,
    )
    @click.option(
        "--debug",
        help="Show decoded token",
        is_flag=True,
    )
    def create_token(id, secret, expires_after, debug):
        "Create a signed API token for the specified actor ID"
        ds = Datasette(secret=secret)
        bits = {"a": id, "token": "dstok", "t": int(time.time())}
        if expires_after:
            bits["d"] = expires_after
        token = ds.sign(bits, namespace="token")
        click.echo("dstok_{}".format(token))
        if debug:
            click.echo("\nDecoded:\n")
            click.echo(json.dumps(ds.unsign(token, namespace="token"), indent=2))


@hookimpl
def skip_csrf(scope):
    # Skip CSRF check for requests with content-type: application/json
    if scope["type"] == "http":
        headers = scope.get("headers") or {}
        if dict(headers).get(b"content-type") == b"application/json":
            return True
