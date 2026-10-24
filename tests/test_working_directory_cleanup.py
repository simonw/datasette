import contextlib
import os
import pathlib
import tempfile

import pytest

from datasette import utils
from datasette.publish.heroku import temporary_heroku_directory

from . import fixtures


@pytest.fixture
def work_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    factory = tempfile.TemporaryDirectory
    cleanups = []

    def temporary_directory(*args, **kwargs):
        kwargs.setdefault("dir", tmp_path)
        directory = factory(*args, **kwargs)
        cleanups.append(directory.cleanup)
        return directory

    monkeypatch.setattr(tempfile, "TemporaryDirectory", temporary_directory)
    try:
        yield tmp_path
    finally:
        os.chdir(tmp_path)
        for cleanup in reversed(cleanups):
            cleanup()


@pytest.mark.parametrize("exit_mode", ["normal", "body-error", "init-error"])
def test_make_app_client_restores_working_directory(
    work_directory, monkeypatch, exit_mode
):
    if exit_mode == "init-error":

        def fail_initialization(*args, **kwargs):
            raise RuntimeError(exit_mode)

        monkeypatch.setattr(fixtures, "Datasette", fail_initialization)
    expected = (
        contextlib.nullcontext()
        if exit_mode == "normal"
        else pytest.raises(RuntimeError, match=exit_mode)
    )
    with expected, fixtures.make_app_client() as client:
        assert pathlib.Path.cwd() != work_directory
        response = client.get("/fixtures/simple_primary_key.json")
        assert response.status_code == 200
        if exit_mode == "body-error":
            raise RuntimeError(exit_mode)
    assert pathlib.Path.cwd() == work_directory
    assert not list(work_directory.iterdir())


def test_nested_app_clients_restore_each_working_directory(work_directory):
    with fixtures.make_app_client():
        outer_directory = pathlib.Path.cwd()
        with fixtures.make_app_client():
            assert pathlib.Path.cwd() != outer_directory
        assert pathlib.Path.cwd() == outer_directory
    assert pathlib.Path.cwd() == work_directory
    assert not list(work_directory.iterdir())


@pytest.fixture(params=["docker", "heroku"])
def publish_directory(work_directory, request):
    kwargs = {
        "files": [],
        "name": "app",
        "metadata": None,
        "extra_options": None,
        "branch": None,
        "template_dir": None,
        "plugins_dir": None,
        "static": [],
        "install": [],
        "version_note": None,
        "secret": None,
    }
    if request.param == "docker":
        return (
            utils.temporary_docker_directory(**kwargs, spatialite=False),
            "Dockerfile",
        )
    return temporary_heroku_directory(**kwargs), "Procfile"


@pytest.mark.parametrize("body_error", [False, True])
def test_publish_context_restores_directory_before_removal(
    work_directory, publish_directory, body_error
):
    directory, expected_file = publish_directory
    expected = (
        pytest.raises(RuntimeError, match="build failed")
        if body_error
        else contextlib.nullcontext()
    )
    with expected, directory:
        build_directory = pathlib.Path.cwd()
        assert build_directory != work_directory
        assert (build_directory / expected_file).is_file()
        if body_error:
            raise RuntimeError("build failed")
    assert pathlib.Path.cwd() == work_directory
    assert not build_directory.exists()
    assert not list(work_directory.iterdir())


def test_publish_cleanup_failure_restores_working_directory(
    work_directory, publish_directory, monkeypatch
):
    context, _ = publish_directory
    directory = tempfile.TemporaryDirectory()

    def fail_cleanup():
        raise PermissionError("build context is locked")

    monkeypatch.setattr(directory, "cleanup", fail_cleanup)
    monkeypatch.setattr(tempfile, "TemporaryDirectory", lambda: directory)
    with pytest.raises(PermissionError, match="build context is locked"), context:
        pass
    assert pathlib.Path.cwd() == work_directory
