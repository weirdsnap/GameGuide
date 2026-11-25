# 聊天历史渲染与滚动方案（分批渲染 / 反向无限滚动 / 流式期间不拽底）

> 2026-11-25。前端实现在博客仓库 `htmls/hollow_knight.html`（助手页），这份文档解释
> 为什么那么改，以及什么时候该升级到虚拟列表。

## 1. 技术名词（你问的那个）

这块需求在业界不是一个词，是四个技术叠起来，各有名字：

| 需求 | 术语 | 说明 |
|---|---|---|
| 打开会话先只显示最近几条，滚到顶部再往前加载 | **反向无限滚动**（reverse infinite scroll / scroll-up pagination） | 普通无限滚动往底部加载新的，这个是往顶部加载旧的 |
| 顶部那个"触底自动加载"的监测点 | **哨兵 + IntersectionObserver**（sentinel） | 比监听 scroll 事件便宜，不动手算 scrollTop |
| 插入更早内容时画面别跳 | **滚动锚定**（scroll anchoring；CSS `overflow-anchor`，这里手写） | 插入前记 `scrollHeight`，插入后补差值 |
| 消息上千条才需要的优化 | **虚拟列表 / 窗口化渲染**（virtual scrolling / windowing，react-window、vue-virtual-scroller） | 只挂载视口附近的 DOM |

**当前体量不需要虚拟列表**：单会话最多几十轮（线上实测 p90 history 33 条消息），
分批渲染就够；虚拟列表会把"v-html + marked 渲染好的富文本高度"变成需要动态测量的麻烦事，
反而引入抖动。触发门槛写在 §5。

"前面的部分压缩展示"我按两种读法各给结论：
- 读法 A（采用）：更早的消息**先不渲染**，顶部放一个折叠隔条「↑ 更早的 N 条」，滚动到顶自动展开一批。
- 读法 B（不采用）：每条早期消息压成一行摘要。要做摘要得额外生成（LLM 或截断），
  和 `receipts` 那套确定性设计冲突，且信息损失不可逆。不做。

## 2. 现状问题（改之前，`htmls/hollow_knight.html`）

`restoreSession()`（第 513 行起）：

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

第 3 条是这次设计里顺带修的独立 bug，跟历史加载无关，但更烦人。

## 3. 方案

### 3.1 数据与渲染分离

- `allTurns`：`/api/sessions/{id}` 拿到的原始轮次，只存不改。
- `messages`：只装要渲染的窗口 + 欢迎语。
- `olderCount`：还有多少轮没渲染（顶部隔条文案用）。
- 新提问产生的轮次直接 push 进 `messages` 和 `allTurns` 末尾，保持单一源、刷新后顺序不变。

### 3.2 初始窗口

`VISIBLE_TURNS = 6`（12 条消息）。恢复时只渲染最后 6 轮，其余进 `olderTurns` 待加载。
首屏 DOM 稳定在 ~14 条消息，与历史长度无关。

### 3.3 顶部哨兵 + 反向无限滚动

```html
<div class="older-sentinel" ref="olderSentinel">
  <button v-if="olderCount > 0" class="older-btn" @click="loadOlder" :disabled="loadingOlder">
    {{ loadingOlder ? '正在加载…' : '↑ 更早的消息（' + olderCount + ' 轮）' }}
  </button>
  <span v-else-if="turnsTotal > VISIBLE_TURNS" class="older-done">已经到最早的对话了</span>
</div>
```

```js
const io = new IntersectionObserver(entries => {
    if (entries[0].isIntersecting) this.loadOlder();
}, { root: this.$refs.msgBox, rootMargin: '160px 0px 0px 0px' });
```

- `rootMargin` 向上预取 160px，滚到离顶还有一小段就开始加载，手感上不会"卡一下"。
- 按钮同时保留：IO 在某些 WebView 里不可靠，点击是兜底路径。
- `PAGE_TURNS = 6`，每次展开 6 轮。
- 加载完成、`olderCount` 归零后显示「已经到最早的对话了」，并 `io.disconnect()`。

### 3.4 滚动锚定（关键，不做就是"跳一下"）

