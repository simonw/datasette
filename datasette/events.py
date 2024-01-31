from abc import ABC, abstractproperty
from dataclasses import asdict, dataclass
from datasette.hookspecs import hookimpl
from typing import Optional


@dataclass
class Event(ABC):
    @abstractproperty
    def name(self):
        pass

    actor: dict

    def properties(self):
        properties = asdict(self)
        properties.pop("actor", None)
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


@hookimpl
def register_events():
    return [LoginEvent, LogoutEvent, CreateTableEvent, CreateTokenEvent]
