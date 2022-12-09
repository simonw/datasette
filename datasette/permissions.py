import collections

Permission = collections.namedtuple(
    "Permission", ("name", "abbr", "takes_database", "takes_resource", "default")
)
