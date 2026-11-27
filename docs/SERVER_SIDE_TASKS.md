# 服务器端待办清单（交接给服务器侧 agent）

> 来源：Mac 侧，2026-11-22。与 `docs/MAC_SIDE_TASKS.md` 互为镜像——那份是服务器留给
> Mac 的，这份是 Mac 留给服务器的。
>
> 分工依据 AGENTS.md：`api_server.py`、部署重启、线上验证、`scripts/analysis/`、
> 日志/turns 数据分析归服务器侧；`src/rag_agent/`、`tests/`、`scripts/tool|build|fetch|enrich|eval`
> 归 Mac 侧。交叉修改同一文件时遵守 AGENTS.md 的「防冲突纪律」（开工必拉、push 前再拉）。

## P3-1 I-25 L2 progress 事件已落地 Mac 侧（f2fb79f），服务器需转发（2026-11-25）

Mac 侧已完成 `docs/STREAM_UX_PLAN.md` 的 **L2**：`multi_agent.ask_stream` 现在会产出
第三类事件 `("progress", {...})`，与 `token/meta/error` 并列：

```
("progress", {"phase": "tool_start", "tool": "search_knowledge_base", "round": 1, "elapsed": 3.2})
("progress", {"phase": "tool_done",  "tool": "search_knowledge_base", "round": 1, "elapsed": 6.8})
```

- `tool` 字段可能缺省（取不到名字时发通用 tool_start）；`elapsed` 是秒（相对请求开始）。
- 首 token 之后不再发；progress 不进 meta、不影响 quota 判定。
- 测试：`tests/test_stream_progress.py` 8 条（离线假图，全量 328 passed）。

**服务器侧动作（`api_server.py` 归你们）**：

1. `handle_ask_stream` 的事件分发目前只认 `token/meta/error`——`progress` 会被静默丢弃，
   需要加一个分支：`ev == "progress"` → 原样写 SSE `data: {"type":"progress", ...}`。
2. 与 L0 心跳（STREAM_UX_PLAN §L0）同批上线最划算：心跳兜底无工具阶段，progress
   覆盖工具阶段。
3. L4 记账要求 `first_token_ms` 观测字段；progress 事件数（`progress_events`）如果
   要落 turns，可以在转发分支顺手计数，由你们定。
4. 验收按 STREAM_UX_PLAN §3：`stream_chunks=0` 轮次归零、`first_token_ms` p90 有数。

**前端（L1/L3）暂缓**：用户 2026-11-25 拍板前端（博客仓库 `htmls/hollow_knight.html`）
先不动，所以 progress 事件短期内没有消费方——转发分支加上即可，不急着验证前端效果。


## P0-1 部署 Mac 侧最新提交并验证（f55ea5f）

Mac 侧已完成 I-16~I-20 + I-02 全部修复并推送（`f55ea5f`，本地 224 passed / routing 13/13）：

- **I-16** `check_citation` 假阳性：标记词（来源/知识库/数据库/条目）需紧跟引号才算引用，
  正文引号里的人名不再被当成伪造来源。——修完后 `bg3-lorrokan-act2` 的 full 报告里
  「伪造来源：洛若坎」这类噪音应消失，剩下的 Nightsong 才是真越界读数。
- **I-17** `build_common_rules` 三个进度轴（act/chapter/ability）删掉「先追问进度」，
  统一为「报过进度是硬上界；未报不追问，按条目 `[act N]`/问题所指作答」。
  与 `progress_prompt_note`、SPOILER_CONTROL_PLAN 的口径不再打架。
- **I-18** `_row_act` 多地名取 `max(act)`（防剧透宁多屏蔽不漏）；
  `_format_db_result` 数值筛选结果每行也附 `[act N]`。
- **I-20** 评测 `run_full` 的 context 占位 assistant 文本改为中性的「好的，明白了。」，
  不再用「（前置上下文，评测不跑这一轮）」干扰「不得假设章节」类用例。
