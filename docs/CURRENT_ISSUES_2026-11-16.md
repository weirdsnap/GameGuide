# 当前问题清单（2026-11-16 快照）

> 记录 Mac/服务器两端在 2026-11-16 这轮路由与防剧透改动后的**未解决问题**，
> 避免只在对话里说过就丢。新问题按日期追加，解决后把条目移到「已解决」并附 commit。
>
> 快照基线：`main @ 507db1f`（Mac 本地另有 `65f0df9`，未推送）；线上
> `beta6-88-g15b62f6`；`pytest` = 186 passed / 20 skipped / 1 xfailed（G4）；
> `agent_behavior.py` routing = 9/9、known_issue 0。

## P0：阻塞质量或线上验证

### 1. G4：turn 记录没有 sources，伪造引用无法批量审计

- 现象：`api_server.turn_record` 只有 `tool_calls` 计数，没有工具返回的来源标题；
  事故里「编造三条不存在的来源」只能靠读回答正文反推。
- 已有契约用例：`tests/test_routing_gaps.py::test_turn_record_carries_sources`
  （当前唯一 xfail）。
- 归属：服务器侧（`api_server.py`）。
- 验收：上述用例转绿；`turn_record` 输出去重、上限 30、每条截断 120 字符；
  采集侧从工具返回的 `【参考 i】[EN] 标题` 抽标题塞进 `meta["sources"]`。

### 2. L2 评测器两个缺陷，full 模式基线不可比

来源：`docs/EVAL_SYSTEM.md`「2026-11-16 全量模式首跑」与 TODO。

- **缺陷 A**：`scripts/eval/agent_behavior.py` 的 `run_full()` 没有像 routing 模式
  那样注入 `case["context"]` 前置轮次 → 依赖前文的轮次在无历史时落 menu，被判成
  路由错（假阴性）。
- **缺陷 B**：`check_citation()` 用 `来源|知识库|条目` 匹配整行，再把整行所有「」
  当引用，正文引号会被误报成伪引用（实测假报「代价」「护符里困着幽灵」）。
- 影响：full 模式基线无法与旧版对比；`bg3-spoiler-act2` 的偶发 L3
  （`Nightsong` / `夜之歌`）无法可靠复现。
- 归属：测试/评测 owner（服务器侧），文档 TODO 已登记。

### 3. BG3 v3 向量库是否已部署未确认

- 本地 v3：66,698 块，`title` 前置 100%，`metadata.act` 6,611 块（act1/2/3 =
  1,747/945/3,919）。
- 服务器按 AGENTS 交接仍是「旧库补 language」的补丁版（无 title 前置、无 act），
  `index.pkl.bak-20261112` 也还在；线上代码已是 `15b62f6`，但 store 是否替换没有回执。
- 影响：不部署 v3，`prepend_title` 的收益和 `metadata.act` 过滤只吃到一部分
  （`act_index.py` 只能补 326 个同名地点/任务）。
- 归属：Mac 上传（`vectorstores.sh`）+ 服务器重启/验收。

### 4. Silksong 数据不可用，且采集卡在网络上

- 本地库：196 个 title / **47,155 块**；`Grimm` 3,071 块、`Hollow Knight` 1,835 块、
  `Zote` 1,295 块，混入大量空洞骑士页面。
- `wiki_data.md`：29MB / 196 页 = 平均 150KB/页；抽查 `Baldur Shell` 正文是 Zote 来历、
  `Absolute Radiance` 开头是 HK beta 发售信息——是整站导航/搜索碎片，不是页面正文。
- 网络：Mac 与服务器直连 fandom.com 都超时（服务器 DNS 仅 AAAA、无 IPv6 路由）；
  `scripts/fetch/silksong.py` 的分类过滤已修，但重抓仍需要代理或换源。
- 归属：用户决定网络方案 → 服务器重采 → Mac 重建。

## P1：质量与数据问题

### 5. 泰拉瑞亚中文 wiki 是垃圾数据

- `games/terraria/data/wiki_data_zh.md`：90 页，其中 **78 页是版本号**
  （`1.0`、`1.4.4`…），只剩 1 个物品页；中文问「克苏鲁之眼怎么召唤」top-1 是
  `Platforms`，期望实体不在 top-8。
- 2026-11-16 已跑 `clean_wiki.py`（-8.0%：删 18 个模板、70 行重复），但**标题层面的
  垃圾改不掉**，仍需重采；重采前不要指望中文检索。
- 根因：zh 分类配置缺失/用英文分类查中文 wiki；Fandom 不可达，暂时无法重采。
- 影响：泰拉瑞亚中文检索基本不可用；英文库正常。
- 归属：数据采集（网络/分类配置）→ Mac 重建。

### 6. title 前置只对 BG3 生效

各库 `page_content` 以 `metadata.title` 开头的比例：

```
baldurs_gate3 100% | oni 16% | va11halla 14% | terraria 10%
hollow_knight 3%  | cyberpunk2077 5% | mhw 1% | silksong 0%
```

- 新加 `prepend_title_to_chunks` 只惠及 BG3；HK/ONI/Terraria 等仍是旧 embedding，
  跨语实体召回没有吃到该修复。
- 归属：Mac 重建（数据量约 2 万块，不含 silksong）。

### 7. 检索评测存在假绿与覆盖缺口

- `tests/test_light.py --retrieval`：12/13，ONI `cooling loop` 真失败
  （top-5 是 Thermal Conductivity/Insulator，期望 Cool Steam Vent/Coolant）。
- HK 中文用例「螳螂爪怎么拿」top-1 是 `Void Heart`，靠 top-2 `Mantis Lords`
  命中期望词 `Mantis` 过关——断言过松。
