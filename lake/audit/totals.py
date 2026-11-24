from collections.abc import Iterable, Sequence


def column_totals(rows: Iterable[Sequence[int | None]], width: int) -> tuple[int, ...]:
    sums = [0] * width
    for row in rows:
        if len(row) != width:
            raise ValueError('report row width does not match its column list')
        for index, value in enumerate(row):
            if value is not None:
                if type(value) is not int:
                    raise ValueError('report values must be integers or null')
                sums[index] += value
    return tuple(sums)
