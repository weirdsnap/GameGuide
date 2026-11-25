# Mac 端待办清单（交接给 Mac 侧 agent）

> 来源：服务器端线上数据与部署体检，2026-11-12。
>
> **2026-11-22 状态快照**：新一批**内容层**问题见
> [`docs/ISSUES_2026-11-22.md`](ISSUES_2026-11-22.md)：I-09 上下文裁剪仍未落地（单轮已到 29 万 tokens）、
> I-10 检索条目不带 act 导致章节被改写（两轮点踩）、I-11 首答自信追问才纠正、I-12 洛若坎线 L3 越界。
> 对应 L0/L2 用例已入库（`tests/test_history_trim.py` + 3 条 `behavior_cases.jsonl`），
> 待办条目见 `EVAL_SYSTEM.md` 的 TODO 4/5/6。
>
> **2026-11-16 状态快照**：未解决问题已统一记录到
> [`docs/CURRENT_ISSUES_2026-11-16.md`](CURRENT_ISSUES_2026-11-16.md)。
> 本文件的完成状态：P0-1 剧透提示词 ✅、P1-1 信号词 ✅、P1-2 内存上限 ✅（maxsize=3）、
> P2-1 术语表接线 ✅；P0-2 BG3 v3 本地已建、待服务器部署；P0-3 silksong 仍阻塞（网络）。
> 分工依据 AGENTS.md：`src/rag_agent/`、`tests/`、`scripts/tool|build|fetch|enrich|eval` 以 Mac 侧为主。
> 服务器侧当天已做的不要重做，见文末「服务器侧已完成」。

## P3 流式体验（2026-11-25 新交接，方案见 `docs/STREAM_UX_PLAN.md`）

线上已经在咬人：11-25 11:56 一轮 iPhone 移动网络提问，服务端工具循环跑 25.6s、
7 次 DeepSeek 全 200，却**一个字节没写给前端**，客户端先断 → 前端弹「网络错误」。

- **P3-1 `progress` 事件【两端已接线 f2fb79f + api_server 转发，2026-11-25】**：`src/rag_agent/multi_agent.py:ask_stream`
  在 `mode == "messages"` 分支识别工具阶段——`AIMessageChunk.tool_call_chunks` 拼出完整 name →
  `yield ("progress", {phase:"tool_start", tool, round, elapsed})`；`ToolMessage` → `phase:"tool_done"`。
  只发工具 id，中文文案在前端映射；ToolMessage 内容绝不转发；首 token 之后停发。
  注意：前端**不做**假计时器（用户 11-25 否掉），没这个事件前端就只显示「思考中…」。
  ~~服务器侧剩余：`api_server.handle_ask_stream` 要加 `progress` 分支转发~~
  **已补**（2026-11-25）：`handle_ask_stream` 加了 `progress` 分支，测试
  `tests/test_api_handlers.py::test_ask_stream_progress_事件透传给前端` 覆盖，
  需重启 API 才生效。详见 `ONLINE_DIAGNOSIS_2026-11-25.md` §10.1。
- **P3-2 断流恢复 + 取消**：已有半截答案时保留正文 + 灰字「（连接中断，回答可能不完整）」+ 重试；
  零 token 时显示「连接中断了 · 点这里重试」；加 `AbortController` 取消按钮与
  `visibilitychange` 回前台兜底。

## P0-1 剧透控制（提示词 + 数据）

方案全文在 `docs/SPOILER_CONTROL_PLAN.md`，其中**第三节「用户进度：怎么来、怎么存、
在哪一层生效」是最新补充的骨架**（进度对象、单调规则、未知进度不问、L1–L3 分工），
动手前先读那一节。这里只列要动手的点：

- `game_registry.py` 每款游戏加 `spoiler_axis` 字段（act / chapter / ability / none），
  BG3 = act；`game_router.build_common_rules()` 的「剧透管理」改为按该字段生成，
  不再靠「例如 VA-11 Hall-A、CP2077」这种举例。
- 规则补两条硬约束：① 用户报过进度后，进度构成**硬上界**，不得越过、也不得重复追问；
  ② 用户明说「别剧透后面」时，作答前必须复述边界。
- BG3 `extra_prompt` 补三幕结构与进度锚点（第一章地表/幽暗地域 → 第二章最后之光旅店/月出之塔
  → 第三章下城区）。
- 验收：以「我卡在最后之光旅店」为前提提问，回答不出现 `石匠工会 / Mason's Guild /
  大陵墓 / 月出之塔`。服务器侧 `spoiler_hits` 已在记录，可拿真实流量验证。

## P0-2 BG3 向量库重建（language + act）

