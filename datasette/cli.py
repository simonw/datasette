import asyncio
import uvicorn
import click
from click import formatting
from click.types import CompositeParamType
from click_default_group import DefaultGroup
import functools
import json
import os
import pathlib
from runpy import run_module
import shutil
from subprocess import call
import sys
import textwrap
import webbrowser
from .app import (
    Datasette,
    DEFAULT_SETTINGS,
    SETTINGS,
    SQLITE_LIMIT_ATTACHED,
    pm,
)
from .utils import (
    LoadExtension,
    StartupError,
    check_connection,
    find_spatialite,
    parse_metadata,
    ConnectionProblem,
    SpatialiteConnectionProblem,
    initial_path_for_datasette,
    pairs_to_nested_config,
    temporary_docker_directory,
    value_as_boolean,
    SpatialiteNotFound,
    StaticMount,
    ValueAsBooleanError,
)
from .utils.sqlite import sqlite3
from .utils.testing import TestClient
from .version import __version__

# Use Rich for tracebacks if it is installed
try:
    from rich.traceback import install

    install(show_locals=True)
except ImportError:
    pass


class Setting(CompositeParamType):
    name = "setting"
    arity = 2

    def convert(self, config, param, ctx):
        name, value = config
        if name in DEFAULT_SETTINGS:
            # For backwards compatibility with how this worked prior to
            # Datasette 1.0, we turn bare setting names into setting.name
            # Type checking for those older settings
            default = DEFAULT_SETTINGS[name]
            name = "settings.{}".format(name)
            if isinstance(default, bool):
                try:
                    return name, "true" if value_as_boolean(value) else "false"
                except ValueAsBooleanError:
                    self.fail(f'"{name}" should be on/off/true/false/1/0', param, ctx)
            elif isinstance(default, int):
                if not value.isdigit():
                    self.fail(f'"{name}" should be an integer', param, ctx)
                return name, value
            elif isinstance(default, str):
                return name, value
            else:
                # Should never happen:
                self.fail("Invalid option")
        return name, value


def sqlite_extensions(fn):
    fn = click.option(
        "sqlite_extensions",
        "--load-extension",
        type=LoadExtension(),
        envvar="SQLITE_EXTENSIONS",
        multiple=True,
        help="Path to a SQLite extension to load, and optional entrypoint",
    )(fn)

    # Wrap it in a custom error handler
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except AttributeError as e:
            if "enable_load_extension" in str(e):
                raise click.ClickException(
                    textwrap.dedent(
                        """
                    Your Python installation does not have the ability to load SQLite extensions.

                    More information: https://datasette.io/help/extensions
                    """
                    ).strip()
                )
            raise

    return wrapped


@click.group(cls=DefaultGroup, default="serve", default_if_no_args=True)
@click.version_option(version=__version__)
def cli():
    """
    Datasette is an open source multi-tool for exploring and publishing data

    \b
    About Datasette: https://datasette.io/
    Full documentation: https://docs.datasette.io/
    """


@cli.command()
@click.argument("files", type=click.Path(exists=True), nargs=-1)
@click.option("--inspect-file", default="-")
@sqlite_extensions
def inspect(files, inspect_file, sqlite_extensions):
    """
    Generate JSON summary of provided database files

    This can then be passed to "datasette --inspect-file" to speed up count
    operations against immutable database files.
    """
    app = Datasette([], immutables=files, sqlite_extensions=sqlite_extensions)
    loop = asyncio.get_event_loop()
    inspect_data = loop.run_until_complete(inspect_(files, sqlite_extensions))
    if inspect_file == "-":
        sys.stdout.write(json.dumps(inspect_data, indent=2))
    else:
        with open(inspect_file, "w") as fp:
            fp.write(json.dumps(inspect_data, indent=2))


async def inspect_(files, sqlite_extensions):
    app = Datasette([], immutables=files, sqlite_extensions=sqlite_extensions)
    data = {}
    for name, database in app.databases.items():
        counts = await database.table_counts(limit=3600 * 1000)
        data[name] = {
            "hash": database.hash,
            "size": database.size,
            "file": database.path,
            "tables": {
                table_name: {"count": table_count}
                for table_name, table_count in counts.items()
            },
        }
    return data


