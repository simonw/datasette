"""
Default permission implementations for Datasette.

This module provides the built-in permission checking logic through implementations
of the permission_resources_sql hook. The hooks are organized by their purpose:

1. Actor Restrictions - Enforces _r allowlists embedded in actor tokens
2. Root User - Grants full access when --root flag is used
3. Config Rules - Applies permissions from datasette.yaml
4. Default Settings - Enforces default_allow_sql and default view permissions

IMPORTANT: These hooks return PermissionSQL objects that are combined using SQL
UNION/INTERSECT operations. The order of evaluation is:
  - restriction_sql fields are INTERSECTed (all must match)
  - Regular sql fields are UNIONed and evaluated with cascading priority
"""

from __future__ import annotations

# Re-export all hooks and public utilities
from .restrictions import (
    actor_restrictions_sql as actor_restrictions_sql,
    restrictions_allow_action as restrictions_allow_action,
    ActorRestrictions as ActorRestrictions,
)
from .root import root_user_permissions_sql as root_user_permissions_sql
from .config import config_permissions_sql as config_permissions_sql
from .defaults import (
    # Avoid "datasette.default_permissions" does not explicitly export attribute
    default_allow_sql_check as default_allow_sql_check,
    default_action_permissions_sql as default_action_permissions_sql,
    default_query_permissions_sql as default_query_permissions_sql,
    DEFAULT_ALLOW_ACTIONS as DEFAULT_ALLOW_ACTIONS,
)
