import click
from click_default_group import DefaultGroup
import os
from subprocess import call
import tempfile
from .app import Datasette, ensure_build_metadata
from .utils import make_dockerfile


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
def publish(files):
    tmp = tempfile.TemporaryDirectory()
    # We create a datasette folder in there to get a nicer now deploy name
    datasette_dir = os.path.join(tmp.name, 'datasette')
    os.mkdir(datasette_dir)
    saved_cwd = os.getcwd()
    file_paths = [
        os.path.join(saved_cwd, name)
        for name in files
    ]
    try:
        dockerfile = make_dockerfile(files)
        os.chdir(datasette_dir)
        open('Dockerfile', 'w').write(dockerfile)
        for path, filename in zip(file_paths, files):
            os.link(path, os.path.join(datasette_dir, filename))
        call('now')
    finally:
        tmp.cleanup()
        os.chdir(saved_cwd)


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
