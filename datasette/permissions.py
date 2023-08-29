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
    implies_can_view: bool = False
