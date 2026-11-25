# 聊天历史渲染与滚动方案（窗口化无限滚动 / 双向自动加载 / 流式不拽底）

> 2026-11-25 初版（分批渲染 + 顶部按钮），当天按用户要求改成**窗口化无限滚动**：
> 不要按钮、全自动加载、并且滚动时清理远端 DOM。前端实现在博客仓库
> `htmls/hollow_knight.html`（助手页），本文解释为什么这么改、踩过哪些坑。

## 1. 技术名词

这块需求在业界不是一个词，是几个技术叠起来：

| 需求 | 术语 | 说明 |
|---|---|---|
| 滚到边界自动加载、不要按钮 | **无限滚动**（infinite scroll） | 用 scroll 事件判边界，不用哨兵按钮 |
| 往顶部加载更早的消息 | **反向无限滚动**（reverse infinite scroll） | 普通无限滚动加载更新的，这个加载更早的 |
| 只保留视口附近 DOM、远端卸载 | **窗口化渲染 / 虚拟列表**（windowing / virtual scrolling） | 卸载后用占位高度撑住滚动条 |
| 卸载或插入内容时画面别跳 | **滚动锚定**（scroll anchoring；CSS `overflow-anchor`，这里手写） | 记锚点元素位置，改完把 scrollTop 补回去 |

第一版用的是「顶部哨兵按钮 + 分批展开」，用户否掉了：要的是电商那种全自动的手感。
现在四个都用上了，区别是窗口化是自己手写的、带动态高度测量，没有引第三方库。

## 2. 现状问题（改之前，`htmls/hollow_knight.html`）

`restoreSession()`：

```js
const msgs = [this.messages[0]];
for (const t of turns) {
    msgs.push({ ...user... });
    msgs.push({ ...assistant, content: DOMPurify.sanitize(marked.parse(t.answer...)) });
}
this.messages = msgs;
this.$nextTick(() => this.scrollBottom());
```

三个问题：

1. **一次性全量渲染**。30 轮 = 60 条消息节点，每条回答内部还有 p/ul/li/strong。
   `marked.parse + DOMPurify.sanitize` 在恢复时对每轮各跑一次，iOS Safari 上首屏明显卡白。
2. **恢复后立刻 `scrollTop = scrollHeight`**。字体、图片、`v-html` 布局在下一帧才稳定，
   高度变化会让"已经到底"变成"差一屏"，用户看到的是停在中间某处。
3. **流式期间每 token 都拽底**。`updateAssistantMessage()` 结尾无条件 `scrollBottom()`，
   回答正在输出时用户想往上翻前面的内容，会被每个 chunk 拽回底部，翻不动。

第 3 条是顺带修的独立 bug，跟历史加载无关，但更烦人。

## 3. 方案

### 3.1 数据与渲染分离

- `messages`：**全量**消息（欢迎语 + 所有轮次），是渲染和上下文构建的唯一数据源。
- `chunkStart / chunkEnd`：当前挂载到 DOM 的窗口 `[chunkStart, chunkEnd)`。
- `visibleMessages`（computed）：`messages.slice(chunkStart, chunkEnd)`，模板只 v-for 这个。

关键点：**卸载 DOM 不影响发给模型的上下文**。上下文一律从全量 `messages` 构建，
所以窗口化只解决渲染，不动业务逻辑。第一版把未渲染消息另存在 `olderTurns` 里、
拼历史时还要单独拼进去——窗口化之后这份重复数据就没必要了。

### 3.2 初始窗口与常量

```js
const WIN_MSG  = 12;   // 首屏渲染条数（6 轮）
const STEP_MSG = 12;   // 每次自动加载 / 卸载的条数
const MAX_WIN  = 36;   // 窗口上限，超出后卸载离视口远的一端
const EDGE_PX  = 240;  // 距渲染区边界多远触发加载
const GAP_PX   = 14;   // 与 CSS .chat-messages 的 gap 一致
const EST_H    = 110;  // 未测量条目的估算高度（只用于占位）
```

恢复会话时 `chunkEnd = messages.length`、`chunkStart = max(0, length - WIN_MSG)`，
DOM 稳定在 12 条，与历史长度无关。

### 3.3 双向自动加载（不用哨兵）

哨兵 + IntersectionObserver 那套有个问题：卸载/插入是同一批 DOM 操作，
IO 的回调时机和窗口改动互相打架。直接判 scroll 边界更可控：

