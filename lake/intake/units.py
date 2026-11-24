import re
from fractions import Fraction


class InvalidMileage(ValueError):
    pass


_MULTIPLIERS = {'m': 1, 'km': 1000}
_DECIMAL = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?')


def normalize_unit(unit: str) -> str:
    if not isinstance(unit, str) or unit not in _MULTIPLIERS:
        raise InvalidMileage('mileage unit must be m or km')
    return unit


def mileage_fraction(value: int | str) -> Fraction:
    if type(value) is int:
        return Fraction(value)
    if isinstance(value, str) and _DECIMAL.fullmatch(value):
        try:
            return Fraction(value)
        except ValueError as error:
            raise InvalidMileage('invalid decimal mileage') from error
    raise InvalidMileage('mileage must be an integer or a decimal string')


def to_integer_metres(value: int | str | None, unit: str) -> int | None:
    multiplier = _MULTIPLIERS[normalize_unit(unit)]
    if value is None:
        return None
    scaled = mileage_fraction(value) * multiplier
    if scaled.denominator != 1:
        raise InvalidMileage('mileage must convert to whole metres')
    return scaled.numerator
