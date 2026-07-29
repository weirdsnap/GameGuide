"""
Hollow Knight RAG Agent — 工具函数。

提供两种检索能力：
1. KnowledgeSearchTool — 自然语言 RAG 向量检索（文本/概念/策略）
2. StructuredQueryTool — 结构化 SQLite 查询（数值/属性/精确数据）
"""

import json
import re
from typing import Any, Dict, List, Optional, Type
from pydantic import BaseModel, Field

from rag_agent.vectorstore import load_vectorstore, get_retriever
from rag_agent.hk_db import HollowKnightDB

# ── 共享数据库实例（延迟加载） ──
_hk_db: Optional[HollowKnightDB] = None


def get_hk_db() -> HollowKnightDB:
    global _hk_db
    if _hk_db is None:
        _hk_db = HollowKnightDB()
        _hk_db.connect()
    return _hk_db


# ══════════════════════════════════════════
# 1. RAG 向量检索工具
# ══════════════════════════════════════════

class KnowledgeSearchInput(BaseModel):
    query: str = Field(description="搜索查询（英文关键词优先）")
    k: int = Field(default=8, description="返回的文档数量")


class KnowledgeSearchTool:
    name: str = "search_knowledge_base"
    description: str = """搜索空洞骑士维基知识库（RAG 向量检索）。
适合查询：剧情背景、区域描述、Boss 战策略、护符效果概念、游戏机制说明、NPC 对话等自然语言内容。
输入应为英文关键词或短语。"""
    args_schema: Type[BaseModel] = KnowledgeSearchInput

    def run(self, query: str, k: int = 8) -> str:
        try:
            retriever = get_retriever(k=k)
            docs = retriever.invoke(query)
            if not docs:
                return "(知识库未找到相关内容)"
            parts = []
            for i, doc in enumerate(docs, 1):
                content = doc.page_content.strip()
                meta = doc.metadata or {}
                source = meta.get("source", meta.get("name", ""))
                parts.append(f"【参考 {i}】{source}\n{content[:500]}")
            return "\n\n".join(parts)
        except Exception as e:
            return f"[知识库检索出错] {e}"


# ══════════════════════════════════════════
# 2. 结构化数据查询工具
# ══════════════════════════════════════════

QUERY_EXAMPLES = """
查询格式：自然语言描述你想查的数据

可用表：charms（护符）, bosses（Boss）, skills（技能/能力）, areas（区域）, characters（角色/NPC）, enemies（敌人）, items（物品）

示例查询：
- "查询护符 Grubsong" → 返回 Grubsong 的详细属性
- "列出所有 3 格 Cost 的护符" → 返回 Cost=3 的所有护符
- "查询 Boss False Knight 的属性" → 返回 HP、地点、Geo 奖励等
- "列出 HP 大于 500 的 Boss" → 按 HP 排序的 Boss 列表
- "查询 Monarch Wings 技能" → 返回技能属性
- "所有角色的出售物品列表" → 查 Sells 字段
- "列出所有区域" → 区域列表
- "搜索护符含有 fury 关键词的" → 模糊名称搜索
"""

TABLE_ALIASES = {
    # 中英文别名 → 表名
    "护符": "charms", "charm": "charms", "charms": "charms",
    "boss": "bosses", "bosses": "bosses", "Boss": "bosses", "BOSS": "bosses",
    "技能": "skills", "skill": "skills", "skills": "skills", "能力": "skills", "法术": "skills",
    "区域": "areas", "area": "areas", "areas": "areas", "地区": "areas",
    "角色": "characters", "character": "characters", "characters": "characters", "npc": "characters", "NPC": "characters",
    "敌人": "enemies", "enemy": "enemies", "enemies": "enemies", "怪": "enemies",
    "物品": "items", "item": "items", "items": "items", "道具": "items",
}


class StructuredQueryInput(BaseModel):
    query: str = Field(description="查询描述，如 '查询护符 Grubsong' 或 '所有 3 格 Cost 的护符'")


