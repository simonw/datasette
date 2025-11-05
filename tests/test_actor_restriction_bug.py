"""
Test for actor restrictions bug with database-level config.

This test currently FAILS, demonstrating the bug where database-level
config allow blocks can bypass table-level restrictions.
"""

import pytest
from datasette.app import Datasette
from datasette.resources import TableResource


@pytest.mark.asyncio
async def test_table_restrictions_not_bypassed_by_database_level_config():
    """
    Actor restrictions should act as hard limits that config cannot override.

    BUG: When an actor has table-level restrictions (e.g., only table2 and table3)
    but config has a database-level allow block, the database-level config rule
    currently allows ALL tables, not just those in the restriction allowlist.

    This test documents the expected behavior and will FAIL until the bug is fixed.
    """
    # Config grants access at DATABASE level (not table level)
    config = {
        "databases": {
            "test_db_rnbbdlc": {
                "allow": {
                    "id": "user"
                }  # Database-level allow - grants access to all tables
            }
        }
    }

    ds = Datasette(config=config)
    await ds.invoke_startup()
    db = ds.add_memory_database("test_db_rnbbdlc")
    await db.execute_write("create table table1 (id integer primary key)")
    await db.execute_write("create table table2 (id integer primary key)")
    await db.execute_write("create table table3 (id integer primary key)")
    await db.execute_write("create table table4 (id integer primary key)")

    # Actor restricted to ONLY table2 and table3
    # Even though config allows the whole database, restrictions should limit access
    actor = {
        "id": "user",
        "_r": {
            "r": {  # Resource-level (table-level) restrictions
                "test_db_rnbbdlc": {
                    "table2": ["vt"],  # vt = view-table abbreviation
                    "table3": ["vt"],
                }
            }
        },
    }

    # table2 should be allowed (in restriction allowlist AND config allows)
    result = await ds.allowed(
        action="view-table",
        resource=TableResource("test_db_rnbbdlc", "table2"),
        actor=actor,
    )
    assert result is True, "table2 should be allowed - in restriction allowlist"

    # table3 should be allowed (in restriction allowlist AND config allows)
    result = await ds.allowed(
        action="view-table",
        resource=TableResource("test_db_rnbbdlc", "table3"),
        actor=actor,
    )
    assert result is True, "table3 should be allowed - in restriction allowlist"

    # table1 should be DENIED (NOT in restriction allowlist)
    # Even though database-level config allows it, restrictions should deny it
    result = await ds.allowed(
        action="view-table",
        resource=TableResource("test_db_rnbbdlc", "table1"),
        actor=actor,
    )
    assert (
        result is False
    ), "table1 should be DENIED - not in restriction allowlist, config cannot override"

    # table4 should be DENIED (NOT in restriction allowlist)
    # Even though database-level config allows it, restrictions should deny it
    result = await ds.allowed(
        action="view-table",
        resource=TableResource("test_db_rnbbdlc", "table4"),
        actor=actor,
    )
    assert (
        result is False
    ), "table4 should be DENIED - not in restriction allowlist, config cannot override"


@pytest.mark.asyncio
async def test_database_restrictions_with_database_level_config():
    """
    Verify that database-level restrictions work correctly with database-level config.

    This should pass - it's testing the case where restriction granularity
    matches config granularity.
    """
    config = {
        "databases": {"test_db_rwdl": {"allow": {"id": "user"}}}  # Database-level allow
    }

    ds = Datasette(config=config)
    await ds.invoke_startup()
    db = ds.add_memory_database("test_db_rwdl")
    await db.execute_write("create table table1 (id integer primary key)")
    await db.execute_write("create table table2 (id integer primary key)")

    # Actor has database-level restriction (all tables in test_db_rwdl)
    actor = {
        "id": "user",
        "_r": {"d": {"test_db_rwdl": ["vt"]}},  # Database-level restrictions
    }

    # Both tables should be allowed (database-level restriction matches database-level config)
    result = await ds.allowed(
        action="view-table",
        resource=TableResource("test_db_rwdl", "table1"),
        actor=actor,
    )
    assert result is True, "table1 should be allowed"

    result = await ds.allowed(
        action="view-table",
        resource=TableResource("test_db_rwdl", "table2"),
        actor=actor,
    )
    assert result is True, "table2 should be allowed"
