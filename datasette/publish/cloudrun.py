from datasette import hookimpl
import click
import json
import os
import re
from subprocess import check_call, check_output

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
        help="Application name to use when building",
    )
    @click.option(
        "--service", default="", help="Cloud Run service to deploy (or over-write)"
    )
    @click.option("--spatialite", is_flag=True, help="Enable SpatialLite extension")
    @click.option(
        "--show-files",
        is_flag=True,
        help="Output the generated Dockerfile and metadata.json",
    )
    @click.option(
        "--memory",
        callback=_validate_memory,
        help="Memory to allocate in Cloud Run, e.g. 1Gi",
    )
    @click.option(
        "--apt-get-install",
        "apt_get_extras",
        multiple=True,
        help="Additional packages to apt-get install",
    )
    def cloudrun(
        files,
        metadata,
        extra_options,
        branch,
        template_dir,
        plugins_dir,
        static,
        install,
        plugin_secret,
        version_note,
        secret,
        title,
        license,
        license_url,
        source,
        source_url,
        about,
        about_url,
        name,
        service,
        spatialite,
        show_files,
        memory,
        apt_get_extras,
    ):
        fail_if_publish_binary_not_installed(
            "gcloud", "Google Cloud", "https://cloud.google.com/sdk/"
        )
        project = check_output(
            "gcloud config get-value project", shell=True, universal_newlines=True
        ).strip()

        if not service:
            # Show the user their current services, then prompt for one
            click.echo("Please provide a service name for this deployment\n")
            click.echo("Using an existing service name will over-write it")
            click.echo("")
            existing_services = get_existing_services()
            if existing_services:
                click.echo("Your existing services:\n")
                for existing_service in existing_services:
                    click.echo(
                        "  {name} - created {created} - {url}".format(
                            **existing_service
                        )
                    )
                click.echo("")
            service = click.prompt("Service name", type=str)

        extra_metadata = {
            "title": title,
            "license": license,
            "license_url": license_url,
            "source": source,
            "source_url": source_url,
            "about": about,
            "about_url": about_url,
        }

        if not extra_options:
            extra_options = ""
        if "force_https_urls" not in extra_options:
            if extra_options:
                extra_options += " "
            extra_options += "--setting force_https_urls on"

        environment_variables = {}
        if plugin_secret:
            extra_metadata["plugins"] = {}
            for plugin_name, plugin_setting, setting_value in plugin_secret:
                environment_variable = (
                    f"{plugin_name}_{plugin_setting}".upper().replace("-", "_")
                )
                environment_variables[environment_variable] = setting_value
                extra_metadata["plugins"].setdefault(plugin_name, {})[
                    plugin_setting
                ] = {"$env": environment_variable}

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
            secret,
            extra_metadata,
            environment_variables,
            apt_get_extras=apt_get_extras,
        ):
            if show_files:
                if os.path.exists("metadata.json"):
                    print("=== metadata.json ===\n")
                    print(open("metadata.json").read())
                print("\n==== Dockerfile ====\n")
                print(open("Dockerfile").read())
                print("\n====================\n")

            image_id = f"gcr.io/{project}/{name}"
            check_call(f"gcloud builds submit --tag {image_id}", shell=True)
        check_call(
            "gcloud run deploy --allow-unauthenticated --platform=managed --image {} {}{}".format(
                image_id, service, " --memory {}".format(memory) if memory else ""
            ),
            shell=True,
        )


def get_existing_services():
    services = json.loads(
        check_output(
            "gcloud run services list --platform=managed --format json",
            shell=True,
            universal_newlines=True,
        )
    )
    return [
        {
            "name": service["metadata"]["name"],
            "created": service["metadata"]["creationTimestamp"],
            "url": service["status"]["address"]["url"],
        }
        for service in services
    ]


def _validate_memory(ctx, param, value):
    if value and re.match(r"^\d+(Gi|G|Mi|M)$", value) is None:
        raise click.BadParameter("--memory should be a number then Gi/G/Mi/M e.g 1Gi")
    return value
