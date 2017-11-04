import click
from click_default_group import DefaultGroup
from .app import app, ensure_build_metadata


@click.group(cls=DefaultGroup, default='serve', default_if_no_args=True)
def cli():
    """
    Datasite!
    """


@cli.command()
def build():
    ensure_build_metadata(True)


@cli.command()
@click.argument('files', type=click.Path(exists=True), nargs=-1)
@click.option('-h', '--host', default='0.0.0.0')
@click.option('-p', '--port', default=8001)
@click.option('--debug', is_flag=True)
def serve(files, host, port, debug):
    """Serve up specified database files with a web UI"""
    click.echo('Serve! files={} on port {}'.format(files, port))
    app.run(host=host, port=port, debug=debug)