@cli.group()
def publish():
    """Publish specified SQLite database files to the internet along with a Datasette-powered interface and API"""
    pass


# Register publish plugins
pm.hook.publish_subcommand(publish=publish)


@cli.command()
@click.option("--all", help="Include built-in default plugins", is_flag=True)
@click.option(
    "--requirements", help="Output requirements.txt of installed plugins", is_flag=True
)
@click.option(
    "--plugins-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Path to directory containing custom plugins",
)
def plugins(all, requirements, plugins_dir):
    """List currently installed plugins"""
    app = Datasette([], plugins_dir=plugins_dir)
    if requirements:
        for plugin in app._plugins():
            if plugin["version"]:
                click.echo("{}=={}".format(plugin["name"], plugin["version"]))
    else:
        click.echo(json.dumps(app._plugins(all=all), indent=4))


@cli.command()
@click.argument("files", type=click.Path(exists=True), nargs=-1, required=True)
@click.option(
    "-t",
    "--tag",
    help="Name for the resulting Docker container, can optionally use name:tag format",
)
@click.option(
    "-m",
    "--metadata",
    type=click.File(mode="r"),
    help="Path to JSON/YAML file containing metadata to publish",
)
@click.option("--extra-options", help="Extra options to pass to datasette serve")
@click.option("--branch", help="Install datasette from a GitHub branch e.g. main")
@click.option(
    "--template-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Path to directory containing custom templates",
)
@click.option(
    "--plugins-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Path to directory containing custom plugins",
)
@click.option(
    "--static",
    type=StaticMount(),
    help="Serve static files from this directory at /MOUNT/...",
    multiple=True,
)
@click.option(
    "--install", help="Additional packages (e.g. plugins) to install", multiple=True
)
@click.option("--spatialite", is_flag=True, help="Enable SpatialLite extension")
@click.option("--version-note", help="Additional note to show on /-/versions")
@click.option(
    "--secret",
    help="Secret used for signing secure values, such as signed cookies",
    envvar="DATASETTE_PUBLISH_SECRET",
    default=lambda: os.urandom(32).hex(),
)
@click.option(
    "-p",
    "--port",
    default=8001,
    type=click.IntRange(1, 65535),
    help="Port to run the server on, defaults to 8001",
)
@click.option("--title", help="Title for metadata")
@click.option("--license", help="License label for metadata")
@click.option("--license_url", help="License URL for metadata")
@click.option("--source", help="Source label for metadata")
@click.option("--source_url", help="Source URL for metadata")
@click.option("--about", help="About label for metadata")
@click.option("--about_url", help="About URL for metadata")
def package(
    files,
    tag,
    metadata,
    extra_options,
    branch,
    template_dir,
    plugins_dir,
    static,
    install,
    spatialite,
    version_note,
    secret,
    port,
    **extra_metadata,
):
    """Package SQLite files into a Datasette Docker container"""
    if not shutil.which("docker"):
        click.secho(
            ' The package command requires "docker" to be installed and configured ',
            bg="red",
            fg="white",
            bold=True,
            err=True,
        )
        sys.exit(1)
    with temporary_docker_directory(
        files,
        "datasette",
        metadata=metadata,
        extra_options=extra_options,
        branch=branch,
        template_dir=template_dir,
        plugins_dir=plugins_dir,
        static=static,
        install=install,
        spatialite=spatialite,
        version_note=version_note,
        secret=secret,
        extra_metadata=extra_metadata,
        port=port,
    ):
        args = ["docker", "build"]
        if tag:
            args.append("-t")
            args.append(tag)
        args.append(".")
        call(args)


@cli.command()
@click.argument("packages", nargs=-1)
@click.option(
    "-U", "--upgrade", is_flag=True, help="Upgrade packages to latest version"
)
@click.option(
    "-r",
    "--requirement",
    type=click.Path(exists=True),
    help="Install from requirements file",
)
@click.option(
    "-e",
    "--editable",
    help="Install a project in editable mode from this path",
)
def install(packages, upgrade, requirement, editable):
    """Install plugins and packages from PyPI into the same environment as Datasette"""
    if not packages and not requirement and not editable:
        raise click.UsageError("Please specify at least one package to install")
    args = ["pip", "install"]
    if upgrade:
        args += ["--upgrade"]
    if editable:
        args += ["--editable", editable]
    if requirement:
        args += ["-r", requirement]
    args += list(packages)
    sys.argv = args
    run_module("pip", run_name="__main__")


