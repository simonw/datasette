import click
from click_default_group import DefaultGroup
import json
import shutil
from subprocess import call
import sys
from .app import Datasette
from .utils import (
    temporary_docker_directory,
)


@click.group(cls=DefaultGroup, default='serve', default_if_no_args=True)
def cli():
    """
    Datasette!
    """


@cli.command()
@click.argument('files', type=click.Path(exists=True), nargs=-1)
@click.option('--inspect-file', default='inspect-data.json')
def build(files, inspect_file):
    app = Datasette(files)
    open(inspect_file, 'w').write(json.dumps(app.inspect(), indent=2))


@cli.command()
@click.argument('files', type=click.Path(exists=True), nargs=-1)
@click.option(
    '-n', '--name', default='datasette',
    help='Application name to use when deploying to Now'
)
@click.option(
    '-m', '--metadata', type=click.File(mode='r'),
    help='Path to JSON file containing metadata to publish'
)
def publish(files, name, metadata):
    if not shutil.which('now'):
        click.secho(
            ' The publish command requires "now" to be installed and configured ',
            bg='red',
            fg='white',
            bold=True,
            err=True,
        )
        click.echo('Follow the instructions at https://zeit.co/now#whats-now', err=True)
        sys.exit(1)

    with temporary_docker_directory(files, name, metadata):
        call('now')


@cli.command()
@click.argument('files', type=click.Path(exists=True), nargs=-1, required=True)
@click.option(
    '-t', '--tag',
    help='Name for the resulting Docker container, can optionally use name:tag format'
)
@click.option(
    '-m', '--metadata', type=click.File(mode='r'),
    help='Path to JSON file containing metadata to publish'
)
def package(files, tag, metadata):
    "Package specified SQLite files into a new datasette Docker container"
    if not shutil.which('docker'):
        click.secho(
            ' The package command requires "docker" to be installed and configured ',
            bg='red',
            fg='white',
            bold=True,
            err=True,
        )
        sys.exit(1)
    with temporary_docker_directory(files, 'datasette', metadata):
        args = ['docker', 'build']
        if tag:
            args.append('-t')
            args.append(tag)
        args.append('.')
        call(args)


@cli.command()
@click.argument('files', type=click.Path(exists=True), nargs=-1)
@click.option('-h', '--host', default='0.0.0.0')
@click.option('-p', '--port', default=8001)
@click.option('--debug', is_flag=True)
@click.option('--reload', is_flag=True)
@click.option('--inspect-file')
@click.option('-m', '--metadata', type=click.File(mode='r'))
def serve(files, host, port, debug, reload, inspect_file, metadata):
    """Serve up specified database files with a web UI"""
    if reload:
        import hupper
        hupper.start_reloader('datasette.cli.serve')

    inspect_data = None
    if inspect_file:
        inspect_data = json.load(open(inspect_file))

    metadata_data = None
    if metadata:
        metadata_data = json.loads(metadata.read())

    click.echo('Serve! files={} on port {}'.format(files, port))
    ds = Datasette(
        files,
        cache_headers=not debug and not reload,
        inspect_data=inspect_data,
        metadata=metadata_data,
    )
    # Force initial hashing/table counting
    ds.inspect()
    ds.app().run(host=host, port=port, debug=debug)
