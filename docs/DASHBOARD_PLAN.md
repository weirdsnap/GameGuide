# 数据看板（只读后台）设计

状态：设计待评审，未实现
日期：2026-11-22
作者：服务器端
关联：`scripts/analysis/turns_report.py`（现有 CLI）、`api_server.py` 的 `/api/stats`、
`docs/ISSUES_2026-11-22.md`、`docs/SESSION_MEMORY_PLAN.md`（未来换 SQLite 的接口）

---

## 1. 目标与边界

要解决的事：现在每看一次数据都要在终端跑 `turns_report.py`，加参数、翻日志。想有一个页面能看：
今天用了几次、慢不慢、烧了多少 token、哪些轮次被点踩、剧透有没有漏、上次评测过没过。

**要做的**：单页面、只读、零构建、数据全来自已有 JSONL。

**不做的**：

- 不做 BI 平台（无拖拽、无自定义查询语言、无多用户权限）。
- 不做写入操作（不提供封号/清额度/改配置按钮）。
- 不引前端框架与构建链（没 node，服务器 2 核 3.6G）。
- 不引 CDN（图表手绘 SVG，离线可用；日志与提问文本不出服务器）。
- 不重复造统计口径（见 §4，CLI 与看板共用同一份纯函数）。

---

## 2. 现状盘点

| 已有 | 位置 | 缺口 |
|---|---|---|
| `GET /api/stats?days=N` | `api_server.py` | 只有总数/日计数/游戏路由分布，没有分位数、没有历史趋势、没有反馈 |
| `turns_report.py` | `scripts/analysis/` | 终端输出，无法筛选单轮、无法跳转详情 |
| `GET /api/feedback/stats` | `api_server.py` | 按 turn_id 去重、按游戏/路由聚合，但**不与 turn 记录关联**（看不到被踩的是哪个问题、当时耗时多少） |
| `data/turns/*.jsonl` | 每日追加 | 字段稳定（20 个），可直接消费 |
| `data/feedback/*.jsonl` | 每日追加 | 可直接消费 |
| `data/quota.json` | 单文件 | 今日已用次数 |
| `data/eval/`（评测输出） | 手工 `--json` | 文件名不固定，看板无法定位「最新一份」 |

关键缺口一句话：**数据都在，缺一个把它们拼起来看的入口**。

---

## 3. 数据源与规模

```
data/turns/YYYY-MM-DD.jsonl     每轮一行，约 1.5KB（answer_head 截 200 字）
data/feedback/YYYY-MM-DD.jsonl  点踩/赞，约 150B
data/quota.json                 {"date":"...","count":N}
data/eval/*.json                评测报告（需规范命名，见 §7）
```

量级：当前 203 轮 / 10 天，峰值单日 63 轮。一年按高强度估 3–5 万行，全部读进内存
也就几十 MB 量级。**直接读 JSONL，不上数据库**；真到 50 万行再切 `store.py`（方案 B）。

---

## 4. 架构：一份统计函数，两个消费方

```
scripts/analysis/report.py          ← 新增：纯函数（load / 聚合 / 分位数）
    ├── turns_report.py             ← CLI 改成薄壳，只负责打印
    └── api_server.py               ← /api/dashboard/* 直接调纯函数
```

理由：现在统计口径写死在 `turns_report.py` 与 `handle_stats` 两处，已经出现过
「CLI 算 P90、API 算 P50 却都叫 avg」这类不一致。看板上线前先把口径收拢，
否则页面数字和终端数字对不上，谁都不知道信谁。

纯函数清单（全部无 I/O，可单测）：

```python
load_turns(days, game=None) -> list[dict]        # 读文件（唯一 I/O）
summarize(records) -> dict                      # 总数/失败/剧透/游戏/路由
daily_series(records) -> dict[date, day_stats]  # 每日一行：轮次、tokens、耗时、点踩数
percentiles(values) -> {p50,p90,p99,max}
link_feedback(turns, feedback) -> list[dict]     # 按 turn_id 关联，输出被踩轮次详情
budget_status(records, limit) -> dict            # p90 vs 阈值，红绿灯
```

---

## 5. 页面与指标

单页四块，从上到下：

**① 顶部状态条**（一眼看完）