- 现状：服务器上这份是 10-41 建的老库，**没有 language 元数据**。服务器侧临时做了补丁
  （原地给 66698 条补 `language=en`，备份 `index.pkl.bak-20261112`），只为不让严格模式把
  BG3 检索打到零命中。你重建后直接覆盖，记得删掉那个 `.bak`。
- 重建顺带把 `act` 写进 metadata：结构化 `locations`(249 行) / `quests`(152 行) 都有 `act` 列；
  wiki 条目可从 `Act_One_*` / `Act_Two_*` / `Act_Three_*` 分类解析，解析不出的**不写**该字段。
- 检索侧配合：`tools.search_knowledge_base()` 加可选 `max_act`，缺 `act` 字段的文档默认放行
  （建议把 act 预先拆成 `acts: ["1","2"]` 做成员匹配，FAISS 的 dict filter 只支持等值）。
- 验收：Act 2 进度下检索不返回 `act=3` 的 quest/location 文档；
  `tests/test_mac_build_contract.py` + `mac_prepare_build.sh` 第 5 步通过。

## P0-3 silksong 向量库重建（现有库是坏的）

- 证据（服务器实测）：196 个不同 `title`，却有 **47,155** 个块（平均 240 块/条目，切分异常）；
  内容混着空洞骑士的 Boss（Grimm、Radiance、Mantis Lords、Hornet、Lost Kin、
  Watcher Knight、Grey Prince Zote 等），就是历史上「149/196 页是空洞骑士」那个问题的残留。
- 代价：109MB 磁盘，一旦被问到约 600MB 常驻内存。
- **新增证据（2026-11-15）：连 `wiki_data.md` 本身都是垃圾，不只是分类混了。**
  文件 29MB / 196 页 = **平均 150KB/页**，正常 wiki 页面 5–20KB。抽样看：
  `# Baldur Shell` 一节正文写的是 Zote 的来历，`# Absolute Radiance` 一节开头是
  空洞骑士 beta 版的发售信息。说明旧版采集抓到的不是页面正文，而是整站导航/
  搜索片段拼成的文本块——这种产物无论怎么过滤分类都救不回来，必须重抓。
  所以重抓后**不能只看页数**，要抽 20 页比对「标题 vs 正文是否对题」。
- 采集网络：Mac 与服务器**都**直连 fandom.com 超时。服务器实测：DNS 只返回 AAAA
  （`2a03:2880:...`），无 IPv6 路由；`fandom.com`、`r.jina.ai`、`zh.wikipedia.org`
  全部不可达，本地无代理端口。只能靠出墙（Mac 挂机场代理跑脚本，或给服务器配一个
  代理节点仅供采集用）。这一步卡在网络上，等用户决定。

## P0-4 分页器把页内小节当成了独立页面（terraria / silksong / mhw 三个库）—— 分页已修，待重建

> 完整证据、检索实测与验收口径见 `docs/ISSUES_2026-11-25.md` **I-27**（本次补齐，
> 含 mhw 整库塌成一页的实测：1,863 块 title 全是 `MHWilds`）。这里只留结论。

- **发现（2026-11-25，两个库刚上传后实查）**：terraria 新库 31,248 块里
  **48.1%** 的 title 是「制作 / 历史 / 花絮 / 备注 / 小贴士 / 另见」这类页内小节名
  （制作 4322、历史 4255、花絮 1979、备注 1814、小贴士 1388…），
  7,347 个不同 title 对应只有 3,031 个页面。
- **现场症状**：用真实检索路径查「克苏鲁之眼怎么召唤」，top-3 是
  `Demon's Eye` / `Eye of the Golem` / `Face Monster`，后面跟着「部位」「另见」，
  **页面本身（title 确实是「克苏鲁之眼」）没进 top-k**；
  「泰拉刀怎么获得」返回 `Huntress's Buckler` / `Digger` / 「另见」。
  同质碎片块在向量空间里互相聚集，把正文块挤掉了。
- **根因**：`refetch_wiki.py` 的输出里页面之间用 `---` 分隔（`grep -c '^---$'` 正好等于
  总页数：terraria 3031、silksong 79），页内小节也用 `##`。而建库分页器只按 `## ` 切
  （`mac_build.py` 格式 B、`ingest_game.py` 格式 2），页面边界信息被丢掉了。
- **修法**：分页前先按 `(?m)^---\s*$` 切页，页内再按 `##` 切小节；
  **小节块的 title 必须继承页面名**，小节名另存 `section` 元数据。
  两个文件都要改（两条建库入口，别只改一条）。
- **契约测试已就位**：`tests/test_wiki_page_split.py`，其中
  `test_refetch_格式按页面分页而不是按小节分页` 当前是 **xfail**——这就是问题存在的证明；
  修好后会 XPASS，届时删掉该文件的 xfail 标记。
