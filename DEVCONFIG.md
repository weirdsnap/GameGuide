# 项目配置说明

## Embedding：必须使用多语言模型

当前默认：

```text
sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
```

原因是 Wiki 以英文为主，而用户常用中文提问。纯英文模型曾导致中文查询无法命中英文文档。模型维度相同并不代表语义空间兼容，切换模型后必须全量重建向量库。

默认模型只在 `src/rag_agent/config.py` 的 `FASTEMBED_MODEL` 中维护；构建脚本
`scripts/tool/ingest_game.py` 会读取同一配置，也允许通过环境变量覆盖。

### 更换模型

1. 修改 `FASTEMBED_MODEL` 或设置对应环境变量。
2. 在 Mac 上运行 `python3 scripts/tool/ingest_game.py --game all`（或 `mac_build.py`，
   它会额外并入结构化 DB；一键脚本 `mac_prepare_build.sh --game <game>` 含建后自检）。
3. 运行 `bash scripts/deploy/vectorstores.sh` 部署向量库。
4. 在服务器运行 `bash scripts/deploy/api.sh` 重启服务。

不要在未重建向量库的情况下只修改服务器查询模型。

## 向量库元数据契约（language）

`tools.search_knowledge_base` 检索时用 `filter={"language": "en"}` / `{"language": "zh"}`。
FAISS 会直接过滤掉**缺少** `language` 字段的文档，因此建库时每篇文档都必须打标：

- 英文 wiki、结构化数据库导出的文档 → `language: "en"`
- 中文 wiki（`wiki_data_zh.md`）→ `language: "zh"`

`ingest_game.py` 与 `mac_build.py` 都已按此实现（`mac_build.collect_documents()` 还有
缺失守卫）。漏标的症状很隐蔽：向量库看起来建好了、裸 `similarity_search` 也有结果，
但线上工具恒返回「(知识库未找到…)」。快速排查：

```bash
python3 - <<'PY'
import pickle, collections
obj = pickle.load(open("games/baldurs_gate3/vectorstore/index.pkl", "rb"))
print(collections.Counter(
    (d.metadata or {}).get("language", "<missing>") for d in obj[0]._dict.values()))
PY
```

`mac_prepare_build.sh` 第 5 步会调用真实检索工具自检（带 filter），建完库建议直接跑。

## LLM 与环境变量

复制 `.env.example` 为 `.env`，配置：

- `OPENAI_API_KEY`：OpenAI 兼容服务密钥。
- `OPENAI_BASE_URL`：兼容接口地址。
- `CHAT_MODEL`：默认对话模型。
- `FASTEMBED_MODEL`：可选，覆盖本地 Embedding 模型。
- `HF_ENDPOINT`、`HF_HUB_OFFLINE`：模型下载镜像与离线缓存控制。

## API 配置

`api_server.py` 首次运行会生成 `api_config.json`。主要字段：

| 字段 | 默认值 | 说明 |
|:-----|:-------|:-----|
| `daily_quota` | `100` | 全局每日提问额度 |
| `quota_file` | `data/quota.json` | 额度持久化文件 |
| `rate_limit_per_min` | `20` | 单客户端 IP 每分钟上限 |
| `rate_limit_per_day` | `600` | 单客户端 IP 每日上限 |
| `request_timeout` | `120` | 非流式请求超时秒数 |
| `trusted_proxies` | `127.0.0.1`, `::1` | 可信反向代理地址 |

Caddy 默认通过 `X-Forwarded-For` 传递客户端地址。服务只采信来自
`trusted_proxies` 的转发头，防止公网客户端伪造 IP 绕过限流。

## 多轮对话状态

服务不保存全局游戏状态。前端每次请求都应传入当前会话的 `history`；后端从最近的
用户消息推导游戏。无历史且问题不含可识别游戏信号时，接口返回游戏菜单。

## 常用命令

| 操作 | 命令 |
|:-----|:-----|
| 启动 API | `python api_server.py` |
| 离线测试 | `python -m pytest tests -q` |
| Mac 一键建库 + 自检 | `bash scripts/tool/mac_prepare_build.sh --game <game>` |
| 构建全部向量库 | `python3 scripts/tool/ingest_game.py --game all` |
| Mac 远程构建（含结构化 DB） | `python3 scripts/tool/mac_build.py --game all` |
| 部署向量库 | `bash scripts/deploy/vectorstores.sh` |
| 重启 API | `bash scripts/deploy/api.sh` |
