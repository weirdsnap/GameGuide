# 服务器端待办清单（交接给服务器侧 agent）

> 来源：Mac 侧，2026-11-22。与 `docs/MAC_SIDE_TASKS.md` 互为镜像——那份是服务器留给
> Mac 的，这份是 Mac 留给服务器的。
>
> 分工依据 AGENTS.md：`api_server.py`、部署重启、线上验证、`scripts/analysis/`、
> 日志/turns 数据分析归服务器侧；`src/rag_agent/`、`tests/`、`scripts/tool|build|fetch|enrich|eval`
> 归 Mac 侧。交叉修改同一文件时遵守 AGENTS.md 的「防冲突纪律」（开工必拉、push 前再拉）。

## P0-1 部署 Mac 侧最新提交并验证（f55ea5f）

Mac 侧已完成 I-16~I-20 + I-02 全部修复并推送（`f55ea5f`，本地 224 passed / routing 13/13）：

- **I-16** `check_citation` 假阳性：标记词（来源/知识库/数据库/条目）需紧跟引号才算引用，
  正文引号里的人名不再被当成伪造来源。——修完后 `bg3-lorrokan-act2` 的 full 报告里
  「伪造来源：洛若坎」这类噪音应消失，剩下的 Nightsong 才是真越界读数。
- **I-17** `build_common_rules` 三个进度轴（act/chapter/ability）删掉「先追问进度」，
  统一为「报过进度是硬上界；未报不追问，按条目 `[act N]`/问题所指作答」。
  与 `progress_prompt_note`、SPOILER_CONTROL_PLAN 的口径不再打架。
- **I-18** `_row_act` 多地名取 `max(act)`（防剧透宁多屏蔽不漏）；
  `_format_db_result` 数值筛选结果每行也附 `[act N]`。
- **I-20** 评测 `run_full` 的 context 占位 assistant 文本改为中性的「好的，明白了。」，
  不再用「（前置上下文，评测不跑这一轮）」干扰「不得假设章节」类用例。
- **I-02** `query_structured_data` 接术语表翻译：中文物品名命中术语表（阈值与检索扩展一致）
  时替换为英文全名并**整词优先 LIKE**；精确替换没盖住的模糊叫法
  （「山丘巨人的铁手套」对「山丘巨人之力手套」，≥4 字公共子串）用英文全名整词兜底。
  Mac 实测：两种叫法都命中 `Gauntlets of Hill Giant Strength` 且带 `[act 3]`，
  「影心」命中 Shadowheart。

服务器侧动作：

1. `git pull` → `bash scripts/deploy/api.sh` 重启。
2. `.venv/bin/python -m pytest tests -q`（基线 224 passed / 20 skipped / 1 xfailed）。
3. `.venv/bin/python scripts/eval/agent_behavior.py`（routing 应 13/13）。
4. full 模式重跑两条点踩溯源用例（I-16 修完后报告才有可读性）：
   ```bash
   FASTEMBED_CACHE_PATH=$PWD/.cache/fastembed \
     .venv/bin/python scripts/eval/agent_behavior.py --mode full \
     --only bg3-hill-giant-gauntlets bg3-lorrokan-act2
   ```
   - `bg3-hill-giant-gauntlets`：I-02+I-18 上线后「铁手套」两轮应命中正确条目且带
     `[act 3]`，不再出现「第二章」改写——这条过了，I-10 的点踩复现链路才算闭环。
   - `bg3-lorrokan-act2`：I-17 的 prompt 硬上界生效后，`法术杂货铺/拉玛吉斯之塔/下城区`
     应消失；`Nightsong` 的 L3 命中属词表偏严（用户已知暗夜之歌），可记录为已知偏严
     而非失败，是否放宽词表由读数决定。
5. 用 `scripts/analysis/turns_report.py --check-budget 40000` 确认裁剪上线后
   tokens_in p90 已降到预算内（I-09 验收线：p90 < 40,000，单轮 max < 80,000）。

## P0-2 refetch_wiki.py 解析器两个盲区（Mac 已实测反馈，等修复后开抓）

Mac 侧用代理（`export https_proxy=http://192.168.5.1:7890`，requests 认环境变量）
对候选源做了 `--probe` 探测，结论：

