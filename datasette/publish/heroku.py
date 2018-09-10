from datasette import hookimpl
import click
import json
from subprocess import call, check_output

from .common import (
    add_common_publish_arguments_and_options,
    fail_if_publish_binary_not_installed,
)
from ..utils import temporary_heroku_directory


@hookimpl
def publish_subcommand(publish):
    @publish.command()
    @add_common_publish_arguments_and_options
    @click.option(
        "-n",
        "--name",
        default="datasette",
        help="Application name to use when deploying",
    )
    def heroku(
        files,
        metadata,
        extra_options,
        branch,
        template_dir,
        plugins_dir,
        static,
        install,
        version_note,
        title,
        license,
        license_url,
        source,
        source_url,
        name,
    ):
        fail_if_publish_binary_not_installed(
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
            version_note,
            {
                "title": title,
                "license": license,
                "license_url": license_url,
                "source": source,
                "source_url": source_url,
            },
        ):
            app_name = None
            if name:
                # Check to see if this app already exists
                list_output = check_output(["heroku", "apps:list", "--json"]).decode(
                    "utf8"
                )
                apps = json.loads(list_output)

                for app in apps:
                    if app["name"] == name:
                        app_name = name
                        break

            if not app_name:
                # Create a new app
                cmd = ["heroku", "apps:create"]
                if name:
                    cmd.append(name)
                cmd.append("--json")
                create_output = check_output(cmd).decode("utf8")
                app_name = json.loads(create_output)["name"]

            call(["heroku", "builds:create", "-a", app_name])
