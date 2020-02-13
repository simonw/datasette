from .utils import Results
import asyncpg


class PostgresqlResults:
    def __init__(self, rows, truncated):
        self.rows = rows
        self.truncated = truncated

    @property
    def description(self):
        return [[c] for c in self.columns]

    @property
    def columns(self):
        try:
            return list(self.rows[0].keys())
        except IndexError:
            return []

    def __iter__(self):
        return iter(self.rows)

    def __len__(self):
        return len(self.rows)


class PostgresqlDatabase:
    size = 0
    is_mutable = False
    is_memory = False
    hash = None

    def __init__(self, ds, name, dsn):
        self.ds = ds
        self.name = name
        self.dsn = dsn
        self._connection = None

    async def connection(self):
        if self._connection is None:
            self._connection = await asyncpg.connect(self.dsn)
        return self._connection

    async def execute(
        self,
        sql,
        params=None,
        truncate=False,
        custom_time_limit=None,
        page_size=None,
        log_sql_errors=True,
    ):
        """Executes sql against db_name in a thread"""
        print(sql, params)
        rows = await (await self.connection()).fetch(sql)
        # Annoyingly if there are 0 results we cannot use the equivalent
        # of SQLite cursor.description to figure out what the columns
        # should have been. I haven't found a workaround for that yet
        # return Results(rows, truncated, cursor.description)
        return PostgresqlResults(rows, truncated=False)

    async def table_counts(self, limit=10):
        # Try to get counts for each table, TODO: $limit ms timeout for each count
        counts = {}
        for table in await self.table_names():
            table_count = await (await self.connection()).fetchval(
                "select count(*) from {}".format(table)
            )
            counts[table] = table_count
        return counts

    async def table_exists(self, table):
        return table in await self.table_names()

    async def table_names(self):
        results = await self.execute(
            "select tablename from pg_catalog.pg_tables where schemaname not in ('pg_catalog', 'information_schema')"
        )
        return [r[0] for r in results.rows]

    async def table_columns(self, table):
        sql = """SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
            AND table_name = '{}'
        """.format(
            table
        )
        results = await self.execute(sql)
        return [r[0] for r in results.rows]

    async def primary_keys(self, table):
        sql = """
        SELECT a.attname
            FROM   pg_index i
            JOIN   pg_attribute a ON a.attrelid = i.indrelid
                                 AND a.attnum = ANY(i.indkey)
            WHERE  i.indrelid = '{}'::regclass
            AND    i.indisprimary;""".format(
            table
        )
        results = await self.execute(sql)
        return [r[0] for r in results.rows]

    async def fts_table(self, table):
        return None
        # return await self.execute_against_connection_in_thread(
        #     lambda conn: detect_fts(conn, table)
        # )

    async def label_column_for_table(self, table):
        explicit_label_column = self.ds.table_metadata(self.name, table).get(
            "label_column"
        )
        if explicit_label_column:
            return explicit_label_column
        # If a table has two columns, one of which is ID, then label_column is the other one
        column_names = await self.execute_against_connection_in_thread(
            lambda conn: table_columns(conn, table)
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
        # return await self.execute_against_connection_in_thread(
        #     lambda conn: get_outbound_foreign_keys(conn, table)
        # )
        return []

    async def hidden_table_names(self):
        # Just the metadata.json ones:
        hidden_tables = []
        db_metadata = self.ds.metadata(database=self.name)
        if "tables" in db_metadata:
            hidden_tables += [
                t
                for t in db_metadata["tables"]
                if db_metadata["tables"][t].get("hidden")
            ]
        return hidden_tables

    async def view_names(self):
        # results = await self.execute("select name from sqlite_master where type='view'")
        return []

    async def get_all_foreign_keys(self):
        # return await self.execute_against_connection_in_thread(get_all_foreign_keys)
        return {t: [] for t in await self.table_names()}

    async def get_outbound_foreign_keys(self, table):
        # return await self.execute_against_connection_in_thread(
        #     lambda conn: get_outbound_foreign_keys(conn, table)
        # )
        return []

    async def get_table_definition(self, table, type_="table"):
        sql = """
        SELECT                                          
        'CREATE TABLE ' || relname || E'\n(\n' ||
        array_to_string(
            array_agg(
            '    ' || column_name || ' ' ||  type || ' '|| not_null
            )
            , E',\n'
        ) || E'\n);\n'
        from
        (
        SELECT
            c.relname, a.attname AS column_name,
            pg_catalog.format_type(a.atttypid, a.atttypmod) as type,
            case
            when a.attnotnull
            then 'NOT NULL'
            else 'NULL'
            END as not_null
        FROM pg_class c,
        pg_attribute a,
        pg_type t
        WHERE c.relname = $1
        AND a.attnum > 0
        AND a.attrelid = c.oid
        AND a.atttypid = t.oid
        ORDER BY a.attnum
        ) as tabledefinition
        group by relname;
        """
        return await (await self.connection()).fetchval(sql, table)

    async def get_view_definition(self, view):
        # return await self.get_table_definition(view, "view")
        return []

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
