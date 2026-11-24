import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class GradeDictionary:
    values: tuple[int, ...]

    def __post_init__(self):
        if any(type(value) is not int for value in self.values):
            raise ValueError('grade dictionary values must be integers')
        if len(set(self.values)) != len(self.values):
            raise ValueError('grade dictionary values must be unique')

    @property
    def dictionary_id(self) -> str:
        payload = json.dumps(self.values, separators=(',', ':')).encode('ascii')
        return hashlib.sha256(payload).hexdigest()


def build_grade_dictionary(grades: Iterable[int | None], *, reverse: bool = False) -> GradeDictionary:
    values = tuple(sorted({grade for grade in grades if grade is not None}, reverse=reverse))
    return GradeDictionary(values)


def encode_grade(dictionary: GradeDictionary, grade: int | None) -> int | None:
    if grade is None:
        return None
    if type(grade) is not int:
        raise ValueError('grade must be an integer')
    return dictionary.values.index(grade)


def decode_grade(dictionary: GradeDictionary, code: int | None) -> int | None:
    if code is None:
        return None
    if type(code) is not int or not 0 <= code < len(dictionary.values):
        raise ValueError(f'unknown grade code {code!r}')
    return dictionary.values[code]
