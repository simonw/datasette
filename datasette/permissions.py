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
    takes_child: bool = False  # Whether this resource type operates on child resources

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
    def takes_parent(cls) -> bool:
        """
        Whether actions on this resource require a parent identifier.

        Returns True for parent-level and child-level resources.
        Returns False for top-level resources (where parent_name is None).
        """
        return cls.parent_name is not None

    @classmethod
    def __init_subclass__(cls):
        """
        Validate that resource hierarchy doesn't exceed 2 levels.

        Raises:
            ValueError: If this resource would create a 3-level hierarchy
        """
        super().__init_subclass__()

        if cls.parent_name is None:
            return  # Top of hierarchy, nothing to validate

        # Find the parent resource class by looking through Resource subclasses
        # Use __subclasses__() to avoid issues with sys.modules iteration
        parent_cls = None

        def find_resource_by_name(name, resource_cls=Resource):
            """Recursively search for a Resource subclass with the given name."""
            for subclass in resource_cls.__subclasses__():
                if hasattr(subclass, 'name') and subclass.name == name:
                    return subclass
                # Recursively search subclasses
                found = find_resource_by_name(name, subclass)
                if found:
                    return found
            return None

        parent_cls = find_resource_by_name(cls.parent_name)

        if parent_cls and parent_cls.parent_name is not None:
            # We have a parent, and that parent has a parent
            # This creates a 3-level hierarchy, which is not allowed
            raise ValueError(
                f"Resource {cls.__name__} creates a 3-level hierarchy: "
                f"{parent_cls.parent_name} -> {cls.parent_name} -> {cls.name}. "
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


@dataclass(frozen=True)
class Action:
    name: str
    abbr: str | None
    description: str | None
    resource_class: type[Resource] | None = None
    global_: bool = False  # If True, action applies only at top level (no resource)
    also_requires: str | None = None  # Optional action name that must also be allowed

    def __post_init__(self):
        """Validate that global_ and resource_class are mutually exclusive."""
        if self.global_ and self.resource_class is not None:
            raise ValueError(
                f"Action {self.name} cannot be both global_=True and have a resource_class"
            )
        if not self.global_ and self.resource_class is None:
            raise ValueError(
                f"Action {self.name} must either have global_=True or a resource_class"
            )

    @property
    def takes_parent(self) -> bool:
        """
        Whether this action requires a parent identifier.

        Returns False for global actions, otherwise delegates to resource_class.
        """
        if self.global_:
            return False
        return self.resource_class.takes_parent()

    @property
    def takes_child(self) -> bool:
        """
        Whether this action requires a child identifier.

        Returns False for global actions, otherwise delegates to resource_class.
        """
        if self.global_:
            return False
        return self.resource_class.takes_child


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
