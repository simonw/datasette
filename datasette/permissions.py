import collections

Permission = collections.namedtuple(
    "Permission", ("name", "abbr", "takes_database", "takes_table", "default")
)

PERMISSIONS = (
    Permission("view-instance", "vi", False, False, True),
    Permission("view-database", "vd", True, False, True),
    Permission("view-database-download", "vdd", True, False, True),
    Permission("view-table", "vt", True, True, True),
    Permission("view-query", "vq", True, True, True),
    Permission("insert-row", "ir", True, True, False),
    Permission("delete-row", "dr", True, True, False),
    Permission("drop-table", "dt", True, True, False),
    Permission("execute-sql", "es", True, False, True),
    Permission("permissions-debug", "pd", False, False, False),
    Permission("debug-menu", "dm", False, False, False),
)
