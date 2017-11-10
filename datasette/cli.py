import click
from click_default_group import DefaultGroup
from .app import Datasette, ensure_build_metadata


@click.group(cls=DefaultGroup, default='serve', default_if_no_args=True)
def cli():
    """
    Datasette!
    """


@cli.command()
@click.argument('files', type=click.Path(exists=True), nargs=-1)
def build(files):
    ensure_build_metadata(files, True)


@cli.command()
@click.argument('files', type=click.Path(exists=True), nargs=-1)
@click.option('-h', '--host', default='0.0.0.0')
@click.option('-p', '--port', default=8001)
@click.option('--debug', is_flag=True)
@click.option('--reload', is_flag=True)
def serve(files, host, port, debug, reload):
    """Serve up specified database files with a web UI"""
    if reload:
        import hupper
        hupper.start_reloader('datasette.cli.serve')

    click.echo('Serve! files={} on port {}'.format(files, port))
    app = Datasette(files, cache_headers=not debug and not reload).app()
    app.run(host=host, port=port, debug=debug)
