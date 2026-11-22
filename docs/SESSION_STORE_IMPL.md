# 会话存储实施规格（可开工版）

状态：**待实现**。本文是 `docs/SESSION_MEMORY_PLAN.md`（方案 B）的可执行细化，
承接 I-23（刷新丢历史，用户 2026-11-22 提出）。
两份文档冲突时：**为什么这么做看 PLAN，怎么做（表结构/接口/函数签名/上线顺序）看本文**。

> 范围边界（不变）：不做账号、不做登录、不做跨会话用户画像、不向量化历史。
> 本轮唯一目标是「刷新页面 / 关浏览器 / 换天回来，能接着上次聊」，顺带把
> turns 结构化落到 SQLite，让 feedback 能 join、存量长会话有存根。

---

## 1. 身份模型与参数（定稿）

三层标识，全部匿名、全部前端生成，服务端不签发：

| 标识 | 存哪 | 作用域 | 生成规则 |
|---|---|---|---|
| `client_id` | 浏览器 localStorage `gg_client_id` | 一个浏览器 = 一个用户 | `crypto.randomUUID()` 去掉横线（32 hex），首次访问生成 |
| `session_id` | localStorage `gg_session_id` | 一次对话 | 同上，点「新对话」时重新生成 |
| `turn_id` | 服务端生成（现状，`new_turn_id()`） | 一轮问答 | 12 hex，已有，不动 |

为什么不加 cookie：前端在 `weirdsnap.github.io`，API 在 `api.weirdsnap.top`，
跨站 cookie 属第三方 cookie，Chrome/Safari 默认拦或 7 天过期，不能当唯一手段。

为什么不做 IP+UA 兜底：同出口 IP（公司/校园/机场节点）下会串号，风险大于收益。
真要救「换浏览器」，走第 12 节的恢复码（可选，默认关）。

**留存参数**（全部 env，默认值就是用户口径）：

| env | 默认 | 含义 |
|---|---|---|
| `GAMEGUIDE_SESSION_DB` | `data/sessions.db` | 库文件位置 |
| `GAMEGUIDE_RETENTION_ANSWER_DAYS` | `3` | `turns.answer` 保留天数，过期置 NULL |
| `GAMEGUIDE_RETENTION_RECEIPT_DAYS` | `90` | receipts 保留天数 |
| `GAMEGUIDE_RETENTION_SESSION_DAYS` | `90` | session 无活动多少天后整行删除（含 turns/receipts） |
| `GAMEGUIDE_SERVER_HISTORY` | `0` | `1` = 服务端权威装配上下文；`0` = 仍用前端 history（迁移期） |
| `GAMEGUIDE_SESSION_MAX_TURNS` | `6` | 装配时保留的原文轮数（含本轮之前的最近 6 轮） |
| `GAMEGUIDE_SESSION_MAX_CHARS` | `6000` | 装配时原文总字符预算（与 `trim_history` 一致） |

三条留存的组合效果：**3 天后答案原文消失、但这一局还认得出来**（sessions 行 +
receipts 里的结论/来源/act 还在），符合「不必长期存对话原文，但要能续上进度」。

---

## 2. 数据模型（可直接执行）

