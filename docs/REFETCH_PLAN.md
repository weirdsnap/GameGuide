# 重抓计划：丝之歌 + 泰拉瑞亚中文（2026-11-16）

只处理两件事：丝之歌整站碎片、泰拉瑞亚中文库。其余 6 款游戏的 wiki_data 已就地清洗完成，
不需要重抓（清洗结果见 `docs/CURRENT_ISSUES_2026-11-16.md` 的「数据清洗」节）。

## 一、为什么必须重抓

**丝之歌（`games/silksong/data/wiki_data.md`）**：采集脚本 `scripts/fetch/silksong.py` 的
`fetch()` 拿整页渲染 HTML，用 `<[^>]+>` → 空格、`\s+` → 空格压成**单行**。页面底部的
navbox 导航条是**嵌套表格**，正则 `re.sub(r'<table class="infobox[^>]*>.*?</table>')` 只删得掉
外层，内层导航链接全部留在正文里。后果：

- 单页中位 **161KB**、**88.8% 的页超过 20KB**（正常 wiki 页中位 1–4KB）；
- 一页里混进几十个别的页面标题，于是「Absolute Radiance 页开头讲空洞骑士 Beta 发售信息」、
  「Baldur Shell 页正文是 Zote」；
- 段落结构全丢（整页一行），分块 800/160 切出来的是随机语料，108MB 向量库不可用。

闸门复核（`refetch_wiki.py` 的 audit 拿旧库跑）：`median_chars=161091 ❌`、
`share_over_20k=0.888 ❌`，判定不通过。

**泰拉瑞亚中文（`games/terraria/data/wiki_data_zh.md`）**：`terraria.fandom.com/zh/api.php`
回的**正文是英文**（`Desktop / Console / Mobile / Switch -Only Content : This information
applies only to…`），标题倒是中文（`0x33的飞行员风镜`）；分类还用英文名（`Armor_items` 等）去查
中文站，命中结果接近随机。91 节里标题大量是 `listmap:list=885` / 纯版本号，正文是表格碎片。
**源就错了**，不是清洗能修的——清洗只会把英文段落去重掉，剩下的还是错内容。

## 二、脚本

`scripts/fetch/refetch_wiki.py`（新增，`wiki.py` 不动，它抓的 6 个库是好的）

和旧脚本的差别：

1. 正文限定在 `<div class="mw-parser-output">` 内，侧栏/页脚/分类栏不进来；
2. 用 `HTMLParser` + 跳过节栈剥离，**嵌套 navbox 也能整块删干净**（旧脚本正则删不平）；
3. 节级删除 `Navigation` / `Gallery` / `References` / `External links` / `See also`，
   保留 `Trivia`、`Lore`、`Strategy` 等有信息量的节；
4. 抓完自动跑质量闸门，不达标退出码 2 且不替换文件；
5. 断点续传（`.refetch.state.json` 按页记录），中断重跑同一条命令；
6. 默认写 `wiki_data.refetch.md`，不动正式文件；`--replace` 才替换，替换前自动 `.bak-refetch`。

依赖只有 `requests`。

## 三、执行顺序（都在 Mac 上跑，服务器连不上 fandom/wiki.gg）

### 丝之歌

```bash
cd ~/learning/GameGuide

# 1. 探独立站（2025 首发站，内容最全）
python3 scripts/fetch/refetch_wiki.py --game silksong \
    --api https://silksong.wiki.gg/api.php --probe

# 探到分类后再指定（把 probe 打印的分类名填进去）
python3 scripts/fetch/refetch_wiki.py --game silksong \
    --api https://silksong.wiki.gg/api.php \
    --categories "Areas,Bosses,Enemies,Items,NPCs,Weapons,Tools,Abilities"

# 2. 独立站不行就回退原站（registry 默认），靠标题后缀过滤
python3 scripts/fetch/refetch_wiki.py --game silksong --suffix "(Silksong)" --probe
python3 scripts/fetch/refetch_wiki.py --game silksong --suffix "(Silksong)"
```

`--probe` 只抓前 10 页并打印质检和正文样例，先看样例像不像正经 wiki 正文，再整站跑。

### 泰拉瑞亚中文

```bash
# 1. 官方中文（wiki.gg 迁移后的中文站）
python3 scripts/fetch/refetch_wiki.py --game terraria --lang zh \
    --api https://terraria.wiki.gg/zh/api.php --probe

# 2. 灰机 wiki（中文 Terraria 社区主站）
python3 scripts/fetch/refetch_wiki.py --game terraria --lang zh \
    --api https://terraria.huijiwiki.com/api.php --probe
```

两个源的分类命名不同，用 `--probe` 先看 `📂 分类:` 行报出来的页数，再决定
`--categories` 填什么（中文站常见分类：`护甲物品`、`饰品`、`武器物品`、`Boss`、`敌怪`、
`物品`）。都拿不到就**放弃中文库，只留英文**——错的 zh 库比没有更糟，会让中文提问命中
英文碎片。

## 四、验收阈值（脚本自动判定，不通过不写文件）

