# GameGuide RAG Agent — 给 AI 编码助手的项目指南

> 本文件面向不了解本项目的 AI 编码 agent。项目注释与文档主要使用中文，请保持一致。

## 项目概述

多游戏 RAG（检索增强生成）问答 Agent，基于 LangChain + LangGraph + FAISS + SQLite。
用户用中文提问某款游戏的攻略问题，系统识别游戏后，通过「向量检索 + 结构化数据库」
双通道检索知识并生成回答。

支持的游戏（内部键名 → 显示名）：

| 键名 | 游戏 |
|---|---|
| `hollow_knight` | 空洞骑士 (Hollow Knight) |
| `silksong` | 丝之歌 (Hollow Knight Silksong) |
| `oni` | 缺氧 (Oxygen Not Included) |
| `terraria` | 泰拉瑞亚 (Terraria) |
| `mhw` | 怪物猎人荒野 (Monster Hunter Wilds) |
| `cyberpunk2077` | 赛博朋克2077 (Cyberpunk 2077) |
| `va11halla` | 赛博朋克酒保行动 (VA-11 Hall-A) |
| `baldurs_gate3` | 博德之门3 (Baldur's Gate 3，注册在路由中但数据不全) |

运行时架构：

```
用户问题 → game_router（信号词匹配，识别游戏 + 置信度）
        → LangGraph ReAct Agent（create_react_agent，LLM 自动选工具）
        → 三个工具：
            search_knowledge_base   — FAISS 向量检索（剧情/攻略/描述类）
            query_structured_data   — SQLite 结构化查询（HP/费用/数值类）
            show_database_schema    — 查看游戏 DB 的表结构
```

服务形态：`api_server.py`（**aiohttp**，不是 FastAPI——README 的描述过时了）。
接口**无密码**：全局每日额度（默认 100 问/天，`data/quota.json` 持久化）
+ 按真实客户端 IP 限速（默认 20 次/分、600 次/天），由 Caddy 反代暴露到公网
（`https://api.weirdsnap.top` → `localhost:8765`，见 `Caddyfile`）。

## 技术栈

- **语言**: Python 3.13（`.venv/` 为服务器/开发虚拟环境；仓库里还有旧的 `venv/`，勿用）
- **Agent 框架**: LangChain + LangGraph（`langgraph.prebuilt.create_react_agent`）
- **LLM**: DeepSeek（OpenAI 兼容 API，经 `langchain_openai.ChatOpenAI` 调用），
  模型名由 `CHAT_MODEL` 环境变量控制（默认 `deepseek-v4-flash`）
- **Embedding**: fastembed 本地推理，模型
  `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`（384 维，多语言）
- **向量库**: FAISS（`langchain_community.vectorstores.FAISS`），每游戏一个目录
- **结构化数据**: SQLite，每游戏一个 `.db` 文件
- **Web 服务**: aiohttp（已在 `requirements.txt`）；测试用 pytest（`--run-e2e` 才调真实模型）

## 代码组织

```
api_server.py            # aiohttp API 入口（/api/ask /api/ask/stream /api/quota /api/status；/api/login 仅兼容占位）
api_config.json          # 速率/额度/超时配置（被 .gitignore 忽略）
src/rag_agent/           # 核心包（sys.path 插入 src/ 后按 rag_agent.xxx 导入，无 pyproject.toml）
  ├── config.py          # 环境变量 / LLM_CONFIG / FASTEMBED_MODEL / 路径常量
  ├── game_registry.py   # 游戏配置单源：路径、信号词、菜单、Wiki 配置，脚本都从这里派生
  ├── game_router.py     # detect_game() 信号词识别 + prompt 构建
  ├── graph.py           # LangGraph：detect_game 节点 + ReAct Agent + 菜单/未知游戏回退
  ├── multi_agent.py     # 对外薄壳 ask()/ask_stream()，多轮游戏上下文从 history 推导
  ├── tools.py           # 三个 @tool：FAISS 检索 / SQLite 查询 / schema
  │                      # （检索带 language filter + 术语表 query 扩展）
  ├── glossary.py        # 中英专名术语表 + query 扩展（已接入 tools.search_knowledge_base）
  ├── vectorstore.py     # FAISS 构建/加载（lru_cache）、文档切分、wiki 文档解析、
  │                      # prepend_title_to_chunks（标题前置，建库契约）
  ├── data_converter.py  # HallownestAPI JSON → 文本块（空洞骑士专用，旧管线）
  └── hk_db.py           # 空洞骑士 SQLite 构建模块
games/<game>/            # 每游戏数据目录：
  ├── data/wiki_data.md      # 英文 Wiki 正文（构建向量库的输入）
  ├── data/wiki_data_zh.md   # 中文 Wiki（仅 hollow_knight/oni/terraria/va11halla）
  ├── data/<game>_glossary.jsonl  # 中英术语表（目前只有 baldurs_gate3）
  ├── <game>_data.db         # SQLite 结构化库（空洞骑士是 hk_data.db，例外）
  └── vectorstore/           # FAISS 索引（index.faiss，被 .gitignore 忽略，服务器上需部署）
scripts/
  ├── fetch/    # Wiki 采集（wiki.py 通用、silksong.py、bg3_wiki.py）
  ├── build/    # SQLite 构建（build_game_db.py 通用 + 各游戏专用脚本）
  ├── enrich/   # 数据补全（mhw.py、oni.py、terraria.py）；build_glossary.py 术语表
  ├── tool/     # ingest_game.py（中英 wiki 向量库）、mac_build.py（额外并入结构化 DB）、
  │             # mac_prepare_build.sh（Mac 一键建库 + 真实检索路径自检）
  ├── deploy/   # api.sh（服务器重启 API）、vectorstores.sh（scp 向量库到服务器）
  ├── eval/     # ragas_eval.py（Tier 2）、check_imports.py、glossary_probe.py、golden/
  ├── analysis/ # turns_report.py 等用户日志分析脚本（服务器侧领地）
  └── archive/  # 已归档旧脚本，勿修改、勿引用
tests/                   # pytest 离线套件（见下），conftest 排除 test_light.py
docs/                    # REFACTOR_LANGGRAPH.md（历史方案）、AI_APPLICATION_ENGINEERING_PLAN.md、
                         # SPOILER_CONTROL_PLAN.md（防剧透分层方案，P0 提示词层已实现）
data/                    # 空洞骑士早期管线遗留数据；quota.json 额度文件
evaluation/reports/      # ragas 评估输出（.gitignore，运行产物）
```

README 的结构说明滞后于代码，如有出入以本文件和实际代码为准。

## 环境配置

- 复制 `.env.example` 为 `.env`，填入 `OPENAI_API_KEY`（DeepSeek 的 key）和
  `OPENAI_BASE_URL`（DeepSeek 或其他 OpenAI 兼容端点）。
- `.env` 里 HuggingFace 相关：`HF_ENDPOINT=https://hf-mirror.com`（国内镜像）、
  `HF_HUB_DISABLE_XET=1`、模型已缓存后设 `HF_HUB_OFFLINE=1` 跳过联网检查
  （首次下载模型时必须关掉）。
- 依赖安装：`pip install -r requirements.txt`（服务器/开发全量）；
  Mac 本地只做向量库构建时用 `requirements-mac.txt`（最小集）。

## 运行环境分工：Mac 端 vs 服务器端

项目长期跑在两台机器上，职责必须分清楚，改流程前先对号入座：

### Mac 端（开发/构建机，本仓库的工作机）

- **职责**：写代码、跑测试、Wiki 采集（`scripts/fetch/`）、SQLite 构建（`scripts/build/`）、
  **向量库构建**、ragas 深度评估、golden set 维护。
- **向量库只能在 Mac 建**：fastembed 批量 embedding + FAISS 构建内存需求大，
  服务器 2C/3.6GB 根本跑不动。一键入口 `scripts/tool/mac_prepare_build.sh`
  （pull → 依赖/数据自检 → 建库 → 走真实 `tools.search_knowledge_base` 路径自检）。
- embedding 模型缓存在默认位置（`~/.cache/fastembed`），重启不丢。
- 只做建库时可装最小依赖 `requirements-mac.txt`。

### 服务器端（线上运行机，`/data/learning/agent`）

- **职责边界**：服务器端由**独立的服务器 agent 负责**（重启、线上验证、Caddy、日志分析等），
  Mac 侧 agent **不要直接 ssh 操作服务器**；Mac 侧只需把向量库构建好、自检通过，
  上传（`vectorstores.sh`）和后续重启/验收交给服务器 agent 或人工执行。
- **防冲突纪律**（两端都有 agent 在改同一仓库，务必遵守）：
  **开工前必拉：`git status -sb` → `git fetch --prune` → `git pull --ff-only`（用户要求，每次无例外）；
  push 前再拉一次**——本地改完先 `git fetch` 看 origin 是否又前进了，有就先把
  `git pull --rebase`（或按双方约定合并）拉下来再推，别拿旧基准直接 push。
  这样即使是交叉改同一个文件（tools.py / AGENTS.md 都出过）也能当场对齐，
  而不是推上去才发现冲突。
  改动小步提交、及时推送，别攒大 diff；
  服务器 agent 的领地：部署/配置（`api.sh`、`api_config.json`、`Caddyfile`）
  **以及用户数据分析/存储功能**（`api_server.py` 的日志/turns 落库、
  日志分析脚本——它离数据最近，适合它写）；
  核心 RAG 代码（`src/rag_agent/`、`tests/`、`scripts/tool|build|fetch|enrich|eval`）
  以 Mac 侧为主；
  遇到冲突按「谁的功能谁负责」分拣合并，别无脑 `--theirs`。
- 服务器职责：跑 `api_server.py` 线上服务 + `git pull` 同步代码。
  **可以跑日志分析这类轻量脚本**（grep/Python 解析 turns 日志、统计聚合），
  红线只有一条：**不跑模型类负载**——服务器 2 核 CPU、无 GPU、3.6GB 内存，
  批量 embedding 建库、ragas 评估（要调 LLM 判分）都不行，这些只能在 Mac 做。
  开发依赖也不装。
- 注意服务器**仍然需要 fastembed**：加载 FAISS 后每次检索都要把 query 编码成向量，
  只是不做批量建库。模型缓存必须固定在仓库 `.cache/fastembed`——
  `scripts/deploy/api.sh` 里 `export FASTEMBED_CACHE_PATH`，
  原先默认的 `$TMPDIR/fastembed_cache` 机器一重启就没了（踩过坑，2026-11 修）。
- `api.sh` 负责杀旧进程 + 后台重启；日志 `api_server.log`
  （脚本已带 `PYTHONUNBUFFERED=1`，否则日志揣在内存里不落盘）。
- **用户数据沉淀**：每轮请求以 `turn {...}` JSON 行写入 `api_server.log`
  （`api_server.turn_record`：ts/ip/question/耗时/路由结果/游戏/token 数/工具调用次数，
  字段已按后续 SQLite turns 表对齐）。这份日志是真实用户数据，用于分析路由准确率、
  游戏分布、响应耗时、挖 badcase、给 golden set 选题。
  **数据分析/存储功能由服务器 agent 负责写**（turns 落库、日志统计脚本，它离数据最近），
  这类轻量分析直接在服务器上跑没问题（红线是不跑模型，见上）。
  **观测层已知待改进（2026-11-12 联合评审，归服务器侧）**：
  `spoiler_hits`/`turn_record`/JSONL 双写/`turns_report.py` 缺单测；
  `answer_head` 只存前 200 字，尾部命中词在报告里看不到（复盘对不上）；
  同一实体中英文重复计数；`data/turns/*.jsonl` 无轮转/清理策略；
  `log_turn` 是事件循环里的同步写；答案落盘的留存/隐私策略未定（目前仅 gitignore）；
  `turns_report.py` 边界：`--days 0`、缺 `ts` 的记录会 KeyError。
- Caddy 反代 `https://api.weirdsnap.top` → `localhost:8765`（见 `Caddyfile`）。

### 两端协作流水线（向量库更新）

```
Mac: fetch/ → build/ → enrich/(可选) → tool/mac_prepare_build.sh(建库+自检)
     → deploy/vectorstores.sh(scp 上传)
服务器: deploy/api.sh(重启，因 _load_vectorstore 有 lru_cache，不重启不生效)
```

## 常用命令

```bash
# 激活环境（一律用 .venv）
source .venv/bin/activate

# 本地启动 API 服务
python api_server.py

# 离线测试（每次改动后必跑）
python -m pytest tests -q

# 服务器上重启 API（杀旧进程 + 后台启动，日志在 api_server.log，PID 在 api_server.pid）
bash scripts/deploy/api.sh

# Mac 一键建库（拉代码 → 依赖/数据自检 → 建库 → 按真实检索路径自检）
bash scripts/tool/mac_prepare_build.sh --game baldurs_gate3

# 单独建库（⚠️ 必须在 Mac 本地跑，服务器 3.6GB 内存跑不动 fastembed+FAISS）
python3 scripts/tool/ingest_game.py --game <game>   # 中英 wiki；或 --game all
python3 scripts/tool/mac_build.py --game all        # 额外并入结构化 DB

# 术语表探针（只读，验证中文专名扩展是否命中）
python3 scripts/eval/glossary_probe.py --suite

# 部署向量库到服务器（scp，SSH 密码只需输一次），再重启 API
bash scripts/deploy/vectorstores.sh [游戏名...]

# 重建某游戏的 SQLite 结构化库
python scripts/build/build_game_db.py --game <game>
```

## 测试

主套件是 pytest，默认离线（不调真实 LLM，e2e 用例自动 skip）：

```bash
.venv/bin/python -m pytest tests -q          # 当前 119 passed / 20 skipped
.venv/bin/python -m pytest tests --run-e2e   # 真实模型端到端（需要 .env 的 key）
```

脚本式评估不进 pytest 默认收集（见 `tests/conftest.py` 的 `collect_ignore`）：

```bash
# Tier 1 轻量测试（每次改动后必跑；服务器上也能跑）
.venv/bin/python tests/test_light.py              # 全部
.venv/bin/python tests/test_light.py --routing    # 游戏路由准确率（纯本地，无 LLM）
.venv/bin/python tests/test_light.py --retrieval  # 向量检索命中率 top-k（无 LLM）
.venv/bin/python tests/test_light.py --e2e        # 端到端问答（需要 .env 的 LLM key）
.venv/bin/python tests/test_light.py --summary    # 只看各游戏 DB/向量库状态

# 空洞骑士回归测试（e2e，需要 LLM key）
python tests/test_qa.py              # 全部用例
python tests/test_qa.py --list       # 列出用例
python tests/test_qa.py --filter mantis   # 按名称过滤

# Tier 2 RAGAS 深度评估（Mac 上跑，需 pip install ragas datasets）
python scripts/eval/ragas_eval.py --game all --limit 5   # 报告输出到 evaluation/reports/
```

路由测试中有两条已知预期失败用例（不含游戏关键词的查询路由不到游戏），
这是当前设计行为，不是 bug，见 `tests/test_light.py` 注释。

## 代码风格与约定

- 注释、文档、prompt 用中文；标识符用英文 snake_case。沿用现有文件的风格，
  例如 `multi_agent.py` 的 `# ════ 分区标题 ════` 注释块。
- 系统入口约定：脚本自行 `sys.path.insert(0, str(ROOT / "src"))` 后
  `from rag_agent.xxx import ...`——本项目**没有** pyproject.toml / setup.py，
  不要添加打包配置，沿用 sys.path 约定。
- `load_dotenv()` 必须在导入 `rag_agent` 模块之前调用（否则 fastembed /
  huggingface_hub 已初始化，HF_* 环境变量不生效），见 `ingest_game.py` 头部。
- 新增游戏需要改的地方：
  1. `src/rag_agent/game_registry.py` — 加一条 `_reg({...})`（key/name/dir/db_file/data_file/
     signals/exact_patterns/wiki_*/spoiler_axis/emoji/aliases）。`game_router`、`tools`、
     `ingest_game`、`mac_build`、`vectorstores.sh`、`graph.py` 的菜单与 known_keywords
     都从注册表派生，不需要再改；
  2. 准备 `games/<game>/data/wiki_data.md`（+ 可选 `wiki_data_zh.md`）与 `<game>_data.db`。
- 脚本入口必须先 `sys.path.insert(.../src)` 再 `import rag_agent`。反了会直接
  `ModuleNotFoundError`（`ingest_game.py`/`wiki.py` 曾踩过），
  `tests/test_script_entrypoints.py` 会从任意 cwd 跑 `--help` 冒烟。

## 关键注意事项（血泪教训，勿犯）

- **向量库文档必须带 `language` 元数据（`en`/`zh`）**。`tools.search_knowledge_base`
  用 `filter={"language": ...}` 检索，FAISS 会直接过滤掉缺这个字段的文档——漏标会让
  线上检索恒为空（提示「知识库未找到」），而裸 `similarity_search` 自检完全发现不了。
  `ingest_game.py` 与 `mac_build.py` 都必须打标；`mac_build.collect_documents()` 有守卫。
  改动后用 `tests/test_mac_build_contract.py` 和 `mac_prepare_build.sh` 第 5 步
  （走真实 `tools.search_knowledge_base` 路径）验证。
  **读端不做兜底（2026-11-12 定）**：两路都空时**不**退回无过滤整库检索。
  兜底会把「建库漏标」这个契约错误静默降级成「混合语言照样能答」——兜底存在期间，
  缺 `language` 的 BG3 库连纯英文 query 都能出结果，测试和线上都看不出库是坏的。
  代价：旧库在严格模式下零命中，所以**建库修复必须和向量库替换同一次上线**，
  只推代码不传库会让线上检索全线变空。
  **服务器侧待办（2026-11-12 Mac 侧留）**：严格模式上线后，核对线上**每个游戏**的
  向量库是否带 `language` 标签（不只 BG3）。Mac 侧已审计本地 8 库全部合规
  （BG3 新库 66698 块全 en、HK/oni/terraria/va11halla 为 en+zh 混合），
  但线上库可能是更旧的版本。检查方法（在服务器仓库根跑）：
  `.venv/bin/python -c "import pickle;d=pickle.load(open('games/<game>/vectorstore/index.pkl','rb'))[0];print(sum('language' not in x.metadata for x in d._dict.values()),'块缺标')"`
  输出 0 即合规；非 0 的库要在 Mac 重建后重传（`mac_prepare_build.sh --game <游戏>`）。
  **核对结果（2026-11-12 服务器侧已做）**：8 款库走真实 `tools.search_knowledge_base`
  路径全部命中，`language` 齐；唯独 BG3 原库（10-41 构建）缺标签，已就地补 `language=en`
  （66698 条，未重算向量，备份 `index.pkl.bak-20261112`），重启后严格路径正常。
  Mac 侧重建上传后覆盖这份补丁产物，并删掉 `.bak`。
- **术语表已接入检索（2026-11-12）**：`tools.search_knowledge_base` 内部调用
  `glossary.expand_query()`，命中中文专名时自动追加英文原名（A/B 证据：
  影心 top-8 不中 → 扩展后 rank 1）。术语表异常时静默回退原 query。
  新增游戏的术语表放 `games/<game>/data/<game>_glossary.jsonl` 即生效；
  `tests/test_glossary.py` 锁住最长优先/遮挡/去重/置信度阈值。
- **Embedding 模型必须是多语言模型** `paraphrase-multilingual-MiniLM-L12-v2`。
  维基内容是英文、用户用中文提问，纯英文模型（如 bge-small-en-v1.5）会让
  跨语言检索几乎完全不命中。历史上曾被重构误改回去又改回来（见 DEVCONFIG.md）。
- **切换 embedding 模型后必须全量重建所有向量库**：FAISS 不校验 query 和
  document 向量是否同源，维度相同就能检索但语义空间不同，会静默产生垃圾结果。
  改模型的默认值要同时改 `src/rag_agent/config.py` 和 ingest 脚本里的 fallback。
- 向量库构建只能在 Mac 本地做（服务器内存不够），分工详见「运行环境分工」一节。
- 多轮游戏状态是**每请求**从 `history` 推导的（`multi_agent._game_from_history` →
  LangGraph state 的 `last_game`），没有跨请求全局变量；改这块时注意别把状态塞回进程全局。
- `vectorstore.py` 的 `_load_vectorstore` 带 `@lru_cache(maxsize=3)`——
  内存红线：BG3 单库常驻 ≈610–860MB，8 库全驻留会压垮服务器（3.6GB），
  3 封顶后上限 ≈1.8–2.6GB。向量库文件更新后必须重启进程才能生效。
- FAISS 加载使用 `allow_dangerous_deserialization=True`（本地可信文件，必要项）。
- 结构化查询工具（`query_structured_data`）是启发式中文指令解析
  （"查询 X" / "所有 X" / 数值筛选），不是通用 SQL 生成，改逻辑时先读现有分支。

## 安全注意事项

- `.env`、`api_config.json`、`data/quota.json` 已被 .gitignore 忽略——**不要提交任何密钥或密码**。
- 接口是**无密码**公开访问：`api_config.json` 只放额度/限速/超时，不再存密码；
  `/api/login` 仅为旧前端保留兼容占位。
- 默认额度与限速：全局 100 问/天（`data/quota.json` 持久化）、单 IP 20 次/分 + 600 次/天；
  只采信来自 `trusted_proxies` 的 `X-Forwarded-For`，防止伪造 IP 绕限流。
- 向量库和 SQLite 文件不进 git；服务器上的数据只通过 deploy 脚本同步。

## 变更记录（踩坑留档）

### 2026-11-11 — 检索契约修复 + 文档对齐

- **修复建库缺 `language` 元数据**：`mac_build.py` 建出的向量库会被
  `tools.search_knowledge_base` 的 `filter={"language": ...}` 全部过滤掉，线上表现为
  「知识库未找到」。现在英文 wiki + 结构化数据标 `en`、中文 wiki 标 `zh`，
  新增 `collect_documents()` 统一汇总并加缺失守卫；同时补上中文 wiki 合并
  （此前只有 `ingest_game.py` 会合）。见 `tests/test_mac_build_contract.py`。
- **补齐 wiki 分页格式**：`mac_build.py` 原先只认 `# 文档：` 和 `##` 两种，
  silksong / mhw 的 `# 标题` 文件会解析出 0 篇 wiki（只剩结构化数据，静默变瘦）。
  现与 `ingest_game.py` 对齐支持三种分页 + 整篇兜底；实测 silksong 196 篇、mhw 65 篇。
- **自检改为走真实检索路径**：`mac_prepare_build.sh` 第 5 步从裸 `similarity_search`
  换成 `tools.search_knowledge_base`，元数据漏标会在上传前暴露。
- **修复脚本入口 ImportError**：`ingest_game.py` / `wiki.py` 在 `sys.path.insert`
  之前 import `rag_agent`，按文档命令运行直接 `ModuleNotFoundError`；
  新增 `tests/test_script_entrypoints.py` 从任意 cwd 冒烟 `--help`。
- **补测试与依赖**：新增 `tests/test_glossary.py`（术语表匹配策略）、
  `tests/test_mac_build_contract.py`；`requirements.txt` 补 pytest。
  离线套件 119 passed / 20 skipped。
- **文档同步**：本文件按当前代码更新（无密码 + 全局额度、pytest 主套件、
  game_registry 单源、新增游戏步骤、language 契约）；README / DEVCONFIG / ROADMAP /
  PROJECT_STATUS / AI 应用工程计划同步记录。
