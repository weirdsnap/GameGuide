# 博客访问统计方案（2026-11-26）

问题：博客是 GitHub Pages 静态站，没有自己的后端、拿不到访问日志，
「哪些文章被读、从哪里来、多少人」目前没有任何数据。本文定方案。

## 1. 现状约束（决定了方案形态）

- 站点托管在 GitHub Pages（`weirdsnap.github.io`），仓库里的内容就是线上内容，
  没有中间层可以插服务端逻辑。
- 全站只有 4 个 HTML 入口，其余内容都是 `.md` 由前端 `fetch` + `marked` 现渲染：
  - `index.html` → 首页/搜索（`scripts/index.js`）
  - `htmls/list.html?category=<id>` → 分类列表（`scripts/list.js`）
  - `htmls/blog.html?post=<path>` → 文章正文（`scripts/blog.js`）
  - `htmls/hollow_knight.html` → GameGuide 助手页
- 所以「一篇文章」在 HTTP 层面其实都是 `blog.html`，服务端日志（如果有）
  也分不出发的是哪篇——必须由前端上报文章标识。
- 页面都是手写 HTML，不是 Jekyll，没有统一 layout；但每个页面各自只引一个
  业务 JS，注入成本很低。
- GameGuide 的 API 在 `api.weirdsnap.top:8443`，CORS 已是 `*`，博客可以直接
  `POST` 过去。

## 2. 三个数据源候选

| 方案 | 能拿到什么 | 致命缺点 |
| --- | --- | --- |
| A. GitHub Traffic API（`/repos/{o}/{r}/traffic/views`） | 全站 PV/UV 总量、逐日序列 | 只保留 **14 天**；无单页明细（`/traffic/popular/paths` 只有 top10，且 SPA 下所有文章都是 `blog.html`）；需要 push 权限 token |
| B. 自建 beacon（前端上报到自己的 API） | 文章级 PV/UV、来源域名、设备档位、时间分布 | 只能统计开了 JS 的访客；要自己写端点、存储、看板 |
| C. 第三方（GoatCounter / Plausible / 自建 umami） | 现成面板 | 多一个外部依赖；数据在别人机器上；国内访客再加一个跨域脚本 |

**选 B 为主，A 仅作总量交叉验证**（脚本在本地跑，一次性，不入服务端）。
C 排除：站点已经要靠代理访问，再加外部脚本只会更慢，且数据落第三方的账不值。

## 3. 数据模型

新增 `data/blog.db`（SQLite，gitignore，路径可用 `GAMEGUIDE_BLOG_DB` 覆盖，
与现有 `GAMEGUIDE_SESSION_DB` 同一套路）。

```sql
CREATE TABLE IF NOT EXISTS blog_hits (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  ts      INTEGER NOT NULL,   -- unix 秒
  day     TEXT    NOT NULL,   -- YYYY-MM-DD（Asia/Shanghai，读时聚合用）
  path    TEXT    NOT NULL,   -- 归一化目标：'/'、'/list'、'/blog'、'/hk'、'/post/<slug>'
  kind    TEXT    NOT NULL,   -- home / list / post / hk / other
  ref     TEXT,               -- 只存来源域名（'github.com'、'google.com'、NULL）
  device  TEXT,               -- mobile / tablet / desktop
  visitor TEXT,               -- hmac_sha256(ip|day, salt)[:16]，按天轮换
  flagged INTEGER NOT NULL DEFAULT 0  -- 1 = 可疑（UA 像机器人 / Origin 不在白名单）
);
CREATE INDEX IF NOT EXISTS idx_hits_day  ON blog_hits(day);
CREATE INDEX IF NOT EXISTS idx_hits_path ON blog_hits(path, day);
```

几条口径，写死在实现里：

1. **不存原始 URL、不存完整 referrer、不存完整 IP**。`path` 是前端归一化后的
   短标签（`?post=posts/ai/x.md` → `/post/posts/ai/x.md`），referrer 只取 `host`。
2. **visitor 摘要按天轮换盐**：UV 按天统计本来就够用，跨天无法把同一人串起来，
   属于「设计上就不可能画像」，不是靠承诺。
3. **不写 cookie、不读 localStorage**。浏览器侧完全无状态，因此不需要横幅。
4. 明细保留 90 天；聚合（按 day × path 的 PV/UV）永久保留一张
   `blog_daily` 汇总表，删明细不影响历史曲线——与全站「不物理删用户数据」
   的口径一致（这里只是聚合归档，不丢结论）。
5. 上报失败一律静默，不影响页面任何行为。

## 4. 端点契约

### POST /api/hit

请求体 `text/plain`，内容是一行 JSON（用 `text/plain` 是为了避开 CORS 预检）：

```json
{"p":"/post/posts/ai/x.md","k":"post","r":"github.com","w":390}
```

- `p` 必填，长度 ≤200，只允许 `/` 开头的短标签；
- `w` 是视口宽度，服务端折成 `device`（<768 mobile、<1200 tablet、其余 desktop）；
- 响应 `204 No Content`，无 body。
- 限速：同一 IP 每分钟 60 次，超出直接 204 丢弃（不回 429，避免给爬虫反馈）；
- UA 命中 `bot|spider|crawl|preview|monitor|curl|python-requests` 等 → 落库时
  `flagged=1`（保留数据，统计时默认剔除，方便复核误杀）；