| 卡片 | 口径 | 数据源 |
|---|---|---|
| 今日提问 | `quota.count` / `daily_quota` | quota.json |
| 今日轮次 / 失败 | 当日 turns 计数，`route in (error, None)` 计失败 | turns |
| 耗时 p50 / p90 | `elapsed_ms` | turns |
| 输入 token p90 | `tokens_in` | turns |
| 预算守卫 | p90 > 40000 红，否则绿（接 I-09 验收线） | turns |
| 点踩 | 近 7 天 `rating=-1` 条数 / 总反馈数 | feedback |

**② 趋势图**（近 7/14/30 天切换，SVG 折线）

- tokens_in p90 与 elapsed p90 双线（看裁剪是否真降下来）
- 轮次柱状（看使用强度）
- history_msgs 均值（看前端是否还在发全量）

**③ 轮次列表**（带筛选，默认最近 50 轮）

- 列：时间、游戏、路由、耗时、tokens_in、history_msgs、剧透命中（L1/L2/L3 色块）、反馈
- 筛选：日期、游戏、只看点踩、只看剧透 L3、只看慢轮（>30s）
- 点一行展开：完整 `question`、`answer_head`（**200 字上限，页面要标注**）、tool_calls、turn_id

**④ 质量与评测**

- 剧透命中趋势（按等级堆叠）
- 点踩列表：问题 + 当时 tokens/耗时 + 游戏（从 ② 的关联函数来）
- 上次评测：`agent_behavior --json` 的 pass/known_issue/fail 计数与时间；缺失时显「未跑」
- pytest 基线：P2 再考虑（需 conftest 钩子落文件，见 §8）

---

## 6. API 设计

全部 `GET`、只读、无副作用。路径前缀 `/api/dashboard`，与业务 API 分开，
未来加鉴权时只罩这一组。

| 路径 | 参数 | 返回 | 复用 |
|---|---|---|---|
| `/api/dashboard/summary` | `days=7` | 顶部卡片 + 日序列 | `summarize` + `daily_series` + `budget_status` |
| `/api/dashboard/turns` | `date=&game=&bad=1&l3=1&slow=1&limit=50&before=<ts>` | 轮次列表（游标分页，`before` 为上一页最后一条 ts） | `load_turns` + 过滤 |
| `/api/dashboard/feedback` | `days=7` | 点踩/点赞列表（按 turn_id 关联 turn 记录） | `link_feedback` |
| `/api/dashboard/eval` | — | 最新评测报告摘要（文件名、时间、计数） | 读 `data/eval/latest.json` |

约定：

- 错误一律 `200` + `{"error": "..."}`？**不**。沿用现有风格，参数非法回 400，文件缺失回
  空数据结构 + `"stale": true`，让页面显示「暂无数据」而不是白屏。
- 所有数字字段不返回 `None`，缺值填 0（前端不再做 `|| 0`）。
- 响应加 `"generated_at"`，前端显示「N 分钟前」，避免看着旧数字以为是实时。
- 单响应上限：轮次列表 `limit` 硬上限 200，防止有人拉全量。

**兼容**：保留 `/api/stats`，内部改为调 `summarize()`，字段不变（博客前端在用）。

---

## 7. 前端

单文件 `web/dashboard.html`，原生 JS + 内联 SVG，无依赖。

- 样式跟博客同色系（深底浅字），不引字体，不引图标库。
- 图表：折线/柱状手写 40 行 SVG，坐标轴只用 3 个刻度，不做动画。
- 刷新：进入页面拉一次，右上角手动刷新按钮；不做轮询（数据是秒级变化没意义）。
- 时区固定显示 `Asia/Shanghai`（所有 ts 是 epoch，展示层转）。
- IP 脱敏：`163.125.x.x`（页面上只需要区分「是不是自己」）。

评测报告命名约定（Mac 端配合）：

```
data/eval/latest.json        最新一次 agent_behavior --json 的原样输出
```

跑评测时加 `--json data/eval/latest.json` 即可，不需要额外逻辑。

---

## 8. 部署与安全

- 静态页由 Caddy `file_server` 提供，路径 `/dash`；数据 API 反代到 `127.0.0.1:8765`。
- **必须加一层认证**：页面上有真实提问文本。Caddy `basicauth` 最省事（一个用户名密码，
  写进 Caddyfile，不入仓库）。不引入用户体系。
