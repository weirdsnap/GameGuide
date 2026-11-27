# 战地5（Battlefield V）数据采集计划（2026-11-27 调查）

> 状态：**调查完成，待用户拍板后开工**。与 KCD2 同批调查，结论与方案同构。

## 一、候选源对比（实测）

| 源 | 连通性 | BFV 覆盖 | 结论 |
|---|---|---|---|
| `battlefield.fandom.com`（英文） | 走代理 ✅（`192.168.5.1:7890`，直连 000） | **~550–600 页**，`of Battlefield V` 分类体系完整 | **主源** |
| `battlefield.fandom.com/zh`（中文） | 走代理 ✅ | **可忽略**：全站 610 篇（全系列），BFV 内容约 20 页（武器 3、特色 6、阵营 4） | 弃用——太薄的 zh 库比没有更糟 |
| wiki.gg | Battlefield 无 wiki.gg 站 | — | 不存在 |

## 二、fandom BFV 分类实测页数（2026-11-27）

命名规律是 `X of Battlefield V`（注意排除 `of Battlefield Vietnam`，一代越战作，名字相近）：

| 分类 | 页数 | 备注 |
|---|---|---|
| Vehicles | 90 | 坦克/飞机/载具 |
| Weapons | 82 | 另有子类型分类：SMG 15、Sidearms 13、LMG 11、Melee 10、Semi-Auto 10、Bolt-Action 9、Grenades 9、AR 6、Carbines 6、MMG 6、Shotguns 5、SLR 4、AMR 2、Attachments 5、Addons 2 |
| Features | 68 | 机制/系统页面（含 Firestorm 4） |
| Gadgets | 45 | 装备/道具 |
| Characters | 33 | |
| Specializations | 29 | 专精/天赋 |
| Maps | 22 | 多人地图（样例 Arras 43KB，结构正常） |
| Factions | 19 | 阵营 |
| Gamemodes | 15 | 游戏模式 |
| Elite Classes | 14 | 精英兵种（皮肤角色） |
| Levels | 8 | 单人战役（选集式：Under No Flag / Nordlys / Tirailleur / The Last Tiger 等） |
| Transcripts | 6 | 战役文本（可选） |
| ~~Trailers~~ | 35 | **排除**：宣传片页面，无攻略价值 |

样例页 Arras 43KB 正常；M1928A1 是重定向（36 字节），属正常形态。
预计去重后 550–600 页。

## 三、抓取方案

与 KCD2 完全同构（`scripts/fetch/refetch_wiki.py`，fandom 分类直采）：

- 源：`--api https://battlefield.fandom.com/api.php`（registry 登记时配）
- 网络：**必须挂代理** `https_proxy=http://192.168.5.1:7890`
- 分类：上表除 Trailers 外的全部 `of Battlefield V` 分类；
  **千万别用前缀模糊抓**——会把 `of Battlefield Vietnam` 的页面混进来
- 坑同 REFETCH_PLAN 复盘：`caffeinate -i` 包裹、Cloudflare 滴漏脚本自带防护、
  抓完闸门 + 抽样 20 页比对对题
- zh 不抓（中文站 BFV 内容约 20 页，太薄），按 en-only 模式（同 BG3/mhw/KCD2）

## 四、入库与后续

1. `game_registry.py` 登记 `battlefield5`（signals 含「战地5/战地风云5/战地V/
   Battlefield V/BF5/BFV」；`spoiler_axis` 建议 `none`——主打多人，
   单人战役是选集式短篇、无线性主线，不存在「进度即上界」的结构）。
2. 抓取 → `--replace` → 质量闸门 → commit。
3. 结构化 DB：武器数值（伤害/射速/弹匣）是 BFV 高频问法，先尝试
   `build_game_db.py` 通用构建器，不适配再写 `scripts/build/bfv_db.py`。
4. Mac 建库 → `retrieval_smoke.py` 加 BFV 用例（如「Arras 地图点位」
   「STG44 怎么样」）→ 传库 → 服务器重启。
5. 中文提问走跨语言 embedding；战地系列武器/地图译名混乱（如「二式」
   「格韦尔」），后续补 `games/battlefield5/data/battlefield5_glossary.jsonl`
   术语表收益会很高（I-28 已验证该路径）。

## 五、风险

- 分类成员可能混入其他 Battlefield 代作品页面（同库多代共存），闸门 +
  抽样兜底；混入多则逐叶分类抓。
- 单人战役只有 8 关且薄，相关问答可能答不深——这是源本身的问题，不是采集问题。
