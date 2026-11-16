# 路由缺口挖掘：2026-11-16 扫描线上 turns 的发现

状态：**发现已登记，未实施**（本文件只记问题与建议，代码与测试改动归 Mac 端一并做）
数据源：`data/turns/*.jsonl`（11-12 ~ 11-16，共 84 轮）
配套：`docs/ROUTING_MISROUTE_2026-11-15.md`（误判事故）、`docs/P0_ROUTING_GATE_PLAN.md`（P0 方案）、`docs/EVAL_SYSTEM.md`

## 0. 全量分布（基线）

```
route   agent 68 / menu 13 / fallback 3
game    baldurs_gate3 63 / None 16 / hollow_knight 4 / oni 1
```

13 轮 menu 里，6 轮是用户拿接口做杂项测试（「1」「kg」「通用知识」「threejs…」），
「今天天气怎么样」×2 属正常拦截。**真正是游戏问题却被拦掉的 5 轮里，
3 轮的根因可归到本文的 G1/G2/G3。**

## 1. G1：「BG」这类简写不被识别，用户纠正成本落在自己身上

证据：`data/turns/2026-11-14.jsonl` 11:39，用户输入「BG」→ `route=menu`。

根因：`GAME_EXACT_PATTERNS["baldurs_gate3"]` 只有 `bg3`，signals 里也只有 `bg3`；
`detect_game("BG")` 实测返回 `(None, 0)`。

建议：exact_patterns 增加**整句式**锚定，只认「整句就是 bg」，避免进入正文误触：

```python
r"^\s*bg\s*$"
```

不要加 `\bbg\b`——`_match_signal` 的边界检查对中文不成立（中文与字母同属 `\w`），
「玩bg」这种写法会被漏掉，又在别处误伤，得不偿失。

## 2. G2：专名覆盖缺口会让整轮放弃知识库（比误判更常见）

证据：

- `2026-11-15 00:17`「明莎拉这个职业怎么说？应该怎么发展」→ `route=fallback`，
  `tool_calls=0`，回答自述「⚠️ 以下基于我的训练数据，知识库没有逐条核对」。
- 同一句的用户写法「明萨拉用压迫灵魂之剑还是静止者」→ 被 HK 的「灵魂」抢走（事故记录）。

根因：`detect_game("明莎拉这个职业怎么说")` 与 `detect_game("明萨拉用什么武器")`
实测都是 `(None, 0)`——**两种写法都不在 BG3 signals 里**。
注意「明莎拉」是模型自己的译名，用户写「明萨拉」，同一个 NPC 两个中文名，
只收一个等于收不到。

建议：

1. BG3 signals 同时收 `明萨拉` / `明莎拉` / `minthara`（译名别名一律并列）。
2. 同类高风险专名一并补：贾希拉 / 哈尔辛 / 明斯克 / 米佐拉 / 拉斐尔 / 卡菈克（异体）等，
   以 `scripts/eval/golden` 与 glossary 里的常见写法为准。
3. 长期解：把 BG3 术语表（约 4238 条中英映射，见 `src/rag_agent/glossary.py`）
   接成信号源，按「中文名 → 游戏」建倒排索引；这属后续阶段，不在 P0。

## 3. G3：fallback 不承接上下文，追问会掉菜单（会话碎裂）

证据（两处，模式一致）：

```
11-14 12:09  fallback  「我看到那个空壳铠甲了，但是我是从侧门的飞下去了…」tools=0
11-14 12:09  menu      「但是我飞下去没有办法回来了」        ← 同一会话的下一句
11-15 00:17  fallback  「明莎拉这个职业怎么说？」             tools=0
11-15 00:19  menu      「我选的好像是单手武器+盾牌」          ← 同一会话的下一句
```

根因：`graph.detect_game_node` 的 fallback 分支（`graph.py:189-191`）只返回
`detected_game` 与 `prompt`，不写 `last_game` / `last_game_confirmed`。
新会话首句判不出游戏 → fallback 用常识作答 → 下一句仍无 `last_game`、又无信号
→ 落到 `else` 的「新菜单」分支。用户体感：聊了两句突然被问「你想了解哪款游戏」。

建议（按代价排序）：

