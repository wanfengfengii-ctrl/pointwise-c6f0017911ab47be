from dataclasses import dataclass
from enum import Enum

from lake.packing.catalog import LoadCatalog
from lake.packing.fragments import EncodedFragment, FragmentError, verify_fragment
from lake.packing.layout import FragmentSpan


class MediaState(Enum):
    AVAILABLE = 'available'
    OFFLINE = 'offline'
    INVALID = 'invalid'


@dataclass(frozen=True)
class MediaObservation:
    span: FragmentSpan
    state: MediaState
    fragment: EncodedFragment | None = None
    detail: str = ''


def observe_fragment(catalog: LoadCatalog, load_id: int, span: FragmentSpan) -> MediaObservation:
    try:
        fragment = catalog.read_fragment(load_id, span.index)
        verify_fragment(fragment, span)
    except FileNotFoundError:
        return MediaObservation(span, MediaState.OFFLINE, detail='fragment is offline')
    except FragmentError as error:
        return MediaObservation(span, MediaState.INVALID, detail=str(error))
    return MediaObservation(span, MediaState.AVAILABLE, fragment)
