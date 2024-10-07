"""
    pint.util
    ~~~~~~~~~

    Miscellaneous functions for pint.

    :copyright: 2016 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

import logging
import math
import operator
import re
import tokenize
import types
from collections.abc import Callable, Generator, Hashable, Iterable, Iterator, Mapping
from fractions import Fraction
from functools import lru_cache, partial
from logging import NullHandler
from numbers import Number
from token import NAME, NUMBER
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    TypeVar,
)

from . import pint_eval
from ._typing import Scalar
from .compat import NUMERIC_TYPES, Self
from .errors import DefinitionSyntaxError
from .pint_eval import build_eval_tree

if TYPE_CHECKING:
    from ._typing import QuantityOrUnitLike
    from .registry import UnitRegistry


logger = logging.getLogger(__name__)
logger.addHandler(NullHandler())

T = TypeVar("T")
TH = TypeVar("TH", bound=Hashable)
TT = TypeVar("TT", bound=type)

# TODO: Change when Python 3.10 becomes minimal version.
# ItMatrix: TypeAlias = Iterable[Iterable[PintScalar]]
# Matrix: TypeAlias = list[list[PintScalar]]
ItMatrix = Iterable[Iterable[Scalar]]
Matrix = list[list[Scalar]]


def _noop(x: T) -> T:
    return x


def matrix_to_string(
    matrix: ItMatrix,
    row_headers: Iterable[str] | None = None,
    col_headers: Iterable[str] | None = None,
    fmtfun: Callable[
        [
            Scalar,
        ],
        str,
    ] = "{:0.0f}".format,
) -> str:
    """Return a string representation of a matrix.

    Parameters
    ----------
    matrix
        A matrix given as an iterable of an iterable of numbers.
    row_headers
        An iterable of strings to serve as row headers.
        (default = None, meaning no row headers are printed.)
    col_headers
        An iterable of strings to serve as column headers.
        (default = None, meaning no col headers are printed.)
    fmtfun
        A callable to convert a number into string.
        (default = `"{:0.0f}".format`)

    Returns
    -------
    str
        String representation of the matrix.
    """
    ret: list[str] = []
    if col_headers:
        ret.append(("\t" if row_headers else "") + "\t".join(col_headers))
    if row_headers:
        ret += [
            rh + "\t" + "\t".join(fmtfun(f) for f in row)
            for rh, row in zip(row_headers, matrix)
        ]
    else:
        ret += ["\t".join(fmtfun(f) for f in row) for row in matrix]

    return "\n".join(ret)


def transpose(matrix: ItMatrix) -> Matrix:
    """Return the transposed version of a matrix.

    Parameters
    ----------
    matrix
        A matrix given as an iterable of an iterable of numbers.

    Returns
    -------
    Matrix
        The transposed version of the matrix.
    """
    return [list(val) for val in zip(*matrix)]


def matrix_apply(
    matrix: ItMatrix,
    func: Callable[
        [
            Scalar,
        ],
        Scalar,
    ],
) -> Matrix:
    """Apply a function to individual elements within a matrix.

    Parameters
    ----------
    matrix
        A matrix given as an iterable of an iterable of numbers.
    func
        A callable that converts a number to another.

    Returns
    -------
    A new matrix in which each element has been replaced by new one.
    """
    return [[func(x) for x in row] for row in matrix]


def column_echelon_form(
    matrix: ItMatrix, ntype: type = Fraction, transpose_result: bool = False
) -> tuple[Matrix, Matrix, list[int]]:
    """Calculate the column echelon form using Gaussian elimination.

    Parameters
    ----------
    matrix
        A 2D matrix as nested list.
    ntype
        The numerical type to use in the calculation.
        (default = Fraction)
    transpose_result
        Indicates if the returned matrix should be transposed.
        (default = False)

    Returns
    -------
    ech_matrix
        Column echelon form.
    id_matrix
        Transformed identity matrix.
    swapped
        Swapped rows.
    """

    _transpose: Callable[
        [
            ItMatrix,
        ],
        Matrix,
    ] = (
        transpose if transpose_result else _noop
    )

    ech_matrix = matrix_apply(
        transpose(matrix),
        lambda x: ntype.from_float(x) if isinstance(x, float) else ntype(x),  # type: ignore
    )

    rows, cols = len(ech_matrix), len(ech_matrix[0])
    # M = [[ntype(x) for x in row] for row in M]
    id_matrix: list[list[Scalar]] = [  # noqa: E741
        [ntype(1) if n == nc else ntype(0) for nc in range(rows)] for n in range(rows)
    ]

    swapped: list[int] = []
    lead = 0
    for r in range(rows):
        if lead >= cols:
            return _transpose(ech_matrix), _transpose(id_matrix), swapped
        s = r
        while ech_matrix[s][lead] == 0:  # type: ignore
            s += 1
            if s != rows:
                continue
            s = r
            lead += 1
            if cols == lead:
                return _transpose(ech_matrix), _transpose(id_matrix), swapped

        ech_matrix[s], ech_matrix[r] = ech_matrix[r], ech_matrix[s]
        id_matrix[s], id_matrix[r] = id_matrix[r], id_matrix[s]

        swapped.append(s)
        lv = ech_matrix[r][lead]
        ech_matrix[r] = [mrx / lv for mrx in ech_matrix[r]]
        id_matrix[r] = [mrx / lv for mrx in id_matrix[r]]

        for s in range(rows):
            if s == r:
                continue
            lv = ech_matrix[s][lead]
            ech_matrix[s] = [
                iv - lv * rv for rv, iv in zip(ech_matrix[r], ech_matrix[s])
            ]
            id_matrix[s] = [iv - lv * rv for rv, iv in zip(id_matrix[r], id_matrix[s])]

        lead += 1

    return _transpose(ech_matrix), _transpose(id_matrix), swapped


def pi_theorem(quantities: dict[str, Any], registry: UnitRegistry | None = None):
    """Builds dimensionless quantities using the Buckingham π theorem

    Parameters
    ----------
    quantities : dict
        mapping between variable name and units
    registry :
         (default value = None)

    Returns
    -------
    type
        a list of dimensionless quantities expressed as dicts

    """

    # Preprocess input and build the dimensionality Matrix
    quant = []
    dimensions = set()

    if registry is None:
        getdim = _noop
        non_int_type = float
    else:
        getdim = registry.get_dimensionality
        non_int_type = registry.non_int_type

    for name, value in quantities.items():
        if isinstance(value, str):
            value = ParserHelper.from_string(value, non_int_type=non_int_type)
        if isinstance(value, dict):
            dims = getdim(registry.UnitsContainer(value))
        elif not hasattr(value, "dimensionality"):
            dims = getdim(value)
        else:
            dims = value.dimensionality

        if not registry and any(not key.startswith("[") for key in dims):
            logger.warning(
                "A non dimension was found and a registry was not provided. "
                "Assuming that it is a dimension name: {}.".format(dims)
            )

        quant.append((name, dims))
        dimensions = dimensions.union(dims.keys())

    dimensions = list(dimensions)

    # Calculate dimensionless  quantities
    matrix = [
        [dimensionality[dimension] for name, dimensionality in quant]
        for dimension in dimensions
    ]

    ech_matrix, id_matrix, pivot = column_echelon_form(matrix, transpose_result=False)

    # Collect results
    # Make all numbers integers and minimize the number of negative exponents.
    # Remove zeros
    results = []
    for rowm, rowi in zip(ech_matrix, id_matrix):
        if any(el != 0 for el in rowm):
            continue
        max_den = max(f.denominator for f in rowi)
        neg = -1 if sum(f < 0 for f in rowi) > sum(f > 0 for f in rowi) else 1
        results.append(
            {
                q[0]: neg * f.numerator * max_den / f.denominator
                for q, f in zip(quant, rowi)
                if f.numerator != 0
            }
        )
    return results


def solve_dependencies(
    dependencies: dict[TH, set[TH]],
) -> Generator[set[TH], None, None]:
    """Solve a dependency graph.

    Parameters
    ----------
    dependencies :
        dependency dictionary. For each key, the value is an iterable indicating its
        dependencies.

    Yields
    ------
    set
        iterator of sets, each containing keys of independents tasks dependent only of
        the previous tasks in the list.

    Raises
    ------
    ValueError
        if a cyclic dependency is found.
    """
    while dependencies:
        # values not in keys (items without dep)
        t = {i for v in dependencies.values() for i in v} - dependencies.keys()
        # and keys without value (items without dep)
        t.update(k for k, v in dependencies.items() if not v)
        # can be done right away
        if not t:
            raise ValueError(
                "Cyclic dependencies exist among these items: {}".format(
                    ", ".join(repr(x) for x in dependencies.items())
                )
            )
        # and cleaned up
        dependencies = {k: v - t for k, v in dependencies.items() if v}
        yield t


def find_shortest_path(
    graph: dict[TH, set[TH]], start: TH, end: TH, path: list[TH] | None = None
):
    """Find shortest path between two nodes within a graph.

    Parameters
    ----------
    graph
        A graph given as a mapping of nodes
        to a set of all connected nodes to it.
    start
        Starting node.
    end
        End node.
    path
        Path to prepend to the one found.
        (default = None, empty path.)

    Returns
    -------
    list[TH]
        The shortest path between two nodes.
    """
    path = (path or []) + [start]
    if start == end:
        return path

    # TODO: raise ValueError when start not in graph
    if start not in graph:
        return None

    shortest = None
    for node in graph[start]:
        if node not in path:
            newpath = find_shortest_path(graph, node, end, path)
            if newpath:
                if not shortest or len(newpath) < len(shortest):
                    shortest = newpath

    return shortest


def find_connected_nodes(
    graph: dict[TH, set[TH]], start: TH, visited: set[TH] | None = None
) -> set[TH] | None:
    """Find all nodes connected to a start node within a graph.

    Parameters
    ----------
    graph
        A graph given as a mapping of nodes
        to a set of all connected nodes to it.
    start
        Starting node.
    visited
        Mutable set to collect visited nodes.
        (default = None, empty set)

    Returns
    -------
    set[TH]
        The shortest path between two nodes.
    """

    # TODO: raise ValueError when start not in graph
    if start not in graph:
        return None

    visited = visited or set()
    visited.add(start)

    for node in graph[start]:
        if node not in visited:
            find_connected_nodes(graph, node, visited)

    return visited


class udict(dict[str, Scalar]):
    """Custom dict implementing __missing__."""

    def __missing__(self, key: str):
        return 0

    def copy(self: Self) -> Self:
        return udict(self)


class UnitsContainer(Mapping[str, Scalar]):
    """The UnitsContainer stores the product of units and their respective
    exponent and implements the corresponding operations.

    UnitsContainer is a read-only mapping. All operations (even in place ones)
    return new instances.

    Parameters
    ----------
    non_int_type
        Numerical type used for non integer values.
    """

    __slots__ = ("_d", "_hash", "_one", "_non_int_type")

    _d: udict
    _hash: int | None
    _one: Scalar
    _non_int_type: type

    def __init__(
        self, *args: Any, non_int_type: type | None = None, **kwargs: Any
    ) -> None:
        if args and isinstance(args[0], UnitsContainer):
            default_non_int_type = args[0]._non_int_type
        else:
            default_non_int_type = float

        self._non_int_type = non_int_type or default_non_int_type

        if self._non_int_type is float:
            self._one = 1
        else:
            self._one = self._non_int_type("1")

        d = udict(*args, **kwargs)
        self._d = d
        for key, value in d.items():
            if not isinstance(key, str):
                raise TypeError(f"key must be a str, not {type(key)}")
            if not isinstance(value, Number):
                raise TypeError(f"value must be a number, not {type(value)}")
            if not isinstance(value, int) and not isinstance(value, self._non_int_type):
                d[key] = self._non_int_type(value)
        self._hash = None

    def copy(self: Self) -> Self:
        """Create a copy of this UnitsContainer."""
        return self.__copy__()

    def add(self: Self, key: str, value: Number) -> Self:
        """Create a new UnitsContainer adding value to
        the value existing for a given key.

        Parameters
        ----------
        key
            unit to which the value will be added.
        value
            value to be added.

        Returns
        -------
        UnitsContainer
            A copy of this container.
        """
        newval = self._d[key] + self._normalize_nonfloat_value(value)
        new = self.copy()
        if newval:
            new._d[key] = newval
        else:
            new._d.pop(key)
        new._hash = None
        return new

    def remove(self: Self, keys: Iterable[str]) -> Self:
        """Create a new UnitsContainer purged from given entries.

        Parameters
        ----------
        keys
            Iterable of keys (units) to remove.

        Returns
        -------
        UnitsContainer
            A copy of this container.
        """
        new = self.copy()
        for k in keys:
            new._d.pop(k)
        new._hash = None
        return new

    def rename(self: Self, oldkey: str, newkey: str) -> Self:
        """Create a new UnitsContainer in which an entry has been renamed.

        Parameters
        ----------
        oldkey
            Existing key (unit).
        newkey
            New key (unit).

        Returns
        -------
        UnitsContainer
            A copy of this container.
        """
        new = self.copy()
        new._d[newkey] = new._d.pop(oldkey)
        new._hash = None
        return new

    def unit_items(self) -> Iterable[tuple[str, Scalar]]:
        return self._d.items()

    def __iter__(self) -> Iterator[str]:
        return iter(self._d)

    def __len__(self) -> int:
        return len(self._d)

    def __getitem__(self, key: str) -> Scalar:
        return self._d[key]

    def __contains__(self, key: str) -> bool:
        return key in self._d

    def __hash__(self) -> int:
        if self._hash is None:
            self._hash = hash(frozenset(self._d.items()))
        return self._hash

    # Only needed by pickle protocol 0 and 1 (used by pytables)
    def __getstate__(self) -> tuple[udict, Scalar, type]:
        return self._d, self._one, self._non_int_type

    def __setstate__(self, state: tuple[udict, Scalar, type]):
        self._d, self._one, self._non_int_type = state
        self._hash = None

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, UnitsContainer):
            # UnitsContainer.__hash__(self) is not the same as hash(self); see
            # ParserHelper.__hash__ and __eq__.
            # Different hashes guarantee that the actual contents are different, but
            # identical hashes give no guarantee of equality.
            # e.g. in CPython, hash(-1) == hash(-2)
            if UnitsContainer.__hash__(self) != UnitsContainer.__hash__(other):
                return False
            other = other._d

        elif isinstance(other, str):
            try:
                other = ParserHelper.from_string(other, self._non_int_type)
            except DefinitionSyntaxError:
                return False

            other = other._d

        return dict.__eq__(self._d, other)

    def __str__(self) -> str:
        return self.__format__("")

    def __repr__(self) -> str:
        tmp = "{%s}" % ", ".join(
            [f"'{key}': {value}" for key, value in sorted(self._d.items())]
        )
        return f"<UnitsContainer({tmp})>"

    def __format__(self, spec: str) -> str:
        # TODO: provisional
        from .formatting import format_unit

        return format_unit(self, spec)

    def format_babel(self, spec: str, registry=None, **kwspec) -> str:
        # TODO: provisional
        from .formatting import format_unit

        return format_unit(self, spec, registry=registry, **kwspec)

    def __copy__(self):
        # Skip expensive health checks performed by __init__
        out = object.__new__(self.__class__)
        out._d = self._d.copy()
        out._hash = self._hash
        out._non_int_type = self._non_int_type
        out._one = self._one
        return out

    def __mul__(self, other: Any):
        if not isinstance(other, self.__class__):
            err = "Cannot multiply UnitsContainer by {}"
            raise TypeError(err.format(type(other)))

        new = self.copy()
        for key, value in other.items():
            new._d[key] += value
            if new._d[key] == 0:
                del new._d[key]

        new._hash = None
        return new

    __rmul__ = __mul__

    def __pow__(self, other: Any):
        if not isinstance(other, NUMERIC_TYPES):
            err = "Cannot power UnitsContainer by {}"
            raise TypeError(err.format(type(other)))

        new = self.copy()
        for key, value in new._d.items():
            new._d[key] *= other
        new._hash = None
        return new

    def __truediv__(self, other: Any):
        if not isinstance(other, self.__class__):
            err = "Cannot divide UnitsContainer by {}"
            raise TypeError(err.format(type(other)))

        new = self.copy()
        for key, value in other.items():
            new._d[key] -= self._normalize_nonfloat_value(value)
            if new._d[key] == 0:
                del new._d[key]

        new._hash = None
        return new

    def __rtruediv__(self, other: Any):
        if not isinstance(other, self.__class__) and other != 1:
            err = "Cannot divide {} by UnitsContainer"
            raise TypeError(err.format(type(other)))

        return self**-1

    def _normalize_nonfloat_value(self, value: Scalar) -> Scalar:
        if not isinstance(value, int) and not isinstance(value, self._non_int_type):
            return self._non_int_type(value)  # type: ignore[no-any-return]
        return value


class ParserHelper(UnitsContainer):
    """The ParserHelper stores in place the product of variables and
    their respective exponent and implements the corresponding operations.
    It also provides a scaling factor.

    For example:
        `3 * m ** 2` becomes ParserHelper(3, m=2)

    Briefly is a UnitsContainer with a scaling factor.

    ParserHelper is a read-only mapping. All operations (even in place ones)
    return new instances.

    WARNING : The hash value used does not take into account the scale
    attribute so be careful if you use it as a dict key and then two unequal
    object can have the same hash.

    Parameters
    ----------
    scale
        Scaling factor.
        (default = 1)
    **kwargs
        Used to populate the dict of units and exponents.
    """

    __slots__ = ("scale",)

    scale: Scalar

    def __init__(self, scale: Scalar = 1, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scale = scale

    @classmethod
    def from_word(cls, input_word: str, non_int_type: type = float) -> ParserHelper:
        """Creates a ParserHelper object with a single variable with exponent one.

        Equivalent to: ParserHelper(1, {input_word: 1})

        Parameters
        ----------
        input_word

        non_int_type
            Numerical type used for non integer values.
        """
        if non_int_type is float:
            return cls(1, [(input_word, 1)], non_int_type=non_int_type)
        else:
            ONE = non_int_type("1")
            return cls(ONE, [(input_word, ONE)], non_int_type=non_int_type)

    @classmethod
    def eval_token(cls, token: tokenize.TokenInfo, non_int_type: type = float):
        token_type = token.type
        token_text = token.string
        if token_type == NUMBER:
            if non_int_type is float:
                try:
                    return int(token_text)
                except ValueError:
                    return float(token_text)
            else:
                return non_int_type(token_text)
        elif token_type == NAME:
            return ParserHelper.from_word(token_text, non_int_type=non_int_type)
        else:
            raise Exception("unknown token type")

    @classmethod
    @lru_cache
    def from_string(cls, input_string: str, non_int_type: type = float) -> ParserHelper:
        """Parse linear expression mathematical units and return a quantity object.

        Parameters
        ----------
        input_string

        non_int_type
            Numerical type used for non integer values.
        """
        if not input_string:
            return cls(non_int_type=non_int_type)

        input_string = string_preprocessor(input_string)
        if "[" in input_string:
            input_string = input_string.replace("[", "__obra__").replace(
                "]", "__cbra__"
            )
            reps = True
        else:
            reps = False

        gen = pint_eval.tokenizer(input_string)
        ret = build_eval_tree(gen).evaluate(
            partial(cls.eval_token, non_int_type=non_int_type)
        )

        if isinstance(ret, Number):
            return cls(ret, non_int_type=non_int_type)

        if reps:
            ret = cls(
                ret.scale,
                {
                    key.replace("__obra__", "[").replace("__cbra__", "]"): value
                    for key, value in ret.items()
                },
                non_int_type=non_int_type,
            )

        for k in list(ret):
            if k.lower() == "nan":
                del ret._d[k]
                ret.scale = non_int_type(math.nan)

        return ret

    def __copy__(self):
        new = super().__copy__()
        new.scale = self.scale
        return new

    def copy(self):
        return self.__copy__()

    def __hash__(self):
        if self.scale != 1:
            mess = "Only scale 1 ParserHelper instance should be considered hashable"
            raise ValueError(mess)
        return super().__hash__()

    # Only needed by pickle protocol 0 and 1 (used by pytables)
    def __getstate__(self):
        return super().__getstate__() + (self.scale,)

    def __setstate__(self, state):
        super().__setstate__(state[:-1])
        self.scale = state[-1]

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, ParserHelper):
            return self.scale == other.scale and super().__eq__(other)
        elif isinstance(other, str):
            return self == ParserHelper.from_string(other, self._non_int_type)
        elif isinstance(other, Number):
            return self.scale == other and not len(self._d)

        return self.scale == 1 and super().__eq__(other)

    def operate(self, items, op=operator.iadd, cleanup: bool = True):
        d = udict(self._d)
        for key, value in items:
            d[key] = op(d[key], value)

        if cleanup:
            keys = [key for key, value in d.items() if value == 0]
            for key in keys:
                del d[key]

        return self.__class__(self.scale, d, non_int_type=self._non_int_type)

    def __str__(self):
        tmp = "{%s}" % ", ".join(
            [f"'{key}': {value}" for key, value in sorted(self._d.items())]
        )
        return f"{self.scale} {tmp}"

    def __repr__(self):
        tmp = "{%s}" % ", ".join(
            [f"'{key}': {value}" for key, value in sorted(self._d.items())]
        )
        return f"<ParserHelper({self.scale}, {tmp})>"

    def __mul__(self, other):
        if isinstance(other, str):
            new = self.add(other, self._one)
        elif isinstance(other, Number):
            new = self.copy()
            new.scale *= other
        elif isinstance(other, self.__class__):
            new = self.operate(other.items())
            new.scale *= other.scale
        else:
            new = self.operate(other.items())
        return new

    __rmul__ = __mul__

    def __pow__(self, other):
        d = self._d.copy()
        for key in self._d:
            d[key] *= other
        return self.__class__(self.scale**other, d, non_int_type=self._non_int_type)

    def __truediv__(self, other):
        if isinstance(other, str):
            new = self.add(other, -1)
        elif isinstance(other, Number):
            new = self.copy()
            new.scale /= other
        elif isinstance(other, self.__class__):
            new = self.operate(other.items(), operator.sub)
            new.scale /= other.scale
        else:
            new = self.operate(other.items(), operator.sub)
        return new

    __floordiv__ = __truediv__

    def __rtruediv__(self, other):
        new = self.__pow__(-1)
        if isinstance(other, str):
            new = new.add(other, self._one)
        elif isinstance(other, Number):
            new.scale *= other
        elif isinstance(other, self.__class__):
            new = self.operate(other.items(), operator.add)
            new.scale *= other.scale
        else:
            new = new.operate(other.items(), operator.add)
        return new


#: List of regex substitution pairs.
_subs_re_list = [
    ("\N{DEGREE SIGN}", "degree"),
    (r"([\w\.\-\+\*\\\^])\s+", r"\1 "),  # merge multiple spaces
    (r"({}) squared", r"\1**2"),  # Handle square and cube
    (r"({}) cubed", r"\1**3"),
    (r"cubic ({})", r"\1**3"),
    (r"square ({})", r"\1**2"),
    (r"sq ({})", r"\1**2"),
    (
        r"\b([0-9]+\.?[0-9]*)(?=[e|E][a-zA-Z]|[a-df-zA-DF-Z])",
        r"\1*",
    ),  # Handle numberLetter for multiplication
    (r"([\w\.\)])\s+(?=[\w\(])", r"\1*"),  # Handle space for multiplication
]

#: Compiles the regex and replace {} by a regex that matches an identifier.
_subs_re = [
    (re.compile(a.format(r"[_a-zA-Z][_a-zA-Z0-9]*")), b) for a, b in _subs_re_list
]
_pretty_table = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹·⁻", "0123456789*-")
_pretty_exp_re = re.compile(r"(⁻?[⁰¹²³⁴⁵⁶⁷⁸⁹]+(?:\.[⁰¹²³⁴⁵⁶⁷⁸⁹]*)?)")


def string_preprocessor(input_string: str) -> str:
    input_string = input_string.replace(",", "")
    input_string = input_string.replace(" per ", "/")

    for a, b in _subs_re:
        input_string = a.sub(b, input_string)

    input_string = _pretty_exp_re.sub(r"**(\1)", input_string)
    # Replace pretty format characters
    input_string = input_string.translate(_pretty_table)

    # Handle caret exponentiation
    input_string = input_string.replace("^", "**")
    return input_string


def _is_dim(name: str) -> bool:
    return name[0] == "[" and name[-1] == "]"


class SharedRegistryObject:
    """Base class for object keeping a reference to the registree.

    Such object are for now Quantity and Unit, in a number of places it is
    that an object from this class has a '_units' attribute.

    Parameters
    ----------

    Returns
    -------

    """

    _REGISTRY: ClassVar[UnitRegistry]
    _units: UnitsContainer

    def __new__(cls, *args, **kwargs):
        inst = object.__new__(cls)
        if not hasattr(cls, "_REGISTRY"):
            # Base class, not subclasses dynamically by
            # UnitRegistry._init_dynamic_classes
            from . import application_registry

            inst._REGISTRY = application_registry.get()
        return inst

    def _check(self, other: Any) -> bool:
        """Check if the other object use a registry and if so that it is the
        same registry.

        Parameters
        ----------
        other

        Returns
        -------
        bool

        Raises
        ------
        ValueError
            if other don't use the same unit registry.
        """
        if self._REGISTRY is getattr(other, "_REGISTRY", None):
            return True

        elif isinstance(other, SharedRegistryObject):
            mess = "Cannot operate with {} and {} of different registries."
            raise ValueError(
                mess.format(self.__class__.__name__, other.__class__.__name__)
            )
        else:
            return False


class PrettyIPython:
    """Mixin to add pretty-printers for IPython"""

    default_format: str

    def _repr_html_(self) -> str:
        if "~" in self._REGISTRY.formatter.default_format:
            return f"{self:~H}"
        return f"{self:H}"

    def _repr_latex_(self) -> str:
        if "~" in self._REGISTRY.formatter.default_format:
            return f"${self:~L}$"
        return f"${self:L}$"

    def _repr_pretty_(self, p, cycle: bool):
        # if cycle:
        if "~" in self._REGISTRY.formatter.default_format:
            p.text(f"{self:~P}")
        else:
            p.text(f"{self:P}")
        # else:
        #     p.pretty(self.magnitude)
        #     p.text(" ")
        #     p.pretty(self.units)


def to_units_container(
    unit_like: QuantityOrUnitLike, registry: UnitRegistry | None = None
) -> UnitsContainer:
    """Convert a unit compatible type to a UnitsContainer.

    Parameters
    ----------
    unit_like
        Quantity or Unit to infer the plain units from.
    registry
        If provided, uses the registry's UnitsContainer and parse_unit_name.  If None,
        uses the registry attached to unit_like.

    Returns
    -------
    UnitsContainer
    """
    mro = type(unit_like).mro()
    if UnitsContainer in mro:
        return unit_like
    elif SharedRegistryObject in mro:
        return unit_like._units
    elif str in mro:
        if registry:
            # TODO: document how to whether to lift preprocessing loop out to caller
            for p in registry.preprocessors:
                unit_like = p(unit_like)
            return registry.parse_units_as_container(unit_like)
        else:
            return ParserHelper.from_string(unit_like)
    elif dict in mro:
        if registry:
            return registry.UnitsContainer(unit_like)
        else:
            return UnitsContainer(unit_like)


def infer_base_unit(
    unit_like: QuantityOrUnitLike, registry: UnitRegistry | None = None
) -> UnitsContainer:
    """
    Given a Quantity or UnitLike, give the UnitsContainer for it's plain units.

    Parameters
    ----------
    unit_like
        Quantity or Unit to infer the plain units from.
    registry
        If provided, uses the registry's UnitsContainer and parse_unit_name.  If None,
        uses the registry attached to unit_like.

    Returns
    -------
    UnitsContainer

    Raises
    ------
    ValueError
        The unit_like did not reference a registry, and no registry was provided.

    """
    d = udict()

    original_units = to_units_container(unit_like, registry)

    if registry is None and hasattr(unit_like, "_REGISTRY"):
        registry = unit_like._REGISTRY
    if registry is None:
        raise ValueError("No registry provided.")

    for unit_name, power in original_units.items():
        candidates = registry.parse_unit_name(unit_name)
        assert len(candidates) == 1
        _, base_unit, _ = candidates[0]
        d[base_unit] += power

    # remove values that resulted in a power of 0
    nonzero_dict = {k: v for k, v in d.items() if v != 0}

    return registry.UnitsContainer(nonzero_dict)


def getattr_maybe_raise(obj: Any, item: str):
    """Helper function invoked at start of all overridden ``__getattr__``.

    Raise AttributeError if the user tries to ask for a _ or __ attribute,
    *unless* it is immediately followed by a number, to enable units
    encompassing constants, such as ``L / _100km``.

    Parameters
    ----------
    item
        attribute to be found.

    Raises
    ------
    AttributeError
    """
    # Double-underscore attributes are tricky to detect because they are
    # automatically prefixed with the class name - which may be a subclass of obj
    if (
        item.endswith("__")
        or len(item.lstrip("_")) == 0
        or (item.startswith("_") and not item.lstrip("_")[0].isdigit())
    ):
        raise AttributeError(f"{obj!r} object has no attribute {item!r}")


def iterable(y: Any) -> bool:
    """Check whether or not an object can be iterated over."""
    try:
        iter(y)
    except TypeError:
        return False
    return True


def sized(y: Any) -> bool:
    """Check whether or not an object has a defined length."""
    try:
        len(y)
    except TypeError:
        return False
    return True


def create_class_with_registry(
    registry: UnitRegistry, base_class: type[TT]
) -> type[TT]:
    """Create new class inheriting from base_class and
    filling _REGISTRY class attribute with an actual instanced registry.
    """

    class_body = {
        "__module__": "pint",
        "_REGISTRY": registry,
    }

    return types.new_class(
        base_class.__name__,
        bases=(base_class,),
        exec_body=lambda ns: ns.update(class_body),
    )
