import pytest
from datasette.app import Datasette
from datasette.permissions import PermissionSQL
from datasette.resources import TableResource


@pytest.mark.asyncio
async def test_multiple_restriction_sources_intersect():
    """
    Test that when multiple plugins return restriction_sql, they are INTERSECTed.

    This tests the case where both actor _r restrictions AND a plugin
    provide restriction_sql - both must pass for access to be granted.
    """
    from datasette import hookimpl
    from datasette.plugins import pm

    class RestrictivePlugin:
        __name__ = "RestrictivePlugin"

        @hookimpl
        def permission_resources_sql(self, datasette, actor, action):
            # Plugin adds additional restriction: only db1_multi_intersect allowed
            if action == "view-table":
                return PermissionSQL(
                    restriction_sql="SELECT 'db1_multi_intersect' AS parent, NULL AS child",
                    params={},
                )
            return None

    plugin = RestrictivePlugin()
    pm.register(plugin, name="restrictive_plugin")

    try:
        ds = Datasette()
        await ds.invoke_startup()
        db1 = ds.add_memory_database("db1_multi_intersect")
        db2 = ds.add_memory_database("db2_multi_intersect")
        await db1.execute_write("CREATE TABLE t1 (id INTEGER)")
        await db2.execute_write("CREATE TABLE t1 (id INTEGER)")
        await ds._refresh_schemas()  # Populate catalog tables

        # Actor has restrictions allowing both databases
        # But plugin only allows db1_multi_intersect
        # INTERSECT means only db1_multi_intersect/t1 should pass
        actor = {
            "id": "user",
            "_r": {"d": {"db1_multi_intersect": ["vt"], "db2_multi_intersect": ["vt"]}},
        }

        page = await ds.allowed_resources("view-table", actor)
        resources = {(r.parent, r.child) for r in page.resources}

        # Should only see db1_multi_intersect/t1 (intersection of actor restrictions and plugin restrictions)
        assert ("db1_multi_intersect", "t1") in resources
        assert ("db2_multi_intersect", "t1") not in resources
    finally:
        pm.unregister(name="restrictive_plugin")


@pytest.mark.asyncio
async def test_restriction_sql_with_overlapping_databases_and_tables():
    """
    Test actor with both database-level and table-level restrictions for same database.

    When actor has:
    - Database-level: db1_overlapping allowed (all tables)
    - Table-level: db1_overlapping/t1 allowed

    Both entries are UNION'd (OR'ed) within the actor's restrictions.
    Database-level restriction allows ALL tables, so table-level is redundant.
    """
    ds = Datasette()
    await ds.invoke_startup()
    db = ds.add_memory_database("db1_overlapping")
    await db.execute_write("CREATE TABLE t1 (id INTEGER)")
    await db.execute_write("CREATE TABLE t2 (id INTEGER)")
    await ds._refresh_schemas()

    # Actor has BOTH database-level (db1_overlapping all tables) AND table-level (db1_overlapping/t1 only)
    actor = {
        "id": "user",
        "_r": {
            "d": {
                "db1_overlapping": ["vt"]
            },  # Database-level: all tables in db1_overlapping
            "r": {
                "db1_overlapping": {"t1": ["vt"]}
            },  # Table-level: only t1 in db1_overlapping
        },
    }

    # Within actor restrictions, entries are UNION'd (OR'ed):
    # - Database level allows: (db1_overlapping, NULL) → matches all tables via hierarchical matching
    # - Table level allows: (db1_overlapping, t1) → redundant, already covered by database level
    # Result: Both tables are allowed
    page = await ds.allowed_resources("view-table", actor)
    resources = {(r.parent, r.child) for r in page.resources}

    assert ("db1_overlapping", "t1") in resources
    # Database-level restriction allows all tables, so t2 is also allowed
    assert ("db1_overlapping", "t2") in resources


@pytest.mark.asyncio
async def test_restriction_sql_empty_allowlist_query():
    """
    Test the specific SQL query generated when action is not in allowlist.

    actor_restrictions_sql() returns "SELECT NULL AS parent, NULL AS child WHERE 0"
    Verify this produces an empty result set.
    """
    ds = Datasette()
    await ds.invoke_startup()
    db = ds.add_memory_database("db1_empty_allowlist")
    await db.execute_write("CREATE TABLE t1 (id INTEGER)")
    await ds._refresh_schemas()

    # Actor has restrictions but action not in allowlist
    actor = {"id": "user", "_r": {"r": {"db1_empty_allowlist": {"t1": ["vt"]}}}}

    # Try to view-database (only view-table is in allowlist)
    page = await ds.allowed_resources("view-database", actor)

    # Should be empty
    assert len(page.resources) == 0


