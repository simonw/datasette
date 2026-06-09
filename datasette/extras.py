import re
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

from asyncinject import Registry


def extra_names_from_request(request):
    extra_bits = request.args.getlist("_extra")
    extras = set()
    for bit in extra_bits:
        extras.update(part for part in bit.split(",") if part)
    return extras


class ExtraScope(Enum):
    TABLE = "table"


@dataclass(frozen=True)
class ExtraExample:
    path: str | None = None
    key: str | None = None
    value: object | None = None
    note: str | None = None


class Provider:
    name: ClassVar[str | None] = None
    scopes: ClassVar[frozenset[ExtraScope]] = frozenset()
    public: ClassVar[bool] = False

    @classmethod
    def key(cls):
        return cls.name or _camel_to_snake(cls.__name__)

    @classmethod
    def available_for(cls, scope):
        return scope in cls.scopes

    async def resolve(self, context):
        raise NotImplementedError


class Extra(Provider):
    description: ClassVar[str | None] = None
    example: ClassVar[ExtraExample | None] = None
    public: ClassVar[bool] = True
    stable: ClassVar[bool] = True
    expensive: ClassVar[bool] = False
    docs_note: ClassVar[str | None] = None

    @classmethod
    def documentation(cls):
        return {
            "name": cls.key(),
            "description": cls.description,
            "scopes": [
                scope.value for scope in sorted(cls.scopes, key=lambda s: s.value)
            ],
            "stable": cls.stable,
            "expensive": cls.expensive,
            "docs_note": cls.docs_note,
            "example": cls.example,
        }


class ExtraRegistry:
    def __init__(self, classes):
        self.classes = list(classes)
        self.classes_by_name = {cls.key(): cls for cls in self.classes}

    def classes_for_scope(self, scope, include_internal=True):
        classes = [
            cls
            for cls in self.classes
            if cls.available_for(scope) and (include_internal or cls.public)
        ]
        return classes

    def public_classes_for_scope(self, scope):
        return self.classes_for_scope(scope, include_internal=False)

    async def resolve(self, requested, context, scope):
        registry = Registry()

        async def context_provider():
            return context

        registry.register(context_provider, name="context")

        for cls in self.classes_for_scope(scope):
            registry.register(cls().resolve, name=cls.key())

        public_names = {cls.key() for cls in self.public_classes_for_scope(scope)}
        requested_public_names = [
            name
            for name in requested
            if name in public_names and name in registry._registry
        ]
        resolved = await registry.resolve_multi(requested_public_names)
        return {
            name: resolved[name] for name in requested_public_names if name in resolved
        }


def _camel_to_snake(name):
    name = re.sub(r"(Extra|Provider)$", "", name)
    name = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", name).lower()
