import asyncio
import click
from click import formatting
from click_default_group import DefaultGroup
import json
import os
import shutil
from subprocess import call
import sys
from .app import Datasette, DEFAULT_CONFIG, CONFIG_OPTIONS, pm
from .utils import (
    temporary_docker_directory,
    value_as_boolean,
    StaticMount,
    ValueAsBooleanError,
)


class Config(click.ParamType):
    name = "config"

    def convert(self, config, param, ctx):
        if ":" not in config:
            self.fail('"{}" should be name:value'.format(config), param, ctx)
            return
        name, value = config.split(":")
        if name not in DEFAULT_CONFIG:
            self.fail(
                "{} is not a valid option (--help-config to see all)".format(name),
                param,
                ctx,
            )
            return
        # Type checking
        default = DEFAULT_CONFIG[name]
        if isinstance(default, bool):
            try:
                return name, value_as_boolean(value)
            except ValueAsBooleanError:
                self.fail(
                    '"{}" should be on/off/true/false/1/0'.format(name), param, ctx
                )
                return
        elif isinstance(default, int):
            if not value.isdigit():
                self.fail('"{}" should be an integer'.format(name), param, ctx)
                return
            return name, int(value)
        else:
            # Should never happen:
            self.fail("Invalid option")


@click.group(cls=DefaultGroup, default="serve", default_if_no_args=True)
@click.version_option()
def cli():
    """
    Datasette!
    """


@cli.command()
@click.argument("files", type=click.Path(exists=True), nargs=-1)
@click.option("--inspect-file", default="-")
@click.option(
    "sqlite_extensions",
    "--load-extension",
    envvar="SQLITE_EXTENSIONS",
    multiple=True,
    type=click.Path(exists=True, resolve_path=True),
    help="Path to a SQLite extension to load",
)
def inspect(files, inspect_file, sqlite_extensions):
    app = Datasette([], immutables=files, sqlite_extensions=sqlite_extensions)
    if inspect_file == "-":
        out = sys.stdout
    else:
        out = open(inspect_file, "w")
    loop = asyncio.get_event_loop()
    inspect_data = loop.run_until_complete(inspect_(files, sqlite_extensions))
    out.write(json.dumps(inspect_data, indent=2))


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


class PublishAliases(click.Group):
    aliases = {"now": "nowv1"}

    def get_command(self, ctx, cmd_name):
        if cmd_name in self.aliases:
            return click.Group.get_command(self, ctx, self.aliases[cmd_name])
        return click.Group.get_command(self, ctx, cmd_name)


@cli.group(cls=PublishAliases)
def publish():
    "Publish specified SQLite database files to the internet along with a Datasette-powered interface and API"
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
    "List currently available plugins"
    app = Datasette([], plugins_dir=plugins_dir)
    click.echo(json.dumps(app.plugins(all), indent=4))


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
    help="Path to JSON file containing metadata to publish",
)
@click.option("--extra-options", help="Extra options to pass to datasette serve")
@click.option("--branch", help="Install datasette from a GitHub branch e.g. master")
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
    help="mountpoint:path-to-directory for serving static files",
    multiple=True,
)
@click.option(
    "--install", help="Additional packages (e.g. plugins) to install", multiple=True
)
@click.option("--spatialite", is_flag=True, help="Enable SpatialLite extension")
@click.option("--version-note", help="Additional note to show on /-/versions")
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
    **extra_metadata
):
    "Package specified SQLite files into a new datasette Docker container"
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
        metadata,
        extra_options,
        branch,
        template_dir,
        plugins_dir,
        static,
        install,
        spatialite,
        version_note,
        extra_metadata,
    ):
        args = ["docker", "build"]
        if tag:
            args.append("-t")
            args.append(tag)
        args.append(".")
        call(args)


@cli.command()
@click.argument("files", type=click.Path(exists=True), nargs=-1)
@click.option(
    "-i",
    "--immutable",
    type=click.Path(exists=True),
    help="Database files to open in immutable mode",
    multiple=True,
)
@click.option(
    "-h", "--host", default="127.0.0.1", help="host for server, defaults to 127.0.0.1"
)
@click.option("-p", "--port", default=8001, help="port for server, defaults to 8001")
@click.option(
    "--debug", is_flag=True, help="Enable debug mode - useful for development"
)
@click.option(
    "--reload",
    is_flag=True,
    help="Automatically reload if database or code change detected - useful for development",
)
@click.option(
    "--cors", is_flag=True, help="Enable CORS by serving Access-Control-Allow-Origin: *"
)
@click.option(
    "sqlite_extensions",
    "--load-extension",
    envvar="SQLITE_EXTENSIONS",
    multiple=True,
    type=click.Path(exists=True, resolve_path=True),
    help="Path to a SQLite extension to load",
)
@click.option(
    "--inspect-file", help='Path to JSON file created using "datasette inspect"'
)
@click.option(
    "-m",
    "--metadata",
    type=click.File(mode="r"),
    help="Path to JSON file containing license/source metadata",
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
    help="mountpoint:path-to-directory for serving static files",
    multiple=True,
)
@click.option("--memory", is_flag=True, help="Make :memory: database available")
@click.option(
    "--config",
    type=Config(),
    help="Set config option using configname:value datasette.readthedocs.io/en/latest/config.html",
    multiple=True,
)
@click.option("--version-note", help="Additional note to show on /-/versions")
@click.option("--help-config", is_flag=True, help="Show available config options")
def serve(
    files,
    immutable,
    host,
    port,
    debug,
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
    version_note,
    help_config,
):
    """Serve up specified SQLite database files with a web UI"""
    if help_config:
        formatter = formatting.HelpFormatter()
        with formatter.section("Config options"):
            formatter.write_dl(
                [
                    (option.name, "{} (default={})".format(option.help, option.default))
                    for option in CONFIG_OPTIONS
                ]
            )
        click.echo(formatter.getvalue())
        sys.exit(0)
    if reload:
        import hupper

        reloader = hupper.start_reloader("datasette.cli.serve")
        reloader.watch_files(files)
        if metadata:
            reloader.watch_files([metadata.name])

    inspect_data = None
    if inspect_file:
        inspect_data = json.load(open(inspect_file))

    metadata_data = None
    if metadata:
        metadata_data = json.loads(metadata.read())

    click.echo(
        "Serve! files={} (immutables={}) on port {}".format(files, immutable, port)
    )
    ds = Datasette(
        files,
        immutables=immutable,
        cache_headers=not debug and not reload,
        cors=cors,
        inspect_data=inspect_data,
        metadata=metadata_data,
        sqlite_extensions=sqlite_extensions,
        template_dir=template_dir,
        plugins_dir=plugins_dir,
        static_mounts=static,
        config=dict(config),
        memory=memory,
        version_note=version_note,
    )
    # Run async sanity checks - but only if we're not under pytest
    asyncio.get_event_loop().run_until_complete(ds.run_sanity_checks())

    # Start the server
    ds.app().run(host=host, port=port, debug=debug)
