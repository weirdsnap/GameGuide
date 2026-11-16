# 路由缺口：分析与建议方案（2026-11-16）

状态：**分析 + 方案已定，测试已落地，代码未动**
代码与配置改动归 Mac 端；服务器端负责挖问题、写测试、验收。
配套：`docs/ROUTING_MISROUTE_2026-11-15.md`（误判事故）、`docs/P0_ROUTING_GATE_PLAN.md`（P0 切换门槛方案）、`docs/EVAL_SYSTEM.md`（评测体系）。

---

## 0. 摘要

扫 11-12 ~ 11-16 共 84 轮线上记录，除已知的四轮误判外，另找出 5 个独立缺口。
其中 3 个是纯配置/文案级（1 行到 5 行），2 个需要状态或观测层改动。
**全部 8 条断言在裸跑下失败、在 xfail 下固化**，说明缺口真实存在，
且评测体系能持续盯住它们直到修完——这正是「测试先红后绿」的用法。

| 编号 | 缺口 | 现象 | 改法代价 | 优先级 |
|---|---|---|---|---|
| G1 | 「BG」简写不识别 | 用户打「BG」被弹菜单 | 1 行配置 | P0.5 |
| G2 | NPC 两种译法都不在信号表 | 整轮放弃知识库 | 1 行配置 × N | P0.5 |
| G3 | 兜底路径静默，不引导 | 用户自己重打「BG3」三次 | 3 行文案 | P0.5 |
| G4 | 来源不可事后审计 | 伪引用只能靠读回答反推 | 契约 5 行 + 采集 20 行 | P1 |
| G5 | 专有+通用打平就判不出 | 「我在玩bg3，问个护符」→ 判不出 | 由 P0-2 顺带解决 | P0 |

P0.5 = 不改路由决策逻辑、不碰检索与提示词分层、不影响既有 27 条回归用例的一批改动，
可以与 P0 同批上线，也可以先上。

---

## 1. 扫描方法与基线

数据源 `data/turns/*.jsonl`（一轮一行，`route` / `game` / `tool_calls` / `answer_head`）：

```
route   agent 68 / menu 13 / fallback 3
game    baldurs_gate3 63 / None 16 / hollow_knight 4 / oni 1
```

`route` 只有三种值，每种指向一类缺陷，扫一遍就能分型：

- `agent` 且 `game` 与上下文不符 → **误判**（P0 范围，已有 `ROUTING_MISROUTE` 覆盖）；
- `fallback` 且 `tool_calls=0` → **识别不出但被兜底**，没有任何告警（本文 G2 / G3）；
- `menu` → **上下文断裂的终点**（本文 G1 / G3）。

下次扫描优先看「`fallback` 与 `menu` 相邻出现」的会话段，那是断裂最集中的指纹。

---

## 2. G1：「BG」这类简写不被识别

**现象**：`2026-11-14 11:39` 用户输入「BG」→ `route=menu` → 12:10 才手打「BG3」恢复。

**根因**：`GAME_EXACT_PATTERNS["baldurs_gate3"]` 只有 `bg3`，`signals` 也只有 `bg3`。
实测 `detect_game("BG") == (None, 0)`、`detect_game("bg") == (None, 0)`。

**建议**：exact_patterns 追加**整句式**锚定，只认「整句就是 bg / BG」：

```python
r"^\s*bg\s*$"
```

不要写 `\bbg\b`：Python 的 `\b` 以 `\w` 判边界，中文与字母同属 `\w`，
「玩bg」「bg里」这类写法根本不会命中边界；加了既漏又可能误伤英文正文。

**验证**：`test_bare_bg_alias`、`test_bare_bg_alias_switches_mid_session`
（后者防「简写只在新建会话能用、会话中段切不回去」）。

**代价**：1 行。

---

## 3. G2：同一个 NPC 的两种中文写法都不在信号表

**现象**（两条独立证据，都很典型）：

- `2026-11-15 00:17`「明莎拉这个职业怎么说？应该怎么发展」→ `route=fallback`、
  `tool_calls=0`，回答自述「⚠️ 以下基于我的训练数据，知识库没有逐条核对」——
  **整轮放弃知识库，且没有任何告警**。
- `2026-11-16 00:14`「明萨拉用压迫灵魂之剑还是静止者」→ 被 HK 的「灵魂」抢走
  （已在事故记录里，走的是误判路径）。