@cli.command()
@click.argument("packages", nargs=-1, required=True)
@click.option("-y", "--yes", is_flag=True, help="Don't ask for confirmation")
def uninstall(packages, yes):
    """Uninstall plugins and Python packages from the Datasette environment"""
    sys.argv = ["pip", "uninstall"] + list(packages) + (["-y"] if yes else [])
    run_module("pip", run_name="__main__")


@cli.command()
@click.argument("files", type=click.Path(), nargs=-1)
@click.option(
    "-i",
    "--immutable",
    type=click.Path(exists=True),
    help="Database files to open in immutable mode",
    multiple=True,
)
@click.option(
    "-h",
    "--host",
    default="127.0.0.1",
    help=(
        "Host for server. Defaults to 127.0.0.1 which means only connections "
        "from the local machine will be allowed. Use 0.0.0.0 to listen to "
        "all IPs and allow access from other machines."
    ),
)
@click.option(
    "-p",
    "--port",
    default=8001,
    type=click.IntRange(0, 65535),
    help="Port for server, defaults to 8001. Use -p 0 to automatically assign an available port.",
)
@click.option(
    "--uds",
    help="Bind to a Unix domain socket",
)
@click.option(
    "--reload",
    is_flag=True,
    help="Automatically reload if code or metadata change detected - useful for development",
)
@click.option(
    "--cors", is_flag=True, help="Enable CORS by serving Access-Control-Allow-Origin: *"
)
@sqlite_extensions
@click.option(
    "--inspect-file", help='Path to JSON file created using "datasette inspect"'
)
@click.option(
    "-m",
    "--metadata",
    type=click.File(mode="r"),
    help="Path to JSON/YAML file containing license/source metadata",
)
@click.option(
    "--template-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Path to directory containing custom templates",
)
@click.option(
    "--plugins-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Path to directory containing custom plugins",
)
@click.option(
    "--static",
    type=StaticMount(),
    help="Serve static files from this directory at /MOUNT/...",
    multiple=True,
)
@click.option("--memory", is_flag=True, help="Make /_memory database available")
@click.option(
    "-c",
    "--config",
    type=click.File(mode="r"),
    help="Path to JSON/YAML Datasette configuration file",
)
@click.option(
    "-s",
    "--setting",
    "settings",
    type=Setting(),
    help="nested.key, value setting to use in Datasette configuration",
    multiple=True,
)
@click.option(
    "--secret",
    help="Secret used for signing secure values, such as signed cookies",
    envvar="DATASETTE_SECRET",
)
@click.option(
    "--root",
    help="Output URL that sets a cookie authenticating the root user",
    is_flag=True,
)
@click.option(
    "--get",
    help="Run an HTTP GET request against this path, print results and exit",
)
@click.option(
    "--token",
    help="API token to send with --get requests",
)
@click.option(
    "--actor",
    help="Actor to use for --get requests (JSON string)",
)
@click.option("--version-note", help="Additional note to show on /-/versions")
@click.option("--help-settings", is_flag=True, help="Show available settings")
@click.option("--pdb", is_flag=True, help="Launch debugger on any errors")
@click.option(
    "-o",
    "--open",
    "open_browser",
    is_flag=True,
    help="Open Datasette in your web browser",
)
@click.option(
    "--create",
    is_flag=True,
    help="Create database files if they do not exist",
)
@click.option(
    "--crossdb",
    is_flag=True,
    help="Enable cross-database joins using the /_memory database",
)
@click.option(
    "--nolock",
    is_flag=True,
    help="Ignore locking, open locked files in read-only mode",
)
@click.option(
    "--ssl-keyfile",
    help="SSL key file",
)
@click.option(
    "--ssl-certfile",
    help="SSL certificate file",
)
@click.option(
    "--internal",
    type=click.Path(),
    help="Path to a persistent Datasette internal SQLite database",
)
def serve(
    files,
    immutable,
    host,
    port,
    uds,
    reload,
    cors,
    sqlite_extensions,
    inspect_file,
    metadata,
    template_dir,
    plugins_dir,
    static,
    memory,
    config,
    settings,
    secret,
    root,
    get,
    token,
    actor,
    version_note,
    help_settings,
    pdb,
    open_browser,
    create,
    crossdb,
    nolock,
    ssl_keyfile,
    ssl_certfile,
    internal,
    return_instance=False,
):
    """Serve up specified SQLite database files with a web UI"""
    if help_settings:
        formatter = formatting.HelpFormatter()
        with formatter.section("Settings"):
            formatter.write_dl(
                [
                    (option.name, f"{option.help} (default={option.default})")
                    for option in SETTINGS
                ]
            )
        click.echo(formatter.getvalue())
        sys.exit(0)
    if reload:
        import hupper

        reloader = hupper.start_reloader("datasette.cli.serve")
        if immutable:
            reloader.watch_files(immutable)
        if config:
            reloader.watch_files([config.name])
        if metadata:
            reloader.watch_files([metadata.name])

    inspect_data = None
    if inspect_file:
        with open(inspect_file) as fp:
            inspect_data = json.load(fp)

    metadata_data = None
    if metadata:
        metadata_data = parse_metadata(metadata.read())

    config_data = None
    if config:
        config_data = parse_metadata(config.read())

    config_data = config_data or {}

    # Merge in settings from -s/--setting
    if settings:
        settings_updates = pairs_to_nested_config(settings)
        config_data.update(settings_updates)

    kwargs = dict(
        immutables=immutable,
        cache_headers=not reload,
        cors=cors,
        inspect_data=inspect_data,
        config=config_data,
        metadata=metadata_data,
        sqlite_extensions=sqlite_extensions,
        template_dir=template_dir,
        plugins_dir=plugins_dir,
        static_mounts=static,
        settings=None,  # These are passed in config= now
        memory=memory,
        secret=secret,
        version_note=version_note,
        pdb=pdb,
        crossdb=crossdb,
        nolock=nolock,
        internal=internal,
    )

    # if files is a single directory, use that as config_dir=
    if 1 == len(files) and os.path.isdir(files[0]):
        kwargs["config_dir"] = pathlib.Path(files[0])
        files = []

    # Verify list of files, create if needed (and --create)
    for file in files:
        if not pathlib.Path(file).exists():
            if create:
                sqlite3.connect(file).execute("vacuum")
            else:
                raise click.ClickException(
                    "Invalid value for '[FILES]...': Path '{}' does not exist.".format(
                        file
                    )
                )

    # De-duplicate files so 'datasette db.db db.db' only attaches one /db
    files = list(dict.fromkeys(files))

    try:
        ds = Datasette(files, **kwargs)
    except SpatialiteNotFound:
        raise click.ClickException("Could not find SpatiaLite extension")
    except StartupError as e:
        raise click.ClickException(e.args[0])

    if return_instance:
        # Private utility mechanism for writing unit tests
        return ds

    # Run the "startup" plugin hooks
    asyncio.get_event_loop().run_until_complete(ds.invoke_startup())

    # Run async soundness checks - but only if we're not under pytest
    asyncio.get_event_loop().run_until_complete(check_databases(ds))

    if token and not get:
        raise click.ClickException("--token can only be used with --get")

    if get:
        client = TestClient(ds)
        headers = {}
        if token:
            headers["Authorization"] = "Bearer {}".format(token)
        cookies = {}
        if actor:
            cookies["ds_actor"] = client.actor_cookie(json.loads(actor))
        response = client.get(get, headers=headers, cookies=cookies)
        click.echo(response.text)
        exit_code = 0 if response.status == 200 else 1
        sys.exit(exit_code)
        return

    # Start the server
    url = None
    if root:
        url = "http://{}:{}{}?token={}".format(
            host, port, ds.urls.path("-/auth-token"), ds._root_token
        )
        click.echo(url)
    if open_browser:
        if url is None:
            # Figure out most convenient URL - to table, database or homepage
            path = asyncio.get_event_loop().run_until_complete(
                initial_path_for_datasette(ds)
            )
            url = f"http://{host}:{port}{path}"
        webbrowser.open(url)
    uvicorn_kwargs = dict(
        host=host, port=port, log_level="info", lifespan="on", workers=1
    )
    if uds:
        uvicorn_kwargs["uds"] = uds
    if ssl_keyfile:
        uvicorn_kwargs["ssl_keyfile"] = ssl_keyfile
    if ssl_certfile:
        uvicorn_kwargs["ssl_certfile"] = ssl_certfile
    uvicorn.run(ds.app(), **uvicorn_kwargs)


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
    "alls",
    "-a",
    "--all",
    type=str,
    metavar="ACTION",
    multiple=True,
    help="Restrict token to this action",
)
@click.option(
    "databases",
    "-d",
    "--database",
    type=(str, str),
    metavar="DB ACTION",
    multiple=True,
    help="Restrict token to this action on this database",
)
@click.option(
    "resources",
    "-r",
    "--resource",
    type=(str, str, str),
    metavar="DB RESOURCE ACTION",
    multiple=True,
    help="Restrict token to this action on this database resource (a table, SQL view or named query)",
)
@click.option(
    "--debug",
    help="Show decoded token",
    is_flag=True,
)
@click.option(
    "--plugins-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Path to directory containing custom plugins",
)
def create_token(
    id, secret, expires_after, alls, databases, resources, debug, plugins_dir
):
    """
    Create a signed API token for the specified actor ID

    Example:

        datasette create-token root --secret mysecret

    To allow only "view-database-download" for all databases:

    \b
        datasette create-token root --secret mysecret \\
            --all view-database-download

    To allow "create-table" against a specific database:

    \b
        datasette create-token root --secret mysecret \\
            --database mydb create-table

    To allow "insert-row" against a specific table:

    \b
        datasette create-token root --secret myscret \\
            --resource mydb mytable insert-row

    Restricted actions can be specified multiple times using
    multiple --all, --database, and --resource options.

    Add --debug to see a decoded version of the token.
    """
    ds = Datasette(secret=secret, plugins_dir=plugins_dir)

    # Run ds.invoke_startup() in an event loop
    loop = asyncio.get_event_loop()
    loop.run_until_complete(ds.invoke_startup())

    # Warn about any unknown actions
    actions = []
    actions.extend(alls)
    actions.extend([p[1] for p in databases])
    actions.extend([p[2] for p in resources])
    for action in actions:
        if not ds.permissions.get(action):
            click.secho(
                f"  Unknown permission: {action} ",
                fg="red",
                err=True,
            )

    restrict_database = {}
    for database, action in databases:
        restrict_database.setdefault(database, []).append(action)
    restrict_resource = {}
    for database, resource, action in resources:
        restrict_resource.setdefault(database, {}).setdefault(resource, []).append(
            action
        )

    token = ds.create_token(
        id,
        expires_after=expires_after,
        restrict_all=alls,
        restrict_database=restrict_database,
        restrict_resource=restrict_resource,
    )
    click.echo(token)
    if debug:
        encoded = token[len("dstok_") :]
        click.echo("\nDecoded:\n")
        click.echo(json.dumps(ds.unsign(encoded, namespace="token"), indent=2))


