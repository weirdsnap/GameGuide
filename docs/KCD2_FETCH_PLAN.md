# 天国拯救2（KCD2）数据采集计划（2026-11-27 调查）

> 状态：**调查完成，待用户拍板后开工**。本文只记录源调研结论与抓取方案，
> 实现步骤（注册表登记、抓取、建库）按 AGENTS.md 的新增游戏清单执行。

## 一、候选源对比（实测连通性 + 覆盖盘点）

| 源 | 连通性 | KCD2 覆盖 | 结论 |
|---|---|---|---|
| `kingdom-come-deliverance.fandom.com` | 走代理 ✅（`192.168.5.1:7890`，直连 000） | **~2,500 页**，分类树完整（见下） | **主源** |
| `kingdomcomedeliverance.wiki.gg` | 直连 ✅ | 仅 249 页，主线任务 1 页、技能/Perks 无分类，覆盖太薄 | 弃用 |
| `kingdomcomedeliverance2.wiki.fextralife.com` | 直连/代理都 ❌（子域 000、主域 502，DNS 能解析但连不通） | 覆盖最全但拿不到 | 弃用 |
| 中文源 | 不存在中文社区 wiki（只有机核等文章站，非 wiki 结构） | — | **不做 zh 库**（同 BG3/mhw 的 en-only 模式） |

## 二、fandom KCD2 分类树实测页数（2026-11-27）

根分类 `Category:Kingdom Come: Deliverance II` 下 12 个 `KCD2:` 子分类：

| 分类 | 页数 | 备注 |
|---|---|---|
| Characters | **734** | 含子分类 Characters by Gender / DLC / Skill teachers |
| Perks | 331 | Character progression 下还有 Skills 15、Stats 21、Status effects 3 |
| Codex | 278 | 法典条目（设定/背景） |
| Locations | 230 | 含 Interesting sites / Fast travel 子分类 |
| Weapons | 215 | 子分类：Ammo / Bladed / Blunt / Polearms / Ranged / Shields |
| Quests | 171 | Main 33 + Side 62 + Tasks 76 + DLC 0（含子分类成员） |
| Achievements | 88 | |
| Armour | 16+子分类 | Arm/Body/Head/Leg armour、Horse tack、Jewellery、Armour sets、Women's clothing |
| Items | 10+子分类 | Documents / Food / Materials / Potions / Quest items / Other / DLC items |
| Gameplay | 6+ | Character progression（Perks/Skills/Stats/Status effects） |
| DLC / Patches | 少量 | 可抓可不抓 |

样例页大小：Wedding Crashers（主线任务）wikitext 约 27KB，结构正常。
分类成员有分页（Characters 第一批 500 + cmcontinue），`refetch_wiki.py` 自带分页。

## 三、抓取方案

用现有的 `scripts/fetch/refetch_wiki.py`（fandom 分类直采路径，silksong 同款）：

- 源：`--api https://kingdom-come-deliverance.fandom.com/api.php`（registry 登记时配）
- 网络：**必须挂代理** `https_proxy=http://192.168.5.1:7890`（直连 000，与 HK 同款）
- 分类：上面整棵 `KCD2:` 树（Achievements, Armour, Characters, Codex, DLC,
  Gameplay, Items, Locations, Quests, Weapons 及其全部子分类）
- 注意坑（REFETCH_PLAN 复盘同款）：
  - `caffeinate -i` 包裹长任务（这台 Mac 电池下 1 分钟休眠）
  - Cloudflare 滴漏：脚本自带每 200 页换连接 + 90s 硬超时 + 看门狗
  - Characters 734 页里可能有大量 stub NPC 页（剥离后不足 30 字的会被丢，属正常）
- 质量闸门：沿用默认阈值即可（en 源不查 cjk_ratio）；
  抓完抽样 20 页比对「标题 vs 正文对题」

## 四、入库与后续

1. `game_registry.py` 登记 `kcd2`（ signals 含「天国拯救/KCD2/Kingdom Come」等，
   `spoiler_axis` 建议 `chapter`——主线任务线性推进，与 BG3 的 act 同构，
   登记时定）。
2. 抓取 → `--replace` 覆盖 → 质量闸门 → commit。
3. 结构化 DB：`build_game_db.py` 通用构建器若不适配 KCD2 的 infobox 字段，
   再写 `scripts/build/kcd2_db.py`（武器/护甲数值表是 KCD2 的高频问法）。
4. Mac 建向量库（`mac_prepare_build.sh --game kcd2`）→ `retrieval_smoke.py`
   加 KCD2 用例（如「库滕堡在哪」「长剑怎么选」）→ `vectorstores.sh kcd2` →
   服务器 `api.sh`。
5. KCD2 无中文源，不做 zh 库；中文提问靠跨语言 embedding + 后续可补术语表
   （`games/kcd2/data/kcd2_glossary.jsonl`），这是 I-28 之后已验证的路径。

## 五、风险

- fandom 分类成员里可能混入 KCD1 页面（老站同库），抓完用闸门 +
  抽样检查；若混入多，改用 `KCD2:` 叶分类逐个抓而不是整树递归。
- DLC 分类为空属正常（Brushes with Death 等 DLC 页面可能挂在主分类下）。
