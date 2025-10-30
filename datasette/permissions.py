from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, NamedTuple


class Resource(ABC):
    """
    Base class for all resource types.

    Each subclass represents a type of resource (e.g., TableResource, DatabaseResource).
    The class itself carries metadata about the resource type.
    Instances represent specific resources.
    """

    # Class-level metadata (subclasses must define these)
    name: str = None  # e.g., "table", "database", "model"
    parent_name: str | None = None  # e.g., "database" for tables

    def __init__(self, parent: str | None = None, child: str | None = None):
        """
        Create a resource instance.

        Args:
            parent: The parent identifier (meaning depends on resource type)
            child: The child identifier (meaning depends on resource type)
        """
        self.parent = parent
        self.child = child
        self._private = None  # Sentinel to track if private was set

    @property
    def private(self) -> bool:
        """
        Whether this resource is private (accessible to actor but not anonymous).

        This property is only available on Resource objects returned from
        allowed_resources() when include_is_private=True is used.

        Raises:
            AttributeError: If accessed without calling include_is_private=True
        """
        if self._private is None:
            raise AttributeError(
                "The 'private' attribute is only available when using "
                "allowed_resources(..., include_is_private=True)"
            )
        return self._private

    @private.setter
    def private(self, value: bool):
        self._private = value

    @classmethod
    @abstractmethod
    def resources_sql(cls) -> str:
        """
        Return SQL query that returns all resources of this type.

        Must return two columns: parent, child
        """
        pass


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
    also_requires: str | None = None  # Optional action name that must also be allowed


_reason_id = 1


@dataclass
class PermissionSQL:
    """
    A plugin contributes SQL that yields:
      parent TEXT NULL,
      child  TEXT NULL,
      allow  INTEGER,    -- 1 allow, 0 deny
      reason TEXT
    """

    sql: str  # SQL that SELECTs the 4 columns above
    params: dict[str, Any] | None = (
        None  # bound params for the SQL (values only; no ':' prefix)
    )
    source: str | None = None  # System will set this to the plugin name

    @classmethod
    def allow(cls, reason: str, _allow: bool = True) -> "PermissionSQL":
        global _reason_id
        i = _reason_id
        _reason_id += 1
        return cls(
            sql=f"SELECT NULL AS parent, NULL AS child, {1 if _allow else 0} AS allow, :reason_{i} AS reason",
            params={f"reason_{i}": reason},
        )

    @classmethod
    def deny(cls, reason: str) -> "PermissionSQL":
        return cls.allow(reason=reason, _allow=False)


# This is obsolete, replaced by Action and ResourceType
@dataclass
class Permission:
    name: str
    abbr: str | None
    description: str | None
    takes_database: bool
    takes_resource: bool
    default: bool
    # This is deliberately undocumented: it's considered an internal
    # implementation detail for view-table/view-database and should
    # not be used by plugins as it may change in the future.
    implies_can_view: bool = False
