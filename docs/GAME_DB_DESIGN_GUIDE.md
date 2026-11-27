# 游戏结构化库设计指南（给 AI agent 的操作手册）

> 目的：新游戏接入时，指导 AI 自己完成「探源 → 设计表结构 → 写 builder → 验证」。
> 结构化表没有通用 schema，每款游戏的 infobox 模板和实体关系都不一样，
> 但**方法论是通用的**。本文档沉淀 KCD2（2026-11-27）和 BFV（2026-11-27）
> 两次接入的完整经验。代码实现在 `scripts/build/build_game_db.py`。

## 一、先想清楚结构化表是干什么的

三个消费者，设计时要同时满足：

1. **`query_structured_data`（数值类查询）**：启发式中文指令解析
   （"查询 X"/"所有 X"/"伤害最高的 X"/数值筛选），不是通用 SQL。
   → 需要数值列是**真的数值**（REAL），不是字符串。
2. **`build_glossary.py`（术语表抽取）**：从带 `name` 列的表抽实体名，
   翻译成中文做查询时扩展。
   → 角色/地点/任务这类**名录型实体也要有表**，哪怕只有 name 列。
3. **层级/关系类追问**：「大战役下有什么小章节」「第 X 章有什么任务」。
   → 这类关系代码没有通式，必须按游戏建模（见第四节）。

## 二、探源（设计前必做，别拍脑袋建表）

1. **看分类结构**：用 `action=query&list=categorymembers` 探候选分类。
   - ⚠️ **子分类可能独立**：BFV 实测 `Melee Weapons/Grenades/Attachments`
     的页面完全不在父分类 `Weapons` 里（27 页差点全丢）。
     `cmtype=subcat` 看子分类树，逐类对比成员差集。
2. **抽 3–5 个代表页看 wikitext**（`action=parse&prop=wikitext`）：
   - 确认 infobox 模板名（KCD2 是 `KCD2 item infobox/multi`，BFV 是
     `Infobox/weapon`，每个 wiki 都不一样）；
   - 确认数值字段的格式（单值/区间/多行表/带单位/嵌套模板）。
3. **找层级关系的载体**：wiki 一般自带结构，常见的三种：
   - infobox 的 `prev`/`next` 链（BFV `Infobox/level`）；
   - 页面内 `==大节==` 下的 `===小节===`（BFV 战役的子章节在 `==Plot==` 下）；
   - 分类树父子（KCD2 的 `KCD2: Bladed weapons` 这种类型归属）。

## 三、表设计模式（三种，按实体类型选）

| 模式 | 适用 | 案例 | 要点 |
|---|---|---|---|
| **数值详表** | 武器/防具/载具等 | KCD2 `items`、BFV `weapons` | infobox 逐字段解析；数值列用 parse_numeric 保证 REAL |
| **名录简表** | 角色/地点/任务 | KCD2 `characters/locations/quests`、BFV `vehicles/maps/gadgets/characters` | `slug + name + description`（首句）即可，**存在意义主要是术语表抽取** |
| **关系表** | 章节/进度/归属 | BFV `levels`（prev/next + sub_chapters） | 按游戏定制，无通式；见第四节 |

通用约定：

- `slug`：`lower() + 去撇号/括号 + 只留 a-z0-9-`（`_kcd2_slug()`，跨游戏复用）；
- 表名必须和 `scripts/enrich/build_glossary.py` 的 `TABLE_KINDS` 对齐，
  否则术语抽取静默跳过（SELECT 失败不报错，这是故意的，但排查时很坑）；
- 多语言混合库（terraria zh）另行考虑，本文只覆盖 en-only 模式。

## 四、层级关系：按游戏建模（没有银弹）

关系数据是「wiki 里本来就有，只是没去拿」——设计时先探再建：

- **BFV 战役**：`Infobox/level` 有 `prev`/`next`（线性章节链）；
  子章节是 `==Plot==` 下的 `===小节===`。建成 `levels` 表
  （prev/next/place/date/sub_chapters 逗号分隔），实测还原出
  My Country Calling → Under No Flag → … → Epilogue 的完整链。
- **KCD2**：主线推进靠地区（特罗斯基→库滕堡），这个层级不在 wiki
  结构里，而是进了 `game_registry.spoiler_axis="chapter"` + 进度轴
  （`config/progress/`），说明层级也不一定都在 DB 里，看消费方是谁。
- **提醒**：数据进表 ≠ 问得出来。`query_structured_data` 是启发式解析，
  层级追问（「X 有哪些小节」）目前不一定命中关系列——数据先行，
  查询工具扩展是后续独立工作。

## 五、血泪教训（都是真实踩过的坑）

1. **分类请求超时静默截断**：`get_category_members` 遇超时直接 break，
   KCD2 characters 只抓到 50 页（实际 600+）。已加 3 次重试，但
   **跑完务必核对页数是否符合 probe 阶段的预期**——这是唯一能发现
   静默截断的办法。
2. **`extract_infobox` 非贪婪匹配的截断风险**：pattern 是 `…(.*?)\}\}`，
   参数值里嵌套模板（如 BFV 的 `{{Recoil|…}}`）会让匹配提前结束，
   **排在外层模板后面的字段会丢**。设计解析顺序时把要用的字段
   放在嵌套模板之前，或单独正则。
3. **多行 infobox 值只取到首行**：BFV `damage` 是多行射程表，
   解析器逐行按 `|` 开头取参数，首行 "25.1 - 12.0" 之后的 `*…` 行
   全被跳过。区间值要专门拆首尾（`_bfv_damage_range()`）。
4. **大小写/变体不一致**：KCD2 防具 type 出现 `Other Clothes` /
   `Other clothes` 两种写法，分组统计会裂成两类。查询前没有规范化。
5. **LLM 译名 ≠ 玩家社区叫法**：Kuttenberg 被译成「库特纳霍拉」
   （真实捷克地名），玩家叫「库滕堡」；Henry of Skalitz 被译成
   「斯卡里茨的亨利」，玩家叫「亨利」。→ **冒烟验证过的实体补
   `variants`**，别指望一次翻译到位。
6. **模板/概览页混进分类**：`Weapons/KCD2` 这类聚合页和真实条目
   同分类，按标题后缀（`/KCD2`）或 `ns` 过滤，builder 里要排除。

## 六、验收清单（builder 写完必跑）

1. `python scripts/build/build_game_db.py --game <game>` 跑通，
   核对各类页数与 probe 预期一致（防静默截断）；
2. 抽查 3 条已知实体，数值逐字段比对 wiki 原文（防解析错位）；
3. `.venv/bin/python -m pytest tests -q` 全绿
   （`test_game_registry::test_数据文件真实存在` 会卡住没建 DB 的游戏）；
4. `python scripts/enrich/build_glossary.py --game <game> --dry-run`
   确认名录表被抽到（类别分布里要有 character/location 等）；
5. 向量库建完跑 `scripts/eval/retrieval_smoke.py`，把该游戏用例加进去。
