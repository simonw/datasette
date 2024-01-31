from abc import ABC, abstractproperty
from dataclasses import asdict, dataclass
from datasette.hookspecs import hookimpl


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
class LogoutEvent(Event):
    name = "logout"


@dataclass
class CreateTableEvent(Event):
    name = "create-table"
    database: str
    table: str
    schema: str


@hookimpl
def register_events():
    return [LogoutEvent, CreateTableEvent]