- **I-02** `query_structured_data` 接术语表翻译：中文物品名命中术语表（阈值与检索扩展一致）
  时替换为英文全名并**整词优先 LIKE**；精确替换没盖住的模糊叫法
  （「山丘巨人的铁手套」对「山丘巨人之力手套」，≥4 字公共子串）用英文全名整词兜底。
  Mac 实测：两种叫法都命中 `Gauntlets of Hill Giant Strength` 且带 `[act 3]`，
  「影心」命中 Shadowheart。

服务器侧动作：

1. `git pull` → `bash scripts/deploy/api.sh` 重启。
2. `.venv/bin/python -m pytest tests -q`（基线 224 passed / 20 skipped / 1 xfailed）。
3. `.venv/bin/python scripts/eval/agent_behavior.py`（routing 应 13/13）。
4. full 模式重跑两条点踩溯源用例（I-16 修完后报告才有可读性）：
   ```bash
   FASTEMBED_CACHE_PATH=$PWD/.cache/fastembed \
     .venv/bin/python scripts/eval/agent_behavior.py --mode full \
     --only bg3-hill-giant-gauntlets bg3-lorrokan-act2
   ```
   - `bg3-hill-giant-gauntlets`：I-02+I-18 上线后「铁手套」两轮应命中正确条目且带
     `[act 3]`，不再出现「第二章」改写——这条过了，I-10 的点踩复现链路才算闭环。
   - `bg3-lorrokan-act2`：I-17 的 prompt 硬上界生效后，`法术杂货铺/拉玛吉斯之塔/下城区`
     应消失；`Nightsong` 的 L3 命中属词表偏严（用户已知暗夜之歌），可记录为已知偏严
     而非失败，是否放宽词表由读数决定。
5. 用 `scripts/analysis/turns_report.py --check-budget 40000` 确认裁剪上线后
   tokens_in p90 已降到预算内（I-09 验收线：p90 < 40,000，单轮 max < 80,000）。

## P0-2 refetch_wiki.py 解析器两个盲区（Mac 已实测反馈，等修复后开抓）

Mac 侧用代理（`export https_proxy=http://192.168.5.1:7890`，requests 认环境变量）
对候选源做了 `--probe` 探测，结论：

- **terraria zh**：`https://terraria.wiki.gg/zh/api.php` 可用，13 个中文分类名
  （武器物品/盔甲物品/配饰物品/工具物品/物块物品/家具物品/制作材料物品/宠物召唤物品/
  坐骑召唤物品/弹药物品/Boss NPC/敌怪 NPC/NPC）共 3031 页候选；质检仅
  `share_over_20k=0.1` 擦边挂，可接受。
- **silksong**：fandom 探测到 81 页真内容，但 `refetch_wiki.py` 解析器有两个盲区
  导致产物不可用，**修复前不能整站跑**：
  1. `portable-infobox`（fandom 新版 infobox 的 class 名）未被剥离，混进正文；
  2. 跨页共享模板（导航/数据模板）的幽灵文本残留在多个页面里。
- `silksong.wiki.gg` 与 huijiwiki 已判死（不可用源），silksong 只能走 fandom +
  `--suffix "(Silksong)"`。

~~服务器侧动作：修解析器两个盲区~~（**归属已纠正，见下**）+ 采样策略（之前定的
「抽 20 页比对标题 vs 正文对题」要进闸门），改完在 PR/commit 里说明，Mac 侧随即用
代理跑整站抓取并质检。