pm.hook.register_commands(cli=cli)


async def check_databases(ds):
    # Run check_connection against every connected database
    # to confirm they are all usable
    for database in list(ds.databases.values()):
        try:
            await database.execute_fn(check_connection)
        except SpatialiteConnectionProblem:
            suggestion = ""
            try:
                find_spatialite()
                suggestion = "\n\nTry adding the --load-extension=spatialite option."
            except SpatialiteNotFound:
                pass
            raise click.UsageError(
                "It looks like you're trying to load a SpatiaLite"
                + " database without first loading the SpatiaLite module."
                + suggestion
                + "\n\nRead more: https://docs.datasette.io/en/stable/spatialite.html"
            )
        except ConnectionProblem as e:
            raise click.UsageError(
                f"Connection to {database.path} failed check: {str(e.args[0])}"
            )
    # If --crossdb and more than SQLITE_LIMIT_ATTACHED show warning
    if (
        ds.crossdb
        and len([db for db in ds.databases.values() if not db.is_memory])
        > SQLITE_LIMIT_ATTACHED
    ):
        msg = (
            "Warning: --crossdb only works with the first {} attached databases".format(
                SQLITE_LIMIT_ATTACHED
            )
        )
        click.echo(click.style(msg, bold=True, fg="yellow"), err=True)
