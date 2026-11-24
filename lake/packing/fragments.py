import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass

from lake.packing.dictionary import GradeDictionary, encode_grade
from lake.packing.layout import FragmentSpan


class FragmentError(ValueError):
    pass


@dataclass(frozen=True)
class EncodedFragment:
    index: int
    payload: bytes
    checksum: str


def encode_fragment(span: FragmentSpan, values: Iterable[int | None],
                    dictionary: GradeDictionary) -> EncodedFragment:
    logical = tuple(values)
    if len(logical) != span.size or any(value is not None and type(value) is not int for value in logical):
        raise FragmentError('fragment input does not match its span')
    encoded = tuple(encode_grade(dictionary, value) for value in logical) if span.column == 'grade' else logical
    payload = json.dumps(encoded, separators=(',', ':')).encode('ascii')
    return EncodedFragment(span.index, payload, hashlib.sha256(payload).hexdigest())


def parse_fragment_payload(payload: bytes) -> tuple[int | None, ...]:
    try:
        values = json.loads(payload)
    except ValueError as error:
        raise FragmentError('malformed fragment payload') from error
    if not isinstance(values, list) or any(value is not None and type(value) is not int for value in values):
        raise FragmentError('fragment payload must contain integers or nulls')
    return tuple(values)


def verify_fragment(fragment: EncodedFragment, span: FragmentSpan) -> tuple[int | None, ...]:
    if fragment.index != span.index:
        raise FragmentError('fragment index does not match its slot')
    if hashlib.sha256(fragment.payload).hexdigest() != fragment.checksum:
        raise FragmentError('fragment checksum mismatch')
    values = parse_fragment_payload(fragment.payload)
    if len(values) != span.size:
        raise FragmentError('fragment length does not match its slot')
    return values


def fragment_to_bytes(fragment: EncodedFragment) -> bytes:
    return fragment.checksum.encode('ascii') + b'\n' + fragment.payload


def fragment_from_bytes(index: int, raw: bytes) -> EncodedFragment:
    checksum, separator, payload = raw.partition(b'\n')
    if not separator or len(checksum) != 64 or any(byte not in b'0123456789abcdef' for byte in checksum):
        raise FragmentError('malformed fragment envelope')
    return EncodedFragment(index, payload, checksum.decode('ascii'))
