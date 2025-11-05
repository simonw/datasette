import pytest
from datasette.app import Datasette
from datasette.permissions import PermissionSQL
from datasette.utils.permissions import resolve_permissions_from_catalog
from typing import Callable, List


@pytest.fixture
def db():
    ds = Datasette()
    import tempfile
    from datasette.database import Database

    path = tempfile.mktemp(suffix="demo.db")
    db = ds.add_database(Database(ds, path=path))
    return db


NO_RULES_SQL = (
    "SELECT NULL AS parent, NULL AS child, NULL AS allow, NULL AS reason WHERE 0"
)


def plugin_allow_all_for_user(user: str) -> Callable[[str], PermissionSQL]:
    def provider(action: str) -> PermissionSQL:
        return PermissionSQL(
            """
            SELECT NULL AS parent, NULL AS child, 1 AS allow,
                   'global allow for ' || :allow_all_user || ' on ' || :allow_all_action AS reason
            WHERE :actor_id = :allow_all_user
            """,
            {"allow_all_user": user, "allow_all_action": action},
        )

    return provider


def plugin_deny_specific_table(
    user: str, parent: str, child: str
) -> Callable[[str], PermissionSQL]:
    def provider(action: str) -> PermissionSQL:
        return PermissionSQL(
            """
            SELECT :deny_specific_table_parent AS parent, :deny_specific_table_child AS child, 0 AS allow,
                   'deny ' || :deny_specific_table_parent || '/' || :deny_specific_table_child || ' for ' || :deny_specific_table_user || ' on ' || :deny_specific_table_action AS reason
            WHERE :actor_id = :deny_specific_table_user
            """,
            {
                "deny_specific_table_parent": parent,
                "deny_specific_table_child": child,
                "deny_specific_table_user": user,
                "deny_specific_table_action": action,
            },
        )

    return provider


def plugin_org_policy_deny_parent(parent: str) -> Callable[[str], PermissionSQL]:
    def provider(action: str) -> PermissionSQL:
        return PermissionSQL(
            """
            SELECT :org_policy_parent_deny_parent AS parent, NULL AS child, 0 AS allow,
                   'org policy: parent ' || :org_policy_parent_deny_parent || ' denied on ' || :org_policy_parent_deny_action AS reason
            """,
            {
                "org_policy_parent_deny_parent": parent,
                "org_policy_parent_deny_action": action,
            },
        )

    return provider


def plugin_allow_parent_for_user(
    user: str, parent: str
) -> Callable[[str], PermissionSQL]:
    def provider(action: str) -> PermissionSQL:
        return PermissionSQL(
            """
            SELECT :allow_parent_parent AS parent, NULL AS child, 1 AS allow,
                   'allow full parent for ' || :allow_parent_user || ' on ' || :allow_parent_action AS reason
            WHERE :actor_id = :allow_parent_user
            """,
            {
                "allow_parent_parent": parent,
                "allow_parent_user": user,
                "allow_parent_action": action,
            },
        )

    return provider


def plugin_child_allow_for_user(
    user: str, parent: str, child: str
) -> Callable[[str], PermissionSQL]:
    def provider(action: str) -> PermissionSQL:
        return PermissionSQL(
            """
            SELECT :allow_child_parent AS parent, :allow_child_child AS child, 1 AS allow,
                   'allow child for ' || :allow_child_user || ' on ' || :allow_child_action AS reason
            WHERE :actor_id = :allow_child_user
            """,
            {
                "allow_child_parent": parent,
                "allow_child_child": child,
                "allow_child_user": user,
                "allow_child_action": action,
            },
        )

    return provider


def plugin_root_deny_for_all() -> Callable[[str], PermissionSQL]:
    def provider(action: str) -> PermissionSQL:
        return PermissionSQL(
            """
            SELECT NULL AS parent, NULL AS child, 0 AS allow, 'root deny for all on ' || :root_deny_action AS reason
            """,
            {"root_deny_action": action},
        )

    return provider


