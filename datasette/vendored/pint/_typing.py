from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from fractions import Fraction
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, Union

from .compat import Never, TypeAlias

if TYPE_CHECKING:
    from .facets.plain import PlainQuantity as Quantity
    from .facets.plain import PlainUnit as Unit
    from .util import UnitsContainer


HAS_NUMPY = False
if TYPE_CHECKING:
    from .compat import HAS_NUMPY

if HAS_NUMPY:
    from .compat import np

    Scalar: TypeAlias = Union[float, int, Decimal, Fraction, np.number[Any]]
    Array = np.ndarray[Any, Any]
else:
    Scalar: TypeAlias = Union[float, int, Decimal, Fraction]
    Array: TypeAlias = Never

# TODO: Change when Python 3.10 becomes minimal version.
Magnitude = Union[Scalar, Array]

UnitLike = Union[str, dict[str, Scalar], "UnitsContainer", "Unit"]

QuantityOrUnitLike = Union["Quantity", UnitLike]

Shape = tuple[int, ...]

S = TypeVar("S")

FuncType = Callable[..., Any]
F = TypeVar("F", bound=FuncType)


# TODO: Improve or delete types
QuantityArgument = Any

T = TypeVar("T")


class Handler(Protocol):
    def __getitem__(self, item: type[T]) -> Callable[[T], None]: ...
