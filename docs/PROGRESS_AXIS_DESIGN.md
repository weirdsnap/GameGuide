# 进度轴设计：从「章节数字」到「状态图」，以及提问不剧透的约束

> 2026-11-22 服务器侧（线上数据分析）提出。承接 `docs/SPOILER_CONTROL_PLAN.md`
> 的进度对象与三层防线，**替换其 §3.7 的轴定义**（那份文档把空洞骑士 / 丝之歌 /
> 泰拉瑞亚都写成 `none`，与后来注册表改成 `ability` / `act` 已不一致）。
>
> 分工：本文件、数据 schema、草案数据、契约测试归服务器侧；`src/rag_agent/` 下的
> 实现归 Mac 侧。**本文件不授权服务器侧改任何 `src/` 代码。**

## 1. 为什么要重做轴

### 1.1 现状：三款标了轴的博德之门式之外游戏，实际是零防线

实测（2026-11-22，服务器本机）：

| 事实 | 证据 |
|---|---|
| `infer_progress()` 只对 `axis == "act"` 生效 | `src/rag_agent/progress.py:96-101`，非 act 轴直接返回 `level=None` |
| act 轴的锚点表是 BG3 专用 | `progress.py:50-61`，`_ACT_ANCHORS` 只有最后之光旅店、月出之塔、下城区等 12 个 BG3 地名 |
| 丝之歌被标成 act 轴，但一个锚点都没有 | `game_registry.py:157` `"spoiler_axis": "act"`，锚点表无丝之歌词 |
| 丝之歌 chunk 没有 act 元数据 | `game_registry.py` 紧邻注释自述「max_act 过滤要等 wiki 重采集后给 chunk 打 act 元数据才生效」 |
| 丝之歌结构化库撑不起推断 | `abilities` 2 行且 `effect` / `acquisition` 全空；`areas` 15 行无前置关系；`bosses` 42 行只有 3 行有 `location` |
| 空洞骑士的 ability 轴同样空转 | HK 库无 `abilities` 表，只有 `areas` 53 行、`bosses` 47 行 |

合起来的结果：问丝之歌任何问题，进度恒为未知 → 提示词走「不追问、按条目 `[act N]` 标注作答」
→ 但条目没有 `[act N]` 标注 → **实际等于没有任何约束**。这是当前最大的防剧透缺口。

### 1.2 章节轴为什么套不上丝之歌

- 丝之歌的推进不是线性叙事推进，是**能力门槛造成的图收缩**：你没有抓钩就没有某些区域。
- 区域会被反复回访（同一张地图、同一批 NPC 在 Act 1/2/3 都出现），
  所以「当前在哪个区域」不构成进度——但「**第一次到达**某区域的偏序」是唯一的，
  这正是可以算的东西。
- 玩家不会说「第 2 章」，会说「我拿到抓钩了」「我在格雷穆尔」。BG3 的锚点机制
  （地名 → act）对丝之歌完全失效。

## 2. 设计目标与非目标

目标：

1. 新增一款游戏 = 加一个数据文件，**不改代码分支**。
2. 进度不再是单点数字，而是区间 `[lo, hi]`；作答只取下界，越界部分按存在性回答。
3. 进度推断不需要额外 LLM 调用（纯规则 + 图算法，毫秒级、可单测）。
4. 系统主动追问的**问句模板固定、槽位受限**，从结构上杜绝「XXX 死了吗」这类
   用剧透换进度的问法。
5. 有数据就精确、没数据就退化，不许因为缺数据而报错或静默失效。

非目标：

- 不做每款游戏的定制规则引擎。
- 不做跨会话的用户画像 / 账号进度同步（与 `SESSION_MEMORY_PLAN` 一致）。
- 不追求「猜准用户在哪」，只追求「不越过用户已确认的边界」。

## 3. 核心抽象：三个原语，一条代码路径

分叉压在**轴的形态**上，不压在游戏上。形态只有三种：