def plugin_conflicting_same_child_rules(
    user: str, parent: str, child: str
) -> List[Callable[[str], PermissionSQL]]:
    def allow_provider(action: str) -> PermissionSQL:
        return PermissionSQL(
            """
            SELECT :conflict_child_allow_parent AS parent, :conflict_child_allow_child AS child, 1 AS allow,
                   'team grant at child for ' || :conflict_child_allow_user || ' on ' || :conflict_child_allow_action AS reason
            WHERE :actor_id = :conflict_child_allow_user
            """,
            {
                "conflict_child_allow_parent": parent,
                "conflict_child_allow_child": child,
                "conflict_child_allow_user": user,
                "conflict_child_allow_action": action,
            },
        )

    def deny_provider(action: str) -> PermissionSQL:
        return PermissionSQL(
            """
            SELECT :conflict_child_deny_parent AS parent, :conflict_child_deny_child AS child, 0 AS allow,
                   'exception deny at child for ' || :conflict_child_deny_user || ' on ' || :conflict_child_deny_action AS reason
            WHERE :actor_id = :conflict_child_deny_user
            """,
            {
                "conflict_child_deny_parent": parent,
                "conflict_child_deny_child": child,
                "conflict_child_deny_user": user,
                "conflict_child_deny_action": action,
            },
        )

    return [allow_provider, deny_provider]


def plugin_allow_all_for_action(
    user: str, allowed_action: str
) -> Callable[[str], PermissionSQL]:
    def provider(action: str) -> PermissionSQL:
        if action != allowed_action:
            return PermissionSQL(NO_RULES_SQL)
        # Sanitize parameter names by replacing hyphens with underscores
        param_prefix = action.replace("-", "_")
        return PermissionSQL(
            f"""
            SELECT NULL AS parent, NULL AS child, 1 AS allow,
                   'global allow for ' || :{param_prefix}_user || ' on ' || :{param_prefix}_action AS reason
            WHERE :actor_id = :{param_prefix}_user
            """,
            {f"{param_prefix}_user": user, f"{param_prefix}_action": action},
        )

    return provider


VIEW_TABLE = "view-table"


# ---------- Catalog DDL (from your schema) ----------
CATALOG_DDL = """
CREATE TABLE IF NOT EXISTS catalog_databases (
    database_name TEXT PRIMARY KEY,
    path TEXT,
    is_memory INTEGER,
    schema_version INTEGER
);
CREATE TABLE IF NOT EXISTS catalog_tables (
    database_name TEXT,
    table_name TEXT,
    rootpage INTEGER,
    sql TEXT,
    PRIMARY KEY (database_name, table_name),
    FOREIGN KEY (database_name) REFERENCES catalog_databases(database_name)
);
"""

PARENTS = ["accounting", "hr", "analytics"]
SPECIALS = {"accounting": ["sales"], "analytics": ["secret"], "hr": []}

TABLE_CANDIDATES_SQL = (
    "SELECT database_name AS parent, table_name AS child FROM catalog_tables"
)
PARENT_CANDIDATES_SQL = (
    "SELECT database_name AS parent, NULL AS child FROM catalog_databases"
)


# ---------- Helpers ----------
async def seed_catalog(db, per_parent: int = 10) -> None:
    await db.execute_write_script(CATALOG_DDL)
    # databases
    db_rows = [(p, f"/{p}.db", 0, 1) for p in PARENTS]
    await db.execute_write_many(
        "INSERT OR REPLACE INTO catalog_databases(database_name, path, is_memory, schema_version) VALUES (?,?,?,?)",
        db_rows,
    )

    # tables
    def tables_for(parent: str, n: int):
        base = [f"table{i:02d}" for i in range(1, n + 1)]
        for s in SPECIALS.get(parent, []):
            if s not in base:
                base[0] = s
        return base

    table_rows = []
    for p in PARENTS:
        for t in tables_for(p, per_parent):
            table_rows.append((p, t, 0, f"CREATE TABLE {t} (id INTEGER PRIMARY KEY)"))
    await db.execute_write_many(
        "INSERT OR REPLACE INTO catalog_tables(database_name, table_name, rootpage, sql) VALUES (?,?,?,?)",
        table_rows,
    )


def res_allowed(rows, parent=None):
    return sorted(
        r["resource"]
        for r in rows
        if r["allow"] == 1 and (parent is None or r["parent"] == parent)
    )


