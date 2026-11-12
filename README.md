# GameGuide RAG Agent

多游戏 RAG（检索增强生成）Agent，基于 LangGraph + FAISS + SQLite，
目前注册了 8 款游戏的 Wiki 知识库。

## 支持的游戏

| 游戏 | 内部键 | 主要数据来源 |
|:----|:-------|:------------|
| 空洞骑士 (Hollow Knight) | `hollow_knight` | Fandom Wiki + HallownestAPI |
| 丝之歌 (Hollow Knight: Silksong) | `silksong` | Fandom Wiki |
| 缺氧 (Oxygen Not Included) | `oni` | Fandom Wiki EN/ZH |
| 泰拉瑞亚 (Terraria) | `terraria` | Fandom Wiki |
| 怪物猎人：荒野 (Monster Hunter Wilds) | `mhw` | Fandom Wiki |
| 赛博朋克 2077 (Cyberpunk 2077) | `cyberpunk2077` | Fandom Wiki |
| 赛博朋克酒保行动 (VA-11 Hall-A) | `va11halla` | Fandom Wiki |
| 博德之门 3 (Baldur's Gate 3) | `baldurs_gate3` | bg3.wiki |

游戏名称、路径、路由关键词和 Wiki 配置统一维护在
`src/rag_agent/game_registry.py`。

## 架构

```text
用户问题
   ↓
aiohttp API：限流、每日额度、请求观测
   ↓
LangGraph detect_game：识别游戏与对话上下文
   ├─ 无法识别 → 游戏菜单
   ├─ 未支持的游戏 → LLM 兜底
   └─ 已支持的游戏 → ReAct Agent
                       ├─ FAISS：剧情、机制、位置、攻略等文本检索
                       ├─ SQLite：属性、费用、伤害等结构化查询
                       └─ Schema：查看数据库表结构
```

多轮上下文由调用方随每次请求传入 `history`。服务不会在不同请求或用户之间
共享“上一轮游戏”；没有历史且无法识别游戏时会返回游戏菜单。

## 项目结构

```text
.
├── api_server.py                 # aiohttp 服务入口
├── src/rag_agent/
│   ├── multi_agent.py            # ask / ask_stream 对外入口
│   ├── graph.py                  # LangGraph 路由与 ReAct Agent
│   ├── tools.py                  # 向量检索与 SQLite 工具
│   ├── game_registry.py          # 游戏配置单源
│   ├── game_router.py            # 游戏识别与提示词
│   ├── glossary.py               # 中英专名术语表（query 扩展，尚未接入检索）
│   ├── vectorstore.py            # FAISS 加载、缓存与检索器
│   ├── data_converter.py         # 空洞骑士 JSON 转文档
│   ├── hk_db.py                  # 空洞骑士结构化数据库
│   └── config.py                 # LLM / Embedding 配置
├── scripts/
│   ├── fetch/                    # Wiki 数据采集
│   ├── build/                    # SQLite 数据库构建
│   ├── enrich/                   # 数据补全 + 术语表生成
│   ├── tool/ingest_game.py       # FAISS 向量库构建（中英 wiki）
│   ├── tool/mac_build.py         # 向量库构建（额外并入结构化 DB）
│   ├── tool/mac_prepare_build.sh # Mac 一键建库 + 真实检索路径自检
│   ├── eval/                     # 评估脚本（RAGAS、术语表探针、golden QA）
│   └── deploy/                   # 部署脚本
├── games/<游戏键>/               # 各游戏文档、数据库和向量库
└── tests/                        # 离线单测与可选端到端测试
```

## 启动 API

先准备 Python 环境、安装 `requirements.txt`，并根据 `.env.example` 配置模型服务：

```bash
python -m pip install -r requirements.txt
python api_server.py
```

服务默认监听 `0.0.0.0:8765`，生产环境通过 Caddy 暴露为
`https://api.weirdsnap.top`。

`api_config.json` 不存在时会按默认值生成。主要配置：

| 配置 | 默认值 | 用途 |
|:-----|:-------|:-----|
| `daily_quota` | `100` | 所有访客共享的每日提问额度 |
| `rate_limit_per_min` | `20` | 单客户端 IP 每分钟请求上限 |
| `rate_limit_per_day` | `600` | 单客户端 IP 每日请求上限 |
| `request_timeout` | `120` | 非流式问答的秒级超时 |
| `trusted_proxies` | `["127.0.0.1", "::1"]` | 允许提供真实客户端 IP 的可信代理 |

只有来自 `trusted_proxies` 的请求才会读取 `X-Forwarded-For`。如果更换代理
部署方式，需要同步调整此列表；不要无条件信任公网请求中的转发头。

## API

接口无密码认证。`/api/login` 仅为旧前端保留兼容占位。

### 普通问答

```bash
curl -X POST https://api.weirdsnap.top/api/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "空洞骑士里亡者之怒在哪里拿？", "history": []}'
```

响应示例：

```json
{
  "answer": "亡者之怒（Fury of the Fallen）可以在国王小径找到……",
  "elapsed": 3.2,
  "quota": {"date": "2026-11-05", "used": 12, "limit": 100, "remaining": 88}
}
```

多轮对话传入本会话自己的历史：

```json
{
  "question": "它占用几个护符槽？",
  "history": [
    {"role": "user", "content": "空洞骑士里亡者之怒在哪里拿？"},
    {"role": "assistant", "content": "亡者之怒可以在国王小径找到……"}
  ]
}
```

### 其他端点

- `POST /api/ask/stream`：SSE 流式问答，事件类型为 `meta`、`token`、`error`、`done`。
- `GET /api/quota`：查询当天全局额度。
- `GET /api/status`：健康检查，包含运行时长和额度状态。

额度或 IP 限流触发时返回 HTTP 429；非流式请求超时返回 HTTP 504。

## 数据流水线

```text
① scripts/fetch/               从 Wiki API 抓取页面
② scripts/build/               解析并构建 SQLite 数据库
③ scripts/enrich/              可选：从其他来源补全数据
④ scripts/tool/ingest_game.py  构建 FAISS 向量库（在 Mac 本地运行）
⑤ scripts/deploy/vectorstores.sh  部署向量库
⑥ scripts/deploy/api.sh        重启 API
```

也可以在 Mac 上一键完成依赖/数据自检、建库和检索自检：

```bash
bash scripts/tool/mac_prepare_build.sh --game <游戏键>
```

第 5 步会调用真实的 `tools.search_knowledge_base`（带 `language` filter）自检，
避免「库建好了但线上检索恒为空」的元数据漏标问题。

## 测试

```bash
# 离线测试，不调用真实 LLM
python -m pytest tests -q

# 真实 LLM 端到端用例，需要 API Key
python -m pytest tests/test_qa.py --run-e2e

# 路由、检索与轻量端到端评估脚本
python tests/test_light.py
```

`tests/test_light.py` 是独立评估脚本，不属于默认 pytest 收集范围。

## 开发注意事项

- Embedding 默认使用 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，支持中文查询检索英文文档。
- 更换 Embedding 模型后必须全量重建向量库。
- 向量库文档的 metadata 必须带 `language`（`en` / `zh`）：检索工具按该字段过滤，缺字段的文档会被 FAISS 直接忽略。`ingest_game.py` / `mac_build.py` 已处理，新增数据源时别漏。
- 向量库在 Mac 本地构建，服务器只负责加载与查询。
- 默认对话模型由 `CHAT_MODEL` 配置，通过 OpenAI 兼容接口调用。

## 许可

MIT