> **归属澄清（2026-11-22，服务器侧按 `AGENTS.md` 分工纠正）**
>
> 上面那句「服务器侧动作：修解析器」与 `AGENTS.md` 的分工冲突：`scripts/fetch/` 属
> Mac 侧领地，服务器侧**不改实现**。正确分工是服务器侧出问题、数据与测试用例，
> **实现由 Mac 写**。后续同类交接请在 Mac 侧清单里写「谁实现」，别把 `src`/`scripts`
> 的实现句派给服务器侧——服务器 agent 照着写会越界。
>
> 本次越界已经发生：`7ed127b` 是服务器侧直接改的实现（含 14 条解析器契约用例）。
> Mac 可按自己的做法**覆盖重写**，服务器侧不会再就 refetch 解析器提交实现改动；
> 那批用例要不要留由 Mac 定。
>
> **P0-2 归属纠正后**：`tests/test_refetch_wiki.py` 这 14 条契约由服务器侧补，归服务器侧维护；
> 解析器实现本身（`scripts/fetch/refetch_wiki.py`）归 Mac。
>
> ~~已修~~ 实现已存在（`7ed127b`，服务器侧越界产物）：
>
> 1. `portable-infobox`：精确名 + `pi-*` 前缀 + 名里带 `navbox`/`infobox` 的变体一律整块剥，
>    `aside` 整块丢。注意 `wikitable` 不在跳过名单，正文数据表不会被误删（有回归用例）。
> 2. 幽灵文本：`strip_shared_paragraphs()` —— 同一段长文本出现在 >=15% 页（至少 3 页）就删，
>    不靠猜模板名；另加导航链接墙识别（连续短表格行且带数字比例 <20%）。
> 3. 闸门新增两项：`title_in_body_rate >= 0.85`（抽 20 页看标题词是否在正文开头）、
>    `shared_para_rate <= 0.02`（清理后残留重复段）。
> 4. `--no-strip` / `--strip-ratio` 供误删排查时对照。
>
> 验证方式：服务器连不上 fandom（403/无路由），只有 `tests/test_refetch_wiki.py` 14 条
> 合成 HTML 契约（全绿，pytest 238 passed）。**真实页面未验**，若 Mac 保留这批实现，
> 请先用代理跑 `--probe` 看 `title_in_body_rate` / `shared_para_rate` /
> `nav_pollution_median` 三个读数，再抖 20 页人眼比对。probe 只抓 10 页时共享段阀值噪声大
> （min 3 页即算模板），以正式抓+抽样复查为准。

> **Mac 实测回执（2026-11-23）**
>
> 1. **silksong 不带 `--suffix` 全绿**（81 页分类候选，suffix 过滤会错杀到 5 页——
>    分类本身已是 Silksong 专属，不需要后缀）：probe 9/10 页成功，
>    `title_in_body=1.0 / shared_para=0 / nav_pollution=0`，闸门整体通过，
>    抽看正文（Ballow/Bell Beast/Blasted Steps）对题。
> 2. **新盲区已修（Mac 侧，归属纠正后由 Mac 改实现）**：Fandom 渲染层注入的
>    `dialogue-*` div（随机对话模块，wikitext 里不存在——`Hornet (Silksong)` 的
>    wikitext 0 处 Zote，渲染 HTML 里 57 处）。已把 `dialogue-` 加进
>    `SKIP_CLASS_PREFIXES`。
> 3. **`title_in_body_rate` 对 zh 站结构性失效**：terraria.wiki.gg/zh 标题是英文、
>    正文是中文（如 `1 Second Timer` → 正文全中文计时器内容），标题词永远匹配不上，
>    实测 0.1 必挂。zh 模式下该检查已降为仅供参考（不参与 ok 判定），对错题防线由
>    `cjk_ratio` + 抽样人眼比对承担。服务器的 14 条契约测试不受影响（全绿）。
> 4. **terraria zh 直连可通，代理反而不通**：`terraria.wiki.gg/zh` 直连 1.8s，
>    挂代理 20s 超时（000）。fandom 仍需要代理。zh 分类已固化进注册表
>    `wiki_zh_categories`（13 个，3031 页），`--lang zh` 不再落 allpages。
> 5. terraria zh probe 其余闸门全绿：`median=1242 / cjk=0.56 / shared_para=0`。
>
> 两个整站抓取已在 Mac 后台跑（silksong 81 页、terraria zh 3031 页），
> 完成后抽 20 页人眼比对，产物写 `wiki_data.refetch.md`（不动正式文件）。

