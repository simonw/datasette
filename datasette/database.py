import asyncio
from collections import namedtuple
from pathlib import Path
import janus
import queue
import sqlite_utils
import sys
import threading
import uuid

from .tracer import trace
from .utils import (
    detect_fts,
    detect_primary_keys,
    detect_spatialite,
    get_all_foreign_keys,
    get_outbound_foreign_keys,
    md5_not_usedforsecurity,
    sqlite_timelimit,
    sqlite3,
    table_columns,
    table_column_details,
)
from .utils.sqlite import sqlite_version
from .inspect import inspect_hash

connections = threading.local()

AttachedDatabase = namedtuple("AttachedDatabase", ("seq", "name", "file"))


class Database:
    # For table counts stop at this many rows:
    count_limit = 10000
    _thread_local_id_counter = 1

    def __init__(
        self,
        ds,
        path=None,
        is_mutable=True,
        is_memory=False,
        memory_name=None,
        mode=None,
    ):
        self.name = None
        self._thread_local_id = f"x{self._thread_local_id_counter}"
        Database._thread_local_id_counter += 1
        self.route = None
        self.ds = ds
        self.path = path
        self.is_mutable = is_mutable
        self.is_memory = is_memory
        self.memory_name = memory_name
        if memory_name is not None:
            self.is_memory = True
        self.cached_hash = None
        self.cached_size = None
        self._cached_table_counts = None
        self._write_thread = None
        self._write_queue = None
        # These are used when in non-threaded mode:
        self._read_connection = None
        self._write_connection = None
        # This is used to track all file connections so they can be closed
        self._all_file_connections = []
        self.mode = mode

    @property
    def cached_table_counts(self):
        if self._cached_table_counts is not None:
            return self._cached_table_counts
        # Maybe use self.ds.inspect_data to populate cached_table_counts
        if self.ds.inspect_data and self.ds.inspect_data.get(self.name):
            self._cached_table_counts = {
                key: value["count"]
                for key, value in self.ds.inspect_data[self.name]["tables"].items()
            }
        return self._cached_table_counts

    @property
    def color(self):
        if self.hash:
            return self.hash[:6]
        return md5_not_usedforsecurity(self.name)[:6]

    def suggest_name(self):
        if self.path:
            return Path(self.path).stem
        elif self.memory_name:
            return self.memory_name
        else:
            return "db"

    def connect(self, write=False):
        extra_kwargs = {}
        if write:
            extra_kwargs["isolation_level"] = "IMMEDIATE"
        if self.memory_name:
            uri = "file:{}?mode=memory&cache=shared".format(self.memory_name)
            conn = sqlite3.connect(
                uri, uri=True, check_same_thread=False, **extra_kwargs
            )
            if not write:
                conn.execute("PRAGMA query_only=1")
            return conn
        if self.is_memory:
            return sqlite3.connect(":memory:", uri=True)

        # mode=ro or immutable=1?
        if self.is_mutable:
            qs = "?mode=ro"
            if self.ds.nolock:
                qs += "&nolock=1"
        else:
            qs = "?immutable=1"
        assert not (write and not self.is_mutable)
        if write:
            qs = ""
        if self.mode is not None:
            qs = f"?mode={self.mode}"
        conn = sqlite3.connect(
            f"file:{self.path}{qs}", uri=True, check_same_thread=False, **extra_kwargs
        )
        self._all_file_connections.append(conn)
        return conn

    def close(self):
        # Close all connections - useful to avoid running out of file handles in tests
        for connection in self._all_file_connections:
            connection.close()

    async def execute_write(self, sql, params=None, block=True):
        def _inner(conn):
            return conn.execute(sql, params or [])

        with trace("sql", database=self.name, sql=sql.strip(), params=params):
            results = await self.execute_write_fn(_inner, block=block)
        return results

    async def execute_write_script(self, sql, block=True):
        def _inner(conn):
            return conn.executescript(sql)

        with trace("sql", database=self.name, sql=sql.strip(), executescript=True):
            results = await self.execute_write_fn(_inner, block=block)
        return results

    async def execute_write_many(self, sql, params_seq, block=True):
        def _inner(conn):
            count = 0

            def count_params(params):
                nonlocal count
                for param in params:
                    count += 1
                    yield param

            return conn.executemany(sql, count_params(params_seq)), count

        with trace(
            "sql", database=self.name, sql=sql.strip(), executemany=True
        ) as kwargs:
            results, count = await self.execute_write_fn(_inner, block=block)
            kwargs["count"] = count
        return results

    async def execute_isolated_fn(self, fn):
        # Open a new connection just for the duration of this function
        # blocking the write queue to avoid any writes occurring during it
        if self.ds.executor is None:
            # non-threaded mode
            isolated_connection = self.connect(write=True)
            try:
                result = fn(isolated_connection)
            finally:
                isolated_connection.close()
                try:
                    self._all_file_connections.remove(isolated_connection)
                except ValueError:
                    # Was probably a memory connection
                    pass
            return result
        else:
            # Threaded mode - send to write thread
            return await self._send_to_write_thread(fn, isolated_connection=True)

    async def execute_write_fn(self, fn, block=True, transaction=True):
        if self.ds.executor is None:
            # non-threaded mode
            if self._write_connection is None:
                self._write_connection = self.connect(write=True)
                self.ds._prepare_connection(self._write_connection, self.name)
            if transaction:
                with self._write_connection:
                    return fn(self._write_connection)
            else:
                return fn(self._write_connection)
        else:
            return await self._send_to_write_thread(
                fn, block=block, transaction=transaction
            )

    async def _send_to_write_thread(
        self, fn, block=True, isolated_connection=False, transaction=True
    ):
        if self._write_queue is None:
            self._write_queue = queue.Queue()
        if self._write_thread is None:
            self._write_thread = threading.Thread(
                target=self._execute_writes, daemon=True
            )
            self._write_thread.name = "_execute_writes for database {}".format(
                self.name
            )
            self._write_thread.start()
        task_id = uuid.uuid5(uuid.NAMESPACE_DNS, "datasette.io")
        reply_queue = janus.Queue()
        self._write_queue.put(
            WriteTask(fn, task_id, reply_queue, isolated_connection, transaction)
        )
        if block:
            result = await reply_queue.async_q.get()
            if isinstance(result, Exception):
                raise result
            else:
                return result
        else:
            return task_id

    def _execute_writes(self):
        # Infinite looping thread that protects the single write connection
        # to this database
        conn_exception = None
        conn = None
        try:
            conn = self.connect(write=True)
            self.ds._prepare_connection(conn, self.name)
        except Exception as e:
            conn_exception = e
        while True:
            task = self._write_queue.get()
            if conn_exception is not None:
                result = conn_exception
            else:
                if task.isolated_connection:
                    isolated_connection = self.connect(write=True)
                    try:
                        result = task.fn(isolated_connection)
                    except Exception as e:
                        sys.stderr.write("{}\n".format(e))
                        sys.stderr.flush()
                        result = e
                    finally:
                        isolated_connection.close()
                        try:
                            self._all_file_connections.remove(isolated_connection)
                        except ValueError:
                            # Was probably a memory connection
                            pass
                else:
                    try:
                        if task.transaction:
                            with conn:
                                result = task.fn(conn)
                        else:
                            result = task.fn(conn)
                    except Exception as e:
                        sys.stderr.write("{}\n".format(e))
                        sys.stderr.flush()
                        result = e
            task.reply_queue.sync_q.put(result)

    async def execute_fn(self, fn):
        if self.ds.executor is None:
            # non-threaded mode
            if self._read_connection is None:
                self._read_connection = self.connect()
                self.ds._prepare_connection(self._read_connection, self.name)
            return fn(self._read_connection)

        # threaded mode
        def in_thread():
            conn = getattr(connections, self._thread_local_id, None)
            if not conn:
                conn = self.connect()
                self.ds._prepare_connection(conn, self.name)
                setattr(connections, self._thread_local_id, conn)
            return fn(conn)

        return await asyncio.get_event_loop().run_in_executor(
            self.ds.executor, in_thread
        )

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
        page_size = page_size or self.ds.page_size

        def sql_operation_in_thread(conn):
            time_limit_ms = self.ds.sql_time_limit_ms
            if custom_time_limit and custom_time_limit < time_limit_ms:
                time_limit_ms = custom_time_limit

            with sqlite_timelimit(conn, time_limit_ms):
                try:
                    cursor = conn.cursor()
                    cursor.execute(sql, params if params is not None else {})
                    max_returned_rows = self.ds.max_returned_rows
                    if max_returned_rows == page_size:
                        max_returned_rows += 1
                    if max_returned_rows and truncate:
                        rows = cursor.fetchmany(max_returned_rows + 1)
                        truncated = len(rows) > max_returned_rows
                        rows = rows[:max_returned_rows]
                    else:
                        rows = cursor.fetchall()
                        truncated = False
                except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
                    if e.args == ("interrupted",):
                        raise QueryInterrupted(e, sql, params)
                    if log_sql_errors:
                        sys.stderr.write(
                            "ERROR: conn={}, sql = {}, params = {}: {}\n".format(
                                conn, repr(sql), params, e
                            )
                        )
                        sys.stderr.flush()
                    raise

            if truncate:
                return Results(rows, truncated, cursor.description)

            else:
                return Results(rows, False, cursor.description)

        with trace("sql", database=self.name, sql=sql.strip(), params=params):
            results = await self.execute_fn(sql_operation_in_thread)
        return results

    @property
    def hash(self):
        if self.cached_hash is not None:
            return self.cached_hash
        elif self.is_mutable or self.is_memory:
            return None
        elif self.ds.inspect_data and self.ds.inspect_data.get(self.name):
            self.cached_hash = self.ds.inspect_data[self.name]["hash"]
            return self.cached_hash
        else:
            p = Path(self.path)
            self.cached_hash = inspect_hash(p)
            return self.cached_hash

    @property
    def size(self):
        if self.cached_size is not None:
            return self.cached_size
        elif self.is_memory:
            return 0
        elif self.is_mutable:
            return Path(self.path).stat().st_size
        elif self.ds.inspect_data and self.ds.inspect_data.get(self.name):
            self.cached_size = self.ds.inspect_data[self.name]["size"]
            return self.cached_size
        else:
            self.cached_size = Path(self.path).stat().st_size
            return self.cached_size

    async def table_counts(self, limit=10):
        if not self.is_mutable and self.cached_table_counts is not None:
            return self.cached_table_counts
        # Try to get counts for each table, $limit timeout for each count
        counts = {}
        for table in await self.table_names():
            try:
                table_count = (
                    await self.execute(
                        f"select count(*) from (select * from [{table}] limit {self.count_limit + 1})",
                        custom_time_limit=limit,
                    )
                ).rows[0][0]
                counts[table] = table_count
            # In some cases I saw "SQL Logic Error" here in addition to
            # QueryInterrupted - so we catch that too:
            except (QueryInterrupted, sqlite3.OperationalError, sqlite3.DatabaseError):
                counts[table] = None
        if not self.is_mutable:
            self._cached_table_counts = counts
        return counts

    @property
    def mtime_ns(self):
        if self.is_memory:
            return None
        return Path(self.path).stat().st_mtime_ns

    async def attached_databases(self):
        # This used to be:
        #   select seq, name, file from pragma_database_list() where seq > 0
        # But SQLite prior to 3.16.0 doesn't support pragma functions
        results = await self.execute("PRAGMA database_list;")
        # {'seq': 0, 'name': 'main', 'file': ''}
        return [AttachedDatabase(*row) for row in results.rows if row["seq"] > 0]

    async def table_exists(self, table):
        results = await self.execute(
            "select 1 from sqlite_master where type='table' and name=?", params=(table,)
        )
        return bool(results.rows)

    async def view_exists(self, table):
        results = await self.execute(
            "select 1 from sqlite_master where type='view' and name=?", params=(table,)
        )
        return bool(results.rows)

    async def table_names(self):
        results = await self.execute(
            "select name from sqlite_master where type='table'"
        )
        return [r[0] for r in results.rows]

    async def table_columns(self, table):
        return await self.execute_fn(lambda conn: table_columns(conn, table))

    async def table_column_details(self, table):
        return await self.execute_fn(lambda conn: table_column_details(conn, table))

    async def primary_keys(self, table):
        return await self.execute_fn(lambda conn: detect_primary_keys(conn, table))

    async def fts_table(self, table):
        return await self.execute_fn(lambda conn: detect_fts(conn, table))

    async def label_column_for_table(self, table):
        explicit_label_column = (await self.ds.table_config(self.name, table)).get(
            "label_column"
        )
        if explicit_label_column:
            return explicit_label_column

        def column_details(conn):
            # Returns {column_name: (type, is_unique)}
            db = sqlite_utils.Database(conn)
            columns = db[table].columns_dict
            indexes = db[table].indexes
            details = {}
            for name in columns:
                is_unique = any(
                    index
                    for index in indexes
                    if index.columns == [name] and index.unique
                )
                details[name] = (columns[name], is_unique)
            return details

        column_details = await self.execute_fn(column_details)
        # Is there just one unique column that's text?
        unique_text_columns = [
            name
            for name, (type_, is_unique) in column_details.items()
            if is_unique and type_ is str
        ]
        if len(unique_text_columns) == 1:
            return unique_text_columns[0]

        column_names = list(column_details.keys())
        # Is there a name or title column?
        name_or_title = [c for c in column_names if c.lower() in ("name", "title")]
        if name_or_title:
            return name_or_title[0]
        # If a table has two columns, one of which is ID, then label_column is the other one
        if (
            column_names
            and len(column_names) == 2
            and ("id" in column_names or "pk" in column_names)
            and not set(column_names) == {"id", "pk"}
        ):
            return [c for c in column_names if c not in ("id", "pk")][0]
        # Couldn't find a label:
        return None

    async def foreign_keys_for_table(self, table):
        return await self.execute_fn(
            lambda conn: get_outbound_foreign_keys(conn, table)
        )

    async def hidden_table_names(self):
        hidden_tables = []
        # Add any tables marked as hidden in config
        db_config = self.ds.config.get("databases", {}).get(self.name, {})
        if "tables" in db_config:
            hidden_tables += [
                t for t in db_config["tables"] if db_config["tables"][t].get("hidden")
            ]

        if sqlite_version()[1] >= 37:
            hidden_tables += [
                x[0]
                for x in await self.execute(
                    """
                      with shadow_tables as (
                        select name
                        from pragma_table_list
                        where [type] = 'shadow'
                        order by name
                      ),
                      core_tables as (
                        select name
                        from sqlite_master
                        WHERE  name in ('sqlite_stat1', 'sqlite_stat2', 'sqlite_stat3', 'sqlite_stat4')
                          OR substr(name, 1, 1) == '_'
                      ),
                      combined as (
                        select name from shadow_tables
                        union all
                        select name from core_tables
                      )
                      select name from combined order by 1
                    """
                )
            ]
        else:
            hidden_tables += [
                x[0]
                for x in await self.execute(
                    """
                      WITH base AS (
                        SELECT name
                        FROM sqlite_master
                        WHERE  name IN ('sqlite_stat1', 'sqlite_stat2', 'sqlite_stat3', 'sqlite_stat4')
                          OR substr(name, 1, 1) == '_'
                      ),
                      fts_suffixes AS (
                        SELECT column1 AS suffix
                        FROM (VALUES ('_data'), ('_idx'), ('_docsize'), ('_content'), ('_config'))
                      ),
                      fts5_names AS (
                        SELECT name
                        FROM sqlite_master
                        WHERE sql LIKE '%VIRTUAL TABLE%USING FTS%'
                      ),
                      fts5_shadow_tables AS (
                        SELECT
                          printf('%s%s', fts5_names.name, fts_suffixes.suffix) AS name
                        FROM fts5_names
                        JOIN fts_suffixes
                      ),
                      fts3_suffixes AS (
                        SELECT column1 AS suffix
                        FROM (VALUES ('_content'), ('_segdir'), ('_segments'), ('_stat'), ('_docsize'))
                      ),
                      fts3_names AS (
                        SELECT name
                        FROM sqlite_master
                        WHERE sql LIKE '%VIRTUAL TABLE%USING FTS3%'
                          OR sql LIKE '%VIRTUAL TABLE%USING FTS4%'
                      ),
                      fts3_shadow_tables AS (
                        SELECT
                          printf('%s%s', fts3_names.name, fts3_suffixes.suffix) AS name
                        FROM fts3_names
                        JOIN fts3_suffixes
                      ),
                      final AS (
                        SELECT name FROM base
                        UNION ALL
                        SELECT name FROM fts5_shadow_tables
                        UNION ALL
                        SELECT name FROM fts3_shadow_tables
                      )
                      SELECT name FROM final ORDER BY 1
                    """
                )
            ]
        # Also hide any FTS tables that have a content= argument
        hidden_tables += [
            x[0]
            for x in await self.execute(
                """
                  SELECT name
                  FROM sqlite_master
                  WHERE sql LIKE '%VIRTUAL TABLE%'
                    AND sql LIKE '%USING FTS%'
                    AND sql LIKE '%content=%'
                """
            )
        ]

        has_spatialite = await self.execute_fn(detect_spatialite)
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
                "data_licenses",
                "KNN",
                "KNN2",
            ] + [
                r[0]
                for r in (
                    await self.execute(
                        """
                        select name from sqlite_master
                        where name like "idx_%"
                        and type = "table"
                    """
                    )
                ).rows
            ]

        return hidden_tables

    async def view_names(self):
        results = await self.execute("select name from sqlite_master where type='view'")
        return [r[0] for r in results.rows]

    async def get_all_foreign_keys(self):
        return await self.execute_fn(get_all_foreign_keys)

    async def get_table_definition(self, table, type_="table"):
        table_definition_rows = list(
            await self.execute(
                "select sql from sqlite_master where name = :n and type=:t",
                {"n": table, "t": type_},
            )
        )
        if not table_definition_rows:
            return None
        bits = [table_definition_rows[0][0] + ";"]
        # Add on any indexes
        index_rows = list(
            await self.execute(
                "select sql from sqlite_master where tbl_name = :n and type='index' and sql is not null",
                {"n": table},
            )
        )
        for index_row in index_rows:
            bits.append(index_row[0] + ";")
        return "\n".join(bits)

    async def get_view_definition(self, view):
        return await self.get_table_definition(view, "view")

    def __repr__(self):
        tags = []
        if self.is_mutable:
            tags.append("mutable")
        if self.is_memory:
            tags.append("memory")
        if self.hash:
            tags.append(f"hash={self.hash}")
        if self.size is not None:
            tags.append(f"size={self.size}")
        tags_str = ""
        if tags:
            tags_str = f" ({', '.join(tags)})"
        return f"<Database: {self.name}{tags_str}>"


class WriteTask:
    __slots__ = ("fn", "task_id", "reply_queue", "isolated_connection", "transaction")

    def __init__(self, fn, task_id, reply_queue, isolated_connection, transaction):
        self.fn = fn
        self.task_id = task_id
        self.reply_queue = reply_queue
        self.isolated_connection = isolated_connection
        self.transaction = transaction


class QueryInterrupted(Exception):
    def __init__(self, e, sql, params):
        self.e = e
        self.sql = sql
        self.params = params

    def __str__(self):
        return "QueryInterrupted: {}".format(self.e)


class MultipleValues(Exception):
    pass


class Results:
    def __init__(self, rows, truncated, description):
        self.rows = rows
        self.truncated = truncated
        self.description = description

    @property
    def columns(self):
        return [d[0] for d in self.description]

    def first(self):
        if self.rows:
            return self.rows[0]
        else:
            return None

    def single_value(self):
        if self.rows and 1 == len(self.rows) and 1 == len(self.rows[0]):
            return self.rows[0][0]
        else:
            raise MultipleValues

    def dicts(self):
        return [dict(row) for row in self.rows]

    def __iter__(self):
        return iter(self.rows)

    def __len__(self):
        return len(self.rows)
