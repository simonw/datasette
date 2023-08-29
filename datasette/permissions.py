from dataclasses import dataclass, fields
from typing import Optional


@dataclass
class Permission:
    name: str
    abbr: Optional[str]
    description: Optional[str]
    takes_database: bool
    takes_resource: bool
    default: bool
    # This is deliberately undocumented: it's considered an internal
    # implementation detail for view-table/view-database and should
    # not be used by plugins as it may change in the future.
    implies_can_view: bool = False