`store.init_db()` 里逐条执行，幂等：

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    client_id    TEXT NOT NULL,
    created_at   INTEGER NOT NULL,
    last_active  INTEGER NOT NULL,
    game         TEXT,
    progress     INTEGER,
    progress_src TEXT,
    title        TEXT,
    n_turns      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_client ON sessions(client_id, last_active DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(last_active);

CREATE TABLE IF NOT EXISTS turns (
    turn_id         TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    ts              INTEGER NOT NULL,
    question        TEXT NOT NULL,
    answer          TEXT,
    route           TEXT,
    game            TEXT,
    progress_level  INTEGER,
    progress_source TEXT,
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    tool_calls      INTEGER,
    elapsed_ms      INTEGER,
    answer_partial  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, seq);

CREATE TABLE IF NOT EXISTS receipts (
    turn_id    TEXT PRIMARY KEY REFERENCES turns(turn_id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    conclusion TEXT,
    sources    TEXT,
    entry_acts TEXT,
    entities   TEXT
);
CREATE INDEX IF NOT EXISTS idx_receipts_session ON receipts(session_id, seq);
```

设计约束（评审时别改，理由在 PLAN §2–§4）：

- `turns.ts / last_active` 用 **unix 秒整数**，与 `turn_record` 的 `ts` 同源，便于和 jsonl 对账。
- `turns` 存**全文 answer**，不存 IP、不存 UA、不存 history 原文。
- `answer` 可为 NULL：留存过期（正常）或流式中断（`answer_partial=1`，只存已产出部分）。
- receipts 不依赖 `turns.answer`，自己带 `seq / conclusion`——原文被置空后存根仍可用。
- `sessions.game` / `sessions.progress` 是**权威值**，每轮 `max()` 更新，见 §7。

---

## 3. store.py 接口（Mac 实现）

位置：`src/rag_agent/store.py`。约定：**每次调用开一个连接、用完关**（单进程 aiohttp，
低 QPS，SQLite 足够）；所有函数失败要么抛 `StoreError`，要么返回 None，由调用方降级。

```python
class StoreError(RuntimeError): ...

def init_db(path) -> None                      # 幂等建表 + PRAGMA
def ensure_session(*, session_id, client_id, now=None) -> bool
    # INSERT OR IGNORE 空 session；返回 True=新建，False=已存在
def touch_session(session_id, *, now=None) -> None
    # last_active = now
def get_session(session_id) -> dict | None     # 全部列，dict
def list_sessions(client_id, *, limit=10) -> list[dict]
    # 按 last_active DESC；只返回该 client 的，跨 client 不可见
def delete_session(session_id) -> bool
def reset_progress(session_id) -> None         # progress=NULL, progress_src='reset'

def next_seq(session_id) -> int                # SELECT COALESCE(MAX(seq),0)+1
def append_turn(*, session_id, turn_id, question, answer, route=None, game=None,
                progress_level=None, progress_source=None, tokens_in=None,
                tokens_out=None, tool_calls=None, elapsed_ms=None,
                answer_partial=False, now=None) -> int
    # 单事务：BEGIN IMMEDIATE → next_seq → INSERT turns → UPDATE sessions
    #   (last_active, n_turns=n_turns+1, title=COALESCE(title, question[:40]),
    #    game=COALESCE(:game, game), progress=max(progress,:progress_level),
    #    progress_src CASE progress_level>COALESCE(progress,-1) THEN :src ELSE progress_src END)
    # COMMIT；返回 seq
def update_game(session_id, game) -> None      # 菜单轮识别出游戏但未作答时也可写

def session_turns(session_id, *, limit_turns=6, max_chars=6000) -> list[dict]
    # 取最近 limit_turns 轮的 (question, answer)，展开为 [{"role","content"}, ...]
    # 复用 trim_history 的语义：单条 >2000 字截断、总字符超 max_chars 从旧往新丢
def session_receipts(session_id, *, limit=20) -> list[dict]   # v2 才用，见 §6

def save_receipt(*, turn_id, session_id, seq, conclusion=None,
                 sources=None, entry_acts=None, entities=None) -> None
def purge(*, answer_days=3, receipt_days=90, session_days=90, now=None) -> dict
    # 返回 {"answers_cleared":n, "receipts_deleted":n, "sessions_deleted":n}
```

`append_turn` 的事务体（唯一需要小心的并发点）：

```python
with conn:                                   # 隐式事务，失败自动回滚
    conn.execute("BEGIN IMMEDIATE")          # 先拿写锁，防两连接算出同一个 seq
    seq = conn.execute(
        "SELECT COALESCE(MAX(seq),0)+1 FROM turns WHERE session_id=?", (sid,)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO turns (turn_id,session_id,seq,ts,question,answer,route,game,"
        "progress_level,progress_source,tokens_in,tokens_out,tool_calls,elapsed_ms,"
        "answer_partial) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (turn_id, sid, seq, now, question, answer, route, game,
         progress_level, progress_source, tokens_in, tokens_out, tool_calls,
         elapsed_ms, 1 if answer_partial else 0),
    )
    # 进度 max 在锁内读-算-写（比写复杂 CASE 好读，事务已保证原子）
    old, old_src = conn.execute(
        "SELECT progress, progress_src FROM sessions WHERE session_id=?", (sid,)
    ).fetchone()
    if progress_level is not None and (old is None or progress_level > old):
        old, old_src = progress_level, progress_source
    conn.execute(
        "UPDATE sessions SET last_active=?, n_turns=n_turns+1, title=COALESCE(title,?),"
        " game=COALESCE(?, game), progress=?, progress_src=? WHERE session_id=?",
        (now, question[:40], game, old, old_src, sid),
    )
```

注意：`BEGIN IMMEDIATE` 必须在 `with conn` 之后显式执行（sqlite3 的隐式事务是
DEFERRED，并发下会先读后写导致 `database is locked` 或 seq 重号）；
`busy_timeout=5000` 已给重试窗口。

`purge` 的 SQL：
```sql
UPDATE turns SET answer=NULL, answer_partial=0
 WHERE answer IS NOT NULL AND ts < :answer_cutoff;

DELETE FROM receipts WHERE turn_id IN
  (SELECT turn_id FROM turns WHERE ts < :receipt_cutoff);

DELETE FROM sessions WHERE last_active < :session_cutoff;   -- 级联删 turns/receipts
```

---

## 4. HTTP 接口契约

### 4.1 `POST /api/ask`、`POST /api/ask/stream`（请求体增量，向后兼容）

```jsonc
{
  "question": "山丘巨人的铁手套在哪拿",
  "history": [ ... ],        // 旧前端仍发；GAMEGUIDE_SERVER_HISTORY=1 时服务端忽略
  "client_id": "3f9c…",      // 新增，可选（缺失=旧前端，走 legacy 路径）
  "session_id": "a71e…"      // 新增，可选（缺失=新对话，服务端新建）
}
```

响应（非流式 json / 流式 `done` 事件）新增两个字段：

```jsonc
{ "answer": "...", "elapsed": 12.3, "quota": {...}, "turn_id": "9b3f…",
  "session_id": "a71e…", "client_id": "3f9c…" }
```

旧前端不读这两个字段，行为不变。

**身份解析顺序**（`resolve_session(body)`，纯函数可单测）：

1. 有 `session_id` 且 DB 里存在、且（`client_id` 为空 或 与库里一致）→ 用它。
2. 有 `session_id` 但库里没有（过期/被清理/换库）→ **新建**一个 session 并沿用请求的
   `client_id`；响应里回传新的 `session_id`（前端覆盖 localStorage，用户无感）。
3. 没有 `session_id`、有 `client_id` → 新建 session 挂在该 client 下。
4. 两个都没有 → 新建 client_id（服务端生成）+ 新 session，响应回传（旧前端也能被记账）。
5. DB 不可用（打不开/写失败）→ `session_id=None`，全链路退回 legacy 行为，**不能 500**。

### 4.2 `GET /api/sessions?client_id=xxx&limit=10`

```jsonc
{ "sessions": [ { "session_id": "…", "last_active": 1795000000, "game": "baldurs_gate3",
                  "progress": 2, "title": "山丘巨人的铁手套在哪拿", "n_turns": 7,
                  "turns_available": 7 } ] }   // turns_available = answer 未被清空的行数
```

`client_id` 缺失 → 200 + 空列表（不 400，前端首访就是这种）。空列表时前端不展示恢复卡片。

### 4.3 `GET /api/sessions/{session_id}?limit=50`

```jsonc
{ "session_id": "…", "game": "baldurs_gate3", "progress": 2, "n_turns": 7,
  "turns": [ { "seq": 1, "ts": 1795000000, "question": "…", "answer": "…",
               "answer_expired": false, "turn_id": "…", "game": "baldurs_gate3" } ] }
```

`answer` 已过期 → `answer: null, answer_expired: true`，前端只渲染问题行 +「（这轮回答已过期）」。
不存在或已清理 → 404 `{"error": "session not found"}`。

### 4.4 `POST /api/sessions/{session_id}/reset` → 进度归零（`progress=NULL`）。

### 4.5 `DELETE /api/sessions/{session_id}` → 删整段（用户主动清），返回 `{"deleted": true}`。

不做 `GET /api/clients`、不做跨 client 查询；`client_id` 就是唯一凭证（32 hex 随机，
不可枚举），这等价于「无需登录的私密链接」。

---

## 5. api_server.py 改造点（服务器侧逐处）

| # | 位置 | 改动 |
|---|---|---|
| 1 | 顶部常量区（现 `TURNS_DIR` 附近） | 加 `SESSION_DB`、六个 env 参数解析；`import rag_agent.store as store` |
| 2 | `turn_record()` | 增加 `session_id` / `client_id` 两列透传（jsonl 也记，便于和 SQLite 对账） |
| 3 | `handle_ask` / `handle_ask_stream` 入口 | `resolve_session()` → 若 `SERVER_HISTORY=1` 用 `store.session_turns()` 覆盖 `history` |
| 4 | 调用 `ask()` 之后 | 组装 `answer` → `store.append_turn(...)`（非流式在 try 内、stream 在 `done` 之前） |
| 5 | `handle_ask_stream` 的 except 分支 | 断连/超时也落 `append_turn(answer_partial=1)`，`answer` 用已拼 `answer_parts` |
| 6 | 菜单/未识别轮 | **不落 turns**（避免污染历史），只 `touch_session` + 有 game 就 `update_game` |
| 7 | 响应 / `done` 事件 | 带上 `session_id`、`client_id` |
| 8 | `create_app()` | `store.init_db()`（失败只告警，服务照起）+ 注册 §4.2–4.5 四个路由 |
| 9 | 启动 + 每小时 | `asyncio.create_task(cleanup_loop())`：`store.purge()` 后 sleep 3600；`on_shutdown` 取消 |

关键骨架（照抄即可，细节随实现）：

```python
def resolve_session(body) -> tuple[str | None, str | None]:
    """返回 (client_id, session_id)。DB 不可用时返回 (None, None) → legacy。"""
    cid = body.get("client_id") or None
    sid = body.get("session_id") or None
    try:
        if not cid:
            cid = uuid.uuid4().hex
        if sid:
            row = store.get_session(sid)
            if row and (not body.get("client_id") or row["client_id"] == cid):
                store.touch_session(sid)
                return cid, sid
            if row:                       # 别人的 session，拒绝复用
                sid = None
        store.ensure_session(session_id=sid or uuid.uuid4().hex, client_id=cid)
        return cid, sid or store.newest_or_created(cid)   # 实现里直接返回新 sid
    except store.StoreError:
        logger.warning("session store 不可用，本轮走 legacy")
        return None, None
```

```python
async def cleanup_loop():
    while True:
        try:
            stats = await asyncio.to_thread(store.purge)
            if any(stats.values()):
                logger.info("session purge %s", stats)
        except Exception as e:
            logger.warning("purge 失败: %s", e)
        await asyncio.sleep(3600)
```

顺序纪律：**先扣额度/限速 → 再解析 session → 再跑 Agent → 再落库**。
落库异常只记日志，不能让已生成的答案变成 500。

---

## 6. 上下文装配（v1 原文窗口 / v2 加存根）

`GAMEGUIDE_SERVER_HISTORY=1` 时，`history` 由服务端生成：

```python
def assemble(session_id, *, max_turns=6, max_chars=6000) -> list[dict]:
    turns = store.session_turns(session_id, limit_turns=max_turns, max_chars=max_chars)  # v1
    # v2：receipts = store.session_receipts(session_id, limit=20)
    #     head = [{"role": "user", "content": "<存档点>" + 存根拼行}] if receipts else []
    return head + turns
```

- **v1（服务器可独立上线）**：只读原文窗口，等价于现在的 `trim_history(前端 history)`，
  唯一变化是数据源从请求体变成 SQLite。解决「刷新丢历史」，不解决「超长会话忘事」。
- **v2（Mac 补）**：更早的轮次压缩成 receipts 单行（`seq. 结论 [act N]`），拼在原文窗口之前，
  总长 ≤ 3000 字。存根确定性生成（PLAN §4），不经过 LLM。
- 装配结果**不含本轮 question**（本轮单独传），`_game_from_history` 的锚点因此少一条；
  游戏判定顺序改为：`sessions.game` → 本轮问题专有命中 → 装配历史。切换词/外部游戏名
  优先级不变（仍能切走），这条要在 `test_session_api.py` 里锁死。
- `history_full`（进度锚点兜底）在服务端权威模式下优先用 `sessions.progress`，
  `infer_progress` 只算本轮问题，防止历史被裁后进度回退。

---

## 7. 进度对象写入

- 每轮：`new = infer_progress(game, question, history)`；
  `progress = max(sessions.progress or 0, new.level or 0)`，`progress=0` 归一成 NULL。
- `progress_src` 只有在**本轮抬高了上界**时才改写（保留原始来源，便于追责）。
- 下调只认用户明说：`POST /api/sessions/{id}/reset`（前端给「重开一局」按钮）。
- 未识别游戏轮（menu）不更新 progress。

---

## 8. 前端改造（`weirdsnap.github.io/htmls/hollow_knight.html`）

改动点集中在 `<script>` 里，约 80 行：

1. 初始化（`data()` 之前同步执行）：
   ```js
   const CLIENT_KEY = 'gg_client_id', SESSION_KEY = 'gg_session_id';
   let clientId = localStorage.getItem(CLIENT_KEY);
   if (!clientId) { clientId = crypto.randomUUID().replace(/-/g, ''); localStorage.setItem(CLIENT_KEY, clientId); }
   let sessionId = localStorage.getItem(SESSION_KEY);   // 可能为 null
   ```
2. 首屏若 `sessionId` 为空但 `localStorage` 里有：读 `gg_last_session`，
   `GET /api/sessions/{id}` 成功则把 turns 渲染成消息（**必须走现有 markdown→HTML 渲染函数**，
   `answer_expired` 的轮次只渲染问题 + 灰字提示），失败则忽略。
3. 请求体加 `client_id: clientId, session_id: sessionId`。
4. 处理 `done` 事件时若带回 `session_id`：`sessionId = it; localStorage.setItem(SESSION_KEY, it); localStorage.setItem('gg_last_session', it)`。
5. 「新对话」按钮：`sessionId = crypto.randomUUID().replace(/-/g,'')`，写回 localStorage，
   清空 `messages` 到欢迎语（不调服务端删除，旧会话仍留在历史列表里）。
6. 侧栏「最近对话」：`GET /api/sessions?client_id=` 前 5 条，标题 + 相对时间，点开切 session。

前端不做的：不缓存答案副本（服务端权威），不自己算 token，不在本地裁剪。

---

## 9. 迁移与上线顺序（每步独立可回滚）

| 步 | 内容 | 谁 | 验收 | 回滚 |
|---|---|---|---|---|
| S0 | `store.py` + `test_store.py`（不接线） | Mac | `pytest tests/test_store.py` 全绿 | 无影响 |
| S1 | 双写：每轮落 sessions/turns，上下文仍用前端 history | 服务器 | jsonl 与 SQLite 行数逐日对账一致（脚本见 §10） | 删 env `GAMEGUIDE_SESSION_DB` 指向，或 `SERVER_HISTORY=0`+忽略表 |
| S2 | `SERVER_HISTORY=1`：装配改服务端 v1 | 服务器 | 刷新后续聊正确；`tokens_in` p90 ≤ 31,936（当前基线） | 改回 `0` 立刻恢复旧路径 |
| S3 | 前端改版 + 留存清理 cron | 用户 + 服务器 | 刷新、关浏览器、隔天回来三场景通过 | 前端回滚单文件；清理任务停掉 |
| S4 | receipts（v2）+ golden set 导出 | Mac | 50 行存根 + 3 轮原文，第 80 轮仍能引用第 5 轮结论 | receipts 表空 = v1 行为 |

S1 的对账命令（服务器侧，可写进 `scripts/analysis/`）：

```bash
# jsonl 当日轮次 vs SQLite 当日行数
python - <<'PY'
import json, sqlite3, time, pathlib
day = time.strftime("%Y-%m-%d")
lines = [json.loads(l) for l in pathlib.Path(f"data/turns/{day}.jsonl").read_text().splitlines() if l.strip()]
lines = [r for r in lines if r.get("ip") not in ("127.0.0.1",)]
db = sqlite3.connect("data/sessions.db")
n = db.execute("SELECT COUNT(*) FROM turns WHERE date(ts,'unixepoch','localtime')=?", (day,)).fetchone()[0]
print("jsonl", len(lines), "sqlite", n, "ok" if len(lines) == n else "MISMATCH")
PY
```

---

## 10. 测试清单（先写会失败的用例，再实现）

`tests/test_store.py`（纯 SQLite，tmp_path，秒级）：
- 建表幂等；重复 `init_db` 不报错
- `append_turn` 事务内 seq 连续；两连接并发（thread）不撞 seq、不丢行
- 同一 `session_id` 两轮后 `n_turns=2`、`title` 取首问
- `progress` 只增不减；传 None 不覆盖已有值
- `session_turns` 单条 >2000 字截断；总字符超预算从旧往新丢；只返回 `answer IS NOT NULL` 的轮次
- `purge`：答案 3 天清空、receipts 90 天删除、session 90 天级联删 turns
- `list_sessions` 只返回同 client；按 last_active 倒序；limit 生效
- 直接对已删除 session `append_turn` → `StoreError`（或返回 None），不产生孤儿行

`tests/test_session_api.py`（aiohttp 工厂模式，每次请求新建 app）：
- 无 `client_id`/`session_id` → 200，响应回传二者，DB 有新行
- 有 `session_id` 属于别的 client → 不复用，新建（不允许串号）
- `session_id` 过期（库里删掉）→ 自动新建，前端无感
- `GAMEGUIDE_SERVER_HISTORY=1` 时不读请求体 `history`（用 monkeypatch 断言装配入参）
- 菜单/未识别轮不落 turns；菜单轮不扣额度（沿用现状）
- 流式中断：`answer_parts` 半截落库且 `answer_partial=1`
- `GET /api/sessions` 缺 client_id → 空列表不报错
- `GET /api/sessions/{id}` 不存在的 id → 404
- store 抛 `StoreError` 时 `/api/ask` 仍 200（降级），日志有告警
- 游戏判定：`sessions.game` 优先，但本轮出现切换词仍能切走（防「刷新后无法切游戏」）

`tests/test_context_assembly.py`（纯函数）：
- 6 轮窗口内不裁剪；超长会话裁旧留新
- 装配结果不含本轮 question
- 装配后 `history_used_msgs` 与 `turns_available` 一致（观测字段一致性）

`tests/test_retention.py`：
- 时间注入（`now=` 参数）验证三条 cutoff 边界（第 3/4 天、90/91 天）
- purge 幂等，重复跑不报错

---

## 11. 风险与对策

| 风险 | 对策 |
|---|---|
| SQLite 写失败拖垮问答 | 全部落库走 try/except 降级；存储故障 → legacy 路径，绝不 500 |
| 磁盘增长 | 粗算：一轮 ~3KB，100 轮/天 → 1 年 <100MB；答案 3 天清理后主要剩存根 |
| `client_id` 被他人获知 | 32 hex 随机不可枚举；无跨 client 查询接口；session 行不存 IP |
| 答案里有用户隐私 | 原文 3 天，purge 用 `UPDATE ... answer=NULL`；jsonl 只存 200 字 answer_head（现状） |
| 服务端权威后模型看不到长历史 | receipts 存根兜底（S4）；原文窗口仍是 6 轮，等价今天 |
| 前端渲染历史走错管线 | 复用同一个 markdown→HTML 函数，`answer_expired` 单独分支；手动过一遍 3 个历史会话 |
| 双写期数据不一致 | S1 的对账脚本按天跑；不一致先查 handler 异常分支（超时/断连） |

---

## 12. 可选增强（默认不做，留口）

- **恢复码**：`GET /api/sessions?client_id=` 时若带 `recovery=1`，返回 6 位短码映射，
  换设备输入即可认领 client。默认关（要额外表 + 输入 UI，收益小）。
- **会话摘要**（PLAN §6 已推后）：等 S4 跑一段看存根够不够，够就永不做。
- **多会话并存**：数据模型已支持（sessions 多行），前端只暴露「最近 5 条」，不做命名/置顶。

---

## 13. 分工与预估

| 部分 | 归属 | 预估 |
|---|---|---|
| `store.py` + `test_store.py` + `test_retention.py` | Mac | 0.5–1 天 |
| `context.py` + receipts 生成 + `test_context_assembly.py` | Mac | 0.5 天（S4） |
| `api_server.py` 接线 + 4 个路由 + `test_session_api.py` | 服务器 | 1 天 |
| 前端 localStorage + 恢复 + 新对话 + 最近对话 | 用户 | 半天 |
| 上线验证、对账、跑基线 | 服务器 | 0.5 天 |

**建议排期**：S0+S1 一天内可完成（服务器侧可先写一个临时 `session_turns` 兜底不阻塞），
S2 当天可开；前端 S3 与 Mac 的 S4 并行。

---

## 14. 立即可以做的第一步（不改行为）

1. 服务器侧把 §2 的 DDL 和 §3 的 `append_turn/touch_session/list_sessions` 先落在
   `api_server.py` 内部的 `_session_store` 小模块里（S1 双写，不读），跑一天对账；
   —— 若 Mac 侧的 `store.py` 先到，直接切过去，不重复实现。
2. 同时把 `GAMEGUIDE_SERVER_HISTORY` 开关读进来（恒为 0 时无副作用），为 S2 留门。
3. 对账通过后把 §4.2–4.5 四个只读/管理路由挂上，前端就能先做「最近对话」，
   不依赖装配切换。

这样刷新丢历史这条（I-23）在不引入任何回答行为变化的前提下先止血。
