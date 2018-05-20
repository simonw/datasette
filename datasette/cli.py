import click
from click import formatting
from click_default_group import DefaultGroup
import json
import os
import shutil
from subprocess import call, check_output
import sys
from .app import Datasette, DEFAULT_CONFIG, CONFIG_OPTIONS
from .utils import temporary_docker_directory, temporary_heroku_directory


class StaticMount(click.ParamType):
    name = "static mount"

    def convert(self, value, param, ctx):
        if ":" not in value:
            self.fail(
                '"{}" should be of format mountpoint:directory'.format(value),
                param, ctx
            )
        path, dirpath = value.split(":")
        if not os.path.exists(dirpath) or not os.path.isdir(dirpath):
            self.fail("%s is not a valid directory path" % value, param, ctx)
        return path, dirpath


class Config(click.ParamType):
    name = "config"

    def convert(self, value, param, ctx):
        ok = True
        if ":" not in value:
            ok = False
        else:
            name, intvalue = value.split(":")
            ok = intvalue.isdigit()
        if not ok:
            self.fail(
                '"{}" should be of format name:integer'.format(value),
                param, ctx
            )
        if name not in DEFAULT_CONFIG:
            self.fail("{} is not a valid limit".format(name), param, ctx)
        return name, int(intvalue)


@click.group(cls=DefaultGroup, default="serve", default_if_no_args=True)
@click.version_option()
def cli():
    """
    Datasette!
    """


@cli.command()
@click.argument("files", type=click.Path(exists=True), nargs=-1)
@click.option("--inspect-file", default="inspect-data.json")
@click.option(
    "sqlite_extensions",
    "--load-extension",
    envvar="SQLITE_EXTENSIONS",
    multiple=True,
    type=click.Path(exists=True, resolve_path=True),
    help="Path to a SQLite extension to load",
)
def inspect(files, inspect_file, sqlite_extensions):
    app = Datasette(files, sqlite_extensions=sqlite_extensions)
    open(inspect_file, "w").write(json.dumps(app.inspect(), indent=2))


@cli.command()
@click.argument("publisher", type=click.Choice(["now", "heroku"]))
@click.argument("files", type=click.Path(exists=True), nargs=-1)
@click.option(
    "-n",
    "--name",
    default="datasette",
    help="Application name to use when deploying to Now (ignored for Heroku)",
)
@click.option(
    "-m",
    "--metadata",
    type=click.File(mode="r"),
    help="Path to JSON file containing metadata to publish",
)
@click.option("--extra-options", help="Extra options to pass to datasette serve")
@click.option("--force", is_flag=True, help="Pass --force option to now")
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
    "--install",
    help="Additional packages (e.g. plugins) to install",
    multiple=True,
)
@click.option("--title", help="Title for metadata")
@click.option("--license", help="License label for metadata")
@click.option("--license_url", help="License URL for metadata")
@click.option("--source", help="Source label for metadata")
@click.option("--source_url", help="Source URL for metadata")
def publish(
    publisher,
    files,
    name,
    metadata,
    extra_options,
    force,
    branch,
    template_dir,
    plugins_dir,
    static,
    install,
    **extra_metadata
):
    """
    Publish specified SQLite database files to the internet along with a datasette API.

    Options for PUBLISHER:
        * 'now' - You must have Zeit Now installed: https://zeit.co/now
        * 'heroku' - You must have Heroku installed: https://cli.heroku.com/

    Example usage: datasette publish now my-database.db
    """

    def _fail_if_publish_binary_not_installed(binary, publish_target, install_link):
        """Exit (with error message) if ``binary` isn't installed"""
        if not shutil.which(binary):
            click.secho(
                "Publishing to {publish_target} requires {binary} to be installed and configured".format(
                    publish_target=publish_target, binary=binary
                ),
                bg="red",
                fg="white",
                bold=True,
                err=True,
            )
            click.echo(
                "Follow the instructions at {install_link}".format(
                    install_link=install_link
                ),
                err=True,
            )
            sys.exit(1)

    if publisher == "now":
        _fail_if_publish_binary_not_installed("now", "Zeit Now", "https://zeit.co/now")
        with temporary_docker_directory(
            files,
            name,
            metadata,
            extra_options,
            branch,
            template_dir,
            plugins_dir,
            static,
            install,
            extra_metadata,
        ):
            if force:
                call(["now", "--force"])
            else:
                call("now")

    elif publisher == "heroku":
        _fail_if_publish_binary_not_installed(
            "heroku", "Heroku", "https://cli.heroku.com"
        )

        # Check for heroku-builds plugin
        plugins = [
            line.split()[0] for line in check_output(["heroku", "plugins"]).splitlines()
        ]
        if b"heroku-builds" not in plugins:
            click.echo(
                "Publishing to Heroku requires the heroku-builds plugin to be installed."
            )
            click.confirm(
                "Install it? (this will run `heroku plugins:install heroku-builds`)",
                abort=True,
            )
            call(["heroku", "plugins:install", "heroku-builds"])

        with temporary_heroku_directory(
            files,
            name,
            metadata,
            extra_options,
            branch,
            template_dir,
            plugins_dir,
            static,
            install,
            extra_metadata,
        ):
            create_output = check_output(["heroku", "apps:create", "--json"]).decode(
                "utf8"
            )
            app_name = json.loads(create_output)["name"]
            call(["heroku", "builds:create", "-a", app_name])


