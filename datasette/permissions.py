from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, NamedTuple
import contextvars


# Context variable to track when permission checks should be skipped
_skip_permission_checks = contextvars.ContextVar(
    "skip_permission_checks", default=False
)


class SkipPermissions:
    """Context manager to temporarily skip permission checks.

    This is not a stable API and may change in future releases.

    Usage:
        with SkipPermissions():
            # Permission checks are skipped within this block
            response = await datasette.client.get("/protected")
    """

    def __enter__(self):
        self.token = _skip_permission_checks.set(True)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        _skip_permission_checks.reset(self.token)
        return False


class Resource(ABC):
    """
    Base class for all resource types.

    Each subclass represents a type of resource (e.g., TableResource, DatabaseResource).
    The class itself carries metadata about the resource type.
    Instances represent specific resources.
    """

    # Class-level metadata (subclasses must define these)
    name: str = None  # e.g., "table", "database", "model"
    parent_class: type["Resource"] | None = None  # e.g., DatabaseResource for tables

    # Instance-level optional extra attributes
    reasons: list[str] | None = None
    include_reasons: bool | None = None

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
    def __init_subclass__(cls):
        """
        Validate resource hierarchy doesn't exceed 2 levels.

        Raises:
            ValueError: If this resource would create a 3-level hierarchy
        """
        super().__init_subclass__()

        if cls.parent_class is None:
            return  # Top of hierarchy, nothing to validate

        # Check if our parent has a parent - that would create 3 levels
        if cls.parent_class.parent_class is not None:
            # We have a parent, and that parent has a parent
            # This creates a 3-level hierarchy, which is not allowed
            raise ValueError(
                f"Resource {cls.__name__} creates a 3-level hierarchy: "
                f"{cls.parent_class.parent_class.__name__} -> {cls.parent_class.__name__} -> {cls.__name__}. "
                f"Maximum 2 levels allowed (parent -> child)."
            )

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


@dataclass(frozen=True, kw_only=True)
class Action:
    name: str
    description: str | None
    abbr: str | None = None
    resource_class: type[Resource] | None = None
    also_requires: str | None = None  # Optional action name that must also be allowed

    @property
    def takes_parent(self) -> bool:
        """
        Whether this action requires a parent identifier when instantiating its resource.

        Returns False for global-only actions (no resource_class).
        Returns True for all actions with a resource_class (all resources require a parent identifier).
        """
        return self.resource_class is not None

    @property
    def takes_child(self) -> bool:
        """
        Whether this action requires a child identifier when instantiating its resource.

        Returns False for global actions (no resource_class).
        Returns False for parent-level resources (DatabaseResource - parent_class is None).
        Returns True for child-level resources (TableResource, QueryResource - have a parent_class).
        """
        if self.resource_class is None:
            return False
        return self.resource_class.parent_class is not None


_reason_id = 1


@dataclass
class PermissionSQL:
    """
    A plugin contributes SQL that yields:
      parent TEXT NULL,
      child  TEXT NULL,
      allow  INTEGER,    -- 1 allow, 0 deny
      reason TEXT

    For restriction-only plugins, sql can be None and only restriction_sql is provided.
    """

    sql: str | None = (
        None  # SQL that SELECTs the 4 columns above (can be None for restriction-only)
    )
    params: dict[str, Any] | None = (
        None  # bound params for the SQL (values only; no ':' prefix)
    )
    source: str | None = None  # System will set this to the plugin name
    restriction_sql: str | None = (
        None  # Optional SQL that returns (parent, child) for restriction filtering
    )

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
