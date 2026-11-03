from __future__ import annotations

import re
from dataclasses import dataclass

TYPED_PARAMS_FLAG = "_typed_params"
TYPE_SEPARATOR = "::"
SUPPORTED_TYPES = ("int",)
INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1
_INTEGER_RE = re.compile(r"-?[0-9]+\Z")


class TypedParameterError(ValueError):
    "A query string violated the opt-in typed parameter contract."


@dataclass
class ResolvedTypedParameters:
    params: dict
    # base parameter name -> key used in the query string, e.g. "age" -> "age::int"
    typed_names: dict[str, str]
    enabled: bool


def resolve_typed_parameters(params, named_parameters, args=None):
    """Resolve opt-in ``name::int`` values into Python integers.

    ``params`` carries the legacy-resolved values (first value wins for GET
    query strings, last value wins for POST form bodies), ``named_parameters``
    the names derived from the SQL (or a stored query's declared parameters),
    and ``args`` the original MultiParams used to detect repeated keys. With the
    feature off this returns ``params`` unchanged, preserving legacy string
    semantics.

    A repeated ``_typed_params`` flag is rejected before the enabled check so
    the outcome does not depend on which value happens to be first or last.
    """
    if args is not None and len(args.getlist(TYPED_PARAMS_FLAG)) > 1:
        raise TypedParameterError(f"{TYPED_PARAMS_FLAG} may only be supplied once")

    if params.get(TYPED_PARAMS_FLAG) != "1":
        return ResolvedTypedParameters(params=params, typed_names={}, enabled=False)

    named = set(named_parameters)
    typed_names = {}
    resolved = dict(params)

    for key in params:
        if TYPE_SEPARATOR not in key:
            continue
        base, _, type_name = key.partition(TYPE_SEPARATOR)
        if not base:
            raise TypedParameterError(f"Invalid typed parameter name {key!r}")
        if type_name not in SUPPORTED_TYPES:
            raise TypedParameterError(
                "Unsupported parameter type {!r} in {!r}; supported types: {}".format(
                    type_name, key, ", ".join(SUPPORTED_TYPES)
                )
            )
        if base.startswith("_"):
            raise TypedParameterError(
                f"Typed parameter {key!r} cannot target an underscore parameter"
            )
        if base not in named:
            raise TypedParameterError(
                f"Typed parameter {key!r} does not match a named parameter in this query"
            )
        if base in params:
            raise TypedParameterError(
                f"Parameter {base!r} was supplied both with and without a type suffix"
            )
        if args is not None and len(args.getlist(key)) > 1:
            raise TypedParameterError(f"Parameter {key!r} was supplied more than once")

        value = params[key]
        if value == "":
            raise TypedParameterError(
                f"Parameter {key!r} requires an integer value; it was empty"
            )
        if not _INTEGER_RE.match(value):
            raise TypedParameterError(
                f"Parameter {key!r} requires a base-10 integer; got {value!r}"
            )
        try:
            integer = int(value)
        except ValueError:
            # Python limits how many digits int() will parse
            raise TypedParameterError(
                f"Parameter {key!r} requires a base-10 integer; got {value!r}"
            )
        if not INT64_MIN <= integer <= INT64_MAX:
            raise TypedParameterError(
                f"Parameter {key!r} is outside SQLite's signed 64-bit integer range "
                f"({INT64_MIN} to {INT64_MAX})"
            )

        del resolved[key]
        resolved[base] = integer
        typed_names[base] = key

    return ResolvedTypedParameters(
        params=resolved, typed_names=typed_names, enabled=True
    )