**根因**：`detect_game("明莎拉这个职业怎么说")` 与 `detect_game("明萨拉用什么武器")`
实测都是 `(None, 0)`。`明莎拉` 是模型自己的译名，`明萨拉` 是用户写法，
**两种都不在 BG3 signals 里**。信号表只收一个译名等于一个都没收。

**建议**：

1. BG3 signals 并列收：`明萨拉` / `明莎拉` / `minthara`。
2. 同类高风险专名一并补（游戏里常出现的同伴/重要 NPC）：
   `贾希拉` / `jaheira`、`哈尔辛` / `halsin`、`明斯克` / `minsc`、
   `米佐拉` / `mizora`、`拉斐尔` / `raphael`、`卡菈克`（`卡拉克` 的异体）。
3. 长期解：把 BG3 术语表（约 4238 条中英映射，`src/rag_agent/glossary.py`）
   接成信号源，按「中文名 → 游戏」建倒排索引。不在 P0.5 内。

**验证**：`test_minthara_alias_recognized[mingshala/mingsala/minthara]` 三条。

**代价**：每项 1 行配置，纯数据。

---

## 4. G3：兜底路径静默，恢复成本落在用户身上

**现象**（三次同一模式，全部由用户手动恢复）：

```
11-14 12:09  fallback  「我看到那个空壳铠甲了…」      → 12:09 menu「但是我飞下去…」→ 12:10 用户打「BG3」
11-14 12:19  fallback  「关于那个空壳铠甲你再讲讲…」  → 12:20 menu「主厅？」        → 12:20 用户打「BG3」
11-15 00:17  fallback  「明莎拉这个职业怎么说？」      → 00:19 menu「我选的好像是…」 → 00:19 用户打「bg3」
```

**根因（两点，别混）**：

1. `_FALLBACKPT`（`graph.py:52-61`）是给 LLM 的指令，四条全是「怎么答」，
   没有一条让模型告诉用户**说游戏名就能查库**。菜单文案有这句
   （`_MENU_NEW` 的「直接告诉我游戏名称就可以开始啦！」），兜底没有。
2. `fallback` 分支（`graph.py:189-191`）只返回 `detected_game` 与 `prompt`，
   不写 `last_game`。新会话首句判不出游戏 → 兜底 → 下一句在 history 为空时直接掉 `menu`。
   线上 `history_msgs` 字段是后来才加的，这三轮都没值，无法确认 history 是
   前端没传还是用户刷新；**服务端只能保证「有 history 时不弹菜单」，当前已满足**
   （`test_followup_after_fallback_no_menu` 是绿的，作为回归保护）。

**建议**（按代价排序，做第 1 条即可）：

1. **文案级**：`_FALLBACKPT` 追加第 5 条——
   「如果用户想要知识库级别的准确信息，提示他直接说出游戏名称（支持列表见上）。」
2. 状态级（可选，与 P0 同批）：`fallback` 分支把 `last_game` 原值带回并标记
   `confirmed=False`，让「有弱候选」的会话下一轮能沿用，而不是再次兜底。
   注意不要在没有候选时**猜**一个游戏——猜错就是伪造来源的温床。
3. 监控级（P1）：把「`fallback` 后紧跟 `menu`」作为会话断裂指标进日报。

**验证**：`test_fallback_prompt_guides_user`（断言文案含「游戏名」）。

**代价**：文案 3 行。

---

## 5. G4：来源不可事后审计

**现象**：`turn_record` 只有 `tool_calls` 计数，没有工具返回的标题。
事故记录 §2.1 那种「编造三条不存在的来源」只能靠读回答正文反推，无法批量检测。

**根因**：`api_server.turn_record`（`api_server.py:283-320`）没有 `sources` 字段；
`search_knowledge_base` 的返回文本其实**已经带来源**——
每块以 `【参考 i】[EN] 标题` 开头（`tools.py:295-299`），只是没人把它抽出来。

**建议（两步走）**：

1. 契约先落（5 行）：`turn_record` 接受 `meta["sources"]`，去重保序、上限 30 条、
   每条截断 120 字符，写进记录。这一步单独可测。