## ~~P1-1 turn_record 观测字段~~ ✅ 已完成（服务器侧 `9b801b1`，2026-11-22）

`turn_record` 已透传 `progress_level`/`progress_source`/`entry_acts` 与裁剪三字段
（history_raw_msgs/history_used_msgs/history_trim_chars）；`graph.py`/`multi_agent.py`
的 meta 也补了进度对象。I-22（pytest 写脏生产 turns）同 commit 根治：
`GAMEGUIDE_*_DIR` 可 env 覆盖，conftest autouse 指向 tmp。

## P1-2 I-19 `max_turns=6` 观察 3 天再定（不要现在调参数）

`history_trim` 已上线（`f9eaabb`）。服务器侧复核认为 6 轮对短消息会话偏激进
（线上 `history_msgs` p50=31），建议 10–12 或字符远低于预算时不按轮数裁。
**已定的口径：裁完头 3 天看 tokens_in 与点踩率再动**，数据在服务器侧，由服务器给结论
（约 11-25 前后）。Mac 侧负责在拿到结论后调参。

## P1-3 数据清洗稳定后给 Mac 重建信号

全量重建 7 库的前提是你们那边 wiki 数据清洗迭代稳定（清洗脚本还在改）。
稳定后通知 Mac：Mac 跑 `mac_prepare_build.sh --game all` 重建 +
`vectorstores.sh` 上传，服务器负责接收后重启验收。**重建与代码上线必须同一次**
（严格模式下旧库零命中的教训，见 AGENTS.md「读端不做兜底」一节）。

## ~~P2-1 方案 B 会话记忆~~ ✅ 已落地（服务器侧 I-23，`04afd5f`/`e2e1f65`，2026-11-22）

用户 2026-11-22 已给留存口径「也许 3 天」，服务器侧按 `docs/SESSION_STORE_IMPL.md`
完成了 S0–S3：`src/rag_agent/store.py` 三表、`resolve_session()`（client_id +
session_id）、`save_turn()` 双写、留存清理（答案 3 天 / 存根与会话 90 天，
`GAMEGUIDE_RETENTION_*` 可改）、`/api/sessions` 系列路由、前端 localStorage 恢复。
**上线顺序**：`GAMEGUIDE_SERVER_HISTORY` 默认 `0`（仍用请求体 history），双写对账
1–2 天后服务器侧再翻 `1`。Mac 侧后续：翻 `1` 后 `multi_agent`/`graph` 的 history
语义从「前端全量重发」变为「服务端装配」，届时配合改。

## P2-2 golden set 扩充（依赖方案 B）

评测 golden set 要扩到 30–50 条，数据源是 `data/feedback/*.jsonl` 的真实点踩/点赞轮。
方案 B 落地后由服务器侧从 feedback 导出候选，Mac 侧转成 `behavior_cases.jsonl` 用例。

## 需用户本人跑（服务器无法执行）

本机 curl 被 SSRF 守卫拦，HTTP 层验证需用户跑：

```bash
curl -s http://localhost:8765/api/status
curl -s -X POST http://localhost:8765/api/ask -H 'Content-Type: application/json' \
  -d '{"question":"BG3 山丘巨人的铁手套在哪拿"}'
```

验收点：`version` 为最新提交；铁手套回答带「第三章/希望之邸（House of Hope）」且
**不再出现「第二章」**；影心类问题落到 Shadowheart 条目。

## 已完成（勿重做）

- SPOILER_TERMS 补 act3 地名（`87568ec`）+ 12 条契约测试。
- 数据看板设计 `docs/DASHBOARD_PLAN.md`（服务器侧自己的活，Mac 无动作项）。
- 防剧透数据分析与问题防护（ISSUES_2026-11-22 全链路）。

---

## 服务器侧执行回执（2026-11-22 13:30，对应 P0-1）

拉取到 `f8ffb83`（含 `f55ea5f`），重启 PID 1631717。