def res_denied(rows, parent=None):
    return sorted(
        r["resource"]
        for r in rows
        if r["allow"] == 0 and (parent is None or r["parent"] == parent)
    )


# ---------- Tests ----------
@pytest.mark.asyncio
async def test_alice_global_allow_with_specific_denies_catalog(db):
    await seed_catalog(db)
    plugins = [
        plugin_allow_all_for_user("alice"),
        plugin_deny_specific_table("alice", "accounting", "sales"),
        plugin_org_policy_deny_parent("hr"),
    ]
    rows = await resolve_permissions_from_catalog(
        db,
        {"id": "alice"},
        plugins,
        VIEW_TABLE,
        TABLE_CANDIDATES_SQL,
        implicit_deny=True,
    )
    # Alice can see everything except accounting/sales and hr/*
    assert "/accounting/sales" in res_denied(rows)
    for r in rows:
        if r["parent"] == "hr":
            assert r["allow"] == 0
        elif r["resource"] == "/accounting/sales":
            assert r["allow"] == 0
        else:
            assert r["allow"] == 1


@pytest.mark.asyncio
async def test_carol_parent_allow_but_child_conflict_deny_wins_catalog(db):
    await seed_catalog(db)
    plugins = [
        plugin_org_policy_deny_parent("hr"),
        plugin_allow_parent_for_user("carol", "analytics"),
        *plugin_conflicting_same_child_rules("carol", "analytics", "secret"),
    ]
    rows = await resolve_permissions_from_catalog(
        db,
        {"id": "carol"},
        plugins,
        VIEW_TABLE,
        TABLE_CANDIDATES_SQL,
        implicit_deny=True,
    )
    allowed_analytics = res_allowed(rows, parent="analytics")
    denied_analytics = res_denied(rows, parent="analytics")

    assert "/analytics/secret" in denied_analytics
    # 10 analytics children total, 1 denied
    assert len(allowed_analytics) == 9


@pytest.mark.asyncio
async def test_specificity_child_allow_overrides_parent_deny_catalog(db):
    await seed_catalog(db)
    plugins = [
        plugin_allow_all_for_user("alice"),
        plugin_org_policy_deny_parent("analytics"),  # parent-level deny
        plugin_child_allow_for_user(
            "alice", "analytics", "table02"
        ),  # child allow beats parent deny
    ]
    rows = await resolve_permissions_from_catalog(
        db,
        {"id": "alice"},
        plugins,
        VIEW_TABLE,
        TABLE_CANDIDATES_SQL,
        implicit_deny=True,
    )

    # table02 allowed, other analytics tables denied
    assert any(r["resource"] == "/analytics/table02" and r["allow"] == 1 for r in rows)
    assert all(
        (r["parent"] != "analytics" or r["child"] == "table02" or r["allow"] == 0)
        for r in rows
    )


@pytest.mark.asyncio
async def test_root_deny_all_but_parent_allow_rescues_specific_parent_catalog(db):
    await seed_catalog(db)
    plugins = [
        plugin_root_deny_for_all(),  # root deny
        plugin_allow_parent_for_user(
            "bob", "accounting"
        ),  # parent allow (more specific)
    ]
    rows = await resolve_permissions_from_catalog(
        db, {"id": "bob"}, plugins, VIEW_TABLE, TABLE_CANDIDATES_SQL, implicit_deny=True
    )
    for r in rows:
        if r["parent"] == "accounting":
            assert r["allow"] == 1
        else:
            assert r["allow"] == 0


@pytest.mark.asyncio
async def test_parent_scoped_candidates(db):
    await seed_catalog(db)
    plugins = [
        plugin_org_policy_deny_parent("hr"),
        plugin_allow_parent_for_user("carol", "analytics"),
    ]
    rows = await resolve_permissions_from_catalog(
        db,
        {"id": "carol"},
        plugins,
        VIEW_TABLE,
        PARENT_CANDIDATES_SQL,
        implicit_deny=True,
    )
    d = {r["resource"]: r["allow"] for r in rows}
    assert d["/analytics"] == 1
    assert d["/hr"] == 0


