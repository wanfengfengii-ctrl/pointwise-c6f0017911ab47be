from lake.audit.receipts import ReportReceipt, ReportStore


class ReportNotFound(LookupError):
    pass


def retrieve(store: ReportStore, receipt_id: str) -> ReportReceipt:
    if not isinstance(receipt_id, str) or not receipt_id:
        raise ReportNotFound(receipt_id)
    receipt = store.get(receipt_id)
    if receipt is None:
        raise ReportNotFound(receipt_id)
    return receipt
