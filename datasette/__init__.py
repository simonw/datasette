from datasette.version import __version_info__, __version__  # noqa
from .hookspecs import hookimpl # noqa
from .hookspecs import hookspec # noqa

from ._version import get_versions
__version__ = get_versions()['version']
del get_versions
