# Mac 端待办清单（交接给 Mac 侧 agent）

> 来源：服务器端线上数据与部署体检，2026-11-12。
> 分工依据 AGENTS.md：`src/rag_agent/`、`tests/`、`scripts/tool|build|fetch|enrich|eval` 以 Mac 侧为主。
> 服务器侧当天已做的不要重做，见文末「服务器侧已完成」。

## P0-1 剧透控制（提示词 + 数据）

方案全文在 `docs/SPOILER_CONTROL_PLAN.md`，这里只列要动手的点：

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
- 要做：先从 `scripts/fetch/silksong.py` 重新采（确认分类只取 Silksong），再
  `mac_prepare_build.sh --game silksong`，建完检查 title 数与内容归属。
- 验收：随机抽 20 条 title 全部属丝之歌；块数与该游戏 wiki 规模相称（不应是 4 万级）。

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

## 服务器侧当日已完成（勿重复实现）

- `tools.py`：撤掉「两路都空 → 退回整库」的兜底，改严格模式（理由已写进 AGENTS.md）。
- BG3 库临时补 `language=en`（原地改 pkl，未重算向量），重启后严格路径全部命中。
- `api_server.py`：turn 记录加 `answer_head` / `spoiler_hits`，双写 `data/turns/YYYY-MM-DD.jsonl`；
  流式加整体超时（`stream_timeout`，默认 240s）；菜单/未识别/失败轮退回额度；
  `/api/status` 的 version 改为 `git describe`；新增 `GET /api/stats?days=N` 聚合。
- `scripts/analysis/turns_report.py`：离线统计（`--days` / `--game` / `--spoilers`）。
- `scripts/deploy/api.sh`：关停逻辑改为按端口找 PID（不再 `pgrep -f api_server.py` 误杀），
  启动前把超过 5MB 的旧日志转 `.prev`。
