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
import shutil
from subprocess import call
import sys
from runpy import run_module
import textwrap
import webbrowser
from .app import (
    OBSOLETE_SETTINGS,
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


class Config(click.ParamType):
    # This will be removed in Datasette 1.0 in favour of class Setting
    name = "config"

    def convert(self, config, param, ctx):
        if ":" not in config:
            self.fail(f'"{config}" should be name:value', param, ctx)
            return
        name, value = config.split(":", 1)
        if name not in DEFAULT_SETTINGS:
            msg = (
                OBSOLETE_SETTINGS.get(name)
                or f"{name} is not a valid option (--help-settings to see all)"
            )
            self.fail(
                msg,
                param,
                ctx,
            )
            return
        # Type checking
        default = DEFAULT_SETTINGS[name]
        if isinstance(default, bool):
            try:
                return name, value_as_boolean(value)
            except ValueAsBooleanError:
                self.fail(f'"{name}" should be on/off/true/false/1/0', param, ctx)
                return
        elif isinstance(default, int):
            if not value.isdigit():
                self.fail(f'"{name}" should be an integer', param, ctx)
                return
            return name, int(value)
        elif isinstance(default, str):
            return name, value
        else:
            # Should never happen:
            self.fail("Invalid option")


class Setting(CompositeParamType):
    name = "setting"
    arity = 2

    def convert(self, config, param, ctx):
        name, value = config
        if name not in DEFAULT_SETTINGS:
            msg = (
                OBSOLETE_SETTINGS.get(name)
                or f"{name} is not a valid option (--help-settings to see all)"
            )
            self.fail(
                msg,
                param,
                ctx,
            )
            return
        # Type checking
        default = DEFAULT_SETTINGS[name]
        if isinstance(default, bool):
            try:
                return name, value_as_boolean(value)
            except ValueAsBooleanError:
                self.fail(f'"{name}" should be on/off/true/false/1/0', param, ctx)
                return
        elif isinstance(default, int):
            if not value.isdigit():
                self.fail(f'"{name}" should be an integer', param, ctx)
                return
            return name, int(value)
        elif isinstance(default, str):
            return name, value
        else:
            # Should never happen:
            self.fail("Invalid option")


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
        if name == "_internal":
            # Don't include the in-memory _internal database
            continue
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
    "--plugins-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Path to directory containing custom plugins",
)
def plugins(all, plugins_dir):
    """List currently installed plugins"""
    app = Datasette([], plugins_dir=plugins_dir)
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
@click.argument("packages", nargs=-1, required=True)
@click.option(
    "-U", "--upgrade", is_flag=True, help="Upgrade packages to latest version"
)
def install(packages, upgrade):
    """Install plugins and packages from PyPI into the same environment as Datasette"""
    args = ["pip", "install"]
    if upgrade:
        args += ["--upgrade"]
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
    "--config",
    type=Config(),
    help="Deprecated: set config option using configname:value. Use --setting instead.",
    multiple=True,
)
@click.option(
    "--setting",
    "settings",
    type=Setting(),
    help="Setting, see docs.datasette.io/en/stable/settings.html",
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
    version_note,
    help_settings,
    pdb,
    open_browser,
    create,
    crossdb,
    nolock,
    ssl_keyfile,
    ssl_certfile,
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
        if metadata:
            reloader.watch_files([metadata.name])

    inspect_data = None
    if inspect_file:
        with open(inspect_file) as fp:
            inspect_data = json.load(fp)

    metadata_data = None
    if metadata:
        metadata_data = parse_metadata(metadata.read())

    combined_settings = {}
    if config:
        click.echo(
            "--config name:value will be deprecated in Datasette 1.0, use --setting name value instead",
            err=True,
        )
        combined_settings.update(config)
    combined_settings.update(settings)

    kwargs = dict(
        immutables=immutable,
        cache_headers=not reload,
        cors=cors,
        inspect_data=inspect_data,
        metadata=metadata_data,
        sqlite_extensions=sqlite_extensions,
        template_dir=template_dir,
        plugins_dir=plugins_dir,
        static_mounts=static,
        settings=combined_settings,
        memory=memory,
        secret=secret,
        version_note=version_note,
        pdb=pdb,
        crossdb=crossdb,
        nolock=nolock,
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

    if get:
        client = TestClient(ds)
        response = client.get(get)
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