```
chain   带 rank 的线性刻度。进度 = 历史命中锚点的最大 rank
        适用：BG3(act 1-3)、CP2077(chapter)、VA-11 Hall-A(day)、MHW(HR)
dag     节点 + 前置边。进度 = 已确认节点集在 DAG 上的可达集与最长路径距离
        适用：丝之歌、空洞骑士、泰拉瑞亚
null    无进度概念，空实现
        适用：缺氧
```

三者实现同一个 Protocol：

```python
class ProgressModel(Protocol):
    game: str
    kind: Literal["chain", "dag", "null"]

    def evidence_targets(self) -> EvidenceTargets:
        """暴露本模型认识的锚点/节点词表，供证据抽取与问句护栏复用。"""

    def bound(self, evidence: Evidence) -> ProgressBound:
        """Evidence -> ProgressBound(lo, hi, axis_label, source, evidence)。
        chain 返回 lo == hi == rank；dag 返回 lo/hi 区间；null 返回 None。"""

    def render_note(self, bound: ProgressBound) -> str:
        """把 bound 渲染成提示词片段（统一模板，见 §7）。"""
```

`ProgressBound` 与旧 `Progress` 的关系：**`ProgressBound.lo` 等价于旧 `progress.level`**，
`hi` 是新字段。chain 模型下 `lo == hi`，旧调用点行为不变，可以平滑替换。

代码归属：`models.py`（三个类）、`evidence.py`（证据抽取，见 §5）、
`guard.py`（问句护栏，见 §8）——**共用，不含任何游戏名**。
loader 读 `config/progress/<game>.json`。加游戏只加 JSON。

## 4. 数据文件 schema

一个游戏一个 JSON，放 `config/progress/`（新建目录；草案暂放 `docs/progress_drafts/`）。

### 4.1 chain 模型

```json
{
  "game": "baldurs_gate3",
  "kind": "chain",
  "axis_label": "章",
  "max_rank": 3,
  "front_end_label": "第一章 / 第二章 / 第三章",
  "anchors": {
    "1": ["幽暗地域", "underdark", "地表", "德鲁伊营地", "goblin camp"],
    "2": ["最后之光旅店", "月出之塔", "大陵墓", "莎尔的试炼", "nightsong", "凯瑟里克"],
    "3": ["下城区", "利文顿", "钢铁卫士铸造厂", "主脑", "戈塔什", "奥林", "lower city"]
  },
  "explicit_patterns": ["第\\s*([一二三123])\\s*[章幕]", "\\bact\\s*([123一二三])\\b"],
  "reviewed": true
}
```

`anchors` 就是现在 `_ACT_ANCHORS` 的数据化，`explicit_patterns` 是
`_EXPLICIT_PATTERNS` 的数据化。行为完全一致，只是从代码搬到配置。

### 4.2 dag 模型

```json
{
  "game": "silksong",
  "kind": "dag",
  "axis_label": "幕",
  "act_of_node": true,
  "abilities": {
    "clawline":   {"name": "Clawline（抓钩线）", "act": 1, "from": ["bone-bottom"]},
    "swift-step": {"name": "Swift Step",          "act": 1, "from": []}
  },
  "areas": {
    "moss-grotto":   {"name": "Moss Grotto",  "act": 1, "requires_areas": [],            "requires_abilities": []},
    "bone-bottom":   {"name": "Bone Bottom",  "act": 1, "requires_areas": ["moss-grotto"], "requires_abilities": []},
    "marrow":        {"name": "Marrow",       "act": 1, "requires_areas": ["bone-bottom"], "requires_abilities": []},
    "citadel":       {"name": "Citadel",      "act": 2, "requires_areas": [], "requires_abilities": ["clawline"]}
  },
  "reviewed": false,
  "_status": "draft：节点与 act 归属待 wiki 校对，边关系只保留无争议项"
}
```

字段口径：

- `act`：1 起整数；`act_of_node: true` 表示启用「区域 → act」反查（见 §9）。
- `requires_areas` / `requires_abilities`：**全部满足**才可达（AND 语义）；
  引用的 id 必须存在（测试会查）。