| 项 | 结果 |
|---|---|
| pytest | **224 passed / 20 skipped / 1 xfailed**（4.79s）与 Mac 本地一致 |
| routing | **13/13**，0 失败 |
| full `bg3-hill-giant-gauntlets` | 路由正确；内容层 `命中禁用词 ['第三章']` —— 是用例禁词与正确性矛盾，已记 **I-21**（需 Mac 改用例） |
| full `bg3-lorroakan-act2` | **citation 噪音消失**，只剩 `Nightsong L3` 一条（I-16 生效；L3 属词表偏严，维持 KNOWN） |
| I-02 中文名 | 两轮均落在 baldurs_gate3，路由层已确认（内容层被 I-21 挡住） |

预算（`--check-budget 40000`）：全量 p90 = 150,306 **超线**，但那是裁剪上线
（`f9eaabb` 01:34）前的历史数据。**当日 18 轮裁剪后真实流量重新统计（2026-11-22 15:40）：**

| 区间 | n | tokens_in p50 | p90 | max | 均值 | 耗时 p50/p90 | history_msgs max |
|---|---|---|---|---|---|---|---|
| 裁剪前（00:00–01:34） | 11 | 79,016 | 210,653 | 290,355 | 103,262 | 16.0 / 38.6s | 87 |
| 重启后（13:01–22:58） | 34 | 17,367 | 36,888 | 44,527 | 18,352 | 15.3 / 25.3s | 33 |

均值 −82%、p90 −82.5%、max −85%，耗时反而变好（p90 38.6s → 25.3s），
工具调用均值 6.2 → 4.4。**p90 = 36,888 已在 40,000 预算线以下**（15:40 那次
读到的 40,756 是 18 轮样本且恰有一轮 40.8k，样本扩到 34 轮后回落，不构成超线）。
单轮基线仍在 20k 上下（系统提示词 + 约 4.4 次工具返回），裁剪治的是「越聊越长」。
按既定口径续观到 11-25，I-19 的 6→10~12 轮参数暂不动。

当日 46 个真实轮里 27 轮有剧透命中：L1 40、L2 7、L3 零。高频词 下城区 14、
希望之邸/House of Hope 各 8、巴尔 5——用户当晚确实在下城区推进剧情（22:17
「去哪找巴尔神殿，已经拿到了护符」），这类命中属已授权，不算越界。

另：`oni / 高压制氧` 那 8 条 `in=1 hist=0` **不是菜单轮，是 pytest 测试请求写进了生产
turns**（`ip=127.0.0.1`、`elapsed=0`、`chunks=1`、答案固定「氧气」），已记 **I-22**
并已于 `04afd5f` 根治（conftest autouse fixture 把 TURNS_DIR/FEEDBACK_DIR/SESSION_DB
全指到 tmp；本次跑完全量 pytest 后 turns 行数不变，已实证）。
`scripts/analysis/report.load_turns()` 默认就剔除本地 IP，所以官方统计口径
（turns_report / 看板 / `/api/stats`）从未被污染，只有手写直读 jsonl 的临时脚本会踩到。

### 等 Mac 侧动作

- **I-21**：`bg3-hill-giant-gauntlets` 去掉「第三章」禁词（或 max_level 提到 2）。
- **P0-2 已修**（`7ed127b`）：先 `--probe` 看三个闸门读数，再整站抓 + 抽 20 页人眼比对；
  发现误删晃 `--no-strip` 对照。

### 2026-11-27 KCD2 / BFV 部署就绪（Mac 侧已完成）

- 代码已推送（`bf6c2c7`），两个游戏的向量库已 scp 到服务器
  （kcd2 13.8MB、battlefield5 11.5MB，games/<game>/vectorstore/）。
- 待服务器端动作：`git pull` + `bash scripts/deploy/api.sh` 重启。
- 重启后抽查（端到端，应命中具体条目且带来源，不应出现「知识库暂时不可用」）：
  - KCD2：「库滕堡在哪」「亨利是谁」「伤害最高的长剑」
  - BFV：「阿拉斯地图怎么打」「STG44 怎么样」
