from abc import ABC, abstractproperty
from dataclasses import asdict, dataclass, field
from datasette.hookspecs import hookimpl
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Event(ABC):
    @abstractproperty
    def name(self):
        pass

    created: datetime = field(
        init=False, default_factory=lambda: datetime.now(timezone.utc)
    )
    actor: Optional[dict]

    def properties(self):
        properties = asdict(self)
        properties.pop("actor", None)
        properties.pop("created", None)
        return properties


@dataclass
class LoginEvent(Event):
    """
    Event name: ``login``

    A user (represented by ``event.actor``) has logged in.
    """

    name = "login"


@dataclass
class LogoutEvent(Event):
    """
    Event name: ``logout``

    A user (represented by ``event.actor``) has logged out.
    """

    name = "logout"


@dataclass
class CreateTokenEvent(Event):
    """
    Event name: ``create-token``

    A user created an API token.

    :ivar expires_after: Number of seconds after which this token will expire.
    :type expires_after: int or None
    :ivar restrict_all: Restricted permissions for this token.
    :type restrict_all: list
    :ivar restrict_database: Restricted database permissions for this token.
    :type restrict_database: dict
    :ivar restrict_resource: Restricted resource permissions for this token.
    :type restrict_resource: dict
    """

    name = "create-token"
    expires_after: Optional[int]
    restrict_all: list
    restrict_database: dict
    restrict_resource: dict


@dataclass
class CreateTableEvent(Event):
    """
    Event name: ``create-table``

    A new table has been created in the database.

    :ivar database: The name of the database where the table was created.
    :type database: str
    :ivar table: The name of the table that was created
    :type table: str
    :ivar schema: The SQL schema definition for the new table.
    :type schema: str
    """

    name = "create-table"
    database: str
    table: str
    schema: str


@dataclass
class DropTableEvent(Event):
    """
    Event name: ``drop-table``

    A table has been dropped from the database.

    :ivar database: The name of the database where the table was dropped.
    :type database: str
    :ivar table: The name of the table that was dropped
    :type table: str
    """

    name = "drop-table"
    database: str
    table: str


@dataclass
class AlterTableEvent(Event):
    """
    Event name: ``alter-table``

    A table has been altered.

    :ivar database: The name of the database where the table was altered
    :type database: str
    :ivar table: The name of the table that was altered
    :type table: str
    :ivar before_schema: The table's SQL schema before the alteration
    :type before_schema: str
    :ivar after_schema: The table's SQL schema after the alteration
    :type after_schema: str
    """

    name = "alter-table"
    database: str
    table: str
    before_schema: str
    after_schema: str


@dataclass
class InsertRowsEvent(Event):
    """
    Event name: ``insert-rows``

    Rows were inserted into a table.

    :ivar database: The name of the database where the rows were inserted.
    :type database: str
    :ivar table: The name of the table where the rows were inserted.
    :type table: str
    :ivar num_rows: The number of rows that were requested to be inserted.
    :type num_rows: int
    :ivar ignore: Was ignore set?
    :type ignore: bool
    :ivar replace: Was replace set?
    :type replace: bool
    """

    name = "insert-rows"
    database: str
    table: str
    num_rows: int
    ignore: bool
    replace: bool


@dataclass
class UpsertRowsEvent(Event):
    """
    Event name: ``upsert-rows``

    Rows were upserted into a table.

    :ivar database: The name of the database where the rows were inserted.
    :type database: str
    :ivar table: The name of the table where the rows were inserted.
    :type table: str
    :ivar num_rows: The number of rows that were requested to be inserted.
    :type num_rows: int
    """

    name = "upsert-rows"
    database: str
    table: str
    num_rows: int


@dataclass
class UpdateRowEvent(Event):
    """
    Event name: ``update-row``

    A row was updated in a table.

    :ivar database: The name of the database where the row was updated.
    :type database: str
    :ivar table: The name of the table where the row was updated.
    :type table: str
    :ivar pks: The primary key values of the updated row.
    """

    name = "update-row"
    database: str
    table: str
    pks: list


@dataclass
class DeleteRowEvent(Event):
    """
    Event name: ``delete-row``

    A row was deleted from a table.

    :ivar database: The name of the database where the row was deleted.
    :type database: str
    :ivar table: The name of the table where the row was deleted.
    :type table: str
    :ivar pks: The primary key values of the deleted row.
    """

    name = "delete-row"
    database: str
    table: str
    pks: list


@hookimpl
def register_events():
    return [
        LoginEvent,
        LogoutEvent,
        CreateTableEvent,
        CreateTokenEvent,
        AlterTableEvent,
        DropTableEvent,
        InsertRowsEvent,
        UpsertRowsEvent,
        UpdateRowEvent,
        DeleteRowEvent,
    ]