class StructuredQueryTool:
    name: str = "query_structured_data"
    description: str = """查询空洞骑士的结构化数据库（SQLite 精确数值）。
适合查询：护符 Cost、Boss HP、技能伤害、区域连接、角色售价、敌人 Geo 掉落等精确数据。
当用户问及数值、属性、统计类问题时使用此工具。"""
    args_schema: Type[BaseModel] = StructuredQueryInput

    @staticmethod
    def _parse_table(target: str) -> str:
        """解析用户提到的表名称。"""
        target_lower = target.lower().strip()
        # Direct match
        if target_lower in TABLE_ALIASES:
            return TABLE_ALIASES[target_lower]
        # Try partial match
        for alias, table in TABLE_ALIASES.items():
            if alias in target_lower or target_lower in alias:
                return table
        return target_lower

    def run(self, query: str) -> str:
        try:
            return self._execute(query)
        except Exception as e:
            return f"[结构化查询出错] {e}"

    def _execute(self, query: str) -> str:
        db = get_hk_db()
        q = query.lower().strip()

        # ── 单一实体查询 ──
        # "查询护符 X", "查询 Boss X", "查技能 X" 等
        for prefix, table in [
            ("护符", "charms"), ("charm", "charms"),
            ("boss", "bosses"),
            ("技能", "skills"), ("skill", "skills"), ("能力", "skills"),
            ("法术", "skills"), ("spell", "skills"),
            ("区域", "areas"), ("area", "areas"), ("地区", "areas"),
            ("角色", "characters"), ("npc", "characters"), ("character", "characters"),
            ("敌人", "enemies"), ("enemy", "enemies"),
            ("物品", "items"), ("item", "items"), ("道具", "items"),
        ]:
            if q.startswith(prefix) or f"查询{prefix}" in q or f"{prefix}查询" in q:
                # Extract entity name
                rest = q
                for p in [f"查询{prefix}", f"查询{prefix[:-1]}" if prefix.endswith("s") else "", prefix]:
                    if rest.startswith(p):
                        rest = rest[len(p):].strip()
                        break
                rest = rest.replace("的", "").replace("属性", "").replace("信息", "").strip()
                if rest:
                    result = db.get_all(table)
                    # Try exact match first
                    for item in result:
                        if item.get("name", "").lower() == rest.lower() or item.get("name", "").lower().replace(" ", "_") == rest.lower().replace(" ", "_"):
                            full = db.query(f"SELECT * FROM {table} WHERE slug = ? OR name = ? LIMIT 1", (item.get("name", "").lower().replace(" ", "_"), item.get("name", "")))
                            if full:
                                return self._format_record(full[0], table)
                    # Try fuzzy
                    full = db.query(f"SELECT * FROM {table} WHERE name LIKE ? LIMIT 1", (f"%{rest}%",))
                    if full:
                        return self._format_record(full[0], table)
                    return f"未找到 {prefix}：{rest}"

        # ── Cost 查询 ──
        # "3格cost", "3格", "cost:3", "notch=3", "cost3", "3 cost" 等
        cost_match = (
            re.search(r"(?:cost|槽|格|notch)\s*[:：=]?\s*(\d+)", q, re.IGNORECASE) or
            re.search(r"(\d+)\s*(?:格|cost|notch|槽)", q, re.IGNORECASE)
        )
        if cost_match:
            cost_val = int(cost_match.group(1))
            rows = db.list_charms_by_cost(cost_val)
            if rows:
                lines = [f"🪄 Cost {cost_val} 的护符 ({len(rows)} 个)："]
                for r in rows:
                    lines.append(f"  · {r['name']} (Cost {r['notch_cost']}) — {r['effect']}")
                    if r.get('location'):
                        lines[-1] += f" [位置：{r['location']}]"
                return "\n".join(lines)
            return f"没有 Cost={cost_val} 的护符"

        # ── HP 范围查询 ──
        hp_match = re.search(r"(?:HP|血量|生命)\s*[>≥:：=]?\s*(\d+)", q, re.IGNORECASE)
        if hp_match:
            min_hp = int(hp_match.group(1))
            rows = db.boss_hp_range(min_hp, 99999)
            if rows:
                lines = [f"💀 HP ≥ {min_hp} 的 Boss ({len(rows)} 个)："]
                for r in rows:
                    lines.append(f"  · {r['name']} — HP {r['hp_base']}「{r.get('geo', '?')} Geo」[区域：{r.get('area_name', '?')}]")
                return "\n".join(lines)
            return f"没有 HP≥{min_hp} 的 Boss"

        # ── 列表查询 ──
        for cmd, table, label in [
            ("所有护符", "charms", "护符"),
            ("全部护符", "charms", "护符"),
            ("所有技能", "skills", "技能/能力"),
            ("全部技能", "skills", "技能/能力"),
            ("所有区域", "areas", "区域"),
            ("全部区域", "areas", "区域"),
            ("所有boss", "bosses", "Boss"),
            ("全部boss", "bosses", "Boss"),
            ("所有敌人", "enemies", "敌人"),
            ("全部敌人", "enemies", "敌人"),
            ("所有物品", "items", "物品"),
            ("全部物品", "items", "物品"),
        ]:
            if cmd in q:
                rows = db.get_all(table)
                lines = [f"📋 所有{label} ({len(rows)} 个)："]
                for i, r in enumerate(rows, 1):
                    # Show key fields
                    parts = [r.get("name", "?")]
                    if r.get("notch_cost") is not None:
                        parts.append(f"Cost {r['notch_cost']}")
                    if r.get("hp_base"):
                        parts.append(f"HP {r['hp_base']}")
                    if r.get("geo_drop"):
                        parts.append(f"Geo {r['geo_drop']}")
                    if r.get("area_name"):
                        parts.append(f"[{r['area_name']}]")
                    lines.append(f"  {i}. {' · '.join(str(p) for p in parts)}")
                return "\n".join(lines[:60])  # cap at 60 lines

        # ── 搜索特定名称 ──
        # "搜索护符 fury", "查名字 kings" 等
        search_match = re.search(r"(?:搜索|查找|找)\s*(护符|charm|boss|技能|区域|敌人|物品|道具|item|enemy|area|skill)\s*(.+?)(?:的|信息|属性)?$", q)
        if search_match:
            table_alias = search_match.group(1)
            keyword = search_match.group(2).strip()
            table = self._parse_table(table_alias)
            rows = db.search_named(table, keyword)
            if "error" in rows[0] if rows else {}:
                return rows[0]["error"]
            if rows:
                lines = [f"🔍 搜索 {table} 含「{keyword}」({len(rows)} 条)："]
                for r in rows:
                    name = r.get("name", "?")
                    details = []
                    if r.get("notch_cost") is not None:
                        details.append(f"Cost {r['notch_cost']}")
                    if r.get("hp_base"):
                        details.append(f"HP {r['hp_base']}")
                    if r.get("geo_drop"):
                        details.append(f"Geo {r['geo_drop']}")
                    if r.get("location"):
                        details.append(f"[{r['location']}]")
                    elif r.get("area_name"):
                        details.append(f"[{r['area_name']}]")
                    lines.append(f"  · {name}" + (" — " + " · ".join(str(p) for p in details) if details else ""))
                return "\n".join(lines)
            return f"未找到含「{keyword}」的{table}"

        # ── 全能搜索（没有匹配到特定模式，尝试整体搜索所有表） ──
        all_results = []
        for table in ["charms", "bosses", "skills", "areas", "characters"]:
            rows = db.search_named(table, q)
            if rows and "error" not in rows[0]:
                for r in rows[:3]:
                    all_results.append(f"  [{table}] {r.get('name', '?')}")
        if all_results:
            return f"未识别明确查询，但在数据中找到以下相关条目：\n" + "\n".join(all_results[:10]) + "\n\n💡 试试更具体的查询，如「查询护符 Grubsong」或「列出所有 3 格 Cost 的护符」"

        return "未能识别查询。试试看：\n- 「查询护符 Grubsong」\n- 「3 格 Cost 的护符」\n- 「HP > 300 的 Boss」\n- 「所有区域」\n- 「搜索护符 fury」"

    def _format_record(self, record: Dict, table: str) -> str:
        """将单条数据库记录格式化为可读文本。"""
        lines = []
        r = record

        if table == "charms":
            lines.append(f"🪄 护符：{r.get('name', '?')}")
            if r.get('notch_cost') is not None:
                lines.append(f"  Cost：{r['notch_cost']}")
            if r.get('effect'):
                lines.append(f"  效果：{r['effect']}")
            if r.get('description'):
                lines.append(f"  描述：{r['description']}")
            if r.get('location'):
                lines.append(f"  位置：{r['location']}")
            if r.get('area_name'):
                lines.append(f"  区域：{r['area_name']}")
            if r.get('acquisition'):
                lines.append(f"  获取方式：{r['acquisition']}")
            if r.get('fragile'):
                lines.append(f"  ⚠️ 易碎护符")
            if r.get('synergies'):
                try:
                    syns = json.loads(r['synergies'])
                    if syns:
                        lines.append(f"  联动：{', '.join(syns[:5])}")
                except: pass
        elif table == "bosses":
            lines.append(f"💀 Boss：{r.get('name', '?')}")
            if r.get('hp_base') is not None:
                lines.append(f"  HP：{r['hp_base']}")
            if r.get('geo') is not None:
                lines.append(f"  Geo：{r['geo']}")
            if r.get('area_name'):
                lines.append(f"  区域：{r['area_name']}")
            if r.get('optional'):
                lines.append(f"  ☑️ 可选 Boss")
            if r.get('rewards'):
                try:
                    rew = json.loads(r['rewards'])
                    if rew:
                        lines.append(f"  奖励：{', '.join(rew[:5])}")
                except: pass
        elif table == "skills":
            lines.append(f"⚡ 技能：{r.get('name', '?')}")
            if r.get('kind'):
                lines.append(f"  类型：{r['kind']}")
            if r.get('effect'):
                lines.append(f"  效果：{r['effect']}")
            if r.get('damage'):
                lines.append(f"  伤害：{r['damage']}")
            if r.get('soul_cost'):
                lines.append(f"  Soul Cost：{r['soul_cost']}")
            if r.get('area_name'):
                lines.append(f"  区域：{r['area_name']}")
            if r.get('acquisition'):
                lines.append(f"  获取：{r['acquisition']}")
        elif table == "areas":
            lines.append(f"🗺️ 区域：{r.get('name', '?')}")
            if r.get('kind'):
                lines.append(f"  类型：{r['kind']}")
            if r.get('description'):
                lines.append(f"  描述：{r['description'][:200]}")
            if r.get('parent'):
                lines.append(f"  父区域：{r['parent']}")
            if r.get('stag_station'):
                lines.append(f"  🦌 有鹿角站")
        elif table in ("characters", "enemies"):
            lines.append(f"👤 {r.get('name', '?')}")
            if r.get('hp') is not None:
                lines.append(f"  HP：{r['hp']}")
            if r.get('damage'):
                lines.append(f"  伤害：{r['damage']}")
            if r.get('geo_drop') is not None:
                lines.append(f"  Geo 掉落：{r['geo_drop']}")
            if r.get('location'):
                lines.append(f"  位置：{r['location']}")
            if r.get('area_name'):
                lines.append(f"  区域：{r['area_name']}")
            if r.get('kind'):
                lines.append(f"  类型：{r['kind']}")
            if r.get('role'):
                lines.append(f"  角色：{r['role']}")
            if r.get('sells'):
                try:
                    sell = json.loads(r['sells'])
                    if sell:
                        items_str = []
                        for s in sell:
                            if isinstance(s, dict):
                                items_str.append(f"{s.get('item', s.get('name', str(s)))} — {s.get('cost', '?')} Geo")
                            else:
                                items_str.append(str(s))
                        lines.append(f"  出售：{', '.join(items_str[:5])}")
                except: pass
        elif table == "items":
            lines.append(f"📦 物品：{r.get('name', '?')}")
            if r.get('kind'):
                lines.append(f"  类型：{r['kind']}")
            if r.get('description'):
                lines.append(f"  描述：{r['description'][:200]}")
            if r.get('location'):
                lines.append(f"  位置：{r['location']}")
            if r.get('cost'):
                lines.append(f"  价格：{r['cost']}")

        return "\n".join(lines) if lines else f"记录：{r}"