- Origin 不在 `{https://weirdsnap.github.io, null}` 且非 localhost → 同样 `flagged=1`；
- **不扣每日额度、不进 turn 记录、不写 api_server.log 的 info 级**（只在 debug 打）。

### GET /api/dashboard/blog?days=7

只读聚合，返回：

```json
{"days":7,"pv":123,"uv":45,"series":[{"day":"2026-11-26","pv":12,"uv":9}],
 "top_posts":[{"path":"/post/posts/ai/x.md","pv":8,"uv":6}],
 "refs":[{"ref":"github.com","pv":20}],"devices":{"mobile":0.6,"desktop":0.35,"tablet":0.05},
 "flagged":3}
```

- 统计口径与现有 `scripts/analysis/report.py` 同一原则：**统计只写一处**——
  在 report 里加 `blog_summary(rows, days)` 纯函数，端点与看板都调它；
- 默认剔除 `flagged=1` 的行，返回数量让页面可以提示「另有 N 条可疑记录」。

## 5. 前端 beacon（`scripts/hit.js`，约 40 行）

```js
(function () {
  try {
    var API = 'https://api.weirdsnap.top:8443/api/hit';
    var q = new URLSearchParams(location.search);
    var path = '/', kind = 'other';
    if (location.pathname.indexOf('/htmls/blog.html') >= 0) {
      path = '/post/' + (q.get('post') || ''); kind = 'post';
    } else if (location.pathname.indexOf('/htmls/list.html') >= 0) {
      path = '/list'; kind = 'list';
    } else if (location.pathname.indexOf('/htmls/hollow_knight.html') >= 0) {
      path = '/hk'; kind = 'hk';
    } else { path = '/'; kind = 'home'; }
    if (!path || path.length > 200) return;
    var ref = '';
    try { if (document.referrer) ref = new URL(document.referrer).host; } catch (e) {}
    if (ref === location.host) ref = '';          // 站内跳转不算来源
    var body = JSON.stringify({ p: path, k: kind, r: ref, w: window.innerWidth });
    if (navigator.sendBeacon) {
      navigator.sendBeacon(API, new Blob([body], { type: 'text/plain' }));
    } else {
      var x = new XMLHttpRequest();
      x.open('POST', API, true);
      x.setRequestHeader('Content-Type', 'text/plain');
      x.send(body);
    }
  } catch (e) {}                                   // 任何异常都不影响页面
})();
```

注入：4 个 HTML 各加一行 `<script defer src="<相对路径>/scripts/hit.js"></script>`。
`defer` + `sendBeacon` 保证不阻塞渲染、关闭页面也能发出。

已知不覆盖：JS 关闭、adblock 拦跨域、纯爬虫（GH Pages 会吃到它们的请求但我们看不到）。
所以数字应解读为「人类访客的下界」，与 GH Traffic 的总量差值就是这部分。

## 6. 看板接入（`web/dashboard.html`）

在现有三块（总额/趋势/轮次表）之后加第四块「博客访问」：

- 一行三卡：7 日 PV、7 日 UV、7 日活跃文章数；
- 日 PV/UV 双折线（手写 SVG，与现有趋势图同一套画法）；
- top 10 文章表（path 短标签 + PV + UV），点击展开到 `/api/dashboard/blog/<path>` 可后置；
- 来源域名与设备档位的横向条形（比例，不做地图/国家，Caddy 层才有 `CF-IPCountry`，
  现在不采）。

## 7. GH Traffic 交叉验证（一次性，本地跑）

```bash
gh api repos/weirdsnap/weirdsnap.github.io/traffic/views --jq '.views[]|[.timestamp,.count,.uniques]|@tsv'
```

拿 14 天总量与我们 beacon 的同期总量对比，写进本文「实测记录」小节，
差异来源逐条解释（JS 关闭 / 爬虫 / 代理缓存 / GH 自身过滤）。这个脚本不入服务端
（服务器没有 token，也不需要）。

## 8. 上线顺序（每步可单独回滚）

- **S0** 建表 + `POST /api/hit` + 单测（伪造 aiohttp 请求，不写真库）+ 手动 curl 验证
  （由用户在服务器/本机跑，助手侧 curl 会被自己的 SSRF 守卫拦）；
- **S1** 博客侧 `scripts/hit.js` + 4 个 HTML 各一行引用，push（GitHub Pages 自动发），
  观察 1 天；
- **S2** `report.blog_summary` + `GET /api/dashboard/blog` + 看板第四块；
- **S3** GH Traffic 交叉验证，把差值写进本文。

验收口径：

- S0：表建好、`POST /api/hit` 返回 204、库里多一行、`flagged` 规则生效；
- S1：真实打开一篇文章后，库里出现 `/post/posts/...` 一行；关掉 API 时页面无任何报错；
- S2：看板数字与直接 `sqlite3 data/blog.db "select day,count(*) ..."` 一致；
- S3：Beacon 总量 ≤ GH Traffic 总量，且差距有解释。

## 9. 明确不做

- 不做用户级追踪（无 cookie、无 fingerprint、无跨会话 ID）；
- 不做实时看板（按天聚合就够，实时只会让页面反复请求）；
- 不做文章内阅读时长/滚动深度（要先埋点再算，收益不抵复杂度，等有需要再说）；
- 不把 `hollow_knight.html` 的提问流量混进来——那部分已经有 `data/turns/*.jsonl`；
- 不为统计改博客架构（不引 Jekyll/Hugo，不动现有多文件结构）。