- 注：KCD2 的 `kcd2_glossary.jsonl` 在 `games/kcd2/data/` 下，已随代码推送，
  查询时扩展生效，无需额外部署。

#### 服务器端已完成（2026-11-27 19:47，`beta6-185-g315dfa0`）

- `git pull`（bf6c2c7 → 91617a0）+ `api.sh` 重启成功，两个向量库就位
  （kcd2 13.8MB / battlefield5 11.5MB，mtime 19:44）。
- 抽查 5 问全部命中知识库、无「知识库暂时不可用」，落库游戏正确：
  - 「库滕堡在哪」→ game=kcd2，库滕堡词条（含意大利区、铸币厂、旅店）
  - 「亨利是谁」→ game=kcd2，斯卡里茨的亨利身世链
  - 「阿拉斯地图怎么打」→ game=battlefield5，地形 + 各模式要点
  - 「STG44 怎么样」→ game=battlefield5，600 RPM / 4 发低后坐 / 专精建议
  - 「伤害最高的长剑」→ **落菜单**（新会话无游戏信号、detect_game 不臆断，属既有设计不是回归）；
    补问「天国拯救2 伤害最高的长剑」→ game=kcd2，战争之舌长剑斩击 170 / 重铸的亨利的剑刺击最高
- 基线：`pytest tests/ -q` 433 passed / 23 skipped / 1 xfailed。

---

## P0-3 I-31 title_pin 上线 + KCD2 quests 新 DB（Mac 侧 2026-11-27 完成，`f1020a4`）

Mac 侧完成并推送（`618166c` + `f1020a4`，本地 467 passed / 24 skipped / 1 xfailed）：

- **I-31 title_pin 词面补位**（`f1020a4`，`src/rag_agent/tools.py`）：`search_knowledge_base`
  新增——扫库内 title，query 词面命中的块补进候选并直通置顶，解决中英混合库小语种块
  全局排名低、fetch_k 截断不可达（HK 螳螂爪块排 532/1960）。护栏全部带齐：排除游戏名/
  别名、去重键 `(title, 正文)`、同 title 只补 1 条、单字 title 跳过、cap 4。
  Mac 本地冒烟：**螳螂爪怎么获得 MISS → PASS**，16/18。
- **KCD2 quests 新 schema**（`618166c`）：8 列（prev_quest/next_quest 等），主线链
  Last Rites→…→For Victory! 双向一致。DB 随 git 走，pull 即生效（查询每次新建连接），
  但建议和重启同批做。
- **I-28 数据面**（HK/ONI/VA11 中文 wiki）用户拍板**先不做**；**I-29** silksong 扩容
  **blocked**（fandom 天花板 ~107 成员、wiki.gg 全站 401/403）。均记
  `docs/ISSUES_2026-11-25.md`。

**服务器侧动作**：

1. `git pull` → `bash scripts/deploy/api.sh` 重启（title_pin 在检索链路里，必须重启才生效）。
2. `.venv/bin/python -m pytest tests -q`（基线 467 passed / 24 skipped / 1 xfailed）。
3. 冒烟 `FASTEMBED_CACHE_PATH=$PWD/.cache/fastembed .venv/bin/python scripts/eval/retrieval_smoke.py`：
   基线应从 10/12 转 **11/12**（螳螂爪转 PASS；剩 Silk Soar 一条为 I-29 数据缺口，
   属已知 blocked，不算失败）。
4. 端到端抽查 KCD2 关系链：「Laboratores 之后做什么任务」应给出 Wedding Crashers
   后续链（query_structured_data 关系分支 `a3b182a` + 新 DB 生效的标志）。
5. title_pin 代价：全量扫 title，BG3 66k 块实测增量 ~20ms，观察 `/api/ask/stream`
   耗时不应有可感变化；若 turns 里 elapsed p90 明显上抬请回告 Mac。
