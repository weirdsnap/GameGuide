"""
游戏工具 — 向量检索 + 结构化数据库查询。

从 multi_agent.py 搬入，供 graph.py 的 agent 节点使用。
"""

import logging
import os
import re
from contextvars import ContextVar
from pathlib import Path
from typing import Dict, List, Optional

from langchain.tools import tool

from rag_agent.game_router import AVAILABLE_GAMES
from rag_agent.act_index import title_act
from rag_agent.glossary import expand_query
from rag_agent.vectorstore import load_vectorstore

logger = logging.getLogger(__name__)

# 本轮请求的进度上界：graph 在调用 Agent 前设置，工具在模型没传 max_act 时兜底。
# 用 ContextVar 而不是模块全局，保证并发请求之间互不串（aiohttp 每个请求一个 task）。
ACT_LIMIT: ContextVar[Optional[int]] = ContextVar("gameguide_act_limit", default=None)


def _resolve_game_key(name: str) -> Optional[str]:
    """将游戏显示名或内部键解析为内部键。"""
    clean = name.lower().strip().replace(' ', '_')
    if not clean:
        return None
    if clean in AVAILABLE_GAMES:
        return clean
    for key, cfg in AVAILABLE_GAMES.items():
        if clean in cfg['name'].lower().replace(' ', '_'):
            return key
    return None


# ══════════════════════════════════════════
#  游戏数据库工具
# ══════════════════════════════════════════

def _get_db(path: str):
    """懒加载 SQLite 连接。"""
    import sqlite3
    db_path = Path(path)
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _query_db(db_path: str, sql: str, params: tuple = ()) -> List[Dict]:
    """通用的 SQLite 查询。"""
    db = _get_db(db_path)
    if not db:
        return []
    try:
        cur = db.cursor()
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.warning(f"DB query error: {e}")
        return []
    finally:
        db.close()


def _table_columns(db_path: str, table: str) -> set[str]:
    """读取表的列名（PRAGMA），避免对不存在的列发查询刷 warning。"""
    db = _get_db(db_path)
    if not db:
        return set()
    try:
        cur = db.cursor()
        cur.execute(f"PRAGMA table_info([{table}])")
        return {row["name"] for row in cur.fetchall()}
    except Exception:
        return set()
    finally:
        db.close()


def _format_db_result(rows: List[Dict], table: str,
                      game_key: Optional[str] = None) -> str:
    """格式化查询结果为可读文本（与表无关的通用格式）。"""
    if not rows:
        return "（数据库未找到相关内容）"

    lines = []
    for i, r in enumerate(rows, 1):
        parts = []
        for key, val in r.items():
            if val is not None and val != "":
                label = key.replace("_", " ").title()
                parts.append(f"{label}: {val}")
        if parts:
            line = f"{i}. " + " | ".join(parts[:6])
            # I-18：数值筛选/列表分支也要带 act——11-16 实测这类请求
            # 占工具调用大头，不标注模型照样自己安章节
            act = _row_act(game_key, r)
            if act is not None:
                line += f" [act {act}]"
            lines.append(line)
    return "\n".join(lines[:30])  # cap at 30 rows


def _row_act(game_key: Optional[str], row: Dict) -> Optional[int]:
    """推条目所属章节（防剧透 I-10）：优先 act 列，否则用 where_to_find
    里的地名去 locations/quests 同名表查（act_index）。查不到就 None，别猜。

    I-18：where_to_find 同时提到多章地名时取 max(act)——防剧透场景
    宁可多屏蔽，不能由 dict 遍历顺序决定漏不漏。
    """
    raw = row.get("act")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    if not game_key:
        return None
    where = str(row.get("where_to_find") or "").lower()
    if not where:
        return None
    try:
        from rag_agent.act_index import act_by_title
        acts = [act for name, act in act_by_title(game_key).items()
                if name and name in where]
        return max(acts) if acts else None
    except Exception:
        return None