```js
loadOlder() {
    if (this.loadingOlder || !this.olderCount) return;
    this.loadingOlder = true;
    const box = this.$refs.msgBox;
    const prevHeight = box.scrollHeight;
    const prevTop = box.scrollTop;
    const batch = this.olderTurns.splice(-PAGE_TURNS);     // 从最近的一批往回取
    this.messages.splice(1, 0, ...batch);                  // 插在欢迎语之后
    this.olderCount = this.olderTurns.length;
    this.$nextTick(() => {
        box.scrollTop = prevTop + (box.scrollHeight - prevHeight);
        this.loadingOlder = false;
        if (!this.olderCount) this.io && this.io.disconnect();
    });
}
```

插入前后补 `scrollHeight` 差值，视觉上停在原来那句话上，不会跳到新内容的开头。

### 3.5 流式期间不拽底

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

- 发送新问题时 `scrollBottom(true)` 强制到底。
- 流式 token 到达时用非强制版本：用户已经上翻就闭嘴，右下角显示一个「↓ 回到底部」小按钮。
- `onScroll` 里更新 `autoStick`，回到贴底状态后隐藏按钮。

### 3.6 不要假进度

前端**不**做"等 N 秒换个文案"的假计时器（用户明确否掉：假进度比没进度更伤信任）。
等待文案只由后端 `progress` 事件驱动（见 `STREAM_UX_PLAN.md` L2）：

| 事件 | 文案 |
|---|---|
| 无事件 | `思考中…` |
| `tool_start` + `search_knowledge_base` | `正在检索攻略库…` |
| `tool_start` + `query_structured_data` | `正在查数据表…` |
| `tool_start` + `show_database_schema` | `正在看数据结构…` |
| `tool_done` 且 round ≥ 2 | `已查 {round} 次，还在整理…` |
| 第一个 `token` | 切正文流式 |

后端 `progress` 还没上线之前，这段代码保持「思考中…」，不会退化成假文案。

## 4. 改动清单（`htmls/hollow_knight.html`）

1. data 增：`allTurns / olderTurns / olderCount / turnsTotal / loadingOlder / hasNewBelow / progressText`。
2. `restoreSession()`：构建全量后切成「欢迎语 + 最近 6 轮」，其余进 `olderTurns`；`scrollBottom(true)`。
3. 模板：`chat-messages` 顶部加哨兵；`@scroll="onScroll"`；loading 占位改为 `msg.placeholder`
   渲染 `{{ progressText }}`；右下角加「↓ 回到底部」。
4. `mounted`：建 `IntersectionObserver`（`$nextTick` 后建，`beforeUnmount` 断开）。
5. `loadOlder()` 带滚动锚定补偿。
6. `scrollBottom(force)` 改条件版；`updateAssistantMessage` 改调非强制版。
7. `switch (data.type)` 加 `progress` 分支，收到第一个 `token` 时把 `placeholder` 置 false。
8. CSS：`.older-sentinel / .older-btn / .older-done / .placeholder .dots / .to-bottom`，
   移动端断点里 `older-btn` 字号缩小。

## 5. 什么时候该升级到虚拟列表

满足任意两条再考虑：
- 单会话 `turns` > 120 轮（DOM 节点 > 300）；
- iOS Safari 首屏（已分批到 6 轮）仍然卡；
- 需要在一个会话里放图片/表格这类重型富文本。

升级方式：`vue-virtual-scroller` 或自写 windowing，配 `dynamic size` 模式按渲染后高度测量。
在此之前分批 + 补偿滚动是成本最低且不抖的方案。

## 6. 验收

| 项 | 判定 |
|---|---|
| 30 轮会话打开 | 首屏只渲染 6 轮 + 顶部隔条「↑ 更早的 24 轮」，位置在底部 |
| 上滚到顶 | 自动再加载 6 轮，视口停在同一句话（不跳） |
| 加载到最后一批 | 显示「已经到最早的对话了」，不再触发 |
| 流式输出时上翻 | 不被拽回底部，出现「↓ 回到底部」，点一下才回 |
| 新提问 → 刷新 | 顺序、条数不变（`allTurns` 同步） |
| 移动端 | 30 轮首屏无肉眼可见卡顿；隔条按钮可点 |

## 7. 分工

- 前端这项（本文 §4）：**服务器侧直接改**（用户 2026-11-25 授权），改完推送博客仓库，
  用户手机上看效果。
- `progress` 事件（§3.6 依赖）：Mac 侧 `src/rag_agent/multi_agent.py`，见 `MAC_SIDE_TASKS.md` P3-2。
- 前端在事件没来之前不显示假进度，所以两边可以分开上线，不会互相卡。
