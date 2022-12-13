import collections

Permission = collections.namedtuple(
    "Permission",
    ("name", "abbr", "description", "takes_database", "takes_resource", "default"),
)