- `requires_gates`：自由文本的非状态门槛（如 `"Last Judge"`、`"White key"`），
  只用于人读与提示词，不参与图算法，**不校验引用**。
- `_source`：这条断言的 wiki 依据（原文摘句或页名#小节）。校对时先看这个字段，
  不要盲信 `act`。`Connections` 段只给**无向邻接**，不能直接当 `requires_areas` 用。
- 若同一个区域有两条到达路径（如 Citadel 可走 5 bells 也可经 The Mists），
  在 `notes` 里写明，暂用 `requires_any` 数组预留。
- `reviewed: false` 时，加载器只允许它参与「低置信度」推断（只用作
  「用户提到该区域就把 lo 抬到该节点的 act」），不参与可达集剪枝——
  避免未校对的数据把用户限死在错误的层。

### 4.3 渐进增强：没有 DAG 数据也能用

| 数据齐备度 | 能力 | 成本 |
|---|---|---|
| 只有 `anchors` / 只有 `areas.*.act` | 用户提到 → 抬 lo；反向证据「怎么去 X」→ 记 X 未到达 | 几十行词表，半天 |
| 有完整 DAG | 可达集 + 最长路径 → 精确区间、能推断「不可能已到哪」 | 需要 wiki 前置关系 + 人工校 |

**所以不必等数据齐才开始用。** BG3 现在就是「chain 已齐」状态，丝之歌可以先上
「锚点版」，DAG 慢慢补。

## 5. 证据层：从用户话里榨进度，不问也能拿到

纯规则，不调 LLM。输入 `history`（只扫 `role == "user"`）+ 本轮 `question`。

### 5.1 证据类型与置信度

| 类型 | 触发 | conf | 效果 |
|---|---|---|---|
| explicit_progress | 「第 2 章 / 第三幕 / Act 2」 | 1.0 | lo = max(lo, rank)，source=user |
| arrival | 「我在 X」「我到了 X」「刚进 X」「现在在 X」 | 0.9 | lo = max(lo, rank(X)) |
| ability_gain | 「我拿到 X 了」「刚学了 X」 | 0.9 | 图内把 X 标记为已获得，重算可达集 |
| boss_kill | 「我打赢了 X」「X 干掉了」 | 0.85 | 同上（boss 在节点表里时） |
| howto_at | 「X 怎么打 / X 在哪拿 / 怎么过 X」 | 0.8 | lo = max(lo, rank(X))（问怎么打说明已碰上） |
| **reverse** | 「怎么去 X」「X 在哪」「X 怎么进」 | 0.8 | 记 X 为 **not_reached**，lo 不得越过 rank(X)-1 |
| mention_only | 只出现专名，问的是「是什么 / 有什么用 / 强不强」 | 0.4 | 只作提示，不抬 lo（可能只是听说） |

`reverse` 是这类游戏最值钱的证据：用户问「怎么去腐泽」= 他还没到腐泽。
它不需要用户配合，也不泄露任何东西。BG3 那边同样适用（问「怎么去下城区」= 还在 act 2）。

### 5.2 合并规则（沿用原方案的硬约束，不得放宽）

1. **单调不减**：`lo` 只上调；只有用户明说「我重开了 / 新周目」才重置。
2. **多来源取 max**：前端值 / 推断值 / 提问所指，取最大。
3. **提问即授权**：问题点到某幕，`lo` 至少抬到那一幕。
4. **只扫 user 消息**，不得用 assistant 答案反推进度。
5. **`not_reached` 优先于推断**：反向证据与正向推断冲突时（用户先问「怎么去 X」
   后又说「我在 X」），以更新的那条为准，并在 `evidence` 里两条都留档。

### 5.3 从证据到区间（dag 模型）

```
已知能力集 A = {ability_gain 命中} ∪ {area.requires_abilities 反推出必需的}   # 只取已确认的
可达区集 R = BFS(A, 全部节点)            # 前置全满足即入队
已到达集 V = {arrival / howto_at 命中的节点} ⊆ R
lo = max( rank(n) for n in V )                 # 已确认到达的最深层
hi = min( rank(n) for n in not_reached ) - 1   # 已确认没到的最浅层减一
if hi < lo or hi is None: hi = lo
```

