from pathlib import Path

from .utils import (
    QueryInterrupted,
    detect_fts,
    detect_primary_keys,
    detect_spatialite,
    get_all_foreign_keys,
    get_outbound_foreign_keys,
    sqlite3,
    table_columns,
)
from .inspect import inspect_hash


class Database:
    def __init__(self, ds, path=None, is_mutable=False, is_memory=False):
        self.ds = ds
        self.path = path
        self.is_mutable = is_mutable
        self.is_memory = is_memory
        self.hash = None
        self.cached_size = None
        self.cached_table_counts = None
        if not self.is_mutable:
            p = Path(path)
            self.hash = inspect_hash(p)
            self.cached_size = p.stat().st_size
            # Maybe use self.ds.inspect_data to populate cached_table_counts
            if self.ds.inspect_data and self.ds.inspect_data.get(self.name):
                self.cached_table_counts = {
                    key: value["count"]
                    for key, value in self.ds.inspect_data[self.name]["tables"].items()
                }

    @property
    def size(self):
        if self.is_memory:
            return 0
        if self.cached_size is not None:
            return self.cached_size
        else:
            return Path(self.path).stat().st_size

    async def table_counts(self, limit=10):
        if not self.is_mutable and self.cached_table_counts is not None:
            return self.cached_table_counts
        # Try to get counts for each table, $limit timeout for each count
        counts = {}
        for table in await self.table_names():
            try:
                table_count = (
                    await self.ds.execute(
                        self.name,
                        "select count(*) from [{}]".format(table),
                        custom_time_limit=limit,
                    )
                ).rows[0][0]
                counts[table] = table_count
            # In some cases I saw "SQL Logic Error" here in addition to
            # QueryInterrupted - so we catch that too:
            except (QueryInterrupted, sqlite3.OperationalError):
                counts[table] = None
        if not self.is_mutable:
            self.cached_table_counts = counts
        return counts

    @property
    def mtime_ns(self):
        return Path(self.path).stat().st_mtime_ns

    @property
    def name(self):
        if self.is_memory:
            return ":memory:"
        else:
            return Path(self.path).stem

    async def table_exists(self, table):
        results = await self.ds.execute(
            self.name,
            "select 1 from sqlite_master where type='table' and name=?",
            params=(table,),
        )
        return bool(results.rows)

    async def table_names(self):
        results = await self.ds.execute(
            self.name, "select name from sqlite_master where type='table'"
        )
        return [r[0] for r in results.rows]

    async def table_columns(self, table):
        return await self.ds.execute_against_connection_in_thread(
            self.name, lambda conn: table_columns(conn, table)
        )

    async def primary_keys(self, table):
        return await self.ds.execute_against_connection_in_thread(
            self.name, lambda conn: detect_primary_keys(conn, table)
        )

    async def fts_table(self, table):
        return await self.ds.execute_against_connection_in_thread(
            self.name, lambda conn: detect_fts(conn, table)
        )

    async def label_column_for_table(self, table):
        explicit_label_column = self.ds.table_metadata(self.name, table).get(
            "label_column"
        )
        if explicit_label_column:
            return explicit_label_column
        # If a table has two columns, one of which is ID, then label_column is the other one
        column_names = await self.ds.execute_against_connection_in_thread(
            self.name, lambda conn: table_columns(conn, table)
        )
        # Is there a name or title column?
        name_or_title = [c for c in column_names if c in ("name", "title")]
        if name_or_title:
            return name_or_title[0]
        if (
            column_names
            and len(column_names) == 2
            and ("id" in column_names or "pk" in column_names)
        ):
            return [c for c in column_names if c not in ("id", "pk")][0]
        # Couldn't find a label:
        return None

    async def foreign_keys_for_table(self, table):
        return await self.ds.execute_against_connection_in_thread(
            self.name, lambda conn: get_outbound_foreign_keys(conn, table)
        )

    async def hidden_table_names(self):
        # Mark tables 'hidden' if they relate to FTS virtual tables
        hidden_tables = [
            r[0]
            for r in (
                await self.ds.execute(
                    self.name,
                    """
                select name from sqlite_master
                where rootpage = 0
                and sql like '%VIRTUAL TABLE%USING FTS%'
            """,
                )
            ).rows
        ]
        has_spatialite = await self.ds.execute_against_connection_in_thread(
            self.name, detect_spatialite
        )
        if has_spatialite:
            # Also hide Spatialite internal tables
            hidden_tables += [
                "ElementaryGeometries",
                "SpatialIndex",
                "geometry_columns",
                "spatial_ref_sys",
                "spatialite_history",
                "sql_statements_log",
                "sqlite_sequence",
                "views_geometry_columns",
                "virts_geometry_columns",
            ] + [
                r[0]
                for r in (
                    await self.ds.execute(
                        self.name,
                        """
                        select name from sqlite_master
                        where name like "idx_%"
                        and type = "table"
                    """,
                    )
                ).rows
            ]
        # Add any from metadata.json
        db_metadata = self.ds.metadata(database=self.name)
        if "tables" in db_metadata:
            hidden_tables += [
                t
                for t in db_metadata["tables"]
                if db_metadata["tables"][t].get("hidden")
            ]
        # Also mark as hidden any tables which start with the name of a hidden table
        # e.g. "searchable_fts" implies "searchable_fts_content" should be hidden
        for table_name in await self.table_names():
            for hidden_table in hidden_tables[:]:
                if table_name.startswith(hidden_table):
                    hidden_tables.append(table_name)
                    continue

        return hidden_tables

    async def view_names(self):
        results = await self.ds.execute(
            self.name, "select name from sqlite_master where type='view'"
        )
        return [r[0] for r in results.rows]

    async def get_all_foreign_keys(self):
        return await self.ds.execute_against_connection_in_thread(
            self.name, get_all_foreign_keys
        )

    async def get_outbound_foreign_keys(self, table):
        return await self.ds.execute_against_connection_in_thread(
            self.name, lambda conn: get_outbound_foreign_keys(conn, table)
        )

    async def get_table_definition(self, table, type_="table"):
        table_definition_rows = list(
            await self.ds.execute(
                self.name,
                "select sql from sqlite_master where name = :n and type=:t",
                {"n": table, "t": type_},
            )
        )
        if not table_definition_rows:
            return None
        return table_definition_rows[0][0]

    async def get_view_definition(self, view):
        return await self.get_table_definition(view, "view")

    def __repr__(self):
        tags = []
        if self.is_mutable:
            tags.append("mutable")
        if self.is_memory:
            tags.append("memory")
        if self.hash:
            tags.append("hash={}".format(self.hash))
        if self.size is not None:
            tags.append("size={}".format(self.size))
        tags_str = ""
        if tags:
            tags_str = " ({})".format(", ".join(tags))
        return "<Database: {}{}>".format(self.name, tags_str)
