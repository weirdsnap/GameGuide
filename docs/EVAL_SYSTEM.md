# 评测体系（四层，从快到慢）

一次线上问题不该只变成一条「知道了」，而应该落到某一层评测里，下次自动报警。
本文说清四层各管什么、用例怎么入库、怎么跑。

| 层 | 入口 | 跑什么 | 要 LLM | 耗时 | 何时跑 |
|---|---|---|---|---|---|
| L0 单测 | `pytest tests/` | 纯函数与契约：路由决策、剧透分级、额度、SSE 协议 | 否 | ~2s | 每次改动 / CI |
| L1 冒烟评估 | `tests/test_light.py` | 路由 12 + 检索 13 + E2E 6（脚本式，pytest 里被 collect_ignore） | E2E 部分要 | 分钟级 | 部署前 |
| L1.5 单游戏 e2e | `scripts/eval/hk_e2e_gate.sh` → `tests/test_qa.py` | 空洞骑士 20 条关键词式用例，按改动触发 | 是 | 约 10 分钟 | 每周至少一次 / HK 改动后 |
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

## L1.5：单游戏端到端——按改动触发，不做定时

`tests/test_qa.py` 是空洞骑士的关键词式回归（20 条：螳螂爪中英 / 梦钉 / 水晶之心 /
伊丝玛的眼泪 / 帝王之翼 / 泪水之城 / 深巢入口 / 愚人竞技场 / 白宫 / Boss / 辐光 /
白王 / 大黄蜂 / 面具碎片 / 骨钉升级 + silksong_info / unrelated_game / unrelated_math 三条边界）。
每条断言是「回答必须包含 X、不得出现 Y」，真调 LLM 与检索。

**历史问题**：它标了 `@pytest.mark.e2e`，默认永远 skip，等于存在但不验证任何东西
（僵尸用例，还在 `pytest -q` 的 skipped 计数里占 20）。

**现在的规则**：用闸门脚本判定「该不该跑」，不靠人记。

```bash
scripts/eval/hk_e2e_gate.sh            # 判定 + 需要时跑
scripts/eval/hk_e2e_gate.sh --dry-run  # 只看判定结果
scripts/eval/hk_e2e_gate.sh --force    # 无条件跑
```

判定逻辑：读 `data/eval/hk_e2e.state` 里上次通过的 commit，
`git diff --name-only <last>..HEAD` 命中下列任一前缀才跑，否则跳过：

```
src/rag_agent/game_registry.py      # HK 信号词与配置
src/rag_agent/game_router.py        # 共享路由
src/rag_agent/graph.py              # 决策节点与提示词
src/rag_agent/tools.py              # 检索工具
src/rag_agent/vectorstore.py        # 分块与 embedding
games/hollow_knight/                # HK 数据与库
tests/test_qa.py                    # 用例本身
requirements.txt                    # 换 embedding 模型也算
```

（完整名单以脚本里的 `HK_PATHS` 数组为准，上表可能漏记共享模块如 `multi_agent.py`。）

**不做定时任务**。装 cron、换机器重装、失败了还得有人盯，维护成本大过收益；HK 改动
本来就低频，改成「改动触发 + 两个发现入口」：

1. **部署前提醒**：`scripts/deploy/api.sh` 启动成功后会调本脚本 `--dry-run`，判定「该跑」
   就打印一行提示（只提示，不阻断启动、不改退出码）。
2. **手动**：改完 HK 相关文件后自己跑一次 `scripts/eval/hk_e2e_gate.sh`。

真要全自动时再加一行 crontab（不要提前装）：

```cron
0 4 * * 1  cd ~/learning/GameGuide && scripts/eval/hk_e2e_gate.sh >> data/eval/hk_e2e.log 2>&1
```

**约定**：

- 只在**通过**时写 `hk_e2e.state`；失败不更新，下次触发仍会跑——避免一次失败被
  「SHA 没变」跳过而永远看不见。`data/eval/hk_e2e.history` 记 pass/FAIL 流水。
- 需要 `.env` 的 API key 与 `FASTEMBED_CACHE_PATH`（脚本默认 `$ROOT/.cache/fastembed`）；
  缓存不在时退出码 2，不当作失败。
- 一次跑 20 问、约 10 分钟，会消耗 DeepSeek 额度，所以不进 CI、不跟单测绑。
- 新增游戏若要类似保障，把用例拆到 `tests/test_qa_<game>.py` 并在脚本 `HK_PATHS`
  旁再加一套路径与状态文件；不要往 `test_qa.py` 塞别的游戏。

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

### 2026-11-16 全量模式首跑：结论 + 两个评测器缺陷（待 Mac 修）

首次真正跑 `--mode full`（9 用例 14 轮，服务器，约 4 分钟）：

- 最终结果：干净通过 5/9，已知缺陷 4，失败 0；基线存 `data/eval/beh_baseline.json`（本地、gitignore）。
- 4 个 known_issue 全部同一根因：HK 的「护符」「灵魂」抢走 BG3 会话；「BG」「明莎拉」
  识别不出落 None。即 P0 + G1 + G2 那批，与线上 turns 挖出的完全一致——表现层已能复现。

**缺陷 A：full 模式没注入 `context` 前置轮次**（`run_full()`）。routing 模式有这段，
full 模式漏了，于是依赖前文的轮次（「狂笑术的代价是什么」）在无历史时落 menu，
被判成路由错——假阴性。修法：`run_full` 的每个 case 开头按 `case["context"]` 真跑一遍
（不计分），history 照常追加，与 `run_routing()` 保持一致。

**缺陷 B：`check_citation` 抽取过宽**。原来用 `来源|知识库|条目` 匹配整行再取该行所有「」，
把正文引号当来源：实测把「代价」「护符里困着幽灵」当成伪造引用（3 条假报）。
修法：只认 `来源\s*[:：]` 这种标注，且每条标注只负责它后面到下一个标注之间的引号内容：

```python
_SOURCE_MARK = re.compile(r"来源\s*[:：]")
for line in (answer or "").splitlines():
    marks = list(_SOURCE_MARK.finditer(line))
    for i, m in enumerate(marks):
        seg_end = marks[i + 1].start() if i + 1 < len(marks) else len(line)
        quoted.extend(_QUOTED.findall(line[m.end():seg_end]))
```

两处修完后重跑：3 个失败 → 0，对照组（bg3-wet-lightning / bg3-to-hk-switch /
bg3-to-oni-switch）全绿，证明修的是评测器而不是把问题掩下去。

**一个重要观察**：bg3-amulet-chain T3 被误路由到 HK 后，模型在回答里写「这段内容属于
《博德之门3》，不在当前游戏范围内……如果继续回答，我就越界了」——游戏边界规则兜住了，
但路由已经错了。说明误路由不只是检索错，还会直接丢掉可用答案。

**待查（不稳定性）**：`bg3-spoiler-act2` 首轮报 L3 命中（`Nightsong` / `夜之歌`，act2），
第二、三轮同样的提问却通过。偶发 L3 比稳定泄露更危险（单次跑看不到），需要多跑几次
测复现率；也可能只是词表把「报名」也按最坏情况标成 L3（`SPOILER_SEVERITY.md` 的已知局限）。

**附带噪音**：全量跑时 `query_structured_data` 每轮都刷一堆
`DB query error: no such column: health/cost/hp/damage/buy_price/reward_eb`——它在逐个
试列名，失败也打日志（1 秒内几十行）。不影响结果，但线上日志会被淹没。

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
