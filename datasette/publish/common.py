from ..utils import StaticMount
import click
import os
import shutil
import sys


def add_common_publish_arguments_and_options(subcommand):
    for decorator in reversed(
        (
            click.argument("files", type=click.Path(exists=True), nargs=-1),
            click.option(
                "-m",
                "--metadata",
                type=click.File(mode="r"),
                help="Path to JSON/YAML file containing metadata to publish",
            ),
            click.option(
                "--extra-options", help="Extra options to pass to datasette serve"
            ),
            click.option(
                "--branch", help="Install datasette from a GitHub branch e.g. master"
            ),
            click.option(
                "--template-dir",
                type=click.Path(exists=True, file_okay=False, dir_okay=True),
                help="Path to directory containing custom templates",
            ),
            click.option(
                "--plugins-dir",
                type=click.Path(exists=True, file_okay=False, dir_okay=True),
                help="Path to directory containing custom plugins",
            ),
            click.option(
                "--static",
                type=StaticMount(),
                help="Serve static files from this directory at /MOUNT/...",
                multiple=True,
            ),
            click.option(
                "--install",
                help="Additional packages (e.g. plugins) to install",
                multiple=True,
            ),
            click.option(
                "--plugin-secret",
                nargs=3,
                type=(str, str, str),
                callback=validate_plugin_secret,
                multiple=True,
                help="Secrets to pass to plugins, e.g. --plugin-secret datasette-auth-github client_id xxx",
            ),
            click.option(
                "--version-note", help="Additional note to show on /-/versions"
            ),
            click.option(
                "--secret",
                help="Secret used for signing secure values, such as signed cookies",
                envvar="DATASETTE_PUBLISH_SECRET",
                default=lambda: os.urandom(32).hex(),
            ),
            click.option("--title", help="Title for metadata"),
            click.option("--license", help="License label for metadata"),
            click.option("--license_url", help="License URL for metadata"),
            click.option("--source", help="Source label for metadata"),
            click.option("--source_url", help="Source URL for metadata"),
            click.option("--about", help="About label for metadata"),
            click.option("--about_url", help="About URL for metadata"),
        )
    ):
        subcommand = decorator(subcommand)
    return subcommand


def fail_if_publish_binary_not_installed(binary, publish_target, install_link):
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


def validate_plugin_secret(ctx, param, value):
    for plugin_name, plugin_setting, setting_value in value:
        if "'" in setting_value:
            raise click.BadParameter("--plugin-secret cannot contain single quotes")
    return value
