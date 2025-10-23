from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional, NamedTuple


class Resource(ABC):
    """
    Base class for all resource types.

    Each subclass represents a type of resource (e.g., TableResource, DatabaseResource).
    The class itself carries metadata about the resource type.
    Instances represent specific resources.
    """

    # Class-level metadata (subclasses must define these)
    name: str = None  # e.g., "table", "database", "model"
    parent_name: Optional[str] = None  # e.g., "database" for tables

    def __init__(self, parent: Optional[str] = None, child: Optional[str] = None):
        """
        Create a resource instance.

        Args:
            parent: The parent identifier (meaning depends on resource type)
            child: The child identifier (meaning depends on resource type)
        """
        self.parent = parent
        self.child = child

    @classmethod
    @abstractmethod
    def resources_sql(cls) -> str:
        """
        Return SQL query that returns all resources of this type.

        Must return two columns: parent, child
        """
        pass

    def __str__(self) -> str:
        if self.parent is None and self.child is None:
            return f"{self.name}:*"
        elif self.child is None:
            return f"{self.name}:{self.parent}"
        else:
            return f"{self.name}:{self.parent}/{self.child}"

    def __repr__(self) -> str:
        parts = [f"{self.__class__.__name__}("]
        args = []
        if self.parent:
            args.append(f"{self.parent!r}")
        if self.child:
            args.append(f"{self.child!r}")
        parts.append(", ".join(args))
        parts.append(")")
        return "".join(parts)

    def __eq__(self, other):
        if not isinstance(other, Resource):
            return False
        return (
            self.__class__ == other.__class__
            and self.parent == other.parent
            and self.child == other.child
        )

    def __hash__(self):
        return hash((self.__class__, self.parent, self.child))


class AllowedResource(NamedTuple):
    """A resource with the reason it was allowed (for debugging)."""

    resource: Resource
    reason: str


@dataclass(frozen=True)
class Action:
    name: str
    abbr: str | None
    description: str | None
    takes_parent: bool
    takes_child: bool
    resource_class: type[Resource]


@dataclass
class PermissionSQL:
    """
    A plugin contributes SQL that yields:
      parent TEXT NULL,
      child  TEXT NULL,
      allow  INTEGER,    -- 1 allow, 0 deny
      reason TEXT
    """

    source: str  # identifier used for auditing (e.g., plugin name)
    sql: str  # SQL that SELECTs the 4 columns above
    params: Dict[str, Any]  # bound params for the SQL (values only; no ':' prefix)


# This is obsolete, replaced by Action and ResourceType
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
