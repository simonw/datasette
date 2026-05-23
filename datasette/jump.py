from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class JumpSQL:
    sql: str
    params: dict[str, Any] | None = None


_PARAM_RE = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")


def namespace_sql_params(sql: str, params: dict[str, Any], prefix: str):
    """Rename named SQL parameters so UNION fragments cannot collide."""
    if not params:
        return sql, {}

    renamed = {key: f"{prefix}_{key}" for key in params}

    def replace(match):
        key = match.group(1)
        if key not in renamed:
            return match.group(0)
        return f":{renamed[key]}"

    return _PARAM_RE.sub(replace, sql), {
        renamed[key]: value for key, value in params.items()
    }