```js
onScroll() {
    const box = this.$refs.msgBox;
    if (this.chunkStart > 0 && box.scrollTop < this.padTop + EDGE_PX) return this.loadEarlier();
    const bottomEdge = box.scrollHeight - this.padBottom - EDGE_PX;
    if (this.chunkEnd < this.messages.length && box.scrollTop + box.clientHeight > bottomEdge) {
        return this.loadLater();
    }
    if (this.chunkEnd - this.chunkStart > MAX_WIN) this.trimWindow();
}
```

**必须带上 `padTop` / `padBottom`**（坑 1）。卸载掉的内容是靠容器 padding 撑高度的，
`scrollTop = 0` 落在空白里，真正的内容从 `padTop` 处才开始。第一版忘了加这个偏移，
用户得先滚过一整屏空白才触发加载，手感直接废掉。

### 3.4 远端卸载与占位

```js
padTop() {    // computed
    let h = 0;
    for (let i = 0; i < this.chunkStart; i++) h += this.hCache[this.messages[i].id] || EST_H;
    return Math.round(h);
}
```

- `hCache`：`msg.id -> 实测高度 + GAP_PX`，由 `measure()` 在窗口变动前后写入。
- 占位不用 spacer div 而用容器 `padding-top / padding-bottom`：`.chat-messages` 是
  flex column + `gap`，插一个 spacer div 会多吃一个 gap，高度算不准；padding 不受 gap 影响。
- 窗口上限 `MAX_WIN`：`loadEarlier` 时同帧把尾部卸一段（`chunkEnd -= STEP_MSG`），
  `loadLater` 时卸头部。**流式回答期间不卸尾部**——正在生成的那条就在尾部。

### 3.5 滚动锚定：一次修正不够（坑 2，实测偏 290px）

锚定逻辑是「记下视口顶部那条消息的位置，改完窗口再把它挪回原位」。
第一版只做了一次补偿，用 `padTop` 差值算：

```js
box.scrollTop = prevTop + (this.padTop - prevPad);   // 不够
```

偏 290px。原因：`padTop` 里那些**从未渲染过**的条目只有估算高度（`EST_H`），
而新接进窗口的条目是实测高度，两者不相等，差值就留在画面上了。

正确做法是**按实测坐标补偿，并且分两步**：

```js
applyWindow(mutate) {
    const box = this.$refs.msgBox;
    this.measure();                       // ① 旧窗口实测
    const a = this.anchorBefore();        // ② 记锚点（getBoundingClientRect，不依赖估算）
    mutate();                             // ③ 改 chunkStart/chunkEnd
    this.$nextTick(() => {
        this.restoreAnchor(a);            // ④ 先钉一次（此时占位还是估算值）
        this.measure();                   // ⑤ 新条目写进缓存 → 占位高度变化
        this.$nextTick(() => {
            this.restoreAnchor(a);        // ⑥ 再钉一次，估算误差被抹掉
            this._inWinOp = false;
        });
    });
}
```

`loadEarlier / loadLater / trimWindow` 全部走这一个入口，窗口改动只有一处实现。

### 3.6 节流只能用「进行中」标志，不能用时间（坑 3）

第一版是 120ms 时间节流。用户一把甩到顶时 scroll 事件很密，被丢掉的事件**不会重来**——
停在顶部不动就再也加载不了，只能反复甩。改成状态锁：

```js
winGuard() {
    if (this._inWinOp) return false;
    this._inWinOp = true;
    return true;
}
```

在 `applyWindow` 的第二次 `nextTick` 里解锁，即"上一次窗口改动落地后才允许下一次"。

### 3.7 流式期间不拽底

```js
isNearBottom() {
    const box = this.$refs.msgBox;
    if (!box) return true;
    return box.scrollHeight - box.scrollTop - box.clientHeight < 120;
}
scrollBottom(force = false) {
    if (!force && !this.isNearBottom()) { this.hasNewBelow = true; return; }
    this.$nextTick(() => { const b = this.$refs.msgBox; if (b) b.scrollTop = b.scrollHeight; });
}
```

- 发送新问题时 `scrollBottom(true)` 强制到底，并把窗口拉回末尾
  （用户上翻过时也要能看到自己刚发的问题）。
- 流式 token 到达时用非强制版本：用户已经上翻就闭嘴，右下角显示「↓ 回到底部」。

### 3.8 不要假进度