def _common_substring_len(a: str, b: str) -> int:
    """最长公共子串长度（供术语表模糊匹配用，串都很短，DP 开销可忽略）。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def _search_all_tables(db_path: str, keyword: str,
                       game_key: Optional[str] = None) -> str:
    """在所有表中搜索关键词，返回命中条目的关键字段（供 Agent 直接引用）。"""
    db = _get_db(db_path)
    if not db:
        return "(数据库不可用)"
    cur = db.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r['name'] for r in cur.fetchall()]

    INFO_COLS = {"description", "effect", "where_to_find", "special_effect",
                 "loot", "objectives", "subtitle", "properties"}
    results = []
    for table in tables:
        # Try name column first (common across all structured tables)
        try:
            sql = f"SELECT * FROM [{table}] WHERE name LIKE ? LIMIT 5"
            cur.execute(sql, (f"%{keyword}%",))
            rows = cur.fetchall()
        except Exception:
            rows = []
        if not rows:
            # Some tables might not have a 'name' column; try title
            try:
                sql = f"SELECT * FROM [{table}] WHERE title LIKE ? LIMIT 5"
                cur.execute(sql, (f"%{keyword}%",))
                rows = cur.fetchall()
            except Exception:
                rows = []
        for r in rows:
            r = dict(r)
            name = r.get('name') or r.get('title') or '?'
            extras = []
            for c in INFO_COLS:
                v = r.get(c)
                if v:
                    extras.append(str(v)[:140])
            line = f"  [{table}] {name}"
            if extras:
                line += " | " + " | ".join(extras[:2])
            # I-10：条目附章节。模型手里没有「这是第几章」的信息时，
            # 会把 act3 的藏品改写到用户所在的第二章（两轮点踩实证）。
            act = _row_act(game_key, r)
            if act is not None:
                line += f" [act {act}]"
            results.append(line)

    db.close()
    if results:
        return "数据库中找到以下相关条目：\n" + "\n".join(results[:15]) + \
               "\n\n💡 试试更具体的查询，如「查询 Jackie」或「列出所有武器」"
    return "(数据库中未找到相关内容)"


def _fmt_table_names(db_path: str) -> str:
    """获取数据库的表名和行数列（用于提示）。"""
    db = _get_db(db_path)
    if not db:
        return ""
    cur = db.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('sqlite_sequence', 'game_meta')")
    tables = [r['name'] for r in cur.fetchall()]
    info = []
    for t in tables:
        try:
            cur.execute(f"SELECT COUNT(*) FROM [{t}]")
            cnt = cur.fetchone()[0]
            info.append(f"{t}({cnt}条)")
        except:
            info.append(t)
    db.close()
    return ", ".join(info)


# ══════════════════════════════════════════
#  通用游戏工具（接受 game 参数路由到对应数据）
# ══════════════════════════════════════════


def _tool_game_cfg(game: str) -> tuple:
    """解析 game 参数并返回 (game_key, cfg) 元组。"""
    resolved = _resolve_game_key(game)
    if not resolved:
        valid = ", ".join(AVAILABLE_GAMES.keys())
        raise ValueError(f"无效游戏「{game}」，可用选项：{valid}")
    cfg = AVAILABLE_GAMES[resolved]
    return resolved, cfg


@tool
def search_knowledge_base(query: str, game: str, k: int = 8, max_act: int | None = None) -> str:
    """使用向量检索搜索游戏的维基知识库。

    适合查询：剧情背景、区域/地点描述、Boss 打法、游戏机制、
    NPC 对话、合成配方等描述性内容。

    在每个游戏中调用时，使用 game 参数指定目标游戏。

    Args:
        query: 搜索关键词，使用英文关键词效果更佳
        game: 目标游戏键名（如 hollow_knight、oni、terraria、silksong、cyberpunk2077、va11halla、mhw、baldurs_gate3）
        k: 返回的相关结果数量（默认 8）
        max_act: 防剧透过滤（仅 BG3 等按幕推进的游戏）：用户已报进度时传当前幕数
            （1/2/3），act 大于该值的地点/任务文档会被过滤；缺 act 标签的文档保留
    """
    try:
        _game_key, cfg = _tool_game_cfg(game)
    except ValueError as e:
        return f"[参数错误] {e}"

    # 模型没有显式传 max_act 时，使用 graph 注入的本轮进度上界（确定性兜底）
    if max_act is None:
        max_act = ACT_LIMIT.get()

    vs_dir = cfg["vectorstore_dir"]
    game_name = cfg["name"]
    vs_ok = os.path.isdir(vs_dir) and os.path.isfile(os.path.join(vs_dir, 'index.faiss'))

    if not vs_ok:
        return (f"[知识库暂时不可用] {game_name} 的向量库尚未构建。\n"
                f"请在 Mac 本地运行 `python3 scripts/tool/mac_build.py --game {_game_key}` 来构建。")
    try:
        vs = load_vectorstore(save_dir=vs_dir)

        # 中文专名扩展：命中术语表时把英文原名追加进 query（跨语专名是主要
        # 检索弱点——BG3 实测 cos("影心","Shadowheart")=0.692 不低，但长正文块
        # 把名字信号稀释到 top-8 开外；扩展后 影心 rank 1 / 下城区 rank 1）。
        # 术语表缺失或异常时静默用原 query，不拖垮检索。
        search_query = query
        hits = []
        try:
            search_query, hits = expand_query(query, _game_key)
        except Exception:
            pass
        if search_query != query:
            logger.info("术语扩展: %s → %s", query, search_query)

        # 分语言过滤，保证中文和英文文档都有候选进入合并
        if max_act is not None:
            # 防剧透：语言 + act 上界组合过滤（dict filter 表达不了「缺 act 放行」，
            # 用 callable）。act 解析不出的文档（设定/法术/物品类）始终保留。
            # 建库时只有约 10% 的 wiki 块带 act；这里用结构化库的
            # locations/quests 同名表做查询期兜底（act_index.py），不重建也能收紧。
            def _resolved_act(meta):
                a = meta.get("act")
                if a is None:
                    a = title_act(_game_key, meta.get("title"))
                return a

            def _lang_act_filter(lang):
                def _f(meta):
                    if meta.get("language") != lang:
                        return False
                    try:
                        a = _resolved_act(meta)
                        return a is None or int(a) <= max_act
                    except (TypeError, ValueError):
                        return True
                return _f
            en_filter = _lang_act_filter("en")
            zh_filter = _lang_act_filter("zh")
        else:
            en_filter = {"language": "en"}
            zh_filter = {"language": "zh"}

        # 分语言检索（带分数），按分数跨语言合并——不再「en 全量在前、zh 在后」
        # 顺序拼接（I-28：k=4 时中文块即使更相关也被英文块挤出前 k；
        # 「螳螂爪怎么获得」召不回库里确实存在的 Mantis Claw 就是因此）。
        # FAISS 返回 L2 距离，越小越近；同一索引内分数可直接比较。
        # fetch_k 设为 200 确保过滤后的候选数充足
        en_scored = vs.similarity_search_with_score(search_query, k=k, filter=en_filter, fetch_k=200)
        zh_scored = vs.similarity_search_with_score(search_query, k=k, filter=zh_filter, fetch_k=200)
        scored = sorted(en_scored + zh_scored, key=lambda t: t[1])

        # 标题精确匹配直通：术语扩展命中的英文名若在库里有同标题文档，直接置顶，
        # 不赌向量排序——"Lae'zel 养鸡妹好用吗"这类带撇号实体的向量排名可能
        # 落在 k 窗口之外（实测 top4 全是无关条目），排序置顶救不回来。
        pinned = []
        if hits:
            try:
                wanted = {en.lower() for _zh, en, _c, _f in hits}
                for d in getattr(vs.docstore, "_dict", {}).values():
                    t = (d.metadata or {}).get("title", "").strip().lower()
                    if t and t in wanted:
                        # 遵循与检索一致的过滤：act 不越界（含 DB 同名兜底）
                        if max_act is not None:
                            a = (d.metadata or {}).get("act")
                            if a is None:
                                a = title_act(_game_key, t)
                            try:
                                if a is not None and int(a) > max_act:
                                    continue
                            except (TypeError, ValueError):
                                pass
                        pinned.append(d)
                        if len(pinned) >= 2 * len(hits):
                            break
            except Exception:
                pinned = []

        # 合并，去重（按 page_content 去重）
        #
        # 故意不做「两路都空 → 退回到无过滤整库检索」的兜底：language 是建库契约，
        # 漏标就应该表现成零命中、在自检和线上立刻暴露，而不是被混合语言结果盖住。
        # 实测教训：兜底存在期间，缺 language 标签的 BG3 库连纯英文 query 都能出结果，
        # 「库是坏的」这件事在测试里完全看不见。理由记在 AGENTS.md。
        seen = set()
        docs = []
        for doc, _score in scored:
            sig = doc.page_content[:100]
            if sig not in seen:
                seen.add(sig)
                docs.append(doc)

        # 直通文档插到最前（按内容去重，避免和语义检索结果重复）
        if pinned:
            pinned_sigs = set()
            unique_pinned = []
            for d in pinned:
                sig = d.page_content[:100]
                if sig not in pinned_sigs:
                    pinned_sigs.add(sig)
                    unique_pinned.append(d)
            docs = unique_pinned + [d for d in docs if d.page_content[:100] not in pinned_sigs]

        # 标题词面命中置顶：实体名查询（尤其带撇号的，如 Lae'zel）在向量排序里
        # 不稳——实测 "Lae'zel" 查询下 Lae'zel 条目被 Lorroakan 压到 rank 4。
        # 稳定排序，只把 title 出现在 query 里的块提前，其余保持语义顺序。
        q_low = search_query.lower()
        docs.sort(key=lambda d: 0 if (d.metadata or {}).get("title", "").strip().lower()
                  and (d.metadata or {})["title"].strip().lower() in q_low else 1)

        if not docs:
            return f"(知识库未找到关于「{query}」的内容)"
        parts = []
        for i, doc in enumerate(docs, 1):
            content = doc.page_content.strip()
            meta = doc.metadata or {}
            source = meta.get("source", meta.get("title", ""))
            lang_tag = f"[{meta.get('language', '?').upper()}] " if meta.get("language") else ""
            parts.append(f"【参考 {i}】{lang_tag}{source}\n{content[:500]}")
        return "\n\n".join(parts)
    except Exception as e:
        return f"[知识库检索出错] {e}"


@tool
def query_structured_data(query: str, game: str) -> str:
    """查询游戏的结构化数据库获取精确数据。

    适合查询：Boss/角色属性、物品价格、伤害值、费用等可量化的数值数据。

    输入格式：请使用自然语言描述，例如：
    - "查询 X" — 查询特定物品/Boss/敌人的详情
    - "所有敌人" 或 "所有武器" — 列出某个分类的全部条目
    - "HP>500" 或 "cost 3" — 根据属性/数值筛选

    在每个游戏中调用时，使用 game 参数指定目标游戏。

    Args:
        query: 自然语言查询描述
        game: 目标游戏键名（如 hollow_knight、oni、terraria、silksong、cyberpunk2077、va11halla、mhw）
    """
    try:
        _game_key, cfg = _tool_game_cfg(game)
    except ValueError as e:
        return f"[参数错误] {e}"

    db_path = cfg["db_path"]
    game_name = cfg["name"]

    # 中英专名映射：把中文译名替换为英文原名，便于 LIKE 匹配英文数据库
    # （BG3 角色/关键名词常用译名，覆盖 Agent 直接传入中文名的情况）
    _ZH_TO_EN = {
        "阿斯代伦": "Astarion", "影心": "Shadowheart", "盖尔": "Gale",
        "威尔": "Wyll", "卡拉克": "Karlach", "莱埃泽尔": "Lae'zel",
        "养鸡妹": "Lae'zel", "哈尔辛": "Halsin", "明萨拉": "Minthara",
        "贾希拉": "Jaheira", "明斯克": "Minsc", "塔夫": "Tav",
        "邪念": "Dark Urge", "戈塔什": "Gortash", "奥林": "Orin",
        "凯瑟里克": "Ketheric", "夺心魔": "Illithid", "至上真神": "Absolute",
        "蒸煮罐": "Bubbling Cauldron", "沸腾蒸煮罐": "Bubbling Cauldron",
        "咕噜粥": "Gruel", "营地补给": "Camp Supplies",
    }
    q = query.lower().strip()
    if game == "baldurs_gate3":
        # 剥离游戏名残留（Agent 可能把完整问题传给搜索工具）
        q = re.sub(r"(博德之门3|博德之门 3|博得之门3|博得之门 3|博德之门|博得之门|bg3|baldur'?s? gate 3)", " ", q, flags=re.I).strip()
        for zh, en in _ZH_TO_EN.items():
            if zh in q:
                q = q.replace(zh, en)

    # I-02：术语表翻译——「山丘巨人的铁手套」这类中文物品名在英文库里
    # LIKE 必然不中，命中术语表时替换为英文原名（阈值与检索扩展一致）。
    # 与上面硬编码的 _ZH_TO_EN 并存：硬编码覆盖术语表没有的高频词。
    _exact_en = []  # 精确替换出的英文全名（整词优先，避免拆词误中）
    _fuzzy_en = []  # 模糊候选：精确替换没盖住时，用术语表英文全名整词兜底
    try:
        from rag_agent.glossary import (
            _zh_index, DEFAULT_MIN_CONF, SOFT_MIN_CONF, DEFAULT_FREQ_CUT,
        )
        for zh, en, conf, freq in _zh_index(_game_key):
            if not (conf >= DEFAULT_MIN_CONF
                    or (conf >= SOFT_MIN_CONF and freq >= DEFAULT_FREQ_CUT)):
                continue
            if zh in q:
                q = q.replace(zh.lower(), en)
                _exact_en.append(en)
            elif _common_substring_len(zh, q) >= 4:
                # 用户叫法与词条有 ≥4 字公共子串（如「山丘巨人的铁手套」对
                # 「山丘巨人之力手套」共享「山丘巨人」），记下英文全名，
                # 等精确 token 都落空后整词 LIKE 一次。
                _fuzzy_en.append(en)
    except Exception:
        pass

    try:
        tbl_list = _fmt_table_names(db_path)

        # Try entity query: "查询 X Y" / "查 X"
        for prefix in ["查询", "查", "搜索", "搜"]:
            if q.startswith(prefix) and len(q) > 3:
                keyword = q[len(prefix):].strip()
                if keyword:
                    # I-02：术语表翻出的英文全名先整词 LIKE——「Gauntlets of
                    # Hill Giant Strength」拆词后首个 token「Gauntlets」会
                    # 命中无关手套，全名整词才能命中本体。
                    for en in _exact_en:
                        hit = _search_all_tables(db_path, en, _game_key)
                        if "未找到" not in hit:
                            return hit
                    # Agent 可能画蛇添足拼接多个词（如「查询 蒸煮罐 stewpot 烹饪锅」），
                    # 逐个 token 搜索，取第一个能命中的即可
                    for token in keyword.split():
                        hit = _search_all_tables(db_path, token, _game_key)
                        if "未找到" not in hit:
                            return hit
                    # I-02 模糊兜底：中文 token 全落空时，用术语表英文全名
                    # 整词 LIKE（全名精确，误中风险低）。
                    for en in _fuzzy_en:
                        hit = _search_all_tables(db_path, en, _game_key)
                        if "未找到" not in hit:
                            return hit
                    return _search_all_tables(db_path, keyword, _game_key)

        # ── 关系/层级查询 ──
        # KCD2 quests（prev_quest/next_quest/giver）和 BFV levels
        # （prev/next/sub_chapters）的链式列，供「X 的前置/后续是什么」
        # 「X 有哪些小节」这类追问。表是动态发现的——有链式列才算关系表，
        # 没有这些列的老库直接跳过本分支。
        # （模式较宽，命中后若 LIKE 查不到实体会继续往下走其他分支）
        _REL_PATTERNS = [
            (r"(.+?)的前置任务", "prev"),
            (r"(.+?)之前(?:要|得|需要)?先(?:做|完成|打)什么", "prev"),
            (r"做(.+?)之前", "prev"),
            (r"(.+?)的后续任务", "next"),
            (r"(.+?)之后(?:是|接|做|打|有)什么", "next"),
            (r"(.+?)的下一(?:章|关|任务)", "next"),
            (r"(.+?)有(?:哪些|哪几个|几个)(?:小章节|小节|章节|部分|关)", "sub"),
            (r"(.+?)包含(?:哪些|哪几个)(?:小章节|小节|章节|部分|关)", "sub"),
            (r"(.+?)的(?:小章节|小节|章节)有哪些", "sub"),
            (r"(.+?)是(?:谁|谁发布的|哪个npc|哪个NPC)发布", "giver"),
            (r"谁发布(?:了)?(?:任务|任务叫)?(.+)", "giver"),
        ]
        _rel_tables = []
        db3 = _get_db(db_path)
        if db3:
            cur = db3.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            _all_tables = [r["name"] for r in cur.fetchall()]
            db3.close()
            for t in _all_tables:
                cols = _table_columns(db_path, t)
                if {"prev_quest", "next_quest"} & cols or {"prev", "next"} <= cols:
                    _rel_tables.append((t, cols))
        if _rel_tables:
            for pat, direction in _REL_PATTERNS:
                m = re.search(pat, q)
                if not m:
                    continue
                kw = m.group(1).strip()
                # 清掉问句残留：「的任务」「任务」尾巴、英文「quest」
                kw = re.sub(r"(的任务|任务|quest)\s*$", "", kw, flags=re.I).strip()
                if not kw:
                    continue
                # 术语表精确替换出的英文全名优先（中文专名 LIKE 不中英文库）
                candidates = ([en for en in _exact_en] if _exact_en else []) + [kw]
                for cand in candidates:
                    for t, cols in _rel_tables:
                        rows = _query_db(
                            db_path,
                            f"SELECT * FROM [{t}] WHERE name LIKE ? LIMIT 3",
                            (f"%{cand}%",))
                        if not rows:
                            continue
                        r0 = rows[0]
                        name = r0.get("name", cand)
                        if direction == "prev":
                            val = r0.get("prev_quest") or r0.get("prev") or ""
                            label = "前置任务"
                        elif direction == "next":
                            val = r0.get("next_quest") or r0.get("next") or ""
                            label = "后续内容"
                        elif direction == "sub":
                            val = r0.get("sub_chapters") or ""
                            label = "子章节"
                        else:
                            val = r0.get("giver") or ""
                            label = "发布人"
                        val = str(val).strip()
                        if val and val.lower() != "n/a":
                            return f"[{t}] {name} 的{label}：{val}"
                        return f"[{t}] 找到了 {name}，但数据库中没有记录它的{label}" \
                               f"（可能是链上首/末位，或 wiki 未标注）。"

        # Try "所有 X" / "全部 X" / "列出所有 X"
        for cmd in ["所有", "全部", "列出所有", "列出全部"]:
            if cmd in q:
                keyword = q.split(cmd)[-1].strip()
                if keyword:
                    # Find matching table (only query tables that actually exist)
                    db1 = _get_db(db_path)
                    existing = set()
                    if db1:
                        cur = db1.cursor()
                        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                        existing = {r['name'] for r in cur.fetchall()}
                        db1.close()
                    for table_match in keyword.split():
                        if table_match in existing:
                            rows = _query_db(db_path, f"SELECT * FROM [{table_match}] LIMIT 30")
                            if rows:
                                return _format_db_result(rows, table_match, _game_key)
                    # Try fuzzy table name match
                    db2 = _get_db(db_path)
                    if db2:
                        cur = db2.cursor()
                        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                        tables = [r['name'] for r in cur.fetchall()]
                        db2.close()
                        for table in tables:
                            if any(k in table for k in keyword.split()):
                                rows = _query_db(db_path, f"SELECT * FROM [{table}] LIMIT 30")
                                if rows:
                                    return _format_db_result(rows, table, _game_key)
                    # Try table name aliases (跨游戏通用表别名)
                    table_aliases = {
                        "boss": ["bosses", "monsters", "monster"],
                        "bosses": ["bosses", "monsters"],
                        "敌人": ["enemies", "monsters", "monster"],
                        "enemy": ["enemies", "monsters"],
                        "enemies": ["enemies", "monsters"],
                        "怪物": ["monsters", "monster", "enemies"],
                        "monster": ["monsters"],
                        "技能": ["skills", "skill"],
                        "skill": ["skills"],
                        "武器": ["weapons", "weapon"],
                        "weapon": ["weapons"],
                        "防具": ["armor", "armors"],
                        "armour": ["armor"],
                        "物品": ["items", "item"],
                        "item": ["items"],
                        "道具": ["items"],
                        "区域": ["areas", "locations", "location"],
                        "地区": ["areas", "locations"],
                        "地点": ["locations", "areas"],
                        "位置": ["locations"],
                        "area": ["areas"],
                        "location": ["locations"],
                        "places": ["locations"],
                        "角色": ["characters", "character"],
                        "character": ["characters", "character"],
                        "NPC": ["characters", "character"],
                        "同伴": ["characters"],
                        "伙伴": ["characters"],
                        "companion": ["characters"],
                        "法术": ["spells"],
                        "魔法": ["spells"],
                        "spell": ["spells"],
                        "spells": ["spells"],
                        "书": ["books"],
                        "书籍": ["books"],
                        "book": ["books"],
                        "books": ["books"],
                        "任务": ["quests"],
                        "quest": ["quests"],
                        "quests": ["quests"],
                        "首领": ["bosses"],
                        "boss": ["bosses", "monsters"],
                        "装备": ["items", "weapons", "armor"],
                        "护甲": ["armor"],
                        "盔甲": ["armor"],
                    }
                    for kw in keyword.split():
                        aliases = table_aliases.get(kw.lower(), []) + table_aliases.get(kw, [])
                        if not aliases:
                            aliases = table_aliases.get(kw, [])
                        for alias in aliases:
                            for table in tables:
                                if alias == table:
                                    rows = _query_db(db_path, f"SELECT * FROM [{table}] LIMIT 30")
                                    if rows:
                                        return _format_db_result(rows, table, _game_key)

                    # If no table match, search all tables
                    return _search_all_tables(db_path, keyword, _game_key)

        # ── 通用数值筛选 ──
        # 自动发现所有表中的数值列，然后逐个尝试匹配
        db3 = _get_db(db_path)
        if db3:
            cur = db3.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('sqlite_sequence','game_meta')")
            tables = [r['name'] for r in cur.fetchall()]
            db3.close()

            # 提取查询中的数值
            num_match = __import__('re').search(r"(\d+)", q)
            val = num_match.group(1) if num_match else None

            if val:
                # 判断查询是 cost/价格筛选还是 HP/血量筛选
                is_cost_query = bool(__import__('re').search(r"(?:cost|价格|价|费用|等级|级|格|槽|品质)", q))
                is_hp_query = bool(__import__('re').search(r"(?:HP|hp|血量|生命|health|强度|战力)", q))

                # 收集所有表的所有数值列名
                num_cols = ['hp', 'health', 'damage', 'cost', 'buy_price', 'sell_price',
                            'power', 'defense', 'capacity', 'ram_cost', 'reward_eb',
                            'reward_xp', 'buy_price', 'top_speed', 'horse_power',
                            'ammo_capacity', 'armor_penetration', 'weight',
                            'attack_speed', 'upload_time', 'effective_range']

                if is_hp_query:
                    priority_cols = ['hp', 'health', 'reward_xp', 'ram_cost']
                elif is_cost_query:
                    priority_cols = ['cost', 'buy_price', 'sell_price', 'reward_eb']
                else:
                    priority_cols = ['cost', 'hp', 'health', 'damage']

                # 先按表过滤掉不存在的列：否则每轮会为几十个候选列发无效 SQL，
                # 把 "no such column" 刷进线上日志（2026-11-16 实测每秒几十行）。
                table_cols = {table: _table_columns(db_path, table) for table in tables}
                results = []
                for table in tables:
                    for col in priority_cols:
                        if col not in table_cols[table]:
                            continue
                        rows = _query_db(db_path, f"SELECT name, {col} FROM [{table}] WHERE {col} IS NOT NULL AND CAST({col} AS REAL) >= CAST(? AS REAL) LIMIT 5", (val,))
                        if rows:
                            results.append(f"  [{table}] " + ", ".join(f"{r.get('name','?')} ({col}={r.get(col,'?')})" for r in rows))
                            break

                # 如果没找到，尝试所有可能数值列（同样只查真实存在的列）
                if not results:
                    for table in tables:
                        for col in dict.fromkeys(num_cols):
                            if col not in table_cols[table]:
                                continue
                            rows = _query_db(db_path, f"SELECT name, {col} FROM [{table}] WHERE {col} IS NOT NULL AND CAST({col} AS REAL) >= CAST(? AS REAL) LIMIT 5", (val,))
                            if rows:
                                results.append(f"  [{table}] " + ", ".join(f"{r.get('name','?')} ({col}={r.get(col,'?')})" for r in rows))
                                break

                if results:
                    return f"筛选 ≥{val} 的结果：\n" + "\n".join(results[:20])
                return f"（数据库中没有匹配 ≥{val} 的数值条目）"

        # Fallback: search all tables
        # 去掉常见疑问词/语气词，提高英文 LIKE 命中率（如「Bubbling Cauldron 有什么用」）
        search_q = re.sub(
            r"(有什么用处|有什么用|是什么|有哪些|在哪里|在哪|怎么获得|如何获得|怎么得到|"
            r"多少钱|价格|介绍|资料|攻略|用处|用途|作用|效果|干嘛|物品|道具|装备|武器|角色|"
            r"what is |吗|呢|的|，|。|？)",
            "", q,
        ).strip()
        if not search_q:
            search_q = q
        return _search_all_tables(db_path, search_q, _game_key) + f"\n\n💡 可用的表: {tbl_list}"

    except Exception as e:
        return f"[结构化查询出错] {e}"


@tool
def show_database_schema(game: str) -> str:
    """查看游戏数据库的所有表结构、列名和行数。

    当你不确定目标游戏的数据库包含哪些表时使用此工具。
    它会列出每一张表的列名/类型和行数，方便你构造精确的 query_structured_data 调用。

    Args:
        game: 目标游戏键名（如 hollow_knight、oni、terraria、silksong、cyberpunk2077、va11halla、mhw）
    """
    try:
        _game_key, cfg = _tool_game_cfg(game)
    except ValueError as e:
        return f"[参数错误] {e}"

    db_path = cfg["db_path"]
    game_name = cfg["name"]

    import sqlite3
    try:
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        cur = db.cursor()

        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('sqlite_sequence', 'game_meta')")
        tables = [r['name'] for r in cur.fetchall()]

        result_parts = []
        for table in tables:
            cur.execute(f"SELECT COUNT(*) FROM [{table}]")
            count = cur.fetchone()[0]

            cur.execute(f"PRAGMA table_info([{table}])")
            columns = cur.fetchall()
            col_desc = ", ".join(f"{c['name']} ({c['type']})" for c in columns)
            result_parts.append(f"📋 {table} ({count} rows)\n   Columns: {col_desc}")

        db.close()

        if not result_parts:
            return f"[{game_name}] 数据库中没有数据表。"

        return f"**{game_name}** 数据库结构：\n\n" + "\n\n".join(result_parts)
    except Exception as e:
        return f"[获取数据库结构出错] {e}"


# 全局共享的工具列表（所有游戏共用同一套工具）
GAME_TOOLS = [search_knowledge_base, query_structured_data, show_database_schema]

