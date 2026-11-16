# 评测体系（四层，从快到慢）

一次线上问题不该只变成一条「知道了」，而应该落到某一层评测里，下次自动报警。
本文说清四层各管什么、用例怎么入库、怎么跑。

| 层 | 入口 | 跑什么 | 要 LLM | 耗时 | 何时跑 |
|---|---|---|---|---|---|
| L0 单测 | `pytest tests/` | 纯函数与契约：路由决策、剧透分级、额度、SSE 协议 | 否 | ~2s | 每次改动 / CI |
| L1 冒烟评估 | `tests/test_light.py` | 路由 12 + 检索 13 + E2E 6（脚本式，pytest 里被 collect_ignore） | E2E 部分要 | 分钟级 | 部署前 |
| L2 表现回放 | `scripts/eval/agent_behavior.py` | 多轮场景：落库游戏 / 工具 game 参数 / 引用真伪 / 剧透等级 | routing 不要，full 要 | routing ~2s，full 分钟级 | 改路由、改提示词、改检索前后 |
| L3 质量打分 | `scripts/eval/ragas_eval.py` | RAGAS（忠实度/相关性）+ LLM 裁判 | 要 | 十分钟级 | 阶段迭代（Mac 端跑） |

L0/L2 是这次事故后新加的两层，正好对应「代码层」与「模型 agent 表现层」。

## L0：代码层（pytest）

不碰 LLM、不碰网络，判的是「决策函数对不对」。

- `tests/test_routing_regression.py`：线上误判轮次固化成用例。
  已知缺陷挂 `xfail(strict=True)`——修好后变 XPASS 会直接报错，逼着摘标记。
- `tests/test_routing_gaps.py`：非误判类缺口（简写不识别、专名两种译法、兜底静默、
  专有与通用词打平、来源不可审计），来源见 `docs/ROUTING_GAPS_2026-11-16.md`。
  与上面的文件分开是为了避免两端同时改同一文件。
- `tests/test_spoiler_severity.py`：剧透分级词表与阈值筛选（`spoiler_hits` / `spoiler_over`）。
- 其余：`test_graph.py`、`test_api_handlers.py`、`test_quota.py`、`test_tools.py` 等。

约定：

- 一发现问题先写一条会失败的用例，再改代码。
- 用例 id 带线上时间戳（如 `online-2026-11-15#2346`），能直接定位到 `data/turns/` 里的记录。
- 单字信号词、单字剧透词这类数据契约用测试兜住，别靠 review 记性。

## L2：模型/Agent 表现层（场景回放）

`scripts/eval/agent_behavior.py` —— 把真实图跑起来（`graph.astream`，含工具与检索），
逐轮判四件事：

1. `route` 落库游戏 == 期望游戏（错了两件事：检索错库 + 后续会话粘错）
2. `tool_game` 每次工具调用的 `game` 参数 == 本轮落库游戏
   （实现：把 `GAME_TOOLS` 里的工具换成 `func` 被包过的副本，记录入参与返回）
3. `citation` 答案里显式标来源的「X」必须在本轮工具返回文本里出现过
   （抓伪造来源，见 `docs/ROUTING_MISROUTE_2026-11-15.md` §2）
4. `spoiler` 命中高于用例 `max_level` 的剧透词即失败；等级口径见 `docs/SPOILER_SEVERITY.md`

用例集：`scripts/eval/behavior_cases.jsonl`，一行一个场景：

```json
{"id": "bg3-amulet-chain", "context": ["bg3 塞尔死灵法术书怎么读完"],
 "turns": ["狂笑术的代价是什么？", "护符里的幽灵那个任务的"],
 "expect_games": ["baldurs_gate3", "baldurs_gate3"],
 "max_level": 1, "forbidden_terms": [], "known_issue": true,
 "source": "线上 turns 2026-11-15 23:45", "notes": "..."}
```

- `context`：复现「会话已经进行了一会儿」的前置轮次，不计分（否则第一轮总会因无历史而落空）。
- `known_issue`：已知缺陷。失败照常打印，但不计进退出码——和 pytest 的 xfail 一个意思。
  修好后请把 `known_issue` 删掉，让它变成真失败守卫。

跑法：

```bash
# 路由模式（默认，不调 LLM，秒级）
.venv/bin/python scripts/eval/agent_behavior.py

# 全量（真实 LLM + 检索；一定要指定 fastembed 缓存，否则检索报 config.json 缺失）
FASTEMBED_CACHE_PATH=$PWD/.cache/fastembed \
  .venv/bin/python scripts/eval/agent_behavior.py --mode full --json /tmp/beh.json

# 只跑某几条 / 与上次基线比回归
.venv/bin/python scripts/eval/agent_behavior.py --mode full --only bg3-amulet-chain
.venv/bin/python scripts/eval/agent_behavior.py --mode full --baseline /tmp/beh.json
```

## 用例入库流程（线上问题 → 评测）

1. 从 `data/turns/*.jsonl` 捞出问题轮次（`scripts/analysis/turns_report.py` 聚合，
   `--spoilers` 看剧透命中，L3 优先人工抽检）。
2. 写进 `docs/ROUTING_MISROUTE_*.md` 这类事故记录：现象、证据、根因、建议。
3. 固化成 L0 用例（要能一键复现决策错误）+ L2 场景（要能复现整条链路）。
4. 修完代码：摘掉 `xfail` / `known_issue`，确认从红转绿。
5. 需要语义级判定的（「换个说法讲 L3」这类词表抓不到的），进 L3 RAGAS + 裁判。

## 基线管理

- L2 报告用 `--json` 存一份（建议放仓库外，如 `/tmp` 或 `data/eval/`，后者已 gitignore）。
- 下次用 `--baseline` 对比，只提示新增失败与已修复项，不重跑历史结论。
- 判据看趋势不看单点：路由用例应长期 100%，citation/spoiler 允许有波动但要能解释。

## 已知缺口

- L2 全量模式尚未跑过一轮（工具记录器在一次性脚本里验证过，未端到端验证）。
- L3 RAGAS 从未实跑（需 Mac 装 ragas/datasets）。
- 剧透词表只有 BG3；丝之歌/空洞骑士要 axis-aware 词表（act / ability 轴不同）。
- 单轮分钟级、每轮都真调 LLM，因此 L2 全量不进 CI，只在改路由/提示词/检索前后手动跑。
