# 桥北轨检湖

测车按行驶顺序上报里程和缺陷等级。白班核收重传，按原行登记复测并冻结日版。库管将同一冻结版发往甲乙机房，两边可采用不同分块和等级码表。源仓关停后，夜班从一份完整核验的正式冷装领取指定行列。巡检班使用明细、出处和小计派查，换班后凭首执复领原报告。

## 运行

使用 Python 3.11 标准库。从项目根目录运行平台提供的完整回归入口。

```sh
python3 -B check_project.py
python3 -B demo.py
```

直接运行完整 unittest 套件的命令如下。

```sh
python3 -B -m unittest discover -s tests -t . -v
```

演示在独立临时目录读取 `samples/day.json` 和 `samples/layouts.json`。初版两行为 `(120, 2)`、`(135, 1)`，里程小计 255 米，等级小计 3。甲机房把两个等级保持在同一片，乙机房将其分成两片并使用反向字典。演示分别领取两套冷装，再签存把第二行里程更正为缺测的子版报告。复领的小计为 120 米，保留最初签存的出处。

## Python 接口

```python
from lake.audit.receipts import ReportStore
from lake.audit.retrieve import retrieve
from lake.intake.batches import ingest
from lake.intake.receipts import BatchLedger
from lake.packing.catalog import LoadCatalog
from lake.packing.layout import RoomLayout
from lake.packing.publish import pack
from lake.reading.projection import project
from lake.revision.edits import RowEdit
from lake.revision.freeze import freeze
from lake.revision.store import RevisionStore

batches = BatchLedger('source.sqlite')
revisions = RevisionStore('source.sqlite')
cold = LoadCatalog('cold.sqlite', 'media')
reports = ReportStore('reports.sqlite')

ingest(batches, 'day-01', [
    {'mileage': '0.120', 'unit': 'km', 'grade': 2, 'source': 'car-7/log-1'},
    {'mileage': 135, 'unit': 'm', 'grade': 1, 'source': 'car-7/log-2'},
])
initial = freeze(revisions, batches, batch_id='day-01')
corrected = freeze(revisions, batches, parent_id=initial.revision_id,
                   edits=[RowEdit(2, 'mileage', None, 'survey/day-01/row-2')])
pack(revisions, cold, initial.revision_id, RoomLayout('甲', 2, 2))
pack(revisions, cold, initial.revision_id, RoomLayout('乙', 1, 1, True))
first = project(cold, reports, initial.revision_id, (1, 2), ('mileage', 'grade'))
snapshot = retrieve(reports, first.receipt_id).snapshot
```

`ingest` 返回 `BatchReceipt`。每批至少一行，四个字段均需给出，里程和等级可显式为空。里程接受整数或十进制字符串，单位为 `m` 或 `km`，换算后须为整米。等级接受整数，布尔值非法。原始数值的等价写法复用原确认，改单位、来源、值或行序属于正文变化。同号异件抛出 `BatchConflict`，非法正文抛出 `ValueError`。

`freeze` 返回 `FrozenRevision`。初版传 `batch_id`，子版传确切 `parent_id` 及一个或多个 `RowEdit`。行号从 1 开始，以原批顺序固定。里程更正默认按米，也可指定 `km`，等级更正无单位。复测来源为必填，同一格在一笔更正中只能出现一次。未知批、未知父版和非法更正分别抛出 `UnknownBatch`、`UnknownRevision`、`InvalidEdit`。每次成功冻结都取得独立编号，已有版次仍可用。

`pack` 返回 `PublishedLoad`。`RoomLayout` 依次指定机房名、里程每片行数、等级每片行数及是否采用倒序等级字典。等级码是所属装载字典中的位置。分块按冻结列顺序和行序产生。待建登记与媒体文件先准备，校验整份布局、字典、物理片段和冻结摘要后才提交正式登记。失败时已有正式装载仍可查，待建件仅供库管处置。

`project` 只使用冷侧目录、片段和报告账。行区间为从 1 开始的闭区间，列清单有序且列键互异。已知版的越界或未知列抛出 `InvalidProjection`。正式装载按登记顺序尝试，完整替代优先于报告失败。没有可交付装载时，缺少在线材料抛出 `ProjectionPending`，可读候选的格式、内容或整装核验失败抛出 `ProjectionVerificationError`。只有完整结果才产生首执。坏请求、核验失败和报告提交失败均无成功首执。

`ReportReceipt.snapshot` 提供有序的 `rows`、`columns` 和 `totals`。每格含整数或 `None`、冻结坐标、原观测或复测出处、实读机房和片段、片内偏移及等级字典标识。片内偏移从 0 开始。空值保留在行中并跳过小计，全空列为 0。`retrieve` 返回首执绑定的已签存快照，未知首执抛出 `ReportNotFound`。

## 本地存储

源账、冷目录和报告账分别打开，各构造器首次建表，重开沿用已提交记录。为保持阶段隔离，三个账使用独立文件，文件所在父目录由调用方提供。媒体根目录下每个装载拥有独立的 `load-<id>` 目录。测试及演示通过移动实际片段或装载目录模拟离线，通过替换字节模拟损坏。报告账的同一事务持久化完整明细和首执，复领只查询报告账。
