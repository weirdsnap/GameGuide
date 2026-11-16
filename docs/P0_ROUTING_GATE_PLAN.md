# P0 方案：切换门槛 + 通用词降权（交 Mac 端实施）

状态：**方案已定，未实施**（2026-11-16 由服务器端出方案；实现归 Mac 端）
背景：`docs/ROUTING_MISROUTE_2026-11-15.md` §3.2、§4 的 P0 两行
评测：`docs/EVAL_SYSTEM.md`（L0 代码层 + L2 表现层）
剧透口径：`docs/SPOILER_SEVERITY.md`（本方案不涉及）

本方案的验收标准只有一条：**现有挂 xfail / known_issue 的用例从红转绿，对照组保持绿，
其余用例零回归。** 测试先红后绿就是「改对了」的定义，不要靠人工观察线上表现判断。

---

## 1. 目标

已确认的会话（`last_game_confirmed=True`）不再被单个**跨游戏通用词**推翻。
新建会话（无 history）仍然允许用通用词定向——删掉通用词会让「这个护符怎么拿」无解。

## 2. 约束

1. 不碰检索链路、embedding、提示词分层（`build_common_rules`、`build_game_prompt`）。
2. 不删通用词。**降权 + 门槛**，不是删除。
3. 最小改动：只动 4 个文件——`game_registry.py`、`game_router.py`、`graph.py`、`multi_agent.py`。
4. 不改 `detect_game` 的公开签名（有别的调用方与测试），新增函数承载新信息。

## 3. 现状机制（为什么单个词就能切走）

`src/rag_agent/game_router.py:51-79`：

```python
scores[game] += 1          # 每个命中信号 +1，不分轻重
confidence = best_score / total
```

只有一个游戏命中时 `total == best_score`，**confidence 恒为 1.0**。
`src/rag_agent/graph.py:149,169-172`：

```python
GAME_CONFIDENCE_TH = 0.4
if confidence >= GAME_CONFIDENCE_TH:
    if last_game and last_game_confirmed and game_key != last_game:
        switched = True          # 无额外条件，1.0 ≥ 0.4 直接切
```

于是 `"护符"`、`"灵魂"`（hollow_knight.signals）与 `"法术"`（baldurs_gate3.signals）
成了硬切换开关，双向都成立。

`src/rag_agent/multi_agent.py:23-36` 的 `_game_from_history` 也用同一个
`GAME_CONFIDENCE_TH` 反推历史游戏——改动必须与它同步，见 §6。

## 4. 设计

### 4.1 信号分两档

在 `game_registry.py` 增加一张显式清单（**不要**在 signals 里就地打标记，注册表已经是单源真理，
另起一个 set 最好审、最不容易漏）：

```python
# 跨游戏通用词：在 ≥2 款游戏里都有意义，只作弱证据（权重 0.25），
# 不得作为会话切换的依据。新增词条必须在 PR 说明里给出「哪款游戏也用它」。
GENERIC_SIGNALS: set[str] = {
    # 空洞骑士
    "灵魂", "护符", "梦境", "梦钉",
    # 博德之门3
    "法术", "动作", "按等级", "第5版", "5e",
    # 缺氧
    "电池", "太空", "火箭", "冷却", "散热", "温度管理", "发电", "管道", "精炼",
    # 泰拉瑞亚
    "钓鱼", "向导", "神圣", "腐化", "血腥", "恶魔", "史莱姆", "矿车",
    # 赛博朋克2077
    "义体", "黑客", "圣物",
    # VA-11 Hall-A
    "调制", "调酒", "鸡尾酒",
}
```

取舍原则：只登记「确实在另一款游戏里也会出现」的词。
像 `"圣巢"`、`"夜之城"`、`"螳螂领主"`、`"下城区"` 这类地名/专名留在专有档。
名单**宁少勿多**——多登记一个词，就少一分新会话识别率。

`GENERIC_SIGNALS` 需要在注册表导出（`from rag_agent.game_registry import GENERIC_SIGNALS`）。

### 4.2 打分：权重 + 封顶

```python
W_EXCLUSIVE = 1.0
W_GENERIC = 0.25
GEN_ONLY_CAP = 0.35        # 全靠通用词时 confidence 的上限
```

`detect_game_detail(query) -> (game_key | None, confidence, exclusive_hit: bool)`：

1. `exact_patterns` 命中 → `(game, 1.0, True)`，保持现有短路（游戏名是硬证据）。
2. 逐游戏累加：专有信号 `+= W_EXCLUSIVE`，通用词 `+= W_GENERIC`；同时记
   `exclusive_hits[game]`。
3. `confidence = best_score / total_score`；打平时沿用现有的 `* 0.5` 折半。
4. 封顶规则：
   - `exclusive_hits[best] == 0` 且 `len(scores) > 1` → 返回 `(None, confidence, False)`。
     两个游戏都只靠通用词互相打架时，正确结论是「不知道」，不是挑一个。
   - `exclusive_hits[best] == 0` 且只有一个游戏命中 → `confidence = min(confidence, GEN_ONLY_CAP)`，
     `exclusive_hit=False`。
