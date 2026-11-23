# 进度模型数据草案（progress model drafts）

设计文档：`docs/PROGRESS_AXIS_DESIGN.md`

这些 JSON 是**结构草案**，不是可直接上线的数据：

- `baldurs_gate3.chain.json`：从现有 `src/rag_agent/progress.py` 的锚点表原样搬出，
  行为等价，可直接当 S1 的迁移输入。
- `silksong.dag.json`：**未校对**（`reviewed: false`）。节点名取自仓库内
  `silksong_data.db` 的 `areas` 表与 `bosses.location`；`act` 与前置边只保留了
  高把握项，其余留 `null` / 空数组并标 `_note: TODO`。

## 状态字段口径

| 字段 | 含义 |
|---|---|
| `reviewed: true` | 可用于全部推断：抬下界、`not_reached` 剪枝、检索反查 |
| `reviewed: false` | 只允许抬高下界（用户提到 X 就把 lo 抬到 X 的 act）；不剪枝、不参与反查 |

`silksong.dag.json` 的 `_todo` 里列了校对项。校对完成后改 `reviewed: true`。

## 校验

```
pytest tests/test_progress_model_schema.py -q
```

校验内容：顶层字段、`kind` 合法、节点/能力 id 唯一、边引用存在、**区域图无环**、
`act` 为空或正整数、锚点非空且不跨 rank 重复、文件名与 `game` 字段一致。

`config/progress/*.json`（S1 落地后的正式目录）存在时也走同一套校验，
所以这个测试文件是 schema 的唯一契约来源。

## 校对流程（建议）

先跑一次探测工具拿待填清单（只提线索、不下判断）：

```
python3 scripts/analysis/progress_draft_probe.py --game silksong --out docs/progress_drafts/silksong.todo.md
```

输出：act 待定的区域及对应 wiki 原句、前置为空的区域、能力获取地缺失、已填 act
与 wiki 线索打架的地方。工具会过滤 wiki 的页内小节（`## Dialogue` / `## Location` 等），
只把真页当页。

然后：

1. 从 wiki 抽区域列表与 "requires / needs / blocked by" 句子 → 填 `requires_*`；
2. `act` 只在原文出现「Act N」时填，`early-game` / `late-game` 不换算成数字；
3. `Connections` 段是无向邻接，不能直接当 `requires_areas`；
4. 只填有原文支持的边，拿不准的留空并写 `_note`；
5. 每加一条边跑一次校验（无环是硬约束）；
6. 抽 20 条真实用户问法做回放，确认 `lo` 不越界、也不把用户卡在过低层。