作答只用 `lo`；`[lo, hi]` 之间的内容按「存在性 + 机制」给，越界的只给占位提示。
输出一句人话给 prompt 用，例如：

> 用户进度：Act 1 后段（已确认到达 Marrow，未到 Citadel；依据：我在 Marrow、怎么去 Citadel）。

对 `reviewed: false` 的数据，只允许用 `V` 里的最大 act，不做 `not_reached` 剪枝。

## 6. 为什么这不会变成「每款游戏一份代码」

| 层 | 是否含游戏名 | 共享程度 |
|---|---|---|
| `evidence.py` 证据抽取 | 否（词表由模型传入） | 全部游戏共享 |
| `models.py` ChainModel / DagModel / NullModel | 否 | 每种轴一份，共三份 |
| `guard.py` 问句护栏 | 否（专名集由模型与 glossary 提供） | 全部游戏共享 |
| `render_note()` 提示词渲染 | 否 | 全部游戏共享 |
| `config/progress/*.json` | 是 | **每游戏一份数据，零代码** |

真正的大头（证据抽取、护栏、提示词模板）与游戏无关；每游戏的差异只剩
「rank 怎么算」——链上取 max、图上跑最长路径，两个函数。

**防退化的硬约束**：任何 `if game == "..."` 都算违规。需要游戏特定行为时，
只能加模型字段（如 `act_of_node`、`requires_any`），不能加分支。
这条要写进 review checklist。

## 7. 提示词渲染：统一模板，per-game 只留名词

现在 `game_router.build_common_rules()` 里 `act` / `chapter` / `ability` 三段文案是
手写的，加游戏时很容易顺手再写一段。改为由 `render_note(bound)` 统一生成：

已知：

```
## 用户进度（系统注入，优先级高于历史）
- 当前进度：{axis_label} {lo}{区间说明，如「，可能已到 {hi}」}（依据：{evidence}）。
- 这是硬上界：不得正向描述 {axis_label} {lo} 之后的地点、任务、敌人或结局；
  必须提及时只给占位提示。
- 进度已知，不要再追问用户打到哪。
```

未知（所有轴统一，包括 act）：

```
## 用户进度（系统注入）
- 用户没有报过进度：不要替用户假设 {axis_label}。
- 条目属于哪一章就按哪一章说；不确定时不写章节，不要出现猜测性表述。
- 只给「存在 + 触发方式」，具体位置与后续按占位处理。
```

`axis == null` 的游戏返回空串，不注入。

## 8. 提问不剧透：一条机械规则 + 受限槽位

### 8.1 一条机械规则

> **追问里出现的每个专有名词，必须已经在用户自己的话里出现过。**

可机械检查，因为「专名」不需要通用 NER，只需要在**我们自己维护的词表**里匹配：
anchors + areas + abilities + glossary + 结构化表 `name` 列。检查过程：

```
Q_terms = 从候选问句里匹配到的已知专名
U_terms = 从历史 user 消息里匹配到的已知专名 ∪ 通用白名单（游戏名、"进度"等）
违规 = Q_terms - U_terms
若 违规 非空 → 该问法作废
```

精确、无歧义、可写契约测试（给定历史 + 候选问句，断言通过/拒绝）。

### 8.2 问句槽位受限（本次设计的核心约束）

系统主动追问**不允许从剧情实体生成问句**，只允许从「进度刻度」生成。
槽位只有四个：

```
{rank}      第几幕 / 第几章 / 第几天
{percent}   完成度百分比（该游戏有官方完成度时）
{area_known} 用户已经提过的区域
{ability_known} 用户已经提过的能力
```

模板固定几套，例如：

- 「你现在大概推到哪了？刚开局、到中途、还是快收尾？」
- 「这个游戏有完成度显示吗，大概多少？」
- 「你说的 {area_known}，那片探索完了吗？」
- 「{ability_known} 你拿到了吗？」