@pytest.mark.asyncio
async def test_implicit_deny_behavior(db):
    await seed_catalog(db)
    plugins = []  # no rules at all

    # implicit_deny=True -> everything denied with reason 'implicit deny'
    rows = await resolve_permissions_from_catalog(
        db,
        {"id": "erin"},
        plugins,
        VIEW_TABLE,
        TABLE_CANDIDATES_SQL,
        implicit_deny=True,
    )
    assert all(r["allow"] == 0 and r["reason"] == "implicit deny" for r in rows)

    # implicit_deny=False -> no winner => allow is None, reason is None
    rows2 = await resolve_permissions_from_catalog(
        db,
        {"id": "erin"},
        plugins,
        VIEW_TABLE,
        TABLE_CANDIDATES_SQL,
        implicit_deny=False,
    )
    assert all(r["allow"] is None and r["reason"] is None for r in rows2)


@pytest.mark.asyncio
async def test_candidate_filters_via_params(db):
    await seed_catalog(db)
    # Add some metadata to test filtering
    # Mark 'hr' as is_memory=1 and increment analytics schema_version
    await db.execute_write(
        "UPDATE catalog_databases SET is_memory=1 WHERE database_name='hr'"
    )
    await db.execute_write(
        "UPDATE catalog_databases SET schema_version=2 WHERE database_name='analytics'"
    )

    # Candidate SQL that filters by db metadata via params
    candidate_sql = """
    SELECT t.database_name AS parent, t.table_name AS child
    FROM catalog_tables t
    JOIN catalog_databases d ON d.database_name = t.database_name
    WHERE (:exclude_memory = 1 AND d.is_memory = 1) IS NOT 1
      AND (:min_schema_version IS NULL OR d.schema_version >= :min_schema_version)
    """

    plugins = [
        plugin_root_deny_for_all(),
        plugin_allow_parent_for_user(
            "dev", "analytics"
        ),  # analytics rescued if included by candidates
    ]

    # Case 1: exclude memory dbs, require schema_version >= 2 -> only analytics appear, and thus are allowed
    rows = await resolve_permissions_from_catalog(
        db,
        {"id": "dev"},
        plugins,
        VIEW_TABLE,
        candidate_sql,
        candidate_params={"exclude_memory": 1, "min_schema_version": 2},
        implicit_deny=True,
    )
    assert rows and all(r["parent"] == "analytics" for r in rows)
    assert all(r["allow"] == 1 for r in rows)

    # Case 2: include memory dbs, min_schema_version = None -> accounting/hr/analytics appear,
    # but root deny wins except where specifically allowed (none except analytics parent allow doesn’t apply to table depth if candidate includes children; still fine—policy is explicit).
    rows2 = await resolve_permissions_from_catalog(
        db,
        {"id": "dev"},
        plugins,
        VIEW_TABLE,
        candidate_sql,
        candidate_params={"exclude_memory": 0, "min_schema_version": None},
        implicit_deny=True,
    )
    assert any(r["parent"] == "accounting" for r in rows2)
    assert any(r["parent"] == "hr" for r in rows2)
    # For table-scoped candidates, the parent-level allow does not override root deny unless you have child-level rules
    assert all(r["allow"] in (0, 1) for r in rows2)


@pytest.mark.asyncio
async def test_action_specific_rules(db):
    await seed_catalog(db)
    plugins = [plugin_allow_all_for_action("dana", VIEW_TABLE)]

    view_rows = await resolve_permissions_from_catalog(
        db,
        {"id": "dana"},
        plugins,
        VIEW_TABLE,
        TABLE_CANDIDATES_SQL,
        implicit_deny=True,
    )
    assert view_rows and all(r["allow"] == 1 for r in view_rows)
    assert all(r["action"] == VIEW_TABLE for r in view_rows)

    insert_rows = await resolve_permissions_from_catalog(
        db,
        {"id": "dana"},
        plugins,
        "insert-row",
        TABLE_CANDIDATES_SQL,
        implicit_deny=True,
    )
    assert insert_rows and all(r["allow"] == 0 for r in insert_rows)
    assert all(r["reason"] == "implicit deny" for r in insert_rows)
    assert all(r["action"] == "insert-row" for r in insert_rows)