@pytest.mark.asyncio
async def test_restriction_sql_with_pagination():
    """
    Test that restrictions work correctly with keyset pagination.
    """
    ds = Datasette()
    await ds.invoke_startup()
    db = ds.add_memory_database("db1_pagination")

    # Create many tables
    for i in range(10):
        await db.execute_write(f"CREATE TABLE t{i:02d} (id INTEGER)")
    await ds._refresh_schemas()

    # Actor restricted to only odd-numbered tables
    restrictions = {"r": {"db1_pagination": {}}}
    for i in range(10):
        if i % 2 == 1:  # Only odd tables
            restrictions["r"]["db1_pagination"][f"t{i:02d}"] = ["vt"]

    actor = {"id": "user", "_r": restrictions}

    # Get first page with small limit
    page1 = await ds.allowed_resources(
        "view-table", actor, parent="db1_pagination", limit=2
    )
    assert len(page1.resources) == 2
    assert page1.next is not None

    # Get second page using next token
    page2 = await ds.allowed_resources(
        "view-table", actor, parent="db1_pagination", limit=2, next=page1.next
    )
    assert len(page2.resources) == 2

    # Should have no overlap
    page1_ids = {r.child for r in page1.resources}
    page2_ids = {r.child for r in page2.resources}
    assert page1_ids.isdisjoint(page2_ids)

    # All should be odd-numbered tables
    all_ids = page1_ids | page2_ids
    for table_id in all_ids:
        table_num = int(table_id[1:])  # Extract number from "t01", "t03", etc.
        assert table_num % 2 == 1, f"Table {table_id} should be odd-numbered"


@pytest.mark.asyncio
async def test_also_requires_with_restrictions():
    """
    Test that also_requires actions properly respect restrictions.

    execute-sql requires view-database. With restrictions, both must pass.
    """
    ds = Datasette()
    await ds.invoke_startup()
    db1 = ds.add_memory_database("db1_also_requires")
    db2 = ds.add_memory_database("db2_also_requires")
    await ds._refresh_schemas()

    # Actor restricted to only db1_also_requires for view-database
    # execute-sql requires view-database, so should only work on db1_also_requires
    actor = {
        "id": "user",
        "_r": {
            "d": {
                "db1_also_requires": ["vd", "es"],
                "db2_also_requires": [
                    "es"
                ],  # They have execute-sql but not view-database
            }
        },
    }

    # db1_also_requires should allow execute-sql
    result = await ds.allowed(
        action="execute-sql",
        resource=TableResource("db1_also_requires", None),
        actor=actor,
    )
    assert result is True

    # db2_also_requires should not (they have execute-sql but not view-database)
    result = await ds.allowed(
        action="execute-sql",
        resource=TableResource("db2_also_requires", None),
        actor=actor,
    )
    assert result is False


@pytest.mark.asyncio
async def test_restriction_abbreviations_and_full_names():
    """
    Test that both abbreviations and full action names work in restrictions.
    """
    ds = Datasette()
    await ds.invoke_startup()
    db = ds.add_memory_database("db1_abbrev")
    await db.execute_write("CREATE TABLE t1 (id INTEGER)")
    await ds._refresh_schemas()

    # Test with abbreviation
    actor_abbr = {"id": "user", "_r": {"r": {"db1_abbrev": {"t1": ["vt"]}}}}
    result = await ds.allowed(
        action="view-table",
        resource=TableResource("db1_abbrev", "t1"),
        actor=actor_abbr,
    )
    assert result is True

    # Test with full name
    actor_full = {"id": "user", "_r": {"r": {"db1_abbrev": {"t1": ["view-table"]}}}}
    result = await ds.allowed(
        action="view-table",
        resource=TableResource("db1_abbrev", "t1"),
        actor=actor_full,
    )
    assert result is True

    # Test with mixed
    actor_mixed = {"id": "user", "_r": {"d": {"db1_abbrev": ["view-database", "vt"]}}}
    result = await ds.allowed(
        action="view-table",
        resource=TableResource("db1_abbrev", "t1"),
        actor=actor_mixed,
    )
    assert result is True


@pytest.mark.asyncio
async def test_permission_resources_sql_multiple_restriction_sources_intersect():
    """
    Test that when multiple plugins return restriction_sql, they are INTERSECTed.

    This tests the case where both actor _r restrictions AND a plugin
    provide restriction_sql - both must pass for access to be granted.
    """
    from datasette import hookimpl
    from datasette.plugins import pm

    class RestrictivePlugin:
        __name__ = "RestrictivePlugin"

        @hookimpl
        def permission_resources_sql(self, datasette, actor, action):
            # Plugin adds additional restriction: only db1_multi_restrictions allowed
            if action == "view-table":
                return PermissionSQL(
                    restriction_sql="SELECT 'db1_multi_restrictions' AS parent, NULL AS child",
                    params={},
                )
            return None

    plugin = RestrictivePlugin()
    pm.register(plugin, name="restrictive_plugin")

    try:
        ds = Datasette()
        await ds.invoke_startup()
        db1 = ds.add_memory_database("db1_multi_restrictions")
        db2 = ds.add_memory_database("db2_multi_restrictions")
        await db1.execute_write("CREATE TABLE t1 (id INTEGER)")
        await db2.execute_write("CREATE TABLE t1 (id INTEGER)")
        await ds._refresh_schemas()  # Populate catalog tables

        # Actor has restrictions allowing both databases
        # But plugin only allows db1
        # INTERSECT means only db1/t1 should pass
        actor = {
            "id": "user",
            "_r": {
                "d": {
                    "db1_multi_restrictions": ["vt"],
                    "db2_multi_restrictions": ["vt"],
                }
            },
        }

        page = await ds.allowed_resources("view-table", actor)
        resources = {(r.parent, r.child) for r in page.resources}

        # Should only see db1/t1 (intersection of actor restrictions and plugin restrictions)
        assert ("db1_multi_restrictions", "t1") in resources
        assert ("db2_multi_restrictions", "t1") not in resources
    finally:
        pm.unregister(name="restrictive_plugin")
