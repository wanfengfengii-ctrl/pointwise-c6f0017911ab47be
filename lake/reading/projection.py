from collections.abc import Sequence

from lake.audit.receipts import ReportReceipt, ReportStore
from lake.audit.reports import seal_report
from lake.packing.catalog import LoadCatalog, PublishedRevision
from lake.reading.coverage import ProjectionRequest, VerifiedLoad, conclude_coverage, qualify_load
from lake.reading.decode import decode_projection


class InvalidProjection(ValueError):
    pass


def validate_request(catalog: LoadCatalog, revision_id: int, row_range: Sequence[int],
                     columns: Sequence[str]) -> tuple[ProjectionRequest, PublishedRevision]:
    if type(revision_id) is not int or revision_id < 1:
        raise InvalidProjection('a positive frozen revision ID is required')
    if not isinstance(row_range, Sequence) or len(row_range) != 2:
        raise InvalidProjection('row range must contain its first and last rows')
    first, last = row_range
    if type(first) is not int or type(last) is not int or not 1 <= first <= last:
        raise InvalidProjection('row range must be a nonempty ascending closed interval')
    if not isinstance(columns, Sequence) or isinstance(columns, (str, bytes)) or not columns:
        raise InvalidProjection('specify a nonempty ordered list of columns')
    keys = tuple(columns)
    if any(not isinstance(key, str) or not key for key in keys) or len(set(keys)) != len(keys):
        raise InvalidProjection('column keys must be nonempty and distinct')
    published = catalog.describe_revision(revision_id)
    if published is None:
        conclude_coverage(revision_id, ())
    if last > published.row_count:
        raise InvalidProjection(f'last row exceeds the {published.row_count} frozen rows')
    if any(key not in published.columns for key in keys):
        raise InvalidProjection('request contains an unknown frozen column')
    return ProjectionRequest(revision_id, first, last, keys), published


def project(catalog: LoadCatalog, reports: ReportStore, revision_id: int, row_range: Sequence[int],
            columns: Sequence[str]) -> ReportReceipt:
    request, published = validate_request(catalog, revision_id, row_range, columns)
    failures = []
    for candidate in catalog.list_published(revision_id):
        outcome = qualify_load(catalog, candidate, published, request)
        if isinstance(outcome, VerifiedLoad):
            return seal_report(reports, request, decode_projection(outcome, request))
        failures.append(outcome)
    conclude_coverage(revision_id, tuple(failures))