**永久禁止** `{character} + {状态}` 形式的问句：「XXX 死了没有」「你见过某某的结局吗」
「你打过蕾丝第二场了吗」。原因不是措辞不好，是这类问句泄露的不是进度，是剧情结构：

- 「XXX 死了没有」泄露三件事：X 有死亡线、死亡是这一作的一个节点、这件事值得讨论；
- 「蕾丝第二场」泄露她有多场战斗；
- 而且它们严格来说没在问进度，是在问「剧情已经进行到哪了」——用剧透换进度的最坏形态。

机制上靠「模板不允许角色槽位」保证，比事后扫词更可靠：扫词只能挡已收录的词，
挡不住新角色；槽位限制是结构性的。

### 8.3 用户自己提出的剧透假设，不算新泄露

用户问「XXX 死了吗」，说明他**已经知道** X 有死亡可能——这个信息是他带进来的。
所以不能一刀切拒答，但也绝不能在进度未知时确认或否认：「没死」与「死了，后面你会看到」
都是 L3 级泄露，否定式泄露同样成立。

正确处理：

- 进度已知且已过该节点 → 正常回答；
- 进度已知且未到 → 只给「这段涉及你后面的进度」，不确认不否认；
- 进度未知 → 不给结论，把权限交回用户：「这个涉及后期剧情，我先不给结论。
  你说一下大概打到哪了，我按你能看的范围讲。」
  这句话不引入任何新事实，也不打断他（安全部分照常答完）。

### 8.4 三级问法分级（对齐 `docs/SPOILER_SEVERITY.md`）

| 级别 | 可用的追问 | 说明 |
|---|---|---|
| 安全（零增量） | 完成度、幕数、粗档位（开局/中途/收尾）、用户提过的区域/能力 | 不承认任何未出现的事物 |
| 有条件（L1 换精度） | 「你进过 {某区域} 了吗」，区域可以是用户没提过的 | 承认地名存在，按已定口径 L1 几乎无损 |
| 禁止 | 任何 L2（机制 / Boss 名 / 任务名）、任何 L3（命运 / 生死 / 结局 / 背叛 / 牺牲） | 结构性禁止，不靠词表漏不漏 |

### 8.5 默认策略仍然是不追问

追问的收益（精度）与成本（剧透 + 打断）要分开算：线性叙事游戏追问「第几章」
性价比高；Metroidvania 用户描述位置门槛高、自己也未必讲得清，追问性价比低。
所以维持 `SPOILER_CONTROL_PLAN` §3.4 的方案 C：

> 不主动追问；保守作答 + 一句「告诉我打到哪我能讲细点」，把披露决定权留给用户，
> 且**一个会话最多出现一次**。§8 的护栏只服务于「真要问」的那一侧。

## 9. 顺带收益：用「区域 → act」反查替代 chunk 打标

丝之歌现在卡在「chunk 没有 act 元数据，所以没法按 act 过滤」。有了 dag 数据里
`areas.*.act`，检索侧可以走反查：

```
检索命中 chunk → 取标题 / 文本里的区域名 → 查 areas 表得 act → 与 lo 比较 → 决定是否降权/丢弃
```

比给每个 chunk 打标便宜得多，**不用重建向量库**，也不用等 wiki 重采集。
代价是精度略低（区域名不出现在文本里就查不到），但比现在「完全不设防」强得多。

`act_index.py` 现有的 `act_by_title()`（从 `locations` / `quests` 的 `act` 列建映射）
就是同一思路的 BG3 版；丝之歌版的数据源改成 `config/progress/silksong.json` 的
`areas.*.act` 即可复用同样机制。

## 10. 迁移路径（每步可独立上线、可回滚）