2. 采集再接（约 20 行）：在 agent 轮次的工具返回聚合处用正则
   `【参考 \d+】\[?[A-Z]{2}\]?\s*(.+)` 抽标题，塞进 `meta["sources"]`。
   有了它，P1 的引用校验可以先离线跑：扫历史答案里的「来源：知识库「X」」，
   比 `sources` 里有没有 X，得出**伪引用率**。

**验证**：`test_turn_record_carries_sources`（只覆盖第 1 步的契约）。

**代价**：5 行 + 20 行，两批可分开合。

---

## 6. G5：专有词与通用词等权，打平即判不出

**现象**：实测 `detect_game("我在玩bg3，问个护符") == (None, 0.25)`。
BG3 靠 `bg3` 记 1 分、HK 靠「护符」记 1 分，打平后 conf 折半 0.25 < 0.3 → 判不出。

**根因**：`game_router.detect_game` 每个信号 +1，不看轻重（P0-2 已覆盖）。

**建议**：不单独改，**由 P0-2 的权重（专有 1.0 / 通用 0.25）顺带解决**，改后 conf 0.8。
这条的价值是给 P0-2 提供了一个「不打补丁也应该自然变绿」的实证用例。

**验证**：`test_mixed_concrete_and_generic`。

---

## 7. 测试与评测的落地结果（本轮已跑）

### 7.1 代码层 L0（新文件 `tests/test_routing_gaps.py`）

与 `test_routing_regression.py` 分文件，原因是后者是 P0 实施挂点、Mac 端要改它，
本文件只登记独立缺口，避免双端改同一文件。

**裸跑（`--runxfail`，等价于「没有 xfail 保护时的真实结果」）**：

```
$ .venv/bin/python -m pytest tests/test_routing_gaps.py -q --runxfail
8 failed, 1 passed
```

8 条失败即 8 个缺口断言全部成立（G1×2、G2×3、G3×1、G5×1、G4×1）；
唯一通过的是 `test_followup_after_fallback_no_menu`——它本来就是回归保护，
现状已满足，改坏了才红。

**固化后全量**：

```
$ .venv/bin/python -m pytest -q
173 passed, 20 skipped, 14 xfailed      # 原为 172/20/6，新增 8 条 xfail，另 1 条真通过
```

### 7.2 表现层 L2（`scripts/eval/behavior_cases.jsonl` 追加 2 个场景）

```
$ .venv/bin/python scripts/eval/agent_behavior.py --mode routing
结果：干净通过 5/9，已知缺陷 4，失败 0
```

新增 `bg-shorthand-bg`、`bg-minthara-spelling` 均为 `known_issue`，
路由模式下分别显示 `实际=None`（落菜单 / 兜底）。修好后删 `known_issue` 字段即变真守卫。

### 7.3 这一轮证明了什么

- 缺口不是读日志时的猜测：8 条断言在真实代码上全部失败。
- 测试集是对的：失败点精确落在 `detect_game` / `detect_game_node` / `turn_record`
  这三个函数上，与根因分析一一对应，没有一条是靠「看起来像」写出来的。
- Mac 改完后只需看两件事：`--runxfail` 从 8 failed 变 0 failed，
  且在正常模式下 `xfail` 数量回落到 0（因为标记要被摘掉）。

---

## 8. 实施顺序与交付边界

**Mac 端（建议合成一个 commit 或两个：配置+文案 / 观测）**

1. G1 exact_patterns 1 行。
2. G2 signals 补 8~10 个别名。
3. G3 `_FALLBACKPT` 加第 5 条。
4. G4 第 1 步 `turn_record` 加 `sources`。
5. 摘掉本文件 8 处 `xfail`、2 处 `known_issue`（`bg3-amulet-chain`、`bg3-minthara-weapons` 归 P0）。
6. 跑 §7 的三条命令，全绿后 push。

**服务器端**

1. pull → 重启 API → 跑一轮真实会话抽检「BG」「明莎拉」两句。
2. G4 第 2 步上线一周后，离线扫历史答案做伪引用率统计，作为 P1 引用校验的输入。
3. 继续扫 turns（下一步：会话级 route 序列分段）。

**明确不做**

- 不为了兜底「猜」游戏（会产生伪引用）。
- 不把菜单文案扩展到兜底路径以外的位置。
- 不在这一轮接 BG3 术语表到信号源（属后续阶段）。
