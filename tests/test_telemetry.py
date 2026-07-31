import subprocess
import sys


def test_datasette_package_never_imports_the_sdk():
    """
    Core depends on opentelemetry-api only. The SDK is a test dependency.

    Checked by importing datasette in a fresh process and inspecting
    sys.modules, rather than by grepping, so a lazy `import
    opentelemetry.sdk` inside a function body cannot slip past.

    conftest.py's pytest_collection_modifyitems() moves this test to the
    front of the run by name - if you rename it, rename it there too.
    """
    code = (
        "import datasette.app, datasette.database, datasette.telemetry, sys; "
        "print([m for m in sys.modules if m.startswith('opentelemetry.sdk')])"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert (
        result.stdout.strip() == "[]"
    ), f"datasette imported the OpenTelemetry SDK: {result.stdout.strip()}"
