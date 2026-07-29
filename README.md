# GameGuide RAG Agent 🎮

多游戏 RAG（检索增强生成）Agent，基于 LangGraph + FAISS + SQLite 架构，
支持对 7 款游戏的 Wiki 知识进行问答。

## 支持的游戏

| 游戏 | 数据来源 | 结构化 DB 记录 | 向量库 |
|:----|:---------|:--------------:|:------:|
| 空洞骑士 (Hollow Knight) | Fandom Wiki + HallownestAPI | 437 条 | ✅ |
| 缺氧 (ONI) | Fandom Wiki EN/ZH | 271 条 | ✅ |
| 泰拉瑞亚 (Terraria) | Fandom Wiki | 820 条 | ✅ |
| 丝之歌 (Silksong) | Fandom Wiki | 100 条 | ✅ |
| 赛博朋克2077 (Cyberpunk 2077) | Fandom Wiki | ~4,302 条 | ✅ |
| 怪物猎人：荒野 (MH Wilds) | Fandom Wiki | ~1,961 条 | ✅ |
| VA-11 Hall-A | Fandom Wiki | 133 条 | ✅ |

## 架构

```
用户问题
    │
    ▼
┌────────────────────┐
│   game_router      │  ← 检测是哪个游戏
│   (信号词匹配)     │
└────────┬───────────┘
         ▼
┌────────────────────┐
│  LangGraph Agent   │  ← 自动选择工具
│  (ReAct 模式)      │
└──┬─────────────┬───┘
   ▼             ▼
┌────────┐ ┌──────────┐
│ RAG    │ │ SQLite   │
│ 向量   │ │ 结构化   │
│ 检索   │ │ 查询     │
└────────┘ └──────────┘
```

**双通道检索：**
- **RAG 向量检索** — 自然语言描述、剧情、策略、攻略等非结构化文本
- **SQLite 结构化查询** — 数值、属性、精确数据（Boss HP、护符费用等）

## 项目结构

```
.
├── api_server.py              # FastAPI 服务器入口
├── api_config.json            # 服务器配置（端口、密码、限速）
├── src/rag_agent/
│   ├── multi_agent.py         # Agent 主逻辑 + 工具定义
│   ├── vectorstore.py         # FAISS 向量库加载
│   ├── data_converter.py      # 文档→向量转换
│   ├── config.py              # LLM / Embedding 配置
│   ├── db/                    # 各游戏数据库模块
│   └── games/                 # 各游戏数据路径配置
├── scripts/
│   ├── build/                 # 数据库构建
│   │   ├── build_game_db.py
│   │   ├── cyberpunk_db.py
│   │   ├── generic_db.py
│   │   ├── mhw_db.py
│   │   └── va11halla_db.py
│   ├── enrich/                # 数据补全
│   │   ├── mhw.py
│   │   ├── oni.py
│   │   ├── terraria.py
│   │   └── wiki_data.py
│   ├── fetch/                 # Wiki 数据采集
│   │   ├── wiki.py
│   │   └── silksong.py
│   ├── eval/                  # 评估
│   │   ├── ragas.py
│   │   └── check_imports.py
│   ├── deploy/                # 部署
│   │   ├── api.sh             # 服务器启动/重启
│   │   └── vectorstores.sh    # 向量库 scp 部署
│   ├── tool/                  # 开发工具
│   │   ├── ingest_game.py     # 向量库构建（本地）
│   │   └── mac_build.py       # Mac 远程构建助手
│   └── archive/               # 已归档的旧脚本
├── games/                     # 各游戏数据目录
│   ├── hollow_knight/data/
│   ├── oni/data/
│   │   ...
├── tests/                     # 测试
├── config.py                  # 项目级配置
├── requirements.txt           # Python 依赖
└── .env                       # API Key 配置
```

## 快速开始

### 启动 API 服务器

已在服务器上部署，通过 Caddy 反向代理暴露：
- **地址**: `https://api.weirdsnap.top`
- **端口**: 8765（内部）→ 443（公共）
- **认证**: 密码（在 `api_config.json` 中配置）

```bash
# 本地测试（需要先配置 .env）
source .venv/bin/activate
python api_server.py
```

### 发送请求

```bash
curl -X POST https://api.weirdsnap.top/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <密码>" \
  -d '{"message": "空洞骑士里亡者之怒在哪里拿？"}'
```

响应格式：
```json
{
  "answer": "亡者之怒（Fury of the Fallen）可以在国王小径（King's Pass）找到...",
  "game": "hollow_knight"
}
```

## 数据流水线

添加新游戏或更新数据的流程：

```
① fetch/wiki.py           ← 从 Fandom API 抓取 Wiki 页面
② build/build_game_db.py  ← 解析并存入 SQLite 数据库
③ enrich/*                ← 可选：从其他来源补全数据
④ tool/ingest_game.py     ← 构建 FAISS 向量库（需在 Mac 本地运行）
⑤ deploy/vectorstores.sh  ← 将向量库 scp 到服务器
⑥ deploy/api.sh           ← 在服务器上重启 API
```

## 开发笔记

- **Embedding 模型**: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
  - 支持中文查询检索英文文档
  - 切换模型需全量重构建向量库
- **向量库构建**：必须在 Mac 本地运行（服务器仅 3.6GB 内存，跑不动 FastEmbed + FAISS）
- **Mac 远程构建**: `python3 scripts/tool/mac_build.py --game all`
- **LLM**: DeepSeek Flash (deepseek-chat)，兼容 OpenAI API

## 许可

MIT
