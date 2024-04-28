from datasette.utils import LoadExtension


def test_dos_path():
    path_string = "C:\Windows\System32\mod_spatialite.dll"
    le = LoadExtension()
    path = le.convert(path_string, None, None)
    assert path == "C:\Windows\System32\mod_spatialite.dll"


def test_dos_pathentry():
    path_entry = "C:\Windows\System32\mod_spatialite.dll:testentry"
    le = LoadExtension()
    pathen, entry = le.convert(path_entry, None, None)
    assert pathen == "C:\Windows\System32\mod_spatialite.dll"
    assert entry == "testentry"


def test_linux_path():
    path_string = "/base/test/test2"
    le = LoadExtension()
    path = le.convert(path_string, None, None)
    assert path == path_string


def test_linux_path_entry():
    path_string = "/base/test/test2:testentry"
    le = LoadExtension()
    path, entry = le.convert(path_string, None, None)
    assert path == "/base/test/test2"
    assert entry == "testentry"