- **terraria zh**：`https://terraria.wiki.gg/zh/api.php` 可用，13 个中文分类名
  （武器物品/盔甲物品/配饰物品/工具物品/物块物品/家具物品/制作材料物品/宠物召唤物品/
  坐骑召唤物品/弹药物品/Boss NPC/敌怪 NPC/NPC）共 3031 页候选；质检仅
  `share_over_20k=0.1` 擦边挂，可接受。
- **silksong**：fandom 探测到 81 页真内容，但 `refetch_wiki.py` 解析器有两个盲区
  导致产物不可用，**修复前不能整站跑**：
  1. `portable-infobox`（fandom 新版 infobox 的 class 名）未被剥离，混进正文；
  2. 跨页共享模板（导航/数据模板）的幽灵文本残留在多个页面里。
- `silksong.wiki.gg` 与 huijiwiki 已判死（不可用源），silksong 只能走 fandom +
  `--suffix "(Silksong)"`。

服务器侧动作：修解析器两个盲区 + 采样策略（之前定的「抽 20 页比对标题 vs 正文对题」
要进闸门），改完在 PR/commit 里说明，Mac 侧随即用代理跑整站抓取并质检。

## P1-1 turn_record 观测字段（I-14/I-10 C 的剩余部分）

`api_server.py` 的 `turn_record` 目前还没有（Mac 侧 grep 确认）：

- `progress_level` + `progress_source`（I-14 建议 2）：没有这两个字段，离线量不出
  「进度=2 的轮次占比」「未知进度占比」，I-14 改进前后无法对比。
- `entry_acts`（I-10 C 方案附带）：工具返回条目里出现的 act 集合（去重、上限 10），
  供离线统计「返回了 act3 但用户进度是 act2」的比例。

SPOILER_TERMS 的 act3 地名已补（`87568ec`），不用重复做。

## P1-2 I-19 `max_turns=6` 观察 3 天再定（不要现在调参数）

`history_trim` 已上线（`f9eaabb`）。服务器侧复核认为 6 轮对短消息会话偏激进
（线上 `history_msgs` p50=31），建议 10–12 或字符远低于预算时不按轮数裁。
**已定的口径：裁完头 3 天看 tokens_in 与点踩率再动**，数据在服务器侧，由服务器给结论
（约 11-25 前后）。Mac 侧负责在拿到结论后调参。

## P1-3 数据清洗稳定后给 Mac 重建信号

全量重建 7 库的前提是你们那边 wiki 数据清洗迭代稳定（清洗脚本还在改）。
稳定后通知 Mac：Mac 跑 `mac_prepare_build.sh --game all` 重建 +
`vectorstores.sh` 上传，服务器负责接收后重启验收。**重建与代码上线必须同一次**
（严格模式下旧库零命中的教训，见 AGENTS.md「读端不做兜底」一节）。

## P2-1 方案 B 会话记忆（等用户拍板留存期后启动）

设计文档：`docs/SESSION_MEMORY_PLAN.md`（三表 sessions/turns/receipts、进度存数不存文、
存根确定性生成、三步迁移）。其中**留存策略 90/180 天还等用户拍板**。
拍板后服务器侧负责：`api_server.py` 接 session 存取、三表落库、留存清理任务；
Mac 侧负责：`multi_agent`/`graph` 从「前端全量重发 history」切换为「session_id + 增量」。

## P2-2 golden set 扩充（依赖方案 B）

评测 golden set 要扩到 30–50 条，数据源是 `data/feedback/*.jsonl` 的真实点踩/点赞轮。
方案 B 落地后由服务器侧从 feedback 导出候选，Mac 侧转成 `behavior_cases.jsonl` 用例。

## 需用户本人跑（服务器无法执行）

本机 curl 被 SSRF 守卫拦，HTTP 层验证需用户跑：

```bash
curl -s http://localhost:8765/api/status
curl -s -X POST http://localhost:8765/api/ask -H 'Content-Type: application/json' \
  -d '{"question":"BG3 山丘巨人的铁手套在哪拿"}'
```

验收点：`version` 为最新提交；铁手套回答带「第三章/希望之邸（House of Hope）」且
**不再出现「第二章」**；影心类问题落到 Shadowheart 条目。

## 已完成（勿重做）

- SPOILER_TERMS 补 act3 地名（`87568ec`）+ 12 条契约测试。
- 数据看板设计 `docs/DASHBOARD_PLAN.md`（服务器侧自己的活，Mac 无动作项）。
- 防剧透数据分析与问题防护（ISSUES_2026-11-22 全链路）。