1. **文案级（5 行）**：fallback 的回答末尾加一句「如果你告诉我游戏名，我就能查知识库」，
   把恢复路径交给用户，而不是静默掉菜单。`_FALLBACKPT` 里加。
2. **状态级（约 10 行）**：fallback 分支返回 `last_game: last_game`（原值）且
   `last_game_confirmed: False`，让 `_game_from_history` 在前端 history 里仍能捞到线索；
   同时 menu 分支判断「有 history 且有过 fallback」时改为沿用 + 提示，不弹菜单。
3. **不要**用「fallback 时猜一个游戏」来解决——猜错就是伪造来源的温床。

推荐先做 1，观察一周；2 涉及状态语义，与 P0 的 `last_game_confirmed` 改造同批做更省事。

## 4. G4：观测缺口——来源不可事后审计

`turn_record` 只存 `tool_calls` 计数，不存工具返回的来源标题。事故记录 §2.1 那种
「编造不存在的三条来源」只能靠回答正文反推，无法批量检测，P1 引用校验也就没有样本来源。

建议：`turn_record` 增加 `sources`（本轮所有工具返回里出现过的标题集合，去重后取前 N 条，
上限 30 条防膨胀），与 `spoiler_hits` 同级。有了它，P1 的「引用校验」可以先离线跑：
扫历史 `answer` 里的「来源：知识库「X」」，比 `sources` 里有没有 X，得出伪引用率。

## 5. G5：专有词与通用词打平就判不出（P0-2 的实证用例）

实测 `detect_game("我在玩bg3，问个护符")` → `(None, 0.25)`。
BG3 靠 `bg3` 得 1 分、HK 靠「护符」得 1 分，打平后 conf 折半 = 0.25 < 0.3，判不出。

这是真实写法（游戏名 + 通用词混在一句），现状会走兜底。P0-2 的加权分数
（专有 1.0 / 通用 0.25）能自然解掉：conf 变 0.8。已作为算例写进 P0 方案 §4.2，
用例见下。

## 6. 待固化用例清单（交 Mac 端一并加进测试）

| # | 用例 | 断言 | 归属 |
|---|---|---|---|
| 1 | `test_bare_bg_alias` | `route("BG")` → `baldurs_gate3` | G1 |
| 2 | `test_minthara_alias_both_spellings` | `detect_game("明莎拉这个职业怎么说")` 与 `detect_game("明萨拉用什么武器")` 都 → BG3 | G2 |
| 3 | `test_fallback_then_followup_keeps_context` | 首轮「明莎拉这个职业怎么说」→ fallback；次轮「我选的好像是单手武器+盾牌」→ 不弹菜单（非 `menu_text`） | G3 |
| 4 | `test_mixed_proper_and_generic_picks_proper` | `detect_game("我在玩bg3，问个护符")[0] == "baldurs_gate3"` | G5 / P0-2 |
| 5 | `test_turn_record_has_sources` | `turn_record` 输出含 `sources` 键 | G4 |

1–4 都是目前会失败的用例：先挂 `xfail(strict=True)` + reason 指回本文件，
修好变 XPASS 再摘——与仓库既有约定一致。5 是新增字段的契约测试，实施时一起加。

## 7. 已排除（不是缺陷）

- `11-12 11:33`「我卡在最后之光旅店，附近的塞伦捏秘密任务怎么做」→ menu：
  `最后之光旅店`/`塞伦涅` 直到 `5b037d0`（11-12 13:27）才进注册表，
  该轮发生在 11:33，属「已修未上线」，不新增用例。
- `11-12 21:02`、`11-14 11:39` 之后的 `menu` 多为接口杂项测试，非游戏问答。

## 8. 这一轮的方法论产出

`route` 字段只有三种值，每种指向不同缺陷类型，扫一遍就能分类：

- `agent` + `game` 与上下文不符 → 误判（P0 范围，已有体系）
- `fallback` + `tool_calls=0` → 识别不出但被兜底，**没有告警**（本文 G2/G3）
- `menu` → 上下文断裂的终点（本文 G1/G3）

下次扫描优先看「`fallback` 与 `menu` 相邻出现」的会话段，那是断裂最集中的指纹。
