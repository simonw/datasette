from ._version import get_versions

__version__ = get_versions()["version"]
del get_versions

__version_info__ = tuple(__version__.split("."))