- Silksong 两条用例靠污染文档（`Absolute Radiance` / `Hornet Sentinel`）过关。
- 用例只覆盖 HK/ONI/Terraria/Silksong，没有 BG3/CP2077/VA-11/MHW；中文只有 1 条。
- 归属：测试 owner（服务器侧），本次未动。

### 8. 剧透覆盖仍有限

- BG3 只有 6,611/66,698（约 10%）的块带 `metadata.act`；`act_index.py` 用结构化库
  补了 326 个地点/任务名（含复合标题包含匹配），但普通 NPC/物品页仍无上界。
- `SPOILER_TERMS` 只有 BG3；丝之歌已改 act 轴，但没有审计词表。
- L3 RAGAS 从未实跑（需 Mac 装 ragas/datasets）。
- 归属：Mac 建库打标 + 服务器词表/评测。

### 9. 内存加载策略需要实测

- `_load_vectorstore` 已从 `maxsize=8` 降到 3；BG3 单库常驻约 610–860MB。
- 风险：多游戏交替会反复加载/释放 BG3 库，线上 P90 27.9s 中可能有重载成本。
- 归属：Mac 侧观测/优化（空闲释放或预热）。

## P2：工程与配置

### 10. Caddy 端口与仓库不一致

- 仓库 `Caddyfile` 和 README 写 `https://api.weirdsnap.top`（443），实际 443 连不上，
  HTTP 会 308 跳到 `:8443`；`https://api.weirdsnap.top:8443/api/status` 可用。
- 影响：文档/客户端按 443 配会失败。
- 归属：服务器侧配置（Caddy）与文档同步。

### 11. 观测层待改进（服务器侧）

- `answer_head` 只存前 200 字，尾部命中词在报告里看不到（复盘对不上）。
- 同一实体中英文重复计数（`Mason's Guild` + `石匠工会`）。
- `data/turns/*.jsonl` 无轮转/清理；`log_turn` 是事件循环里的同步写。
- 答案落盘的留存/隐私策略未定（目前仅 gitignore）。
- `turns_report.py`：`--days 0`、缺 `ts` 记录会 KeyError。

### 12. 结构化查询日志噪音（已修，待推送）

- `query_structured_data` 的数值分支会把 `hp/health/cost/reward_eb...` 逐个试列，
  缺失列每轮刷几十行 `DB query error: no such column`。
- 已改：先 `PRAGMA table_info` 只查真实列 + 候选列去重。
- commit `65f0df9`（本地领先 origin 1 个，随本次文档一起推送）。

## 数据清洗（2026-11-16 服务器侧，已完成，待重建生效）

脚本：`scripts/build/clean_wiki.py`（就地清洗 `games/*/data/wiki_data*.md`）

规则：Infobox/DBEntry/Block Quote 压平成字段文本（空值与 image/baseid 类丢弃）；
`#dpl:`/Cleanup 等纯导航模板删除；`<ref>`/HTML 注释/标签清理 + 实体反转义；
短导航行删除，**超长行（>600B，导航与正文挤在一行）只断句不删行**；节内长重复行去重。

| 文件 | 前 | 后 | 变化 |
|---|---|---|---|
| baldurs_gate3 | 27.5MB | 26.9MB | -2.4%（1579 行断句、7035 行去重） |
| cyberpunk2077 | 4.95MB | 4.11MB | -16.9%（压平 2342 个模板、删 2237 个 ref、2504 个标签） |
| mhw | 1.13MB | 1.08MB | -4.8%（16 个超长导航行断句、250 行去重） |
| hollow_knight | 1.05MB | 1.03MB | -2.2%（3162 个 HTML 标签） |
| oni | 998KB | 957KB | -4.1%（947 个标签，含 `<math>`） |
| terraria | 2.33MB | 2.28MB | -2.0% |
| terraria_zh | 857KB | 788KB | -8.0%（垃圾标题仍在，救不回来） |
| va11halla | 133KB | 131KB | -1.3% |

校验：`load_wiki_documents()` 抽出的文档数清洗前后一致（BG3 9341 / cp2077 2562 /
HK 416 / oni 400 / terraria 1622 / va11 79·27），标题无 Unknown；二次运行全 0 命中
（幂等）。原文件备份在磁盘 `*.bak-20261116`（已 gitignore，不入库）。

顺手删掉两个 0 字节垃圾库：`games/hollow_knight/hollow_knight_data.db`、
`games/terraria/data/terraria_data.db`（无代码引用）。

**未做（需要重抓，卡在 Fandom 不可达）**：silksong 整站碎片、terraria 中文标题垃圾。
**生效条件**：改动的是源文本，向量库还是旧的 —— 需要 Mac 按现有流程重建（分块与嵌入都变了）。

## 本轮已解决（别再重复排查）

- 路由 P0：通用词降权 + 切换门槛；`BG`/明萨拉/明莎拉识别（`5ee96b9`）。
- 进度推断 + `max_act` 确定性注入（`c844e09`）。
- 兜底路径进度边界与来源约束（`e38ed98`）。
- 结构化库 act 列补检索期上界（`15b62f6`）。
- 服务器已摘 13 处 xfail / 4 个 known_issue；线上已部署到 `15b62f6`。
- `pytest` 186 passed / 20 skipped / 1 xfailed；`agent_behavior` routing 9/9。

## 验收命令

```bash
# 代码层（期望 186/20/1，G4 修完是 187/20/0）
.venv/bin/python -m pytest tests -q

# 表现层（路由，不调 LLM）
.venv/bin/python scripts/eval/agent_behavior.py

# L1 检索（会加载向量库；ONI cooling 目前预期失败）
.venv/bin/python tests/test_light.py --retrieval

# 线上版本/端口
curl -sS https://api.weirdsnap.top:8443/api/status
```