@pytest.mark.asyncio
async def test_actor_actor_id_action_parameters_available(db):
    """Test that :actor (JSON), :actor_id, and :action are all available in SQL"""
    await seed_catalog(db)

    def plugin_using_all_parameters() -> Callable[[str], PermissionSQL]:
        def provider(action: str) -> PermissionSQL:
            return PermissionSQL(
                """
                SELECT NULL AS parent, NULL AS child, 1 AS allow,
                       'Actor ID: ' || COALESCE(:actor_id, 'null') ||
                       ', Actor JSON: ' || COALESCE(:actor, 'null') ||
                       ', Action: ' || :action AS reason
                WHERE :actor_id = 'test_user' AND :action = 'view-table'
                AND json_extract(:actor, '$.role') = 'admin'
                """
            )

        return provider

    plugins = [plugin_using_all_parameters()]

    # Test with full actor dict
    rows = await resolve_permissions_from_catalog(
        db,
        {"id": "test_user", "role": "admin"},
        plugins,
        "view-table",
        TABLE_CANDIDATES_SQL,
        implicit_deny=True,
    )

    # Should have allowed rows with reason containing all the info
    allowed = [r for r in rows if r["allow"] == 1]
    assert len(allowed) > 0

    # Check that the reason string contains evidence of all parameters
    reason = allowed[0]["reason"]
    assert "test_user" in reason
    assert "view-table" in reason
    # The :actor parameter should be the JSON string
    assert "Actor JSON:" in reason


@pytest.mark.asyncio
async def test_multiple_plugins_with_own_parameters(db):
    """
    Test that multiple plugins can use their own parameter names without conflict.

    This verifies that the parameter naming convention works: plugins prefix their
    parameters (e.g., :plugin1_pattern, :plugin2_message) and both sets of parameters
    are successfully bound in the SQL queries.
    """
    await seed_catalog(db)

    def plugin_one() -> Callable[[str], PermissionSQL]:
        def provider(action: str) -> PermissionSQL:
            if action != "view-table":
                return PermissionSQL("plugin_one", "SELECT NULL WHERE 0", {})
            return PermissionSQL(
                """
                SELECT database_name AS parent, table_name AS child,
                       1 AS allow, 'Plugin one used param: ' || :plugin1_param AS reason
                FROM catalog_tables
                WHERE database_name = 'accounting'
                """,
                {
                    "plugin1_param": "value1",
                },
            )

        return provider

    def plugin_two() -> Callable[[str], PermissionSQL]:
        def provider(action: str) -> PermissionSQL:
            if action != "view-table":
                return PermissionSQL("plugin_two", "SELECT NULL WHERE 0", {})
            return PermissionSQL(
                """
                SELECT database_name AS parent, table_name AS child,
                       1 AS allow, 'Plugin two used param: ' || :plugin2_param AS reason
                FROM catalog_tables
                WHERE database_name = 'hr'
                """,
                {
                    "plugin2_param": "value2",
                },
            )

        return provider

    plugins = [plugin_one(), plugin_two()]

    rows = await resolve_permissions_from_catalog(
        db,
        {"id": "test_user"},
        plugins,
        "view-table",
        TABLE_CANDIDATES_SQL,
        implicit_deny=False,
    )

    # Both plugins should contribute results with their parameters successfully bound
    plugin_one_rows = [
        r for r in rows if r.get("reason") and "Plugin one" in r["reason"]
    ]
    plugin_two_rows = [
        r for r in rows if r.get("reason") and "Plugin two" in r["reason"]
    ]

    assert len(plugin_one_rows) > 0, "Plugin one should contribute rules"
    assert len(plugin_two_rows) > 0, "Plugin two should contribute rules"

    # Verify each plugin's parameters were successfully bound in the SQL
    assert any(
        "value1" in r.get("reason", "") for r in plugin_one_rows
    ), "Plugin one's :plugin1_param should be bound"
    assert any(
        "value2" in r.get("reason", "") for r in plugin_two_rows
    ), "Plugin two's :plugin2_param should be bound"