- 不提交 `dashboard.html` 到博客仓库（避免和公开站点混在一起），放 `web/` 并 gitignore？
  **不**：放仓库 `web/` 正常提交，Caddy 指向仓库路径，省一份复制。
- `robots.txt` 禁用 `/dash`（防搜索引擎抓）。
- 看板不写任何文件，仅读。日志轮转（api.sh 转 `.prev`）不影响，因为它只读 `turns/` 与 `feedback/`。

---

## 9. 分期

| 期 | 内容 | 依赖 | 估时 |
|---|---|---|---|
| P0 | `report.py` 纯函数 + `/api/dashboard/summary` + 页面① ② | 无（现有字段够） | 半天 |
| P0 | `/api/dashboard/turns` + 页面③（筛选/展开） | 无 | 半天 |
| P1 | `/api/dashboard/feedback` + 关联 + 页面④ 点踩列表 | 无 | 2 小时 |
| P1 | Caddy basicauth + `/dash` 路由 + robots | 需要 Caddyfile 编辑权限（用户） | 半小时 |
| P2 | `/api/dashboard/eval` + 评测面板 | 评测报告固定落 `data/eval/latest.json` | 1 小时 |
| P2 | pytest 基线面板 | conftest 钩子落 `data/eval/pytest_last.json` | 2 小时 |
| P3 | 告警（cron 跑 check-budget，超阈值发飞书） | 飞书 webhook | 1 小时 |

P0 完成后就能替代 `turns_report.py` 的日常使用；P1 之后才真的叫「后台」。

---

## 10. 测试

| 层 | 文件 | 内容 |
|---|---|---|
| L0 | `tests/test_report_stats.py` | 聚合纯函数：空数据、单日、跨日、缺字段（老记录没 tokens_in）、时区边界（23:59 与 00:01）、分位数（n=1/2/100）、反馈按 turn_id 去重 |
| L0 | `tests/test_dashboard_api.py` | aiohttp 工厂 + 临时 JSONL 目录：字段完整性、`limit` 硬上限、非法 `days` 兜底、`before` 游标不重复不丢 |
| L0 | `tests/test_report_parity.py` | CLI 与 API 同源：同一份数据两边 `summarize()` 输出相等（防口径漂移） |
| 手工 | 验收清单 | 见 §11 |

不写前端单测（单文件、无构建，手工验收更划算）。

---

## 11. 验收标准

1. 页面上「今日提问」「今日轮次」「耗时 p90」「token p90」四个数字与 `turns_report.py`
   同期输出**完全一致**（同源验证）。
2. 点踩列表里的 turn_id 能在 `data/turns/` 里 grep 到，且问题文本对得上。
3. 预算守卫：把阈值调到 1000 能变红，调回 40000 变绿。
4. 首屏（P0 数据量：近 7 天）在服务器上 < 1s，局域网浏览器 < 2s。
5. 无网环境（断外网）页面仍能渲染——证明无 CDN 依赖。
6. 未认证访问 `/dash` 返回 401；认证后 200。
7. `pytest -q` 不因新增测试变慢超过 1s（当前 4.8s）。

---

## 12. 风险与不做清单

| 风险 | 应对 |
|---|---|
| 数据量增长到百万行 | 切 `store.py`（方案 B），API 层不变 |
| 与方案 B 的 turns 表重复 | `report.py` 的 load 层做成可替换（JSONL / SQLite 两实现） |
| `answer_head` 只有 200 字，详情页看着像被截 | 页面上明写「仅前 200 字，完整答案需改 turn_record」 |
| 提问文本泄露 | basicauth + robots + 不公开域名 |
| 时区漂移 | 服务端固定 `Asia/Shanghai`（`TZ` 环境变量 + 分组函数显式传 tz） |
| 页面跟 `/api/stats` 口径不一致 | `test_report_parity.py` 卡住 |

明确不做：

- 不做「重跑某一轮」按钮（会二次计费、污染 turns 数据）。
- 不做实时（WebSocket）——1 秒刷新一次没人看，还费电。
- 不做用户级看板（本项目就一个用户，IP 聚合足够）。
