from datasette import hookimpl
import click
import json
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
    def cloudrun(
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
        service,
        spatialite,
    ):
        fail_if_publish_binary_not_installed(
            "gcloud", "Google Cloud", "https://cloud.google.com/sdk/"
        )
        project = check_output(
            "gcloud config get-value project", shell=True, universal_newlines=True
        ).strip()

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
            image_id = "gcr.io/{project}/{name}".format(project=project, name=name)
            check_call("gcloud builds submit --tag {}".format(image_id), shell=True)
        check_call(
            "gcloud beta run deploy --allow-unauthenticated --image {}{}".format(
                image_id, " {}".format(service) if service else ""
            ),
            shell=True,
        )