5. `confidence < 0.3` → `(None, confidence, False)`（保持现有最低阈值语义）。

`detect_game(query) -> (game, confidence)` 改成对 `detect_game_detail` 的兼容包装，
外部调用方与既有测试零改动。

算例（写成测试断言用）：

| 提问 | 命中 | confidence | exclusive |
|---|---|---|---|
| 这个护符怎么拿（无历史） | HK 护符 0.25 | min(1.0, 0.35) = **0.35** | False |
| 护符里的幽灵那个任务的 | HK 护符 0.25 | **0.35** | False |
| 明萨拉用压迫灵魂之剑还是静止者？ | BG3 明萨拉 1.0 / HK 灵魂 0.25 → total 1.25 | **0.80** | True |
| 空洞骑士螳螂爪怎么拿 | exact → 1.0 | **1.0** | True |
| 法术护符（两个游戏都只有通用词） | BG3 0.25 / HK 0.25 | — | 返回 **None** |
| 我在玩bg3，问个护符（专有+通用混句） | BG3 bg3 1.0 / HK 护符 0.25 → total 1.25 | **0.80** | True |

### 4.3 阈值拆成两个

`graph.py` 现在一个 `GAME_CONFIDENCE_TH = 0.4` 同时管「定游戏」和「切游戏」，语义过载：

```python
DETECT_TH = 0.30    # 新建会话（无 history）靠这个定游戏；通用词封顶 0.35 刚好过
SWITCH_TH = 0.60    # 会话中段从 A 切到 B
```

**这两个常量必须与 `multi_agent._game_from_history` 共用同一个定义**（从 `graph` 导入或
提到 `rag_agent/routing_consts.py`，二选一，别各写一份字面量）。旧常量
`GAME_CONFIDENCE_TH` 可以留着做别名，但不要在两处出现不同数值。

### 4.4 决策表（`detect_game_node` 改为查表）

| last_game 已确认 | 新游戏 == last_game | 新游戏有专有命中 | conf | 结果 |
|---|---|---|---|---|
| 否（新会话） | — | 不限 | ≥ DETECT_TH | 定该游戏 |
| 否 | — | 不限 | < DETECT_TH | 走原有兜底链（switch_query / 外部游戏 / 菜单） |
| 是 | 是 | 不限 | ≥ DETECT_TH | 维持，刷新 prompt |
| 是 | 否 | 是 | ≥ SWITCH_TH | 切换（`switched=True`，追加忽略旧上下文提示） |
| 是 | 否 | 是 | < SWITCH_TH | **沿用 last_game**，静默 |
| 是 | 否 | 否 | 任意 | **沿用 last_game**，静默 |

「静默沿用」= 回到 `game_key = last_game` 那条路径，不弹菜单、不清上下文、不写 switched。
这是本次改动的行为核心：**宁可留在旧游戏里答不好，也不要跳到错游戏里编答案**——
错库会引发伪造来源（见事故记录 §2.1），答不上来不会。

### 4.5 数据层顺手修（同批，纯配置）

1. BG3 signals 补同伴名：`明萨拉` / `minthara`、`贾希拉` / `jaheira`、
   `哈尔辛` / `halsin`、`明斯克` / `minsc`。
   （`bg3-minthara-weapons` 用例双保险：既靠专有词赢过 HK 的通用词，也补上表覆盖缺口。）
2. `cyberpunk2077.signals`：`"漩"` → `"漩涡帮"`；删掉单字母 `"v"`
   （有边界检查但收益低噪声高，`cp2077`/`夜之城`/`义体` 已足以定向）。
3. `oni` 的 `"太空"`、`"火箭"`、`"电池"` 等不删，登记进 `GENERIC_SIGNALS`。

## 5. 测试先行（Mac 端实施顺序）

### 5.1 摘 xfail 的三个挂点

| 用例 | 位置 | 现在 | 改后应 |
|---|---|---|---|
| `online-2026-11-15#2346` | `tests/test_routing_regression.py:66` | xfail strict | 通过（删条目里的 `True` → `False`，删 `_XFAIL_REASON` 对应项） |
| `online-2026-11-16#0014` | 同上 `:71` | xfail strict | 通过（同上） |
| `test_generic_signal_alone_does_not_switch[charm/soul/spell]` | 同上 `:113` | 函数内 `add_marker(xfail)` | 通过（删掉那个 `add_marker`） |
| `bg3-amulet-chain` | `scripts/eval/behavior_cases.jsonl` | `known_issue: true` | 删掉该字段 |
| `bg3-minthara-weapons` | 同上 | `known_issue: true` | 删掉该字段 |

摘标记是验收的一部分：`xfail(strict=True)` 修好后变 XPASS 会直接报 failure，
不摘测试反而红——这是设计出来的强制动作。

### 5.2 新增用例（L0，必须能在无 LLM 环境跑）

