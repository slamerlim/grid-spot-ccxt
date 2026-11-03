"""Bounded Decimal arithmetic with an explicit, ambient-context-independent policy."""

from __future__ import annotations

from collections.abc import Callable
from decimal import ROUND_HALF_EVEN, Context, Decimal, DecimalException, Inexact, localcontext
from functools import wraps

ZERO = Decimal("0")
ONE = Decimal("1")
ARITHMETIC = Context(prec=96, rounding=ROUND_HALF_EVEN, Emin=-999, Emax=999)


class DomainError(ValueError):
    """Invalid or unsupported normalized facts; never permission to guess."""


def decimal(value: Decimal, *, positive: bool = False, signed: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise DomainError("finite Decimal required")
    if len(value.as_tuple().digits) > 96 or abs(value.adjusted()) > 96:
        raise DomainError("numeric range unsupported")
    if (positive and value <= ZERO) or (not signed and value < ZERO):
        raise DomainError("invalid amount sign")
    return value


def bounded_int(value: int, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise DomainError("integer outside supported range")
    return value


def identifier(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or not value.isprintable():
        raise DomainError("invalid scoped identifier")
    return value


def arithmetic[**P, T](function: Callable[P, T]) -> Callable[P, T]:
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            with localcontext(ARITHMETIC):
                return function(*args, **kwargs)
        except DecimalException as exc:
            raise DomainError("unsupported Decimal arithmetic") from exc

    return wrapped


def exact[**P, T](function: Callable[P, T]) -> Callable[P, T]:
    """Reject financial posting that would silently lose decimal digits."""

    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            with localcontext(ARITHMETIC) as context:
                context.traps[Inexact] = True
                return function(*args, **kwargs)
        except DecimalException as exc:
            raise DomainError("financial arithmetic exceeds exact supported precision") from exc

    return wrapped


@exact
def units(value: Decimal, quantum: Decimal, *, up: bool) -> Decimal:
    """Round to an arbitrary positive increment using exact integer division."""
    decimal(value)
    decimal(quantum, positive=True)
    n, d = value.as_integer_ratio()
    qn, qd = quantum.as_integer_ratio()
    numerator, denominator = n * qd, d * qn
    count, remainder = divmod(numerator, denominator)
    return Decimal(count + int(up and remainder != 0)) * quantum


@exact
def proportional_cost(
    cost: Decimal, quantity: Decimal, owned: Decimal, quantum: Decimal
) -> Decimal:
    """Floor a proportional cost allocation using exact integer ratios."""
    cn, cd = cost.as_integer_ratio()
    qn, qd = quantity.as_integer_ratio()
    on, od = owned.as_integer_ratio()
    un, ud = quantum.as_integer_ratio()
    count = (cn * qn * od * ud) // (cd * qd * on * un)
    return Decimal(count) * quantum
