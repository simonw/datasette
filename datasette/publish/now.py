from datasette import hookimpl
import click
import json
from subprocess import run, PIPE

from .common import (
    add_common_publish_arguments_and_options,
    fail_if_publish_binary_not_installed,
)
from ..utils import temporary_docker_directory


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
    @click.option("--force", is_flag=True, help="Pass --force option to now")
    @click.option("--token", help="Auth token to use for deploy")
    @click.option("--alias", multiple=True, help="Desired alias e.g. yoursite.now.sh")
    @click.option("--spatialite", is_flag=True, help="Enable SpatialLite extension")
    def nowv1(
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
        about,
        about_url,
        name,
        force,
        token,
        alias,
        spatialite,
    ):
        fail_if_publish_binary_not_installed("now", "Zeit Now", "https://zeit.co/now")
        if extra_options:
            extra_options += " "
        else:
            extra_options = ""
        extra_options += "--config force_https_urls:on"

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
            spatialite,
            version_note,
            {
                "title": title,
                "license": license,
                "license_url": license_url,
                "source": source,
                "source_url": source_url,
                "about": about,
                "about_url": about_url,
            },
        ):
            now_json = {"version": 1}
            open("now.json", "w").write(json.dumps(now_json, indent=4))
            args = []
            if force:
                args.append("--force")
            if token:
                args.append("--token={}".format(token))
            if args:
                done = run(["now"] + args, stdout=PIPE)
            else:
                done = run("now", stdout=PIPE)
            deployment_url = done.stdout
            if alias:
                # I couldn't get --target=production working, so I call
                # 'now alias' with arguments directly instead - but that
                # means I need to figure out what URL it was deployed to.
                for single_alias in alias:  # --alias can be specified multiple times
                    args = ["now", "alias", deployment_url, single_alias]
                    if token:
                        args.append("--token={}".format(token))
                    run(args)
            else:
                print(deployment_url.decode("latin1"))