| 指标 | 含义 | 阈值 |
|---|---|---|
| `median_chars` | 单页正文中位字符数 | 500 – 8000 |
| `share_over_20k` | 超 20KB 页面占比 | ≤ 5% |
| `share_under_100` | 近乎空页占比 | ≤ 12% |
| `paragraph_rate` | 含空行分段的页面占比 | ≥ 70% |
| `nav_pollution_median` | 抽样 20 页，正文里出现的「其他页面标题」个数中位 | ≤ 6 |
| `cjk_ratio`（仅中文源） | 正文中汉字占比 | ≥ 25% |

额外人工抽检（脚本管不到）：随机 5 页，看开头是否是本页主题、`## ` 节标题是否是正文小节。

## 五、抓完之后

1. 对新文件跑一遍清洗（清洗器对已干净的文件是幂等的，只会做事后压缩）：
   `python3 scripts/build/clean_wiki.py --game silksong --write`
2. `python3 scripts/build/audit_clean.py --game silksong --rows 0` 看残留是否为 0；
3. 契约检查：`load_wiki_documents()` 文档数与页数一致；
4. `--replace` 覆盖正式文件 → commit → push；
5. Mac 重建向量库（`python3 scripts/tool/mac_build.py --game silksong`）→
   `scripts/deploy/vectorstores.sh` 上传 → 服务器 `bash scripts/deploy/api.sh` 重启；
6. 线上抽 5 个问题对照旧答案（丝之歌：`蕾丝怎么打`、`丝线在哪刷`；泰拉瑞亚中文：
   `克苏鲁之眼怎么召唤`、`肉山怎么打`）验证命中内容真的变干净了。

## 六、回滚

正式文件只在 `--replace` 时被覆盖，且自动生成 `*.bak-refetch`。
向量库回滚用上一版 `games/<game>/vectorstore/`（Mac 保留），重传后重启即可。

## 七、执行复盘（2026-11-23 ~ 11-25，已收官）

两个库都按本计划抓完并替换，但过程比计划曲折，记录如下。

**实际源选择**

- 丝之歌：没用 silksong.wiki.gg，用 **hollowknight.fandom.com 分类直采**（靠分类过滤
  而不是 `--suffix`）。结果 79/81 页、99KB 干净正文（旧文件 29MB，b7cde90），
  六项质检全绿，抽样 8 页人眼比对对题、无 HK 污染。
- 泰拉瑞亚中文：**terraria.wiki.gg/zh 整站重抓成功**（灰机 huijiwiki 没用上）。
  3,031 页（0395909）。八项质检七项原生通过；`nav_pollution_median=13` 超阈值
  是**结构性误报**——列表页枚举全条目 + 正文密集互链是泰拉瑞亚中文站的自然形态
  （旁证 shared_para_rate=0、导航表清理 0 处），用 `--nav-threshold 20` 显式放宽。

**抓的过程中踩的坑（都已修为脚本能力）**

1. **Cloudflare 滴漏陷阱**：fandom 长连接会降速到滴漏但不报错，脚本假死。
   修：每 200 页强制换连接 + SIGALRM 单页 90s 硬超时，超时抛 `PageStallError`
   穿透 urllib3（f0796a6、c69da64）。
2. **macOS mDNS 解析挂死**：加看门狗线程兜底（a664173）。
   ⚠️ 这台 Mac 电池供电 1 分钟自动休眠也会冻住抓取——长任务必须 `caffeinate -i` 包裹。
3. **断点太稀**：改每 100 页落盘（a8e77f5），中断重跑同一条命令即可续。
4. **真 stub 页误杀**：剥离 infobox 后不足 80 字的页面被丢，阈值降到 30 字补抓
   （81941ce）；剩 2 个纯 infobox 空页（Claw Mirror / Smelt Shoveler）放弃。
5. **解析器盲区**：`dialogue-*` 组件、`portable-infobox`、跨页共享模板会漏剥
   （7ed127b、2f2b751）。

**抓完之后又暴露的下游坑（最重要的一条教训）**

- **分页器丢页边界（P0-4）**：`refetch_wiki.py` 用 `---` 分隔页面，页内小节也用 `##`，
  而 `mac_build.py` / `ingest_game.py` 只按 `## ` 切——terraria 31,248 块里 48.1% 的
  title 是「制作/历史/花絮」这类小节名，mhw 整库塌成一页。修复：先按 `^---$` 切页、
  小节继承页面 title（e19b994，契约测试 `tests/test_wiki_page_split.py`）。
- **教训**：抓完数据质检全绿 ≠ 检索可用。文件级闸门（本计划第四节）管不到建库分页。
  以后链路验收必须以 `scripts/eval/retrieval_smoke.py` 的真实 top-k 命中为准。

**网络结论（定稿）**

- Mac 挂代理（`https_proxy=http://192.168.5.1:7890`）可抓 fandom；
  **wiki.gg/zh 必须直连**（走代理 403），用 `no_proxy='*'` 覆盖。
- 服务器侧 fandom 000 / wiki.gg 403 / huijiwiki 403，采集固定归 Mac，不再尝试服务器采集。