前端**不**做"等 N 秒换个文案"的假计时器（用户明确否掉：假进度比没进度更伤信任）。
等待文案只由后端 `progress` 事件驱动（`STREAM_UX_PLAN.md` L2）：

| 事件 | 文案 |
|---|---|
| 无事件 | `思考中…` |
| `tool_start` + `search_knowledge_base` | `正在检索攻略库…` |
| `tool_start` + `query_structured_data` | `正在查数据表…` |
| `tool_start` + `show_database_schema` | `正在看数据结构…` |
| `tool_done` 且 round ≥ 2 | `已查 {round} 次，还在整理…` |
| 第一个 `token` | 切正文流式 |

事件没到就保持「思考中…」，不退化。

## 4. 改动清单（`htmls/hollow_knight.html`）

1. 常量：`WIN_MSG / STEP_MSG / MAX_WIN / EDGE_PX / GAP_PX / EST_H`。
2. data：`chunkStart / chunkEnd / hCache / _inWinOp / hasNewBelow / progressText`；
   删掉 `olderTurns / olderCount / everHadOlder / loadingOlder / io`。
3. computed：`visibleMessages / padTop / padBottom`。
4. 模板：`chat-messages` 绑定 `paddingTop / paddingBottom`，v-for `visibleMessages`，
   每条加 `:data-mid="msg.id"`（测量和锚定都靠它定位）；顶部提示从按钮改成一行灰字。
5. 方法：`measure / anchorBefore / restoreAnchor / applyWindow / loadEarlier / loadLater /
   trimWindow / winGuard`；`resetWindow` 取代 `resetOlder`。
6. `restoreSession()`：全量进 `messages`，窗口取末尾 12 条，`scrollBottom(true)` 后 `measure()`。
7. `sendQuestion()`：窗口拉回末尾（`chunkEnd = messages.length`）。
8. `onScroll`：边界判断带 `padTop / padBottom`。
9. CSS：删 `.older-sentinel / .older-btn / .older-done`，加 `.top-note`。

## 5. 已知边界

- **估算高度误差**：从未渲染过的条目只能用 `EST_H`，用户滚一遍之后全部转成实测值、
  误差收敛到 0。在此之前滚动条长度会有小幅波动（实测多轮移动后总高度差 ≤ 3 条）。
  单条消息高度差异大（短问题 + 长回答），想彻底消除只能预渲染测量，代价比收益大。
- **窗口上限会卸掉远端**：滚到最早时尾部已经被卸载，「已经到最早的对话了」上面
  看不到最新的几条——往上滚回来自动接回。
- **降级**：`measure()` 依赖 `offsetHeight`，某些隐藏状态（`display:none`）测出来是 0，
  代码里 `if (h > 0)` 挡掉了，不会把占位写成 0。
- **体量**：单会话上限 50 轮（服务端 `session_turns` 限制），窗口化后 DOM 恒定 ≤ 36 条，
  不需要再上虚拟列表库。

## 6. 验收

离线逻辑测试：`tests/test_chat_window.js`（从页面里抽出真实的 methods / computed，
配假布局模型跑窗口移动，覆盖初始窗口、滚到最早、窗口上限、锚定不跳、
尾部接回、高度守恒、短会话、流式期间不卸尾 8 组共 17 条断言）。

| 项 | 判定 |
|---|---|
| 30 轮会话打开 | 首屏 DOM 只挂 12 条，位置在底部，顶部一行灰字 |
| 上滚到顶 | 自动接进更早 12 条，视口停在同一句话（±2px），无按钮 |
| 继续上滚 | 一直加载到最早一条，窗口始终 ≤ 36 条 |
| 再滚回底部 | 尾部自动接回，能看到最新回答 |
| 流式输出时上翻 | 不被拽回底部，出现「↓ 回到底部」，点一下才回 |
| 流式中上滚 | 尾部不卸载（正在生成的那条不会被卸掉） |
| 新提问 → 刷新 | 顺序、条数不变 |
| 移动端 | 30 轮首屏无肉眼可见卡顿 |

## 7. 分工

- 前端这项：**服务器侧直接改**（用户 2026-11-25 授权），改完推博客仓库，用户手机上看效果。
- `progress` 事件：Mac 侧 `multi_agent.ask_stream` 已实现（f2fb79f），
  服务器侧 `api_server.handle_ask_stream` 的转发分支已补（2026-11-25），
  补完必须重启 API 才生效——`src/` 改动不会热加载。