@cli.command()
@click.argument("files", type=click.Path(exists=True), nargs=-1, required=True)
@click.option(
    "-m",
    "--metadata",
    default="metadata.json",
    help="Name of metadata file to generate",
)
@click.option(
    "sqlite_extensions",
    "--load-extension",
    envvar="SQLITE_EXTENSIONS",
    multiple=True,
    type=click.Path(exists=True, resolve_path=True),
    help="Path to a SQLite extension to load",
)
def skeleton(files, metadata, sqlite_extensions):
    "Generate a skeleton metadata.json file for specified SQLite databases"
    if os.path.exists(metadata):
        click.secho(
            "File {} already exists, will not over-write".format(metadata),
            bg="red",
            fg="white",
            bold=True,
            err=True,
        )
        sys.exit(1)
    app = Datasette(files, sqlite_extensions=sqlite_extensions)
    databases = {}
    for database_name, info in app.inspect().items():
        databases[database_name] = {
            "title": None,
            "description": None,
            "description_html": None,
            "license": None,
            "license_url": None,
            "source": None,
            "source_url": None,
            "queries": {},
            "tables": {
                table_name: {
                    "title": None,
                    "description": None,
                    "description_html": None,
                    "license": None,
                    "license_url": None,
                    "source": None,
                    "source_url": None,
                    "units": {},
                }
                for table_name in (info.get("tables") or {})
            },
        }
    open(metadata, "w").write(
        json.dumps(
            {
                "title": None,
                "description": None,
                "description_html": None,
                "license": None,
                "license_url": None,
                "source": None,
                "source_url": None,
                "databases": databases,
            },
            indent=4,
        )
    )
    click.echo("Wrote skeleton to {}".format(metadata))


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
    "--install",
    help="Additional packages (e.g. plugins) to install",
    multiple=True,
)
@click.option("--title", help="Title for metadata")
@click.option("--license", help="License label for metadata")
@click.option("--license_url", help="License URL for metadata")
@click.option("--source", help="Source label for metadata")
@click.option("--source_url", help="Source URL for metadata")
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
    "-h", "--host", default="127.0.0.1", help="host for server, defaults to 127.0.0.1"
)
@click.option("-p", "--port", default=8001, help="port for server, defaults to 8001")
@click.option(
    "--debug", is_flag=True, help="Enable debug mode - useful for development"
)
@click.option(
    "--reload",
    is_flag=True,
    help="Automatically reload if code change detected - useful for development",
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
@click.option(
    "--config",
    type=Config(),
    help="Set config option using configname:value datasette.readthedocs.io/en/latest/config.html",
    multiple=True,
)
@click.option(
    "--help-config",
    is_flag=True,
    help="Show available config options",
)
def serve(
    files,
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
    config,
    help_config,
):
    """Serve up specified SQLite database files with a web UI"""
    if help_config:
        formatter = formatting.HelpFormatter()
        with formatter.section("Config options"):
            formatter.write_dl([
                (option.name, '{} (default={})'.format(
                    option.help, option.default
                ))
                for option in CONFIG_OPTIONS
            ])
        click.echo(formatter.getvalue())
        sys.exit(0)
    if reload:
        import hupper

        reloader = hupper.start_reloader("datasette.cli.serve")
        if metadata:
            reloader.watch_files([metadata.name])

    inspect_data = None
    if inspect_file:
        inspect_data = json.load(open(inspect_file))

    metadata_data = None
    if metadata:
        metadata_data = json.loads(metadata.read())

    click.echo("Serve! files={} on port {}".format(files, port))
    ds = Datasette(
        files,
        cache_headers=not debug and not reload,
        cors=cors,
        inspect_data=inspect_data,
        metadata=metadata_data,
        sqlite_extensions=sqlite_extensions,
        template_dir=template_dir,
        plugins_dir=plugins_dir,
        static_mounts=static,
        config=dict(config),
    )
    # Force initial hashing/table counting
    ds.inspect()
    ds.app().run(host=host, port=port, debug=debug)
