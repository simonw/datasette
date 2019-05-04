def pytest_configure(config):
    import sys

    sys._called_from_test = True


def pytest_unconfigure(config):
    import sys

    del sys._called_from_test


def pytest_collection_modifyitems(items):
    # Ensure test_black.py runs first before any asyncio code kicks in
    test_black = [fn for fn in items if fn.name == "test_black"]
    if test_black:
        items.insert(0, items.pop(items.index(test_black[0])))
