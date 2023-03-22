from datasette.app import Datasette
import pytest
from pathlib import Path

# not necessarily a full path - the full compiled path looks like "ext.dylib"
# or another suffix, but sqlite will, under the hood, decide which file
# extension to use based on the operating system (apple=dylib, windows=dll etc)
# this resolves to "./ext", which is enough for SQLite to calculate the rest
COMPILED_EXTENSION_PATH = str(Path(__file__).parent / "ext")


# See if ext.c has been compiled, based off the different possible suffixes.
def has_compiled_ext():
    for ext in ["dylib", "so", "dll"]:
        path = Path(__file__).parent / f"ext.{ext}"
        if path.is_file():
            return True
    return False


@pytest.mark.asyncio
@pytest.mark.skipif(not has_compiled_ext(), reason="Requires compiled ext.c")
async def test_load_extension_default_entrypoint():
    # The default entrypoint only loads a() and NOT b() or c(), so those
    # should fail.
    ds = Datasette(sqlite_extensions=[COMPILED_EXTENSION_PATH])

    response = await ds.client.get("/_memory.json?_shape=arrays&sql=select+a()")
    assert response.status_code == 200
    assert response.json()["rows"][0][0] == "a"

    response = await ds.client.get("/_memory.json?_shape=arrays&sql=select+b()")
    assert response.status_code == 400
    assert response.json()["error"] == "no such function: b"

    response = await ds.client.get("/_memory.json?_shape=arrays&sql=select+c()")
    assert response.status_code == 400
    assert response.json()["error"] == "no such function: c"


@pytest.mark.asyncio
@pytest.mark.skipif(not has_compiled_ext(), reason="Requires compiled ext.c")
async def test_load_extension_multiple_entrypoints():
    # Load in the default entrypoint and the other 2 custom entrypoints, now
    # all a(), b(), and c() should run successfully.
    ds = Datasette(
        sqlite_extensions=[
            COMPILED_EXTENSION_PATH,
            (COMPILED_EXTENSION_PATH, "sqlite3_ext_b_init"),
            (COMPILED_EXTENSION_PATH, "sqlite3_ext_c_init"),
        ]
    )

    response = await ds.client.get("/_memory.json?_shape=arrays&sql=select+a()")
    assert response.status_code == 200
    assert response.json()["rows"][0][0] == "a"

    response = await ds.client.get("/_memory.json?_shape=arrays&sql=select+b()")
    assert response.status_code == 200
    assert response.json()["rows"][0][0] == "b"

    response = await ds.client.get("/_memory.json?_shape=arrays&sql=select+c()")
    assert response.status_code == 200
    assert response.json()["rows"][0][0] == "c"
