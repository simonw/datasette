from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class JumpSQL:
    sql: str
    params: dict[str, Any] | None = None
    database: str | None = None

    @classmethod
    def menu_item(
        cls,
        *,
        label: str,
        url: str,
        description: str = "Menu item",
        search_text: str | None = None,
        display_name: str | None = None,
        item_type: str = "menu",
    ) -> "JumpSQL":
        if search_text is None:
            search_text = " ".join(
                text for text in (label, display_name, description) if text is not None
            )
        return cls(
            sql="""
            SELECT
                :type AS type,
                :label AS label,
                :description AS description,
                :url AS url,
                :search_text AS search_text,
                :display_name AS display_name
            """,
            params={
                "type": item_type,
                "label": label,
                "description": description,
                "url": url,
                "search_text": search_text,
                "display_name": display_name,
            },
        )


_PARAM_RE = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")


def namespace_sql_params(sql: str, params: dict[str, Any], prefix: str):
    """Rename named SQL parameters so UNION query parameters cannot collide."""
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