- **重建范围**：terraria、silksong 两个库（其余游戏的源数据不是 refetch 格式，不受影响）。
  重建后抽检口径：`## 克苏鲁之眼` 应能作为单一页面命中，块数应接近页数 × 2–4 而不是 ×10。

## P1-1 信号词缺口

- 证据：`我卡在最后之光旅店，附近的塞伦捏秘密任务怎么做？` 不加游戏名时路由到菜单
  （`route=menu`），因为 `最后之光旅店` / `塞伦捏` / `石匠工会` 不在 BG3 的 `signals` 里。
  有 history 的真实会话不受影响，刷新后的新会话必踩。
- 注意：`graph.py` 的菜单文案与 `_is_unknown_game_query` 的 known_keywords 仍是硬编码，
  与 `game_registry` 双份维护，改 signals 时容易漏一边——建议顺手收敛为从注册表派生。

## P1-2 内存加载策略（跨端，需你定）

- 现状：`_load_vectorstore` 是 `lru_cache(maxsize=8)`，没有内存上限。BG3 单库常驻约 860MB
  （实测进程 RSS 991MB），8 库全驻留估 2.2–2.5GB；服务器 3.7GB 且 swap 已用 1.3GB。
- 选项：① 把 maxsize 降到 3；② 按需加载 + 空闲释放（定时清 cache）；
  ③ 保持现状，接受「多游戏并发会把机器压到 swap」。
- 这条建议先在 Mac 上测一下单库真实驻留量再定。

## P2-1 术语表接线

- `glossary.expand_query()` 目前只有 `scripts/eval/glossary_probe.py` 在调，
  `tools.py` 仍用原始 query。已知：影心/明斯克改善明显；至圣斩（conf 0.5）会引入噪声；
  养鸡妹无覆盖。
- 接线前用探针 + golden QA 做 A/B，阈值先按 0.7 硬线。

## P2-2 进度轴重构（2026-11-22 新交接，服务器侧已出设计与草案）

方案全文：[`docs/PROGRESS_AXIS_DESIGN.md`](PROGRESS_AXIS_DESIGN.md)。
数据结构草案与校对说明：[`docs/progress_drafts/`](progress_drafts/)，
契约测试 `tests/test_progress_model_schema.py`（已入库，当前 6 passed）。

按依赖顺序：

1. **S1 拆配置**：`src/rag_agent/progress.py` 的 `_EXPLICIT_PATTERNS` / `_ACT_ANCHORS`
   搬进 `config/progress/baldurs_gate3.chain.json`，加 loader；行为必须与现状等价
   （BG3 推断结果一条不变，用现有 `tests/` 做回归）。
2. **S2 加 DagModel**：新增 `progress/models.py`（ChainModel / DagModel / NullModel，
   共一个 Protocol）与 `progress/evidence.py`（证据抽取，含 **reverse 证据**：
   「怎么去 X」→ 记 X 未到达）。丝之歌先吃 `reviewed:false` 的数据，只抬下界、不剪枝。
3. **S3 问句护栏**：`progress/guard.py`，两条硬约束——
   ① 追问里出现的专名 ⊆ 用户历史里出现过的专名（白名单只放游戏名/通用词）；
   ② **问句模板不允许角色/剧情实体槽位**，只有 rank / percent / area_known /
   ability_known 四个槽位。这条比事后扫词可靠：扫词挡不住新角色，槽位限制是结构性的。
4. **硬约束**：任何 `if game == "..."` 都算违规；游戏差异只能加数据字段。

背景：丝之歌现在 `spoiler_axis=act` 但零锚点、chunk 无 act 元数据、`abilities` 表
2 行且全空，实际是「零防线」，证据见设计文档 §1.1。

## 服务器侧当日已完成（勿重复实现）

- `tools.py`：撤掉「两路都空 → 退回整库」的兜底，改严格模式（理由已写进 AGENTS.md）。
- BG3 库临时补 `language=en`（原地改 pkl，未重算向量），重启后严格路径全部命中。
- `api_server.py`：turn 记录加 `answer_head` / `spoiler_hits`，双写 `data/turns/YYYY-MM-DD.jsonl`；
  流式加整体超时（`stream_timeout`，默认 240s）；菜单/未识别/失败轮退回额度；
  `/api/status` 的 version 改为 `git describe`；新增 `GET /api/stats?days=N` 聚合。
- `scripts/analysis/turns_report.py`：离线统计（`--days` / `--game` / `--spoilers`）。
- `scripts/deploy/api.sh`：关停逻辑改为按端口找 PID（不再 `pgrep -f api_server.py` 误杀），
  启动前把超过 5MB 的旧日志转 `.prev`。