| 步 | 内容 | 依赖 | 归属 |
|---|---|---|---|
| S0 | 本文件 + 数据 schema + 草案数据 + schema 校验测试 | 无 | **服务器侧（已完成）** |
| S1 | `config/progress/` 落地，ChainModel 接线，替换 `progress.py` 的硬编码锚点（BG3 行为不变） | S0 | **Mac（已完成 2026-11-27）** |
| S2 | DagModel + 证据抽取（含 reverse）+ 丝之歌锚点版（`reviewed:false`，只抬 lo） | S1 | **Mac（已完成 2026-11-27）** |
| S3 | 问句护栏 `guard.py` 接线（槽位限制 + 专名子集检查） | S1 | Mac |
| S4 | 丝之歌 DAG 数据校齐（`reviewed:true`），启用可达集剪枝 | S2 + 数据 | Mac（数据）+ 服务器（校对工具） |
| S5 | 检索侧区域 → act 反查（替代 chunk 打标） | S2 / S4 | Mac 实现 + 服务器验证线上泄漏率 |

S1 单独上线就有收益：行为等价但去掉了硬编码，为后续铺路。
S2 的锚点版当天能让丝之歌从「零防线」变成「有防线」。

## 11. 验收（可自动化，服务器侧实跑）

1. **schema 契约**：`tests/test_progress_model_schema.py` —— 所有
   `docs/progress_drafts/*.json` 与将来的 `config/progress/*.json` 必须：
   顶层字段齐全、`kind` 合法、节点 id 唯一、边的引用存在、DAG 无环、
   `act` 为正整数、锚点非空且不重复。
2. **行为契约**（S2 后补）：
   - 「怎么去 Citadel」→ `lo` 不得 ≥ citadel 的 act；
   - 「我在 Marrow」→ `lo` ≥ marrow 的 act；
   - 历史里两条都有 → 以时间更新者为准；
   - 用户明说「我重开了」→ `lo` 归零。
3. **问句护栏契约**（S3 后补）：
   - 历史无 Citadel，候选问句「你进过 Citadel 了吗」→ 允许（L1 换精度）；
   - 候选问句「XXX 死了没有」→ 一律拒绝（槽位限制）；
   - 候选问句里出现用户未提过的 L2 名词 → 拒绝。
4. **线上指标**：`turn_record` 已有 `progress_level` / `progress_source`，
   S2 之后加 `progress_hi` 与 `evidence_kind`；丝之歌轮次的 `spoiler_hits`
   命中率应下降，`progress_source=inferred` 的占比应上升。

## 12. 风险与明确不做的事

- **数据不准比没有更糟**：`reviewed:false` 的数据只允许抬 lo，不许剪枝，
  就是为了防止错误的前置关系把用户限死。
- **不追求准确定位**：目标是「不越界」，不是「猜中位置」。区间越宽越安全。
- **不做**：per-game 代码分支、跨账号进度、进度猜谜式追问、用 LLM 做证据抽取
  （不可测、不稳定、成本高）。
- **不做**：给八款游戏都建 DAG。chain 类（BG3 / CP2077 / VA-11）先做，DAG 只给
  丝之歌试点，跑通再复制到空洞骑士 / 泰拉瑞亚。

## 13. 待裁决

1. 丝之歌的区域与能力数据谁来校：Mac 侧从 wiki 抽 + 人工校，还是先由服务器侧
   出一份英文名骨架交 Mac 填中文？目前倾向后者（骨架已在 `docs/progress_drafts/`）。
2. `act_of_node` 反查检索（S5）要不要先上「只降权不丢弃」的软过滤，观察一轮再改硬过滤。
3. 空洞骑士的能力偏序（能力 → 区域门槛）是否值得建：HK 玩家更常报「拿了几个护符」
   还是「打到哪个 boss」，需要线上样本。当前 HK 流量很少，暂缓。
4. **（S1 新增）BG3 第一章锚点是否加入**：草案 `docs/progress_drafts/baldurs_gate3.chain.json`
   补了第一章锚点，但含「地表」这类泛词——substring 匹配下「地表最强武器」会把进度
   误钉到第一章。S1 正式配置（`config/progress/`）未纳入以保持行为不变。
   若后续要加，建议只收「幽暗地域/underdark/德鲁伊营地」这类高置信词，剔除泛词。