```python
# 1. 降权不许过头：新会话单通用词仍要能定向（保护 hk-charm-slot 语义）
def test_new_session_generic_still_orients():
    out = route("亡者之怒占几个护符槽？")
    assert out.get("detected_game") == "hollow_knight"

# 2. 两个游戏都只有通用词 → 视为无结论，不许乱定、更不许切
def test_conflicting_generic_terms_yield_none():
    assert detect_game("法术护符")[0] is None

# 3. 专有词仍能切（防门槛把正当切换一起掐死）
def test_proper_noun_beats_generic():
    out = route("明萨拉和影心谁更适合当奶妈", last_game="hollow_knight",
                confirmed=True, history=["HK 的追问"])
    assert out.get("detected_game") == "baldurs_gate3"

# 4. 静默沿用不带切换标记（前端会读 game_switched 决定提示）
def test_generic_word_does_not_flag_switch():
    out = route("这个护符怎么样？", last_game="baldurs_gate3", confirmed=True,
                history=["BG3 的追问"])
    assert out.get("game_switched") is False
    assert out.get("menu_text") is None

# 5. 数据契约：单字中文信号（已有 test_no_single_char_cjk_signals）
#    修掉「漩」后把 _SINGLE_CHAR_DEBT 里的 ('cyberpunk2077','漩') 删掉，
#    那条 xfail 从「已知债务」变成「真守卫」。

# 6. 历史反推与决策同源：同一句在新会话能定游戏时，历史反推也必须能定
def test_history_inference_uses_detect_threshold():
    assert _game_from_history([{"role": "user", "content": "亡者之怒占几个护符槽？"}]) \
        == "hollow_knight"
```

### 5.3 明确不要做的事

- 不要把 `DETECT_TH` 提到 0.4 以上：通用词封顶 0.35 会被自己卡死，新会话识别率暴跌。
- 不要为「法术」单独写特例判断（`if "法术" in q: ...`）。通用词靠集合，不靠 if。
- 不要动 `is_switch_query` 的语义（「换个游戏」是用户显式意图，必须立刻切）。
- 不要顺手实现 P1（引用校验、兜底约束），那是另一轮，见事故记录 §4。

## 6. 最容易踩的连锁点

`multi_agent._game_from_history`（`multi_agent.py:23-36`）用 `confidence >= GAME_CONFIDENCE_TH`
从 history 里反向推导游戏。降权后通用词句子的 confidence 从 1.0 掉到 0.35：

- 若历史反推仍用 0.4，**历史里那句「护符」推不出游戏** → `last_game=None` →
  前端每轮重建 history 的机制下，会话会反复回到「无历史」状态，表现为
  **首轮正常、第二轮开始问什么都弹菜单**。这是本次改动最可能的线上事故。
- 必做：两个常量同源；上线前用真实 history 跑一遍两轮会话（第一轮通用词、第二轮追问）。

## 7. 验收清单（Mac 端交付前逐条勾）

```
1. pytest -q                                 → 全部通过，且 xfail 数量减少 3
                                                （原 172 passed / 20 skipped / 6 xfailed）
2. pytest tests/test_routing_regression.py -q → 无 xfail 残留（或仅剩与本次无关的）
3. python scripts/eval/agent_behavior.py      → 7/7 干净通过，known_issue 0，失败 0
4. 两轮真实会话手测（FASTEMBED_CACHE_PATH 设好）：
   「亡者之怒占几个护符槽？」→ HK；接着「那它升级要多少？」→ 仍是 HK，不弹菜单
   「bg3 卡拉克用厄运之锤还是警觉长戟好」→ BG3；接着「这个护符怎么样？」→ 仍是 BG3
5. git push 到 origin，服务器端 pull 后重启 API（重启归服务器端）
```

第 4 条是唯一需要 LLM 的验收项，其余全在秒级。

## 8. 风险与回滚

| 风险 | 症状 | 处置 |
|---|---|---|
| 通用词集合登记过多 | 新会话识别率下降，冷启动弹菜单变多 | 从 `GENERIC_SIGNALS` 删词，只看名单，不调阈值 |
| 阈值与历史反推不同源 | 第二轮开始弹菜单 | §6 的检查，一条测试锁住 |
| `SWITCH_TH=0.60` 太高 | 用户真换游戏却切不过去 | 专有词单命中 conf 通常是 0.8~1.0，正常切换不受影响；真出问题先降门槛再加信号 |
| 线上仍是旧进程 | 改完没生效 | 重启 API 后看 `/api/status` 的 version 是否带新 SHA |

回滚 = `git revert` 单个提交，无数据迁移、无向量库重建，因此风险可控。

## 9. 交付边界

- **Mac 端**：按本文改 4 个文件 + 测试，跑 §7 的 1–4 条，push。
- **服务器端**：pull 后重启 API、跑 §7 第 5 条、抽查线上 turns 里 route 字段是否稳定；
  之后继续做问题挖掘（turns 全量扫描、引用伪造样本、P1 方案）。
